# Native App fault acceptance fixture

This is a QA fixture, not an alternative agent runtime. It starts an isolated
loopback upstream and the production Responses bridge with test_upstream=True.
Codex App remains responsible for tools, approvals, threads and retries. The
fixture never starts/stops an App or edits its configuration.

The default is synthetic-only. HTTP errors, partial text and scripted counter
tool items are clearly synthetic and are not evidence of DeepSeek task quality.
The selftest uses only loopback mock traffic and the standard MCP SDK, never
WinCred or OpenRouter. Its tool-frame checks are protocol checks, not evidence
that Codex App executed a tool.

## Prepare and connect (parent-controlled acceptance phase only)

Use the project venv and a NEW state directory under this project's .evidence.
Initialize and run the server as the intended Limited InteractiveToken user:

    python.exe -I -X utf8 fault_server.py init --state-dir <NEW-QA-DIR>
    python.exe -I -X utf8 fault_server.py serve --state-dir <QA-DIR>

The ready.json descriptor contains the two random loopback ports, exact PID and
creation time, nonce hash, production bridge source hash and a 900-second lifetime.
It contains no authentication values. The private fixture.json has three random
QA-only tokens; it never contains an OpenRouter key.

During the next controlled App configuration window, back up the private config
and change only the existing custom provider's base_url to ready.json gateway_url
and its official auth.command/args to:

    command = "<PROJECT-VENV>/Scripts/python.exe"
    args = ["-I", "-X", "utf8", "<fixture>/fault_server.py",
            "auth-token", "--state-dir", "<QA-DIR>"]

Use auth-token only as the provider's official auth command: stdout is local QA
authentication, not diagnostic output. Keep the fixed model/catalog, sandbox and
approval policies intact. Do not use this fixture's unsupported web endpoint as
a web acceptance result; this fixture tests Responses failures.

Register the counter through the current private native CLI, with its explicit
private CODEX_HOME and no other App/CDS home:

    codex.exe mcp add cdag_fault_counter -- <PROJECT-VENV>/Scripts/python.exe -I -X utf8 <fixture>/fault_server.py counter --state-dir <QA-DIR>

The standard SDK tool is record_effect(case_id). It accepts only pre-registered
case IDs and appends a row EVERY time. It is explicitly non-idempotent; repeated
calls produce counts 1, 2, ... and cannot hide a duplicated effect. It has no
arbitrary file, shell or network argument.

## Exact native-thread arming

Control commands read only this fixture's private control token and send it in an
HTTP header. Tokens do not appear in command arguments:

    python.exe -I -X utf8 fault_server.py control --state-dir <QA-DIR> --command-json '{"action":"status"}'
    python.exe -I -X utf8 fault_server.py control --state-dir <QA-DIR> --command-json '{"action":"arm","fault":"http_429","thread_id":"<ACTUAL NATIVE THREAD-ID>","repeat":5}'

Faults match the authenticated native thread-id header exactly. Native source
core/client.rs shows that prompt_cache_key may be overridden/shared; even the
root session-id header may carry cache affinity. Neither is a unique thread ID.
The QA bridge adds a request-context middleware and an aiohttp TraceConfig to its
own loopback upstream transport only. It forwards native thread-id/session-id to
that local fixture without changing the production bridge source or model body.
These QA headers are never forwarded to OpenRouter. Use the actual App thread ID
and verify its observed hash before counting a fault as targeted. Missing native
identity is never a fault target. An unarmed synthetic request returns explicit
409 without contacting a model; preserve that discovery failure separately.
Diagnostics expose only thread/session/cache-affinity hashes.

Arming returns a new case_id. For the real side-effect task, tell App to call the
counter with that exact case_id once and then explain its result. Reuse the same
case_id when rearming a later phase of that same test. Each case is persistently
bound to its first native thread; cross-thread reuse is rejected, including after
cold restart. Faults consume a bounded
repeat count from 1 through 5; record each native retry and Retry-After timing.

## Fault matrix and required App assertions

- http_429 / http_503 / http_401: preserve status and (429) Retry-After. Show a
  real App error/retry or authentication state; no fabricated completed response.
  The bridge does not retry internally. Attribute every extra request to its
  actual source.
- text_cut: valid response/message/text-delta frames followed by EOF without a
  terminal. Partial output is allowed; successful completion is not.
- tool_incomplete_cut: function added + complete argument delta, without
  output_item.done or terminal. App must not execute the incomplete call.
