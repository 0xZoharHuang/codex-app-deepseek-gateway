# R222: native LaTeX boundary and candidate qualification

Stable App support is NOT QUALIFIED.

Actual DeepSeek App create_latex_project and source editing succeeded on official
Windows App 26.1023.6621.0 / native 0.165.0-alpha.1.2. Both incomplete and complete
three-file projects failed before source processing: CreateProcessAsUserW error 5
for packaged Tectonic. open_in_codex opened the project; successful compilation
and PDF preview have not been proved.

An ordinary limited-user process with original App package identity ran the
original compiler's --version. Native restricted launch failed; a byte-identical
copy in the secured native .sandbox-bin ran. An earlier copy in another private
directory lacked namespace RX and also failed, so that observation alone does
not establish a path-only cause. No WindowsApps ACL, Defender setting, global
group or approval policy was changed.

## Candidate inputs and boundary

| Input | Value |
| --- | --- |
| build lock SHA256 | 5bfdec0bc745a3a31e209078b1424d9f063d1eea73918eb394c8eae59f01648b |
| source SHA256 | 3830ef7a9318ff1c6016ecaa6840805250c527c9a2b75b1e77b6b9c334210548 |
| source files | 9516 |
| fifth patch SHA256 | e9585d91f7aa3db567c067a628040be8b4de2e594533e2af224a369731085fd7 |

packaged-tectonic-materialization-0165.patch recognizes only the OS-staged pinned
official package and compiler path/SHA. Existing read/deny policy applies to
original/resolved source and destination. Read-root overrides and redirected
stores are rejected. Native atomic helper copying and private locking are reused.
Only argv[0] changes; arguments, cwd, environment, token, network, approval,
transport and actual App compilation remain native. There is no second compiler
or agent loop. Known mismatches fail; unknown executables retain native handling.

The installed four-patch CLI is still
18ef99bac7865066e1e802521e0c0c058862956cc569a6abc193b2005fa36b7f.
This source recipe is not a deployed runtime.

## Completed targeted qualification

14 materialization tests and one native exact/glob deny-policy test passed.
Filtered tests are not counted as passed. Coverage includes corruption,
both read/deny boundaries, uncreated destinations, ambiguous paths, read
overrides, redirected/missing stores, argument preservation and concurrent copies.

114 targeted Python build/packaging checks passed. A real MSVC compile/link
preflight passed; an incomplete SDK environment failed before source/owner/output
mutation. The probe is never run. A limited process resolved the pinned App via
the Windows OS API. Earlier controller and missing-SDK-header failures remain
retained. SDK INCLUDE/LIB were corrected in the build child environment only.

The optional --target-dir selects an existing real owned Cargo target separately
from --cache-dir/Cargo home. Redirects and overlapping roots are rejected;
default behavior is unchanged. Use standard build ownership/CAS transitions,
not forged manifests or Cargo fingerprint changes.

## Storage recovery evidence

Selected non-executable compiler-cache compression completed its compact batches
but post-hash verification failed when D was full. The consumption cause was not
established; that run does not prove byte verification. Two duplicate cache CLI
outputs were retired after exact installed-byte and process checks.

Frozen old outputs were archived privately and archive bytes checked by size/SHA
before retiring listed expansions. The first ZIP verifier used Windows separators
and failed before deletion; its valid archive was independently reconciled with
POSIX names. Original manifests and failures remain.

Cargo cache migration uses the original inventory and durable verification/
retirement ledger, retains hardlinks/timestamps and pauses at a C free-space
floor. No Cargo may start with a partial cache. Source, credentials, App profile,
data home and installed native images are outside the move.

## Remaining real App proof

Build/package/admit one exact four-binary producer from the five-patch source.
Verify main and separate LaTeX preview app-server executable paths and SHA.
In the real DeepSeek App, obtain a missing-source compile error, restore sources,
compile the multi-file project and read back its PDF in the actual App preview.

The worktree tool restoration defect, native IAB/computer-use/server queue gates,
secure-desktop boundary and other [open qualification items](qualification-status.md)
are not closed by this patch or unit tests.
