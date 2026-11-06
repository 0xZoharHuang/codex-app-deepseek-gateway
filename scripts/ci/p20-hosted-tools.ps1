param(
    [Parameter(Mandatory=$true)]
    [ValidateSet("Space","Toolchain")]
    [string]$Mode
)
$ErrorActionPreference = "Stop"
# No reclaim or configuration on shared/self-hosted computers.
if ($env:GITHUB_ACTIONS -cne "true" -or $env:RUNNER_ENVIRONMENT -cne "github-hosted" -or
    $env:RUNNER_OS -cne "Windows" -or $env:RUNNER_ARCH -cne "X64" -or
    $env:CI_REPOSITORY_VISIBILITY -cne "public" -or
    $env:GITHUB_RUN_ID -notmatch '^\d+$' -or $env:GITHUB_RUN_ATTEMPT -notmatch '^\d+$') {
    throw "Only a disposable standard public GitHub-hosted Windows x64 job is admitted."
}
$expected = "$env:SystemDrive\cagp20-$env:GITHUB_RUN_ID-$env:GITHUB_RUN_ATTEMPT"
if ($env:CI_P20_ROOT -cne $expected) { throw "Unexpected CI work root." }
$root = $env:CI_P20_ROOT
New-Item -ItemType Directory -Path "$root\evidence" -Force | Out-Null

