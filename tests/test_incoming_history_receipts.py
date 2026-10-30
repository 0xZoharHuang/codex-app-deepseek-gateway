"""Incoming native receipt observations must preserve HTTP outcomes and durable evidence."""
import json

import pytest
from aiohttp import web

from codex_deepseek.protocol import MODEL
from codex_app_deepseek.protocol_state import ProtocolState
from test_bridge import HEADERS, KEY, harness, terminal



def test_incoming_history_receipt_excludes_ambiguous_invalid_and_unclosed_pairs():
    import hashlib
    from codex_app_deepseek.protocol_state import incoming_native_history_metadata

    def pair(call_id, custom=False):
        kind = "custom_tool_call" if custom else "function_call"
        call = {"type": kind, "call_id": call_id, "name": "PRIVATE_TOOL_NAME",
                **({"input": "FINAL \u4e2d\u6587\n\"PRIVATE_INPUT\""} if custom else
                   {"arguments": '{ "label" : "PRIVATE_ARGUMENTS" }'})}
        return [call, {"type": kind + "_output", "call_id": call_id, "output": "PRIVATE_OUTPUT"}]

    final_call, final_output = pair("valid-private-call", custom=True)
    call, output = pair("duplicate-call")
    dup_call = [call, dict(call), output]
    call, output = pair("duplicate-output")
    dup_output = [call, output, dict(output)]
    cross_type = [*pair("cross-type"), *pair("cross-type", custom=True)]
    reversed_pair = list(reversed(pair("out-before-call")))
    mismatched = pair("mismatched")
    mismatched[1]["type"] = "custom_tool_call_output"
    unfinished = pair("unfinished")
    unfinished[0]["status"] = "in_progress"
    invalid_field = pair("invalid-field")
    invalid_field[0]["arguments"] = None
    invalid_encoding = pair("invalid-encoding")
    invalid_encoding[0]["arguments"] = chr(0xd800)
    missing_id = pair(None)[0]
    unclosed = pair("no-output")[0]
    items = [final_call, final_output, *dup_call, *dup_output, *cross_type,
             *reversed_pair, *mismatched, *unfinished, *invalid_field,
             *invalid_encoding, missing_id, unclosed]
    before = json.dumps(items, ensure_ascii=True, sort_keys=True)
    observed = incoming_native_history_metadata(items, [""])
    assert observed["count"] == 1 and observed["candidate_count"] == 2
    assert observed["ambiguous"] == 3 and observed["invalid"] == 6 and observed["unclosed"] == 1
    assert observed["omitted"] == 0
    assert observed["thread_header"] == {"count": 1, "status": "invalid", "sha256": None}
    # Independent oracle: hash the entire original FINAL items, including exact string spacing.
    def expected(item):
        canonical = json.dumps(item, ensure_ascii=False, sort_keys=True,
                               separators=(",", ":"), allow_nan=False).encode("utf-8")
        return hashlib.sha256(canonical).hexdigest()
    assert observed["records"] == [{
        "call_id_sha256": hashlib.sha256(b"valid-private-call").hexdigest(),
        "call_type": "custom_tool_call", "call_index": 0, "output_index": 1,
        "call_sha256": expected(final_call), "output_sha256": expected(final_output)}]
    assert json.dumps(items, ensure_ascii=True, sort_keys=True) == before
    serialized = json.dumps(observed, ensure_ascii=True)
    for raw in ["PRIVATE_TOOL_NAME", "PRIVATE_INPUT", "PRIVATE_OUTPUT", "PRIVATE_ARGUMENTS",
                "valid-private-call", "duplicate-call", "cross-type", "invalid-encoding"]:
        assert raw not in serialized


