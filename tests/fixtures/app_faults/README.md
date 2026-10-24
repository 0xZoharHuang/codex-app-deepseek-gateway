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
    python.exe -I -X utf8 fault_server.py control --state-dir <QA-DIR> --command-json '{"action":"arm","fault":"http_429","thread_id":"<EXACT prompt_cache_key>","repeat":5}'

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
