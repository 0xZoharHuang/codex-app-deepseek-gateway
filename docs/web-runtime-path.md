# Native web runtime path and backend acceptance

Audit date: 2026-10-22. Native source: `680c4ba93ec79c292287fb9e0b627dc7a76e3b5e` (release tag `rust-v0.163.0-alpha.11.1`). Pure CDS protocol dependency: `83056e7cf320461c30a609e911f3bcf0bea41be0`.

This is a source audit and implementation boundary, not a claim that web acceptance passed. The first App request rejected cached search, and a later request rejected the inherited text-and-image hosted-search declaration. Those failures remain evidence. A separate shared-machine sandbox-account incident has paused App execution; no additional sandbox, credential, setup, or live provider probes should run until the owner resumes them.

## What the native runtime actually does

There are two distinct native paths:

```text
Hosted path (current pure CDS conversion)
App runtime -> Responses tools[type=web_search]
            -> gateway -> OpenRouter openrouter:web_search(engine=exa)
            <- provider search event/results
App runtime retains native web_search_call history

Standalone path (preferred native integration)
App runtime publishes namespace web, function run, native SearchCommands schema
DeepSeek -> ordinary namespaced function_call web.run
App's unchanged WebSearchTool executor -> POST <provider base>/alpha/search
                                      -> backend adapter
                                      <- {output: string, results?: JSON[]}
App's unchanged executor -> native web begin/end, external-context tool result
```

Native `web.run` does not contain a local search engine, HTTP page reader, or image search implementation. Its executor calls the provider's standalone search service. Keeping this executor and supplying its service contract preserves the native tool directory, call identity, scheduling, parallel execution, event lifecycle, and external-context treatment.

Source facts:

- [Provider configuration](https://github.com/openai/codex/blob/680c4ba93ec79c292287fb9e0b627dc7a76e3b5e/codex-rs/model-provider-info/src/lib.rs#L200): `supports_standalone_web_search` is a provider field, default false.
- [Feature declaration](https://github.com/openai/codex/blob/680c4ba93ec79c292287fb9e0b627dc7a76e3b5e/codex-rs/features/src/lib.rs#L1251): `standalone_web_search` is under development and default false.
- [Extension availability](https://github.com/openai/codex/blob/680c4ba93ec79c292287fb9e0b627dc7a76e3b5e/codex-rs/ext/web-search/src/extension.rs#L42): a custom provider needs the standalone capability, and search mode must not be disabled.
- [Per-turn selection](https://github.com/openai/codex/blob/680c4ba93ec79c292287fb9e0b627dc7a76e3b5e/codex-rs/core/src/tools/spec_plan.rs#L697): an available registered standalone executor replaces the hosted declaration. Merely enabling a feature without an available executor does not establish the standalone path.
- [Namespaced function and executor](https://github.com/openai/codex/blob/680c4ba93ec79c292287fb9e0b627dc7a76e3b5e/codex-rs/ext/web-search/src/tool.rs#L54): native `web.run` is a directly exposed function and supports parallel calls.
- [HTTP endpoint](https://github.com/openai/codex/blob/680c4ba93ec79c292287fb9e0b627dc7a76e3b5e/codex-rs/codex-api/src/endpoint/search.rs#L14): provider-relative `alpha/search`, POST, ordinary JSON response. A provider base ending in `/v1` therefore requires `/v1/alpha/search`.

Required configuration, merged into the project-owned App integration:

```toml
web_search = "live"

[features]
standalone_web_search = true

[model_providers.<gateway_provider_name>]
supports_standalone_web_search = true
```

Provider capabilities must still allow web search and external web access. Do not switch to Responses Lite to bypass this path. Do not remove image capability from the model catalog to make an incompatible hosted declaration appear successful. Confirm the effective request has native namespace `web.run` and lacks the redundant hosted `web_search` declaration.

A temporary explicit 501 endpoint can make the missing service visible while unrelated local-runtime tests proceed. It does not pass any web gate. In this version, the native executor maps service errors to a fatal tool error, so a 501 may end that turn rather than act as a harmless empty result.

## Exact alpha/search contract

[Request and command types](https://github.com/openai/codex/blob/680c4ba93ec79c292287fb9e0b627dc7a76e3b5e/codex-rs/codex-api/src/search.rs#L8):

| Request field | Native behavior |
| --- | --- |
| `id` | Required opaque ID from the extension's session store. It is not a unique tool-call id or authorization token. |
| `model` | Current native model name; backend must resolve only explicitly accepted aliases. |
| `reasoning` | Optional in the API; the current native executor sends none. |
| `input` | Optional text or response items. The native executor includes a limited visible conversation tail, not all previous page contents. |
| `commands` | Structured operations below. The native executor sends this, including an empty object for empty arguments. |
| `settings` | Search policy and presentation controls below. |
| `max_output_tokens` | Native tool-output token budget; presentation must honor it without discarding the stored source. |

[Complete operation schema](https://github.com/openai/codex/blob/680c4ba93ec79c292287fb9e0b627dc7a76e3b5e/codex-rs/codex-api/src/search.rs#L31):

| Command | Fields and behavior to preserve |
| --- | --- |
| `search_query[]` | `q`; optional `recency` in days and `domains[]`. |
| `image_query[]` | The same query fields, but requires genuine image-search results. Text snippets alone do not satisfy this operation. |
| `open[]` | `ref_id` containing a URL or known reference; optional `lineno`. |
| `click[]` | `ref_id` of an opened page and numeric link `id`. |
| `find[]` | `ref_id` or URL and `pattern`; must search the actual retained document. |
| `screenshot[]` | `ref_id` or URL and zero-based PDF `pageno`. |
| `finance[]` | `ticker`, `type` (equity/fund/crypto/index), optional `market`. |
| `weather[]` | `location`, optional `start` date and `duration`. |
| `sports[]` | `fn` (schedule/standings), `league`; optional tool/team/opponent/date_from/date_to/num_games/locale. Leagues: nba/wnba/nfl/nhl/mlb/epl/ncaamb/ncaawb/ipl. |
| `time[]` | `utc_offset`. |
| `response_length` | short/medium/long; a presentation control, not an operation. |

Settings include `user_location`, `search_context_size`, `filters.allowed_domains/blocked_domains`, `image_settings.max_results/caption`, `allowed_callers`, and `external_web_access`. The native extension currently sends direct callers, configured location/context/domain filters, and the search mode. Cached maps to false; live to true; indexed to the indexed string mode. Unsupported policies must produce an explicit error, not silently become live/unfiltered search.

[Response](https://github.com/openai/codex/blob/680c4ba93ec79c292287fb9e0b627dc7a76e3b5e/codex-rs/codex-api/src/search.rs#L297):

```json
{
  "encrypted_output": null,
  "output": "Actual source-derived tool text with stable references",
  "results": [
    {"type": "text_result", "ref_id": "turn0search0", "url": "https://example.com/result"}
  ]
}
```

`output` is required. `results` is optional and opaque JSON to the native Rust client; the upstream test establishes text_result/ref_id/url and preservation of future fields, not the entire closed desktop rendering contract. Native code forwards results into web events and returns output as plaintext function_call_output with `contains_external_context=true`. Image/screenshot and other rich cards need actual App rendering acceptance.

Native web actions include Search, OpenPage, FindInPage, and Other. Click can legitimately appear as Other in the legacy event; its function-call arguments and fetched target must prove the action. Do not assert that a legacy Click enum exists.

## References, lines, citations, and state ownership

The service owns source retrieval, reference resolution, line numbering, link tables, and result formatting. Native code forwards commands; it does not resolve URLs or reference IDs. Its limited `input` tail contains the recent visible user/assistant exchange, not a page database ([history implementation](https://github.com/openai/codex/blob/680c4ba93ec79c292287fb9e0b627dc7a76e3b5e/codex-rs/ext/web-search/src/history.rs#L14)).

A backend adapter therefore needs its own protected, bounded source cache and immutable provenance records:

- Record requested URL, final URL, retrieval time, content type, source digest, extracted lines, actual link targets, and an opaque reference. Search excerpts remain marked as excerpts until open retrieves the page.
- `open(ref)` and `find(ref)` operate on the recorded document version. `open(URL)` records an actual retrieval. `click` resolves the stored numbered link and retrieves the target; it must not guess a URL from a title.
- Line references and link IDs remain stable for that source version. Truncated display must not make missing lines or links appear present. Missing/stale/foreign references must fail clearly.
- Keep source state scoped to this gateway installation and its authenticated user. Record the originating native session ID. Do not assume that session IDs or references automatically survive cold resume or full forks; validate both workflows. Avoid replacing a source when an old reference is reused.
- The source cache is backend state, separate from model-history attestations in ProtocolState. Do not import CDS Store, browser, web_mcp, launcher, or process ownership code.
- Public URL retrieval must enforce explicit network/domain policies, redirect limits, response limits, and local/private-address restrictions. Do not use a model-generated summary as a retrieved source.
- Plaintext backend content remains external evidence. It must never become user approval or a system/developer policy instruction.

The native service call uses the configured provider's request/retry transport. Its session id alone cannot support exactly-once call deduplication. Do not invent idempotency by treating all identical commands in a session as one call: a later deliberate refresh may be legitimate. Distinguish retries/unknown outcomes from source-cache reads in receipts.

## What pure CDS provides, and what it does not

The pinned CDS `search.py` converts hosted `web_search` to `openrouter:web_search` with Exa. It explicitly rejects cached/indexed modes, unsupported location bias, and text-and-image search declarations. `Registry.output` converts supported provider search items into native web_search_call items and stores provider evidence for later history replay.

The same protocol registry already handles ordinary namespaces and functions. Consequently native `web.run` calls can use the normal namespaced tool conversion without a new agent loop or native tool scheduler.

The former CDS distribution separately installed a browser MCP server for open/find/click. That server is not part of the pure protocol dependency contract and is not automatically installed into this App. It is not the implementation proposed here.

OpenRouter's separate `openrouter:web_fetch` server tool is real and documented, but the pinned CDS Registry does not declare or translate its output type. It cannot be silently inserted into the main Responses request and called completed native browsing.

## Official service options and the smallest reliable backend

OpenRouter documents search and fetch as server tools **inside** Chat Completions, Responses, or Messages requests. Search is executed by the configured engine; fetch can use OpenRouter's HTTP backend, Exa, or other engines. These are useful backend candidates, but no documented direct deterministic tool-execution REST endpoint was established in this audit. Do not treat an unverified/private endpoint as a supported API.

Sources downloaded on Windows:

- [OpenRouter server tools](https://github.com/OpenRouterTeam/docs/blob/main/guides/features/server-tools.mdx)
- [Web search server tool](https://github.com/OpenRouterTeam/docs/blob/main/guides/features/server-tools/web-search.mdx)
- [Web fetch server tool](https://github.com/OpenRouterTeam/docs/blob/main/guides/features/server-tools/web-fetch.mdx)

Two authorized, bounded protocol probes used the fixed model, official DeepSeek route, one server-tool step, 512 output tokens, and a public official documentation domain. Both returned routing HTTP 404 before producing a generation id. They used `tool_choice=required`, which does not match this integration's known official-DeepSeek tool-choice constraints. They establish neither successful tool execution nor absence of raw tool results. Evidence: `.evidence/web-server-tool-real-probes.json`; runner `223c1ed86728446a85211e4a5654d2dc` exited and deleted its owned scheduled task.

A later, separately authorized corrected probe should use supported/default auto tool choice, inspect the exact server-tool item and raw-source fields, and keep strict route/engine selection. No tool invocation means inconclusive. A model-written answer with citations is not raw page evidence. Capture sanitized routing error codes and step names; do not automatically retry or change providers. Further live probes are currently paused.

Implementation choices, in order:

1. Keep native web.run and its handler unchanged; implement only authenticated `/v1/alpha/search` protocol/backend service.
2. Prefer a documented deterministic official search/content API when available. Direct Exa search/contents is a service option, but requires its own authorized credential; an OpenRouter key is not an Exa key.
3. If OpenRouter is used as an intermediate server-tool service, accept only verified server-tool source records. Bound all server-tool/model work and report its additional usage. Do not use assistant prose to fabricate URLs, snippets, timestamps, lines, links, or data points.
4. A small HTTP document backend can retrieve and parse public HTML/text/PDF and deterministically perform open/find/click over retained sources. This is a web-service backend; native Codex still makes and schedules tool calls. Use established parsing/PDF libraries, not CDS business modules.
5. Image search needs an actual image-search source; webpage thumbnails alone do not establish equivalence. PDF screenshot needs actual rendering. Finance/weather/sports require reliable structured services with matching identifiers, units, dates, and freshness; generic web summaries are not equivalent. Time is deterministic offset conversion.
6. Every command remains a named acceptance row. Temporary explicit unsupported errors are implementation checkpoints, not permanent completion. No final stable claim while required operations remain unimplemented.

The route adapter must validate the entire supplied command set and settings before outbound work, preserve explicit failures, and retain operation-level provenance. It must not infer a new task from the conversation, spawn subagents, implement model scheduling, or modify native approval decisions.

## Required real acceptance

These checks are required after the shared environment is cleared for execution; none is claimed passed by this document.

1. **Native admission:** App's actual effective catalog contains the native web.run schema, including every command; hosted web_search is absent when standalone is selected. Record the exact binary/config versions and sanitized tool catalog.
2. **Search:** App-generated native web.run requests reach alpha/search; real search evidence and query/domain/recency controls are verified, with authentic backend/provider receipts. Search snippets alone do not pass open.
3. **Open/find/click:** Use a document with known headings and real links. Verify actual HTTP retrieval, stable line numbers, literal matching context, a numbered-link target fetch, redirect destination, and final cited URL. Include URL and reference variants.
4. **Full schema:** Independently exercise image search, actual PDF screenshot/page bounds, finance asset types/markets, weather dates/units, sports league/schedule/standings filtering, and positive/negative UTC offsets. Verify actual rich results in App, not just a JSON parser accepting a list.
5. **Lifecycle:** Reopen references after gateway restart, App cold resume, and a parent-to-child/full-fork handoff. Record session ids to establish actual reference scope; test unknown or mismatched references.
6. **Failure and permissions:** Disallowed domains/private targets, invalid link/page indices, unsupported search policy, DNS/HTTP errors, timeout, oversized content, cancellation, and mixed-command failures must remain visible. No fabricated successful result or unauthorized fallback.
7. **External evidence:** A hostile page claiming authorization must remain tool data. It must not change native approvals or the authorized filesystem scope.
8. **GUI and output:** Confirm web begin/end/error lifecycle, citations and rich cards, and native tool call/result identity. The legacy action label alone is insufficient for click.
9. **Side effects and billing:** A valid earlier operation may already have fetched or incurred cost before a later failure. Lack of a final success marker does not imply no work occurred. Use fixture/server counters and receipts where necessary.

All source copies used for this audit are under `.evidence/web-runtime-source`; they are small text-only reads of fixed native sources or primary provider documentation. The audit did not alter the bridge service, App configuration, sandbox, or native tool execution.
