import copy
import importlib.util
import json
from pathlib import Path
import unittest
from unittest.mock import patch

_SPEC = importlib.util.spec_from_file_location(
    "_payload_fingerprint",
    Path(__file__).parent / "fixtures" / "app_faults" / "payload_fingerprint.py",
)
assert _SPEC is not None and _SPEC.loader is not None
fp = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(fp)


class PayloadFingerprintTests(unittest.TestCase):
    KEY = b"ephemeral-test-key-never-persist!!"

    def observe(self, raw):
        result = fp.fingerprint(raw, self.KEY)
        self.assertEqual(result["status"], "comparable")
        self.assertEqual(result["node_count"], len(result["nodes"]))
        return result

    @staticmethod
    def encode(value, **kwargs):
        return json.dumps(value, ensure_ascii=False, **kwargs).encode("utf-8")

    @staticmethod
    def base():
        return {
            "model": "fixed-model",
            "input": [
                {"role": "user", "content": "earlier",
                 "future_field": {"keep": "yes"}},
                {"role": "user", "content": "last prompt"},
            ],
            "tools": [{
                "type": "function", "name": "example",
                "parameters": {
                    "type": "object",
                    "properties": {"x": {"type": "string"}},
                    "required": ["x"],
                },
            }],
            "client_metadata": {"root": "same", "turn": "same"},
        }

    def test_object_order_and_whitespace_only(self):
        def reverse_keys(value):
            if isinstance(value, dict):
                return {name: reverse_keys(value[name])
                        for name in reversed(list(value))}
            if isinstance(value, list):
                return [reverse_keys(item) for item in value]
            return value

        value = self.base()
        first = self.encode(value, separators=(",", ":"))
        second = self.encode(reverse_keys(value), indent=3)
        self.assertNotEqual(first, second)
        self.assertEqual(self.observe(first), self.observe(second))

    def test_earlier_history_loss_not_hidden_by_last_prompt_or_tools(self):
        before = self.base()
        after = copy.deepcopy(before)
        del after["input"][0]["future_field"]
        self.assertEqual(before["input"][-1], after["input"][-1])
        self.assertEqual(before["tools"], after["tools"])
        left, right = self.observe(self.encode(before)), self.observe(self.encode(after))
        self.assertNotEqual(left["root"], right["root"])
        self.assertGreater(left["node_count"], right["node_count"])
        self.assertTrue(set(left["nodes"]) - set(right["nodes"]))

    def test_unknown_and_nested_schema_fields_are_covered(self):
        original = self.base()
        expected = self.observe(self.encode(original))["root"]
        missing = copy.deepcopy(original)
        del missing["tools"][0]["parameters"]["required"]
        added = copy.deepcopy(original)
        added["new_unknown_field"] = {"enabled": True}
        for changed in (missing, added):
            self.assertNotEqual(expected, self.observe(self.encode(changed))["root"])

    def test_array_order_and_equal_length_strings_are_detected(self):
        original = self.base()
        expected = self.observe(self.encode(original))["root"]
        reordered = copy.deepcopy(original)
        reordered["input"].reverse()
        edited = copy.deepcopy(original)
        edited["input"][0]["content"] = "EARLIER"
        self.assertEqual(len(self.encode(original)), len(self.encode(edited)))
        for changed in (reordered, edited):
            self.assertNotEqual(expected, self.observe(self.encode(changed))["root"])

    def test_tool_array_order_is_preserved(self):
        value = self.base()
        value["tools"].append({"type": "function", "name": "other"})
        changed = copy.deepcopy(value)
        changed["tools"].reverse()
        self.assertNotEqual(self.observe(self.encode(value))["root"],
                            self.observe(self.encode(changed))["root"])

    def test_types_missing_null_unicode_and_number_lexemes(self):
        pairs = [
            (b'{"x":false}', b'{"x":0}'),
            (b'{}', b'{"x":null}'),
            ('{"x":"\u00e9"}'.encode(), '{"x":"e\u0301"}'.encode()),
            (b'{"x":1}', b'{"x":1.0}'),
            (b'{"x":0}', b'{"x":-0}'),
            (b'{"x":"1"}', b'{"x":1}'),
            (b'{"x":"a\\r\\nb"}', b'{"x":"a\\nb"}'),
        ]
        for left, right in pairs:
            with self.subTest(left=left):
                self.assertNotEqual(self.observe(left)["root"],
                                    self.observe(right)["root"])

    def test_json_inside_a_string_is_never_parsed(self):
        left = self.encode({"opaque": '{"a":1,"b":2}'})
        right = self.encode({"opaque": '{"b":2,"a":1}'})
        self.assertNotEqual(self.observe(left)["root"], self.observe(right)["root"])

    def test_rejects_duplicates_nonfinite_invalid_unicode(self):
        cases = [
            b'{"private-name":1,"private-name":2}',
            b'{"x":{"same":1,"same":2}}',
            b'{"x":1,"\\u0078":2}',
            b'{"x":NaN}', b'{"x":Infinity}', b'{"x":-Infinity}',
            b'{"x":"\xff"}', b'{"x":"\\ud800"}', b'{"private-name":[}',
        ]
        for raw in cases:
            with self.subTest(raw=raw):
                result = fp.fingerprint(raw, self.KEY)
                self.assertEqual(result["status"], "not_comparable")
                self.assertIsNone(result["node_count"])
                self.assertNotIn("root", result)
                self.assertNotIn("nodes", result)
                self.assertNotIn("private-name", json.dumps(result))

    def test_limits_reject_without_partial_fingerprints(self):
        for name, limit, raw in [
            ("MAX_BYTES", 1, b"{}"),
            ("MAX_DEPTH", 1, b'{"a":{"b":0}}'),
            ("MAX_NODES", 2, b"[0,1]"),
        ]:
            with self.subTest(name=name), patch.object(fp, name, limit):
                result = fp.fingerprint(raw, self.KEY)
                self.assertEqual(result["status"], "not_comparable")
                self.assertIsNone(result["node_count"])
                self.assertNotIn("nodes", result)

    def test_all_nodes_and_no_plaintext_or_key(self):
        self.assertEqual(self.observe(b'{"a":1,"b":[true,null]}')["node_count"], 5)
        raw = self.encode({"private-field-name-791": "private-value-582",
                           "other": ["private-value-583"]})
        result = self.observe(raw)
        serialized = json.dumps(result)
        for secret in ("private-field-name-791", "private-value-582",
                       "private-value-583", self.KEY.decode("ascii"), self.KEY.hex()):
            self.assertNotIn(secret, serialized)
        for path_id in result["nodes"]:
            self.assertEqual(len(path_id), 64)
            int(path_id, 16)
        other = fp.fingerprint(raw, b"Z" * 32)
        self.assertEqual(other["status"], "comparable")
        self.assertNotEqual(result["root"], other["root"])
        self.assertTrue(set(result["nodes"]).isdisjoint(other["nodes"]))

    def test_large_numbers_bypass_float_and_integer_conversion(self):
        self.assertEqual(self.observe(b'{"n":' + b"1" * 5000 + b"}")["node_count"], 2)
        self.observe(b'{"n":1e999999}')

    def test_json_pointer_escaping_has_no_node_collisions(self):
        raw = self.encode({"a/b": {"~1": 0}, "a": {"b": 1}, "": [0, {"0": 2}]})
        self.observe(raw)

    def test_invalid_API_inputs_fail_closed(self):
        for raw, key in ((bytearray(b"{}"), self.KEY), (b"{}", b"short")):
            result = fp.fingerprint(raw, key)
            self.assertEqual(result["status"], "not_comparable")
            self.assertNotIn("root", result)


if __name__ == "__main__":
    unittest.main()
