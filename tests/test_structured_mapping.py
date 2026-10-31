"""HTTP integration for native schema consumption; not App GUI acceptance."""
import copy
import hashlib
import json
import re

from aiohttp import web
import pytest

from codex_deepseek.protocol import MODEL, encode_sse
from test_bridge import HEADERS, harness, message, read_events, terminal


CASES = [
    ({"type": "object", "properties": {"count": {"type": "integer"}},
      "required": ["count"], "additionalProperties": False}, {"count": 1}, {"count": "bad"}, "json_object"),
    ({"type": "array", "items": {"type": "integer"}, "minItems": 1}, [1], ["bad"], "json_object"),
    ({"type": "string", "pattern": "^OWNED_[a-z]+$"}, "OWNED_nonce", 9, "text"),
]


def digest(value):
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False,
                     allow_nan=False, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("valid", [False, True])
@pytest.mark.parametrize("schema,good,bad,wire_format", CASES)
async def test_schema_auto_tools_mapping_preserves_call_history_and_rejects_bad_final(
    tmp_path, stream, valid, schema, good, bad, wire_format
):
    schema = {**schema, "description": "SCHEMA_PRIVATE_SENTINEL"}
    requests = []
    call = {"type": "function_call", "id": "fc_nonce", "call_id": "call_nonce",
            "name": "get_nonce", "arguments": "{}", "status": "completed"}
    final = message(json.dumps(good if valid else bad))
    # Null phase is valid native protocol; do not require final_answer.
    final["phase"] = None

    async def handler(request):
        payload = await request.json()
        requests.append(payload)
        assert payload["tool_choice"] == "auto"
        assert payload["text"]["format"] == {"type": wire_format}
        assert payload["provider"] == {
            "only": ["deepseek"], "allow_fallbacks": False, "require_parameters": True}
        assert json.dumps(schema, sort_keys=True, ensure_ascii=False,
                          allow_nan=False, separators=(",", ":")) in payload["instructions"]
        assert len(payload["tools"]) == 1 and payload["tools"][0]["name"] == "get_nonce"
        output = [call] if len(requests) == 1 else [final]
        if len(requests) == 2:
            retained = payload["input"]
            assert retained[-2]["type"] == "function_call"
            assert retained[-2]["call_id"] == "call_nonce"
            assert retained[-1] == {"type": "function_call_output", "call_id": "call_nonce",
                                    "output": "owned-real-tool-result"}
        if not stream:
            return web.json_response(terminal(output)["response"])
        frames = [{"type": "response.created", "response": {"id": "resp_one"}},
                  {"type": "response.output_item.added", "output_index": 0,
                   "item": {**output[0], "status": "in_progress"}},
                  {"type": "response.output_item.done", "output_index": 0, "item": output[0]},
                  terminal(output)]
        return web.Response(body=b"".join(encode_sse(x) for x in frames),
                            content_type="text/event-stream")

    original = {"model": MODEL, "stream": stream, "tool_choice": "auto",
                "input": [{"role": "user", "content": [{"type": "input_text", "text": "Read the owned nonce."}]}],
                "tools": [{"type": "function", "name": "get_nonce",
                           "parameters": {"type": "object", "properties": {},
                                          "additionalProperties": False}}],
                "text": {"format": {"type": "json_schema", "name": "FORMAT_PRIVATE_SENTINEL",
                                    "strict": True, "schema": schema}}}
    original_copy = copy.deepcopy(original)
    async with harness(tmp_path, handler) as (client, state):
        first = await client.post("/v1/responses", json=original, headers=HEADERS)
        assert first.status == 200
        if stream:
            frames = read_events(await first.text())
            assert frames[-1]["type"] == "response.completed"
            delivered = [x["item"] for x in frames if x["type"] == "response.output_item.done"]
        else:
            result = await first.json()
            assert result["status"] == "completed"
            delivered = result["output"]
        assert delivered == [call]
        followup = copy.deepcopy(original)
        followup["input"] += [delivered[0], {"type": "function_call_output",
                                          "call_id": "call_nonce", "output": "owned-real-tool-result"}]
        second = await client.post("/v1/responses", json=followup, headers=HEADERS)
        assert second.status == (200 if stream or valid else 502)
        if stream:
            frames = read_events(await second.text())
            if valid:
                assert frames[-1]["type"] == "response.completed"
                delivered_final = [x["item"] for x in frames if x["type"] == "response.output_item.done"]
                assert delivered_final == [final]
                assert json.loads(delivered_final[0]["content"][0]["text"]) == good
            else:
                assert frames[-1]["type"] == "response.failed"
                assert not any(x["type"] in {"response.completed", "response.output_item.done"}
                               for x in frames)
        else:
            result = await second.json()
            if valid:
                assert result["status"] == "completed" and result["output"] == [final]
                assert json.loads(result["output"][0]["content"][0]["text"]) == good
            else:
                assert result["error"]["code"] == "provider_compatibility"
                assert result["error"]["type"] == "api_error"
                assert result.get("status") != "completed"
        receipts = state.list_receipts()
        assert len(requests) == len(receipts) == 2
        by_id = {row["id"]: row for row in receipts}
        for response in (first, second):
            receipt = by_id[response.headers["X-Gateway-Request-Id"]]
            mapping = receipt["adaptations"]["structured_output_mapping"]
            assert mapping["requested_format_sha256"] == digest(original["text"]["format"])
            assert mapping["requested_schema_sha256"] == digest(schema)
            assert mapping["requested_strict"] is True
            assert mapping["upstream_format"] == wire_format
            assert mapping["upstream_decoder_constraint"] == ("json_syntax_only" if wire_format == "json_object" else False)
            assert mapping["tool_choice"] == "auto_preserved"
            assert set(mapping) == {
                "schema", "strategy", "requested_format_sha256", "requested_schema_sha256",
                "requested_strict", "schema_root_strategy", "final_schema_validator",
                "final_schema_failure", "tool_choice", "schema_instruction_sha256",
                "upstream_format", "upstream_decoder_constraint", "route_format_strategy"}
            assert mapping["schema"] == 1
            assert mapping["strategy"] == "auto_tools_final_schema_validation"
            assert mapping["final_schema_validator"] == "codex_deepseek.StructuredOutput_on_original_request"
            assert mapping["final_schema_failure"] == "adapter_error_no_completed_final"
            assert mapping["route_format_strategy"] == (
                "official_container_json_mode" if wire_format == "json_object" else "other_schema_root_text")
            assert mapping["schema_root_strategy"] == (
                "explicit_object_or_array" if wire_format == "json_object" else "other_schema_root")
            for field in ("requested_format_sha256", "requested_schema_sha256", "schema_instruction_sha256"):
                assert re.fullmatch("[0-9a-f]{64}", mapping[field])
            assert "SCHEMA_PRIVATE_SENTINEL" not in json.dumps(mapping)
            assert "FORMAT_PRIVATE_SENTINEL" not in json.dumps(mapping)
        assert by_id[first.headers["X-Gateway-Request-Id"]]["status"] == "completed"
        assert by_id[second.headers["X-Gateway-Request-Id"]]["status"] == ("completed" if valid else "adapter_error")
    assert original == original_copy
