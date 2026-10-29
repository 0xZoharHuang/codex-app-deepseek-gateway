"""Source-contract and loopback checks only. Never launches native/App/model."""
from __future__ import annotations
import argparse,asyncio,copy,hashlib,importlib.util,json,os,sys,time
from pathlib import Path
import uuid
import aiohttp
from mcp import ClientSession,StdioServerParameters
from mcp.client.stdio import stdio_client
HERE=Path(__file__).parent
sys.path.insert(0,str(HERE));import codemode_fault as f
spec=importlib.util.spec_from_file_location("aa3_selftest_helpers",HERE/"selftest.py")
orig=importlib.util.module_from_spec(spec);spec.loader.exec_module(orig)
TARGET="source-contract-loopback-thread"
parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("--native-source",type=Path,required=True,help="Explicit fixed native source for grammar-only mock binding")
SOURCE=parser.parse_args().native_source.resolve()
SPEC=SOURCE/"codex-rs/core/src/tools/code_mode/execute_spec.rs"
grammar=SPEC.read_text().split('r#"')[1].split('"#')[0]
NATIVE_TOOLS=[{"type":"custom","name":"exec","description":
    "Source-derived test shape only. Run JavaScript with tools and ALL_TOOLS; text prints output.",
    "format":{"type":"grammar","syntax":"lark","definition":grammar}}]
def body():
    return {"model":f.base.MODEL,"stream":True,"input":"PRIVATE_SOURCE_CONTRACT_TEST",
        "prompt_cache_key":"deliberately-not-a-thread","tools":copy.deepcopy(NATIVE_TOOLS)}
