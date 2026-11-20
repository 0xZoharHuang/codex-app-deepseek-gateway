"""Version-matched model metadata for the official Codex App runtime.

Native instruction templates and runtime selectors come from this binary's
debug models --bundled output, never an older CLI's prompt.
Aliases are internal routing identities, not claims that GPT is serving them.
"""
from __future__ import annotations
from copy import deepcopy
import hashlib
import json
from typing import Any, Mapping, Sequence

NATIVE_VERSION = "0.169.0-alpha.11.1"
NATIVE_COMMIT = "226fa88979b1a9d76dc36f339ee9c5fbdb2c8e7f"
NATIVE_CATALOG_CAPTURE_SHA256 = "9e01cd50ec8c4ba6d782413d1154cb2dbe1da63e014d622631b0603bc19659d6"
NATIVE_CATALOG_VALUE_SHA256 = "5a1acd847e4d23245f70f399d559fa51307a0410014851b508ae5f973034f28c"
PINNED_MODEL = "deepseek/deepseek-v4.1-flash-1017"
TEMPLATE_MODEL = "gpt-6.1-sol"
CONTEXT_WINDOW = 1_048_576
AUTO_COMPACT_TOKEN_LIMIT = 650_000
# P169 Guardian may choose its independent reviewer. V2 sampling uses
# gpt-5.6-luna and shares the decision alias with the signed App's helpers.
NATIVE_AUXILIARY_ALIASES = ("gpt-5.6-luna", "codex-auto-review")
# Each fixed desktop helper keeps its own captured P169 metadata.
APP_AUXILIARY_ALIASES = ("gpt-6-luna", "gpt-6.1-sol", "gpt-6.1-terra")
REQUIRED_AUXILIARY_ALIASES = (*NATIVE_AUXILIARY_ALIASES, *APP_AUXILIARY_ALIASES)
REQUEST_MODEL_IDS = (PINNED_MODEL, *REQUIRED_AUXILIARY_ALIASES)

class CatalogCompatibilityError(ValueError):
    """The supplied binary/catalog combination has not been qualified."""

def _model(template: Mapping[str, Any], *, slug: str, hidden: bool) -> dict[str, Any]:
    result = deepcopy(dict(template))
    messages = result.get("model_messages")
    if isinstance(messages, dict):
        instructions = messages.get("instructions_template")
        if isinstance(instructions, str):
            for identity in (
                "You are Codex, an agent based on GPT-6.",
                "You are Codex, an agent based on GPT-5.",
            ):
                if instructions.startswith(identity):
                    instructions = (
                        "You are Codex, an agent powered by DeepSeek V4.1 Flash 1017."
                        + instructions[len(identity):]
                    )
                    break
            if instructions.startswith("You are Codex, an agent based on GPT"):
                raise CatalogCompatibilityError("Unrecognized native model identity template")
            messages["instructions_template"] = instructions
    for field in ("base_instructions", "comp_hash", "available_access_programs",
                  "model_specialty", "default_service_tier"):
        result.pop(field, None)
    result.update(
        slug=slug,
        display_name=("DeepSeek V4.1 Flash 1017" if not hidden
                      else f"DeepSeek internal route ({slug})"),
        description=("DeepSeek V4.1 Flash 1017 through the local Codex App gateway."
                     if not hidden else
                     f"Internal Codex routing alias {slug}; inference uses {PINNED_MODEL}, not an OpenAI model."),
        default_reasoning_level="high",
        supported_reasoning_levels=[
            {"effort": "low", "description": "DeepSeek low reasoning"},
            {"effort": "high", "description": "DeepSeek high reasoning"},
            {"effort": "max", "description": "DeepSeek maximum reasoning"},
            {"effort": "ultra", "description": "Native automatic delegation; DeepSeek workers use high reasoning"},
        ],
        visibility="hide" if hidden else "list",
        supported_in_api=True,
        priority=100 if hidden else 0,
        additional_speed_tiers=[],
        service_tiers=[],
        availability_nux=None,
        upgrade=None,
        support_verbosity=False,
        default_verbosity=None,
        supports_reasoning_summary_parameter=False,
        default_reasoning_summary="none",
        context_window=CONTEXT_WINDOW,
        max_context_window=CONTEXT_WINDOW,
        auto_compact_token_limit=AUTO_COMPACT_TOKEN_LIMIT,
        input_modalities=["text", "image"],
        # Let native features select direct/mixed/code mode. The borrowed
        # template's model override must not mask explicit user choices.
        tool_mode=None,
        use_responses_lite=False,
        supports_reasoning_effort_updates=False,
        include_skills_usage_instructions=True,
        include_plugin_usage_instructions=True,
        include_apps_usage_instructions=True,
        multi_agent_reasoning_effort="high",
    )
    return result

