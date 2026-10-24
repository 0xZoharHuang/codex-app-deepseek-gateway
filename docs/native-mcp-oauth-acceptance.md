# Native App MCP OAuth and bundled-plugin acceptance handoff

This is a controller runbook for fixed signed App 26.1016.8120.0 and private runtime
C, source 680c4ba93ec79c292287fb9e0b627dc7a76e3b5e. Preparing or self-testing this
fixture does not qualify the App. The worker has not changed App configuration,
registered a native MCP client, opened a browser, read an OS vault, or run Codex.

## Live fixture

Read `.evidence/oauth-App-fixture-current.json`, then its qa_dir/ready.json and
ready-validation.json. They contain the random loopback URL, exact server name,
PID/creation identity, source SHA and ready time. The listener is 127.0.0.1 only.
Lifetime is 3,600 seconds; access tokens last 120 seconds. All tokens and clients
are synthetic and exist only in this server's memory. No production key is needed.
The config-snippet.toml in that QA directory is an unapplied proposal.

The existing Limited InteractiveToken runner owns the server task. It will remove
only its own task when the server exits. Create `stop.request` inside that exact
QA directory for graceful shutdown; require stopped.json, the runner's result.json,
and connection refusal at the original port. Do not stop processes by name.
Create `revoke.request` there to revoke this server's in-memory access/refresh pairs;
revoked.json and a fixed operator_tokens_revoked event confirm the action. This
control never reads the App's credential store. Events contain only fixed names,
timestamps and a nonce hash, never codes/verifiers/access or refresh tokens.

Original fixture self-tests: 12 passed in 12.64 seconds. They cover SDK discovery/DCR, actual loopback
callback, PKCE S256, deny/CSRF/replay, grant binding, real MCP initialize/list/call,
refresh rotation, expiry, standard revoke, operator revoke, safe receipts and stop.
The initial new control test failed because Windows venv python.exe launches a
runtime-python child. The corrected assertion verifies actual PID/creation and its
parent ancestry/fixture argv; it does not accept any process at a familiar path.
Both runs are retained as oauth-fixture-pre-App-selftest-r1.log and -r2.log.

Current independent fixture R2 (read ready.json again before use):

- Name: `cdag_oauth_79635554a76731332100b79b22f838e8`
- URL: `http://127.0.0.1:61412/mcp/79635554a76731332100b79b22f838e8`; resource is discovered automatically.
- scopes: `["fixture:read"]`; no redundant oauth_resource.
- Actual process: PID 27252, creation 1792713494.5311787; admin/session 1.
- Runner: `b432538589324161af12c821168110fc`; access TTL 120 seconds, fixed lifetime 3,600 seconds.
- Nominal expiry upper bound: 2026-10-23T00:58:15.405425+00:00. Do not extend or relaunch automatically.
- QA receipt directory: `D:\Projects\codex-app-deepseek-gateway\.evidence\oauth-App-b88934caa384411f888aa45305e4cee8`.
- This is a new identity/port after the old R1 naturally exited and its task was removed.
  R1's registration-only and invalid_origin browser failures remain preserved; no
  successful App authentication is inferred from readiness or fixture self-tests.

## Native App UI and configuration

The installed frontend source is webview/assets/plugins-settings-4cc1197353d1.js.
Its current labels are Plugins/MCPs, Servers, Add server, Streamable HTTP, URL,
Save and Authenticate. The Add menu can also say Add MCP server. Read back actual
UIA controls before acting; these labels are source evidence, not a GUI visit.

1. Let current tasks finish before changing their configuration. In the private
   admin App, open Settings / Plugins (MCPs), add a Streamable HTTP server with the
   exact ready.json name and URL, and save. Leave Bearer token env var empty: this
   test is OAuth discovery, not an injected bearer token. No OpenRouter credentials
   belong in this entry. If the GUI does not expose scopes/resource, root may merge
   only this owned server entry and the chosen storage setting into the private
   config using the existing reversible config process, then reload native config.
2. The source-correct TOML is the generated snippet: global
   mcp_oauth_credentials_store = "auto", then [mcp_servers.<name>] with url,
   scopes = ["fixture:read"], with no redundant oauth_resource for this fixed SDK,
   startup_timeout_sec = 20 and tool_timeout_sec = 20. No client_id/client_secret
   override is needed. The provider advertises DCR; native Automatic discovery
   should select it. If selecting DCR explicitly, record that choice.
