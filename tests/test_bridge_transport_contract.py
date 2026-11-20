"""Real loopback transport boundaries for the gateway's own aiohttp owner."""
import asyncio
import hashlib
from io import BytesIO
import json

import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

import codex_app_deepseek.bridge as bridge
import codex_app_deepseek.image_transport as images
from codex_deepseek.protocol import MODEL, encode_sse
from test_bridge import HEADERS, KEY, harness, terminal, image_factory, image_input, ImageFiles


def observe_response_streams(monkeypatch):
    streams = []
    original = aiohttp.ClientSession.post
    def post(client, url, **kwargs):
        # TestClient uses request(), while the gateway's upstream uses post().
        stream = kwargs.get("data")
        assert isinstance(stream, BytesIO) and not stream.closed
        assert "json" not in kwargs and kwargs["headers"]["Content-Type"] == "application/json"
        streams.append(stream)
        return original(client, url, **kwargs)
    monkeypatch.setattr(aiohttp.ClientSession, "post", post)
    return streams


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [413, 429])
@pytest.mark.parametrize("stream, with_images", [(False, False), (True, True)])
async def test_upstream_error_keeps_status_when_body_is_truncated(tmp_path, status, stream, with_images):
    calls = 0
    backend = ImageFiles()
    factory = image_factory(tmp_path, backend)[0] if with_images else None

    async def upstream(request):
        nonlocal calls
        calls += 1
        await request.read()
        response = web.StreamResponse(status=status, headers={
            "Content-Type": "application/json",
            "Content-Length": "4096",
            "Retry-After": "7",
        })
        await response.prepare(request)
        await response.write(b'{"error":"PRIVATE_PARTIAL_DIAGNOSTIC')
        request.transport.close()
        return response

    async with harness(tmp_path, upstream, image_transport_factory=factory) as (client, state):
        body = image_input(stream=stream) if with_images else {
            "model": MODEL, "input": "synthetic", "stream": stream,
        }
        response = await client.post("/v1/responses", json=body, headers=HEADERS)
        assert response.status == status
        assert response.headers["Retry-After"] == "7"
        assert (await response.json())["error"]["code"] == "upstream_error_body_unavailable"
        assert "PRIVATE_PARTIAL_DIAGNOSTIC" not in await response.text()
        receipt, = state.list_receipts()
        assert receipt["status"] == "upstream_error"
        assert receipt["upstream_status"] == status
        assert "PRIVATE_PARTIAL_DIAGNOSTIC" not in json.dumps(receipt)
        assert calls == 1
        if with_images:
            assert receipt["adaptations"]["image_transport"]["consumption_status"] == "unknown"


@pytest.mark.asyncio
@pytest.mark.parametrize("with_images", [False, True])
@pytest.mark.parametrize("outcome", ["completed", "rejected", "truncated"])
async def test_large_response_request_is_exact_owned_stream_on_every_http_outcome(tmp_path, monkeypatch, with_images, outcome):
    streams = observe_response_streams(monkeypatch)
    text = "x" * (2**20 + 117) + " 真实字符😀"
    backend = ImageFiles()
    factory = image_factory(tmp_path, backend)[0] if with_images else None
    calls = []
    async def upstream(request):
        raw = b"".join([chunk async for chunk in request.content.iter_chunked(65536)])
        data = json.loads(raw)
        assert request.headers["Content-Length"] == str(len(raw)) and len(raw) > 2**20
        if with_images:
            assert data["input"][0]["content"][0]["text"] == text
            assert "file_id" in data["input"][0]["content"][1]
            assert "image_url" not in data["input"][0]["content"][1]
            assert raw == images.encode_body(data)
        else:
            assert data["input"] == text
            assert raw == json.dumps(data, allow_nan=False).encode("utf-8")
        calls.append(hashlib.sha256(raw).hexdigest())
        if outcome == "rejected":
            return web.json_response({"error": "synthetic quota boundary"}, status=429)
        if outcome == "truncated":
            return web.Response(content_type="text/event-stream", body=encode_sse({
                "type": "response.created", "response": {"id": "resp_one"}}))
        return web.json_response(terminal()["response"])
    async with harness(tmp_path, upstream, image_transport_factory=factory) as (client, state):
        body = image_input(stream=outcome == "truncated") if with_images else {
            "model": MODEL, "input": text, "stream": outcome == "truncated"}
        if with_images:
            body["input"][0]["content"][0]["text"] = text
        # The test sender must not itself create the forbidden large BytesPayload.
        with BytesIO(json.dumps(body, ensure_ascii=False).encode("utf-8")) as sender:
            response = await client.post("/v1/responses", data=sender, headers={**HEADERS, "Content-Type": "application/json"})
            raw = await response.read()
        assert len(calls) == len(streams) == 1
        assert streams[0].closed
        row = state.list_receipts()[0]
        if outcome == "completed":
            assert response.status == 200 and row["status"] == "completed"
        elif outcome == "rejected":
            assert response.status == 429 and row["status"] == "upstream_error"
        else:
            assert b"response.failed" in raw and b"response.completed" not in raw
            assert row["status"] == "adapter_error"


