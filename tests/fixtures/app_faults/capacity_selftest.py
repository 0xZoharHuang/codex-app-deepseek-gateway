"""Finite pre-response 413 / installed Bridge loopback checks; no App or native."""
from __future__ import annotations
import argparse,asyncio,hashlib,importlib.util,json,sys,time,traceback
from contextlib import asynccontextmanager
from pathlib import Path
# Load production modules before the common fixture's optional source lookup.
import codex_app_deepseek.bridge as installed_bridge
import codex_app_deepseek.protocol_state as installed_state
import aiohttp
HERE=Path(__file__).resolve().parent
REPO=HERE.parents[2]
spec=importlib.util.spec_from_file_location("capacity_qa_codemode",HERE/"codemode_fault.py")
f=importlib.util.module_from_spec(spec);sys.modules[spec.name]=f;spec.loader.exec_module(f)
TARGET="capacity-loopback-only"
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def body():return {"model":f.base.MODEL,"stream":True,"input":"Synthetic capacity control only.",
                   "prompt_cache_key":"shared-affinity-is-not-thread-identity","tools":[]}
def transitions(h):return [e for e in h.events if e["event"]=="finite_recovery_transition"]
def source_hashes():return {p.name:sha(p) for p in
    [HERE/"fault_server.py",HERE/"codemode_fault.py",Path(__file__),HERE/"synthetic_reasoning.py"]}
