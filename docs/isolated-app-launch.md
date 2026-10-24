# Isolated Windows App launch

The launcher opens the installed, signed Codex App with a private native runtime and
private state. It does not implement an agent loop, tools, approvals, sandbox setup,
process recovery, or App updates. The installed App and native runtime retain those
responsibilities. This entrypoint is Windows-only.

Installed users should use [the formal commands](installed-app-entrypoints.md).
The PowerShell examples below remain low-level development/diagnostic entrypoints;
the same script and fixed baseline are packaged in the wheel, so installed app-launch
does not refer to this source checkout.

**Current validation is launcher fixtures, package/resource verification, and a
harmless packaged Python child. Actual App UI, runtime behavior, frontend storage,
and failure recovery remain separate acceptance gates.** A launch receipt that says
`started_expected_backend` only proves the expected child executable and home were
observed. It is not a complete App acceptance result.

## Fixed inputs

`native/app-launch-baseline.json` fixes package `OpenAI.Codex_26.1016.8120.0_x64__2p2nqsd0c76g0`,
its production package identity, valid signatures, executable signer, App/archive
hashes, official Code Mode Host, Node/Node REPL, ripgrep, and resource manifests.
It is itself hash-pinned by the installed launcher module. An App update fails
preflight until a new combination is independently qualified; no automatic baseline
refresh or fallback is performed.

Supply a native manifest and its separately trusted SHA-256. The manifest must
identify upstream `680c4ba93ec79c292287fb9e0b627dc7a76e3b5e` and native version
`0.163.0-alpha.11.1`, a full source SHA-256, and exactly these four private executables:
`codex.exe`, `codex-command-runner.exe`, `codex-windows-sandbox-setup.exe`, and
`codex-windows-managed-deny-probe.exe`. Every binary's size/hash is checked. Paths
must remain in the manifest's directory. The launcher does not infer provenance
from a filename, accept an unknown manifest hash, or hardcode a count of patches.

