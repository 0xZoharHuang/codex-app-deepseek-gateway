# Reversible Codex App configuration

The gateway configures the official App through its documented custom provider interface. It does not replace the App runtime, log in or out, edit `auth.json`, enable or disable runtime features, create threads, or install a global proxy. All paths are explicit parameters. Configure only the intended Windows user, after stopping that user's relevant App instance; other users and existing CDS instances remain independent.

## API and generated settings

`codex_app_deepseek.app_config.prepare(config_path, *, catalog_path, auth_command, port, model=PINNED_MODEL)` renders a merge **in memory**. `auth_command` is an existing absolute path to the gateway executable. It is never invoked by `prepare`. The returned `PreparedConfig` holds original/configured content privately from its normal representation; do not serialize the complete object or log its `rendered_text` or `_original` fields. Existing configuration can contain unrelated credentials.

The merge selects provider `codex_app_deepseek`, model `deepseek/deepseek-v4.1-flash-1017`, and the supplied absolute `model_catalog_json` path. Its provider settings are:

```toml
[model_providers.codex_app_deepseek]
name = "Codex App DeepSeek Gateway"
base_url = "http://127.0.0.1:18765/v1" # caller supplies port
wire_api = "responses"
supports_websockets = false # use the native HTTP/SSE transport

[model_providers.codex_app_deepseek.auth]
command = 'C:\absolute\gateway\codex-app-deepseek.exe'
args = ["auth-token"]
timeout_ms = 30000
refresh_interval_ms = 300000
```

The authentication helper emits the dedicated local gateway token according to the official command-backed auth contract. The configuration module does not call the helper, read either credential, or store an OpenRouter key. Existing `env_key`, `experimental_bearer_token`, or `requires_openai_auth` fields in the **target provider** cause a conflict rather than mixing authentication mechanisms. Other provider tables are preserved.

The provider explicitly selects the native HTTP/SSE transport with `supports_websockets = false`. If this project's provider previously set it to `true`, preparation deliberately changes it and records `model_providers.codex_app_deepseek.supports_websockets` in `changed_settings`; it is not represented as an unchanged user setting or a disabled runtime feature.

Memory extraction and consolidation are auxiliary model requests. The matched native runtime defaults those jobs to `gpt-5.6-luna` and `gpt-5.6-terra`; for the all-DeepSeek configuration, this merge explicitly sets both `[memories].extract_model` and `[memories].consolidation_model` to the pinned DeepSeek model. Other memory switches, thresholds and comments are preserved. This changes model routing, not whether memory runs, and does not claim that memory behavior has passed the separate runtime acceptance gate.

The caller supplies a catalog produced for the **actual App runtime version**. This module verifies readable JSON, a top-level `models` array, and the pinned model's `slug`; it does not invent capability declarations or claim that these checks establish native model compatibility. The catalog hash must remain unchanged between preparation and apply.

`tomlkit` preserves unrelated tables, settings, and comments. A `tomllib` readback verifies the result. For a previously absent config only, the module adds `sandbox_mode = "workspace-write"` and `approval_policy = "on-request"`. Existing permission settings, profiles, approvals and feature flags are retained, including Guardian, memory, compaction, multi-agent and tool-search choices. Their presence in the configuration is not proof of runtime acceptance.

## Apply and rollback

```python
from codex_app_deepseek.app_config import prepare, apply, restore

planned = prepare(
    user_config_path,
    catalog_path=version_matched_catalog,
    auth_command=installed_gateway_executable,
    port=18765,
)
checkpoint = apply(planned, backup_dir=private_checkpoint_root)
# Persist or display checkpoint.manifest_path, not the PreparedConfig contents.

# On explicit rollback:
result = restore(checkpoint.manifest_path)
```

