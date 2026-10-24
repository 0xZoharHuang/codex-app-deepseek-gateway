<#
Offline, hash-pinned user installation. Install does not configure or launch Codex.
ManifestSha256 must come from a trusted release channel. No mutable dependency fetch.
Other actions require an already activated release, except Activate and cleanup recovery.
#>
param(
 [ValidateSet('Install','Activate','Diagnose','Start','Stop','RegisterCheckpoint','Rollback','Uninstall')][string]$Action='Install',
 [string]$ManifestPath,[string]$ManifestSha256,[string]$Wheelhouse,[string]$PythonExe,
 [string]$InstallRoot=(Join-Path $env:LOCALAPPDATA 'codex-app-deepseek-gateway\managed-install'),
 [string]$DataHome=(Join-Path $env:LOCALAPPDATA 'codex-app-deepseek-gateway'),
 [string]$ReleaseId,[string]$Checkpoint,[int]$Port=42817,[switch]$AtLogon
)
$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.Encoding]::UTF8
$Repo='https://github.com/0xZoharHuang/codex-app-deepseek-gateway'
function Assert-Hash([string]$Path,[string]$Sha){
 if($Sha -notmatch '^[0-9a-fA-F]{64}$' -or !(Test-Path -LiteralPath $Path -PathType Leaf) -or (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Sha.ToLowerInvariant()){throw 'Pinned release artifact hash mismatch'}
}
function Read-Record([string]$Path){return Get-Content -LiteralPath $Path -Raw -Encoding UTF8|ConvertFrom-Json}
function Invoke-Bootstrap([string]$Python,[string]$Wheel,[string[]]$ModuleArguments){
 # All arguments remain argv. No Invoke-Expression, global PATH or environment change.
 $code='import runpy,sys;sys.path.insert(0,sys.argv[1]);sys.argv=["codex-app-deepseek-lifecycle",*sys.argv[2:]];runpy.run_module("codex_app_deepseek.lifecycle",run_name="__main__")'
 $encoded=[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes($code))
 $invoke="import base64;exec(base64.b64decode('"+$encoded+"'))"
 & $Python -I -c $invoke $Wheel @ModuleArguments
 if($LASTEXITCODE -ne 0){throw ('Lifecycle operation failed (exit '+$LASTEXITCODE+')')}
}
if($Action -eq 'Install'){
 if(!$ManifestPath -or !$ManifestSha256 -or !$Wheelhouse -or !$PythonExe){throw 'Install requires ManifestPath, ManifestSha256, Wheelhouse and independent PythonExe'}
 Assert-Hash $ManifestPath $ManifestSha256
 $manifest=Read-Record $ManifestPath
 if($manifest.project -ne 'codex-app-deepseek-gateway' -or $manifest.source_repository -ne $Repo){throw 'Unexpected release source'}
 if($manifest.gateway_wheel -notmatch '^[A-Za-z0-9_.+-]+\.whl$'){throw 'Invalid gateway wheel filename'}
 $wheelMatches=@($manifest.files|Where-Object{$_.filename -ceq $manifest.gateway_wheel})
 if($wheelMatches.Count -ne 1){throw 'Gateway wheel not uniquely pinned'}
 $wheel=Join-Path $Wheelhouse $manifest.gateway_wheel
 Assert-Hash $wheel $wheelMatches[0].sha256
 Invoke-Bootstrap $PythonExe $wheel @('install','--manifest',$ManifestPath,'--manifest-sha256',$ManifestSha256,'--wheelhouse',$Wheelhouse,'--python',$PythonExe,'--install-root',$InstallRoot,'--data-home',$DataHome)
 exit 0
}
$active=$null
$cleanupPath=Join-Path $InstallRoot 'uninstall-ready.json'
if($Action -eq 'Uninstall' -and !(Test-Path -LiteralPath (Join-Path $InstallRoot 'active.json')) -and (Test-Path -LiteralPath $cleanupPath)){
 $proof=Read-Record $cleanupPath
 Assert-Hash $proof.bootstrap_wheel $proof.bootstrap_sha256
 if(!$PythonExe){$PythonExe=$proof.installation.bootstrap_python}
 Invoke-Bootstrap $PythonExe $proof.bootstrap_wheel @('finalize-uninstall','--install-root',$InstallRoot,'--receipt-sha256',(Get-FileHash -LiteralPath $cleanupPath -Algorithm SHA256).Hash.ToLowerInvariant())
 exit 0
}
if($Action -eq 'Activate'){
 if($ReleaseId -notmatch '^[0-9a-f]{20}$'){throw 'Activate requires ReleaseId from a verified install'}
}else{
 $active=Read-Record (Join-Path $InstallRoot 'active.json')
 if($active.project -ne 'codex-app-deepseek-gateway' -or $active.release_id -notmatch '^[0-9a-f]{20}$'){throw 'Invalid active installation'}
 $ReleaseId=$active.release_id
}
$release=Join-Path (Join-Path $InstallRoot 'releases') $ReleaseId
$receiptPath=Join-Path $release 'installation.json'
if($null -ne $active){Assert-Hash $receiptPath $active.receipt_sha256}
$receipt=Read-Record $receiptPath
Assert-Hash (Join-Path $release 'release.json') $receipt.manifest_sha256
Assert-Hash $receipt.bootstrap_wheel $receipt.bootstrap_sha256
$runtime=Join-Path $release 'venv\Scripts\python.exe'
if($Action -eq 'Uninstall'){
 # First process uses installed dependencies for stop/rollback, then exits. Final
 # deletion runs from independent Python so Windows does not hold venv DLLs open.
 $preparedText=& $runtime -I -m codex_app_deepseek.lifecycle prepare-uninstall --install-root $InstallRoot
 if($LASTEXITCODE -ne 0){throw 'Uninstall preparation failed; service/binaries are retained'}
 $prepared=($preparedText -join "`n")|ConvertFrom-Json
 Assert-Hash $prepared.receipt $prepared.receipt_sha256
 if(!$PythonExe){$PythonExe=$prepared.bootstrap_python}
 Invoke-Bootstrap $PythonExe $receipt.bootstrap_wheel @('finalize-uninstall','--install-root',$InstallRoot,'--receipt-sha256',$prepared.receipt_sha256)
 exit 0
}
$arguments=@($Action.ToLowerInvariant(),'--install-root',$InstallRoot)
if($Action -eq 'Activate'){$arguments+=@('--release-id',$ReleaseId)}
if($Action -eq 'Start'){$arguments+=@('--port',[string]$Port);if($AtLogon){$arguments+='--at-logon'}}
if($Action -eq 'RegisterCheckpoint'){
 if(!$Checkpoint){throw 'RegisterCheckpoint requires Checkpoint'}
 $arguments[0]='register-checkpoint';$arguments+=$Checkpoint
}
& $runtime -I -m codex_app_deepseek.lifecycle @arguments
exit $LASTEXITCODE