The raw build manifest has `schema_version: 1`, `status: built-not-app-qualified`
(or the QA build's `passed`), `native_version`, `upstream_commit`, `source_sha256`,
and either `binaries` (`file`, `size`, `sha256`) or `outputs` (`name`, `path`, `size`,
`sha256`). A raw build's passing status says nothing about App acceptance. Keep the
build manifest, source/patch manifest, licenses, and acceptance receipts together.

## Prepare the private resource bundle

Use this gateway's explicitly selected installed Python. Do not discover Python
from CDS or modify a global PATH. Replace the angle-bracket placeholders:

```powershell
.\scripts\launch_isolated_app.ps1 -Mode PrepareRuntime -PythonPath "<gateway-python.exe>" -NativeManifest "<candidate-directory>\manifest.json" -NativeManifestSha256 "<trusted-raw-manifest-sha256>"
```

Preparation verifies the fixed official package and all listed resources. It copies
only the matching official `codex-code-mode-host.exe` into the private candidate
directory; the source App resource is never modified. That sibling is necessary
because the App's `CODEX_CLI_PATH` override bypasses its usual CLI/host-copy path.
The script preserves the raw manifest, writes `app-runtime-manifest.json` with the
host's source/hash, and returns its SHA-256. Existing conflicting files or a running
App for the current user cause an error. This step does not execute any native
binary, helper, or App.

## Check and launch in the current interactive desktop

Use a non-elevated PowerShell in the intended user's existing desktop session.
An SSH/session-0 launch is rejected. No user/session switching is performed and
other users' processes are not terminated. Configure the gateway and dedicated App
home first; the launcher requires this gateway's model provider to be selected.

```powershell
.\scripts\launch_isolated_app.ps1 -Mode Check -PythonPath "<gateway-python.exe>" -NativeManifest "<candidate-directory>\app-runtime-manifest.json" -NativeManifestSha256 "<trusted-prepared-manifest-sha256>"
.\scripts\launch_isolated_app.ps1 -Mode Launch -PythonPath "<gateway-python.exe>" -NativeManifest "<candidate-directory>\app-runtime-manifest.json" -NativeManifestSha256 "<trusted-prepared-manifest-sha256>"
```

`Check` is the default and does not create state or start the App/native runtime.
It may run the discovered trusted Git executable with `--version` as a bounded,
credential-free dependency probe. `Launch`
performs fresh checks, binds a one-shot plan to the current SID/session, config,
launcher code and trusted manifest, and records the attempt under
`%LOCALAPPDATA%\codex-app-deepseek-gateway\app-launches`. It uses
`Invoke-CommandInDesktopPackage` to enter the fixed App package context; the helper
sets environment variables only on the App child it creates. The package command's
outer caller environment is not relied upon. A package helper transport test is
not a replacement for the actual App qualification.

The exact controlled child environment is:

```text
CODEX_HOME                    %LOCALAPPDATA%\codex-app-deepseek-app
CODEX_ELECTRON_USER_DATA_PATH  %LOCALAPPDATA%\codex-app-deepseek-app\electron-user-data
CODEX_CLI_PATH                <verified-private-directory>\codex.exe
CODEX_APP_SERVER_FORCE_CLI    1
```

The fixed App's bootstrap explicitly reads `CODEX_ELECTRON_USER_DATA_PATH`, sets
Electron `userData` before its instance lock, and preserves `CODEX_HOME` when loading
the shell environment. This is the source-supported override, not an assumption
about Chromium's `--user-data-dir`. The dedicated native home is not migrated from
a default App/CDS home. An explicit `sqlite_home` config must remain inside it.

Before setting these four keys, only the child environment loses inherited Codex/
OpenAI routing, credential, issuer, registered-core, WebSocket/daemon and SQLite
selectors, `OPENROUTER_API_KEY`, and `CODEX_DEEPSEEK_*`. The precise list lives in
`isolated_app.py`; receipts include names only, never inherited values. System/user
environment, registry, unrelated variables, proxies, and other processes are not
changed. No OpenAI subscription or credentials are imported.

The launcher additionally derives `--user-data-dir=<private frontend directory>`
from the same validated path. The pinned official App's own demo launcher uses
this switch together with `CODEX_ELECTRON_USER_DATA_PATH`. The environment override
alone did not isolate the embedded Chromium browser in the first real run; the
original failure is retained. No APPDATA/LOCALAPPDATA override or unproved browser
environment selector is used. Actual default-profile metadata and new profile
locations must still be checked for each qualified App combination.

The only optional caller-supplied App argument is `-Accessibility`, which supplies
`--force-renderer-accessibility`. Arbitrary arguments, user-supplied profile paths
and deep links are rejected.
The launcher refuses any existing Codex App for the current user, in any session.
It never adopts that instance or sends it a second launch. A user-scoped lock also
prevents simultaneous launches from this entrypoint.

## Git as an external Windows dependency

Git is separate from the pinned App and private native manifest. The launcher uses
Windows SHGetKnownFolderPath for Program Files and Program Files (x86), then
checks only Git/cmd/git.exe. It does not trust a workspace executable, a
ProgramFiles environment override, or a link that resolves outside those
installation roots. A bounded git --version probe records the selected trusted
fallback's version and SHA-256 without inherited provider or Git credentials.

For a verified installation, only the newly created App child's copied PATH gains
that cmd directory, appended after the complete existing value. Existing PATH
entries, capitalization of its environment key and any earlier Git remain in
priority order. An already present equivalent entry is not added twice. No user
or machine PATH, registry proxy, Git global configuration, or running process is
modified. The App may still select an earlier caller-provided Git; the fallback
receipt does not claim otherwise.

Check and launch receipts expose git_dependency, its diagnostic status, selected
path/version/hash, and whether the prepared and observed App child PATH contain
the directory. They never record the full PATH. Missing Git or a failed version
probe does not prevent unrelated App work, but remains explicitly unavailable.
A missing/unreadable observed PATH is recorded as unverified. The worktree gate
stays pending_app_validation until actual App Git/worktree operations pass;
finding Git and launching the App are not worktree acceptance.

On the qualified test host the external installation is Git for Windows
2.56.0.windows.3 at C:\\Program Files\\Git. It was installed from the official
signed release with no PATH change, credentials helper, shell/file associations
or Start Menu shortcuts. Installation provenance and its own uninstaller are
separate from gateway/App rollback. Other machines may already have a supported
Git installation; this launcher does not install, update or supervise Git.

## Observe failures without relaunching

Launch returns a run directory and records the exact App/native PID, creation time,
owner, session, executable, selected home, and observed controlled environment.
It requires an actual `app-server` command to match the verified private executable;
short-lived version/model probes are not a successful backend observation. The
App process must also retain the single internally derived profile argument.
Use the returned run directory:

```powershell
.\scripts\launch_isolated_app.ps1 -Mode Status -PythonPath "<gateway-python.exe>" -RunDirectory "<returned-run-directory>"
```

Status distinguishes the recorded result from current PID/creation-time liveness.
A disappeared process or reused PID is not treated as the original process.
`null` liveness means the process could not be inspected, not that it is running.
Missing backend, auth/startup failure, or observation timeout stays failed/unverified;
there is no automatic retry, fallback, global kill, or permission relaxation. A
failed attempt can leave its owned App alive for diagnosis. Inspect its receipt and
close it explicitly before another launch. Replaying the same plan is refused.

`-ObservationSeconds` bounds native-child observation (5–120 seconds), and
`-WaitSeconds` bounds the PowerShell caller's wait (1–120 seconds). A caller timeout
does not authorize a retry and does not stop an App whose launch is still being
observed. No persistent supervisor is installed.

## Native resource and diagnostic paths

Node, Node REPL and ripgrep continue to use the fixed App's native resolver. On the
Windows 10 test host it can populate the current user's `%LOCALAPPDATA%\OpenAI\Codex`
versioned resource cache and prune its old versioned cache entries. The launcher
neither rewrites that mechanism nor changes `LOCALAPPDATA`. Native App logs retain
`%LOCALAPPDATA%\Codex\Logs`. These diagnostic/resource paths are distinct from
threads, SQLite sessions, Electron cookies and login state; this project does not
clean unrelated logs or another user's caches.

The fixed official App remains installed and unmodified. Rollback consists of
closing this owned App, stopping this project's gateway through its own command,
and using the unmodified official App normally. Do not copy private home contents
back to the default profile. Preserve project session data and evidence unless the
user explicitly chooses to remove them.

## Evidence and remaining gate

The Windows helper test ran as the current user's non-elevated interactive session
with the official package identity. It read back all four environment values from
a harmless Python child, tested empty/Unicode/quoted/backslash arguments, verified
selector removal and an unchanged parent environment, and observed that the child
survived the package helper's exit. It did not execute App, codex, or sandbox setup.
The default native and Electron profiles' 92 and 414 file metadata entries were
unchanged before/after this helper run. The launcher fixture tests also cover
manifest/resource failures, wrong identity/session, existing instances, state path
escape, one-shot replay, and explicit failure results.

These receipts establish launch plumbing only. The release gate still requires the
actual fixed App to load the qualified private runtime, write only the intended
state profiles, preserve default profiles, and pass the full agreed GUI/runtime
acceptance matrix. A build awaiting additional isolation fixes must not be promoted
merely because this launcher can verify its checksum.

Installed `app-launch` additionally carries `expected_config_sha256` from its verified auth/catalog/owned-listener preflight. The PowerShell transport preserves this value, and `validate_request` compares it against one exact config-byte snapshot during both prepare and inside admission. A change fails before a new App process is created; the outer caller does not hold the per-user launch lock across the child. The field is optional only for existing low-level developer calls, which continue to use the original prepare/inside plan hash check.
