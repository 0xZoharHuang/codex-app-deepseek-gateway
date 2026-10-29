"""Loopback-only scripted Responses history checks; never App/model acceptance."""
from __future__ import annotations
import argparse,asyncio,copy,hashlib,importlib.util,json,sys,time,uuid
from pathlib import Path
import aiohttp
from aiohttp import web

HERE=Path(__file__).resolve().parent
ROOT=HERE.parents[2]

def load(name):
    spec=importlib.util.spec_from_file_location("qa_reasoning_"+name,HERE/(name+".py"))
    module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module
    spec.loader.exec_module(module);return module

def frames(raw):
    return [json.loads(line[6:]) for line in raw.splitlines() if line.startswith("data: ") and line[6:]!="[DONE]"]

def marker_text(item):
    return "".join(p.get("text","") for p in item.get("content",[]) if isinstance(p,dict))

def missing_groups(items):
    """Strict test oracle, not an implementation of the provider/runtime."""
    missing=[];has_reasoning=False
    for item in items:
        kind=item.get("type","message")
        if kind=="reasoning":
            has_reasoning=bool(marker_text(item).strip())
        elif kind in {"function_call","custom_tool_call"} or kind=="message" and item.get("role")=="assistant":
            if not has_reasoning:missing.append(item.get("call_id",item.get("id","assistant-message")))
        elif kind in {"function_call_output","custom_tool_call_output"} or kind=="message" and item.get("role") in {"user","system","developer"}:
            has_reasoning=False
    return missing

def native_items(events):
    result={}
    for event in events:
        if event["type"]=="response.output_item.done":result[event["item"]["id"]]=event["item"]
        if event["type"]=="response.completed":
            for item in event["response"]["output"]:result[item["id"]]=item
    return list(result.values())

def mock_binding(c,tools,target):
    tool={"name":"record_effect","description":c.MARKER+" record_effect(case_id: string)",
        "inputSchema":{"type":"object","properties":{"case_id":{"type":"string"}},"required":["case_id"]},
        "annotations":{"idempotentHint":False,"openWorldHint":False,"destructiveHint":False}}
    return {"schema":1,"evidence_level":"source_contract_loopback_mock","thread_id":target,
        "mcp_status_request":{"method":"mcpServerStatus/list","params":{"threadId":target,"serverName":"loopback_counter"}},
        "mcp_status_response":{"data":[{"name":"loopback_counter","runtimeStatus":"connected","toolsError":None,"tools":{"record_effect":tool}}]},
        "native_tools":copy.deepcopy(tools),"introspection_thread_id":target,
        "introspection_history":[{"type":"custom_tool_call","name":"exec","call_id":"explicit-mock-discovery","status":"completed","input":c.discovery_program()},
            {"type":"custom_tool_call_output","call_id":"explicit-mock-discovery","output":json.dumps({"qa":c.DISCOVERY,"entries":[
                {"name":"mcp__loopback_counter__record_effect","description":tool["description"],"callable":True}]})}],
        "raw_evidence":[]}

