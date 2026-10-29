# Windows sandbox isolation for the private App runtime

Current target: App 26.1023.6621.0 / native 0.165.0-alpha.1.2.
The record below belongs to the prior 0.163 cohort. Its observed results are
retained, but are not a 0.165 qualification. The current two-patch recipe and
its remaining gates are in [native-build.md](native-build.md) and
[qualification-status.md](qualification-status.md).


Status: source patch under qualification. This document is not App or sandbox acceptance.

The fixed upstream is `openai/codex@680c4ba93ec79c292287fb9e0b627dc7a76e3b5e`
(`0.163.0-alpha.11.1`). The patch is for this independent gateway's private runtime.
It does not modify an existing CDS installation or the official App package.

## Why an independent home is insufficient

The upstream legacy sandbox stores encrypted account passwords in each `CODEX_HOME`,
but provisions machine-wide `CodexSandboxOffline`, `CodexSandboxOnline`, and
`CodexSandboxUsers`. A later home can rotate those account passwords without updating
an earlier home's credentials. The five firewall rule names and fourteen WFP object
GUIDs (provider, sublayer, twelve filters) are also shared. Independent configuration
or Python environments do not isolate these machine resources.

The official packaged service still uses these identities when a client request has
`registered_core=false`. Clearing the RegisteredCore environment alone does not stop
legacy setup from contacting that service first. The private build therefore selects
the existing native legacy helper before any service IPC. It rejects RegisteredCore
requests and shared installation registration/cleanup, rather than modifying the
system's registered runtime record.

## Scope of the patch

The existing native sandbox remains responsible for provisioning, DPAPI, restricted
tokens, ACLs, firewall rules, WFP conditions, command runners, cancellation and cleanup.
The patch changes resource identities and validates ownership at their boundaries.
It does not add an agent loop, tool executor, approval engine, or sandbox emulator.

`SandboxNamespace` derives an installation identity from:

1. the existing, absolute, canonical Windows `CODEX_HOME`, including its volume
   serial number and 128-bit directory file identity;
2. the initiating token's actual account resolved to a SID, captured before elevation;
3. a fixed domain/version string and SHA-256 with length-delimited inputs.

The elevated helper resolves `real_user` back to the SID and recomputes the namespace
before mutations. It checks both payload usernames against the result. Binding the directory object prevents two distinct homes in a Windows case-sensitive
directory from colliding after case normalization. Recreating or restoring a home as
a different directory object requires explicit fresh setup; an old receipt cannot be
silently adopted. An arbitrary
username string in a payload cannot select an existing global sandbox account.

| Resource | Ownership / compatibility rule |
| --- | --- |
| Offline and online local users | `Cdx` + 80-bit base32 digest + role suffix; exactly 20 characters |
| Local sandbox group | Full namespace digest |
| Setup marker and encrypted users file | Explicit full digest and expected usernames; foreign/legacy state rejected |
| Namespace owner receipt | Canonical home, owner SID, digest and resource names; contains no passwords or tokens |
| Five Windows Firewall rules | Original semantic rule name plus full namespace digest |
| WFP provider, sublayer, twelve filters | SHA-256-derived GUID from full namespace plus each fixed upstream object GUID |
| Hidden-user registry values | Only the exact two namespaced usernames |
| User profile cleanup | Only captured SIDs of those namespaced users, retaining upstream identity rechecks |
| RegisteredCore / installation registry | Never written or deleted by this private build |
| Global setup mutex | Original `Global\CodexSandboxSetup`, deliberately retained for native host-wide serialization |
| Read-ACL progress mutex | Per namespace; the runner reads the protected marker to select its own progress signal |

The rule predicates, protocol/port restrictions, WFP layers and filter conditions,
rights masks, token privileges, and command lifetime algorithms remain upstream's.

## Current J metadata and uninstall boundary

