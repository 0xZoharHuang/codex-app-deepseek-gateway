# Building the fixed Windows native candidate

This is a build step for the private runtime. It does not install, configure or start
Codex App, a gateway, a sandbox helper, a Windows service or an agent session.

The current production-facing entrypoint has passed **dry-run input validation and
comparison with the QA release compiler/profile settings**. It has not yet completed
an independent fresh build through this entrypoint. QA build A used two sequential
Cargo commands. The production entrypoint and completed QA builds B, C and D use one invocation
with four explicit binary targets to unify dependency features and avoid recompiling
the same dependencies for a second package. No retrospective claim is made that A
used this entrypoint or its revised command. A
successful compiler invocation is separate from Windows sandbox and App acceptance.

## Inputs

Use a Windows x64 MSVC Developer PowerShell or Developer Command Prompt with:

- Python 3.11 or later and Git available.
- The x64 MSVC linker and Windows SDK configured by the official Developer shell.
- The already installed `1.99.1-x86_64-pc-windows-msvc` Rust toolchain.
- The upstream source archive identified by `native/build-lock.json`.

The build lock fixes upstream commit `680c4ba93ec79c292287fb9e0b627dc7a76e3b5e`,
archive SHA-256, every listed patch SHA-256, prepared source SHA-256, Cargo.lock SHA-256,
Rust/Cargo version and four executable filenames. Source downloads and toolchain
installation remain ordinary developer prerequisites; this script does not install
tools or create another downloader or process supervisor.

Supply your own paths. No personal drive or user-directory path is embedded in the
scripts. Start with a plan, replacing the angle-bracket placeholders:

```powershell
python -I scripts/build_native.py --archive "<source-archive.tar.gz>" --work-dir "<new-short-build-directory>" --toolchain-bin "<Rust-1.99.1-bin-directory>" --output "<empty-candidate-directory>" --dry-run
```

`--dry-run` checks the archive, patch files, compiler versions and Developer-shell
prerequisites, then prints the exact plan. It creates no work directory and launches
no build. Remove `--dry-run` to build in the foreground.

## What a real build does

The supplied work directory contains only this build's extracted source, Cargo cache,
target directory and receipts. It must be outside the gateway checkout. An existing
nonempty directory is accepted only with this script's matching build ownership
record; an existing source tree must match the locked prepared-source checksum.

The script extracts the verified archive, preserves the archive's license contents,
applies the listed fixed patches using command-scoped Git line-ending settings, and
checks every source file through a full-tree checksum before compiling. It does not
change global Git configuration. The published tag's workspace version normalization
is explained in `windows-sandbox-isolation.md`; external dependency lock records are
unchanged.

The compiler runs these commands with two jobs, below-normal process priority,
incremental compilation disabled, release debug information disabled, symbol stripping,
LTO disabled and sixteen codegen units:

```text
cargo build --locked --release -p codex-cli -p codex-windows-sandbox --bin codex --bin codex-command-runner --bin codex-windows-sandbox-setup --bin codex-windows-managed-deny-probe
```

The CLI package also defines `logs_client`; explicit target selection excludes that
unneeded binary. Neither selected package declares default Cargo features.

Compiler output stays in the foreground. Source and patch checksums are checked again
before outputs are copied. Any source change invalidates the result. The output
directory must be empty; existing candidates are preserved.

Outputs are:

- `codex.exe`
- `codex-command-runner.exe`
- `codex-windows-sandbox-setup.exe`
- `codex-windows-managed-deny-probe.exe`
- `manifest.json`, containing input provenance, compiler/profile information and each
  executable's size and SHA-256. It explicitly says `built-not-app-qualified`.

The work directory retains `build-report.json` and `source-manifest.json`. A compiler
failure can be retried using the same matching work directory and an empty output
directory. Modified or incomplete source is rejected; preserve it for diagnosis and
use a new work directory. The script performs no automatic deletion.

These four executables are not a complete App distribution. The integration also
needs the matching official App resources and the project's independent configuration,
credentials, gateway and acceptance records. Those remain separate deployment gates.

## Exporting the patch for review

To reproduce the sandbox review patch from a deliberately edited fixed source tree:

```powershell
python -I scripts/export_sandbox_patch.py --source "<prepared-source-root>" --archive "<fixed-source-archive.tar.gz>" --output "<patch-output-directory>"
```

`--source-root` remains an alias for `--source`. Both source and archive paths are
required. Output defaults to this repository's `native` directory when omitted.
The exporter verifies the fixed archive SHA and all external lock records, and binds
pure-test claims to the tested module's exact SHA. Re-exporting a changed patch does
not update `native/build-lock.json` automatically and does not qualify a new release.

## Candidate D help-only recipe

The current build lock describes candidate D. Its fourth patch changes only the
module documentation and clap help strings in cli/src/sandbox_uninstall.rs, so
help correctly describes the home-owned namespace cleanup already implemented in
C. Cleanup business logic, permissions, dependency pins and the three sandbox
helper sources are unchanged. The normal patch loop in build_native.py consumes
this fourth pinned patch; the plan/output manifest records candidate_revision=D.

Candidate C remains the deployed and previously observed artifact. D source was
first prepared as an independent one-file overlay; no new runtime has been executed
or deployed. The previous C lock, source inventory and original help file are
retained. The controlled QA incremental build completed after the other owned
CDS compilation exited, reusing the same canonical source path, Cargo/target
directories, Rust version, profile, package selection and four explicit binaries.
Cargo exited 0 after 613.45 seconds. Independent verification rehashed all 9,188
source files, build controls and four outputs: only the CLI binary changed; all
three sandbox helper hashes remain identical to C. The deployed C manifest and
five binary files were independently rehashed and remain unchanged. The D
candidate has not been deployed or qualified in the App. Its exact new
`sandbox uninstall --help` was executed in a separate temporary home under the
Limited interactive token and passed the text assertions; the home and owned
task were removed. No uninstall, sandbox setup or agent turn was executed. See
.evidence/native-D-independent-verification.json and native-release-build-r4.json.
No second cold build, cargo clean, helper setup or App restart is part of this step.
The public builder remains a reproducible from-archive entrypoint; a dry-run is not
a claim that a separate fresh build has completed through it.


D metadata correction: the frozen QA input retained a stale inherited changed-files
summary that omitted the help module, although its authoritative 9,188-file
inventory and changed_since_build_C were correct. Historical build inputs and
manifest remain unchanged. The derived source attestation compares every path,
size and hash to the fixed upstream archive and contains all 30 changed paths.
The final delivery-manifest.json references that attestation, the original frozen
SHA and the help-only validation. Future build_native.py outputs derive their
changed_files and source_changes directly from these full inventories for the
source manifest, build report and binary manifest. Four pure metadata tests cover
all change kinds, the omitted-help regression, materialized license links and
duplicate archive rejection; they do not launch Cargo or a runtime. This metadata
correction changed no Rust source or binary and requires no Rust rebuild.