def build_catalog(
    bundled_catalog: Mapping[str, Any], *,
    native_version: str,
    multi_agent_version: str = "v2",
    auxiliary_aliases: Sequence[str] = REQUIRED_AUXILIARY_ALIASES,
) -> dict[str, Any]:
    """Build one visible DeepSeek model plus explicitly requested native aliases.

    Default aliases are fixed by the captured P169 runtime and the candidate
    desktop App. Each hidden alias carries its own native runtime metadata.
    The gateway's explicit aliases all use the same pinned upstream DeepSeek.
    An explicit V1 selection changes only the visible pinned model selector;
    hidden auxiliary metadata stays native. V2 remains the default. Native
    threads retain their recorded version, so a selection requires a fresh chat.
    No request routing, credentials, or application state are altered here.
    """
    if not isinstance(multi_agent_version, str) or multi_agent_version not in ("v1", "v2"):
        raise CatalogCompatibilityError("multi_agent_version must be v1 or v2")
    version = native_version.removeprefix("codex-cli ").strip()
    if version != NATIVE_VERSION:
        raise CatalogCompatibilityError(f"Unqualified native runtime: {version}")
    try:
        value = json.dumps(bundled_catalog, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (TypeError, ValueError, UnicodeError) as error:
        raise CatalogCompatibilityError("Malformed P169 bundled catalog") from error
    if hashlib.sha256(value).hexdigest() != NATIVE_CATALOG_VALUE_SHA256:
        raise CatalogCompatibilityError("P169 bundled catalog differs from the pinned native capture")
    entries = bundled_catalog.get("models")
    if not isinstance(entries, list):
        raise CatalogCompatibilityError("Bundled model catalog must contain models")
    models: dict[str, dict[str, Any]] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("slug"), str):
            raise CatalogCompatibilityError("Malformed bundled model")
        if entry["slug"] in models:
            raise CatalogCompatibilityError("Duplicate bundled model slug")
        models[entry["slug"]] = entry
    template = models.get(TEMPLATE_MODEL)
    if template is None:
        raise CatalogCompatibilityError("Qualified native template model is missing")
    selectors = {"multi_agent_version": "v2", "tool_mode": "code_mode_only",
                 "shell_type": "unified_exec", "apply_patch_tool_type": "freeform",
                 "node_repl_auto_review_required": True, "node_repl_disabled": False}
    if any(template.get(key) != expected for key, expected in selectors.items()) or template.get("supports_search_tool") is not True:
        raise CatalogCompatibilityError("Native runtime selectors differ from the qualified template")
    messages = template.get("model_messages")
    if not isinstance(messages, dict) or any(
        not messages.get(key) for key in
        ("instructions_template", "guardian_v2", "confirmation_policies", "multi_agent")
    ):
        raise CatalogCompatibilityError("Version-matched native instruction templates are incomplete")
    if template.get("guardian") == {}:
        raise CatalogCompatibilityError("An empty Guardian policy would disable ordinary review")
    if isinstance(auxiliary_aliases, (str, bytes)):
        raise CatalogCompatibilityError("Auxiliary aliases must be a sequence of native model ids")
    if any(not isinstance(alias, str) for alias in auxiliary_aliases):
        raise CatalogCompatibilityError("Invalid auxiliary alias")
    if len(set(auxiliary_aliases)) != len(auxiliary_aliases):
        raise CatalogCompatibilityError("Duplicate auxiliary alias")
    if set(auxiliary_aliases) != set(REQUIRED_AUXILIARY_ALIASES):
        raise CatalogCompatibilityError("P169 requires exactly the captured main and auxiliary model roles")
    if any(alias not in models or models[alias].get("supported_in_api") is not True
           for alias in auxiliary_aliases):
        raise CatalogCompatibilityError("Auxiliary alias lacks compatible native model metadata")
    if any(model.get("auto_review_model_override") is not None
           for model in (template, *(models[alias] for alias in auxiliary_aliases))):
        raise CatalogCompatibilityError("P169 reviewer selection changed from the captured default")
    result = [_model(template, slug=PINNED_MODEL, hidden=False)]
    result[0]["multi_agent_version"] = multi_agent_version
    result.extend(_model(models[alias], slug=alias, hidden=True) for alias in auxiliary_aliases)
    return {"models": result}
