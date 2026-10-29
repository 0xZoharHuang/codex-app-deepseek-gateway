# Building the fixed Windows native candidate

The current candidate is **O165**, paired with signed App **26.1023.6621.0**.
Its official runtime source is
`720fec862a608ae1e5878d1eecb04fb93192e93c` / `0.165.0-alpha.1.2`.
`native/build-lock.json` fixes the archive, patches, complete prepared-source
fingerprint and file count, Cargo.lock, compiler/profile and all four outputs.
Read those exact values from the lock; no binary hash or runtime acceptance
is inferred from the source recipe.

Only two patches are applied, in order:

1. `windows-home-namespace-0165.patch` isolates Windows sandbox identities and
   coordinates filesystem ACL updates across cooperating homes while retaining
   the native setup, token, ACL, runner and policy algorithms.
2. `mcp-keyring-isolation-0165.patch` scopes native Direct MCP credential
   load/save/delete to this project's canonical home. File and encrypted Secrets
   storage, native refresh coordination and the same-home Secrets masterkey
   contract remain native.

The previous 0.163 capacity-checkpoint/retry extensions are **not transplanted**
into this version. Official 0.165 local compaction, error handling, agent loop,
tool scheduling, approvals and all App RPC methods remain upstream code.
Source parsing, compilation, native checks and actual App acceptance are separate.
The new cohort is still undergoing qualification; old N/J and r4/r5 results
remain historical, not a full 0.165 pass.

## Compile all four native targets

Use a Windows x64 MSVC Developer shell, Python 3.11+, Git and the fixed Rust
1.99.1 toolchain. The builder installs no dependencies and starts no App, helper
or sandbox. Substitute your paths; output must be empty:

~~~powershell
python -I scripts/build_native.py --archive "<source-archive.tar.gz>" --work-dir "<new-short-build-directory>" --toolchain-bin "<Rust-1.99.1-bin-directory>" --output "<empty-candidate-directory>" --dry-run
~~~

A dry run verifies inputs/tool versions and prints the plan without writing.
Remove `--dry-run` for the foreground build.

An explicit `--cache-dir "<existing-owned-cache-root>"` reuses that directory's
`cargo` and `target` caches while leaving extracted source, output and ownership
records in the selected work directory. Only use a cache owned by this project.
The option requires an existing directory and rejects overlap with the checkout,
current source or output, and redirected/non-directory cache entries. It does not
copy, clean or relocate an older source tree. Omit the option for the unchanged
default `WORK/cargo` and `WORK/target` layout. The build plan records the actual
cache root, and packaging checks both cache paths against that plan.

The current lock selects one exact source producer for all four outputs:

~~~text
cargo build --locked --release -p codex-cli -p codex-windows-sandbox --bin codex --bin codex-command-runner --bin codex-windows-sandbox-setup --bin codex-windows-managed-deny-probe
~~~

The builder uses two jobs, below-normal priority, no incremental/debug output,
symbol stripping, no LTO and sixteen codegen units. It verifies the full source,
patches and archive again before copying outputs. Its raw manifest says
`built-not-app-qualified`. No Code Mode Host from the official App is compiled
or included in these four outputs.

## Assemble the current raw distribution

`scripts/package_native_release.py` accepts the current single producer directly
from its exact builder manifest. It verifies the trusted raw manifest hash,
current lock, four binary hashes/sizes, source fingerprint, profile and target
invocation. It generates public provenance without the builder's absolute paths.
No hand-authored N/J mixed-source receipt is used for O165.

~~~powershell
python -I -B .\scripts\package_native_release.py --source-root "<verified-prepared-source>" --native-manifest "<candidate>\manifest.json" --native-manifest-sha256 "<trusted-hash>" --cargo "<pinned-rust>\cargo.exe" --cargo-home "<offline-cargo-cache>" --git "C:\Program Files\Git\cmd\git.exe" --output "<new-public-staging>" --evidence-dir "<new-private-evidence>"
~~~