def test_incoming_history_receipt_survives_cold_owner_loss_and_adaptation_merges(tmp_path):
    import copy
    from types import SimpleNamespace
    from codex_app_deepseek.protocol_state import incoming_native_history_metadata

    path = tmp_path / "history.sqlite"
    native = [{"type": "function_call", "name": "sample", "call_id": "private-history-id",
               "arguments": '{"label":"PRIVATE_HISTORY_ARGUMENT"}'},
              {"type": "function_call_output", "call_id": "private-history-id",
               "output": "PRIVATE_HISTORY_OUTPUT"}]
    metadata = incoming_native_history_metadata(native, ["private-native-thread"])
    original_metadata = copy.deepcopy(metadata)
    def begin(state):
        return state.start(requested_model="guardian", requested_upstream_model=MODEL,
                           requested_provider="deepseek", route_reason="official_default",
                           adaptations={"existing_control": {"kept": True},
                                        "incoming_native_history": metadata})
    with ProtocolState(path) as state:
        crashed = begin(state)
        owner = state.instance_id
        stored_before = state.db.execute("SELECT adaptations FROM receipts WHERE id=?", (crashed,)).fetchone()[0]
        # Same simulation as the existing owner-loss test: release only test DB resources.
        state.db.close()
        state._release_lock()
        state._closed = True
    with ProtocolState(path) as state:
        receipt = state.receipt(crashed)
        assert receipt["status"] == "owner_lost" and state.instance_id != owner
        assert receipt["adaptations"]["incoming_native_history"] == original_metadata
        assert state.db.execute("SELECT adaptations FROM receipts WHERE id=?", (crashed,)).fetchone()[0] == stored_before
        assert state.db.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 1
        assert state.db.execute("SELECT COUNT(*) FROM completed_native_evidence").fetchone()[0] == 0
        merged = begin(state)
        state.image_transport(merged, preparation={"after_bytes": 17}, consumption_status="preparing")
        diagnostic = {"total_count": 1, "truncated": False, "records": [{
            "reason": "producer_append_constraint_violation", "item_sha256": "1" * 64,
            "call_sha256": "2" * 64, "preview_arguments_sha256": "3" * 64,
            "candidate_arguments_sha256": "4" * 64, "hash_encoding": "utf8-surrogatepass"}]}
        events = SimpleNamespace(terminal=True, status="completed", done={}, response_id="resp_history",
                                 usage={}, diagnostic_summary=lambda: copy.deepcopy(diagnostic))
        assert state.finish(merged, "completed", 200, events)
        state.image_transport(merged, provider_terminal="completed", consumption_status="provider_terminal")
        expected = {"existing_control": {"kept": True}, "incoming_native_history": original_metadata,
                    "tool_argument_diagnostics": diagnostic,
                    "image_transport": {"preparation": {"after_bytes": 17},
                                        "consumption_status": "provider_terminal", "provider_terminal": "completed"}}
        assert state.receipt(merged)["adaptations"] == expected
        state.delivery(merged, "delivered")
        assert not state.finish(merged, "cancelled")
        assert state.receipt(merged)["adaptations"] == expected
        assert state.db.execute("PRAGMA user_version").fetchone()[0] == 3
        orderly = begin(state)
    with ProtocolState(path) as reopened:
        assert reopened.receipt(merged)["adaptations"] == expected
        assert reopened.receipt(orderly)["status"] == "owner_closed"
        assert reopened.receipt(orderly)["adaptations"]["incoming_native_history"] == original_metadata
        stored = [row[0] for row in reopened.db.execute("SELECT adaptations FROM receipts")]
        for raw in ["PRIVATE_HISTORY_ARGUMENT", "PRIVATE_HISTORY_OUTPUT", "private-history-id", "private-native-thread"]:
            assert all(raw not in value for value in stored)



