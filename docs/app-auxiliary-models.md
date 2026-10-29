# Native and desktop auxiliary model names

The qualified Codex App can request a helper model by an OpenAI-style name even
when the selected provider is a custom gateway. The gateway must recognize the
fixed App's legitimate names while refusing requests outside the effective
policy. The default policy includes the four qualified names below. Users retain
the existing ability to configure additional explicit accepted_models aliases;
the App catalog and policy must contain the same names when used together.

All default names below route to DeepSeek V4.1 Flash 1017. The names identify
native call roles; they do not claim that an OpenAI model or cloud account is
being used.

| Accepted name | Fixed-source purpose | Catalog visibility |
| --- | --- | --- |
| deepseek/deepseek-v4.1-flash-1017 | Main and native child work | Visible |
| gpt-5.6-luna | Native Guardian V2 auxiliary selector | Hidden |
| gpt-6-luna | Desktop title, description and ambient/safety helper default | Hidden |
| gpt-6.1-sol | Desktop visualization and local automation model default | Hidden |

The desktop defaults come from official App 26.1016.8120.0's
codex-app-model-routing defaults in src-CSKkIr_z.js. The signed App bundle is
unchanged. A fresh helper can choose the available provider default through the
native fallback option. A normal helper fork has no such option. For example,
Rename or Pin can acknowledge the title/pin action while an unconfigured
gpt-6-luna description fork fails in the background. This was reproduced in the
real Windows App with the old two-name policy; a successful main chat or generated
commit message did not cover that path.

The adapter uses exact names, with no gpt prefix rule. App helper aliases receive
the qualified native metadata for the actual DeepSeek runtime. Existing native
Guardian metadata remains separately sourced. Unknown names still fail before
contacting the model provider. Strict JSON output still follows the explicitly
configured same-model structured-output provider route; this change adds no
provider and no request-failure fallback. This is not a global restriction that
forbids a user's other explicitly configured aliases.

## Explicit upgrade from an older policy

An existing gateway.json is never expanded automatically. App preparation/checks
report the missing alias names, and the existing file remains unchanged. Upgrade
while the project-owned App and gateway are normally stopped:

1. Retain the previous gateway.json, its hash, App configuration and immutable
   catalog. Use the normal managed installation/upgrade procedure.
2. Deliberately update only accepted_models to the four exact names in this table.
   Preserve allowed_providers, proxy settings, media policy, external search
   selection and all other unrelated fields. Do not add wildcard aliases.
3. Use the new release's separately trusted, version-matched catalog. Its catalog
   builder includes the same four names by default. Keep the old catalog file;
   new bytes receive a new content hash and immutable path.
4. Run the installed app-configure command with that catalog and trusted SHA,
   then start the owned gateway and run app-check. A policy/catalog mismatch is
   rejected. Complete a normal cold launch before claiming the new combination.
5. In an owned chat, exercise the App's normal Pin action, observe the actual
   description helper fork/turn and its successful model receipt, confirm the
   description is written back, then restore the original pin state.

See [installed App entrypoints](installed-app-entrypoints.md) for the exact
managed lifecycle commands. Do not modify App JavaScript, forge a login, rewrite
fork behavior, or change permission settings to repair this model-name mismatch.

## Qualification boundary

Source inspection identifies additional fixed helper names. Protocol tests prove
that exact configured aliases use the pinned DeepSeek model, that the old policy
fails, that unknown names remain rejected, and that policy bytes are not silently
rewritten. Those tests do not establish that the closed App consumed a successful
helper result. The actual App retest must bind the GUI action, helper thread and
turn, response, and metadata consumer separately.

Account-gated ChatGPT services remain subject to their native account requirements.
In particular, desktop account-scoped turn summaries are distinct from native
Codex memories and compaction. These aliases do not create cloud entitlements or
enable account-only services.
