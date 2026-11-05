"""Loopback receipt observation only; this does not qualify the desktop application."""
import copy
import json
from aiohttp import web
from multidict import CIMultiDict
import pytest
from codex_deepseek.protocol import MODEL, prepare
from test_bridge import HEADERS, harness, terminal

THREAD = "01a19df0-4649-7483-adfd-ae8a8e06d7cf"
TURN = "01a19f31-1baa-7452-b3c9-efed1d8a2c92"
CANARY = "private-native-inventory-not-for-receipts"

def canonical(kind):
    return json.dumps({"request_kind": kind, "thread_id": THREAD, "turn_id": TURN,
                       "tool_inventory": CANARY, "arbitrary_future_field": CANARY})

@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["turn", "compaction", "memory", "absent", "invalid", "conflict", "duplicate"])
async def test_native_request_receipt_never_changes_upstream_body_or_infers_unknown(tmp_path, case):
    body = {"model": MODEL, "input": "loopback-only"}
    headers = CIMultiDict(HEADERS)
    if case != "absent":
        body["client_metadata"] = {"x-codex-turn-metadata": canonical(case if case in {"turn", "compaction", "memory"} else "turn")}
    if case == "invalid":
        body["client_metadata"]["x-codex-turn-metadata"] = "bad " + CANARY
    if case == "conflict":
        headers.add("X-Codex-Turn-Metadata", canonical("compaction"))
    if case == "duplicate":
        headers.add("X-Codex-Turn-Metadata", canonical("turn"))
        headers.add("X-Codex-Turn-Metadata", canonical("turn"))
        assert len(headers.getall("X-Codex-Turn-Metadata")) == 2
    expected, _ = prepare(copy.deepcopy(body))
    captured = []
    async def upstream(request):
        captured.append(await request.json())
        return web.json_response(terminal()["response"])
    async with harness(tmp_path, upstream) as (client, state):
        reply = await client.post("/v1/responses", json=body, headers=headers)
        assert reply.status == 200 and (await reply.json())["status"] == "completed"
        assert captured == [expected]
        receipt = state.list_receipts()[0]
        observed = receipt["adaptations"]["native_request"]
        assert CANARY not in json.dumps(receipt)
        if case in {"turn", "compaction", "memory"}:
            assert observed["status"] == "recognized"
            assert observed["request_kind"] == case
            assert observed["thread_id"] == THREAD and observed["turn_id"] == TURN
        else:
            assert observed["status"] == {"absent":"unknown", "invalid":"invalid",
                                           "conflict":"conflict", "duplicate":"invalid"}[case]
            assert "request_kind" not in observed and "thread_id" not in observed

@pytest.mark.asyncio
async def test_observation_failure_is_explicit_bounded_and_does_not_change_wire(tmp_path, monkeypatch):
    from codex_app_deepseek import bridge
    def fail(*args):
        raise RuntimeError(CANARY)
    monkeypatch.setattr(bridge, "native_request_metadata", fail, raising=False)
    body = {"model": MODEL, "client_metadata": {"x-codex-turn-metadata": canonical("turn")}}
    expected, _ = prepare(copy.deepcopy(body))
    captured = []
    async def upstream(request):
        captured.append(await request.json())
        return web.json_response(terminal()["response"])
    async with harness(tmp_path, upstream) as (client, state):
        reply = await client.post("/v1/responses", json=body, headers=HEADERS)
        assert reply.status == 200
        await reply.read()
        assert captured == [expected]
        receipt = state.list_receipts()[0]
        assert receipt["adaptations"]["native_request"] == {
            "schema_version": 1, "status": "unavailable", "error_type": "RuntimeError"}
        assert CANARY not in json.dumps(receipt)
