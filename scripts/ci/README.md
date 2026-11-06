# P20 native candidate on a disposable Windows runner

This is build infrastructure. It never installs/starts App, reads a model key,
provisions sandbox accounts or publishes a stable release. Only standard public
windows-2022 is used; no paid larger runner. The candidate branch prefix is
codex/p20-hosted-ci-*. Pushing the first reviewed candidate starts the workflow.
Manual dispatch is also present; a new workflow only on a non-default branch
may not yet be dispatchable by filename.

## Preserved contract

The existing scripts/build_native.py and package_native_release.py are reused.
Lock SHA256: 3736e573e634180a1443b0c3e2cdbbf3d28304cf17d76fd3752aab344356b44c.
Source SHA256: 37efcaa4a32880ecb16f3420f4efb6a39e9c82fb407a169ec67e0d74991763da,
9529 files, upstream 720fec862a608ae1e5878d1eecb04fb93192e93c.

Official rustup installs exact Rust/Cargo 1.99.1 if missing. No Rust fallback.
MSVC14.44/SDK26100 are preferred when present; the original lock does not pin a
local absolute MSVC path or SDK patch. The normal real x64 compile/link SDK
preflight remains mandatory and records the actual toolchain and image.

The original opt3 release profile, one explicit Cargo invocation for six exact
targets, jobs1, 7200-second build budget and four-GiB available-memory floor
remain. Native-unit compilation and assembly each have separate 1800-second
limits. The outer job includes those stages and input/artifact transfer.
Eighteen exact native component cases must execute once each; an empty filtered
run cannot pass. These cases do not replace actual BFE/network, Session3 or GUI
acceptance. Units cannot mutate the already copied production output bytes.

Only explicit Android and cached Java/Go/Ruby/PyPy tool families may be reclaimed,
behind GitHub-hosted/public/x64/run-ID guards. VS, SDK, Rust, Git, Python, Node,
OS, security products and services are kept. This cannot run on shared or
self-hosted computers. Actual free space and optional candidates are recorded before cleanup. If already at least24GiB, cleanup is skipped; otherwise unsafe/reparse families are skipped with a reason and other allowed families may be tried. Final free disk must still reach24GiB: measured local target
was12.22GiB, Cargo+Rustup5.04GiB, so advertised14GB minimum alone is insufficient.

## Controller-only provenance

The CI observer is Apache-2.0 codex-deepseek-runtime commit
17917162b707574ce86765149ab8b06acbdc3c96. Only three exact SHA256-verified public
files are downloaded under ephemeral CI inputs. The controller records their
URLs/hashes, delegates process ownership to this existing observer, and adds
the existing memory floor. No CDS launcher/agent loop/process module enters
src, the gateway wheel, native runtime or App execution.

Native inputs use the pinned upstream setup-rusty-v8 checksum chain: official
source archive hash, its trusted release-manifest hash, then exactly the
sandbox V8 library and binding. No closed App/sidecar is downloaded or shipped.

## Candidate artifact and remaining gates

The artifact retains the normal native archive/manifest, original build/source
and assembly receipts, eighteen case logs and sealed native libtest. It is
not App-qualified. Failures retain their original scoped evidence. It contains
no keys, App profiles or user conversations.

Verify workflow SHA, candidate.json, archive SHA and normal manifest before
direct transfer to Windows. Normal helper/provisioning, real LimitedSession3
and actual official App acceptance still run there: migration, long images,
compaction/cold recovery, permissions/tools and faults. CI never closes those
gates or updates stable main/tag.

Primary references:
- https://github.com/openai/codex/blob/720fec862a608ae1e5878d1eecb04fb93192e93c/.github/workflows/rust-release-windows.yml
- https://github.com/actions/runner-images/blob/main/images/windows/Windows2022-Readme.md
