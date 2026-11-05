"""Synthetic terminal/identity lifecycle regression; no paid provider or App GUI evidence."""
import json
import pytest
from aiohttp import web
from codex_deepseek.protocol import MODEL, encode_sse
ID = "gen-terminal-identity-offline"

from test_bridge import KEY, HEADERS, harness, ImageFiles, image_factory, image_input


async def run_case(tmp_path, value, *, stream, damage=None, header=None, header_only=False):
    frames = frames_for(value, damage)
    if header_only:
        frames = [{"type": "error", "code": "synthetic_header_only", "message": "No response terminal or ID"}]
        value = {"error": {"code": "synthetic_header_only", "message": "No response terminal or ID"}}
    headers = {} if header is None else {"X-Generation-Id": header}
    calls = []
    async def upstream(request):
        calls.append(await request.json())
        if not stream: return web.json_response(value, headers=headers)
        return web.Response(body=b"".join(encode_sse(e) for e in frames), content_type="text/event-stream", headers=headers)
    backend = ImageFiles()
    factory, made = image_factory(tmp_path, backend)
    async with harness(tmp_path, upstream, image_transport_factory=factory) as (client, state):
        body = image_input(stream=stream, tools=[{"type": "function", "name": "terminal_only", "parameters": {"type": "object"}}])
        reply = await client.post("/v1/responses", headers=HEADERS, json=body)
        text = await reply.text()
        receipt = state.list_receipts()[0]
        return {"receipt_status": receipt["status"], "response_id": receipt["response_id"], "text": text,
                "image": receipt["adaptations"]["image_transport"],
                "attested": state.db.execute("SELECT COUNT(*) FROM completed_native_evidence").fetchone()[0],
                "transport": made[0], "deleted": backend.deletes, "calls": len(calls)}



def envelope(status="completed", identity="model"):
    value = {"id": ID, "status": status, "model": MODEL, "output": [], "error": None}
    if identity == "model": value["model"] = "unknown"
    else: value["provider"] = "unrequested-provider"
    if status == "completed":
        value["output"] = [{"type": "function_call", "id": "fc-terminal-only", "call_id": "call-terminal-only",
                            "name": "terminal_only", "arguments": "{}", "status": "completed"}]
    return value


def frames_for(value, damage=None):
    created = {"type": "response.created", "response": {"id": ID, "model": MODEL, "status": "in_progress"}}
    terminal = {"type": "response." + value["status"], "response": value}
    if damage == "created_unknown": created["response"]["model"] = "unknown"
    elif damage == "flat_error": terminal = {"type": "error", "code": "synthetic_error", "message": "failure without a terminal response"}
    elif damage == "missing_id": value.pop("id")
    elif damage == "changed_id": value["id"] = "gen-unrelated-offline"
    elif damage == "credential_id": value["id"] = "gen-" + KEY
    elif damage == "model_shape": value["model"] = 7
    elif damage == "output_shape": value["output"] = None
    elif damage == "status_mismatch": terminal["type"] = "response.incomplete"
    return [created, terminal]


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("status", ["completed", "incomplete"])
@pytest.mark.parametrize("identity", ["model", "provider"])
async def test_verified_terminal_ends_image_consumption_even_when_success_identity_is_rejected(tmp_path, stream, status, identity):
    result = await run_case(tmp_path, envelope(status, identity), stream=stream)
    assert result["receipt_status"] == "adapter_error" and result["response_id"] == ID
    assert result["attested"] == 0 and "response.completed" not in result["text"]
    assert "response.incomplete" not in result["text"] and "response.output_item" not in result["text"]
    diag = await result["transport"].diagnostics()
    assert diag["active_transport_pins"] == 0 and not diag["unknown_upstream_calls"]
    assert result["image"]["consumption_status"] == "provider_terminal"
    assert result["image"]["provider_terminal"] == status and result["image"]["provider_response_id"] == ID
    assert (await result["transport"].cleanup(all_unpinned=True))["all_owned_files_deleted"]
    assert len(result["deleted"]) == 1 and result["calls"] == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("damage", ["created_unknown", "flat_error", "missing_id", "changed_id", "credential_id", "model_shape", "output_shape", "status_mismatch"])
async def test_unproved_or_unsafe_response_still_keeps_image_consumption_unknown(tmp_path, damage):
    result = await run_case(tmp_path, envelope(), stream=True, damage=damage)
    diag = await result["transport"].diagnostics()
    assert diag["active_transport_pins"] == 1 and len(diag["unknown_upstream_calls"]) == 1
    assert not result["deleted"] and result["image"].get("consumption_status") != "provider_terminal"
    assert result["attested"] == 0 and KEY not in result["text"]
    assert not any(marker in result["text"] for marker in ("response.completed", "response.incomplete", "response.output_item"))


@pytest.mark.asyncio
@pytest.mark.parametrize("stream", [False, True])
async def test_safe_generation_header_is_candidate_only_without_response_identity(tmp_path, stream):
    identifier = "gen-header-candidate-only"
    result = await run_case(tmp_path, envelope(), stream=stream, header=identifier, header_only=True)
    assert result["image"].get("generation_header_candidate") == {
        "id": identifier, "provenance": "http_response_header:X-Generation-Id"}
    assert result["response_id"] is None and identifier not in result["text"]
    assert result["image"].get("provider_response_id") is None
    assert result["image"].get("consumption_status") != "provider_terminal"
    diag = await result["transport"].diagnostics()
    assert diag["active_transport_pins"] == 1 and len(diag["unknown_upstream_calls"]) == 1
    assert not result["deleted"] and result["attested"] == 0
    for line in result["text"].splitlines():
        if line.startswith("data: "):
            assert "id" not in json.loads(line[6:]).get("response", {})


@pytest.mark.asyncio
@pytest.mark.parametrize("header", ["", "or_file_not_a_generation", "gen-bad_id", "gen-" + "x" * 125, "gen-" + KEY])
async def test_unsafe_generation_header_is_not_persisted_or_promoted(tmp_path, header):
    result = await run_case(tmp_path, envelope(), stream=True, header=header, header_only=True)
    assert "generation_header_candidate" not in result["image"]
    assert result["response_id"] is None and KEY not in result["text"]
    assert result["image"].get("provider_response_id") is None
    diag = await result["transport"].diagnostics()
    assert diag["active_transport_pins"] == 1 and len(diag["unknown_upstream_calls"]) == 1
    assert not result["deleted"] and result["attested"] == 0
