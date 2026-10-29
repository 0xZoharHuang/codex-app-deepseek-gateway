# App runtime acceptance matrix

Current target is signed App **26.1023.6621.0** / native **0.165.0-alpha.1.2**
(`720fec862a608ae1e5878d1eecb04fb93192e93c`), with the O165 two-patch single
producer loaded in the actual App. Current engineering, memory and App MCP evidence
is recorded in the qualification ledger; an Internal Guardian defect is being repaired.
New-cohort qualification is in progress. The existing gate definitions
remain applicable; prior 0.163 observations below are historical and do not
automatically qualify this App update. See [qualification-status.md](qualification-status.md).


This is an execution ledger, not a support or release declaration. A local mock,
CLI RPC, selected model name, successful HTTP response, or final answer cannot
establish App runtime parity. Each passing row needs a concrete receipt and an
independent assertion against actual state. Unknown and not-run rows block stable
release when they are in the agreed scope.

## Current evidence pointer

The detailed C-run ledger below is historical and deliberately retains its
original failures and then-open assertions. Current combination, per-component
carry, bounded remaining gates and retained failures are tracked in
[qualification-status.md](qualification-status.md).

Historical 0.163 evidence: the signed App loaded N CLI `1dbf68a2...` with the original J
helper/runner/probe and installed r4 manifest `1c4da3f7...`. R4 still uses published
CDS 0.6.6 commit `d2edfa2a49dd9879d45375e7770ff8bd2ec3d753` and its 1,307 unique
installed software cases remain separate from App parity. Historical M/r4
completed the Jinja task with 908 official tests, 22 independent checks and a
native-written report; its 79 sealed references were independently reviewed.
The original failed windows remain failed, not relabeled as N executions.

Historical M/r4 V2 interrupt/same-child follow-up is separately evidenced. A05
selective two-session termination passed: A ended after one native Ctrl-C while
original B continued and naturally exited0. Its first missed-cancellation attempt
and observer2 missing B final kernel time remain explicit limitations.
A03 native RankedSearch/schema/read-only-call and A11 same-child V1 warm/cold
close/resume have passed their scoped actual cases. A09's original M/v2 defect
is retained; N/r4 has now completed two consecutive actual cold capacity
recoveries and manual GUI compaction. Full source-backed summary-prefix,
unconsumed-suffix and checkpoint checks passed on the existing V1 root.

A21 target-3 warm/revoke/grant passed the original strict validator; cache refresh
and expiry have separate scoped evidence, with older full-criterion failures
retained. Fresh-V2 N/r4 GUI Stop now interrupted its exact native turn. A separate
original App background-terminal control terminated the retained resumable
process early. The previous must-kill-on-turnStop assumption and missed QA
windows remain recorded; the cancellation/process files match fixed upstream.
Gateway recovery, changed weather/domain/cold-reference paths, A26 conditional
native availability and final r5 release/lifecycle binding remain open. Guardian,
memory, queues, web and other unchanged components retain scoped historical
receipts, not a claim of a full N rerun.
Actual web.run open/find/click and browser preview do not qualify IAB.

The native hash in the initial baseline is the original bundled runtime.
Current N and J source producers, plus historical M, are recorded per executable in the qualification
ledger and [native-build.md](native-build.md). An unchanged upstream version
alone does not establish byte identity or automatically transfer acceptance.

## Fixed baseline

- Windows 10 Pro 19045, existing admin interactive session; no desktop takeover.
- Official signed Codex App 26.1016.8120.0.
- Bundled native runtime 0.163.0-alpha.11.1, source commit
  `680c4ba93ec79c292287fb9e0b627dc7a76e3b5e`.
