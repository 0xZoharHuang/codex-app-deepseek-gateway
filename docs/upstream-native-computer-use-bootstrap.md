# Native Windows Computer Use bootstrap investigation

**Actual native call failed; desktop-operation acceptance remains open.** This diagnostic is not a production workaround or permission to enable internal flags.

## Actual fixed combination

App 26.1023.6621.0 / frontend 26.1023.51421, Windows 10 19045, original signed ASAR unchanged, API/provider credentials through the isolated gateway. The documented native 0.165.0-alpha.1.2 isolation/Guardian/filesystem patches remain; an entirely unmodified bundled runtime has not reproduced this case. Native policy read returned `requirements: null`.

QA used the released source's process-scoped `CODEX_ELECTRON_ENABLE_WINDOWS_COMPUTER_USE=1` entry point. System/registry environment, SDK assignments, model checks, privileges, security/privacy and approvals were unchanged. The source switch does not prove public product support.

After official native plugin installation, the actual App logged per-thread `node_repl` starting then ready. The model discovered the JS schema, imported `@oai/sky` and called `sky.list_apps()`. It failed: App-owned native pipe unavailable, **OS error 2**. `list_windows()` was not reached. No activation, input, screenshots or substitute automation occurred.

Global MCP configuration and a nested `ALL_TOOLS` list alone do not prove the complete per-thread/deferred surface.

## Source hypothesis and minimum repair

Startup publishes effective availability before constructing a separate native backend. The new backend starts disabled; its existing availability setter synchronizes it and starts the original pipe. The later global old/new comparison can see no change because the opt-in applied earlier, and skip initializing this new instance.

Renderer loading and `statsig-disabled` guards also precede normal feature dispatch. Their operand values were **not observed**. This is a source-supported synchronization hypothesis consistent with the failure, not a confirmed account/policy/region diagnosis.

The minimal upstream repair is to initialize each new backend from the already computed effective availability after dependencies are ready. Keep subsequent change detection and reuse existing native setter/pipe, eligibility, policy and model checks. No App package was patched, internal event fabricated, or pipe server rewritten. The alternative bundled unified plugin delegates to App-managed configuration too.

## Official boundary

[Gateway compatibility](https://learn.chatgpt.com/docs/enterprise/gateway-compatibility) separates model traffic from commands, MCP and browser/app services; gateway configuration grants none of their permissions.

[Computer Use](https://learn.chatgpt.com/docs/computer-use) requires official plugin/native approvals and uses the active visible Windows desktop. [Feature availability](https://learn.chatgpt.com/docs/pricing) marks it region-limited, including API-key access. These do not establish this host's effective eligibility.

This is neither a desktop-control pass nor automatic cloud-entitlement exclusion. A verified normal native bootstrap or signed App fix remains necessary.