- tool_done_cut: a completed function item is exposed, then EOF before the
  response terminal. Observe the fixed native runtime's actual execution timing.
  Once an effect is recorded, retry/cold resume must not repeat it merely because
  the enclosing response failed.
- after_effect_503: first permits normal pass-through. It injects a single 503
  only after BOTH a durable counter effect and a matching completed tool-result
  entry appear in the next request. That result must reference a direct advertised
  counter function call with the same call_id and exact case_id arguments; an
  unrelated tool printing similar JSON, an orphan result or mismatched IDs never
  arms the fault. Missing direct call history (for example some Code Mode forms)
  remains not-fired/inconclusive rather than inventing a counter tool. Call IDs are logged only as hashes. Then it
  resumes unchanged pass-through; count must remain one unless a separate new
  action is explicitly requested.
- live_tool_incomplete_cut / live_tool_done_cut: the same two stream boundaries
  applied to a real provider's counter call. Exact advertised counter function
  marker, exact session and exact case_id are required. If no matching direct
  counter function is advertised, fault firing remains unproved; no arbitrary
  exec/Code Mode call is fabricated.
- synthetic_success is an explicitly synthetic recovery response. It is never a
  real DeepSeek result.

For App tool-cut cases, the direct counter function must have been discovered and
advertised through the native mechanism. Missing declarations produce explicit
409 in scripted cases and do not consume a fault. Do not disable discovery or
replace native scheduling to manufacture a result.

## Explicit real pass-through

Live mode is OFF after every start. Enable it only in the controlled real-model
phase:

    python.exe -I -X utf8 fault_server.py control --state-dir <QA-DIR> --command-json '{"action":"enable_live"}'

The server verifies its own Limited interactive Windows identity. Only then can
a request call the existing credential reader for ai.codex.app-deepseek.openrouter.
The key stays in memory and is never supplied to the App, fixture CLI arguments,
repository or logs. The destination is fixed OpenRouter Responses; the production
bridge's pinned DeepSeek model and single explicit provider selection are passed
through unchanged, with fallback forbidden. Optional --proxy-url is per-fixture,
explicit HTTP(S), and rejects embedded credentials; never guess a proxy or modify
the machine's settings.

Non-target auxiliary sessions are never fault targets. With live explicitly
enabled they may pass through under the same total budget of eight requests.
This prevents the test filter from masquerading as disabled Guardian or other
native mechanisms. Counters record intended route, not proven actual provider;
retain real upstream/gateway receipts and App evidence for that separate claim.
The global fixture limits are 64 requests, 64 effects, 256 events, 2 MiB payloads,
four concurrent requests, at most one per native thread, and the configured lifetime. Exceeding a budget is an
explicit failure, never a switch to another model/provider.

## Stop, restart and cleanup

    python.exe -I -X utf8 fault_server.py control --state-dir <QA-DIR> --command-json '{"action":"stop"}'
    python.exe -I -X utf8 fault_server.py serve --state-dir <QA-DIR> --reuse-port

Same-port restart is explicit. A conflicting port fails; the fixture does not
kill its owner or silently change the App URL. Protocol receipts, effect rows,
event history and live-request budgets persist. Plans and live enablement do not:
re-enable live and arm passthrough with the same case_id/native thread only after
reviewing the prior outcome. There is no request/tool replay on restart.

For an actual process-loss test, the controller must match ready.json PID AND
creation time before terminating only this fixture. Graceful stop is not a crash
test. Restore only the private App configuration and remove the fixture's MCP
entry during the controlled cleanup window. Stop fixture processes, retain
sanitized receipts/effect evidence, then remove only its owned QA directory.
Never terminate by executable name or remove production/default App/CDS state.

Run selftest.py with the project venv to verify the harness. Original failures and
reruns remain under .evidence. Passing selftests does not close any App GUI gate.


## Scripted thinking-mode history (QA protocol revision 2)

Scripted text and tool responses include a completed plaintext reasoning item
from `synthetic_reasoning.py`. It explicitly identifies fixture-supplied
transport metadata and claims no model reasoning, authorization or Guardian
decision. It precedes output index 1 and is retained in completed snapshots.
Cut scenarios still cut at the same text/argument/tool-completion boundary and
omit the response terminal; no fault is turned into success.

This prevents a malformed QA-created assistant group from causing a later
thinking-mode provider request to fail for missing reasoning. It does not repair
or invent missing real model reasoning. Real provider streams are unchanged.

`codemode_fault.py` extends the same bounded fixture for native Code Mode.
Its loopback selftest requires the explicit qualified source path:

    python -I -X utf8 selftest_codemode.py --native-source ABSOLUTE_NATIVE_SOURCE