- Native SHA256 `e757246b4de87359e660647fa9514955a0971ff6a30affdd8c145d1d5ece7b84`.
- OpenRouter request model `deepseek/deepseek-v4.1-flash-1017`.
- Historical c110 protocol dependency commit `4f24af36f62c538ba35817ad48aa49d4308220b5`.
- Current source pin: published CDS 0.6.7 / `cb788738864e97a16cde82f3c584164162ab6f7c`;
  current r4 installation still uses the separately bound 0.6.6 input; r5 installation is pending.
  The pin does not relabel earlier installed/App evidence or close other gates.

Provider route selection is explicit and precedes sending the request. The
requested provider is not proof of the served provider. Compatibility providers
must be present in the operator's allowlist; upstream errors never trigger an
implicit model or provider switch.

## Historical C-run gate ledger (not final H qualification)

| Gate | Mechanisms and assertions | Current evidence level |
| --- | --- | --- |
| Baseline and isolation | Signed package, exact runtime hash, current-user App registration, other sessions and CDS unchanged | Signed package and private candidate C loaded in the second private App run; default native/browser metadata unchanged for that launch; old shared sandbox objects unchanged after real shells. End-of-run drift check remains pending |
| App connection | Visible Codex mode and model, project selection, real tool write and independent readback | Private App C has run the engineering and research tasks through the fixed DeepSeek model; actual native tool output and gateway receipts observed. This does not close every App gate |
| Streaming and history | Completed/failed/truncated streams, multi-turn tool call IDs, custom/function/namespaced tools, strict structured output | Real multi-turn custom/function calls and matched outputs observed. One B request was cancelled/disconnected then the task continued. Structured-output and deliberate fault/side-effect cases remain pending |
| Code Mode and discovery | Native functions wrapper, actual shell/patch tool lookup, deferred tools and schema expansion | Private App C executed native Code Mode, tool discovery, shell, patch, MCP and yielded-cell waits. Remaining catalog/schema edge cases are not inferred from these successes |
| Permission boundaries | In-workspace allow; outside/network deny; explicit human approve and deny; no full-access workaround | Restricted/on-request profiles retained. Native Python access denial, actual user Deny of download, and one-command Allow once for pytest observed. Controlled outside-file/network and revoke/terminate cases remain pending |
| Guardian | Real asynchronous scorer and synchronous reviewer, allowed and denied actions, untrusted historical content negative case | Source/config routes established; actual synchronous reviewer and asynchronous scorer qualification still pending |
| Native agents | Parent-child-grandchild relationships, follow-up, waiting, interruption, closing, message attribution and cold recovery | A DB graph proves root-child-grandchild, inherited model/permissions, directional messages and follow-up turns; B independently spawned a read-only child. Interrupt/close/cold-recovery remain pending |
| Session controls | Queue, goal, cancellation, active background tool termination and revoked permissions | Real create_goal/update_goal transitions and yielded tool waits observed. Completed goals may leave the active goals table. Queue, deliberate cancellation, background termination and revocation remain pending |
| Context lifecycle | Compaction retains usable tools/history; native memory extraction/consolidation use explicit DeepSeek routes | Memories enabled for the natural idle-window trial; neither elapsed time nor auxiliary route configuration establishes successful extraction/consolidation. Compaction and both real memory stages remain pending |
| App development UI | Files and diff, terminal, supported worktree operations, model selection and cold App reopen | Actual private App project/model/task paths and native file modifications observed. GUI diff/terminal/worktree and cold-reopen qualification still pending |
| Extensions and web | Skills, local MCP/plugin discovery, real search/open/find/link following | Private App skill read, local MCP calls/digests and real search/open/find/click succeeded. OpenAI-host page failures retained; PDF/image/time and remaining plugin lifecycle paths not yet App-qualified |
| Engineering repair | Multi-file practical repair, edge cases, failed tests corrected, independently checked artifacts | A: 119 passed, then independent oracle found two gaps; second turn repaired four reproduced failures, then native child/grandchild review found NaN/signed-zero gaps. Those two failed before repair; latest full run passed 125 tests. Final artifact oracle and cleanup reconciliation remain separate |
| Research and collaboration | Web source checking plus an extension and native child agents, independent factual/result check | B completed a cited research file with real MCP/web and a native read-only child. Incorrect gateway-traffic wording was corrected through App. Source access limitations and absent compatibility probes are explicit |
| Recovery | 429, upstream errors, partial stream, disconnect, gateway exit, auth invalidation and restart | Mocks plus one naturally cancelled/disconnected B generation observed. Deliberate 429, partial stream, auth invalidation, gateway exit/restart and duplicate-side-effect checks remain pending |
| Product lifecycle | Immutable install, private credential storage, startup/status/diagnostics, version mismatch, exact rollback/uninstall | Production configuration restore has independent regression coverage, including legacy checkpoints. Default config restoration and private launch/provisioning observed; full install/upgrade/uninstall and rollback workflows remain pending |
| Public delivery | Secret scan, README, reproducible dependency hashes, Windows git push and release provenance | Publication is authorized only after all agreed gates pass; that condition is not yet met |

