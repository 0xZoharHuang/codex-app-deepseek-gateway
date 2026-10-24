<#
Project-owned interactive execution, no desktop switch or system policy mutation.
Invoke with powershell.exe -NoProfile -NonInteractive -ExecutionPolicy Bypass -File ...
Submit: -RequestBase64 BASE64(UTF8(JSON)) [-WaitSeconds 0..60]
JSON fields: executable (absolute existing file), argv (string array), cwd (absolute
existing directory), expected_sid (current user SID), expected_session_id (>0),
timeout_seconds (0 = persistent; 1..86400 = kill exact child on timeout),
output_policy (discard or credential-status). Never put secrets in this request.
WaitSeconds only bounds controller waiting; acceptance is not process completion.
Status: -Mode Status -RunId UUID_HEX reads the same run; it never launches/replays.
Keep the process-scoped ExecutionPolicy argument on every invocation; no global
policy is changed. A completed Worker deletes only its uniquely marked task.
#>
param(
 [ValidateSet('Submit','Worker','Status')][string]$Mode='Submit',
 [string]$RequestBase64,
 [ValidatePattern('^[a-f0-9]{32}$')][string]$RunId,
 [ValidateRange(0,60)][int]$WaitSeconds=20
)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$Repo=Split-Path $PSScriptRoot -Parent
$EvidenceRoot=Join-Path $Repo '.evidence'
$TaskPrefix='CodexAppDeepSeek-'

