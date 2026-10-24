# Isolated App extension acceptance inputs

This bundle supplies reference data and native extension surfaces. It does not
contain an engineering repair, model fixture, agent loop, or an acceptance verdict.
The real App task and native agents must do the work.

## Frozen source and preparation

The parser baseline is Codex commit
`680c4ba93ec79c292287fb9e0b627dc7a76e3b5e`:

- `codex-rs/skills/src/parser.rs` parses skill YAML name/description.
- `codex-rs/skills/src/assets/samples/skill-creator/SKILL.md` and its
  `references/openai_yaml.md` supplied the authoring rules.
- `codex-rs/core-plugins/src/manifest.rs` accepts the compatibility manifest
  at `.codex-plugin/plugin.json`; component paths must start with `./`
  and cannot leave the plugin root.
- `codex-rs/core-plugins/src/marketplace.rs` discovers
  `.agents/plugins/marketplace.json` and resolves local plugin sources.

The [official plugin packaging documentation](https://developers.openai.com/plugins/build/plugins)
still documents this compatibility format. The fixture uses a skills-only plugin,
so it needs no transport path substitution or plugin credential.

The SDK is the official `mcp==2.3.0`, whose server class is `MCPServer`.
The test dependency `PyYAML==6.0.3` runs the frozen skill-creator validator.
Neither package is required by the production gateway protocol adapter.

## Files to copy under the acceptance user's Limited InteractiveToken

Copy only `tests/fixtures/app_extensions/` into the chosen acceptance workspace,
preserving relative paths:

```text
<workspace>/
  mcp_server.py
  data/baseline.json
  data/capabilities.json
  .agents/skills/inspect-cdag-baseline/SKILL.md
  .agents/skills/inspect-cdag-baseline/agents/openai.yaml
  .agents/plugins/marketplace.json
  plugins/cdag-source-reference/.codex-plugin/plugin.json
  plugins/cdag-source-reference/skills/trace-cdag-sources/SKILL.md
```

Do not copy SSH-created owners/ACLs onto the sandbox workspace. The controller
should create the target files as the intended interactive user and compare
their bytes with `.evidence/app-extension-fixtures-handoff.json`.
Do not copy `__pycache__`, test logs, the development repository, or credentials.

The baseline JSON describes the official signed binary. It deliberately does not
claim that a private patched runtime has that binary hash. Observe the actual
loaded executable separately.

## Project-local MCP configuration

Merge the following single entry into the copied workspace's project
`.codex/config.toml` after substituting the actual owned workspace path.
The interpreter must already have the fixed SDK available; verify its identity
and access under the same Limited token used for App acceptance.

```toml
[mcp_servers.cdag_acceptance_inventory]
command = 'D:\Projects\codex-app-deepseek-gateway\.venv\Scripts\python.exe'
args = ['-I', '-X', 'utf8', 'D:\PATH-TO-OWNED-ACCEPTANCE-WORKSPACE\mcp_server.py']
```

The snippet contains no approval overrides, provider changes, credentials, or
global configuration. Keep existing sandbox/approval policy. Trust the project
through the ordinary App workflow when required. For a copied independent Python
environment, substitute its verified executable rather than the development one.

The skill is project-local. The plugin marketplace merely makes the plugin
available; install it through the App's native local marketplace workflow. Do not
pretend that writing a manifest proves installation or model-visible discovery.

## Assertions for the actual App task

- The App discovers `inspect-cdag-baseline` in this workspace and loads its
  instructions when applicable or invoked.
- The App lists the local marketplace plugin, installs it in the dedicated
  runtime home, and exposes `trace-cdag-sources` from that plugin.
- Native MCP discovery exposes exactly `filter_capabilities`,
  `artifact_digest`, and `verify_artifact_digest`, with read-only,
  non-destructive annotations and structured output schemas.
- `filter_capabilities(family="web")` returns two real fixture records.
  Its digest must match an independent native-shell SHA256 over the copied file.
- `artifact_digest("baseline")` matches the copied baseline bytes.
  A matching supplied digest returns `matches:true`; 64 zeroes returns
  `matches:false`, not a successful verification claim.
- Native resource discovery lists `cdag-acceptance://baseline` and the template
  `cdag-acceptance://capability/{capability_id}`. Reading
  `mcp-extensions` returns that record; a missing ID is a real MCP error,
  followed by a successful resource read on the same connection.
- Invalid tool family/path/digest inputs must fail without reading other files.
  Compare all bundle file hashes after the task; the server is read-only.
- Use native web search, open, find, and click to verify relevant official
  sources from the inventory. Record actual tool calls and retrieved pages.
- Delegate part of the independent source checking to native child agents and
  preserve parent/child call attribution. External preparation must not supply
  the task's conclusions.
- App interruption/reopen and native MCP reconnect are separate observations.
  SDK-client self-tests do not close these App gates.

The server accepts only enumerated fixture filenames and capability IDs.
It has no network client, shell execution, arbitrary file path, or write tool.

## OAuth end-to-end plan: separate HTTP fixture, not stdio

OAuth belongs to the HTTP transport; it is not added to the stdio server above.
Follow the [MCP authorization specification](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)
and the official SDK's authorization-provider interface. A separate SDK fixture and HTTP self-tests are now available; see
[OAuth fixture instructions](oauth-mcp-fixture.md). Actual App OAuth and vault
acceptance are still pending.

For the future fixture:

1. Generate a fresh 128-bit random nonce for the server name and URL path.
   Bind to `127.0.0.1` on an OS-assigned port. Use only one synthetic local
   identity and a small in-memory token/code store. No real account or key.
2. Serve protected-resource and authorization-server metadata. The MCP endpoint
   returns a real 401 Bearer challenge pointing to its metadata. Advertise
   PKCE `S256`; reject missing/plain challenges and wrong resource/audience.
3. Prefer pre-registration with an explicit fixture public client identity, or
   the standard SDK's dynamic registration only when testing the fixed native
   compatibility path. Do not advertise unsupported client metadata modes.
   Validate exact registered loopback redirect URIs and echo the native state.
4. The authorization page explicitly says it authorizes a local synthetic test
   and offers Allow/Deny. A single-use code lasts at most 60 seconds. Token
   exchange validates the verifier, client, redirect and resource. Access
   tokens expire quickly; refresh rotates the synthetic refresh token.
5. In the App, configure only `cdag_oauth_<nonce>` under the dedicated home or
   project. Exercise normal OAuth login in its UI, deny once, then allow,
   call a read-only reference tool, wait for expiry, and call again to prove
   native refresh. Close/reopen the App and verify reconnect without another
   account login. Record metadata/PKCE/refresh events using hashes and counts,
   never token or authorization-code values.
6. Confirm synthetic logout/re-authentication through whatever entry point this
   fixed App actually exposes. The source audit established the native public
   logout helper, but did not establish a GUI logout RPC. Keep those scopes
   separate; do not call a CLI logout and label it GUI evidence.
7. Stop only the fixture process by its owned handle/PID and creation time.
   Remove only the nonce-specific MCP config and its synthetic credential
   through the native store path. Retain a cleanup ledger; never enumerate,
   read, migrate or delete another credential.

The currently available CDS `verify_native_mcp_lifetime.py` was read only.
It tests HTTP interruption/owner-exit lifetime and contains no OAuth PKCE fixture.
None of its native launcher, store, or orchestration code was copied.

## Cleanup scope

Before deleting the workspace fixture, remove this one project MCP entry and
uninstall only `cdag-source-reference` through the same dedicated App. Keep
independent receipts and hashes. Stop the stdio child through native MCP lifetime,
not by executable-name kills. Then remove only the copied bundle files, the local
marketplace entry, and the two fixture skill directories. Preserve unrelated
workspace files, user tasks, default App settings, and CDS state.