J keeps the existing per-home sandbox namespace, token admission, network
policy and approval mechanisms. Its extra metadata path grants only
non-inheriting FILE_READ_ATTRIBUTES for current authorized paths. It retains
physical object and home identity, no-reparse/no-delete-share pins, owner and
WRITE_DAC checks, effective-deny checks and existing mutation locks.

The current group SID must fresh-match the expected local SAM alias, and the
two local sandbox user SIDs participate in denial checks. Home itself and its
ancestors use that same path. Group membership remains the native runner's
actual-token admission responsibility.

The extra history ledger and automatic metadata revoke have been removed.
Historical paths, malformed old files, old SID records or deleted IDs do not
authorize new grants or block setup; J leaves those files unchanged.
No raw-volume NTFS lookup or private perfect-ACL-recovery engine is required.
Native deny-read policy state and stale managed-deny removal remain active.

The actual project CLI cleanup delegates to the native None-home uninstaller.
It disables/retire its own accounts and workloads, removes owned network
resources and principals, and preserves user files and filesystem permissions.
It does not restore an old DACL or claim zero leftover permission entries.
The original signed App's shared sandbox resources remain outside this namespace.

Original App configuration/binding rollback still uses the existing checkpoint
mechanism. Downgrading to development H/I is separate: those versions cannot
know later unjournaled grants, even though old ledger bytes are retained.
The unsupported Some-home packaged/service path must not inherit an audit-file
preservation claim from this CLI-specific behavior.

J's source replay and related 32 default Low-token tests plus one separate
read-only namespace-subject test have passed. These do not establish App or
antivirus deployment qualification. H/I results and the original detections
remain retained as historical evidence.

## State, interruption, and cleanup

Old shared `setup_marker.json` or either location of `sandbox_users.json` is not migrated, deleted, or
adopted. Use a dedicated App home. An absent state cannot claim pre-existing account
or group names. A validated owner reservation is persisted before native provisioning,
so a crash after native setup creates its initially empty marker can resume only that
namespace. A corrupt, missing-for-existing-state, or mismatched owner receipt fails closed.
The owner receipt is inside `.sandbox-secrets`, protected by the existing native
sandbox directory ACL handling; it is not writable through the normal sandbox group.

Cleanup requires an explicit `CODEX_HOME`, resolves the namespace before disabling
accounts, and retains that exact namespace while upstream stops processes and checks
account SIDs. It does not remove a shared installation record. The public no-home
cleanup entrypoint cannot guess a machine-wide scope. User project data and unrelated
runtime accounts are outside its scope.

## Shared ACL concurrency: upstream limitation, not resolved by namespacing

The original global setup lock surrounds account provisioning and teardown. Ordinary
ACL refresh and the background read-ACL pass are not all covered by that lock. The
original `Local\CodexSandboxReadAcl` object is both a lock and a signal meaning another
helper is already doing the same work: a second helper exits instead of waiting.

This private build must not occupy that old signal. Doing so could cause an unchanged
CDS helper to skip granting its own group while the new helper grants a different
group. The new signal is namespaced, and the original global setup lock is preserved.
However, two runtimes can still update an overlapping directory DACL concurrently.
The patch does not claim that a new mutex can force an unchanged older runtime to
participate, and does not replace the ACL algorithm or broaden permissions to work
around the race.

Release gates include interleaved project-owned instances that touch overlapping ACL
roots, inspection that other principals' ACEs are preserved, and real allow/deny
checks. No other thread's process or home may be reset to perform these tests. Any
observed lost-update or permission regression blocks release and requires a focused
upstream-compatible fix. Serial provisioning alone is not evidence of runtime safety.

## Evidence and remaining gates

Pure Rust tests cover stable derivation, owner/home separation, account name limits,
legacy/cross-home/swapped identity rejection, malformed progress identities, and
non-overlap of all fourteen fixed upstream WFP keys. They perform no Windows account,
ACL, firewall, WFP, registry or native-runtime mutations.

