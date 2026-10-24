"""Version-matched model metadata for the official Codex App runtime.

Native instruction templates and runtime selectors come from this binary's
debug models --bundled output, never an older CLI's prompt.
Aliases are internal routing identities, not claims that GPT is serving them.
"""
from __future__ import annotations
from copy import deepcopy
from typing import Any, Mapping, Sequence

NATIVE_VERSION = "0.163.0-alpha.11.1"
NATIVE_COMMIT = "680c4ba93ec79c292287fb9e0b627dc7a76e3b5e"
PINNED_MODEL = "deepseek/deepseek-v4.1-flash-1017"
TEMPLATE_MODEL = "gpt-6.1-sol"
CONTEXT_WINDOW = 1_048_576
AUTO_COMPACT_TOKEN_LIMIT = 650_000
# Native guardian-v2/async_scorer/sampler.rs:40,237 at NATIVE_COMMIT.
REQUIRED_AUXILIARY_ALIASES = ("gpt-5.6-luna",)
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
        auto_review_model_override=PINNED_MODEL,
    )
    return result

def build_catalog(
    bundled_catalog: Mapping[str, Any], *,
    native_version: str,
    auxiliary_aliases: Sequence[str] = REQUIRED_AUXILIARY_ALIASES,
) -> dict[str, Any]:
    """Build one visible DeepSeek model plus explicitly requested native aliases.

    The default alias is hardcoded by this version of Guardian V2. Additional
    auxiliary aliases must be justified by this runtime's source or captured
    calls. The caller also configures those exact aliases in the gateway.
    No request routing, credentials, or application state are altered here.
    """
    version = native_version.removeprefix("codex-cli ").strip()
    if version != NATIVE_VERSION:
        raise CatalogCompatibilityError(f"Unqualified native runtime: {version}")
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
    if any(template.get(key) != expected for key, expected in selectors.items()):
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
    if len(set(auxiliary_aliases)) != len(auxiliary_aliases):
        raise CatalogCompatibilityError("Duplicate auxiliary alias")
    if any(alias not in models or alias == PINNED_MODEL for alias in auxiliary_aliases):
        raise CatalogCompatibilityError("Auxiliary alias lacks matching native model metadata")
    result = [_model(template, slug=PINNED_MODEL, hidden=False)]
    result.extend(_model(models[alias], slug=alias, hidden=True) for alias in auxiliary_aliases)
    return {"models": result}
