"""Two bounded source/loopback cases; no App/native/model/host tool execution."""
from __future__ import annotations
import argparse, asyncio, copy, hashlib, json, sys, time, uuid
from types import SimpleNamespace
from pathlib import Path
import aiohttp
from aiohttp import web
from aiohttp.test_utils import TestServer
HERE=Path(__file__).parent
sys.path.insert(0,str(HERE))
import codemode_fault as f
from codex_deepseek import protocol
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("--native-source",type=Path,required=True)
parser.add_argument("--expect-final-authority",action="store_true",
                    help="Require the selected production bridge to accept valid final revisions; old c110 must omit this.")
args=parser.parse_args()
SPEC=args.native_source.resolve()/"codex-rs/core/src/tools/code_mode/execute_spec.rs"
GRAMMAR=SPEC.read_text().split('r#"')[1].split('"#')[0]
TOOLS=[{"type":"custom","name":"exec","description":"Source-only mock: tools and ALL_TOOLS; text prints output.",
        "format":{"type":"grammar","syntax":"lark","definition":GRAMMAR}}]
TARGET="source-only-final-input-thread"
def frames(text):
    return [json.loads(line[5:].strip()) for line in text.splitlines() if line.startswith("data:") and line[5:].strip()!="[DONE]"]
def body():
    return {"model":f.base.MODEL,"stream":True,"input":"SOURCE_ONLY_FINAL_INPUT_FIXTURE","tools":copy.deepcopy(TOOLS)}
def mock_binding():
    tool={"name":"record_effect","description":f.MARKER+": append one fixture effect. record_effect(case_id: string)",
          "inputSchema":{"type":"object","properties":{"case_id":{"type":"string"}},"required":["case_id"]},
          "annotations":{"idempotentHint":False,"openWorldHint":False,"destructiveHint":False}}
    return {"schema":1,"evidence_level":"source_contract_loopback_mock","thread_id":TARGET,
        "mcp_status_request":{"method":"mcpServerStatus/list","params":{"threadId":TARGET,"serverName":"mock-only","detail":"toolsAndAuthOnly"}},
        "mcp_status_response":{"data":[{"name":"mock-only","runtimeStatus":"connected","toolsError":None,"tools":{"record_effect":tool}}]},
        "native_tools":copy.deepcopy(TOOLS),"introspection_thread_id":TARGET,
        "introspection_history":[{"type":"custom_tool_call","name":"exec","call_id":"mock-only","status":"completed","input":f.discovery_program()},
            {"type":"custom_tool_call_output","call_id":"mock-only","output":json.dumps({"qa":f.DISCOVERY,"entries":[{
                "name":"mcp__mock_counter__record_effect","description":tool["description"],"callable":True}]})}],
        "raw_evidence":[]}
