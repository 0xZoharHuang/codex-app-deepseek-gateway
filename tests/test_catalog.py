from copy import deepcopy
import gzip
import hashlib
import json
from pathlib import Path
import unittest
from unittest.mock import patch

from codex_app_deepseek import catalog
from codex_app_deepseek.catalog import (
    AUTO_COMPACT_TOKEN_LIMIT, CatalogCompatibilityError, NATIVE_VERSION,
    PINNED_MODEL, build_catalog,
)


def bundled():
    """Read the P169 `debug models --bundled` output from the pinned native build.

    This fixture preserves the OpenAI Codex Apache-2.0 bundled model data for
    upstream 226fa88979b1a9d76dc36f339ee9c5fbdb2c8e7f (see licenses/codex-LICENSE
    and licenses/codex-NOTICE). The gzip wrapper has mtime=0.
    """
    fixture = Path(__file__).with_name("fixtures") / "p169-bundled-models.json.gz"
    raw = gzip.decompress(fixture.read_bytes())
    assert hashlib.sha256(raw).hexdigest() == catalog.NATIVE_CATALOG_CAPTURE_SHA256
    return json.loads(raw)


def native_models(source):
    return {model["slug"]: model for model in source["models"]}


def admit_mutated_capture(source):
    """Set the test-only digest to exercise semantic checks after hash admission."""
    raw = json.dumps(source, ensure_ascii=False, sort_keys=True,
                     separators=(",", ":"), allow_nan=False).encode("utf-8")
    return patch.object(catalog, "NATIVE_CATALOG_VALUE_SHA256", hashlib.sha256(raw).hexdigest())