@pytest.mark.asyncio
async def test_incoming_history_receipt_hashes_original_last_eight_without_changing_http(tmp_path):
    import copy
    import hashlib
    from codex_deepseek.protocol import prepare

    history = []
    for index in range(10):
        custom = index % 2 == 1
        kind = "custom_tool_call" if custom else "function_call"
        history.extend([{"type": kind, "id": "private-item-" + str(index),
                         "call_id": "private-call-" + str(index),
                         "name": "patcher" if custom else "plain", "status": "completed",
                         **({"input": "FINAL \u4e2d\u6587\n\"PRIVATE_INPUT_" + str(index) + "\""} if custom else
                            {"arguments": '{ "label" : "PRIVATE_ARGUMENT_' + str(index) + '" }'})},
                        {"type": kind + "_output", "call_id": "private-call-" + str(index),
                         "output": "PRIVATE_OUTPUT_" + str(index)}])
    # Calls are interleaved by type, but output closure order runs backwards.
    calls, outputs = history[::2], history[1::2]
    history = [*calls, *reversed(outputs)]
    body = {"model": "guardian", "stream": False, "input": history,
            "tools": [{"type": "function", "name": "plain", "parameters": {"type": "object"}},
                      {"type": "custom", "name": "patcher"}]}
    original = copy.deepcopy(body)
    expected_raw = None
    captured = []
    async def handler(request):
        raw = await request.read()
        assert raw == expected_raw
        assert request.headers["Authorization"] == "Bearer " + KEY
        captured.append(raw)
        return web.json_response(terminal()["response"])
    def canonical_sha(item):
        raw = json.dumps(item, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()
    expected_records = [{"call_id_sha256": hashlib.sha256(calls[i]["call_id"].encode()).hexdigest(),
                         "call_type": calls[i]["type"], "call_index": i, "output_index": 19 - i,
                         "call_sha256": canonical_sha(calls[i]), "output_sha256": canonical_sha(outputs[i])}
                        for i in range(7, -1, -1)]
    async with harness(tmp_path, handler) as (client, state):
        baseline, _ = prepare({**copy.deepcopy(body), "model": MODEL}, state.evidence_view(history))
        expected_raw = json.dumps(baseline, allow_nan=False).encode("utf-8")
        for values in [["private-native-thread"], ["same-private-thread", "same-private-thread"]]:
            headers = [*HEADERS.items(), *(("thread-id", value) for value in values)]
            response = await client.post("/v1/responses", json=body, headers=headers)
            assert response.status == 200 and (await response.json())["status"] == "completed"
            receipt = state.receipt(response.headers["X-Gateway-Request-ID"])
            detail = receipt["adaptations"]["incoming_native_history"]
            assert detail["count"] == 8 and detail["candidate_count"] == 10 and detail["omitted"] == 2
            assert detail["ambiguous"] == detail["invalid"] == detail["unclosed"] == 0
            assert detail["records"] == expected_records
            assert detail["thread_header"] == (
                {"count": 1, "status": "unique", "sha256": hashlib.sha256(values[0].encode()).hexdigest()}
                if len(values) == 1 else {"count": 2, "status": "ambiguous", "sha256": None})
            stored = state.db.execute("SELECT adaptations FROM receipts WHERE id=?", (receipt["id"],)).fetchone()[0]
            for raw in ["PRIVATE_INPUT", "PRIVATE_ARGUMENT", "PRIVATE_OUTPUT", *values, "private-call-"]:
                assert raw not in stored
        text_body = {"model": "guardian", "input": "PRIVATE_RAW_STRING"}
        baseline, _ = prepare({**text_body, "model": MODEL}, state.evidence_view(None))
        expected_raw = json.dumps(baseline, allow_nan=False).encode("utf-8")
        response = await client.post("/v1/responses", json=text_body, headers=HEADERS)
        assert response.status == 200
        await response.read()
        detail = state.receipt(response.headers["X-Gateway-Request-ID"])["adaptations"]["incoming_native_history"]
        assert detail["input_is_list"] is False and detail["count"] == detail["candidate_count"] == 0
        assert detail["records"] == [] and detail["thread_header"] == {"count": 0, "status": "missing", "sha256": None}
        assert "PRIVATE_RAW_STRING" not in json.dumps(detail)
        assert len(captured) == 3 and body == original


@pytest.mark.asyncio
@pytest.mark.parametrize("error_type", [RuntimeError, TypeError])
@pytest.mark.parametrize("upstream_status", [200, 429])
async def test_incoming_history_observer_failure_preserves_http_and_hides_message(
        tmp_path, monkeypatch, caplog, error_type, upstream_status):
    import codex_app_deepseek.bridge as bridge_module

    body = {"model": "guardian", "input": "PRIVATE_REQUEST_CONTENT", "stream": False}
    captures = []
    async def handler(request):
        captures.append((await request.read(), request.headers["Authorization"]))
        if upstream_status == 200:
            return web.json_response(terminal()["response"])
        return web.json_response({"error": {"message": "fixed synthetic rejection"}}, status=429,
                                 headers={"Retry-After": "7"})
    sensitive = "PRIVATE_OBSERVER_MESSAGE PRIVATE_REQUEST_CONTENT PRIVATE_OUTPUT_CONTENT"
    def broken(*args, **kwargs):
        raise error_type(sensitive)
    async with harness(tmp_path, handler) as (client, state):
        baseline = await client.post("/v1/responses", json=body, headers=HEADERS)
        baseline_raw = await baseline.read()
        monkeypatch.setattr(bridge_module, "incoming_native_history_metadata", broken)
        response = await client.post("/v1/responses", json=body, headers=HEADERS)
        assert response.status == baseline.status == upstream_status
        assert await response.read() == baseline_raw
        assert response.headers.get("Retry-After") == baseline.headers.get("Retry-After")
        assert len(captures) == 2 and captures[0] == captures[1]
        assert captures[1][1] == "Bearer " + KEY
        receipt = state.receipt(response.headers["X-Gateway-Request-ID"])
        assert receipt["status"] == ("completed" if upstream_status == 200 else "upstream_error")
        assert receipt["adaptations"]["incoming_native_history"] == {
            "schema": 1, "status": "unavailable", "error_type": error_type.__name__}
        serialized = json.dumps(receipt)
        for raw in [sensitive, "PRIVATE_REQUEST_CONTENT", "PRIVATE_OUTPUT_CONTENT", "Traceback"]:
            assert raw not in serialized and raw.encode() not in baseline_raw and raw not in caplog.text
