param([string]$ExpectedUser='admin')
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$Root=Split-Path $PSScriptRoot -Parent
$Package=Join-Path $Root '.artifacts\ChatGPT-x64.msix'
$RunId=[Guid]::NewGuid().ToString('N').Substring(0,16)
$Evidence=Join-Path $Root ('.evidence\app-install\'+$RunId)
New-Item -ItemType Directory -Path $Evidence -Force|Out-Null
function Save-Json($Name,$Value){$Value|ConvertTo-Json -Depth 12|Set-Content -LiteralPath (Join-Path $Evidence $Name) -Encoding UTF8}
Add-Type -TypeDefinition @'
using System;using System.Collections.Generic;using System.Runtime.InteropServices;
public static class InstallSessionSnapshot{
 [StructLayout(LayoutKind.Sequential)]struct SI{public int Id;public IntPtr Name;public int State;}
 [DllImport("wtsapi32.dll",EntryPoint="WTSEnumerateSessionsW",SetLastError=true)]static extern bool List(IntPtr s,int r,int v,out IntPtr p,out int count);
 [DllImport("wtsapi32.dll",EntryPoint="WTSQuerySessionInformationW",SetLastError=true)]static extern bool Query(IntPtr s,int id,int kind,out IntPtr p,out int bytes);
 [DllImport("wtsapi32.dll")]static extern void WTSFreeMemory(IntPtr p);
 public class Item{public int Id;public string Station,User,Domain,State;}
 static string Read(int id,int kind){IntPtr p;int n;if(!Query(IntPtr.Zero,id,kind,out p,out n))return null;try{return Marshal.PtrToStringUni(p);}finally{WTSFreeMemory(p);}}
 public static Item[] Get(){IntPtr p;int count;if(!List(IntPtr.Zero,0,1,out p,out count))throw new System.ComponentModel.Win32Exception();var a=new List<Item>();string[] states={"Active","Connected","ConnectQuery","Shadow","Disconnected","Idle","Listen","Reset","Down","Init"};try{int n=Marshal.SizeOf(typeof(SI));for(int i=0;i<count;i++){SI v=(SI)Marshal.PtrToStructure(IntPtr.Add(p,i*n),typeof(SI));a.Add(new Item{Id=v.Id,Station=Marshal.PtrToStringUni(v.Name),User=Read(v.Id,5),Domain=Read(v.Id,7),State=v.State>=0&&v.State<states.Length?states[v.State]:v.State.ToString()});}}finally{WTSFreeMemory(p);}return a.ToArray();}
}
'@
function Snapshot{
 @{at=[DateTime]::UtcNow.ToString('o');
 identity=[Security.Principal.WindowsIdentity]::GetCurrent().Name;
 session_id=(Get-Process -Id $PID).SessionId;
 packages=@(Get-AppxPackage -Name OpenAI.Codex|Select-Object Name,Version,PackageFullName,PackageFamilyName,InstallLocation,Status);
 services=@(Get-CimInstance Win32_Service|Where-Object {$_.Name -like '*Codex*'}|Select-Object Name,State,StartMode,StartName,PathName,ProcessId);
 sessions=@([InstallSessionSnapshot]::Get());
 processes=@(Get-CimInstance Win32_Process|Where-Object {$_.Name -match '^(codex|codex-deepseek|codex-http-transport|codex-code-mode-host|codex-windows-sandbox-service|ChatGPT)[.]exe$'}|Select-Object ProcessId,ParentProcessId,Name,SessionId,ExecutablePath,CreationDate)}
}
$result=[ordered]@{schema_version=1;run_id=$RunId;scope='Current user Add-AppxPackage only; no provisioning, launch, configuration, or process shutdown';package_path=$Package;started=[DateTime]::UtcNow.ToString('o');success=$false}
$before=Snapshot;Save-Json 'before.json' $before
try{
 if(($before.identity -split '\\')[-1] -ine $ExpectedUser){throw 'Unexpected installing user'}
 $hash=(Get-FileHash -LiteralPath $Package -Algorithm SHA256).Hash.ToLowerInvariant()
 if($hash -ne 'ef1fc9606261edc9c93d97b504d4c6ac3facf25ac64d7a8aa03496ef4733798d'){throw 'Official package SHA256 mismatch'}
 $signature=Get-AuthenticodeSignature -LiteralPath $Package
 if($signature.Status -ne 'Valid'){throw ('Official signature rejected: '+$signature.Status)}
 $result.sha256=$hash;$result.signature_status=$signature.Status.ToString()
 if($before.packages.Count -gt 0){
  if($before.packages.Count -ne 1 -or $before.packages[0].Version.ToString() -ne '26.1016.8120.0'){throw 'Existing different Codex App package; no automatic upgrade or shutdown'}
  $result.action='already_installed'
 }else{
  Add-AppxPackage -Path $Package -ErrorAction Stop
  $result.action='installed_current_user'
 }
 $after=Snapshot;Save-Json 'after.json' $after
 if($after.packages.Count -ne 1 -or $after.packages[0].Version.ToString() -ne '26.1016.8120.0'){throw 'Registered package version mismatch'}
 $app=$after.packages[0]
 $result.package=$app
 $result.app_executable=Join-Path $app.InstallLocation 'app\ChatGPT.exe'
 $result.native_executable=Join-Path $app.InstallLocation 'app\resources\codex.exe'
 $result.native_sha256=(Get-FileHash -LiteralPath $result.native_executable -Algorithm SHA256).Hash.ToLowerInvariant()
 if($result.native_sha256 -ne 'e757246b4de87359e660647fa9514955a0971ff6a30affdd8c145d1d5ece7b84'){throw 'Installed native executable hash differs from signed package baseline'}
 $result.before_sessions=$before.sessions;$result.after_sessions=$after.sessions
 $result.services_before=$before.services;$result.services_after=$after.services
 $result.preexisting_runtime_processes=@($before.processes|ForEach-Object{
  $p=$_;$match=@($after.processes|Where-Object {$_.ProcessId -eq $p.ProcessId -and $_.CreationDate -eq $p.CreationDate})
  @{pid=$p.ProcessId;name=$p.Name;executable=$p.ExecutablePath;same_process_present=($match.Count -eq 1)}
 })
 $result.new_processes=@($after.processes|Where-Object {$id=$_.ProcessId;!($before.processes|Where-Object {$_.ProcessId -eq $id})})
 $result.success=$true
}catch{
 $result.error=@{message=$_.Exception.Message;type=$_.Exception.GetType().FullName;hresult=$_.Exception.HResult;fully_qualified_id=$_.FullyQualifiedErrorId;stack=$_.ScriptStackTrace;details=$_.ErrorDetails.Message}
 try{Save-Json 'after-error.json' (Snapshot)}catch{}
}finally{
 $result.finished=[DateTime]::UtcNow.ToString('o')
 Save-Json 'result.json' $result
 $result|ConvertTo-Json -Depth 12
}
if(!$result.success){exit 1}