## Failure and side-effect accounting

A legitimate early tool completion can be executed by the native runtime before
a later stream failure. Consequently, absence of `response.completed` does not
prove absence of side effects. Fault scenarios must distinguish failure before
and after the tool completion and count effects with stable operation identities.
Retried App requests and native call IDs must be inspected, not inferred from
local gateway retry settings. A final success never removes an earlier failure.

Evidence outputs must be frozen after their producing process/turn has ended.
Do not hash an output file that the native runtime can still overwrite. Archive
raw evidence separately from sanitized public reports, with per-file hashes.

## Scope

Parity means local runtime capabilities of this exact App build on this Windows
host. Model intelligence, model-inherent modalities, and cloud/account entitlements
are reported separately. Windows 10 tests do not establish Windows 11 MXC support.
No feature is silently disabled to turn a failing gate into a passing one.

## Historical C-run execution evidence

The first App turn was rejected because the native default cached-search contract
cannot be supplied by Exa. The second turn, after explicitly selecting live
search, was rejected because hosted Exa does not provide the native image-search
contract. Both failures remain in the original chat and private evidence.

The third turn used native standalone web tool discovery and completed a local
file task. Native Code Mode executed shell, discovered tools, applied a patch after
a one-file human approval, waited for a yielded cell, read the resulting bytes,
noticed an extra newline, corrected it using the native shell, and read back the
exact expected 22 bytes. Nine upstream generation records were independently
read back: provider_name=DeepSeek and the fixed model identity matched.

Initial shell failures exposed an acceptance-fixture ownership problem: SSH-created
files belonged to Administrators, preventing the unelevated desktop process from
performing native sandbox ACL refresh. Only these new fixture entries had their
owner changed to the intended user; their DACLs were verified unchanged.

At that stage the App was closed because the official Windows 10 legacy sandbox
shared machine-level accounts with other Codex homes. Per-home secrets did not
isolate those accounts or network policy identities. The private namespace patch
and later actual provisioning addressed that collision; the original failure is
retained. No weaker sandbox or full-access setting was substituted.

The standalone web endpoint now has backend implementations for text search,
open/find/click, time, explicitly selected Commons image search, and real PDF text
extraction/screenshots. The service was initially stopped during isolation repair.
It is now serving the second private App run, where text search/open/find/click
were exercised. PDF/image/time paths still have backend evidence only. A visible
tool or a backend-only result is not an App acceptance pass.
Configuration/lifecycle tests now include three-way restoration that preserves real
App-added settings; this does not close the remaining native-runtime/GUI gates.

## Private runtime C: first GUI run and isolation repair

The first private App was launched from the signed package with native candidate C
(SHA256 `92520dddcf87c37fe4619340c6e36b9e5bea6564e952d3f7005e1952cc5af9db`).
Both actual app-server processes had the private home, private executable and
verified bytes. The initial launcher receipt had incorrectly accepted a short
native probe as a backend; this assertion is now narrowed to actual app-server
arguments and regression-tested. The original receipt and corrective observation
are retained. Windows PowerShell 5.1 also exposed an empty PSScriptRoot parameter
default; default resolution was moved after binding and tested in real PowerShell.