@pytest.mark.asyncio
async def test_large_request_cancellation_closes_owned_body_and_upstream_without_replay(tmp_path, monkeypatch):
    streams = observe_response_streams(monkeypatch)
    cancelled = asyncio.Event()
    calls = 0
    async def upstream(request):
        nonlocal calls
        calls += 1
        raw = b"".join([chunk async for chunk in request.content.iter_chunked(65536)])
        assert len(raw) > 2**20
        response = web.StreamResponse(headers={"Content-Type": "text/event-stream"})
        await response.prepare(request)
        await response.write(encode_sse({"type": "response.created", "response": {"id": "resp_one"}}))
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()
    async with harness(tmp_path, upstream) as (client, state):
        with BytesIO(json.dumps({"model": MODEL, "stream": True, "input": "x" * (2**20 + 5)}).encode()) as sender:
            response = await client.post("/v1/responses", data=sender, headers={**HEADERS, "Content-Type": "application/json"})
            await response.content.readuntil(b"\n\n")
            response.close()
        await asyncio.wait_for(cancelled.wait(), 3)
        for _ in range(100):
            if streams[0].closed:
                break
            await asyncio.sleep(.01)
        assert len(streams) == calls == 1 and streams[0].closed
        assert state.list_receipts()[0]["status"] == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("control", [
    {"unrecognized_native_control": True},
    {"provider": {"allow_fallbacks": True}},
    {"parallel_tool_calls": "false"},
    {"reasoning": False},
    {"reasoning": {"effort": 1024}},
    {"reasoning": {"effort": "high", "new_control": True}},
    {"reasoning": {"summary": "detailed"}},
    {"text": {"verbosity": "high"}},
    {"text": {"new_control": True}},
    {"text": {"format": {"type": "json_schema", "schema": {}, "new_control": True}}},
    {"stream_options": {"reasoning_summary_delivery": "sequential_cutoff"}},
    {"service_tier": "priority"},
    {"access_programs": ["unavailable-cloud-right"]},
    {"tools": [{"type": "function", "name": "f", "parameters": {}, "new_control": True}]},
    {"tools": [{"type": "namespace", "name": "ns", "tools": [
        {"type": "custom", "name": "f", "format": {"type": "text", "new_control": True}}]}]},
    {"tools": [{"type": "custom", "name": "f", "format": {"type": "text", "syntax": "regex", "definition": "must-match"}}]},
    {"tool_choice": {"type": "function", "name": "f", "new_control": True}},
])
async def test_unsupported_controls_fail_before_credential_receipt_or_upstream(tmp_path, control):
    calls = []
    async def upstream(request):
        calls.append(request)
        return web.json_response(terminal()["response"])
    def credentials():
        pytest.fail("Unsupported semantics must fail before reading a credential")
    async with harness(tmp_path, upstream, credential_provider=credentials) as (client, state):
        response = await client.post("/v1/responses", json={"model": MODEL, **control}, headers=HEADERS)
        assert response.status == 400 and (await response.json())["error"]["code"] == "provider_compatibility"
        assert not calls and not state.list_receipts()


