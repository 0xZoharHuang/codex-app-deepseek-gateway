"""Actual CADSG HTTP bridge against a synthetic loopback Responses producer."""
import copy
import json
import sqlite3

from aiohttp import web
import pytest

from codex_app_deepseek import protocol_state
from codex_deepseek.protocol import MODEL, STRUCTURED_PROVIDER
from test_bridge import HEADERS, harness, message, read_events, terminal


def opaque():
    return {
        "type": "reasoning", "id": "rs_fixture", "summary": [],
        "encrypted_content": "SYNTHETIC_REASONING_CIPHERTEXT",
        "status": "completed", "vendor_reasoning_envelope": {"format": "opaque"},
    }


def native_opaque():
    item = opaque()
    item.pop("status")
    item.pop("vendor_reasoning_envelope")
    return item


def original(stream=True):
    return {"model": MODEL, "stream": stream,
            "input": [{"type": "message", "role": "user", "content": "first"}]}


def continue_with(item):
    return {**original(), "reasoning": {"context": "all_turns"}, "input": [
        {"type": "message", "role": "user", "content": "first"}, item,
        {"type": "message", "role": "user", "content": "continue"},
    ]}


def opaque_records(store):
    return store.db.execute(
        "SELECT key, value FROM adapter_metadata WHERE key LIKE 'opaque-reasoning-v1:%'"
    ).fetchall()


def opaque_sse(*, terminal_only=False, late=False, failed=False):
    item = opaque()
    output = [item, message()]
    done = [{"type": "response.output_item.done", "output_index": index, "item": entry}
            for index, entry in enumerate(output)]
    completed = terminal(output, "failed" if failed else "completed")
    if failed:
        completed["response"]["error"] = {"code": "fixture_failure", "message": "fixture failed"}
    events = [completed] if terminal_only else [*done, completed]
    if late:
        events.append({"type": "response.output_text.delta", "item_id": "msg_one", "delta": "late"})
    return "".join("data: " + json.dumps(event) + "\n\n" for event in events).encode()


async def posted(client, payload):
    response = await client.post("/v1/responses", json=payload, headers=HEADERS)
    return response.status, await response.text()


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [True, False])
async def test_verified_opaque_replays_exact_producer_after_cold_restart(tmp_path, stream):
    requests = []

    async def handler(request):
        requests.append(await request.json())
        if len(requests) == 1:
            if stream:
                return web.Response(body=opaque_sse(), content_type="text/event-stream")
            return web.json_response(terminal([opaque(), message()])["response"])
        events = [{"type": "response.output_item.done", "output_index": 0, "item": message()}, terminal()]
        data = "".join("data: " + json.dumps(event) + "\n\n" for event in events)
        return web.Response(text=data, content_type="text/event-stream")

    async with harness(tmp_path, handler) as (client, store):
        status, text = await posted(client, original(stream))
        assert status == 200 and opaque()["encrypted_content"] in text
        before = opaque_records(store)
        assert len(before) == 1 and opaque()["encrypted_content"] not in before[0]["value"]
        assert store.db.execute("SELECT status FROM receipts").fetchone()[0] == "completed"

    async with harness(tmp_path, handler) as (client, store):
        status, text = await posted(client, continue_with(native_opaque()))
        assert status == 200 and [event["type"] for event in read_events(text)][-1] == "response.completed"
        assert requests[1]["provider"]["only"] == ["deepseek"]
        assert requests[1]["input"] == [
            {"type": "message", "role": "user", "content": "first"}, opaque(),
            {"type": "message", "role": "user", "content": "continue"},
        ]
        assert opaque_records(store) == before


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["credential", "ciphertext", "extension", "route", "proof-less"])
async def test_opaque_mismatch_is_rejected_before_any_provider_request(tmp_path, fault):
    requests = []

    async def handler(request):
        requests.append(await request.json())
        return web.Response(body=opaque_sse(), content_type="text/event-stream")

    if fault != "proof-less":
        async with harness(tmp_path, handler) as (client, store):
            status, text = await posted(client, original())
            assert status == 200 and read_events(text)[-1]["type"] == "response.completed"
            assert len(opaque_records(store)) == 1
    seed_count = len(requests)
    settings = {"ordinary_default_provider": STRUCTURED_PROVIDER} if fault == "route" else {}
    credential = "synthetic-replaced-credential" if fault == "credential" else None
    item = copy.deepcopy(native_opaque())
    if fault == "ciphertext":
        item["encrypted_content"] += "_ALTERED"
    if fault == "extension":
        item["vendor_reasoning_envelope"] = {"format": "spoof"}
    options = {"credential_provider": lambda: credential} if credential else {}
    async with harness(tmp_path, handler, settings=settings, **options) as (client, store):
        status, text = await posted(client, continue_with(item))
        assert status == 400 and "Opaque reasoning" in text
        assert len(requests) == seed_count
        assert store.db.execute("SELECT status FROM receipts ORDER BY started DESC").fetchone()[0] == "adapter_error"


@pytest.mark.asyncio
@pytest.mark.parametrize("fault", ["truncated", "late", "terminal-only", "failed"])
async def test_opaque_frames_require_completed_done_and_clean_http_eof(tmp_path, fault):
    async def handler(request):
        await request.read()
        body = opaque_sse(terminal_only=fault == "terminal-only", late=fault == "late", failed=fault == "failed")
        if fault != "truncated":
            return web.Response(body=body, content_type="text/event-stream")
        response = web.StreamResponse(headers={"Content-Length": str(len(body) + 31),
                                               "Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(body)
        request.transport.close()
        return response

    async with harness(tmp_path, handler) as (client, store):
        status, text = await posted(client, original())
        assert status == 200
        types = [event["type"] for event in read_events(text)]
        assert types[-1] == "response.failed" and "response.completed" not in types
        assert opaque()["encrypted_content"] not in text and opaque_records(store) == []
        expected = "failed" if fault == "failed" else "adapter_error"
        row = store.db.execute("SELECT status, detail FROM receipts").fetchone()
        assert row[0] == expected
        marker = {"truncated": "ContentLengthError", "late": "event after opaque",
                  "terminal-only": "lacks response.output_item.done", "failed": "fixture_failure"}[fault]
        assert marker in row[1]


@pytest.mark.asyncio
async def test_opaque_terminal_and_evidence_commit_are_atomic(tmp_path, monkeypatch):
    def partial_then_error(db, *_args, **_kwargs):
        db.execute("INSERT INTO adapter_metadata VALUES ('opaque-reasoning-v1:partial','synthetic')")
        raise sqlite3.OperationalError("synthetic opaque evidence error")

    async def handler(request):
        await request.read()
        return web.Response(body=opaque_sse(), content_type="text/event-stream")

    monkeypatch.setattr(protocol_state, "remember_completed", partial_then_error)
    async with harness(tmp_path, handler) as (client, store):
        _, text = await posted(client, original())
        assert "response.failed" in text and "response.completed" not in text
        assert opaque()["encrypted_content"] not in text and opaque_records(store) == []
        assert store.db.execute("SELECT status FROM receipts").fetchone()[0] == "adapter_error"
        assert store.db.execute("SELECT count(*) FROM completed_native_evidence").fetchone()[0] == 0