The actual GUI exposed a further isolation failure: CODEX_ELECTRON_USER_DATA_PATH
isolated Electron state, but the embedded browser still modified the default
Windows browser profile. The default native .codex file metadata stayed unchanged.
The owned App was stopped before model tasks were submitted. Its pending native
UAC request was cancelled by exact PID/creation identity; unrelated processes were
preserved. Browser-profile isolation blocked further tasks until the launcher was
corrected. The second private launch then used the separate frontend/browser paths
and its default native/browser metadata comparison was unchanged; final long-run
drift checking remains separate.

The public native managed sandbox setup subsequently completed under the same
admin interactive identity with elevation, using the fixed private runtime.
Private accounts, group, firewall and WFP objects were created; old shared account
password timestamps, group membership, firewall/WFP objects and RegisteredCore
values remained unchanged. This is provisioning evidence, not a substitute for
actual App sandbox execution and approval tests.

Memory and asynchronous Guardian are separately gated by native configuration.
Routing auxiliary models alone does not enable or qualify these mechanisms. Their
effective configuration and real pipeline/scorer runs remain required gates.

## Second private App run: observed paths and open work

Run `335ddc05731144b08fef671bd571ff82` launched the signed App with private native C.
The isolated browser launcher regression suite passed 51 fixtures. The actual
`default-app-metadata-before-private-launch-r2.json` /
`default-app-metadata-after-private-launch-r2.json` comparison reports unchanged
metadata. The initial browser isolation failure and cancelled UAC receipt remain
retained; successful relaunch does not erase them. The private namespace provisioning
receipt and `private-sandbox-after-first-live-shell.json` show unchanged shared
account SIDs/password timestamps, group, firewall/WFP and RegisteredCore state.

Task A root is `historical-private-engineering-root`. Its database source and first
own session metadata establish the real child and grandchild relationships. Forked
history contains copied parent metadata, so taking the last session_meta would
misattribute a child. Agent messages and tool results are assigned by their own
turn/ordinal, with copied prefixes excluded.

The initial engineering turn's 119 green tests did not close the task. An independent
public-API oracle exposed empty-table/proxy deletion and unowned checkpoint-change
gaps. The App model's second turn produced four genuine failing regression cases,
then four passes after repair, and `123 passed in 5.49s` for its full test command.
Candidate source SHA256
`49773b5988f159526258df7df92c866f583f8275c0ff799814a0a3e44ff40f68`
passed the independent 11-assertion oracle recorded in
`complex-repair-independent-oracle/public-semantic-independent-assertions-r3.json`.
Any later candidate change requires rechecking that exact artifact. Native follow-up
review and cleanup reconciliation are still tracked separately.

Earlier failures remain evidence: denied private Python access; one Code Mode syntax
error; a Python-download command actually denied in the App; 119 fixture setup errors
in the default temp directory; then three assertion failures before the initial fix.
One cleanup command returned exit code zero while PowerShell reported PermissionDenied
and Test-Path remained true. That command was not a successful deletion. Its original
arguments lacked require_escalated; Allow once did not silently widen the whole thread.

Task B root is `historical-private-task-1`. Its own first turn recorded
17 successful local MCP calls, 39 native web Extension items, and a separately spawned
read-only child. Search selected an OpenRouter official document; opening it, finding
text within that returned document, and clicking its real link ID succeeded. Attempts
to open some OpenAI-hosted pages returned private_target/HTTP 403 or upstream-document
HTTP 403 wrapped as 502. These are retained failures, not proof that a page is empty.
The report distinguishes search excerpts from retrieved full text and retains the
search generation-provider metadata limitation.

