from copy import deepcopy
import unittest
from codex_app_deepseek.catalog import (
    AUTO_COMPACT_TOKEN_LIMIT, CatalogCompatibilityError, NATIVE_VERSION,
    PINNED_MODEL, build_catalog,
)

def bundled():
    main = {
        "slug": "gpt-6.1-sol", "multi_agent_version": "v2",
        "tool_mode": "code_mode_only", "shell_type": "unified_exec",
        "apply_patch_tool_type": "freeform", "node_repl_auto_review_required": True,
        "node_repl_disabled": False, "comp_hash": "OpenAI-only-hash",
        "default_service_tier": "priority",
        "model_messages": {"instructions_template": "native current instructions",
                           "guardian_v2": {"policy": "native guardian"},
                           "confirmation_policies": {"confirm": "native"},
                           "multi_agent": {"prompt": "native v2"}},
        "experimental_supported_tools": ["clock", "send_user_message_async"],
        "supports_search_tool": True, "guardian": {"shell": "native-policy"},
    }
    alias = deepcopy(main)
    alias.update(slug="gpt-5.6-luna", multi_agent_version="v1")
    return {"models": [main, alias]}

class CatalogTests(unittest.TestCase):
    def test_preserves_native_runtime_and_current_messages(self):
        source = bundled()
        result = build_catalog(source, native_version=NATIVE_VERSION)["models"][0]
        for field in ("multi_agent_version", "shell_type",
                      "apply_patch_tool_type", "node_repl_auto_review_required",
                      "node_repl_disabled", "model_messages",
                      "experimental_supported_tools", "supports_search_tool", "guardian"):
            self.assertEqual(result[field], source["models"][0][field])
        self.assertEqual(result["slug"], PINNED_MODEL)
        self.assertIsNone(result["tool_mode"])
        self.assertEqual(source["models"][0]["tool_mode"], "code_mode_only")

    def test_changes_only_the_explicit_model_identity(self):
        source = bundled()
        body = " Shared workspace.\\nKeep native tools and approval policy."
        for model, identity in zip(source["models"], (
            "You are Codex, an agent based on GPT-6.",
            "You are Codex, an agent based on GPT-5.",
        )):
            model["model_messages"]["instructions_template"] = identity + body
        result = build_catalog(source, native_version=NATIVE_VERSION)
        for original, model in zip(source["models"], result["models"]):
            self.assertEqual(
                model["model_messages"]["instructions_template"],
                "You are Codex, an agent powered by DeepSeek V4.1 Flash 1017." + body,
            )
            self.assertEqual(model["model_messages"]["guardian_v2"],
                             original["model_messages"]["guardian_v2"])
            self.assertEqual(model["model_messages"]["multi_agent"],
                             original["model_messages"]["multi_agent"])
        self.assertIn("GPT-6", source["models"][0]["model_messages"]["instructions_template"])

    def test_does_not_mutate_inputs_or_share_nested_state(self):
        source = bundled()
        before = deepcopy(source)
        result = build_catalog(source, native_version=NATIVE_VERSION)
        result["models"][0]["model_messages"]["guardian_v2"]["policy"] = "changed"
        self.assertEqual(source, before)

    def test_public_catalog_is_only_the_actual_model(self):
        result = build_catalog(bundled(), native_version=NATIVE_VERSION)["models"]
        self.assertEqual([model["slug"] for model in result if model["visibility"] == "list"], [PINNED_MODEL])
        self.assertEqual([model["slug"] for model in result if model["visibility"] == "hide"],
                         ["gpt-5.6-luna", "gpt-6-luna", "gpt-6.1-sol"])
        self.assertEqual(result[0]["visibility"], "list")
        self.assertEqual(result[0]["service_tiers"], [])
        self.assertNotIn("comp_hash", result[0])
        self.assertNotIn("default_service_tier", result[0])

    def test_auxiliary_identity_is_hidden_and_honest(self):
        result = build_catalog(bundled(), native_version=NATIVE_VERSION,
                               auxiliary_aliases=("gpt-5.6-luna",))["models"]
        self.assertEqual(len(result), 2)
        self.assertEqual(result[1]["visibility"], "hide")
        self.assertIn("DeepSeek", result[1]["display_name"])
        self.assertIn("not an OpenAI model", result[1]["description"])
        self.assertEqual(result[1]["multi_agent_version"], "v1")
        self.assertEqual(result[1]["auto_review_model_override"], PINNED_MODEL)

    def test_rejects_unsupported_binary_and_duplicate_source(self):
        with self.assertRaises(CatalogCompatibilityError):
            build_catalog(bundled(), native_version="0.164.0")
        source = bundled()
        source["models"].append(source["models"][0])
        with self.assertRaises(CatalogCompatibilityError):
            build_catalog(source, native_version=NATIVE_VERSION)

    def test_output_defers_tool_mode_to_native_features_for_every_alias(self):
        result = build_catalog(bundled(), native_version=NATIVE_VERSION)
        self.assertTrue(all(model["tool_mode"] is None for model in result["models"]))
        source = bundled()
        source["models"][0]["tool_mode"] = None
        with self.assertRaises(CatalogCompatibilityError):
            build_catalog(source, native_version=NATIVE_VERSION)

    def test_rejects_missing_native_semantics(self):
        for field in ("multi_agent_version", "model_messages", "node_repl_auto_review_required"):
            source = bundled()
            del source["models"][0][field]
            with self.assertRaises(CatalogCompatibilityError):
                build_catalog(source, native_version=NATIVE_VERSION)
        source = bundled()
        source["models"][0]["guardian"] = {}
        with self.assertRaises(CatalogCompatibilityError):
            build_catalog(source, native_version=NATIVE_VERSION)

    def test_rejects_unproven_or_duplicate_alias_metadata(self):
        for aliases in (("invented-model",), ("gpt-5.6-luna", "gpt-5.6-luna"), "gpt-5.6-luna"):
            with self.assertRaises(CatalogCompatibilityError):
                build_catalog(bundled(), native_version=NATIVE_VERSION, auxiliary_aliases=aliases)

    def test_reasoning_and_context_are_model_appropriate(self):
        result = build_catalog(bundled(), native_version="codex-cli "+NATIVE_VERSION)["models"][0]
        self.assertEqual(result["context_window"], 1_048_576)
        self.assertEqual(result["auto_compact_token_limit"], AUTO_COMPACT_TOKEN_LIMIT)
        self.assertFalse(result["supports_reasoning_summary_parameter"])
        self.assertFalse(result["support_verbosity"])
        self.assertFalse(result["use_responses_lite"])
        self.assertEqual(result["multi_agent_reasoning_effort"], "high")
        self.assertIn("ultra", [x["effort"] for x in result["supported_reasoning_levels"]])
        self.assertEqual(result["input_modalities"], ["text", "image"])

