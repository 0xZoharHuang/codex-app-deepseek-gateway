<#
One-shot official-package launch with a private native home and Electron userData.
Default Check is read-only. Launch does not restart, kill, migrate or fall back.
Run in the current non-elevated interactive Windows session. PythonPath must be
this gateway's installed interpreter; no global Python/CDS discovery is performed.
#>
param(
 [ValidateSet('Check','PrepareRuntime','Launch','Status')][string]$Mode='Check',
 [Parameter(Mandatory=$true)][string]$PythonPath,
 [string]$NativeManifest,
 [ValidatePattern('^[0-9a-f]{64}$')][string]$NativeManifestSha256,
 [string]$BaselinePath,
 [ValidatePattern('^[0-9a-f]{64}$')][string]$ExpectedConfigSha256,
 [string]$RunDirectory,
 [string]$TraceRoot,
 [switch]$Accessibility,
 [ValidateRange(5,120)][int]$ObservationSeconds=60,
 [ValidateRange(1,120)][int]$WaitSeconds=60
)
$ErrorActionPreference='Stop'
# Windows PowerShell 5.1 does not bind PSScriptRoot during parameter defaults.
if(!$BaselinePath){$BaselinePath=Join-Path (Split-Path $PSScriptRoot -Parent) 'native\app-launch-baseline.json'}
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$PythonPath=(Resolve-Path -LiteralPath $PythonPath).Path
function Encode-Request($Value){
 return [Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($Value|ConvertTo-Json -Depth 12 -Compress)))
}
function Invoke-Launcher([string]$Operation,$Value){
 $encoded=Encode-Request $Value
 $text=& $PythonPath -I -m codex_app_deepseek.isolated_app $Operation --request-base64 $encoded
 if($LASTEXITCODE -ne 0){throw "Isolated App $Operation failed; no automatic retry"}
 return ($text -join [Environment]::NewLine)|ConvertFrom-Json
}
function Read-OwnedReceipt([string]$Path){
 # Release the file before JSON parsing or downstream pipeline work. The former
 # Get-Content pipeline kept its handle open during ConvertFrom-Json.
 $share=[IO.FileShare]::ReadWrite -bor [IO.FileShare]::Delete
 $stream=[IO.File]::Open($Path,[IO.FileMode]::Open,[IO.FileAccess]::Read,$share)
 try{
  $reader=[IO.StreamReader]::new($stream,[Text.Encoding]::UTF8,$true)
  try{$text=$reader.ReadToEnd()}finally{$reader.Dispose()}
 }finally{$stream.Dispose()}
 return $text|ConvertFrom-Json
}
function Write-OwnedReceipt([string]$Path,$Value){
 [IO.File]::WriteAllText($Path,($Value|ConvertTo-Json -Depth 12),[Text.UTF8Encoding]::new($false))
}
if($Mode -eq 'Status'){
 if(!$RunDirectory){throw 'Status requires RunDirectory'}
 Invoke-Launcher 'status' @{run_dir=$RunDirectory}|ConvertTo-Json -Depth 15
 exit 0
}
if(!$NativeManifest -or !$NativeManifestSha256){throw 'NativeManifest and its trusted SHA256 are required'}
$request=@{baseline=(Resolve-Path -LiteralPath $BaselinePath).Path;native_manifest=(Resolve-Path -LiteralPath $NativeManifest).Path;native_manifest_sha256=$NativeManifestSha256}
if($Mode -eq 'PrepareRuntime'){
 Invoke-Launcher 'prepare-runtime' $request|ConvertTo-Json -Depth 12
 exit 0
}
$request.app_args=@();if($Accessibility){$request.app_args=@('--force-renderer-accessibility')}
$request.timeout_seconds=$ObservationSeconds
if($PSBoundParameters.ContainsKey('ExpectedConfigSha256')){$request.expected_config_sha256=$ExpectedConfigSha256}
if($PSBoundParameters.ContainsKey('TraceRoot')){$request.trace_root=$TraceRoot}
if($Mode -eq 'Check'){
 Invoke-Launcher 'check' $request|ConvertTo-Json -Depth 15
 exit 0
}
if((Get-Process -Id $PID).SessionId -le 0){throw 'Launch requires the current interactive desktop; SSH session 0 is not a GUI launch'}
$plan=Invoke-Launcher 'prepare' $request
$receipt=Join-Path $plan.run_dir 'activation-requested.json'
Write-OwnedReceipt $receipt @{status='activation_requested';at=[DateTime]::UtcNow.ToString('o');pid=$PID;session=(Get-Process -Id $PID).SessionId;plan_sha256=$plan.plan_sha256;replay_allowed=$false}
try{
 $encoded=Encode-Request @{plan=$plan.plan;plan_sha256=$plan.plan_sha256}
 $arguments='-I -m codex_app_deepseek.isolated_app inside --request-base64 '+$encoded
 $null=Invoke-CommandInDesktopPackage -PackageFamilyName $plan.package_family_name -AppId $plan.app_id -Command $PythonPath -Args $arguments -PreventBreakaway -ErrorAction Stop
}catch{
 Write-OwnedReceipt (Join-Path $plan.run_dir 'activation-error.json') @{status='failed_or_unknown';error_type=$_.Exception.GetType().FullName;message=$_.Exception.Message;replay_allowed=$false}
 throw
}
$deadline=[DateTime]::UtcNow.AddSeconds($WaitSeconds)
while([DateTime]::UtcNow -lt $deadline){
 $path=Join-Path $plan.run_dir 'receipt.json'
 if(Test-Path -LiteralPath $path){
  $result=Read-OwnedReceipt $path
  if($result.status -eq 'started_expected_backend'){
   @{run_dir=$plan.run_dir;receipt=$result}|ConvertTo-Json -Depth 15
   exit 0
  }
  if($result.status -eq 'failed_or_unverified'){
   @{run_dir=$plan.run_dir;receipt=$result}|ConvertTo-Json -Depth 15
   exit 1
  }
 }
 Start-Sleep -Milliseconds 200
}
@{status='observation_timeout';run_dir=$plan.run_dir;replay_allowed=$false;instruction='Use Status and inspect the owned receipt. Do not automatically relaunch.'}|ConvertTo-Json
exit 2