async def main(evidence, expected_bridge_sha256):
    evidence.mkdir(parents=True,exist_ok=False)
    report={"status":"failed","App_or_native_started":False,"real_model_requests":0,
            "WinCred_read":False,"App_acceptance":False,"started":time.time(),
            "installed_bridge":str(Path(installed_bridge.__file__).resolve()),
            "installed_bridge_sha256":sha(Path(installed_bridge.__file__)),
            "installed_protocol_state":str(Path(installed_state.__file__).resolve()),
            "source_hashes_before":source_hashes(),"assertions":[]}
    assert not Path(installed_bridge.__file__).resolve().is_relative_to(REPO),"Use selected installed Python"
    assert len(expected_bridge_sha256)==64 and all(c in "0123456789abcdef" for c in expected_bridge_sha256)
    assert report["installed_bridge_sha256"]==expected_bridge_sha256
    report["expected_bridge_sha256"]=expected_bridge_sha256
    @asynccontextmanager
    async def fixture(name,finite=True):
        home=evidence/name;f.initialize(home)
        h=f.CodeModeHarness(home,finite_recovery=finite)
        ready=await h.start()
        assert h.live_reader is None and not h.live_enabled
        async with aiohttp.ClientSession(trust_env=False) as client:
            async def request(thread=TARGET):
                async with client.post(ready["gateway_url"]+"/responses",json=body(),
                    headers={"Authorization":"Bearer "+h.fixture["gateway_token"],"thread-id":thread}) as response:
                    return response.status,{k.lower():v for k,v in response.headers.items()},await response.text()
            async def control(command):
                async with client.post(ready["control_url"],json=command,
                    headers={"Authorization":"Bearer "+h.fixture["control_token"]}) as response:
                    return response.status,await response.json()
            try:yield h,ready,request,control
            finally:
                assert h.live_calls==0
                await h.close()
    try:
        async with fixture("finite-positive") as (h,ready,request,control):
            status,plan=await control({"action":"arm","fault":"http_413","thread_id":TARGET,"repeat":1})
            assert status==200
            initial_deadline=h.deadline;nonce=h.fixture["nonce"];case=plan["case_id"]
            status,_,_=await request("wrong-thread")
            assert status==409 and h.plan.remaining==1 and h.plan.fired==0 and not transitions(h)
            status,headers,text=await request()
            assert status==413 and headers["content-type"].startswith("application/json")
            assert all(k not in headers for k in ("retry-after","retry-after-ms","x-ms-retry-after-ms"))
            assert "data:" not in text and "response.created" not in text and "response.failed" not in text
            assert json.loads(text)["error"]["code"]=="request_too_large"
            receipt=next(r for r in h.state.list_receipts() if r["id"]==headers["x-gateway-request-id"])
            assert receipt["status"]=="upstream_error" and receipt["upstream_status"]==413
            assert receipt["delivery_status"]=="delivered"
            event=next(e for e in h.events if e["event"]=="pre_response_http_rejection")
            assert event["status"]==413 and event["sse_prepared"] is False
            assert event["response_body_sha256"]==hashlib.sha256(text.encode()).hexdigest()
            assert event["request_id_sha256"]==f.base.digest(headers["x-request-id"])
            assert h.ledger.count(case)==0 and h.plan.remaining==0 and h.plan.fired==1
            report["assertions"].append("genuine_http413_through_installed_bridge_before_SSE_no_retry_advice_no_effect")
            for _ in range(2):
                status,headers,text=await request()
                assert status==200 and '"type": "response.failed"' not in text
                assert "response.completed" in text
                receipt=next(r for r in h.state.list_receipts() if r["id"]==headers["x-gateway-request-id"])
                assert receipt["status"]=="completed" and receipt["delivery_status"]=="delivered"
            assert (await request())[0]==503
            assert len(transitions(h))==1 and transitions(h)[0]["allocated_synthetic_responses"]==2
            slots=[e for e in h.events if e["event"]=="synthetic_capacity_recovery_slot"]
            assert [e["slot"] for e in slots]==[1,2]
            assert all(e["native_request_role_not_inferred"] for e in slots)
            assert h.deadline==initial_deadline and h.fixture["nonce"]==nonce and h.requests==5
            assert h.plan.case_id==case and h.ledger.count(case)==0
            report["assertions"].append("exact_thread_single_transition_two_journaled_slots_then_503_no_live_forward")
            report["positive_state"]={"processed":h.requests,"deadline":h.deadline,
                "nonce_sha256":f.base.digest(nonce),"case_sha256":f.base.digest(case),"state":str(h.home)}
        # Reopening fixture objects is metadata persistence evidence, not process/App cold proof.
        h=f.CodeModeHarness(evidence/"finite-positive",finite_recovery=True)
        try:
            await h.start()
            assert h.deadline==initial_deadline and h.fixture["nonce"]==nonce and h.requests==5
            assert h.plan is None and h.ledger.count(case)==0
            report["assertions"].append("same_state_reopen_preserves_deadline_nonce_budget_and_does_not_rearm")
        finally:await h.close()
        async with fixture("default-off",finite=False) as (h,_,request,control):
            h.arm("http_413",TARGET)
            assert (await request())[0]==413 and (await request())[0]==503 and not transitions(h)
            report["assertions"].append("finite_recovery_is_opt_in")
        async with fixture("contracts") as (h,_,request,control):
            h.arm("http_413",TARGET);original=h.plan
            for cmd in [
                {"action":"arm","fault":"http_413_after_stream","thread_id":TARGET},
                {"action":"arm","fault":"http_413","thread_id":TARGET,"repeat":6},
                {"action":"enable_live"}]:
                assert (await control(cmd))[0]==400 and h.plan is original
            assert not h.live_enabled and h.live_reader is None and original.fired==0 and not transitions(h)
            report["assertions"].append("unknown_stream_fault_invalid_repeat_and_live_enable_refused")
        async with fixture("processed-cap") as (h,_,request,control):
            h.arm("http_413",TARGET);original=h.plan;deadline=h.deadline
            assert (await request())[0]==413
            for _ in range(63):assert (await request("wrong-thread"))[0]==409
            assert h.requests==64
            assert (await request())[0]==503 and h.plan is original and not transitions(h)
            assert h.requests==64 and h.deadline==deadline
            report["assertions"].append("actual_64_processed_cap_refuses_without_rearming_or_reset")
        async with fixture("deadline") as (h,_,request,control):
            h.arm("http_413",TARGET);original=h.plan
            assert (await request())[0]==413
            # Explicitly shortened selftest-only deadline; never extends actual-window limits.
            h.deadline=time.time()+0.04;deadline=h.deadline
            await asyncio.sleep(0.06)
            assert (await request())[0]==503 and h.plan is original and not transitions(h)
            assert h.deadline==deadline and h.requests==1
            report["assertions"].append("expired_selftest_deadline_refuses_without_rearming_or_reset")
        for crossing in ("deadline","cap"):
            async with fixture("wait-crossing-"+crossing) as (h,_,request,control):
                h.arm("http_413",TARGET);original=h.plan
                assert (await request())[0]==413
                # Isolated guard control: emulate concurrent work finishing during the wait.
                h.inflight=1
                if crossing=="deadline":h.deadline=time.time()+0.005
                async def release():
                    await asyncio.sleep(0.01)
                    if crossing=="cap":h.requests=64
                    h.inflight=0
                release_task=asyncio.create_task(release())
                assert (await request())[0]==503
                await release_task
                assert h.plan is original and not transitions(h)
                report["assertions"].append("selftest_inflight_control_"+crossing+"_crossing_refuses_transition")
        report["source_hashes_after"]=source_hashes()
        assert report["source_hashes_before"]==report["source_hashes_after"]
        report["status"]="passed"
    except BaseException as error:
        report.update(error_type=type(error).__name__,error=str(error),traceback=traceback.format_exc())
        raise
    finally:
        report["finished"]=time.time()
        (evidence/"result.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
        print(json.dumps({"status":report["status"],"result":str(evidence/"result.json"),
                          "assertions":len(report["assertions"]),"App_acceptance":False}))
if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("--evidence-dir",type=Path,required=True)
    p.add_argument("--expected-bridge-sha256",required=True,help="Exact independently verified installed candidate bridge SHA256")
    args=p.parse_args()
    asyncio.run(main(args.evidence_dir.resolve(),args.expected_bridge_sha256))
