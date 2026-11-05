"""Loopback-only provider failure propagation; not evidence of historical model errors."""
import asyncio
import copy
import hashlib
import json
import re

from aiohttp import web
import pytest

from codex_deepseek.protocol import MODEL, encode_sse
from test_bridge import HEADERS, KEY, harness, read_events, ImageFiles, image_factory, image_input

ORIGINAL = {"code": "rate_limit_exceeded", "message": "synthetic-original-provider-failure"}
ID = "gen-failure-propagation"


def failed(**updates):
    value = {"id": ID, "status": "failed", "model": "unknown", "provider": "unknown",
             "output": [], "error": dict(ORIGINAL)}
    value.update(updates)
    return {"type": "response.failed", "response": value, "sequence_number": 2}


def created():
    return {"type": "response.created", "response": {"id": ID, "status": "in_progress",
            "model": MODEL}, "sequence_number": 0}


def diagnostic(receipt, original):
    detail = json.loads(receipt["detail"])
    value = detail.get("provider_failure", detail)
    assert value["event_type"] == original["type"]
    assert value["error_code"] == ORIGINAL["code"]
    assert value["hash_basis"] == "canonical_json_utf8"
    expected = hashlib.sha256(json.dumps(original, sort_keys=True, ensure_ascii=False,
                                        separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    assert value["event_json_sha256"] == expected
    assert ORIGINAL["message"] not in receipt["detail"] and KEY not in receipt["detail"]


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["nonstream", "failed_first", "created_failed"])
@pytest.mark.parametrize("redact", [False, True])
async def test_failed_placeholder_preserves_failure_and_response_id(tmp_path, mode, redact):
    event = failed()
    if redact:
        event["response"]["error"]["message"] += " " + KEY
    expected = copy.deepcopy(event["response"])
    expected["error"]["message"] = expected["error"]["message"].replace(KEY, "[REDACTED]")
    calls = []
    async def upstream(request):
        calls.append(await request.json())
        if mode == "nonstream":
            return web.json_response(event["response"])
        frames = [created(), event] if mode == "created_failed" else [event]
        return web.Response(body=b"".join(encode_sse(x) for x in frames), content_type="text/event-stream")
    async with harness(tmp_path, upstream) as (client, state):
        reply = await client.post("/v1/responses", headers=HEADERS,
                                  json={"model": MODEL, "stream": mode != "nonstream"})
        text = await reply.text()
        result = json.loads(text) if mode == "nonstream" else read_events(text)[-1]["response"]
        assert reply.status == 200 and result == expected
        assert KEY not in text and len(calls) == 1
        receipt = state.list_receipts()[0]
        assert receipt["status"] == "failed" and receipt["response_id"] == ID
        diagnostic(receipt, {"type": "response.failed", "response": event["response"]} if mode == "nonstream" else event)
        assert state.db.execute("SELECT COUNT(*) FROM completed_native_evidence").fetchone()[0] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_unknown_success_stays_rejected_but_id_is_recorded(tmp_path, stream):
    result = failed()["response"]
    result.update(status="completed", error=None)
    async def upstream(request):
        await request.read()
        if not stream:
            return web.json_response(result)
        return web.Response(body=encode_sse({"type": "response.completed", "response": result}),
                            content_type="text/event-stream")
    async with harness(tmp_path, upstream) as (client, state):
        reply = await client.post("/v1/responses", headers=HEADERS, json={"model": MODEL, "stream": stream})
        text = await reply.text()
        assert reply.status == (200 if stream else 502)
        assert "response.completed" not in text
        receipt = state.list_receipts()[0]
        assert receipt["status"] == "adapter_error" and receipt["response_id"] == ID
        assert state.db.execute("SELECT COUNT(*) FROM completed_native_evidence").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_failed_snapshot_does_not_reemit_or_require_completed_tool_output(tmp_path):
    tool = {"type": "function_call", "id": "fc-already-done", "call_id": "call-already-done",
            "name": "ping", "arguments": "{}", "status": "completed"}
    event = failed()
    async def upstream(request):
        await request.read()
        frames = [created(), {"type": "response.output_item.done", "output_index": 0, "item": tool}, event]
        return web.Response(body=b"".join(encode_sse(x) for x in frames), content_type="text/event-stream")
    async with harness(tmp_path, upstream) as (client, state):
        reply = await client.post("/v1/responses", headers=HEADERS, json={"model": MODEL, "stream": True,
            "tools": [{"type": "function", "name": "ping", "parameters": {"type": "object"}}]})
        frames = read_events(await reply.text())
        assert sum(x["type"] == "response.output_item.done" for x in frames) == 1
        assert frames[-1]["response"]["error"] == ORIGINAL
        assert state.list_receipts()[0]["status"] == "failed"
        assert state.db.execute("SELECT COUNT(*) FROM completed_native_evidence").fetchone()[0] == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("with_images", [False, True])
@pytest.mark.parametrize("with_id", [False, True])
async def test_standard_error_has_no_fake_id_and_keeps_image_consumption_unknown(tmp_path, with_images, with_id):
    event = {"type": "error", "code": ORIGINAL["code"], "message": ORIGINAL["message"],
             "param": None, "sequence_number": 1}
    if with_id:
        event["response"] = {"id": ID, "model": "unknown"}
    backend = ImageFiles()
    factory, made = image_factory(tmp_path, backend)
    async def upstream(request):
        await request.read()
        return web.Response(body=encode_sse(event), content_type="text/event-stream")
    async with harness(tmp_path, upstream, image_transport_factory=factory if with_images else None) as (client, state):
        body = image_input(stream=True) if with_images else {"model": MODEL, "stream": True}
        reply = await client.post("/v1/responses", json=body, headers=HEADERS)
        frames = read_events(await reply.text())
        assert len(frames) == 1 and frames[0]["type"] == "response.failed"
        value = frames[0]["response"]
        assert value["status"] == "failed" and value["error"]["code"] == ORIGINAL["code"]
        assert value["error"]["message"] == ORIGINAL["message"]
        assert value.get("id") == (ID if with_id else None)
        receipt = state.list_receipts()[0]
        assert receipt["status"] == "failed" and receipt["response_id"] == (ID if with_id else None)
        diagnostic(receipt, event)
        if with_images:
            diag = await made[0].diagnostics()
            assert diag["active_transport_pins"] == 1 and len(diag["unknown_upstream_calls"]) == 1
            assert receipt["adaptations"]["image_transport"]["consumption_status"] == "unknown"
            assert not backend.deletes


@pytest.mark.asyncio
async def test_error_is_delivered_without_waiting_for_upstream_eof(tmp_path):
    closed = asyncio.Event()
    async def upstream(request):
        await request.read()
        reply = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await reply.prepare(request)
        await reply.write(encode_sse({"type": "error", "code": ORIGINAL["code"],
                                     "message": ORIGINAL["message"], "param": None, "sequence_number": 0}))
        try:
            await asyncio.Event().wait()
        finally:
            closed.set()
    async with harness(tmp_path, upstream) as (client, state):
        reply = await client.post("/v1/responses", json={"model": MODEL, "stream": True}, headers=HEADERS)
        text = await asyncio.wait_for(reply.text(), 1)
        assert read_events(text)[-1]["response"]["error"]["message"] == ORIGINAL["message"]
        await asyncio.wait_for(closed.wait(), 1)
        assert state.list_receipts()[0]["status"] == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["status", "missing_id", "output", "model_shape", "changed_id"])