async def main():
    paths=[HERE/"codemode_fault.py",Path(__file__),HERE/"fault_server.py",HERE/"synthetic_reasoning.py",
           Path(protocol.__file__),Path(f.codex_app_deepseek.bridge.__file__)]
    before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    evidence=f.REPO/".evidence"/("final-input-selftest-"+uuid.uuid4().hex[:8]);evidence.mkdir()
    home=evidence/"state";f.initialize(home)
    h=f.CodeModeHarness(home,allow_loopback_test_binding=True,finite_recovery=True)
    checks={};wire={};cases={}
    try:
        for kind in sorted(f.AUTHORITY_KINDS):
            try:h.arm(kind,TARGET)
            except ValueError:pass
            else:raise AssertionError("Effect fault accepted without discovery")
        checks["new_effect_cases_require_discovery"]=True
        h.bind_evidence(mock_binding())
        deadline=h.deadline;limits=(home/"limits.json").read_bytes()
        ready=await h.start();url=ready["gateway_url"]+"/responses"
        async with aiohttp.ClientSession(trust_env=False) as client:
            async def request(payload=None,thread=TARGET,bearer=None):
                headers={"Authorization":"Bearer "+(bearer or h.fixture["gateway_token"]),"thread-id":thread}
                async with client.post(url,json=payload or body(),headers=headers) as r:
                    return r.status,await r.text()
            async def raw_handler(request):
                return await h.synthetic(request,{"tools":f.Registry().tools(TOOLS)},h.plan)
            raw_app=web.Application();raw_app.router.add_post("/",raw_handler)
            async with TestServer(raw_app) as raw_server:
                for kind in sorted(f.AUTHORITY_KINDS):
                    arm=h.arm(kind,TARGET);case=arm["case_id"];cases[kind]=case
                    async with client.post(raw_server.make_url("/")) as response:
                        raw=frames(await response.text());assert response.status==200
                    wire[kind]=raw
                    added=next(x["item"] for x in raw if x["type"]=="response.output_item.added" and x["item"]["type"]=="function_call")
                    preview=next(x["arguments"] for x in raw if x["type"]=="response.function_call_arguments.done")
                    done=next(x["item"] for x in raw if x["type"]=="response.output_item.done" and x["item"]["type"]=="function_call")
                    assert added["id"]==done["id"] and added["call_id"]==done["call_id"] and added["name"]==done["name"]
                    preview_source=json.loads(preview)["input"]
                    assert preview_source==f.preview_effect_program(h.binding["nested_mcp_tool_name"],case)
                    assert preview_source.count("await tools[name](")==2
                    if kind=="codemode_preview_revision":
                        final=f.effect_program(h.binding["nested_mcp_tool_name"],case)
                        assert json.loads(done["arguments"])=={"input":final} and final.count("await tools[name](")==1
                    else:assert json.loads(done["arguments"])=={"input":None}
                    assert raw[-1]["type"]=="response.completed" and raw[-1]["response"]["output"][-1]==done
                    assert h.ledger.count(case)==0
                    checks[kind+"_wire_identity_and_final_authority_shape"]=True
                    prior=h.requests
                    assert (await request(bearer="wrong-local-token"))[0]==401 and h.requests==prior
                    assert (await request(thread="foreign-thread"))[0]==409 and h.plan.remaining==1
                    missing=body();missing["tools"]=[]
                    assert (await request(missing))[0]==409 and h.plan.remaining==1
                    status,text=await request();converted=frames(text);assert status==200
                    complete=[x["item"] for x in converted if x["type"]=="response.output_item.done" and x.get("item",{}).get("type")=="custom_tool_call"]
                    if kind=="codemode_preview_revision" and args.expect_final_authority:
                        assert len(complete)==1 and complete[0]["input"]==final
                        assert [x["input"] for x in converted if x["type"]=="response.custom_tool_call_input.done"]==[final]
                        assert converted[-1]["type"]=="response.completed"
                    else:
                        assert not complete and converted[-1]["type"]=="response.failed"
                        if args.expect_final_authority:
                            assert not any(x["type"]=="response.custom_tool_call_input.done" for x in converted)
                    assert h.ledger.count(case)==0 and h.live_calls==0
                    checks[kind+"_selected_bridge_outcome"]=True
                    status,text=await request();converted=frames(text)
                    assert status==200 and converted[-1]["type"]=="response.completed"
                    assert h.plan.kind=="synthetic_success" and h.plan.fired==1 and h.plan.remaining==0
                    assert (await request())[0]==503 and h.ledger.count(case)==0 and h.live_calls==0
                    assert h.deadline==deadline and (home/"limits.json").read_bytes()==limits
                    checks[kind+"_one_explicit_synthetic_recovery_no_budget_reset"]=True
        final_summary=h.summary()
    finally:await h.close()
    # Fresh unstarted mock instances isolate clock/cap guards; no real run is reset.
    for kind in sorted(f.AUTHORITY_KINDS):
        for fence in ("deadline","processed"):
            guard_home=evidence/(kind+"-"+fence);f.initialize(guard_home)
            guard=f.CodeModeHarness(guard_home,allow_loopback_test_binding=True,finite_recovery=True)
            try:
                guard.bind_evidence(mock_binding());guard.arm(kind,TARGET)
                plan=guard.plan;plan.remaining=0;plan.fired=1
                if fence=="deadline":guard.deadline=time.time()-1
                else:guard.requests=64
                request=SimpleNamespace(headers={"Authorization":"Bearer "+guard.fixture["upstream_token"],"X-CDAG-QA-Thread-ID":TARGET})
                assert await guard.finite_recovery(request) is None and guard.plan is plan and plan.kind==kind
                assert guard.ledger.count(plan.case_id)==0
            finally:await guard.close()
    checks["new_case_deadline_and_cap_refuse_recovery_without_rearming_unit_only"]=True
    after={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    assert before==after
    report={"outcome":"prepared_source_and_loopback_only","checks":checks,"selected_protocol":str(protocol.__file__),
        "selected_protocol_sha256":before[str(Path(protocol.__file__))],"expect_final_authority":args.expect_final_authority,
        "c110_expected_reject_is_not_candidate_pass":not args.expect_final_authority,"summary":final_summary,
        "source_before":before,"source_after":after,"scope":{"actual_App":False,"native_invocations":0,"model_requests":0,
            "host_counter_calls":0,"WinCred_reader":False,"native_binding":"mock_only"},
        "actual_later_assertions":{"valid_case":"FINAL completed call/source and matching unpredictable nonce; count1. Preview-only2/both3 must fail.",
            "invalid_case":"failed stream and no authoritative completed call/effect0; preserve original failure before recovery.",
            "recovery":"Synthetic only, never DeepSeek business/task success."},
        "limits":"Original900s/64processed/256events/64effects; no nonce/deadline/counter reset."}
    (evidence/"wire-samples.json").write_text(json.dumps(wire,indent=2))
    (evidence/"result.json").write_text(json.dumps(report,indent=2))
    print(json.dumps({"outcome":report["outcome"],"checks":len(checks),"root":str(evidence),
                      "selected_protocol_sha256":report["selected_protocol_sha256"],"expect_final_authority":args.expect_final_authority}))
if __name__=="__main__":asyncio.run(main())