class CatalogTests(unittest.TestCase):
    def test_preserves_native_runtime_and_current_messages(self):
        source = bundled()
        template = native_models(source)["gpt-6.1-sol"]
        result = build_catalog(source, native_version=NATIVE_VERSION)["models"][0]
        for field in ("multi_agent_version", "shell_type",
                      "apply_patch_tool_type", "node_repl_auto_review_required",
                      "node_repl_disabled", "experimental_supported_tools",
                      "supports_search_tool"):
            self.assertEqual(result[field], template[field])
        for field in ("guardian_v2", "confirmation_policies", "multi_agent"):
            self.assertTrue(template["model_messages"][field])
            self.assertEqual(result["model_messages"][field], template["model_messages"][field])
        self.assertEqual(result["slug"], PINNED_MODEL)
        self.assertIsNone(result["tool_mode"])
        self.assertEqual(template["tool_mode"], "code_mode_only")

    def test_changes_only_the_explicit_model_identity_in_instructions(self):
        source = bundled()
        by_slug = native_models(source)
        result = build_catalog(source, native_version=NATIVE_VERSION)
        for model in result["models"]:
            slug = model["slug"] if model["slug"] != PINNED_MODEL else "gpt-6.1-sol"
            original = by_slug[slug]["model_messages"]
            instructions = original["instructions_template"]
            identity = instructions.split(".", 1)[0] + "."
            self.assertIn(identity, ("You are Codex, an agent based on GPT-6.",
                                     "You are Codex, an agent based on GPT-5."))
            self.assertEqual(model["model_messages"], {
                **original,
                "instructions_template": (
                    "You are Codex, an agent powered by DeepSeek V4.1 Flash 1017."
                    + instructions[len(identity):]
                ),
            })

    def test_does_not_mutate_inputs_or_share_nested_state(self):
        source = bundled()
        before = deepcopy(source)
        result = build_catalog(source, native_version=NATIVE_VERSION)
        result["models"][0]["model_messages"]["guardian_v2"].clear()
        self.assertEqual(source, before)

    def test_public_catalog_is_only_the_actual_model(self):
        result = build_catalog(bundled(), native_version=NATIVE_VERSION)["models"]
        self.assertEqual([model["slug"] for model in result if model["visibility"] == "list"], [PINNED_MODEL])
        self.assertEqual([model["slug"] for model in result if model["visibility"] == "hide"],
                         ["gpt-5.6-luna", "codex-auto-review", "gpt-6-luna",
                          "gpt-6.1-sol", "gpt-6.1-terra"])
        self.assertEqual(result[0]["visibility"], "list")
        self.assertEqual(result[0]["service_tiers"], [])
        self.assertNotIn("comp_hash", result[0])
        self.assertNotIn("default_service_tier", result[0])

    def test_auxiliary_identity_is_hidden_and_honest(self):
        source = bundled()
        result = native_models(build_catalog(source, native_version=NATIVE_VERSION))
        for slug in ("gpt-5.6-luna", "codex-auto-review"):
            with self.subTest(slug=slug):
                model = result[slug]
                self.assertEqual(model["visibility"], "hide")
                self.assertIn("DeepSeek", model["display_name"])
                self.assertIn(PINNED_MODEL, model["description"])
                self.assertEqual(model["multi_agent_version"], "v1")
                self.assertNotIn("auto_review_model_override", model)
        self.assertNotIn("auto_review_model_override", result[PINNED_MODEL])

    def test_rejects_unsupported_binary_and_duplicate_source(self):
        for older in ("0.165.0-alpha.1.2", "0.164.0", "0.163.0-alpha.11.1"):
            with self.assertRaisesRegex(CatalogCompatibilityError, "Unqualified native runtime"):
                build_catalog(bundled(), native_version=older)
        source = bundled()
        source["models"].append(source["models"][0])
        with admit_mutated_capture(source), self.assertRaisesRegex(
            CatalogCompatibilityError, "Duplicate bundled model slug"
        ):
            build_catalog(source, native_version=NATIVE_VERSION)

    def test_rejects_changes_to_pinned_capture(self):
        source = bundled()
        native_models(source)["gpt-6.1-sol"]["description"] = "tampered"
        with self.assertRaisesRegex(CatalogCompatibilityError, "differs from the pinned native capture"):
            build_catalog(source, native_version=NATIVE_VERSION)

    def test_output_defers_tool_mode_to_native_features_for_every_alias(self):
        result = build_catalog(bundled(), native_version=NATIVE_VERSION)
        self.assertTrue(all(model["tool_mode"] is None for model in result["models"]))
        source = bundled()
        native_models(source)["gpt-6.1-sol"]["tool_mode"] = None
        with admit_mutated_capture(source), self.assertRaisesRegex(
            CatalogCompatibilityError, "Native runtime selectors differ"
        ):
            build_catalog(source, native_version=NATIVE_VERSION)

    def test_rejects_missing_native_semantics(self):
        for field in ("multi_agent_version", "model_messages", "node_repl_auto_review_required"):
            with self.subTest(field=field):
                source = bundled()
                del native_models(source)["gpt-6.1-sol"][field]
                expected = ("instruction templates are incomplete" if field == "model_messages"
                            else "Native runtime selectors differ")
                with admit_mutated_capture(source), self.assertRaisesRegex(CatalogCompatibilityError, expected):
                    build_catalog(source, native_version=NATIVE_VERSION)
        source = bundled()
        native_models(source)["gpt-6.1-sol"]["guardian"] = {}
        with admit_mutated_capture(source), self.assertRaisesRegex(CatalogCompatibilityError, "empty Guardian"):
            build_catalog(source, native_version=NATIVE_VERSION)

    def test_rejects_unproven_or_duplicate_alias_metadata(self):
        for aliases, error in (
            (("invented-model",), "requires exactly"),
            (("gpt-5.6-luna",), "requires exactly"),
            (("gpt-5.6-luna", "gpt-5.6-luna"), "Duplicate auxiliary alias"),
            ("gpt-5.6-luna", "must be a sequence"),
        ):
            with self.subTest(aliases=aliases), self.assertRaisesRegex(CatalogCompatibilityError, error):
                build_catalog(bundled(), native_version=NATIVE_VERSION, auxiliary_aliases=aliases)
        source = bundled()
        del native_models(source)["codex-auto-review"]["supported_in_api"]
        with admit_mutated_capture(source), self.assertRaisesRegex(
            CatalogCompatibilityError, "Auxiliary alias lacks compatible native model metadata"
        ):
            build_catalog(source, native_version=NATIVE_VERSION)

    def test_reasoning_and_context_are_model_appropriate(self):
        result = build_catalog(bundled(), native_version="codex-cli " + NATIVE_VERSION)["models"][0]
        self.assertEqual(result["context_window"], 1_048_576)
        self.assertEqual(result["auto_compact_token_limit"], AUTO_COMPACT_TOKEN_LIMIT)
        self.assertFalse(result["supports_reasoning_summary_parameter"])
        self.assertFalse(result["support_verbosity"])
        self.assertFalse(result["use_responses_lite"])
        self.assertEqual(result["multi_agent_reasoning_effort"], "high")
        self.assertIn("ultra", [x["effort"] for x in result["supported_reasoning_levels"]])
        self.assertEqual(result["input_modalities"], ["text", "image"])

    def test_missing_or_false_native_search_support_fails_closed(self):
        for value in (None, False, 1):
            with self.subTest(value=value):
                source = bundled()
                template = native_models(source)["gpt-6.1-sol"]
                if value is None:
                    del template["supports_search_tool"]
                else:
                    template["supports_search_tool"] = value
                with admit_mutated_capture(source), self.assertRaisesRegex(
                    CatalogCompatibilityError, "Native runtime selectors differ"
                ):
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
        selected["models"][0]["model_messages"]["guardian_v2"].clear()
        self.assertEqual(source, original)
        self.assertEqual(default["models"][0]["model_messages"]["guardian_v2"],
                         native_models(original)["gpt-6.1-sol"]["model_messages"]["guardian_v2"])

    def test_invalid_native_agent_versions_are_rejected_without_input_mutation(self):
        for version in (None, True, False, 1, [], {}, "", "V1", "v3", "v1 "):
            source = bundled()
            original = deepcopy(source)
            with self.assertRaises(CatalogCompatibilityError, msg=repr(version)):
                build_catalog(source, native_version=NATIVE_VERSION, multi_agent_version=version)
            self.assertEqual(source, original, msg=repr(version))


if __name__ == "__main__":
    unittest.main()