Source-derived/mock bindings are not accepted as actual native discovery.
The original frozen R153/R155 runs and their failures remain unchanged.

The common scaffold has a separate zero-model HTTP/SSE roundtrip check:

    python -I -X utf8 synthetic_reasoning_selftest.py --native-source ABSOLUTE_NATIVE_SOURCE

It covers scripted text, completed/incomplete tool cuts and Code Mode outputs.
The actual production bridge converts each response and its next high-effort
history request. Removing the scaffold is a negative control for a local strict
oracle. The oracle's HTTP 200 is never provider acceptance or an App GUI pass.
The original cut cases still finish as failed, and no synthesized tool executes.
The Code Mode selftests write run-specific evidence under repository .evidence;
their explicit mock binding is accepted only by opt-in loopback selftest
instances. A normal fixture requires durable actual native introspection before
arming an effect fault.

## Finite recovery and actual App discovery

For an explicitly bounded GUI fault window, codemode_fault.py serve accepts
--finite-recovery. After one targeted discovery/cut/effect fault is consumed,
the next matching native request receives one synthetic fixture recovery.
The original cut, failed stream, completed tool item and durable effect remain
recorded. This recovery is never a DeepSeek answer or an authorization decision.
Completed tool items can execute before the enclosing response terminal: inspect
the real non-idempotent ledger and native call/result IDs instead of assuming a
cut means zero effects. Never reset a live run's nonce, deadline or counters.

Recovery waits for the original global in-flight guard, including native
auxiliary requests. A bounded busy timeout remains an explicit 503; it cannot be
reported as completed. selftest_codemode.py checks the real HTTP/SSE boundaries,
the guarded recovery, timeout and negative controls without running App/native or
a model. Actual GUI execution is a separate requirement.

A separate app-server cannot query another process's active ThreadManager.
An active-writer/thread-not-found result must remain a failure of that query,
not a reason to bypass the writer guard. Binding supports two explicit sources:

- native_mcp_server_status: the original real native RPC envelope, plus same
  thread completed ALL_TOOLS introspection and durable raw evidence.
- independent_standard_MCP_tools_list: server_name and the real standard MCP
  server_tools_list_response, checked against actual App ALL_TOOLS callable
  discovery, completed call/output history and raw evidence hashes. This is a
  service catalog comparison; it does not claim that an App private RPC was
  observed.

Unknown provenance, duplicate declarations, different schema/annotations,
missing callable/history, foreign threads or changed evidence fail closed.
Source-contract mock evidence still requires the selftest-only constructor
opt-in; normal serving does not accept it.

For the separately authorized real-provider phase, serve with
--enable-live-reader --gateway-policy-home <OWNED-GATEWAY-DATA-HOME>.
The selected installed interpreter's production package is preferred. The
gateway policy supplies the existing trust store/proxy and image transport
factory. This only makes the dedicated Windows credential callback available:
live forwarding remains off until the original authenticated enable_live
control, and all root/auxiliary calls share the unchanged eight-request budget.
The summary's model_requests counts live attempts, not a constant zero. Use protocol
receipts and served generation metadata to confirm actual provider requests.

--trace-images adds image_trace.py to the existing bridge/relay sessions.
It records bounded typed-reference counts, schemes and hashes at native input,
prepared bridge wire and actual provider POST, joined to the gateway receipt.
It never records raw bodies, prompts, reasoning, tokens, image bytes or URLs.
A small inline picture does not qualify the large-upload path. A provider 200
does not prove visual interpretation: use a hidden, independent image oracle and
read the actual App result. Raw JSON/text containing image-shaped content must
remain a negative control. Keep all raw run evidence private.

The QA entrypoint has no second two-model whitelist. It uses the selected
production policy loader, requires the fixed DeepSeek main ID, and inherits the
exact accepted_models, provider allowlist and fingerprint. The fixed App's known
auxiliary aliases and any user-explicit aliases remain policy decisions. An
unlisted request model is rejected before an upstream call; this is different
from rejecting all unfamiliar alias strings in a user-authored configuration.

HTTP auditing records a status when aiohttp prepares response headers, separately
from handler completion. It does not infer that the peer received those headers.
A cancellation or peer disconnect before headers is cancelled/unobserved, never
a fabricated default500. A process crash leaves a pending/unknown attempt.
Legacy rows without header/completion provenance remain legacy_unverified.
The per-row processed delta is a global counter observation during concurrency,
not per-request attribution; use exact native IDs, nonce receipts and the global
request budget for their respective assertions.