3. Select Authenticate. The frontend sends mcpServer/oauth/login with name and
   optional clientRegistration, receives authorizationUrl/loginId, and opens an
   external browser. Use only the current admin's owned test browser session.
   Do not redirect another user's browser or change global browser defaults.
   The native callback defaults to a random 127.0.0.1 port with /callback or its
   native registration suffix; the fixture accepts both. Do not hard-code a port.
4. First choose Deny on the synthetic consent page. Require consent_denied plus
   the App's matching unsuccessful login completion; no read_reference call may
   succeed through that denied grant. A button click or callback page is not enough.
5. Authenticate again and choose Allow. Correlate consent_allowed/code_exchanged
   with the same App loginId success notification. Start a new owned App task and
   have DeepSeek discover and invoke this server's read_reference tool. Require a
   native McpToolCall success and structured identity synthetic-fixture-user, plus
   the independent fixture tool_called event. The server is read-only by design.
6. Keep the server alive while testing token expiry/refresh and cold App restart.
   Native refresh skew is 30 seconds, so refresh can happen before literal expiry.
   Require refresh_exchanged with no new consent/code exchange, then a successful
   actual MCP call. Close/reopen only the owned App through its isolated launcher;
   record the newly loaded private executable/home, then call the tool again.

Native source pointers: config/src/mcp_types.rs:223-300 (url transport, scopes,
oauth and resource); config/src/config_toml.rs:311-321 (store/callback);
rmcp-client/src/oauth_callback.rs:70 (default loopback callback);
app-server-protocol/src/protocol/v2/mcp.rs:294-350 (login params/results/notification);
app-server/src/request_processors/mcp_processor.rs:259-355 (ordinary OAuth path,
DCR/CIMD/Auto selection, save completion and MCP invalidation). This is ordinary
MCP OAuth, not enterprise EMA, and requires no OpenAI subscription entitlement.

## Storage, revoke and home-isolation scenarios

| Scenario | Native settings | Necessary observations |
| --- | --- | --- |
| Auto, Windows default | mcp_oauth_credentials_store="auto"; retain effective secret_auth_storage=true | App login/tool call; refresh and cold reopen; actual resolved store identified without reading tokens. No silent mid-client source switching |
| Strict keyring, Windows default | mcp_oauth_credentials_store="keyring"; secret_auth_storage=true | Native encrypted Secrets backend and successful App reload; a keyring error must fail, not silently become File |
| Direct keyring | keyring plus stable [features] secret_auth_storage=false, only in an owned test profile | Exercises the App C home-scoped direct keyring patch. Restore the prior setting afterward; do not modify another profile |
| File | mcp_oauth_credentials_store="file" | Only selected home .credentials.json metadata changes; actual App reuse after cold restart. Never print/read token values |
| Auto fallback | Auto with unavailable store in an isolated native mock/fault harness | Separate from healthy Auto. Existing native tests can establish decision logic; do not claim real App failure/fallback without a real scoped failure. Do not disable Windows vault globally |

Windows uses Secrets by default because the stable secret_auth_storage feature is
true on Windows (features/src/lib.rs:1067-1072; core/src/config/auth_keyring.rs:13-17,
110-117). There is no top-level auth_keyring_backend setting to invent. File/keyring/
auto alone do not distinguish Secrets versus Direct. Private C has a real published
version, avoiding native 0.0.0 dev-build coercion to File. The Auto resolved source is
pinned for each MCP client lifetime; refresh must not consult a stale alternate store
(rmcp-client/src/oauth/resolved_store.rs). Do not use a global vault outage as a test.

For server-side revoke, snapshot safe event counts, create revoke.request, require
revoked.json access_count=refresh_count=0, then invoke the tool through App. It must
fail/require authentication, not return cached success as a new execution. Record any
invalid-grant/401 transition. Fresh consent must restore normal calls. Local logout
is different: native mcp logout removes stored credentials; it is not evidence that
the OAuth server has revoked every old grant. Test both boundaries distinctly.

Home isolation must reuse the SAME server name and URL in two separate owned homes;
changing the name or URL would trivially change the credential key and prove little.
A must remain usable after B initially has no login, authenticates independently,
refreshes and logs out. A's login should not disappear and no default native App/CDS
credential target may be adopted or deleted. Use only native login/status/logout,
never hand-seed a keyring entry. A separate CLI-home storage probe is useful but must
be labeled CLI scope; it cannot replace actual App authentication/cold-reopen proof.
Root controls any such native execution and any second App-profile launch.

