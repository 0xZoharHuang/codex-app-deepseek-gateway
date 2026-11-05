"""Credential-bearing IDs are rejected before observation or failure redaction."""
import json
from aiohttp import web
import pytest
from codex_deepseek.protocol import MODEL,encode_sse
from test_bridge import harness,HEADERS,KEY
MODES=["created","completed","failed","error","nonstream_completed","nonstream_failed"]
def response(identifier,status):
    return {"id":identifier,"model":MODEL,"status":status,"output":[],
            "error":{"code":"server_error","message":"original failure"} if status=="failed" else None}
@pytest.mark.parametrize("mode",MODES)
@pytest.mark.parametrize("unsafe",[True,False])
async def test_response_id_checked_before_recording_and_redaction(tmp_path,mode,unsafe):
    identifier="gen-prefix-"+KEY+"-suffix" if unsafe else "gen-legitimate-identity"
    status="failed" if "failed" in mode or mode=="error" else "completed"
    value=response(identifier,status)
    event={"type":"response."+mode,"response":value}
    if mode=="created":event={"type":"response.created","response":response(identifier,"in_progress")}
    elif mode=="error":event={"type":"error","response":value,"code":"server_error","message":"original failure","param":None}
    elif mode.startswith("nonstream"):event=None
    async def upstream(request):
        await request.json()
        if event is None:return web.json_response(value)
        body=encode_sse(event)
        if mode=="created":body+=encode_sse({"type":"response.completed","response":response(identifier,"completed")})
        return web.Response(body=body,content_type="text/event-stream")
    async with harness(tmp_path,upstream) as (client,state):
        reply=await client.post("/v1/responses",headers=HEADERS,json={"model":MODEL,"input":"synthetic","stream":event is not None})
        text=await reply.text();row=state.list_receipts()[0]
        parsed=json.loads(text) if event is None else [json.loads(x[6:]) for x in text.splitlines() if x.startswith("data: ")]
        if unsafe:
            assert row["status"]=="adapter_error" and row["response_id"] is None
            assert KEY not in text and "[REDACTED]" not in json.dumps(parsed)
            if event is not None:assert "id" not in parsed[-1]["response"]
        else:
            assert row["status"]==("failed" if status=="failed" else "completed") and row["response_id"]==identifier
            final=parsed if event is None else parsed[-1]["response"]
            assert final["id"]==identifier