## Pre-response capacity rejection (synthetic QA only)

`http_413` returns a small JSON HTTP 413 before constructing any SSE response.
It omits retry advice, includes a unique safe X-Request-Id, and records only
thread/case/request/body hashes and byte count. The native runtime derives
`known_rejected` from the transport error; no fixture header or JSON flag sets it.
The installed Bridge must preserve 413 before its SSE prepare path.

Use the existing durable CodeMode fixture, selected installed Python, and a new
explicit state initialized once:

    python.exe -I -B -X utf8 codemode_fault.py init --state-dir <NEW-STATE>
    python.exe -I -B -X utf8 codemode_fault.py serve --state-dir <NEW-STATE> --finite-recovery --gateway-policy-home <NORMAL-GATEWAY-HOME>
    python.exe -I -B -X utf8 codemode_fault.py control --state-dir <SAME-STATE> --command-json '{"action":"arm","fault":"http_413","thread_id":"<ACTUAL-UUID>","repeat":1}'

Do not enable a live reader. Reuse the same state/case for later phases and retain
the original nonce, 900-second deadline, 64 processed-request and 256-event caps.
The existing arm contract has no idempotency command_id: after an unknown control
outcome inspect status before any replay; do not silently rearm or reset state.

Finite recovery is opt-in. A single fired 413 permits at most two synthetic
success responses for that exact authenticated thread and same case. Both the
deadline and processed cap are checked before and after waiting for in-flight
work. Each slot is journaled; after exhaustion the existing live-disabled 503
remains. Two slots allow native reset plus ordinary continuation, or local summary
plus ordinary continuation. They do not identify which branch actually ran.

Run `capacity_selftest.py --evidence-dir <NEW-EVIDENCE-DIR>` with the selected
installed Python. It retains every result/state and verifies module origin,
actual non-SSE 413 through installed c110, no retry advice/effect/live forwarding,
exact-thread filtering, opt-in recovery, slot bounds, and refusal after cap/expiry.
Fixture object reopening is metadata evidence, not process/App cold acceptance.

Actual cold413 remains a separate Root-authorized App window: establish a genuine
ordinary-completion checkpoint; normal File Quit and guarded admitted-J launch;
reopen the same UUID; submit one GUI marker; bind the real HTTP413 receipt before
ResponseStream to native recovery and the next ordinary completion. Require
native reset evidence for TokenBudget, or a fresh prefix-only summary for local
compaction. Do not force a summarizer assertion on the reset branch. Verify the
unconsumed suffix envelopes/call IDs/order/metadata unchanged and effect count
unchanged. Invalid checkpoint binding, no eligible confirmed prefix, second413
in the same turn/epoch, SSE errors, or empty summary must preserve input and
surface the documented refusal rather than fabricate completion.

The App blocks GUI /compact while a response is active; direct compact RPC aborts
active work and cannot substitute for that GUI contract. No App/native/model or
credential acceptance is claimed by these loopback checks.

## Final input authority (synthetic QA only)

The Code Mode fixture adds two same-thread, discovery-bound faults:

- codemode_preview_revision: a valid preview/arguments.done program would record
  two effects in the existing case, while the valid authoritative output_item.done
  program records one. Native FINAL-only execution must produce exactly one effect,
  a completed FINAL-source call, and its matching unpredictable counter receipt.
  Preview-only execution produces two effects; executing both produces three.
- codemode_invalid_final: the same valid two-effect preview is followed by a
  schema-invalid final input:null. No authoritative completed native item or effect
  is allowed. Preserve the failed response; a later synthetic recovery is separate.

Use a new armed case with repeat=1 and independently verify its initial count is zero.
These use the actual declared native custom exec mapped to an upstream function
tool. They do not claim an ordinary native function-tool App case. The fixture
never evaluates JavaScript or invokes the counter itself. Existing binding,
same-thread authentication, nonce, absolute lifetime and request/effect budgets
apply. Optional finite recovery allocates one synthetic response and never resets
the original budgets. It is not model or business-task success.

    python -I -B selftest_final_input.py --native-source ABSOLUTE_NATIVE_SOURCE

The current c110 bridge is an expected-reject control for a valid preview revision;
that outcome is not a new-candidate pass. After explicitly selecting the updated
production package, add --expect-final-authority. Mock source/loopback success
does not qualify App execution. Actual GUI acceptance must use durable native
ALL_TOOLS/counter binding and inspect completed call/result IDs and ledger effects.
Post-completed conflicting revisions retain their separate protocol guard tests;
these two fixture cases do not claim that third App scenario.