Evidence should use exact fixture events, App loginId/status, native tool calls,
store file metadata and exact synthetic target existence. Do not enumerate or read
unrelated real credentials or dump .credentials.json/secrets contents. Direct service
is Codex App DeepSeek MCP Credentials|<canonical-home SHA prefix>; Secrets remains
native per-home encrypted storage. File-mode logout still attempts native keyring
cleanup, which is why verifying cross-home deletion isolation is essential. Existing
App C mock tests prove scoped keyring calls; the real fixture run is additional proof.

## Signed bundled codex-app-tools

The installed codex-app-tools plugin is hidden:true and installWhenMissing:true.
Do not invent a normal visible gallery toggle for this build. Its descriptor is
isAvailable:()=>true, but automatic reconciliation still waits for a valid complete
DesktopFeatureAvailability. If absent, the App logs
bundled_plugins_reconcile_skipped_features_unavailable rather than installing.

The normal App-owned path materializes the signed packaged openai-bundled marketplace
under private CODEX_HOME/.tmp/bundled-marketplaces and invokes native marketplace/
plugin installation. An earlier source-only audit proposed direct marketplace add
from the signed package path as a manual fallback. Actual native execution rejected
it: marketplace openai-bundled is reserved and cannot be added from this source.
Exit code was 1; plugin add did not execute. Preserve the failure in
.evidence/next-app-native-features/apply-and-plugins.json. That manual fallback is
not available and must not be presented as supported. Next observe the App-owned
materialization/reconciliation and its authorized source; do not rename a reserved
marketplace, bypass its source check or force closed flags. The earlier static audit
records exact package hashes, not a successful installation.

Installation is not execution. main-CT5a1yCd.js Tc supplies the plugin-scoped enabled
override only when kc resolves this App's CODEX_APP_TOOLS_PIPE_PATH, the packaged
plugin definition and native Node. Oc refreshes the installed definition and asks
native config/batchWrite to reload. The packaged .mcp.json is deliberately disabled
until this App-owned injection, so hand-setting enabled=true alone is insufficient.
A standalone CLI without the live App pipe is not an equivalent test.

Actual acceptance: observe the local plugin/native status and its enabled override,
then from an App DeepSeek task invoke an innocuous read-only tool such as list_threads
or list_projects. Require actual tool discovery, MCP result and correct private App
thread attribution. The pipe handler requires thread metadata; a raw SDK call without
it should be rejected, not patched around. Keep the own task scope when checking
results, and do not send messages/create tasks as a plugin smoke test. Preserve the
package's native per-tool approval settings. Code-review is a separate feature/
cloud entitlement and must not be forced or counted as this local plugin gate.

Packaged plugin source/license remains proprietary; no package code is copied into
this repository. .evidence/bundled-plugin-local-entry-followup.json records the
immutable App/chunk hashes and this source-only conclusion.

## Scoped cleanup order

After the actual App assertions, snapshot safe events and per-mode results. Revoke
only this synthetic fixture if needed, then perform native logout for the exact
fixture server while its configuration still exists. Verify local authentication
is gone without reading tokens. Remove only that owned MCP entry and restore the
previous storage/secret_auth_storage settings with the reversible config process;
preserve all other later App edits. Finally create stop.request, require stopped.json
and the exact runner task cleanup result, and confirm the original loopback listener
is gone. Keep the small receipt/log bundle; remove only this run's temporary fixture
files after their hashes have been recorded. No global process/browser/vault cleanup.

## Approved ordinary-MCP entry for signed App tools (still unapplied by this agent)

The exact proposal is `.evidence/signed-app-tools-direct-mcp-unapplied.toml`. It
references the existing signed package
`OpenAI.Codex_26.1016.8120.0_x64__2p2nqsd0c76g0` and its original
`plugins/openai-bundled/plugins/codex-app-tools` directory. It copies only the
original MCP configuration fields: cmd.exe arguments, environment allowlist,
startup/tool timeouts, tool exposure and per-tool approvals. It changes the native
ordinary MCP entry to enabled; it does not install or impersonate a reserved
marketplace/plugin, copy package implementation, override a closed feature, or
start a replacement pipe/agent supervisor.

Both observed App-owned native backends (23432 and 5232, exact creation identities
in bundled-app-tools-selected-env.json) had CODEX_APP_TOOLS_PIPE_PATH with the same
hash and CODEX_MCP_NODE_PATH with the same hash. The selected Node file existed.
No pipe value or full environment was printed. The original signed .cmd selects
that native Node first. The config must inherit these variables from its parent
App process, never persist their current values: they change across launches.