Assembly requires clean committed project source for public artifacts; an explicit
`--development-qualification` build remains a qualification archive. It resolves
the exact target's dependency graph offline, retains available original license
notices and fixed SPDX texts, and produces a hash-checked deterministic archive.
Only the four self-built binaries are redistributable here. The normal
`app-prepare-runtime` command later copies the necessary verified official
Code Mode Host from the user's local App into a fresh runtime directory.
Never repack that locally prepared directory as a public raw release.

The historical N/J mode remains explicit: use
`--build-lock native/build-lock.N.json` and its original
`--producer-references-sha256`. It cannot be relabeled as O165.

The following retained record describes the old recipes and their evidence only.

# Historical 0.163 N/J build and distribution

This is a build step for the private runtime. It does not install, configure or start
Codex App, a gateway, a sandbox helper, a Windows service or an agent session.

The historical lock native/build-lock.N.json selects **N CLI only**: the eighteen retained M patches
plus one replay-hash correction. The prepared source has 9,201 files and
fingerprint
`3be6efb7b73138f54cae24f6f6f04da5a348b02483f2a36dbeddc766689a77bc`.
The CLI built with the fixed compiler/profile is
`1dbf68a29a37c92816ea9eaac22b12d2dfbf9fa42017b26b9b050fc18a6da8f4`.
The new bundle has been prepared through the normal installed App command and
loaded by the signed App. N/r4 completed two consecutive cold capacity recoveries,
native compaction/continuation and a separate manual GUI compaction. Independent
source-backed review checked the entire summary-prefix vector and preserved
suffix. This is scoped actual App evidence, not complete App acceptance or final
r5 qualification. Compilation, preparation, native behavior and App acceptance
remain separate gates.

The retained M lock is `native/build-lock.M.json`, SHA-256
`3259ede15d27dcc590a62f6bdeea1963357c1f3b2133d21664688cfc420ea1d9`.
M's source fingerprint `1a365be6...` and CLI `235e1b93...` keep their original
receipts. The historical M/r4 and current N/r4 combinations and scoped results are in
[qualification-status.md](qualification-status.md).

N changes only the capacity checkpoint hash projection so native Compacted
parallel metadata survives another cold reconstruction. It does not alter
history, compaction, tools, approvals or scheduling. The old capacity patch
is retained byte for byte; the new small overlay is separately pinned.

The intended runtime pairs this CLI with three original qualified J siblings.
Their source producer is J's seventeen-patch tree
`9c29f93e84e26b85498bee62cbaf9ae4331f2b8e01d373a60ea301d7b62d6c90`;
the exact original lock is retained as `native/build-lock.J.json`, SHA256
`f878eaaad7c9d8c3f1c9211ac5e221b07fb47c11c22a2e5e1b9f68311cb5ad85`.
Record provenance per executable; the three J siblings were not built from M or N.

M filters inherited handles at the existing CLI-to-foreground-helper launch,
using NUL and the native Win32 handle list while preserving command resolution,
UTF-16 arguments/environment, token, working directory and exit status.
J's helper, background ACL work, runner and probe keep their original bytes.
No agent loop, tool executor or approval engine is added.

Historical K changed the helper's background launch. Its build completed but
actual deployment encountered Winerror225. That failed deployment remains
recorded; K is not the current build lock. Defender settings are not modified.

## Inputs

Use a Windows x64 MSVC Developer PowerShell or Developer Command Prompt with:

- Python 3.11 or later and Git available.
- The x64 MSVC linker and Windows SDK configured by the official Developer shell.
- The already installed `1.99.1-x86_64-pc-windows-msvc` Rust toolchain.
- The upstream source archive identified by `native/build-lock.N.json`.

The build lock fixes upstream commit `680c4ba93ec79c292287fb9e0b627dc7a76e3b5e`,
archive SHA-256, every listed patch SHA-256, prepared source SHA-256, Cargo.lock SHA-256,
Rust/Cargo version and the exact selected executable filenames. Source downloads and toolchain
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
change global Git configuration. The first isolation layer's workspace version
normalization is explained in `windows-sandbox-isolation.md`. The final Cargo.lock,
including the request-capacity-port inputs, is separately pinned by the build lock.

