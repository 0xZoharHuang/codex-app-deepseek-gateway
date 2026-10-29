"""Self-test uses production bridge, loopback mocks and SDK MCP; never real models."""
from __future__ import annotations
import asyncio, hashlib, importlib.util, json, os, pathlib, shutil, sys, time, uuid
import aiohttp
from aiohttp import web
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

HERE=pathlib.Path(__file__).resolve().parent
spec=importlib.util.spec_from_file_location("cdag_fault_fixture",HERE/"fault_server.py")
f=importlib.util.module_from_spec(spec);sys.modules[spec.name]=f;spec.loader.exec_module(f)
ROOT=HERE.parents[2]
TARGET="target-thread"
FROZEN=[HERE/"fault_server.py",HERE/"synthetic_reasoning.py",HERE/"selftest.py",ROOT/"src/codex_app_deepseek/bridge.py",
        ROOT/"src/codex_app_deepseek/protocol_state.py"]
def hashes():return {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in FROZEN}
def frames(text):return [json.loads(line[6:]) for line in text.splitlines() if line.startswith("data: ") and line[6:]!="[DONE]"]
def body(aux=False):
    return {"model":f.MODEL,"stream":True,"prompt_cache_key":"deliberately-shared-cache-affinity",
            "input":"AUXILIARY_TEST_MARKER" if aux else "DO_NOT_LOG_PROMPT_SECRET_MARKER",
            "tools":[{"type":"function","name":name,"description":description,
                      "parameters":{"type":"object","properties":{"case_id":{"type":"string"}},"required":["case_id"],"additionalProperties":False}}
                     for name,description in [("record_effect",f.MARKER),("other_tool","Not the counter")]]}