function Write-JsonAtomic([string]$Path,$Value){
 $tmp=$Path+'.'+[Guid]::NewGuid().ToString('N')+'.tmp'
 [IO.File]::WriteAllText($tmp,($Value|ConvertTo-Json -Depth 15),[Text.UTF8Encoding]::new($false))
 Move-Item -LiteralPath $tmp -Destination $Path -Force
}
function Quote-Arg([string]$Value){
 # Windows CRT argv quoting; never evaluate arguments as PowerShell source.
 $b=New-Object Text.StringBuilder;[void]$b.Append('"');$slashes=0
 foreach($c in $Value.ToCharArray()){
  if($c -eq '\'){$slashes++;continue}
  if($c -eq '"'){[void]$b.Append(('\' * (2*$slashes+1)));[void]$b.Append('"')}
  else{[void]$b.Append(('\' * $slashes));[void]$b.Append($c)}
  $slashes=0
 }
 [void]$b.Append(('\' * (2*$slashes)));[void]$b.Append('"');return $b.ToString()
}
function Connect-Scheduler(){
 $s=New-Object -ComObject 'Schedule.Service';$s.Connect();return $s
}
function Remove-OwnedTask($Scheduler,[string]$Name,[string]$Nonce){
 try{$task=$Scheduler.GetFolder('\').GetTask($Name)}catch{return $false}
 if($task.Definition.RegistrationInfo.Description -ne ('CodexAppDeepSeek owned interactive run '+$Nonce)){throw 'Task ownership marker mismatch'}
 $Scheduler.GetFolder('\').DeleteTask($Name,0);return $true
}
function Get-Run([string]$Id){
 $dir=Join-Path $EvidenceRoot ('credentials-'+$Id)
 if(!(Test-Path -LiteralPath $dir -PathType Container)){throw 'Owned run directory not found'}
 return $dir
}

if($Mode -eq 'Status'){
 if(!$RunId){throw 'Status requires RunId'}
 $dir=Get-Run $RunId
 $resultPath=Join-Path $dir 'result.json'
 if(Test-Path -LiteralPath $resultPath){Get-Content -LiteralPath $resultPath -Raw;exit 0}
 $startedPath=Join-Path $dir 'started.json'
 $status=[ordered]@{run_id=$RunId;completion='unknown_or_running';replay_allowed=$false;result_path=$resultPath}
 if(Test-Path -LiteralPath $startedPath){$status.started=Get-Content -LiteralPath $startedPath -Raw|ConvertFrom-Json}
 $status|ConvertTo-Json -Depth 10;exit 0
}

if($Mode -eq 'Worker'){
 if(!$RunId){throw 'Worker requires RunId'}
 $dir=Get-Run $RunId;$taskName=$TaskPrefix+$RunId
 $request=Get-Content -LiteralPath (Join-Path $dir 'request.json') -Raw|ConvertFrom-Json
 $identity=[Security.Principal.WindowsIdentity]::GetCurrent()
 $session=(Get-Process -Id $PID).SessionId
 $result=[ordered]@{schema_version=1;run_id=$RunId;task_name=$taskName;runner_pid=$PID;sid=$identity.User.Value;session_id=$session;
  started_at=[DateTime]::UtcNow.ToString('o');completion='not_started';exit_code=$null;timed_out=$false;secret_values_in_output=$false;cleanup=@{}}
 $proc=$null;$scheduler=$null
 try{
  if($request.run_id -ne $RunId){throw 'Run identity mismatch'}
  if($session -eq 0 -or $session -ne $request.expected_session_id -or $identity.User.Value -ne $request.expected_sid){throw 'Interactive identity/session mismatch'}
  Write-JsonAtomic (Join-Path $dir 'started.json') $result
  $info=New-Object Diagnostics.ProcessStartInfo
  $info.FileName=$request.executable
  $info.Arguments=(@($request.argv)|ForEach-Object{Quote-Arg ([string]$_)}) -join ' '
  $info.WorkingDirectory=$request.cwd;$info.UseShellExecute=$false;$info.CreateNoWindow=$true
  $info.RedirectStandardOutput=$true;$info.RedirectStandardError=$true
  $info.StandardOutputEncoding=[Text.Encoding]::UTF8;$info.StandardErrorEncoding=[Text.Encoding]::UTF8
  $proc=New-Object Diagnostics.Process;$proc.StartInfo=$info
  if(!$proc.Start()){throw 'Child launch was not accepted'}
  $result.child_pid=$proc.Id;$result.child_started_at=$proc.StartTime.ToUniversalTime().ToString('o')
  $result.child_session_id=$proc.SessionId
  if($proc.SessionId -ne $session){throw 'Child session mismatch'}
  $result.completion='running';Write-JsonAtomic (Join-Path $dir 'started.json') $result
  if($request.output_policy -eq 'credential-status'){
   $stdoutTask=$proc.StandardOutput.ReadToEndAsync();$stderrTask=$proc.StandardError.ReadToEndAsync()
  }else{$proc.BeginOutputReadLine();$proc.BeginErrorReadLine()}
  if([int]$request.timeout_seconds -eq 0){$proc.WaitForExit();$exited=$true}
  else{$exited=$proc.WaitForExit([int]$request.timeout_seconds*1000)}
  if(!$exited){
   $result.timed_out=$true;$result.completion='timed_out'
   # Kill only this exact child object, not a process name or unverified PID tree.
   $proc.Kill();[void]$proc.WaitForExit(5000)
  }else{$proc.WaitForExit();$result.completion='exited'}
  if($proc.HasExited){$result.exit_code=$proc.ExitCode}
  if($request.output_policy -eq 'credential-status' -and $proc.HasExited){
   $out=$stdoutTask.GetAwaiter().GetResult();$err=$stderrTask.GetAwaiter().GetResult()
   if($err -match '\(error 1312\)'){$result.credential_error_code=1312}
   $result.stderr_present=($err.Length -gt 0)
   if($result.exit_code -eq 0){
    if($out.Length -gt 4096){throw 'Credential status exceeds allowed size'}
    $status=$out|ConvertFrom-Json
    $names=@($status.PSObject.Properties.Name)
    if((($names|Sort-Object) -join ',') -ne 'gateway_auth,secret_values_in_output,upstream'){throw 'Unexpected credential status fields'}
    if($status.upstream -notin @('already_configured','copied_in_memory_from_authorized_windows_cds_store')){throw 'Unexpected upstream status'}
    if($status.gateway_auth -notin @('already_configured','created')){throw 'Unexpected gateway credential status'}
    if($status.secret_values_in_output -ne $false){throw 'Credential status did not assert redaction'}
    $result.status=@{upstream=$status.upstream;gateway_auth=$status.gateway_auth;secret_values_in_output=$false}
   }
   $out=$null;$err=$null
  }
 }catch{
  # Keep untrusted output/exception values out of receipts. Raw child output is never saved.
  $result.error_type=$_.Exception.GetType().FullName
  if($result.completion -eq 'not_started'){$result.completion='rejected'}else{$result.completion='failed'}
 }finally{
  try{$scheduler=Connect-Scheduler;$result.cleanup.owned_task_deleted=Remove-OwnedTask $scheduler $taskName $RunId}catch{$result.cleanup.owned_task_deleted=$false;$result.cleanup.error_type=$_.Exception.GetType().FullName}
  $result.finished_at=[DateTime]::UtcNow.ToString('o')
  Write-JsonAtomic (Join-Path $dir 'result.json') $result
  if($null -ne $proc){$proc.Dispose()}
 }
 if($result.completion -ne 'exited' -or $result.exit_code -ne 0){exit 1}
 exit 0
}

# Submit requests must never contain secrets in argv or code. A dedicated credential
# helper accesses WinCred within the interactive identity; there is no SYSTEM fallback.
if(!$RequestBase64){throw 'Submit requires Base64 UTF-8 JSON request'}
$request=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String($RequestBase64))|ConvertFrom-Json
$identity=[Security.Principal.WindowsIdentity]::GetCurrent()
if($request.expected_sid -ne $identity.User.Value -or $request.expected_sid -notmatch '^S-1-5-21-'){throw 'Only current user interactive SID is supported'}
if($request.expected_session_id -isnot [int] -or $request.expected_session_id -le 0){throw 'Explicit nonzero interactive session ID required'}
if(!$request.executable -or ![IO.Path]::IsPathRooted($request.executable) -or !(Test-Path -LiteralPath $request.executable -PathType Leaf)){throw 'Existing absolute executable required'}
if(!$request.cwd -or ![IO.Path]::IsPathRooted($request.cwd) -or !(Test-Path -LiteralPath $request.cwd -PathType Container)){throw 'Existing absolute working directory required'}
if($request.argv -isnot [Array]){throw 'argv must be an array of strings'}
foreach($arg in $request.argv){if($arg -isnot [string] -or $arg.Contains([char]0)){throw 'argv must contain plain strings'};if($arg -eq 'auth-token'){throw 'Auth token output is forbidden in this runner'}}
if($request.timeout_seconds -isnot [int] -or $request.timeout_seconds -lt 0 -or $request.timeout_seconds -gt 86400){throw 'timeout_seconds must be 0 (persistent) or 1..86400'}
if($request.output_policy -notin @('discard','credential-status')){throw 'Use discard or credential-status output policy'}
$RunId=[Guid]::NewGuid().ToString('N');$taskName=$TaskPrefix+$RunId
$dir=Join-Path $EvidenceRoot ('credentials-'+$RunId)
[void](New-Item -ItemType Directory -Path $dir)
# Request contains commands, never credential values; private ACL also protects future
# task diagnostics from unrelated local users.
$privateAcl=Get-Acl -LiteralPath $dir
$privateAcl.SetSecurityDescriptorSddlForm(('D:P(A;OICI;FA;;;'+$identity.User.Value+')(A;OICI;FA;;;SY)'),[Security.AccessControl.AccessControlSections]::Access)
[IO.Directory]::SetAccessControl($dir,$privateAcl)
$request|Add-Member -NotePropertyName run_id -NotePropertyValue $RunId
Write-JsonAtomic (Join-Path $dir 'request.json') $request
$scheduler=Connect-Scheduler;$folder=$scheduler.GetFolder('\');$task=$scheduler.NewTask(0)
$task.RegistrationInfo.Description='CodexAppDeepSeek owned interactive run '+$RunId
$task.Principal.UserId=$request.expected_sid;$task.Principal.LogonType=3;$task.Principal.RunLevel=0
$task.Settings.Enabled=$true;$task.Settings.AllowDemandStart=$true;$task.Settings.AllowHardTerminate=$false
$task.Settings.DisallowStartIfOnBatteries=$false;$task.Settings.StopIfGoingOnBatteries=$false
$task.Settings.MultipleInstances=2;$task.Settings.ExecutionTimeLimit='PT0S'
$action=$task.Actions.Create(0)
$action.Path=Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
$action.Arguments='-NoProfile -NonInteractive -ExecutionPolicy Bypass -File '+(Quote-Arg $PSCommandPath)+' -Mode Worker -RunId '+$RunId
$action.WorkingDirectory=$Repo
$admission=[ordered]@{schema_version=1;run_id=$RunId;task_name=$taskName;sid=$request.expected_sid;session_id=$request.expected_session_id;logon_type='InteractiveToken';run_level='Limited';run_dir=$dir;result_path=(Join-Path $dir 'result.json');replay_allowed=$false;admission='prepared'}
Write-JsonAtomic (Join-Path $dir 'admission.json') $admission
try{
 $taskSddl='D:P(A;;GA;;;SY)(A;;GA;;;'+$request.expected_sid+')'
 $registered=$folder.RegisterTaskDefinition($taskName,$task,2,$request.expected_sid,$null,3,$taskSddl)
 $running=$registered.RunEx($null,4,[int]$request.expected_session_id,$null)
 $admission.admission='accepted';$admission.instance_guid=$running.InstanceGuid
 Write-JsonAtomic (Join-Path $dir 'admission.json') $admission
}catch{
 $admission.admission='unknown';$admission.error_type=$_.Exception.GetType().FullName
 Write-JsonAtomic (Join-Path $dir 'admission.json') $admission
 # Unknown admission is never resubmitted. Inspect this unique task/run first.
 $admission|ConvertTo-Json -Depth 8;exit 1
}
$deadline=[DateTime]::UtcNow.AddSeconds($WaitSeconds)
while(!(Test-Path -LiteralPath $admission.result_path) -and [DateTime]::UtcNow -lt $deadline){Start-Sleep -Milliseconds 200}
if(Test-Path -LiteralPath $admission.result_path){
 $final=Get-Content -LiteralPath $admission.result_path -Raw|ConvertFrom-Json
 if(!$final.cleanup.owned_task_deleted){
  try{
   $deleted=Remove-OwnedTask $scheduler $taskName $RunId
   $final.cleanup|Add-Member -NotePropertyName controller_cleanup_attempted -NotePropertyValue $true -Force
   $final.cleanup|Add-Member -NotePropertyName controller_owned_task_deleted -NotePropertyValue $deleted -Force
   Write-JsonAtomic $admission.result_path $final
  }catch{}
 }
 $final|ConvertTo-Json -Depth 15
 if($final.completion -ne 'exited' -or $final.exit_code -ne 0){exit 1}
}else{$admission|ConvertTo-Json -Depth 8}
