# R222: native LaTeX boundary and candidate qualification

Stable App support is NOT QUALIFIED.

The actual official App now loads the five-patch native candidate and has passed
the LaTeX failure/repair, preview and PDF-export scope described below. Other
local-runtime gates remain open; this is not stable App qualification.

## Original failure retained

Actual DeepSeek App create_latex_project and source editing initially succeeded on
Windows App 26.1023.6621.0 / native 0.165.0-alpha.1.2. In the prior four-patch
candidate, both incomplete and complete three-file projects failed before source
processing: CreateProcessAsUserW error 5 for packaged Tectonic. open_in_codex
opened the project, but compilation and PDF preview had not then been proved.

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

The original four-patch CLI 18ef99ba remains retained for rollback. The five-patch
runtime is now actually loaded through the installed product's formal interfaces;
its actual App evidence is recorded below.

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

## Required real App proof and its disposition

The required five-patch four-binary producer, formal admission, main and separate
preview-server identity checks, actual missing-source failure/repair, compilation
and App preview were completed as recorded below. The first cold compilation
timeout remains part of the evidence.

The worktree tool restoration defect, native IAB/computer-use/server queue gates,
secure-desktop boundary and other [open qualification items](qualification-status.md)
are not closed by this patch or unit tests.

## Producer and independent-target packaging correction

The standard five-patch, four-target producer completed successfully in one Cargo
invocation (56m08s). Its raw manifest SHA256 is
85781c2d9951f4ed77b261addb07d3964a3b7e223a9b39d7e20951affff57a0a.
That producer result alone was not App qualification. Formal preparation, binding,
configuration and actual App loading were subsequently verified below.

| Native output | SHA256 |
| --- | --- |
| codex.exe | ebec4115b77c4710feb71d9d460d893bb6cb164c2fbee906087783d83ac685fa |
| codex-command-runner.exe | b96e440ae0e5f629f89d4fbfbd9590036e6bbb578d74df7d4203c149a25c2eb1 |
| codex-windows-sandbox-setup.exe | 87713953bb9219219bd35906ec083a2e91e455a84391cc8a964c6697c78eba85 |
| codex-windows-managed-deny-probe.exe | 8e93f4291093cbf37930de393eb81303f8aa3d199960e189042274ce436708f4 |

The first formal package attempt rejected the real receipt before creating output:
its old rule required target == cache/target. Earlier synthetic tests exercised
only that default layout. The corrected packager retains exact command, source,
lock, profile, Cargo-home and four-output validation. For an independent target,
it reuses the standard builder's actual path policy: absolute recorded
target/source/output, existing ordinary target, no redirects and no protected
overlaps. It neither rewrites the producer receipt nor infers a cache alias.

122 focused build/packaging checks passed after this correction, including both
four/five-patch independent-target projections and missing, relative, unrecorded
or overlapping target rejection. Native Rust source and the four binaries did
not change, so this packaging fix does not require another native compilation.

## Additional independent coverage follow-up

The actual native feature command confirmed hooks stable=true and
request_permissions_tool under-development=false with App config unchanged.
Source defaults alone were not used as an effective-config assertion.

Before final release, separately verify actual App fork/history clipping and
cold recovery with inherited web references; native thread/revert semantics
(which do not undo files); strict turn/start.outputSchema after real tool use;
and supported Command/McpTool lifecycle hooks plus blocked zero-effect tool use
and actual GUI file Undo. Prior subagent, Guardian and Plan-question tests do not
substitute for these distinct paths. Prompt/Agent hook protocol enum entries
are not executable handlers in this pinned runtime and are not added as tests.

## Actual normal App result after the formal native upgrade

The installed product completed prepare-runtime, bind, configure and app-launch
as the intended non-elevated admin desktop user. Preparation correctly rejected
the initial SSH/elevated context and an existing dedicated App profile; neither
guard was bypassed. The original App was closed once using its own File/Quit
menu. Its complete recorded process family exited. The UI observer then returned
NoSuchProcess after the effect; that observer failure is retained, not a second
Quit attempt.

The App configuration SHA256 stayed
cbf8819cbcb2ee4b245b8840550835c5fc457365bafbb3993048a023d8ab8b7f,
the existing gateway PID/birth stayed unchanged, and the same immutable catalog
and enabled native App Tools were reused. Normal launch used no debug switches
or temporary computer-use flags. The signed official App actually loaded CLI
ebec4115b77c4710feb71d9d460d893bb6cb164c2fbee906087783d83ac685fa.

DeepSeek resumed the original owned App thread and performed three native
compile_latex_document calls:

1. The first complete-project cold compile timed out. The real error was
   compile-failed / Compilation timed out / command timed out. It was not
   replaced by a success claim.
2. After the model temporarily moved one included source file, the original
   compiler returned a genuine LaTeX missing-source diagnostic for
   sections/evidence.tex. The original file was restored byte-for-byte.
3. The model appended a new acceptance marker to that restored source and the
   native tool returned success. main.tex and scope.tex were unchanged; no
   temporary hold file remained.

Passive process evidence separately identified all three dedicated preview
servers by CODEX_INTERNAL_ORIGINATOR_OVERRIDE=codex_latex_preview, their exact
new CLI hash, and registered-Core being unset. Their native runners and actual
Tectonic processes matched the expected runner/compiler hashes. Native logs
recorded the pinned original-to-effective compiler path adaptation.

The actual App LaTeX editor rendered the resulting three-page PDF. Its PDF text
layer contained the new nonce, independently of the source editor and chat.
The native Download/PDF GUI action exported a real 41,924-byte PDF. pypdf 6.21.1
independently parsed all three pages and found the exact nonce on the last page;
an independent PDF renderer also displayed that page. PDF SHA256:
316569c9f80ae344f6981c7a28988da4cfe850a6a56c358e68906cd0ac17feaa.
The controller did not compile or alter the document.

The native compilation tool returns status, not a filesystem PDF path; the PDF
is held by the App preview until exported. The model correctly declined to invent
a path. A prompt mistakenly requested ticker_canary.py; the actual protected
control is ticker.ps1. This preparation mistake and its real file-not-found
result are retained separately from runtime failures. An independent readback
confirmed README.md (888 bytes) and ticker.ps1 (1,398 bytes) stayed unchanged.

This closes the narrowly defined native LaTeX warm failure/repair, App preview,
export and independent PDF readback scope. It does not prove a timeout-free
first cold resource load, nor close unrelated runtime gates or stable release.