Gateway receipt metadata for B's prompt-cache context contains 116 completed,
HTTP-200, delivered generations and one cancelled/disconnected generation in the
initial turn. Cache context is not a unique per-child thread attribution field.
The fixed DeepSeek model was observed; requested_provider=deepseek is a routing
request, while observed_provider was null in these receipts. Do not turn that null
into proof of an independently observed provider. The task's own model traffic is
real; the absence of a separately constructed protocol compatibility probe says
nothing to the contrary. The report's earlier ambiguous wording was corrected by
an actual App follow-up turn, with file readback.

Private audit snapshots are `native-app-two-task-audit-r1.json` and
`live-task-phase-one-handoff.json`. They are live/phase snapshots, not a frozen final
acceptance bundle. Goal completion returned by the model is also distinct from
independent closure; the active goals table removes completed records and may
already hold a subsequent goal for the same thread.

## Production configuration restore audit

The sample engineering task is a historical source snapshot. Its tests do not
qualify the production gateway module. Independent production checks found existing
coverage for typed B-to-A differences, empty-table presence, proxy preservation and
validation before the exact-current fast path. Seven focused checks passed.

Two separate production gaps were then reproduced: an already-restored checkpoint
without C/R evidence could self-certify its hash without validating owned changes;
and implicit tomlkit parent tables repeated a child header comment during pruning.
The added regression suite first produced eight failures and four passes. The fix
validates every schema-2 checkpoint's full typed B-to-A transformation, including
restored paths, and moves comments only from their actual explicit header. It does
not deduplicate intentional equal comments in distinct headers.

Older restored checkpoints remain compatible: exact-original restores work, and a
legacy schema-2 partial restore must match its recorded current hash and be a byte
fixed point of the independently rerun owned transformation. That establishes the
current restored owned state, not missing historical C evidence. New checkpoints
retain their stronger captured-input/output proof. Schema 1 can establish only its
original byte result. An initially overstrict legacy check was caught by two failing
compatibility fixtures and corrected; the failed run is retained.

The final production module/CLI regression run passed 168 tests in 7.17 seconds.
Raw before/after logs are `production-config-regression-before-r1.log`,
`production-config-legacy-compat-before-r1.log` and
`production-config-regression-after-r2.log`. These are isolated fixture checks; they
neither modify the live App configuration nor close App GUI/lifecycle gates.

Subsequent A native child/grandchild review exposed NaN and signed-zero cases.
The model reproduced two failures, fixed its float semantics and obtained
`125 passed in 5.24s` (`exec-da248cb5-f61c-4d76-ae91-7109c2300dff`).
The sample source then changed to SHA256
`65dbe30a2ed3a4eef4bf66b1e3256f46180b66b0787efd73ef2c2226d4cdce25`;
the earlier 11-assertion oracle must not be attached to that new SHA without rerun.
Native child reviews completed statically, but the grandchild's attempted Python
execution again failed under the restricted sandbox. No independent child test
pass is claimed. Their recorded checkpoint trust and final-write race limitations
remain explicit. `native-app-two-task-audit-r2-incremental.json` preserves the delta
and verifies the preceding rollout prefixes were unchanged.

Production was checked independently for the same float family. It reproduced two
unchanged-NaN restore errors and two applied-path signed-zero scope bypasses. Two
additional restored-path checks already failed closed through the fixed-point guard
but missed the expected earlier unowned-change classification. `_equal` now treats
two NaNs consistently and distinguishes zero signs, recursively retaining the
existing strict type comparison. The first post-fix run had one overstrict new test
that demanded byte identity after a partial merge; that assertion was corrected to
check every retained key, NaN/infinity/zero sign and the single user comment. The
existing byte-exact no-later-edit assertions were retained. All these failed runs
remain in the private evidence. The final module/CLI run passed **174 tests in
7.60 seconds**, recorded in `production-config-float-after-r2.log`; this supersedes
the earlier 168-test production result, not the independent App task results.

Manual compact was selected through the real App UI, but the audit snapshot at
this update had no compaction marker for B. UI admission is not completion; the
compaction output and a subsequent context/tool-recovery turn remain pending.