if __name__ == "__main__":
    unittest.main()


def test_missing_or_false_native_search_support_fails_closed():
    for value in (None, False, 1):
        source = bundled()
        if value is None:
            source['models'][0].pop('supports_search_tool')
        else:
            source['models'][0]['supports_search_tool'] = value
        with unittest.TestCase().assertRaises(CatalogCompatibilityError):
            build_catalog(source, native_version=NATIVE_VERSION)

class NativeAgentVersionSelectionTests(unittest.TestCase):
    def test_v2_remains_exact_default(self):
        source = bundled()
        self.assertEqual(build_catalog(source, native_version=NATIVE_VERSION),
                         build_catalog(source, native_version=NATIVE_VERSION, multi_agent_version="v2"))

    def test_v1_changes_only_visible_main_selector_and_is_deep_copied(self):
        source = bundled()
        original = deepcopy(source)
        default = build_catalog(source, native_version=NATIVE_VERSION)
        selected = build_catalog(source, native_version=NATIVE_VERSION, multi_agent_version="v1")
        expected = deepcopy(default)
        expected["models"][0]["multi_agent_version"] = "v1"
        self.assertEqual(selected, expected)
        self.assertEqual(source, original)
        self.assertEqual(selected["models"][0]["slug"], PINNED_MODEL)
        self.assertEqual(selected["models"][1:], default["models"][1:])
        selected["models"][0]["model_messages"]["guardian_v2"]["policy"] = "changed"
        self.assertEqual(source, original)
        self.assertEqual(default["models"][0]["model_messages"]["guardian_v2"]["policy"], "native guardian")

    def test_invalid_native_agent_versions_are_rejected_without_input_mutation(self):
        for version in (None, True, False, 1, [], {}, "", "V1", "v3", "v1 "):
            source = bundled()
            original = deepcopy(source)
            with self.assertRaises(CatalogCompatibilityError, msg=repr(version)):
                build_catalog(source, native_version=NATIVE_VERSION, multi_agent_version=version)
            self.assertEqual(source, original, msg=repr(version))