def frozen():
    paths=[HERE/"codemode_fault.py",HERE/"selftest_codemode.py",HERE/"image_trace.py",HERE/"fault_server.py",HERE/"synthetic_reasoning.py",HERE/"selftest.py",f.REPO/"src/codex_app_deepseek/bridge.py",f.REPO/"src/codex_app_deepseek/protocol_state.py"]
    return {str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
def simulated_binding(tool):
    return {"schema":1,"evidence_level":"source_contract_loopback_mock","thread_id":TARGET,
        "mcp_status_request":{"method":"mcpServerStatus/list","params":{"threadId":TARGET,"serverName":"source_contract_counter","detail":"toolsAndAuthOnly"}},
        "mcp_status_response":{"data":[{"name":"source_contract_counter","runtimeStatus":"connected","toolsError":None,"tools":{"record_effect":tool}}]},
        "native_tools":copy.deepcopy(NATIVE_TOOLS),"introspection_thread_id":TARGET,
        "introspection_history":[{"type":"custom_tool_call","name":"exec","call_id":"mock-discovery-only","status":"completed","input":f.discovery_program()},
            {"type":"custom_tool_call_output","call_id":"mock-discovery-only","output":json.dumps({"qa":f.DISCOVERY,
                "entries":[{"name":"mcp__source_contract_counter__record_effect",
                    "description":tool["description"]+" record_effect(case_id: string)", "callable":True}]})}],
        "raw_evidence":[]}
async def finite_controls(evidence,tool):
    """Real HTTP/SSE fixture checks; never native or model execution."""
    home=evidence/"finite-loopback-state";f.initialize(home)
    h=f.CodeModeHarness(home,allow_loopback_test_binding=True,finite_recovery=True)
    checks={}
    try:
        binding=simulated_binding(tool)
        binding.pop("mcp_status_request");binding.pop("mcp_status_response")
        binding.update(catalog_source="independent_standard_MCP_tools_list",
                       server_name="source_contract_counter",
                       server_tools_list_response={"tools":[tool]})
        result=h.bind_evidence(binding)
        assert result["catalog_source"]=="independent_standard_MCP_tools_list"
        assert not result["catalog_native_RPC_envelope"] and not result["actual_App_acceptance"]
        for name,mutate in [
            ("unknown_source",lambda x:x.update(catalog_source="fabricated_App_RPC")),
            ("wrong_thread",lambda x:x.update(introspection_thread_id="other-native-thread")),
            ("duplicate_descriptor",lambda x:x["server_tools_list_response"]["tools"].append(copy.deepcopy(tool))),
            ("bad_schema",lambda x:x["server_tools_list_response"]["tools"][0]["inputSchema"].update(required=[])),
            ("idempotent_claim",lambda x:x["server_tools_list_response"]["tools"][0]["annotations"].update(idempotentHint=True)),
        ]:
            wrong=copy.deepcopy(binding);mutate(wrong);before=copy.deepcopy(h.binding)
            try:h.bind_evidence(wrong)
            except ValueError:pass
            else:raise AssertionError("Invalid independent catalog accepted: "+name)
            assert h.binding==before
        checks["independent_catalog_explicit_provenance_and_five_negative_guards"]=True
        plainhome=evidence/"strict-default-state";f.initialize(plainhome);plain=f.CodeModeHarness(plainhome)
        try:
            try:plain.bind_evidence(binding)
            except ValueError:pass
            else:raise AssertionError("Mock capability accepted without selftest opt-in")
            assert not plain.finite_recovery_enabled and plain.live_reader is None and not plain.live_enabled
        finally:await plain.close()
        checks["new_serve_defaults_keep_native_binding_and_live_disabled"]=True
        ready=await h.start();url=ready["gateway_url"]+"/responses"
        async with aiohttp.ClientSession(trust_env=False) as client:
            async def request(thread=TARGET,bearer=None,payload=None):
                async with client.post(url,json=body() if payload is None else payload,headers={
                    "Authorization":"Bearer "+(h.fixture["gateway_token"] if bearer is None else bearer),
                    "thread-id":thread}) as response:
                    return response.status,await response.text()
            for kind in ["codemode_discovery","tool_incomplete_cut","tool_done_cut"]:
                case=h.arm(kind,TARGET)["case_id"]
                unconsumed=h.plan
                strict_initial=body();strict_initial["text"]={"format":{"type":"json_schema","name":"same_root_auxiliary_control","schema":{"type":"object"},"strict":True}}
                noexec_initial=body();noexec_initial["tools"]=[]
                none_initial=body();none_initial["tool_choice"]="none"
                for auxiliary in [strict_initial,noexec_initial,none_initial]:
                    assert (await request(payload=auxiliary))[0]==409
                    assert h.plan is unconsumed and h.plan.remaining==1 and h.plan.fired==0
                status,raw=await request();frames=orig.frames(raw)
                assert status==200
                done=[e for e in frames if e["type"]=="response.output_item.done" and e.get("item",{}).get("type")=="custom_tool_call"]
                if kind=="codemode_discovery":assert len(done)==1 and frames[-1]["type"]=="response.completed"
                elif kind=="tool_incomplete_cut":assert not done and frames[-1]["type"]=="response.failed"
                else:assert len(done)==1 and frames[-1]["type"]=="response.failed"
                # Exact actual incident: SAME root structured auxiliary with no
                # exec must not allocate/consume the pending CodeMode recovery.
                original_plan=h.plan;transitions_before=sum(e["event"]=="finite_recovery_transition" for e in h.events)
                strict_aux=body();strict_aux["tools"]=[]
                strict_aux["text"]={"format":{"type":"json_schema","name":"same_root_auxiliary_control",
                    "schema":{"type":"object","properties":{"ok":{"type":"boolean"}},"required":["ok"],"additionalProperties":False},"strict":True}}
                no_exec=body();no_exec["tools"]=[]
                strict_with_exec=copy.deepcopy(strict_aux);strict_with_exec["tools"]=copy.deepcopy(NATIVE_TOOLS)
                choice_none=body();choice_none["tool_choice"]="none"
                for auxiliary in [strict_aux,no_exec,strict_with_exec,choice_none]:
                    assert (await request(payload=auxiliary))[0] in {409,503}
                    assert h.plan is original_plan and h.plan.fired==1 and h.plan.remaining==0
                    assert sum(e["event"]=="finite_recovery_transition" for e in h.events)==transitions_before
                # A still active auxiliary request at the existing global guard
                # must delay the HTTP path, not throw the old recovery-arm 500.
                h.inflight=1
                async def release_auxiliary():
                    await asyncio.sleep(.08);h.inflight=0
                release=asyncio.create_task(release_auxiliary())
                status,raw=await request();await release
                frames=orig.frames(raw);assert status==200 and frames[-1]["type"]=="response.completed"
                assert frames[-1]["response"]["output"][-1]["content"][0]["text"]=="Synthetic fixture recovery only."
                assert h.plan.kind=="synthetic_success" and h.plan.fired==1
                assert h.ledger.count(case)==0 and h.live_calls==0
            checks["three_original_boundaries_then_one_recovery_wait_for_auxiliary_guard"]=True
            checks["same_root_strict_noexec_and_choice_none_do_not_allocate_recovery"]=True
            checks["original_CodeMode_slot_requires_same_MAIN_profile_before_consumption"]=True
            # Simulate the narrow interval between MAIN rearm and consumption.
            race_case=h.arm("codemode_discovery",TARGET)["case_id"]
            assert (await request())[0]==200
            normalized=body();normalized["tools"]=f.Registry().tools(copy.deepcopy(NATIVE_TOOLS))
            class UpstreamMain:
                headers={"Authorization":"Bearer "+h.fixture["upstream_token"],"X-CDAG-QA-Thread-ID":TARGET}
                async def json(self):return normalized
            assert await h.finite_recovery(UpstreamMain()) is None
            allocated=h.plan;assert allocated.kind=="synthetic_success" and allocated.fired==0 and allocated.remaining==1
            pending_requests=h.requests
            assert (await request(bearer="wrong-local-token"))[0]==401 and h.requests==pending_requests
            assert (await request(thread="foreign-allocated-slot"))[0]==409
            assert h.plan is allocated and allocated.fired==0 and allocated.remaining==1
            for auxiliary in [strict_aux,no_exec,choice_none]:
                assert (await request(payload=auxiliary))[0]==503
                assert h.plan is allocated and h.plan.fired==0 and h.plan.remaining==1
            assert (await request())[0]==200 and h.plan.fired==1 and h.plan.remaining==0
            assert (await request())[0]==503 and h.ledger.count(race_case)==0
            checks["allocated_slot_race_preserves_aux_then_MAIN_once"]=True
            # Explicit manual recovery for the same consumed CodeMode case also
            # retains one-slot and request-role protection.
            manual_case=h.arm("codemode_invalid_final",TARGET)["case_id"]
            assert (await request())[0]==200
            original_plan=h.plan
            try:h.arm("synthetic_success",TARGET,case_id=manual_case,repeat=2)
            except ValueError:pass
            else:raise AssertionError("Manual CodeMode recovery accepted two slots")
            assert h.plan is original_plan
            h.arm("synthetic_success",TARGET,case_id=manual_case,repeat=1);manual_plan=h.plan
            try:h.arm("synthetic_success",TARGET,case_id=manual_case,repeat=1)
            except ValueError:pass
            else:raise AssertionError("Allocated same-case CodeMode recovery rearmed")
            assert h.plan is manual_plan and h.plan.remaining==1
            assert (await request(payload=strict_aux))[0]==503 and h.plan is manual_plan and h.plan.remaining==1
            assert (await request())[0]==200 and h.plan.remaining==0 and h.ledger.count(manual_case)==0
            try:h.arm("synthetic_success",TARGET,case_id=manual_case,repeat=1)
            except ValueError:pass
            else:raise AssertionError("Consumed same-case CodeMode recovery rearmed")
            assert h.plan is manual_plan and h.plan.remaining==0
            assert (await request())[0]==503
            checks["manual_CodeMode_recovery_role_guard_one_slot"]=True
            before=h.requests;transitions=sum(e["event"]=="finite_recovery_transition" for e in h.events)
            assert transitions==4
            assert (await request(thread="unarmed-foreign-thread"))[0]==409
            assert (await request(bearer="wrong-local-token"))[0]==401
            assert h.requests==before+1 and sum(e["event"]=="finite_recovery_transition" for e in h.events)==4
            checks["auth_and_foreign_thread_cannot_trigger_recovery"]=True
            case=h.arm("tool_incomplete_cut",TARGET)["case_id"]
            assert (await request())[0]==200
            h.inflight=1
            try:
                status,raw=await request()
                assert status==503 and "waiting for in-flight" in raw
                assert h.plan.kind=="tool_incomplete_cut" and h.plan.fired==1
                assert h.ledger.count(case)==0
            finally:h.inflight=0
            assert (await request())[0]==200
            checks["bounded_busy_timeout_preserves_original_plan_and_no_effect"]=True
        normalized=body();normalized["tools"]=f.Registry().tools(copy.deepcopy(NATIVE_TOOLS));declared=f.exec_tool(normalized)
        assert declared and f._code_mode_recovery_main(normalized)
        for choice in ["auto","required",{"type":"function","name":declared["name"]}]:
            positive=copy.deepcopy(normalized);positive["tool_choice"]=choice;assert f._code_mode_recovery_main(positive)
        negatives=[]
        for field,value in [("text",{"format":{"type":"json_schema"}}),("text",{"format":{"type":"json_object"}}),
                            ("text",{"format":"malformed"}),("text",[]),("response_format",{"type":"json_schema"}),
                            ("response_format",{"type":"json_object"}),("response_format","malformed"),
                            ("tools",None),("tools",[]),("tool_choice","none"),
                            ("tool_choice",{"type":"function","name":"foreign_function"})]:
            rejected=copy.deepcopy(normalized);rejected[field]=value;negatives.append(rejected)
        malformed_description=copy.deepcopy(normalized)
        next(t for t in malformed_description["tools"] if t.get("name")==declared["name"])["description"]=None
        duplicate_exec=copy.deepcopy(normalized);duplicate_exec["tools"].append(copy.deepcopy(declared))
        negatives.extend([malformed_description,duplicate_exec])
        assert all(not f._code_mode_recovery_main(value)for value in negatives)
        checks["selector_declared_choice_formats_and_malformed_edge_controls"]=True
        # Fresh unstarted fixtures isolate clock/cap fences; never reset a live
        # run's deadline or counters to exercise these negative controls.
        for recovery_kind in ["codemode_discovery","tool_incomplete_cut","tool_done_cut","codemode_effect","after_effect_503"]:
            for fence in ["deadline","processed"]:
                guard_home=evidence/("recovery-fence-"+recovery_kind+"-"+fence);f.initialize(guard_home)
                guard=f.CodeModeHarness(guard_home,allow_loopback_test_binding=True,finite_recovery=True)
                try:
                    guard.bind_evidence(binding);guard.arm(recovery_kind,TARGET)
                    guard.plan.remaining=0;guard.plan.fired=1;original_guard_plan=guard.plan
                    if fence=="deadline":guard.deadline=time.time()-1
                    else:guard.requests=64
                    class FencedMain:
                        headers={"Authorization":"Bearer "+guard.fixture["upstream_token"],"X-CDAG-QA-Thread-ID":TARGET}
                        async def json(self):return normalized
                    assert await guard.finite_recovery(FencedMain()) is None
                    assert guard.plan is original_guard_plan and guard.plan.kind==recovery_kind
                finally:await guard.close()
        checks["all_CodeMode_kinds_rearm_clock_and_cap_fences"]=True
        # Real HTTP cancellation and peer disconnect must not be relabelled as
        # a synthetic/default HTTP500 when no response headers were prepared.
        original_synthetic=h.synthetic
        started=asyncio.Event();cancelled=asyncio.Event()
        async def held(request,payload,plan):
            if plan.kind=="http_503":
                started.set()
                try:await asyncio.sleep(20)
                except asyncio.CancelledError:
                    cancelled.set();raise
            return await original_synthetic(request,payload,plan)
        h.synthetic=held
        async def wait_cancel_row(previous):
            end=time.monotonic()+3
            while time.monotonic()<end:
                count=h.audit.execute("SELECT COUNT(*) FROM attempts WHERE completion='cancelled' AND headers_prepared=0 AND status IS NULL").fetchone()[0]
                if count>previous:return
                await asyncio.sleep(.02)
            raise AssertionError("Disconnected HTTP attempt was not classified cancelled/unobserved")
        async with aiohttp.ClientSession(trust_env=False) as client:
            h.arm("http_503",TARGET);before_cancel=h.audit.execute("SELECT COUNT(*) FROM attempts WHERE completion='cancelled'").fetchone()[0]
            async def waiting_request():
                async with client.post(url,json=body(),headers={"Authorization":"Bearer "+h.fixture["gateway_token"],"thread-id":TARGET}) as response:
                    return response.status,await response.text()
            task=asyncio.create_task(waiting_request());await asyncio.wait_for(started.wait(),3)
            task.cancel()
            try:await task
            except asyncio.CancelledError:pass
            await asyncio.wait_for(cancelled.wait(),3);await wait_cancel_row(before_cancel)
        started.clear();cancelled.clear();h.arm("http_503",TARGET)
        before_cancel=h.audit.execute("SELECT COUNT(*) FROM attempts WHERE completion='cancelled'").fetchone()[0]
        port=int(ready["gateway_url"].split(":")[2].split("/")[0])
        reader,writer=await asyncio.open_connection("127.0.0.1",port)
        data=json.dumps(body()).encode()
        header=("POST /v1/responses HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Type: application/json\r\n"
                "Authorization: Bearer "+h.fixture["gateway_token"]+"\r\nthread-id: "+TARGET+"\r\nContent-Length: "+str(len(data))+"\r\n\r\n").encode()
        writer.write(header+data);await writer.drain();await asyncio.wait_for(started.wait(),3)
        writer.close();await writer.wait_closed();await asyncio.wait_for(cancelled.wait(),3);await wait_cancel_row(before_cancel)
        assert h.inflight==0 and h.summary()["physical_http_completion_counts"]["cancelled"]==2
        assert h.summary()["physical_http_status_counts"]["unobserved"]==2
        assert h.summary()["physical_http_status_counts"].get("500",0)==0
        checks["HTTP_cancel_and_peer_close_have_no_fabricated500_headers"]=True
        h.synthetic=original_synthetic
        assert h.live_calls==0 and sum(h.ledger.count(r[0]) for r in h.ledger.db.execute("SELECT id FROM cases"))==0
        from importlib.util import spec_from_file_location,module_from_spec
        spec=spec_from_file_location("qa_public_image_summary",HERE/"image_trace.py")
        trace=module_from_spec(spec);spec.loader.exec_module(trace)
        import base64
        raw_png=b"\x89PNG\r\n\x1a\nQA metadata fixture";url="data:image/png;base64,"+base64.b64encode(raw_png).decode()
        summary=trace.image_summary({"input":[{"type":"custom_tool_call_output","call_id":"private-image-call",
            "output":[{"type":"input_image","image_url":url,"detail":"original"}]}]})
        assert summary["scan_complete"] and summary["typed_image_count"]==1
        assert summary["images"][0]["data_sha256"]==hashlib.sha256(raw_png).hexdigest()
        assert url not in json.dumps(summary) and "private-image-call" not in json.dumps(summary)
        fake=trace.image_summary({"input":[{"type":"input_text","text":json.dumps({"type":"input_image","image_url":url})}]})
        assert fake["typed_image_count"]==0 and fake["scan_complete"]
        remote=trace.image_summary({"input":[{"type":"input_image","image_url":"https://example.invalid/PRIVATE-URL.png"}]})
        assert remote["typed_image_count"]==1 and remote["images"][0]["scheme"]=="https"
        assert "PRIVATE-URL" not in json.dumps(remote)
        checks["typed_image_hashes_and_text_negative_never_log_raw_URL_or_call_id"]=True
        return checks
    finally:await h.close()

async def scripted_formats_controls(evidence,tool):
    """Two new wire shapes and real loopback conversion; no native/host tool calls."""
    from aiohttp.test_utils import TestServer
    from codex_deepseek.protocol import Events,ProtocolError
    mock_clock={'description': 'Source-contract mock namespace copied from observed schema; no actual host call.', 'name': 'clock', 'tools': [{'description': 'Pause execution for a specified duration. The sleep ends early when new input arrives for the active turn. Returns the elapsed wall-clock time.', 'name': 'sleep', 'parameters': {'additionalProperties': False, 'properties': {'duration_ms': {'description': 'How long to sleep in milliseconds. Must be between 1 and 43200000.', 'type': 'number'}}, 'required': ['duration_ms'], 'type': 'object'}, 'strict': False, 'type': 'function'}], 'type': 'namespace'}
    native=copy.deepcopy(NATIVE_TOOLS)+[mock_clock]
    home=evidence/"scripted-formats-state";f.initialize(home)
    h=f.CodeModeHarness(home,allow_loopback_test_binding=True,finite_recovery=True)
    checks={};wire={}
    try:
        for kind in f.FUNCTION_KINDS|f.POST_COMPLETED_KINDS:
            try:h.arm(kind,TARGET)
            except ValueError:pass
            else:raise AssertionError("New format armed without binding")
        binding=simulated_binding(tool);h.bind_evidence(binding)
        try:h.arm("native_function_preview_revision",TARGET)
        except ValueError:pass
        else:raise AssertionError("Ordinary function armed without actual declared clock schema")
        binding["native_tools"]=native;h.bind_evidence(binding)
        assert h.binding.get("clock_sleep")
        ready=await h.start();url=ready["gateway_url"]+"/responses"
        def payload():
            value=body();value["tools"]=copy.deepcopy(native);return value
        async with aiohttp.ClientSession(trust_env=False)as client:
            async def request(value=None,bearer=None,thread=TARGET):
                async with client.post(url,json=payload()if value is None else value,headers={
                        "Authorization":"Bearer "+(bearer or h.fixture["gateway_token"]),"thread-id":thread})as response:
                    return response.status,await response.text()
            async def raw(request):return await h.synthetic(request,{"tools":f.Registry().tools(native)},h.plan)
            app=f.web.Application();app.router.add_post('/',raw)
            async with TestServer(app)as server:
                for kind in ["native_function_preview_revision","codemode_completed_revision"]:
                    case=h.arm(kind,TARGET)["case_id"]
                    if kind in f.FUNCTION_KINDS:
                        for variant in ["missing","duplicate","schema","strict","choice_none"]:
                            wrong=payload()
                            if variant=="missing":wrong["tools"]=[t for t in wrong["tools"]if t.get("name")!="clock"]
                            elif variant=="duplicate":wrong["tools"].append(copy.deepcopy(mock_clock))
                            elif variant=="schema":wrong["tools"][-1]["tools"][0]["parameters"]["properties"]["duration_ms"]["type"]="string"
                            elif variant=="strict":wrong["text"]={"format":{"type":"json_schema","name":"same_root_aux","schema":{"type":"object"},"strict":True}}
                            else:wrong["tool_choice"]="none"
                            assert(await request(wrong))[0]==409 and h.plan.fired==0 and h.plan.remaining==1
                        assert(await request(bearer="wrong-token"))[0]==401 and h.plan.remaining==1
                        assert(await request(thread="foreign-format-root"))[0]==409 and h.plan.remaining==1
                        checks["ordinary_clock_bound_schema_and_role_refusals_preserve_slot"]=True
                    async with client.post(server.make_url('/'))as response:
                        frames=orig.frames(await response.text());assert response.status==200
                    wire[kind]=frames
                    done=[e["item"]for e in frames if e["type"]=="response.output_item.done"and e.get("item",{}).get("type")=="function_call"]
                    assert len(done)==1;done=done[0];terminal=frames[-1]["response"]["output"][-1]
                    if kind in f.FUNCTION_KINDS:
                        preview=next(e["arguments"]for e in frames if e["type"]=="response.function_call_arguments.done")
                        assert json.loads(preview)=={"duration_ms":1000}and json.loads(done["arguments"])=={"duration_ms":1}and terminal==done
                    else:
                        A=f.effect_program(h.binding["nested_mcp_tool_name"],case);B=f.preview_effect_program(h.binding["nested_mcp_tool_name"],case)
                        assert json.loads(done["arguments"])["input"]==A and json.loads(terminal["arguments"])["input"]==B
                        assert all(done[k]==terminal[k]for k in("id","call_id","name","status"))and A.count("await tools[name](")==1 and B.count("await tools[name](")==2
                        reg=f.Registry();reg.tools(native);events=Events(reg);normalized=[]
                        for e in frames[:-1]:
                            normalized+=events.transform(e)
                            if e.get("item")==done:assert events.transform(copy.deepcopy(e))==[]
                        same=copy.deepcopy(frames[-1]);same["response"]["output"][-1]=done
                        normalized+=events.transform(same)
                        assert len([e for e in normalized if e["type"]=="response.output_item.done"and e.get("item",{}).get("type")=="custom_tool_call"])==1
                    status,text=await request();converted=orig.frames(text);assert status==200
                    if kind in f.FUNCTION_KINDS:
                        completed=[e["item"]for e in converted if e["type"]=="response.output_item.done"and e.get("item",{}).get("type")=="function_call"]
                        assert len(completed)==1 and completed[0]["name"]=="sleep"and completed[0]["namespace"]=="clock"and json.loads(completed[0]["arguments"])=={"duration_ms":1}and converted[-1]["type"]=="response.completed"
                        checks["ordinary_function_preview1000_FINAL1_wire_and_installed_bridge"]=True
                    else:
                        completed=[e["item"]for e in converted if e["type"]=="response.output_item.done"and e.get("item",{}).get("type")=="custom_tool_call"]
                        assert len(completed)==1 and completed[0]["input"]==A and converted[-1]["type"]=="response.failed"
                        assert not any(e.get("item",{}).get("input")==B for e in converted)
                        detail=h.state.db.execute("SELECT status,detail FROM receipts ORDER BY started DESC LIMIT 1").fetchone()
                        assert detail[0]=="adapter_error"and detail[1]=="Provider changed an already completed output item"
                        checks["postcompleted_A_then_terminal_B_exact_immutability_error_no_B_duplicate"]=True
                    consumed=h.plan;assert consumed.fired==1 and consumed.remaining==0 and h.ledger.count(case)==0
                    assert(await request())[0]==503 and h.plan is consumed  # New kinds never auto-rearm.
                    h.arm("synthetic_success",TARGET,case_id=case,repeat=1);slot=h.plan
                    auxiliary=payload();auxiliary["tools"]=[];auxiliary["text"]={"format":{"type":"json_schema","name":"same_root_aux","schema":{"type":"object","properties":{},"additionalProperties":False},"strict":True}}
                    assert(await request(auxiliary))[0]==503 and slot.remaining==1
                    assert(await request())[0]==200 and slot.remaining==0 and h.ledger.count(case)==0
                    try:h.arm("synthetic_success",TARGET,case_id=case,repeat=1)
                    except ValueError:pass
                    else:raise AssertionError("New samecase cleanup recovery rearmed")
            assert h.live_calls==0 and h.ledger.db.execute("SELECT COUNT(*)FROM effects").fetchone()[0]==0
            checks["new_format_kinds_no_auto_recovery_manual_one_slot_no_host_or_counter_effects"]=True
        (evidence/"scripted-formats-wire.json").write_text(json.dumps(wire,indent=2),encoding="utf-8")
        return checks
    finally:await h.close()

async def policy_controls(evidence):
    """Use the production policy loader and real loopback serving, no model."""
    from codex_app_deepseek import gateway_policy as gp
    import contextlib,io
    policyhome=evidence/"policy-home";policyhome.mkdir()
    value=gp.default_app_policy()
    # These are independently observed fixed-App producer IDs, not a QA serving
    # whitelist. Explicit user aliases must also remain valid.
    assert {"gpt-6-luna","gpt-6.1-sol"} <= set(value["accepted_models"])
    value["accepted_models"].append("explicit-user-helper-qa")
    (policyhome/"gateway.json").write_text(json.dumps(value))
    policy=gp.load_service_config(policyhome)
    assert set(policy.accepted_models)==set(value["accepted_models"])
    home=evidence/"policy-serve-state";f.initialize(home);task=None
    captured=io.StringIO()
    with contextlib.redirect_stdout(captured):
        task=asyncio.create_task(f.serve(home,finite_recovery=True,gateway_policy_home=policyhome))
        try:
            deadline=time.monotonic()+10
            while not (home/"ready.json").exists() and not task.done() and time.monotonic()<deadline:
                await asyncio.sleep(.02)
            if task.done():await task
            assert (home/"ready.json").exists()
            ready=json.loads((home/"ready.json").read_text());fixture=f.base.read_fixture(home)
            async with aiohttp.ClientSession(trust_env=False) as client:
                async def control(command):
                    async with client.post(ready["control_url"],json=command,headers={"Authorization":"Bearer "+fixture["control_token"]}) as response:
                        result=await response.json();assert response.status==200,result;return result
                for model in policy.accepted_models:
                    await control({"action":"arm","fault":"synthetic_success","thread_id":"policy-contract-loopback"})
                    async with client.post(ready["gateway_url"]+"/responses",
                            json={"model":model,"stream":True,"input":"policy alias contract only"},
                            headers={"Authorization":"Bearer "+fixture["gateway_token"],"thread-id":"policy-contract-loopback"}) as response:
                        frames=orig.frames(await response.text());assert response.status==200 and frames[-1]["type"]=="response.completed"
                before=(await control({"action":"status"}))["requests"]
                async with client.post(ready["gateway_url"]+"/responses",
                        json={"model":"gpt-unconfigured-qa-negative","stream":True,"input":"unknown request must not reach fixture upstream"},
                        headers={"Authorization":"Bearer "+fixture["gateway_token"],"thread-id":"policy-contract-loopback"}) as response:
                    await response.read();assert response.status==400
                after=await control({"action":"status"});assert after["requests"]==before and after["live_calls"]==0
                async with client.get(ready["gateway_url"].removesuffix("/v1")+"/healthz",
                        headers={"Authorization":"Bearer "+fixture["gateway_token"]}) as response:
                    health=await response.json();assert response.status==200
                assert set(health["accepted_models"])==set(policy.accepted_models)
                assert set(health["allowed_providers"])==set(policy.allowed_providers)
                assert health["config_sha256"]==policy.config_sha256
                await control({"action":"stop"})
            await task
        finally:
            if task is not None and not task.done():
                task.cancel()
                try:await task
                except asyncio.CancelledError:pass
    bad=evidence/"bad-policy-home";bad.mkdir()
    invalid=copy.deepcopy(value);invalid["unexpected_field"]="must fail before serving"
    (bad/"gateway.json").write_text(json.dumps(invalid))
    unused=evidence/"must-not-be-initialized"
    try:await f.serve(unused,gateway_policy_home=bad)
    except gp.ServiceConfigError:pass
    else:raise AssertionError("Invalid production policy started fixture")
    assert not unused.exists()
    return {"production_policy_four_App_aliases_and_explicit_user_alias_preserved":True,
            "unlisted_request400_no_upstream_and_invalid_policy_no_start":True}

async def main():
    before=frozen();started=time.time();run_id=uuid.uuid4().hex[:8]
    evidence=f.REPO/".evidence"/("codemode-selftest-"+run_id);evidence.mkdir(parents=True,exist_ok=False)
    home=evidence/"loopback-state";f.initialize(home)
    h=f.CodeModeHarness(home,allow_loopback_test_binding=True)
    results={};final={};nonce_hashes=[];counter_server_pid=None
    try:
        for kind in ("tool_done_cut","tool_incomplete_cut","after_effect_503","codemode_effect"):
            try:h.arm(kind,TARGET)
            except ValueError:pass
            else:raise AssertionError("Pending discovery accepted")
        results["native_binding_pending_rejects_effect_fault_arming"]=True
        ready=await h.start();url=ready["gateway_url"]+"/responses"
        async with aiohttp.ClientSession(trust_env=False) as client:
            async def request(payload=None,thread=TARGET):
                headers={"Authorization":"Bearer "+h.fixture["gateway_token"],"thread-id":thread,"session-id":"shared-affinity"}
                async with client.post(url,json=payload or body(),headers=headers) as r:return r.status,await r.text()
            headers={"Authorization":"Bearer "+h.fixture["gateway_token"],"thread-id":TARGET}
            async with client.post(url,data="not-json",headers=headers) as response:
                assert response.status==400;await response.read()
            async with client.post(url,json=body(),headers={"Authorization":"Bearer wrong","thread-id":TARGET}) as response:
                assert response.status==401;await response.read()
            h.arm("codemode_discovery",TARGET)
            missing_discovery=body();missing_discovery["tools"]=[]
            previous=h.requests
            status,text=await request(missing_discovery)
            assert status==409 and h.requests==previous+1 and h.plan.remaining==1 and h.plan.fired==0
            assert h.summary()["physical_http_attempts"]==3 and h.requests==1
            assert h.summary()["physical_http_status_counts"]=={"400":1,"401":1,"409":1}
            results["invalid_json_auth_and_inconclusive_discovery_preserve_AA3_budget_semantics"]=True
            status,text=await request()
            events=orig.frames(text);assert status==200 and any(e["type"]=="response.completed" for e in events)
            discovery_done=next(e["item"] for e in events if e["type"]=="response.output_item.done" and e.get("item",{}).get("type")=="custom_tool_call")
            assert discovery_done["type"]=="custom_tool_call" and discovery_done["input"]==f.discovery_program()
            assert not h.binding and not h.live_enabled
            results["source_derived_custom_exec_converter_discovery_frames"]=True
            params=StdioServerParameters(command=sys.executable,args=["-I","-X","utf8",str(HERE/"codemode_fault.py"),"counter","--state-dir",str(home)],
                env={k:v for k,v in os.environ.items() if not k.upper().startswith(("CODEX_","OPENAI_","OPENROUTER_"))})
            async with stdio_client(params) as (read,write):
                async with ClientSession(read,write) as session:
                    await session.initialize();inventory=await session.list_tools()
                    tool=next(t.model_dump(by_alias=True,exclude_none=True) for t in inventory.tools if t.name=="record_effect")
                    binding=simulated_binding(tool)
                    # This is a constructed source-contract artifact, never a native capture.
                    (evidence/"source-contract-mock-binding.json").write_text(json.dumps(binding,indent=2),encoding="utf-8")
                    h.bind_evidence(binding)
                    for field,value in [("introspection_thread_id","wrong-thread"),("evidence_level","untrusted")]:
                        wrong=copy.deepcopy(binding);wrong[field]=value
                        try:h.bind_evidence(wrong)
                        except ValueError:pass
                        else:raise AssertionError("Invalid binding accepted")
                    results["mock_binding_validation_and_standard_sdk_schema"]=True
                    for kind,want_done in (("tool_incomplete_cut",False),("tool_done_cut",True)):
                        arm=h.arm(kind,TARGET);status,text=await request();events=orig.frames(text)
                        done=[e["item"] for e in events if e["type"]=="response.output_item.done" and e.get("item",{}).get("type")=="custom_tool_call"]
                        assert status==200 and bool(done)==want_done and events[-1]["type"]=="response.failed"
                        assert not any(e["type"]=="response.completed" for e in events)
                        assert h.ledger.count(arm["case_id"])==0
                        if want_done:
                            item=done[0];case=arm["case_id"];assert item["input"]==f.effect_program(h.binding["nested_mcp_tool_name"],case)
                    results["incomplete_done_boundaries_no_counter_execution_by_fixture"]=True
                    h.arm("tool_done_cut",TARGET,case_id=case)
                    missing=body();missing["tools"]=[]
                    status,text=await request(missing);assert status==409 and h.plan.remaining==1
                    altered=body();altered["tools"][0]["description"]="different declaration"
                    status,text=await request(altered);assert status==409 and h.plan.remaining==1
                    results["missing_or_changed_declared_exec_inconclusive"]=True
                    one=await session.call_tool("record_effect",{"case_id":case})
                    two=await session.call_tool("record_effect",{"case_id":case})
                    unknown=await session.call_tool("record_effect",{"case_id":"not-registered"})
                    r1=one.structured_content;r2=two.structured_content
                    assert r1["effect_count"]==1 and r2["effect_count"]==2 and unknown.is_error
                    assert r1["receipt_nonce"]!=r2["receipt_nonce"] and len(r2["receipt_nonce"])>=40
                    nonce_hashes=[f.base.digest(r1["receipt_nonce"]),f.base.digest(r2["receipt_nonce"])]
                    assert h.ledger.count(case)==2
                    results["standard_mcp_two_actual_calls_two_durable_effects_distinct_nonces"]=True
            h.arm("after_effect_503",TARGET,case_id=case)
            continuation=body();continuation["input"]=[item,{"type":"custom_tool_call_output","call_id":item["call_id"],
                "output":[{"type":"input_text","text":"Script completed\nWall time 0.1 seconds\nOutput:\n"},
                    {"type":"input_text","text":json.dumps(r2)}]}]
            registry=f.Registry();converted={"tools":registry.tools(NATIVE_TOOLS),"input":registry.history(continuation["input"])}
            # Unit guard inputs; no native tool is invoked by these arrays.
            negatives=[]
            value=copy.deepcopy(converted);value["input"]=value["input"][1:];negatives.append(("orphan_output",value))
            value=copy.deepcopy(converted);value["input"].append(value["input"][0]);negatives.append(("duplicate_call_id",value))
            value=copy.deepcopy(converted);value["input"].append(value["input"][1]);negatives.append(("duplicate_output_id",value))
            value=copy.deepcopy(converted);value["input"][0]["status"]="in_progress";negatives.append(("incomplete_call",value))
            value=copy.deepcopy(converted);value["input"][0]["name"]="other_exec";negatives.append(("wrong_tool",value))
            value=copy.deepcopy(converted);value["input"][0]["arguments"]=json.dumps({"input":"text('echo')"});negatives.append(("wrong_program",value))
            value=copy.deepcopy(converted);value["input"][1]["call_id"]="wrong";negatives.append(("mismatched_id",value))
            value=copy.deepcopy(converted);value["input"]=[{"role":"assistant","content":json.dumps(r2)}];negatives.append(("assistant_echo",value))
            for label,change in [("wrong_nonce",{"receipt_nonce":"guessed-nonce"}),("cross_case",{"case_id":"other-case"}),("wrong_count",{"effect_count":999})]:
                value=copy.deepcopy(converted);value["input"][1]["output"]=json.dumps({**r2,**change});negatives.append((label,value))
            for label,value in negatives:assert h.case_result_call_ids(value,case)==[],label
            results["receipt_negative_guards"]=[label for label,_ in negatives]
            # Same actual completed item, guessed receipt: loopback rejection must not consume the fault.
            bad=copy.deepcopy(continuation);bad["input"][1]["output"]=json.dumps({**r2,"receipt_nonce":"guessed"})
            status,text=await request(bad);assert status==503 and h.plan.fired==0 and h.plan.remaining==1
            assert h.case_result_call_ids(converted,case)==[item["call_id"]]
            status,text=await request(continuation);assert status==503 and h.plan.fired==1 and h.plan.remaining==0
            status,text=await request(continuation);assert status==503 and h.plan.fired==1 and h.ledger.count(case)==2
            assert h.live_calls==0
            results["after_effect_503_matches_actual_sdk_receipt_once_loopback_only"]=True
            ctrl={"Authorization":"Bearer "+h.fixture["control_token"]}
            async with client.post(ready["control_url"],json={"action":"enable_live"},headers=ctrl) as response:
                assert response.status==400
            results["live_disabled_no_credential_reader"]=True
        prior_requests=h.requests;prior_physical=h.summary()["physical_http_attempts"];deadline=h.deadline
        await h.close()
        strict=f.CodeModeHarness(home)
        assert not strict.usable_binding()
        try:strict.arm("tool_done_cut",TARGET,case_id=case)
        except ValueError:pass
        else:raise AssertionError("Mock discovery usable by ordinary serve")
        await strict.close()
        h=f.CodeModeHarness(home,allow_loopback_test_binding=True)
        ready=await h.start(bridge_port=int(url.split(":")[2].split("/")[0]));url=ready["gateway_url"]+"/responses"
        assert h.requests==prior_requests and h.summary()["physical_http_attempts"]==prior_physical and h.deadline==deadline
        assert h.ledger.count(case)==2 and not h.live_enabled and h.plan is None
        try:h.arm("after_effect_503","wrong-thread",case_id=case)
        except ValueError:pass
        else:raise AssertionError("Cross-thread arm accepted")
        results["cold_reopen_preserves_counts_nonce_limits_and_rejects_mock_binding_by_default"]=True
        async with aiohttp.ClientSession(trust_env=False) as client:
            while h.requests<64:
                async with client.post(url,json=body(),headers={"Authorization":"Bearer "+h.fixture["gateway_token"],"thread-id":"unarmed-budget-thread"}) as response:
                    assert response.status==409;await response.read()
            before_physical=h.summary()["physical_http_attempts"]
            async with client.post(url,json=body(),headers={"Authorization":"Bearer "+h.fixture["gateway_token"],"thread-id":TARGET}) as response:
                assert response.status==503;await response.read()
            assert h.requests==64 and h.summary()["physical_http_attempts"]==before_physical+1
            assert h.summary()["physical_http_attempts"]==67
        final=h.summary()
        results["64_processed_cap_retains_physical_budget_refusal"]=True
    finally:await h.close()
    # Expiry test uses a clearly backdated test-only limits fixture, never waits 900 s.
    expired=evidence/"expired-loopback-state";f.initialize(expired)
    limits=json.loads((expired/"limits.json").read_text());limits["created_at"]-=901
    f.base._atomic_write(expired/"limits.json",json.dumps(limits).encode())
    for _ in range(2):
        h=f.CodeModeHarness(expired)
        try:
            ready=await h.start()
            async with aiohttp.ClientSession(trust_env=False) as client:
                async with client.post(ready["gateway_url"]+"/responses",json=body(),headers={"Authorization":"Bearer "+h.fixture["gateway_token"],"thread-id":TARGET}) as response:
                    assert response.status==503;await response.read()
            assert h.requests==0 and h.live_calls==0
        finally:await h.close()
    ledger=f.NonceLedger(expired)
    try:
        expired_case=ledger.new_case(f.base.digest(TARGET))
        try:ledger.record(expired_case)
        except ValueError:pass
        else:raise AssertionError("Expired fixture accepted durable effect")
        assert ledger.count(expired_case)==0
    finally:ledger.close()
    results["absolute_900s_epoch_persists_across_restart_test_backdated"]=True
    results.update(await finite_controls(evidence,tool))
    results.update(await scripted_formats_controls(evidence,tool))
    results.update(await policy_controls(evidence))
    assert frozen()==before
    events_text=(home/"events.json").read_text()
    assert all(n not in events_text for n in [r1["receipt_nonce"],r2["receipt_nonce"],"PRIVATE_SOURCE_CONTRACT_TEST"])
    final.update({"actual_App_acceptance":False,"native_discovery_binding":"pending",
        "test_binding_evidence_level":"source_contract_loopback_mock"})
    report={"schema":1,"outcome":"passed_source_contract_and_loopback_only","started_at":started,"ended_at":time.time(),
        "scope":{"actual_native_invocations":0,"App_launches":0,"model_requests":0,"fixture_WinCred_reader_calls":0,
            "global_WinCred_claim":False,"production_source_changed":False,"actual_discovery":"pending"},
        "checks":results,"standard_mcp_receipt_nonce_sha256":nonce_hashes,"loopback_summary":final,
        "frozen_sources":before,"limits":{"processed":64,"lifetime_seconds":900,"live_default":False,"live_cap":8},
        "acceptance_limits":["Synthetic tool frames are not DeepSeek output or native execution proof.",
            "SDK counter effects occurred only in explicit standard MCP selftest calls; native CodeMode execution was not run.",
            "Actual H tools declaration, same-thread MCP inventory and introspection are pending.",
            "One-time native side-effect, late-error handling and actual App cold recovery remain untested."]}
    (evidence/"selftest-result.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps({"outcome":report["outcome"],"checks":len(results),"processed":final["requests"],"physical":final["physical_http_attempts"],"native":0,"model":0,"root":str(evidence)}))
if __name__=="__main__":asyncio.run(main())
