# Managed installation and lifecycle on Windows

The lifecycle layer manages this gateway's Python service and files. It does not launch, replace, schedule, or terminate Codex App's agent loop, tools, native subagents, or runtime processes. It has no automatic dependency downloads, model calls, global proxy changes, environment-variable changes, or machine-wide Python installation.

Use `scripts/install_gateway.ps1` for the complete user-facing workflow. After installation, use the returned installed command path for service-start, service-stop, diagnose and the private App entrypoints documented in installed-app-entrypoints.md. The underlying functions are also available through `python -m codex_app_deepseek.lifecycle` for explicit orchestration.

## Release contract and offline installation

A release bundle contains the installer, a complete wheelhouse, and `release.json`. Obtain the expected manifest SHA256 from the trusted fixed project release channel:

`https://github.com/0xZoharHuang/codex-app-deepseek-gateway`

The manifest has exactly these fields:

- `schema_version`: `1`.
- `project`: `codex-app-deepseek-gateway`.
- `version`: the exact gateway wheel version.
- `source_repository`: the fixed repository above.
- `source_commit`: its full 40-character commit.
- `python_version`: exact supported Python major/minor, such as `3.13`.
- `gateway_wheel`: the exact gateway wheel filename, matching `version`.
- `files`: the complete offline wheel set, each with `filename` and `sha256`.
- `app_baseline`: `app_version`, `native_version`, and `native_sha256` for the qualified Windows App/runtime combination.

A digest verifies the bundle against the expected trusted release digest; it is not a publisher signature. The installer verifies the manifest and every wheel before executing a build/install command. It creates an immutable, release-specific venv using a caller-selected independent Windows x64 base Python. That base must not be another venv or belong to App/CDS. Installing into the active App/CDS state directories, using their parent directory, or entering through a junction into them is rejected.

All wheels are installed with `--no-index --no-deps --require-hashes --no-cache-dir`. The complete manifest explicitly selects the dependency artifacts; `pip check` verifies their installed dependency requirements afterward. A distribution version probe and a separate installed codex-app-deepseek.exe --version probe both check the installed gateway version. The installer performs no source builds and no mutable package resolution. The release publisher is responsible for generating the complete dependency set, including the pinned CDS protocol dependency and its provenance.

```powershell
.\scripts\install_gateway.ps1 -Action Install `
  -ManifestPath C:\Downloads\gateway-release\release.json `
  -ManifestSha256 '<trusted manifest SHA256>' `
  -Wheelhouse C:\Downloads\gateway-release\wheels `
  -PythonExe "$env:LOCALAPPDATA\codex-app-deepseek-gateway\runtime-python\python.exe"
```

The default layout is:

```text
%LOCALAPPDATA%\codex-app-deepseek-gateway\
  runtime-python\                 independent base Python, retained
  gateway.json / protocol.sqlite  persistent gateway data, retained
  config-backups\                 private App configuration checkpoints
  managed-install\
    releases\<manifest hash>\
      venv\                       isolated production dependencies
      release.json
      installation.json           file inventory and provenance
      requirements.lock
    bootstrap\<manifest hash>.whl pinned recovery code, retained
    active.json
    checkpoints.json
```

Custom install/data directories are supported. The install directory may be a child of the data home, but may not contain the data home. Only its specific release directories are removed during cleanup. The installation operation does not activate a release, start a service, configure App, initialize credentials, or change the persistent data home. Repeating the identical install checks its immutable receipt instead of overwriting it.

## Activation, version checks, and service ownership

```powershell
.\scripts\install_gateway.ps1 -Action Activate -ReleaseId '<id returned by Install>'
.\scripts\install_gateway.ps1 -Action Diagnose
.\scripts\install_gateway.ps1 -Action Start
.\scripts\install_gateway.ps1 -Action Stop
```

Activation refuses an unresolved `service.json`; stop/reconcile an existing service first. It never takes over a development instance or another installation automatically.

Diagnosis discovers the current user's official App package, checks its packaged native executable SHA256 and maps only a known, previously verified hash to its version, and compares all three values to the qualified release. It also checks the installed file inventory. An App/runtime update produces `compatibility_revalidation_required`; starting that unqualified combination is refused. Updating the declared baseline alone is not a new runtime acceptance result.

`Start` must execute in the intended user's interactive Windows session. It registers a uniquely named `CodexAppDeepSeek-Service-…` task for that exact SID using `InteractiveToken` and `Limited`; there is no SYSTEM or highest-privilege fallback. By default this is an on-demand task. Use `-AtLogon` explicitly to request a current-user logon trigger. It does not switch the desktop, log off other users, or send global keyboard/mouse input.