async def test_malformed_failed_is_not_accepted_as_image_terminal(tmp_path, damage):
    event = failed()
    if damage == "status": event["response"]["status"] = "completed"
    elif damage == "missing_id": event["response"].pop("id")
    elif damage == "output": event["response"]["output"] = {}
    elif damage == "model_shape": event["response"]["model"] = 7
    elif damage == "changed_id": event["response"]["id"] = "gen-unrelated"
    backend = ImageFiles()
    factory, made = image_factory(tmp_path, backend)
    async def upstream(request):
        await request.read()
        frames = [created(), event]
        return web.Response(body=b"".join(encode_sse(x) for x in frames), content_type="text/event-stream")
    async with harness(tmp_path, upstream, image_transport_factory=factory) as (client, state):
        reply = await client.post("/v1/responses", json=image_input(stream=True), headers=HEADERS)
        frames = read_events(await reply.text())
        assert frames[-1]["response"]["error"]["code"] == "provider_compatibility"
        receipt = state.list_receipts()[0]
        assert receipt["status"] == "adapter_error"
        assert (await made[0].diagnostics())["active_transport_pins"] == 1
        assert not backend.deletes


@pytest.mark.asyncio
async def test_genuine_failed_terminal_releases_image_lease(tmp_path):
    event = failed()
    backend = ImageFiles()
    factory, made = image_factory(tmp_path, backend)
    async def upstream(request):
        await request.read()
        return web.Response(body=encode_sse(event), content_type="text/event-stream")
    async with harness(tmp_path, upstream, image_transport_factory=factory) as (client, state):
        reply = await client.post("/v1/responses", json=image_input(stream=True), headers=HEADERS)
        assert read_events(await reply.text())[-1]["response"]["error"] == ORIGINAL
        receipt = state.list_receipts()[0]
        assert receipt["status"] == "failed"
        assert receipt["adaptations"]["image_transport"]["provider_terminal"] == "failed"
        assert (await made[0].diagnostics())["active_transport_pins"] == 0
        assert (await made[0].cleanup(all_unpinned=True))["all_owned_files_deleted"]