Ordinary MCP calls get native threadId/sessionId metadata unconditionally at
core/src/mcp_tool_call.rs:521-546 and 1394-1425. The signed App pipe handler accepts
that metadata. Thus the source supports this entry without a plugin-only metadata
shim; actual App discovery and a read-only call are still the acceptance step.

Native hot reload is supported. app-server-protocol/src/protocol/v2/config.rs:1122
specifies reloadUserConfig; request_processors/config_processor.rs:161-184 and
367-425 reload user layers and publish runtime config to already-loaded threads.
The same signed App uses config/batchWrite with empty edits and reloadUserConfig=true
after refreshing a bundled definition. Empty edits are explicitly not classified
as session-defaults-only, so this path reaches reload. The prepared reload request
is `.evidence/signed-app-tools-direct-reload-request.json`; send it through the
current App-owned native connection, not a fresh unrelated CLI process. A GUI MCP
Save that invokes this native path is also appropriate. A plain external file
write or a successful CLI config read is not proof the running App reloaded it.

A new thread or restart is not required by the native reload implementation. For
acceptance, wait for any running turn to finish, request the native reload, then
use a fresh user turn in a loaded thread to discover and call a read-only tool.
This directly checks hot reload. A separate new thread can independently check
fresh initialization. If the controller has no safe current connection for the
reload request, use the existing isolated cold-restart path and label the result
cold reload rather than claiming hot reload. Never terminate all codex processes.

Actual fixed-C `codex plugin add --help` was run in a separate temporary CODEX_HOME
and returned no local-path installation option; the full output and binary SHA
are retained in `.evidence/native-plugin-add-help.json`, and the temporary home
was removed. It accepts a plugin selector from a configured/remote marketplace.
The earlier reserved-source rejection remains valid. Ordinary MCP success will
not be used as a substitute for the separate owned-plugin lifecycle test.

## First real OAuth attempt: duplicate resource correction

The first real App Authenticate registered a client and successfully returned an
authorization URL, but did not reach consent. Root inspected the owned browser
request shape: resource appeared twice with equal values, all other query keys
once. The fixed RMCP 4048ac4 auth.rs:1846-1860 already adds the discovered resource;
Codex perform_oauth_login.rs:729-733 appends configured oauth_resource without
de-duplication (919-929). The fixture rejects duplicate parameters before its
authorize handler, explaining the observed registration-only state.

The correct minimal fixture configuration omits the redundant optional
oauth_resource while retaining URL and fixture:read scope. Native metadata
discovery still supplies the same resource, and the fixture continues validating
resource/PKCE/state/redirect. No native patch or relaxed validation was used. The
original proposal is retained as config-snippet-original-explicit-resource.toml
in the owned QA directory; the current snippet is corrected but editing it does
not apply changes to App configuration. Original request/UI/event receipts remain.
The first failure did not establish a credential-store problem: native credential
saving occurs after a successful authorization callback. Service lifetime remains
unchanged; expiry is not a successful revocation or authentication result.

## Actual consent failure and Origin-policy correction

After removing the duplicate resource, real App authentication reached the consent
page. Deny was invoked 6.4 seconds later, and the owned browser displayed
invalid_origin. The retained browser-error-observation.json establishes this was
not a ticket-expiry result. No consent_denied/code/token event was recorded. The
original R1 server then reached its unchanged one-hour lifetime and exited 0.

The fixture had combined Referrer-Policy: no-referrer on its consent HTML with a
strict Origin check. The [Fetch Origin-header algorithm](https://fetch.spec.whatwg.org/#append-a-request-origin-header)
sets Origin to null for this form POST under no-referrer. The repaired consent page
uses same-origin, preserving the same-origin POST Origin while withholding a
cross-origin referrer. The authorization callback redirect still uses no-referrer.
Origin:null and foreign origins remain rejected, CSP form-action self remains,
and the browser CSRF cookie plus exact submitted token are still required. Fixed
classification events record only same/none/null/other, never raw headers or tokens.

Consent tickets and their CSRF cookie now last 300 seconds, matching the native
callback window; authorization codes remain 60 seconds, access tokens 120 seconds
and refresh tokens 3,600 seconds. The source repair passed 14 tests in 12.62 seconds,
including null/foreign rejection, correct same-origin denial, CSRF rejection,
expired consent and unchanged code/token limits. Evidence is
.evidence/oauth-fixture-origin-repair-selftest.json. These HTTP tests do not replace
a real browser POST. R2 is a new independent run for actual App Deny, Allow, tool,
refresh, revoke and cold-reopen observations; its readiness alone closes none of
those App gates.