Task admission is not successful service startup. `Start` waits for the service's process record, verifies that the expected loopback listening socket belongs to that PID, and checks an authenticated `/healthz` response. The health request bypasses inherited proxies, uses only the local gateway token, and never calls an upstream model. An unconfirmed outcome is reported for inspection, not blindly replayed.

`Stop` uses the recorded PID **and creation time**, expected executable, current process owner, native command arguments and data home. A PID reuse, different user/executable/home, or replaced record causes a refusal. It terminates only that exact gateway process and removes only its ownership-marked task. It never runs a process-name-wide kill, recursively kills unrelated children, or stops App/CDS. A stop during an in-flight model request can interrupt that request; inspect the App's actual outcome before resubmitting work.

## Configuration rollback

After using the existing `configure-app` command, explicitly register its returned checkpoint:

```powershell
.\scripts\install_gateway.ps1 -Action RegisterCheckpoint `
  -Checkpoint '<checkpoint.json returned by configure-app>'
.\scripts\install_gateway.ps1 -Action Rollback
```

Only checkpoints inside this data home's `config-backups` can be registered, and registry v2 pins their immutable ownership inputs and snapshot hashes. Another caller's checkpoint, an unproved change, or a junction escape is rejected. Verified restore state transitions are checked by recomputing their private B/A/C-to-R proof. Registered checkpoints are simulated as a complete reverse-order chain before mutation, then restored in that order. A known earlier conflict therefore prevents a partially applied rollback. Mid-operation I/O failures retain recovery evidence instead of claiming atomic multi-file completion. This layer delegates configuration restoration and conflict handling to `app_config.restore`; it does not edit configuration fields itself, infer a missing applied snapshot, or weaken a conflict check.

**The semantic rollback acceptance gate remains distinct.** The App can legitimately append project trust settings after configuration. Safe three-way restoration needs the original, verified applied, and current documents; it should restore only the project's owned fields and retain unrelated project/user settings. A legacy checkpoint containing only an applied whole-file hash cannot be silently upgraded by guessing. The matching schema-2 `app_config` implementation now passes Windows three-way restoration fixtures and an exact replay of the three real App checkpoints, preserving project trust and desktop/Windows settings. Whole-product uninstall and GUI qualification remain separate gates; a conflicting owned field still blocks restoration.

A conflict stops uninstall preparation before stopping the service or removing binaries. Successful rollback preserves App sessions, projects, workspace files, unrelated configuration, and WinCred entries. If multiple checkpoints are involved and a later restoration fails, already completed restorations remain recorded; reconcile that state before continuing.

## External uninstall and recovery

```powershell
.\scripts\install_gateway.ps1 -Action Uninstall
```

Windows cannot reliably remove a Python executable or extension DLL while that same venv is executing. Uninstall therefore has two explicit phases:

1. Production Python validates ownership, restores registered configuration, stops only the verified owned service, removes its owned task, and writes a hashed preparation receipt. That Python process exits.
2. Independent base Python loads the pinned recovery wheel, verifies the receipt and unchanged installation, atomically moves the owned release to a retirement directory, removes its active pointer, and deletes the retired files.

Uninstall retains the persistent data home, sessions, user projects, credentials, independent base Python, recovery wheel, and other installed releases. Unknown or modified files inside a release cause a refusal, rather than deletion of potential user work. Owned file enumeration and cleanup use Windows extended-length paths, including retirement paths longer than MAX_PATH. A Windows file lock produces `uninstalled_cleanup_pending` with a receipt; repeating the installer Uninstall action reconciles that same retired release rather than launching another service or changing another installation.

The lower-level `prepare-uninstall` output means only `ready_for_external_cleanup`. It is not a completed uninstall. `finalize-uninstall` refuses to run from the venv being removed. Use the PowerShell wrapper for the complete two-process workflow.

## Validation boundary

Tests use isolated fixture directories and synthetic packages. They cover manifest/wheel tampering, immutable provenance, no-network install flags, failed-install cleanup, version mismatch, service identity mismatch, checkpoint ownership/conflicts, preservation of user data, modified-file refusal, and external cleanup recovery. Native Windows tests also execute real venv creation and offline pip installation, followed by the PowerShell install/activate/external-uninstall path. These are lifecycle tests; they do not by themselves establish App GUI, DeepSeek protocol, or complete runtime equivalence.