The compiler runs these commands with two jobs, below-normal process priority,
incremental compilation disabled, release debug information disabled, symbol stripping,
LTO disabled and sixteen codegen units:

```text
cargo build --locked --release -p codex-cli --bin codex
```

The CLI package also defines `logs_client`; explicit targets exclude it.
The N and retained M locks exclude helper compilation. To reproduce J's original four-target
producer, supply `--build-lock native/build-lock.J.json` and a distinct work
directory. A new build is a new candidate requiring its own qualification.

Compiler output stays in the foreground. Source and patch checksums are checked again
before outputs are copied. Any source change invalidates the result. The output
directory must be empty; existing candidates are preserved.

Outputs are:

- `codex.exe` for the N or retained M lock; the J lock builds its listed four targets.
- `manifest.json`, containing input provenance, compiler/profile information and each
  executable's size and SHA-256. It explicitly says `built-not-app-qualified`.

The work directory retains `build-report.json` and `source-manifest.json`. A compiler
failure can be retried using the same matching work directory and an empty output
directory. Modified or incomplete source is rejected; preserve it for diagnosis and
use a new work directory. The script performs no automatic deletion.

A native CLI build is not a complete App distribution. The integration also
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
not update `native/build-lock.N.json` automatically and does not qualify a new release.

## Historical candidate K background handle inheritance

K appends `windows-setup-background-handles.patch` to J's unchanged seventeen
patches. The native read-ACL helper now starts its background child with the
existing Win32 `PROC_THREAD_ATTRIBUTE_HANDLE_LIST` primitive. Only a newly opened
NUL handle is inherited. The helper's UTF-16 arguments, invocation-scoped payload
environment, native background work and existing completion behavior are retained.

The production module is the existing CDS implementation, SHA-256
`fb85f0b6d059d94a968626775b0b8cef68fc89365445041477a23271c343dc67`.
Its local caller and a test-only fixture are the three changed source paths.
The fixed Codex version, private App namespace derivation, tools, approvals and agent
lifecycle are unchanged. Rust 1.99.1 and the release profile remain pinned.