if ($Mode -eq "Space") {
    $before = (Get-PSDrive -Name $env:SystemDrive.TrimEnd(':')).Free
    # Record actual capacity and explicit optional candidates before inspection
    # or deletion. The documented hosted minimum is not the actual disk size.
    $paths = @("$env:SystemDrive\Android")
    $space = [ordered]@{
        kind="disposable-hosted-runner-only";before_bytes=$before;after_bytes=$before;
        required_bytes=24GB;candidates=$paths;
        candidate_families=@("Android","Java_*","Go","Ruby","PyPy");
        decisions=@();removed=@();shared_pc_modified=$false;
        status="capacity-recorded-before-cleanup"
    }
    function Save-RunnerSpace {
        $space.after_bytes = (Get-PSDrive -Name $env:SystemDrive.TrimEnd(':')).Free
        $space | ConvertTo-Json -Depth 6 |
            Set-Content -Encoding utf8NoBOM "$root\evidence\runner-space.json"
    }
    Save-RunnerSpace
    if ($space.after_bytes -ge 24GB) {
        $space.status = "admitted-without-cleanup"
        Save-RunnerSpace
        if ($space.after_bytes -ge 24GB) { exit 0 }
    }
    if ($env:RUNNER_TOOL_CACHE) {
        try {
            $toolRoot = [IO.Path]::GetFullPath($env:RUNNER_TOOL_CACHE).TrimEnd('\')
            foreach ($entry in (Get-ChildItem -LiteralPath $toolRoot -Directory -ErrorAction Stop)) {
                if ($entry.Name -match '^Java_[A-Za-z0-9_-]+$' -or
                    $entry.Name -cin @("Go","Ruby","PyPy")) { $paths += $entry.FullName }
            }
        } catch {
            $space.decisions += @{path=$env:RUNNER_TOOL_CACHE;action="skip-enumeration";
                                  reason=$_.Exception.GetType().FullName}
        }
    }
    $space.candidates = $paths
    Save-RunnerSpace
    # VS/SDK/Rust/Git/Python/Node, OS, security products and services are not candidates.
    foreach ($path in $paths) {
        if ($space.after_bytes -ge 24GB) { break }
        if (-not (Test-Path -LiteralPath $path)) {
            $space.decisions += @{path=$path;action="skip";reason="absent"}
            Save-RunnerSpace
            continue
        }
        try {
            $item = Get-Item -LiteralPath $path -Force
            if (-not $item.PSIsContainer -or ($item.Attributes -band [IO.FileAttributes]::ReparsePoint)) {
                $space.decisions += @{path=$path;action="skip";reason="redirected-or-not-directory"}
                Save-RunnerSpace
                continue
            }
            # Enumerate one directory at a time. Never push a reparse directory,
            # so neither inspection nor recursive deletion crosses a link.
            $pending = [Collections.Generic.Stack[IO.DirectoryInfo]]::new()
            $pending.Push($item)
            $reparse = $null
            while ($pending.Count -gt 0 -and -not $reparse) {
                $directory = $pending.Pop()
                foreach ($child in (Get-ChildItem -LiteralPath $directory.FullName -Force -ErrorAction Stop)) {
                    if ($child.Attributes -band [IO.FileAttributes]::ReparsePoint) {
                        $reparse = $child.FullName
                        break
                    }
                    if ($child.PSIsContainer) { $pending.Push($child) }
                }
            }
            if ($reparse) {
                $space.decisions += @{path=$path;action="skip";reason="contains-reparse";first_reparse=$reparse}
                Save-RunnerSpace
                continue
            }
            Remove-Item -LiteralPath $path -Recurse -Force
            $space.removed += $path
            $space.decisions += @{path=$path;action="removed";reason="plain-allowlisted-tool-family"}
        } catch {
            # Optional tool families are not a prerequisite. Preserve the
            # original inspection/deletion error and try other allowed families.
            $space.decisions += @{path=$path;action="skip-after-error";
                                  reason=$_.Exception.GetType().FullName}
        }
        Save-RunnerSpace
    }
    $space.status = if ($space.after_bytes -ge 24GB) { "admitted" } else { "insufficient-space" }
    Save-RunnerSpace
    if ($space.after_bytes -lt 24GB) {
        throw "Need 24 GiB free for source, Cargo, target, candidate and assembly."
    }
    exit 0
}

$rustup = (Get-Command rustup.exe -ErrorAction Stop).Source
& $rustup toolchain install 1.99.1 --profile minimal --no-self-update
if ($LASTEXITCODE -ne 0) { throw "Official rustup could not install exact 1.99.1." }
$cargo = (& $rustup which --toolchain 1.99.1 cargo).Trim()
if ($LASTEXITCODE -ne 0 -or -not (Test-Path -LiteralPath $cargo)) { throw "Pinned Cargo unavailable." }
$toolchain = Split-Path -Parent $cargo
$pf86 = [Environment]::GetEnvironmentVariable("ProgramFiles(x86)")
$vswhere = "$pf86\Microsoft Visual Studio\Installer\vswhere.exe"
$vs = (& $vswhere -latest -products '*' -requires Microsoft.VisualStudio.Component.VC.Tools.x86.x64 -property installationPath).Trim()
if ($LASTEXITCODE -ne 0 -or -not $vs) { throw "MSVC x64 tools unavailable." }
$vcargs = "-no_logo -arch=amd64 -host_arch=amd64"
$preferred = Get-ChildItem -LiteralPath "$vs\VC\Tools\MSVC" -Directory |
    Where-Object { $_.Name -like "14.44.*" } | Sort-Object Name -Descending | Select-Object -First 1
if ($preferred) { $vcargs += " -vcvars_ver=14.44" }
if (Test-Path -LiteralPath "$pf86\Windows Kits\10\Include\10.0.26100.0\um\windows.h") {
    $vcargs += " -winsdk=10.0.26100.0"
}
# The native lock pins Rust/profile/source, not a local absolute MSVC path or
# SDK patch. Prefer the measured family; record the actual native toolchain.
$command = 'call "' + "$vs\Common7\Tools\VsDevCmd.bat" + '" ' + $vcargs + ' >nul && set'
$lines = & $env:ComSpec /d /s /c $command
if ($LASTEXITCODE -ne 0) { throw "VsDevCmd could not select the native toolchain." }
$allow = @("PATH","INCLUDE","LIB","LIBPATH","VCINSTALLDIR","VCToolsInstallDir",
           "VCToolsVersion","WindowsSdkDir","WindowsSDKVersion","UniversalCRTSdkDir",
           "UCRTVersion","VSCMD_ARG_TGT_ARCH","VSCMD_ARG_HOST_ARCH")
$selected = @{}
foreach ($line in $lines) {
    if ($line -match '^([^=]+)=(.*)$' -and $matches[1] -in $allow) {
        [Environment]::SetEnvironmentVariable($matches[1],$matches[2],"Process")
        $selected[$matches[1]]=$matches[2]
        "$($matches[1])=$($matches[2])" | Out-File -FilePath $env:GITHUB_ENV -Encoding utf8NoBOM -Append
    }
}
"CI_RUST_TOOLCHAIN_BIN=$toolchain" | Out-File -FilePath $env:GITHUB_ENV -Encoding utf8NoBOM -Append
$selected["rustc"] = (& "$toolchain\rustc.exe" --version --verbose) -join [Environment]::NewLine
$selected["cargo"] = (& "$toolchain\cargo.exe" --version) -join [Environment]::NewLine
$selected["linker"] = (Get-Command link.exe -ErrorAction Stop).Source
$selected["compiler"] = (Get-Command cl.exe -ErrorAction Stop).Source
$selected["ImageOS"]=$env:ImageOS
$selected["ImageVersion"]=$env:ImageVersion
$selected | ConvertTo-Json -Depth 5 | Set-Content -Encoding utf8NoBOM "$root\evidence\toolchain.json"