`apply` checks that the target has not changed since preparation, creates a unique private backup directory, records whether the config existed and its original/applied SHA-256, then replaces the config atomically. Backup and config temporary files are protected before content is written. On Windows, the backup directory ACL grants only the current SID and SYSTEM, with inherited permissions removed; on POSIX it uses owner-only permissions. This is a local protection boundary, not protection against the operating system administrator.

The `PreparedConfig` and `ConfigCheckpoint` include `changed_settings`, a list of managed field paths changed by the merge, without old or new values. The checkpoint persists the same list so auxiliary-model and transport changes are explicit and auditable.

The original configuration is retained byte-for-byte, including BOM and line endings. A checkpoint JSON contains paths, hashes, existence, timestamps and state, never configuration contents or token values. A crash before the final checkpoint status update remains recoverable if the actual config matches the recorded applied hash. Checkpoints are local recovery records; keep them private and do not publish them because their adjacent original config may contain unrelated secrets.

`restore` verifies the backup hash and requires the current config hash to equal this checkpoint's applied hash. It restores exact original bytes, or removes the config if this operation created it. Later user edits cause an explicit refusal; they are never overwritten. Repeated restore is safe when the already-restored file still matches the original state. A repeated identical apply is content-idempotent and produces its own checkpoint; roll back nested checkpoints in reverse order.

Atomic replacement and optimistic hash checks protect incomplete writes and detected intervening edits. They cannot lock out an unrelated editor writing at exactly the same time: close the relevant App/settings editor before apply or rollback. Do not apply to another user's live config. Symlink/junction destinations, non-TOML configuration targets, missing/invalid catalogs and conflicting target authentication fail before configuration mutation.

## Acceptance limits

The tests exercise real Windows filesystem paths, ACLs, Unicode/spaces, existing provider tables, comment retention, exact restoration, concurrent-edit refusal, catalog changes, and absence of secret content in return representations/checkpoints. They never touch a user's real App configuration, invoke a model, read a credential, or establish App runtime equivalence. App GUI and protocol/runtime acceptance are separate delivery gates.

Official interface: [custom model providers and command-backed auth](https://learn.chatgpt.com/docs/config-file/config-advanced#custom-model-providers).

## Preserving later App configuration changes

Schema 2 checkpoints save both the original bytes and a private, hash-verified
applied snapshot. Restore compares original, applied and current TOML and changes
only the exact owned leaf fields. Later project trust entries, desktop settings,
other providers and unrelated comments survive. A user change to an owned value
or its parent table causes a conflict before any write. Booleans and integers
are not equivalent, and quoted literal keys cannot impersonate dotted paths.

If the current file is byte-identical to the applied snapshot, restore recovers
the exact original bytes, including BOM/CRLF. Otherwise it preserves current
formatting/comments where possible and records the actual restored hash. Repeating
restore checks that resulting hash before acting. Schema 1 without an applied
snapshot retains strict whole-file protection; migration requires recovering
bytes with an exact match to the previously recorded applied SHA256.

The actual three-checkpoint App configuration was replayed in a private fixture:
only desktop, windows, and projects remained, with their exact values preserved.
This is proof of configuration merge/rollback behavior, not full App runtime
qualification. The live App configuration was not changed by this replay.

### Recoverable restoration evidence

Schema-2 restoration now records the actual current input C and restored output R
as private `restore-input.toml` / `restored.toml`, each with a hash. It publishes a
`restore_prepared` checkpoint before changing config, then marks `restored` after
the write. Recovery distinguishes a crash before the write from one after it.
The lifecycle registry v2 pins immutable B/A ownership fields and recomputes
B/A/C-to-R; it does not trust a mutable `state` label or accept an arbitrary new
manifest hash. This also permits verified manual restores followed by uninstall,
including several checkpoints on the same config file and later user edits.
Legacy records lacking this evidence retain strict checks instead of guessing.

The live default App config was restored through all three project checkpoints
while App/native processes were absent. Desktop, Windows and project settings
retained their exact parsed values. A separate fresh App home is now prepared for
the namespace-patched runtime; no sandbox has been initialized there yet.