async def run_case(module,kind,evidence,tools,code_mode=False):
    base=module.base if code_mode else module
    target="reasoning-loopback-"+uuid.uuid4().hex
    home=evidence/(kind+"-"+uuid.uuid4().hex[:8]);module.initialize(home)
    harness=(module.CodeModeHarness(home,allow_loopback_test_binding=True) if code_mode else module.FaultHarness(home))
    strict_runner=None;bridge_runner=None;requests=[];ports=[]
    row={"kind":kind,"code_mode":code_mode}
    try:
        if code_mode:harness.bind_evidence(mock_binding(module,tools,target))
        ready=await harness.start()
        ports += [int(ready[k].split(":")[2].split("/")[0]) for k in ("gateway_url","control_url")]
        arm=harness.arm(kind,target)
        body={"model":base.MODEL,"stream":True,"reasoning":{"effort":"high"},"tools":copy.deepcopy(tools),
            "input":[{"role":"user","content":"Explicit synthetic QA transport check. No actual tool execution."}]}
        headers={"Authorization":"Bearer "+harness.fixture["gateway_token"],"thread-id":target,"session-id":target}
        async with aiohttp.ClientSession(trust_env=False) as client:
            async with client.post(ready["gateway_url"]+"/responses",json=body,headers=headers) as response:
                status=response.status;events=frames(await response.text())
            assert status==200
            items=native_items(events)
            reasoning=[i for i in items if i["type"]=="reasoning"]
            assert len(reasoning)==1 and marker_text(reasoning[0])==base.SYNTHETIC_REASONING_TEXT
            done=[e for e in events if e["type"]=="response.output_item.done" and e["item"].get("type")=="reasoning"]
            assert len(done)==1 and done[0]["output_index"]==0
            added=[e for e in events if e["type"]=="response.output_item.added" and e["item"].get("type")!="reasoning"]
            assert all(e["output_index"]==1 for e in added)
            completed=any(e["type"]=="response.completed" for e in events)
            expected_complete=kind in {"synthetic_success","codemode_discovery","codemode_effect"}
            assert completed==expected_complete
            if not expected_complete:
                assert events[-1]["type"]=="response.failed"
            calls=[i for i in items if i["type"] in {"function_call","custom_tool_call"}]
            expected_call=kind in {"tool_done_cut","codemode_discovery","codemode_effect"}
            assert bool(calls)==expected_call
            if expected_call:assert len(calls)==1
            assert harness.ledger.count(arm["case_id"])==0
            assert harness.plan.remaining==0 and harness.plan.fired==1
            assert harness.live_calls==0 and not harness.live_enabled
            continuation=copy.deepcopy(body)
            continuation["input"]+=copy.deepcopy(items)
            if calls:
                call=calls[0]
                output_type="custom_tool_call_output" if call["type"]=="custom_tool_call" else "function_call_output"
                continuation["input"].append({"type":output_type,"call_id":call["call_id"],
                    "output":"Explicit synthetic no-execution result; no approval or side effect is asserted."})
            else:
                continuation["input"].append({"role":"user","content":"Continue the transport check."})
            async def strict(request):
                payload=await request.json()
                missing=missing_groups(payload["input"])
                requests.append({"missing":missing,"model":payload["model"],"reasoning":payload.get("reasoning"),
                    "provider":payload.get("provider"),"input_types":[i.get("type","message") for i in payload["input"]],
                    "scaffold_preserved":any(i.get("id")==reasoning[0]["id"] and marker_text(i)==base.SYNTHETIC_REASONING_TEXT for i in payload["input"])})
                if missing:return web.json_response({"error":{"code":400,"message":"Local QA oracle: missing scripted reasoning scaffold"}},status=400)
                rid="qa-strict-"+uuid.uuid4().hex
                return web.Response(body=b"".join(base.encode_sse(e) for e in [
                    {"type":"response.created","response":{"id":rid,"status":"in_progress","output":[]}},
                    {"type":"response.completed","response":{"id":rid,"status":"completed","output":[]}}]),content_type="text/event-stream")
            app=web.Application();app.router.add_post("/responses",strict)
            strict_runner=web.AppRunner(app,access_log=None);await strict_runner.setup()
            site=web.TCPSite(strict_runner,"127.0.0.1",0);await site.start()
            strict_port=site._server.sockets[0].getsockname()[1];ports.append(strict_port)
            settings=base.BridgeSettings(token="loopback-qa-gateway-token-length-32",accepted_models=(base.MODEL,),
                upstream_url=f"http://127.0.0.1:{strict_port}/responses",connect_timeout=5,read_timeout=5)
            bridge=base.create_app(settings,lambda:"loopback-upstream-key",harness.state,test_upstream=True)
            bridge_runner=web.AppRunner(bridge,access_log=None);await bridge_runner.setup()
            site=web.TCPSite(bridge_runner,"127.0.0.1",0);await site.start()
            bridge_port=site._server.sockets[0].getsockname()[1];ports.append(bridge_port)
            url=f"http://127.0.0.1:{bridge_port}/v1/responses"
            async with client.post(url,json=continuation,headers={"Authorization":"Bearer loopback-qa-gateway-token-length-32"}) as response:
                continuation_status=response.status;await response.read()
            assert continuation_status==200 and requests[-1]["missing"]==[] and requests[-1]["scaffold_preserved"]
            assert requests[-1]["reasoning"]["effort"]=="high"
            assert requests[-1]["provider"]["only"]==["deepseek"] and requests[-1]["provider"]["allow_fallbacks"] is False
            negative_status=None
            if calls or any(i.get("role")=="assistant" for i in items):
                corrupt=copy.deepcopy(continuation)
                corrupt["input"]=[i for i in corrupt["input"] if i.get("id")!=reasoning[0]["id"]]
                async with client.post(url,json=corrupt,headers={"Authorization":"Bearer loopback-qa-gateway-token-length-32"}) as response:
                    negative_status=response.status;await response.read()
                assert negative_status==400 and requests[-1]["missing"]
            assert harness.ledger.count(arm["case_id"])==0 and harness.live_calls==0
            row.update(emitted_types=[i["type"] for i in items],original_terminal="completed" if completed else "failed",
                continuation_status=continuation_status,removed_scaffold_status=negative_status,
                output_call_ids=[i["call_id"] for i in calls],oracle_requests=requests,
                effect_count=0,fixture_live_requests=0)
    finally:
        if bridge_runner:await bridge_runner.cleanup()
        if strict_runner:await strict_runner.cleanup()
        await harness.close()
        row.update(resources_closed=True,known_loopback_ports=ports)
        (evidence/(kind+("-codemode" if code_mode else "")+".json")).write_text(json.dumps(row,indent=2),encoding="utf-8")
    return row