These tests do not establish compilation of the complete runtime, helper admission on
Windows, actual provisioning, sandbox permissions, App loading of the private binary,
Guardian behavior, or coexistence with other runtimes. Those require separately
recorded build and Windows/App acceptance results. Never run the full existing native
sandbox test suite blindly on a shared machine: some upstream integration tests mutate
ACLs and local resources.

The review artifact is `native/windows-sandbox-isolation.patch` with a per-file source
manifest. Reapply only to the fixed upstream commit. An App/runtime update requires
new source review and acceptance rather than inheriting this result.

For byte-for-byte reproducibility on Windows, apply with a command-scoped line-ending
setting: `git -c core.autocrlf=false apply native/windows-sandbox-isolation.patch`.
Do not change the user's global Git settings. The export script binds pure-test
claims to the tested module's exact hash; stale evidence is reported as unverified.

The published upstream tag declares workspace version `0.163.0-alpha.11.1` but its
lockfile still records `0.0.0` for 160 workspace packages. The patch includes the
release metadata synchronization needed for `cargo check --locked`. All external
lock records (versions, sources, checksums, dependency arrays and other fields) are
compared in full to the original archive and remain unchanged. `sha2 0.10.9` was
already pinned upstream; the sandbox crate adds it as an existing workspace dependency.
This normalization is not an external dependency upgrade.

The historical early source qualification also passed locked Cargo checks for
`codex-windows-sandbox --lib`, its three binary targets, and `codex-cli --bin codex`.
The production sandbox library check emits no warnings. The final targeted native
unit runs passed three ownership/directory-state fixtures and eight pure namespace
fixtures; other upstream sandbox tests were filtered out. These establish the stated
source/type and fixture behavior only. The final private candidate C also passed a
single locked release build selecting the CLI and all three sandbox helpers; every
output hash and the complete source/patch/build-control manifest were independently
checked. C removes only an unused command-runner import found in build B. Original
app-server/cloud-tasks Windows warnings remain in the logs; the patch-added unused
import warning is gone. Real provisioning, permission checks, shared-ACL coexistence
and App workflow acceptance remain separate gates.


## Native Windows carveout limitation

The fixed upstream Windows legacy backend explicitly rejects a permission shape
that reopens a writable descendant inside an existing read-only carveout.
For example, granting extra filesystem access to an owned worktree's protected
Git metadata can produce:

    windows elevated sandbox cannot reopen writable descendants under read-only carveouts directly; refusing to run unsandboxed

The same guard is present byte-for-byte in upstream sandboxing/src/windows.rs
and has a corresponding expected-error case in core/src/exec_tests.rs. The
observed rejection is retained; it is not a lost gateway permission or permission
success. The word elevated identifies the preconfigured Windows sandbox backend,
not a grant of administrator privileges to the model command.

Use the App's normal staging workflow for an App-operated Git change. A model
operation that genuinely needs escalation must request the separately authorized,
single-command native fresh review. Do not remove the read-only carveout, change
the whole thread to Full Access, or rerun an unknown Git effect blindly. This
Windows 10 result does not establish Windows 11 MXC behavior.


The dedicated App profile directory must be owned by the Windows user who runs
the App. A directory created from an elevated setup shell can instead be owned
by BUILTIN\Administrators. That is not an interchangeable owner: native
request-capacity checkpoints bind the profile to the process user. Configuration
and launch preflight therefore inspect the directory through a pinned Windows
handle and reject an owner mismatch before starting the App. Initial profile
creation runs under the intended non-elevated interactive account. Existing
directory permissions, native sandbox grants, sessions, and child files are
preserved; the gateway never takes ownership automatically.

If a profile was previously prepared by an elevated shell, quit only this
project's App and confirm the dedicated profile path and its owner first.
Correct only that directory's owner to the intended account using the Windows
Advanced Security dialog, with **Replace owner on subcontainers and objects**
left unchecked. Preserve its existing access entries and inheritance settings.
Then run app-check again before launching. Do not recursively reset permissions
or change the regular Codex profile. An earlier successful task does not prove
checkpoint recovery worked while an ownership warning was present.