The pinned Rust standard library starts ordinary `Command::spawn` with handle
inheritance enabled and no attribute list. Null standard streams leave unrelated
inheritable pipe handles eligible for inheritance. This is verified against the
[exact compiler source](https://github.com/rust-lang/rust/blob/58cf8f9c35b355199727627d07ae34a4e2846d67/library/std/src/sys/process/windows.rs).
The component regression keeps a real child alive behind its own ready/release
gate: K releases the unrelated pipe while the original null-stdio launch retains
it. Both children subsequently exit normally, and both pipe endpoints reach EOF.

On the final formatted source, the two component tests and the thirty-two
metadata/namespace regressions passed under a Limited Windows token. Each selected
test reported one pass, zero failures and zero ignored; the ignored child role is
invoked only by its parent fixture. Six pure build-metadata cases passed separately.
The component artifact is an opt0 test build with debug assertions and overflow
checks enabled. The retained compiler-closeout observations are documented
separately from the successful source/test results.

K's four production executables and actual production/App EOF behavior still
require qualification before changing the running J installation. These component
results do not establish a completed production build or an App acceptance pass.

## Candidate J minimal metadata path

J appends `windows-native-metadata-minimal.patch` to the sixteen unchanged I
patches. It removes the extra ancestor-metadata history ledger, historical
identity reopening, raw-volume NTFS queries and automatic metadata revoke.
Ordinary native ACL updates no longer reconstruct redundant standalone metadata
entries. The existing deny-read policy reconciler and its stale-deny fix remain.

Only current authorized paths select metadata targets. The live path retains
physical home/file identity, no-reparse/no-delete-share pins, owner checks,
root-only non-inheriting FILE_READ_ATTRIBUTES and existing ACL/setup locks.
Before this module writes an ACL, it verifies the current local SAM alias and
both local user SIDs. User-specific inherited denials are considered for home
itself and its ancestors. This does not replace runner token-membership admission.

The project's actual native CLI uninstall uses the upstream None-home contract:
it removes owned sandbox resources while retaining files and filesystem
permissions. Old metadata ledgers are left untouched and are no longer parsed
as authorization or recovery input. No new schema, sidecar or migration engine
is introduced. This does not promise perfect metadata ACL restoration or extend
to the unsupported packaged/registered-service uninstall path.

Thirty-two related default tests passed under a non-elevated Windows token.
One separately authorized test verified existing local namespace subjects by
read-only SAM queries, without grant or provisioning. These are source/unit
results; release construction and actual App acceptance remain separate gates.
The reduction is a functional return to native permission/lifecycle semantics,
not proof of which Defender classifier feature caused the earlier detections.
The compiler and release profile remain unchanged.

## Historical candidate I deployment status

I appends `windows-app-sandbox-coordination.patch` to H's fifteen unchanged
patches. It fixes the native runner's real-home/coordination binding and uses the
actual restricted token's namespace group with read-control access. It does not
add an execution loop, tool scheduler or approval engine.

The private incremental build completed once with the pinned compiler and
profile; 59 native tests passed. The setup helper was then quarantined by
Defender, so I is incomplete and **not App-qualified**. H's deployed helper was
later quarantined as well. No exclusion, restore or security-policy change is
required or performed by these build instructions. A blocked component must not
be hidden by accepting a partial bundle at App launch.

Rebuilding variants to avoid a detection is not part of this recipe. The source
lock preserves the correction for review, while stable Windows deployment
remains a separate unsatisfied gate.

## Historical candidate H source inputs and qualification

H retains the first ten F input patches byte for byte and appends, in order:

1. `cadsg-request-capacity-port.patch`
2. `windows-existing-critical-read.patch`
3. `cadsg-680-summary-kind-compat.patch`
4. `windows-critical-read-deny-readiness.patch`
5. `windows-metadata-owner-policy.patch`

H's historical fifteen-patch input record predates the later I build lock;
the current N lock is described above.
Its prepared source has 9,198 files and fingerprint
`06573f6fde8b44074601c1128101240d9215779f3875e3dc977e9071764caef7`.
Its Cargo.lock SHA-256 is
`fc643e0bfc6c6a3682e5cba84daf8df7ccf1987c364498e0827d161eb607418d`.
The fixed upstream archive is available from
[the pinned openai/codex commit](https://codeload.github.com/openai/codex/tar.gz/680c4ba93ec79c292287fb9e0b627dc7a76e3b5e);
verify its SHA against the lock before extraction.

The source-only replay used this repository's unmodified `prepare_source()` after
the archive and all fifteen patch checks from `main()`. It verified the full
`source_state()` inventory, Cargo.lock, and every file against H's complete prepared
source archive. The source tree has its own Git repository. No private production
plan or machine-specific control path is required by the public builder.

A normal build retains the complete prepared source in `WORK/source` and its full
inventory and upstream change summary in `WORK/source-manifest.json`. Retain the
fixed upstream archive, this build lock and all fifteen patches with that source.
The separately assembled raw H provenance bundle also contains
`sources/prepared-source.tar.gz`, SHA-256
`4932d0bbae2a5433aedac1202f61305875e9eac9eee124478bc2dab9b1dca4b5`,
with all 9,198 source file contents verified. That raw bundle includes its actual
license, metadata and build evidence; it remains separate from this script's
four-binary candidate output and is not a complete or App-qualified distribution.

## Historical candidate D help-only recipe

The former D build lock described candidate D. Its fourth patch changes only the
module documentation and clap help strings in cli/src/sandbox_uninstall.rs, so
help correctly describes the home-owned namespace cleanup already implemented in
C. Cleanup business logic, permissions, dependency pins and the three sandbox
helper sources are unchanged. The normal patch loop in build_native.py consumes
this fourth pinned patch; the plan/output manifest records candidate_revision=D.

At that historical stage, C was the deployed and previously observed artifact. D source was
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


## Checkpoint compatibility when downgrading

N writes the v3 replay hash domain while preserving the checkpoint schema,
cache path and digest label. It can validate exactly two fixed legacy v2
representations. A mixed old representation that matches neither form fails
closed; matching does not edit records or guess per-slot metadata.

M does not recognize N's v3 hashes. A downgrade keeps session history and project
files, but M can reject capacity recovery if it first receives HTTP 413 with a
v3 checkpoint. A successful ordinary completion can establish that runtime's
current checkpoint format. Do not describe rollback as backward checkpoint
compatibility, or edit history/checkpoints to make a mismatch disappear.


## Assemble the raw native release

The compiler output alone is not a redistributable native bundle. In particular,
the locally prepared runtime also contains a Code Mode Host copied from the
official installed App. That local sidecar is not an input to the public archive.

`scripts/package_native_release.py` assembles the already attested N CLI and
three retained J binaries without compiling or running any native component.
It verifies the mixed-producer source-index hash and keeps the N and J source
identities separate; `source_sha256` is not relabeled as the N-only tree hash.

The packager requires the original raw manifest, source index, producer references,
N/J/M lock receipts, the exact prepared N source, the fixed offline Cargo cache,
and this source checkout. It checks that the J recipe is an exact prefix of N's
recipe, their Cargo.lock and target inputs agree, and N's extra overlays change
only the explicitly reviewed Rust files. One offline, locked metadata resolution
therefore provides a shared external-dependency superset. It includes workspace
build/test dependencies and is not described as a list of crates actually linked.

Original available crate LICENSE/NOTICE/COPYING/COPYRIGHT files, declared license
expressions, authors and source references are retained. Crates omitting these
files retain available README notices and their declared attribution; no missing
copyright owner is invented. Canonical SPDX texts use the exact source and
hashes in `native/license-data.json`. Unexpected license identifiers, missing
declared files, outside-cache paths or executable/container bytes in a notice
fail assembly. No downloader or new toolchain is used.

Run from the intended publisher's verified Windows build environment. Substitute
all paths and trusted hashes; the output and private evidence directories must
not exist:

~~~powershell
python -I -B .\scripts\package_native_release.py `
  --build-lock .\native\build-lock.N.json `
  --source-root C:\NativeBuild\prepared-source `
  --native-manifest C:\VerifiedRawNative\manifest.json `
  --native-manifest-sha256 <trusted-raw-manifest-sha256> `
  --producer-references-sha256 <trusted-producer-reference-sha256> `
  --cargo C:\PinnedRust\bin\cargo.exe `
  --cargo-home C:\NativeBuild\cargo `
  --git 'C:\Program Files\Git\cmd\git.exe' `
  --output C:\NewNativeDelivery\raw `
  --evidence-dir C:\PrivateNativeEvidence\new-run
~~~

Normal assembly requires a clean committed gateway source and takes its epoch
from that commit. `--development-qualification` permits an explicit local
candidate and produces a `.qualification.tar.gz`; it is not a public release
source. A changed committed recipe, producer or native binary needs new
qualification rather than a filename-only update.

The new output contains exactly these self-built binaries:

- `codex.exe`, from N.
- `codex-command-runner.exe`, from J.
- `codex-windows-sandbox-setup.exe`, from J.
- `codex-windows-managed-deny-probe.exe`, from J.

It also contains the raw manifest, upstream LICENSE/NOTICE, crate notices,
source/patch/Cargo.lock provenance, original producer receipts and an inventory.
Every file is copied into a new independent file; no input ACL is copied and
no hard link couples the release to a running installation. The archive is
built from this explicit new staging tree and is read back by member name,
size and SHA256. Unknown files, extra native outputs, prepared manifests,
private provenance files and altered binaries are refused. The private
Cargo metadata/stderr remains in the separate evidence directory.

The existing public `app-prepare-runtime` consumes an extracted raw bundle and
obtains its required sidecar from the user's verified official App package.
Its normal no-existing-App guard still applies. Consumer validation must use a
fresh extracted copy after the owned App has closed; never add that sidecar to
the immutable public raw staging or repack a prepared runtime directory.
Preparation alone does not bind, configure, start or qualify the App.

The initial private archive and targeted packaging regressions are development
qualification. Final clean-source assembly, installed-version binding and the
remaining actual App gates still precede stable publication.