async def main():
    before_hashes=hashes()
    home=ROOT/".evidence"/("fault-selftest-"+uuid.uuid4().hex[:8]);f.initialize(home)
    credential_calls=[]
    def fake_key():
        credential_calls.append(True);return "synthetic-only-not-a-WinCred-value"
    harness=f.FaultHarness(home,live_reader=fake_key)
    results={};mock_runner=None;harness_closed=False;completed=False;started=time.time()
    try:
        ready=await harness.start()
        token=harness.fixture["gateway_token"]
        ctrl={"Authorization":"Bearer "+harness.fixture["control_token"]}
        async with aiohttp.ClientSession(trust_env=False) as client:
            async def request(payload=None,thread_id=TARGET):
                headers={"Authorization":"Bearer "+token,"session-id":"shared-session-affinity"}
                if thread_id is not None:headers["thread-id"]=thread_id
                async with client.post(ready["gateway_url"]+"/responses",json=payload or body(),headers=headers) as response:
                    return response.status,dict(response.headers),await response.text()
            async def control(command):
                async with client.post(ready["control_url"],json=command,headers=ctrl) as response:
                    result=await response.json();assert response.status==200,result;return result
            assert not harness.live_enabled
            harness.arm("http_429",TARGET)
            status,_,_=await request(body(),thread_id="another-thread")
            assert status==409 and harness.plan.remaining==1 and harness.plan.fired==0
            status,_,_=await request(body(),thread_id=None)
            assert status==400 and harness.plan.remaining==1
            results["native_thread_header_not_cache_affinity_filter"]="passed"
            for code in (429,503,401):
                harness.arm("http_"+str(code),TARGET);previous=harness.requests
                status,headers,text=await request()
                assert status==code and harness.requests==previous+1
                assert harness.state.list_receipts()[-1]["status"]=="upstream_error"
                if code==429:assert headers["Retry-After"]=="1"
            results["synthetic_http_failures"]="passed"
            harness.arm("text_cut",TARGET);status,_,text=await request();events=frames(text)
            assert status==200 and events[-1]["type"]=="response.failed"
            assert any(x["type"]=="response.output_text.delta" and x.get("delta")=="Synthetic partial output" for x in events)
            assert not any(x["type"]=="response.completed" for x in events)
            results["truncated_text_never_completed"]="passed"
            for kind,want_done in [("tool_incomplete_cut",False),("tool_done_cut",True)]:
                plan=harness.arm(kind,TARGET);status,_,text=await request();events=frames(text)
                assert status==200 and events[-1]["type"]=="response.failed"
                done=[e for e in events if e["type"]=="response.output_item.done" and e.get("item",{}).get("type")=="function_call"]
                assert bool(done)==want_done
                if want_done:
                    completed_item=done[0]["item"];case=plan["case_id"]
                    assert completed_item["name"]=="record_effect"
                    assert json.loads(completed_item["arguments"])=={"case_id":case}
                    assert completed_item["call_id"].startswith("fixture-call-")
                    assert completed_item["status"]=="completed"
                assert not any(x["type"]=="response.completed" for x in events)
                assert harness.ledger.count(plan["case_id"])==0
            results["complete_vs_incomplete_tool_exposure"]="passed_protocol_only_no_native_tool_execution"
            harness.arm("tool_done_cut",TARGET)
            unknown=body()
            for tool in unknown["tools"]:tool["description"]="not a fixture counter"
            status,_,_=await request(unknown)
            assert status==409 and harness.plan.fired==0 and harness.plan.remaining==1
            results["arbitrary_tool_synthesis_refused"]="passed"
            parameters=StdioServerParameters(command=sys.executable,
                args=["-I","-X","utf8",str(HERE/"fault_server.py"),"counter","--state-dir",str(home)],
                env={k:v for k,v in os.environ.items() if not k.upper().startswith(("CODEX_","OPENAI_","OPENROUTER_"))})
            async with stdio_client(parameters) as (read,write):
                async with ClientSession(read,write) as session:
                    await session.initialize();tools=await session.list_tools()
                    counter=next(t for t in tools.tools if t.name=="record_effect")
                    assert counter.annotations.idempotent_hint is False
                    one=await session.call_tool("record_effect",{"case_id":case})
                    two=await session.call_tool("record_effect",{"case_id":case})
                    bad=await session.call_tool("record_effect",{"case_id":"unregistered"})
                    assert one.structured_content["effect_count"]==1 and two.structured_content["effect_count"]==2 and bad.is_error
            assert harness.ledger.count(case)==2
            results["counter_preserves_duplicate_evidence"]="passed_two_calls_count_two"
            try:harness.arm("after_effect_503","another-thread",case_id=case)
            except ValueError:pass
            else:raise AssertionError("Old effects were rebound to another native thread")
            harness.arm("after_effect_503",TARGET,case_id=case)
            results["case_cannot_cross_native_threads"]="passed"
            status,_,_=await request();assert status==503 and harness.plan.fired==0
            continuation=body()
            continuation["input"]=[{"role":"user","content":"fixture continuation"},completed_item,
                {"type":"function_call_output","call_id":completed_item["call_id"],
                 "output":json.dumps({"fixture":True,"case_id":case,"effect_count":2})}]
            negatives=[]
            value=json.loads(json.dumps(continuation));value["input"][1]["name"]="other_tool";negatives.append(value)
            value=json.loads(json.dumps(continuation));value["input"][1]["arguments"]=json.dumps({"case_id":"different-case"});negatives.append(value)
            value=json.loads(json.dumps(continuation));value["input"].pop(1);negatives.append(value)
            value=json.loads(json.dumps(continuation));value["input"][-1]["call_id"]="different-call";negatives.append(value)
            for value in negatives:
                assert harness.case_result_call_ids(value,case)==[]
                async with client.post(ready["control_url"].replace("/control","/responses"),json=value,
                    headers={"Authorization":"Bearer "+harness.fixture["upstream_token"],"X-CDAG-QA-Thread-ID":TARGET}) as response:
                    assert response.status==503;await response.read()
                assert harness.plan.fired==0 and harness.plan.remaining==1
            status,_,_=await request(continuation)
            assert status==503 and harness.plan.fired==1 and harness.ledger.count(case)==2
            status,_,_=await request(continuation)
            assert status==503 and not credential_calls and harness.ledger.count(case)==2
            results["after_effect_requires_exact_counter_call_and_result"]="passed_four_negative_cases"
            results["live_default_disabled"]="passed"

            # All pass-through tests below still use a LOCAL synthetic transport.
            mock_calls=[];mock_identities=[];variant="valid"
            entered=asyncio.Event();release=asyncio.Event()
            async def mock_upstream(request):
                incoming=await request.json()
                auxiliary="AUXILIARY_TEST_MARKER" in json.dumps(incoming.get("input"))
                mock_calls.append({"model":incoming.get("model"),"provider":incoming.get("provider")})
                if variant=="held_target" and not auxiliary:
                    entered.set();await asyncio.wait_for(release.wait(),5)
                rid="synthetic-mock-"+uuid.uuid4().hex
                stream=[{"type":"response.created","response":{"id":rid,"status":"in_progress","output":[]}}]
                if harness.plan and harness.plan.kind.startswith("live_tool_") and not auxiliary:
                    arguments=json.dumps({"case_id":"different-case" if variant=="wrong_case" else harness.plan.case_id})
                    name="other_tool" if variant=="wrong_name" else "record_effect"
                    item={"id":"mock-item-"+uuid.uuid4().hex,"type":"function_call","call_id":"mock-call-"+uuid.uuid4().hex,
                          "name":name,"arguments":"","status":"in_progress"}
                    done={**item,"arguments":arguments,"status":"completed"}
                    if variant=="wrong_type":
                        item.update(type="message",role="assistant",content=[])
                        done={**item,"arguments":arguments,"status":"completed","content":[{"type":"output_text","text":"synthetic non-tool","annotations":[]}]}
                    stream.append({"type":"response.output_item.added","output_index":0,"item":item})
                    if variant!="wrong_type":stream.append({"type":"response.function_call_arguments.delta","item_id":item["id"],"delta":arguments})
                    stream.append({"type":"response.output_item.done","output_index":0,"item":done})
                    output=[done];mock_identities.append((rid,item["id"],item["call_id"]))
                else:
                    output=[{"id":"mock-message","type":"message","role":"assistant","status":"completed",
                             "content":[{"type":"output_text","text":"Local synthetic stand-in only","annotations":[]}]}]
                stream.append({"type":"response.completed","response":{"id":rid,"status":"completed","output":output,
                    "usage":{"input_tokens":1,"output_tokens":1}}})
                return web.Response(body=b"".join(f.encode_sse(e) for e in stream),content_type="text/event-stream")
            mock=web.Application();mock.router.add_post("/responses",mock_upstream)
            mock_runner=web.AppRunner(mock,access_log=None);await mock_runner.setup()
            site=web.TCPSite(mock_runner,"127.0.0.1",0);await site.start()
            mock_url=f"http://127.0.0.1:{site._server.sockets[0].getsockname()[1]}/responses"
            real_client=harness.client
            class LocalOnlyRelay:
                def post(self,url,**kwargs):
                    assert url==f.OPENROUTER_RESPONSES and kwargs.pop("proxy") is None
                    assert kwargs["allow_redirects"] is False
                    assert kwargs["headers"]["Authorization"]=="Bearer synthetic-only-not-a-WinCred-value"
                    assert not any(k.lower().startswith("x-cdag-qa") for k in kwargs["headers"])
                    return real_client.post(mock_url,**kwargs)
                async def close(self):await real_client.close()
            harness.client=LocalOnlyRelay()
            await control({"action":"enable_live"})
            for kind,want_done in [("live_tool_incomplete_cut",False),("live_tool_done_cut",True)]:
                plan=harness.arm(kind,TARGET)
                status,_,text=await request();events=frames(text)
                assert status==200 and events[-1]["type"]=="response.failed" and harness.plan.fired==1
                done=[e for e in events if e["type"]=="response.output_item.done" and e.get("item",{}).get("type")=="function_call"]
                assert bool(done)==want_done
                if want_done:
                    assert done[0]["item"]["name"]=="record_effect"
                    assert json.loads(done[0]["item"]["arguments"])=={"case_id":plan["case_id"]}
                cut=next(e for e in reversed(harness.events) if e["event"]=="passthrough_stream_cut")
                rid,item_id,call_id=mock_identities[-1]
                assert (cut["generation_id_sha256"],cut["item_id_sha256"],cut["call_id_sha256"])==tuple(f.digest(x) for x in (rid,item_id,call_id))
                assert cut["frame_type"]==("response.output_item.done" if want_done else "response.function_call_arguments.delta")
                assert harness.ledger.count(plan["case_id"])==0
            results["live_cut_identity_receipts"]="passed_local_synthetic_transport_only"
            for variant in ("wrong_type","wrong_name","wrong_case"):
                harness.arm("live_tool_done_cut",TARGET)
                status,_,text=await request()
                assert status==200 and frames(text)[-1]["type"]=="response.completed"
                assert harness.plan.fired==0 and harness.plan.remaining==1
            results["wrong_live_type_name_case_not_cut"]="passed"
            variant="held_target";harness.arm("live_tool_done_cut",TARGET)
            target=asyncio.create_task(request())
            try:
                await asyncio.wait_for(entered.wait(),5)
                status,_,text=await request(body(aux=True),thread_id="auxiliary-thread")
                assert status==200 and frames(text)[-1]["type"]=="response.completed"
                assert harness.plan.fired==0 and harness.plan.remaining==1
            finally:release.set()
            status,_,text=await target
            assert status==200 and frames(text)[-1]["type"]=="response.failed" and harness.plan.fired==1
            results["concurrent_auxiliary_same_cache_group_not_faulted"]="passed"
            variant="valid";harness.arm("passthrough",TARGET,case_id=case)
            status,_,text=await request(continuation)
            assert status==200 and frames(text)[-1]["type"]=="response.completed" and harness.ledger.count(case)==2
            assert len(mock_calls)==8 and len(credential_calls)==8
            assert all(x["model"]==f.MODEL and x["provider"]["allow_fallbacks"] is False for x in mock_calls)
            status=await control({"action":"status"});assert status["live_calls"]==8 and status["actual_App_acceptance"] is False
            events_text=(home/"events.json").read_text()
            for forbidden in [token,harness.fixture["upstream_token"],harness.fixture["control_token"],
                              "synthetic-only-not-a-WinCred-value","DO_NOT_LOG_PROMPT_SECRET_MARKER"]:
                assert forbidden not in events_text
            results["redacted_fixture_evidence"]="passed"
            await control({"action":"stop"});assert harness.stop.is_set()
            old_port=int(ready["gateway_url"].split(":")[-1].split("/")[0])
            before_events=len(harness.events);before_requests=harness.requests
            await harness.close();harness_closed=True
            restarted=f.FaultHarness(home,live_reader=fake_key)
            try:
                recovered=await restarted.start(bridge_port=old_port)
                assert recovered["gateway_url"]==ready["gateway_url"]
                assert restarted.plan is None and restarted.live_enabled is False
                assert restarted.live_calls==8 and restarted.requests==before_requests
                assert len(restarted.events)==before_events and restarted.ledger.count(case)==2
                try:restarted.arm("after_effect_503","another-thread",case_id=case)
                except ValueError:pass
                else:raise AssertionError("Persisted case ownership was lost")
                results["same_port_restart_preserves_evidence_without_replay"]="passed"
            finally:await restarted.close()
            completed=True
    finally:
        if not harness_closed:await harness.close()
        if mock_runner:await mock_runner.cleanup()
        def readonly(action,path,error):os.chmod(path,0o700);action(path)
        shutil.rmtree(home,onerror=readonly)
        after_hashes=hashes();stable=before_hashes==after_hashes
        report={"status":"passed" if completed and stable else "failed_or_invalid","fixture_selftest":True,
                "elapsed_seconds":time.time()-started,"results":results,"real_DeepSeek_requests":0,
                "real_OpenRouter_requests":0,"WinCred_read":False,"App_or_native_started":False,
                "App_acceptance":False,"owned_fixture_removed":not home.exists(),"source_unchanged":stable,
                "source_hashes_before":before_hashes,"source_hashes_after":after_hashes}
        (ROOT/".evidence/app-fault-fixture-selftest.json").write_text(json.dumps(report,indent=2),encoding="utf8")
        assert stable,"Selftest source inputs changed during execution; result invalid"

if __name__=="__main__":asyncio.run(main())