async def main(native_source):
    f=load("fault_server");c=load("codemode_fault")
    evidence=ROOT/".evidence"/("synthetic-reasoning-selftest-"+uuid.uuid4().hex[:10]);evidence.mkdir()
    sources=[HERE/name for name in ["synthetic_reasoning.py","fault_server.py","codemode_fault.py","synthetic_reasoning_selftest.py"]]
    sources += [ROOT/"src/codex_app_deepseek/bridge.py",ROOT/"src/codex_app_deepseek/protocol_state.py"]
    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in sources}
    schema={"type":"object","properties":{"case_id":{"type":"string"}},"required":["case_id"],"additionalProperties":False}
    function_tools=[{"type":"function","name":"record_effect","description":f.MARKER,"parameters":schema}]
    spec=native_source/"codex-rs/core/src/tools/code_mode/execute_spec.rs"
    grammar=spec.read_text().split('r#"')[1].split('"#')[0]
    custom_tools=[{"type":"custom","name":"exec","description":"Source-derived QA only. Run JavaScript with ALL_TOOLS and tools; text prints output.",
        "format":{"type":"grammar","syntax":"lark","definition":grammar}}]
    rows=[]
    for kind in ["synthetic_success","text_cut","tool_incomplete_cut","tool_done_cut"]:
        rows.append(await run_case(f,kind,evidence,function_tools))
    for kind in ["codemode_discovery","codemode_effect","tool_incomplete_cut","tool_done_cut"]:
        rows.append(await run_case(c,kind,evidence,custom_tools,True))
    assert all(hashlib.sha256(Path(p).read_bytes()).hexdigest()==digest for p,digest in before.items())
    result={"status":"passed_loopback_only","cases":rows,"source_manifest":before,"source_unchanged":True,
        "actual_model_requests":0,"WinCred_reads":0,"App_or_native_invocations":0,"tool_executions":0,
        "limitations":["Strict checker is a local test oracle, not actual DeepSeek acceptance.",
            "CodeMode binding is explicitly source-contract mock; it is not native discovery.",
            "A local mock 200 is not an App pass. Cut cases retain failed terminals and no tool is executed."]}
    (evidence/"result.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps({"status":result["status"],"cases":len(rows),"result":str(evidence/"result.json")}))
if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument("--native-source",type=Path,required=True)
    asyncio.run(main(parser.parse_args().native_source.resolve()))