@pytest.mark.asyncio
@pytest.mark.parametrize("reasoning", [None, {}, {"effort": "high", "summary": "none"}])
async def test_native_defaults_and_opaque_parameter_schema_remain_supported(tmp_path, reasoning):
    schema = {"type": "object", "$defs": {"name": {"type": "string"}},
              "properties": {"name": {"$ref": "#/$defs/name"}}, "x-native-extension": {"arbitrary": [1, None, True]}}
    calls = []
    async def upstream(request):
        value = await request.json()
        assert value["tools"][0]["parameters"] == schema
        assert value["include"] == ["reasoning.encrypted_content"]
        assert value["text"] == {} and value["client_metadata"] == {"thread_id": "native-thread"}
        calls.append(value)
        return web.json_response(terminal()["response"])
    body = {"model": MODEL, "input": [], "reasoning": reasoning, "text": {}, "stream": False,
            "parallel_tool_calls": True, "store": False, "include": ["reasoning.encrypted_content"],
            "client_metadata": {"thread_id": "native-thread"}, "service_tier": None,
            "tools": [{"type": "function", "name": "f", "description": "native tool", "strict": True, "parameters": schema}]}
    async with harness(tmp_path, upstream) as (client, state):
        response = await client.post("/v1/responses", json=body, headers=HEADERS)
        assert response.status == 200 and len(calls) == 1
        row = state.list_receipts()[0]
        if reasoning and reasoning.get("summary") == "none":
            assert row["adaptations"]["reasoning_summary"]["mapping"] == "no_summary_requested"


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["completed", "rejected", "cancelled"])
async def test_large_files_multipart_preserves_bytes_and_closes_stream(tmp_path, monkeypatch, outcome):
    instances = []
    class OwnedBytesIO(BytesIO):
        def __init__(self, value):
            super().__init__(value)
            instances.append(self)
    monkeypatch.setattr(images, "BytesIO", OwnedBytesIO)
    blob = bytes(range(256)) * 5000
    started, cancelled = asyncio.Event(), asyncio.Event()
    calls, received = [], []
    async def upload(request):
        calls.append(request.path)
        reader = await request.multipart()
        part = await reader.next()
        assert part.name == "file" and part.filename == "synthetic.png" and part.headers["Content-Type"] == "image/png"
        chunk = await part.read_chunk()
        if outcome == "cancelled":
            started.set()
            try:
                # Keep reading so server-side flow control cannot hide the
                # peer disconnect behind an unread multipart request body.
                while await part.read_chunk():
                    pass
                await asyncio.Event().wait()
            finally:
                cancelled.set()
        content = bytearray(chunk)
        while chunk := await part.read_chunk():
            content.extend(chunk)
        received.append(bytes(content))
        if outcome == "rejected":
            return web.json_response({"error": {"code": "invalid_file"}}, status=400)
        return web.json_response({"id": "or_file_boundary123", "filename": "synthetic.png",
                                  "mime_type": "image/png", "size_bytes": len(content)})
    app = web.Application(); app.router.add_post("/files", upload)
    server = TestServer(app)
    # Bound fixture teardown even if a broken cancellation path leaves its
    # request handler running; TestServer constructor kwargs are not runner kwargs.
    await server.start_server(shutdown_timeout=3)
    async with server, aiohttp.ClientSession() as client:
        class LocalClient:
            def request(self, method, url, **kwargs):
                assert url == "https://openrouter.ai/api/v1/files"
                assert kwargs["allow_redirects"] is False and kwargs["headers"]["Authorization"] == "Bearer synthetic"
                return client.request(method, server.make_url("/files"), **kwargs)
        backend = images.OpenRouterFilesBackend(LocalClient())
        if outcome == "cancelled":
            task = asyncio.create_task(backend.upload("synthetic", blob, "image/png", "synthetic.png"))
            await asyncio.wait_for(started.wait(), 3)
            task.cancel()
            done, _ = await asyncio.wait({task}, timeout=3)
            if task not in done:
                task.cancel()
                await asyncio.wait({task}, timeout=1)
                if task.done() and not task.cancelled():
                    task.exception()
                pytest.fail("cancelled upload did not terminate")
            with pytest.raises(asyncio.CancelledError):
                task.result()
            await asyncio.wait_for(cancelled.wait(), 3)
        elif outcome == "rejected":
            with pytest.raises(images.FileOperationError) as error:
                await backend.upload("synthetic", blob, "image/png", "synthetic.png")
            assert error.value.status == 400 and not error.value.definitely_not_created
        else:
            result = await backend.upload("synthetic", blob, "image/png", "synthetic.png")
            assert result.size_bytes == len(blob)
        assert len(instances) == len(calls) == 1 and instances[0].closed
        if outcome != "cancelled":
            assert received == [blob]
