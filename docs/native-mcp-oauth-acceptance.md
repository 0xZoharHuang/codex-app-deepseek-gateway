# Native MCP OAuth and App extension qualification

This runbook applies to the signed Windows App 26.1016.8120.0 and H native runtime,
pinned to upstream commit `680c4ba93ec79c292287fb9e0b627dc7a76e3b5e`.
The gateway translates model traffic. MCP discovery, OAuth, credential storage,
refresh, tool invocation and logout are performed by the native runtime.

The current actual qualification is summarized below. Historical control
failures remain in the private evidence archive; fixture readiness or a successful
backend test is never substituted for an App GUI result.

## Current evidence and its limits

| Path | Observed result | Scope |
| --- | --- | --- |
| App Authenticate and Deny | Actual Authenticate opened the synthetic server; Deny returned the App to authentication state; no grant or successful tool execution | Deny used the owned browser document |
| App consent and tool use | Native discovery, PKCE exchange and an actual DeepSeek MCP Code Mode call returned the fixed fixture identity | Allow used the server's valid cookie/CSRF/Origin-protected HTTP consent role; no browser Allow click is claimed |
| Literal access expiry | The same App thread read again after the 120-second access lifetime; native refresh succeeded without a new consent or authorization code | Refresh before literal expiry is separately distinguishable from this check |
| Remote revocation | The synthetic server revoked its own grants; the next actual tool call required authentication and produced no successful server execution | This is server-side revocation, not local logout |
| Reconnect | Actual App Reconnect obtained a new consent state; a valid consent restored access | No CLI logout was used to enable Reconnect |
| Cold credential reuse | Normal isolated App cold start loaded H and the final installed gateway; the same thread refreshed and read successfully | Actual App loading and tool result were observed |
| Final cleanup | Exact native logout removed the fixture credential, native remove removed its config entry, and normal App restart cleared the stale status row | Existing MCPs, plugin, project trust and permission configuration were preserved |
| Native storage matrix | File, Keyring/Secrets, Keyring/Direct and healthy Auto each passed two-home login, literal-expiry cold refresh and bidirectional logout isolation | Real H app-server tests; not four separate App GUI tests |
| Signed App tools and owned plugin | Actual main and native child used the signed packaged tools; an independently owned plugin was installed, read and removed through native paths | No proprietary packaged implementation is redistributed |

The native storage pairs used the **same server name and URL** in independent
homes. The second home initially had no login. Authenticating or logging out in
one home did not consume or delete the other's credentials. Healthy Auto resolved
to Secrets; it does not establish an actual OS-vault outage/fallback result.
Only exact synthetic credential metadata was inspected, without enumerating
unrelated credentials or recording token values.

Actual provider identity for the first two App tool-use turns was verified by
five generation lookups: the fixed DeepSeek model served by DeepSeek. A gateway
receipt with an absent provider field was retained as absent, not rewritten.
Other acceptance tasks used an explicitly configured same-model compatibility
route for strict structured output; that route is documented separately.

## Repeatable controller procedure

1. Wait for current owned turns to finish. Record the signed App version, native
   hash, installed gateway manifest, current permission configuration and exact
   owned frontend/backend identities. Do not switch another user's desktop.
2. Start the existing loopback synthetic MCP OAuth fixture with a fresh nonce,
   finite lifetime and recorded PID/creation identity. Use its generated
   Streamable HTTP URL and `fixture:read` scope. Keep the gateway's upstream key
   out of this MCP entry. Do not extend a running fixture's deadline.
3. Add only that entry through the App or the existing reversible configuration
   workflow. Leave bearer-token fields empty. Keep normal native discovery,
   PKCE, state, redirect, cookie, CSRF and Origin checks enabled.
4. Select Authenticate, deny the real consent, then authenticate afresh and allow.
   Join the actual fixture events, native tool call and App result. Source evidence
   of a button or a browser page alone is insufficient.
5. Have DeepSeek discover and invoke the fixture tool. Independently require its
   nonce-bound server event and structured identity. Wait beyond literal access
   expiry, read again and require refresh without another consent/code exchange.
6. Revoke only the fixture's grants. Require the next actual App tool call to fail
   or demand authentication, with no cached success counted as a new execution.
   Use App Reconnect and fresh consent, then verify access again.
7. Close only the verified owned App through normal File/Quit. Use the managed
   isolated launcher with a bounded observation window, verify the current native
   backend generation and repeat the tool call on the resumed thread.
8. While the exact entry still exists, perform native logout. Verify the native
   deletion result, remove only that entry, and preserve unrelated later edits.
   Reconcile the App's actual MCP view; external CLI config removal can leave a
   status row until a normal native/App reload. Finally verify the fixture actor,
   listener and its owned task have exited.

An admitted operation whose observation fails must be reconciled by identity,
not replayed automatically. Startup app-server processes may retire naturally;
verify the **current** backend generation and keep initial identities historical.

## Native protocol and storage details

The login request is `mcpServer/oauth/login`. The fixed native completion
notification is `mcpServer/oauthLogin/completed`. The App handles server name and
success in its frontend; this GUI qualification did not capture a literal raw
internal loginId/completion payload. The separate native protocol test observes
that wire contract directly.

Relevant pinned source paths:

- `app-server-protocol/src/protocol/v2/mcp.rs`
- `app-server/src/request_processors/mcp_processor.rs`
- `core/src/config/auth_keyring.rs`
- `rmcp-client/src/oauth/resolved_store.rs`
- `rmcp-client/src/oauth_callback.rs`

On Windows, `mcp_oauth_credentials_store="auto"` with the effective native
`secret_auth_storage=true` uses Secrets. Strict keyring with that feature uses
the same encrypted Secrets backend; strict keyring with the feature false
exercises Direct. File/keyring/auto alone do not identify the concrete backend.
Do not invent an `auth_keyring_backend` setting or disable the Windows vault.

Direct credential target names use `AbsolutePathBuf::canonicalize()` (`dunce`);
Secrets master-key names use `Path::canonicalize()`. Their extended-path prefix
representations can differ. Use native APIs and exact target metadata rather
than manually moving credentials. Local logout is not proof that an OAuth
authorization server has revoked all old grants.

## Retained failures and corrections

| Original observation | Correction and final interpretation |
| --- | --- |
| Explicit configured resource plus discovered resource produced duplicate OAuth parameters | Omit the redundant optional fixture resource; retain native discovery and validation. No runtime patch |
| Consent form returned `invalid_origin` | Correct the fixture's Referrer-Policy to `same-origin`; continue rejecting null/foreign Origin and invalid CSRF |
| Wrong completion-method spelling in an early observer | Correct the observer; preserve the original record rather than calling it a captured notification |
| Direct-store observer missed a target | Match the native canonicalization representation; do not change or migrate runtime credentials |
| External CLI removal left a row under “From plugins” | The label describes status/query entries absent from current configured names, not proof of a plugin. A normal same-version App restart removed it |
| Final private controller required every initial backend PID to remain alive | Preserve its failed assertion; normal current-backend status independently verified the same App and its replacement backends |

## Extension boundaries

The signed App supplies its bundled tool pipe and Node through its own private
environment. Never persist launch-specific pipe values, copy proprietary plugin
code, impersonate a reserved marketplace, or replace native metadata injection.
Installation/listing is a separate assertion from actual tool discovery and
execution. Main/child calls require native thread attribution and the original
per-tool approval policy.

The fixed build has no generic local-path `plugin add` option, and its reserved
bundled marketplace rejects an arbitrary direct source. Those failures are
preserved. The completed owned-plugin lifecycle used the supported native
marketplace path; signed App-tool execution used the actual packaged integration.
