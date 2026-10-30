# Minimal public evidence package

This overlay contains the byte-identical strict V3 proof oracle and four already-qualified passive WIRE/BOUND modules. It adds portable mock-only negative controls and actual loopback HTTP wiring checks. It is QA support, not a provider, runtime, agent loop, fault engine or App controller.

There is deliberately no promise of one-command reproduction of real App acceptance. Private GUI/lifecycle controllers, once-only admissions and real installation/profile inputs are not published. The two private thin entrypoints are not copied; public readers retain the original repository fault fixtures and perform App actions through the normal GUI. Integrating these passive hooks into an already authorized original fixture is an explicit developer operation, not an installed canonical service takeover.

## Run the local controls

Use an isolated Python with the project's fixed gateway/dependencies and CDS0.6.7 protocol input. The pure test needs only stdlib. The HTTP test additionally imports aiohttp and the installed gateway atomic writer plus published pure protocol Registry. No App installation, provider key, WinCred, external network or model is needed.

From this directory, substitute an explicit output location and the separately reviewed inputs.json SHA:

~~~powershell
& $python -I -B .\selftest_proof.py --inputs .\inputs.json --inputs-sha256 '<trusted inputs SHA256>' --output 'C:\QA\new-pure-result.json'
& $python -I -B .\selftest_http.py --inputs .\inputs.json --inputs-sha256 '<trusted inputs SHA256>' --output-dir 'C:\QA\new-http-result'
& $python -I -B .\selftest_tap_http.py --inputs .\inputs.json --inputs-sha256 '<trusted inputs SHA256>' --output-dir 'C:\QA\new-tap-result'
~~~

Outputs must be fresh. Never overwrite a prior failure to convert an unknown outcome into success. HTTP data and the single synthetic custom tool declaration are mock-only; no real native tool directory is included.

## Fixed actual-App reference and normal GUI evidence

The referenced App is26.1023.6621.0, native0.165.0-alpha.1.2/upstream720fec862a608ae1e5878d1eecb04fb93192e93c, prepared source39d578673f2031097dfa82fd97b5126a492ea4ba3a692b565ab5fcaef4dce33f. Source hashes are in inputs.json and immutable-source-provenance.json. A new version needs explicit review; a local mock pass cannot qualify it.

For a real authorized fixture window, preserve the original900-second deadline,64 processed requests,256 events and8 paid live attempts. The original fault fixture owns auth/counters/once-only consumption. These observers cannot authorize actions or extend/reset a case. Stop on an unknown admitted action; inspect existing receipts instead of replaying it.

1. Use the normal installed prepare/bind/configure/check/launch path with a dedicated home. Verify actual App/native SID/session/path/birth/hash and effective policy. Preserve the original source fixture and caller-supplied installation manifest; do not import a source checkout into a live product.
2. On an idle owned QA chat, submit exactly the agreed prompt through normal GUI. Preserve the raw user item and turn context, not just a prompt hash typed by a controller. Do not infer MAIN from every unknown/auxiliary request.
3. The original fixture app can attach NativeRoleObserver first, then attach_observer so the latter is outermost. They share one authenticated POST index through request-local state. NativeRoleObserver binds real upstream trace/request/body metadata. Observer outputs remain private.
4. In an original Guardian relay, Tap.scope uses that actual complete trace-linked row, captured fixture state and original request/payload while its wrapper yields the original sse_events objects. Close restores the original parser. Do not run another verdict producer.
5. A synchronous reviewer can have a different thread ID from Parent. BOUND reads only independent blocks in the last native user message: transcript END immediately followed by the complete Reviewed Codex session id marker, then the final current approval-request JSON section. Embedded transcript substrings, old history, merged blocks and mismatched Parent are not proof. Preserve the actual reviewer header hash; do not replace it with Parent or use cache affinity. Action JSON has no guaranteed native review/target ID.
6. Join literal model response/generation/HTTP receipt and current action hashes to the original Parent's actual approval/action/target/turn and effect. Bare allow must not get fabricated fields. A valid model verdict is not proof native consumed it; a failed-closed review error is not a model deny. Keep later503 and incomplete turns as failures.
7. For fault recovery, the pure oracle requires exact native prompt/turn/model plus physical/body/status/receipt and lossless last-user block SHA. It allows only exact text or one editor LF; a normalized hash does not prove wire equality. A413 body does not mean context-window overflow, compaction or success. Original effects must have exactly one native call/output pair and durable nonce.
8. For cancellation, record actual normal GUI Stop and original transport cancellation inside the original hold window, before natural completion. For canonical gateway loss/restart/auth, use the installed normal service and same-port checks; a QA wrapper must not impersonate that producer.
9. For managed worktrees, do not delete an active primary checkout. Use normal task Archive and normal Restore worktree GUI. Literal Windows path aliases may reference different snapshot refs; verify complete retained files against the exact snapshot and preserve any recovery failure. Do not create refs or edit App queue metadata to make a test pass.

## Private evidence and retained failures

The earlier private tests exposed missing physical indices, lossy newline matching,503 mistaken for413, auxiliary requests blocking MAIN, and the mistaken assumption that reviewer header equals Parent. The original failed runs remain private and immutable; this package does not export them or relabel them. Public tests use synthetic equivalents only.

Never publish request/rollout bodies, original prompt/history/reasoning, OAuth forms/cookies/tokens, native/App tool inventories or closed ASAR/sidecars. This package contains none. A maintainer can publish a redacted result manifest with source hashes and scoped conclusions; full real-App acceptance still requires operator evidence and independent review.

## Observed legacy Windows recovery limitation

On the fixed Windows/App cohort, one normal task archive produced slash and backslash identities for the same physical worktree, with a backslash payload root. Only the slash-derived snapshot ref was found. Normal GUI restoration preserved files, but a later native attach still reported pending archival. This is an observed failure, not a completed public restore/registration pass. Its final classification remains in the main qualification ledger. Neither manual Git-ref alias creation nor App queue/attachment edits are part of this reproduction or native acceptance.
