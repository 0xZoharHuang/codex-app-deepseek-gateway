# R224: App history, Hooks, and final-schema protocol qualification

Overall stable App support remains unqualified. The installed App/gateway have
not been replaced by the new Python protocol source described here.

## Actual App history and native Hooks

The fixed P5 App completed a real GUI fork from turn A after a later B existed.
The child inherited A and its web reference, excluded B, and left the parent's
original bytes unchanged. Two normal Edit/Send operations replaced history in
the same child UUID. A removed turn's file effect remained; message reversion
was not mistaken for filesystem Undo.

The App exited through File/Quit, its complete recorded 35-process family exited,
and one installed app-launch invocation restarted it. The same child UUID cold
continued using the original web reference, source SHA, retrieval time and
4,855-line source. The actual App read_thread interface returned exactly the
retained A, replacement and current turns, with no more pages. B and the removed
turns were absent. An initial turnLimit=20 was correctly rejected; DeepSeek
corrected it to 10. A stale private SQL projection was not treated as API truth.

A separate owned project was added and trusted through the normal App GUI.
The actual DeepSeek App turn exercised Command context injection, a blocked
canary patch, an allowed two-file native patch and a correlated McpTool post hook.
The current cwd, thread, turn, tool-use ID, full patch and actual successful tool
response were independently matched. Nonces came from the hooks, not the prompt.

App Last Turn displayed the exact two-file diff. Normal Undo restored the
pre-GUI bytes, Reapply restored the GUI patch, and a second Undo restored the
baseline again. Unrelated dirty changes and both project configuration files
stayed byte-identical; the canary was absent. Git remained dirty in the original
three files. The checker did not use Git clean as an Undo oracle.

These close the stated App GUI history, cold reference, Hooks and diff/Undo
scopes. The original UI observer failures and intermediate model errors remain
private and indexed. They were not erased or counted as product success.

## Strict final schema with automatic native tools

Direct probes rejected three tempting shortcuts: schema strict=false still
suppressed automatic calls on the tested Morph path; official JSON mode failed
to provide a scalar-string final; and Prime JSON mode with explicit serial tools
returned a placeholder without calling the tool. Those failures are retained.

The minimal central pure-protocol change preserves the original native request,
auto choice, tool schemas, history and selected provider. For strict=true with
effective tools and auto, it chooses the wire format before making a request:

| Selected route and schema root | Upstream format | Final contract |
| --- | --- | --- |
| Official DeepSeek, explicit object or array | JSON mode | Original complete schema, strictly validated locally |
| Other root or existing serial/peer route | Plain text with complete schema instruction | Original complete schema, strictly validated locally |

No request is retried on another format, model or provider after failure. The
existing StructuredOutput validator consumes the original schema. No tool is
invented, no auto-to-required substitution is made, and no agent loop or tool
executor is added. Receipts identify the mapping and hash the original format,
schema and instruction. They do not claim provider schema-constrained decoding.

This follows the distinction between requesting JSON syntax and validating the
application schema; the provider's [JSON mode guide](https://api-docs.deepseek.com/guides/json_mode/)
and [structured-output guidance](https://developers.openai.com/api/docs/guides/structured-outputs)
are background, while the fixed-model route choices above come from actual
OpenRouter observations.

Without prepare/controls/validator overlays, actual P5 native API turns read
unknown file nonces and returned matching object, array and JSON-string finals.
Final-source HTTP plus the real Prime endpoint also preserved auto and
parallel_tool_calls=false. Normal native main hardcodes parallel=true, so that
serial test is not described as a native-main request. No ResponsesLite flag was
enabled to manufacture a test.

The live source was af4ebd8c22531f157f816ac6dc45a7841e9a3236041c84e3bc863d7cc8219638.
Review then found that replacing a format could silently consume unknown format
controls. V2 rejects unknown format fields and invalid present names while
preserving nested schema keywords. Its source is
f878a1091a48ecd195c5616d481f68c846f67ba533d6b12b1cb68a452850094a.

For all 14 captured native/upstream inputs, independent old/new module comparison
found identical canonical payload, complete Registry semantics, route and mapping.
That is explicit normal-input carry, not a new V2 live run. V2's new rejection
paths passed 16 format-control cases alongside the preceding 127 targeted cases,
all 143 unique cases with no failure, error or skip.

Final-source stream and nonstream controls rejected malformed, wrong-type and
reasoning-only completed responses without publishing a completed final.
Already committed tool effects and previously streamed text deltas are not
claimed to be retractable. Installed-wheel and actual upgraded-App qualification
are still required.

## Software regression and runner prerequisites

The first full CDS run reported 2,373 passed, 49 failed and 24 platform-dependent
skips. Those results remain. Independent per-case triage found:

- 32 cases failed before Git could start because it was absent from the test
  child's PATH.
- 11 failed while constructing symlink fixtures without the required privilege.
- 4 failed while assigning test-fixture owners, before the product home guard.
- 2 correctly rejected an installed old adapter that differed from this checkout.

The exact first 47 cases passed with Git in the child PATH and the host's already
elevated test token, using owned temporary directories. No machine policy,
Defender setting or privilege assignment was changed. This is software-fixture
qualification, separate from the ordinary-user App and native API evidence.
The final two cases await a matching wheel in a separate QA environment.

CADSG's 95 focused source HTTP checks passed. An independent review tightened the
new 12 integration cases to require exact HTTP status, complete typed items and
the exact hash-only mapping contract; all 12 then passed. These overlapping test
scopes are not added together as a new total.

## Additional native transport defect found

Each of the three native source tasks logged eight WebSocket upgrade requests
rejected with 405, followed by successful HTTP helper requests. All 24 failures
are retained. Actual native config/read confirmed supports_websockets=false,
including when only the base URL was overridden.

The public Guardian V2 connection pool directly prewarms WebSockets outside the
core client's capability guard. A source trace reaches the unchanged false
provider information from the real session config. The planned minimum native
fix is to respect that capability before refilling the pool and retain its
existing HTTP scoring path. It does not disable Guardian or replace scoring.
No patched binary or successful transport fix is claimed here.

## Still required

The transport patch needs focused native tests, independent source reproduction
and one formal producer before actual App qualification. New Python dependencies
must be pinned to a qualified release, packaged, installed in isolation and
exercised. The worktree model-restore defect and other existing matrix gates
remain separate. Public release, bounded final cleanup and the final live quota
check have not been completed.
