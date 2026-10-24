# Synthetic loopback OAuth MCP fixture

This test-only HTTP server uses official mcp==2.3.0. It has no real account,
external provider, outbound HTTP client, filesystem tool, or vault integration.
Its single read-only MCP tool, read_reference, returns fixed synthetic data.
It is not a gateway, native runtime, or credential manager.

Implementation: tests/fixtures/oauth_mcp/server.py.
Self-tests: tests/test_oauth_mcp_fixture.py.
Keep these files outside the production gateway package.

## SDK responsibilities and one explicit boundary

FixtureProvider implements OAuthAuthorizationServerProvider. The SDK supplies
metadata, client registration, authorize, token, revoke, Bearer middleware,
PKCE verification, client authentication, redirect matching, scope checks, and
MCP HTTP transport. The fixture supplies in-memory identity/code/token records
and a small Allow/Deny page.

SDK 2.3.0 TokenHandler parses the optional token-request resource field but does
not compare it or pass it to the provider exchange hooks. A small fixture
boundary check rejects an explicit resource different from this nonce's MCP URL.
Omission inherits the already resource-bound code/refresh grant. Issued tokens
carry that resource; AuthSettings.validate_token_resource=True is enabled.
No SDK or production gateway code was patched.

The provider retains known expired access records for SDK revocation lookup,
so revoking expired access also revokes its paired refresh. SDK Bearer
middleware still denies expired access. Lookup is distinct from authorization.

References: [official SDK](https://github.com/modelcontextprotocol/python-sdk),
[MCP authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization).

## Launch and stop

Copy the fixture source into the intended user's owned acceptance workspace
without preserving SSH-created owners/ACLs. Compare SHA256 with
.evidence/oauth-mcp-fixture-handoff.json. Use an independently verified Python
environment containing mcp==2.3.0. Example PowerShell, substituting the owned copy:

    & 'D:\Projects\codex-app-deepseek-gateway\.venv\Scripts\python.exe' -I -X utf8 'D:\OWNED-ACCEPTANCE-WORKSPACE\oauth_mcp\server.py' --access-ttl 60 --lifetime 1800

The process binds only 127.0.0.1 on an OS-assigned port and generates a fresh
128-bit nonce. Its one JSON startup descriptor contains base, mcp_url,
server_name, events_url, and nonce_sha256. Its startup_state is bound_not_ready:
poll the authorization metadata URL for HTTP 200 before configuring the App.

For a background process, retain its exact stdin pipe and process handle.
Write one q to its stdin and wait for normal exit. The daemon stdin reader
only requests Uvicorn shutdown; it is not a command interpreter. The lifetime
timer also stops it within 10–3600 seconds. Never kill by image name.
Record PID and creation time if emergency exact cleanup becomes necessary,
and independently verify that the listener disappears.

Self-tests use an environment containing only SystemRoot, WINDIR, and PATH.
No OpenRouter, OpenAI, CDS, or Windows vault credential is required.

## App configuration and flow

Use the exact server_name and mcp_url from the descriptor. Add only one entry
to the chosen project or dedicated App home, retaining normal permissions:

    [mcp_servers.cdag_oauth_REPLACE_WITH_DESCRIPTOR_NONCE]
    url = 'http://127.0.0.1:REPLACE_WITH_DESCRIPTOR_PORT/mcp/REPLACE_WITH_DESCRIPTOR_NONCE'

Do not paste access/refresh tokens, client secrets, codes, or verifiers into
App prompts or configuration. Use the normal App Connect/Login action.
The consent page explicitly identifies a synthetic local test and offers
Allow and Deny.

Registered redirects must be exact HTTP loopback callbacks on 127.0.0.1 or ::1,
an unprivileged port, and /callback or /callback/<safe-id>. Remote hosts,
userinfo, query/fragment callbacks, plain PKCE, duplicate security fields,
and wrong explicit resources are rejected. If this App uses a different
callback mechanism, record it first; do not silently broaden the fixture.

## Actual App acceptance assertions

1. Confirm the dedicated CODEX_HOME and Electron userData and preserve original
   config. The local server descriptor is not proof of App connectivity.
2. Connect in App and Deny once. Verify failed/cancelled login does not yield
   usable tool access.
3. Connect again and Allow. Invoke the read-only tool from an App task; retain
   native call IDs and the returned synthetic identity.
4. Keep the server alive, wait past the appropriate expiry/refresh threshold,
   and invoke again. Check its safe event list for native refresh.
   Replacing a token in the controller is not a native refresh test.
5. Cold-reopen the App with the server still running and verify native stored
   credentials reconnect. Restarting the fixture clears all in-memory client
   and token records, so it cannot prove persistence across a server restart.
6. Exercise native logout/re-auth through the actual App entry point. The fixed
   source's public logout helper removes local credentials; this does not prove
   a GUI entry point or RFC 7009 request exists. The fixture supports /revoke,
   but do not require that event from a native path that only clears local
   storage, or count CLI logout as GUI evidence.
7. Record effective store configuration for each case, then login, invoke,
   cold-reopen, clean up, and independently verify. The native TOML setting is
   mcp_oauth_credentials_store = "file" | "auto" | "keyring".
   features.secret_auth_storage selects Secrets versus Direct keyring in the
   fixed runtime. Use an explicitly prepared case or reversible transition;
   leftovers from another mode do not establish a pass. Do not disable modes
   to narrow the acceptance scope.

The self-tests perform zero vault operations. Root owns all actual App and
native credential-store acceptance.

## Diagnostics and limits

GET events_url through a proxy-free local client. It returns only bounded
fixed event names, timestamps, nonce hashes, and counts. It never returns
access/refresh tokens, codes, verifiers, client secrets, consent-CSRF values,
or client IDs. Access logging and exception-body logging are disabled in the
standalone process.

The startup descriptor is operational endpoint metadata. Keep it separate
from flow-event logs. Do not publish raw token/register responses, callback
query strings, or consent form bodies.

Limits: 64 KiB bodies, 16 clients, 32 pending consents, 32 codes, 64 token pairs,
512 retained events, 16 concurrent connections, and an explicit lifetime.
All OAuth state remains in memory.

## Synthetic same-name / same-URL control plan

This is a plan only; this task performed zero vault operations.

- Use a fresh nonce-specific server name and the same live URL in two explicitly
  prepared temporary runtime homes A and B.
- Seed only a newly generated synthetic control credential in A via the native
  store API. Use B for the App case.
- Before and after B login/refresh/logout, inspect only that exact A synthetic
  record via the native API and compare a private hash or equality boolean.
  Never enumerate the vault or use existing credential records.
- Default unscoped storage was tested with an in-memory sentinel in the native
  patch fixture. Do not read or write the real default service to repeat it.
- For File/Auto/Keyring and Direct/Secrets, verify the actual selected store and
  remove only the exact nonce-specific synthetic record. Transport tests cannot
  prove native storage isolation.

## Cleanup

Remove only the nonce-specific project/App MCP entry and its synthetic
credential through the appropriate native path. Stop the exact fixture through
stdin or lifetime, verify listener absence, and retain safe evidence and hashes.
Preserve user sessions, default App configuration, other MCP/plugin entries,
CDS state, and shared Windows credentials.
