import pytest
from aiohttp import web
from codex_deepseek.protocol import MODEL, encode_sse
from test_bridge import harness, terminal, message, HEADERS, read_events

CASES = [
    ("deepseek", "DeepSeek", {"model": MODEL, "input": "hello"}),
    ("morph/fp8", "Morph", {"model": MODEL, "text": {"format": {
        "type": "json_schema", "name": "result", "strict": True,
        "schema": {"type": "object", "properties": {}, "additionalProperties": False}}}}),
    ("primeintellect", "PrimeIntellect", {"model": MODEL, "parallel_tool_calls": False,
        "tools": [{"type": "function", "name": "ping", "parameters": {"type": "object"}}]}),
]

@pytest.mark.parametrize("route,display,body", CASES)
@pytest.mark.parametrize("use_slug", [False, True])
async def test_matching_observed_provider_identity_remains_accepted(tmp_path, route, display, body, use_slug):
    seen = []
    async def handler(request):
        seen.append(await request.json())
        response = terminal([message("{}")])["response"]
        response.update(model=MODEL, provider=route if use_slug else display)
        return web.json_response(response)
    async with harness(tmp_path, handler) as (client, state):
        reply = await client.post("/v1/responses", headers=HEADERS, json=body)
        result = await reply.json()
        assert reply.status == 200, result
        assert result["status"] == "completed"
        assert [x["provider"]["only"] for x in seen] == [[route]]
        receipt = state.list_receipts()[0]
        assert receipt["status"] == "completed"
        assert receipt["observed_provider"] == (route if use_slug else display)

@pytest.mark.parametrize("route,display,body", CASES)
async def test_explicit_provider_mismatch_is_failure_without_retry(tmp_path, route, display, body):
    calls = 0
    unexpected = "Morph" if route == "deepseek" else "DeepSeek"
    async def handler(request):
        nonlocal calls
        calls += 1
        assert (await request.json())["provider"]["only"] == [route]
        response = terminal([message("{}")])["response"]
        response.update(model=MODEL, provider=unexpected)
        return web.json_response(response)
    async with harness(tmp_path, handler) as (client, state):
        reply = await client.post("/v1/responses", headers=HEADERS, json=body)
        result = await reply.json()
        assert reply.status == 502, result
        assert "provider" in result["error"]["message"]
        receipt = state.list_receipts()[0]
        assert receipt["requested_provider"] == route
        assert receipt["observed_provider"] == unexpected
        assert receipt["status"] == "adapter_error"
        assert receipt["delivery_status"] == "failed"
        assert calls == 1

@pytest.mark.parametrize("route,display,body", CASES)
async def test_stream_provider_mismatch_never_publishes_completed(tmp_path, route, display, body):
    calls = 0
    async def handler(request):
        nonlocal calls
        calls += 1
        assert (await request.json())["provider"]["only"] == [route]
        created = {"type": "response.created", "response": {
            "id": "resp_one", "model": MODEL, "provider": "UnconfiguredProvider"}}
        return web.Response(body=encode_sse(created)+encode_sse(terminal([message("{}")])),
                            content_type="text/event-stream")
    async with harness(tmp_path, handler) as (client, state):
        reply = await client.post("/v1/responses", headers=HEADERS, json={**body, "stream": True})
        events = read_events(await reply.text())
        assert events[-1]["type"] == "response.failed"
        assert not any(x["type"] in {"response.created", "response.output_item.done", "response.completed"} for x in events)
        receipt = state.list_receipts()[0]
        assert receipt["observed_provider"] == "UnconfiguredProvider"
        assert receipt["status"] == "adapter_error"
        assert calls == 1

async def test_late_provider_mismatch_preserves_prior_tool_event_but_never_success(tmp_path):
    calls = 0
    tool = {"type": "function_call", "id": "fc_one", "call_id": "call_one",
            "name": "ping", "arguments": "{}", "status": "completed"}
    async def handler(request):
        nonlocal calls
        calls += 1
        created = {"type": "response.created", "response": {"id": "resp_one", "model": MODEL}}
        done = {"type": "response.output_item.done", "output_index": 0, "item": tool}
        end = terminal([tool])
        end["response"]["provider"] = "Morph"
        return web.Response(body=encode_sse(created)+encode_sse(done)+encode_sse(end),
                            content_type="text/event-stream")
    async with harness(tmp_path, handler) as (client, state):
        reply = await client.post("/v1/responses", headers=HEADERS, json={
            "model": MODEL, "stream": True,
            "tools": [{"type": "function", "name": "ping", "parameters": {"type": "object"}}]})
        events = read_events(await reply.text())
        assert any(x["type"] == "response.output_item.done" and x["item"]["call_id"] == "call_one" for x in events)
        assert events[-1]["type"] == "response.failed"
        assert not any(x["type"] == "response.completed" for x in events)
        receipt = state.list_receipts()[0]
        assert receipt["response_id"] == "resp_one" and receipt["status"] == "adapter_error"
        assert calls == 1

@pytest.mark.parametrize("route,display,body", CASES)
async def test_same_identity_can_change_equivalent_spelling_within_stream(tmp_path, route, display, body):
    calls = 0
    async def handler(request):
        nonlocal calls
        calls += 1
        assert (await request.json())["provider"]["only"] == [route]
        created = {"type": "response.created", "response": {
            "id": "resp_one", "model": MODEL, "provider": display}}
        end = terminal([message("{}")])
        end["response"].update(model="deepseek-v4.1-flash-20261017", provider=route)
        return web.Response(body=encode_sse(created)+encode_sse(end), content_type="text/event-stream")
    async with harness(tmp_path, handler) as (client, state):
        reply = await client.post("/v1/responses", headers=HEADERS, json={**body, "stream": True})
        events = read_events(await reply.text())
        assert events[-1]["type"] == "response.completed"
        assert not any(x["type"] == "response.failed" for x in events)
        receipt = state.list_receipts()[0]
        assert receipt["status"] == "completed"
        assert receipt["observed_model"] == "deepseek-v4.1-flash-20261017"
        assert receipt["observed_provider"] == route
        assert calls == 1
