"""Mock-only actual HTTP and original SSE parser checks for passive Parent/action tap."""
from pathlib import Path
from types import SimpleNamespace
import argparse,asyncio,copy,hashlib,importlib.util,json,time
import aiohttp
from aiohttp import web
from codex_app_deepseek.bridge import sse_events
from codex_deepseek.protocol import encode_sse
ROOT=Path(__file__).parent
p=argparse.ArgumentParser();p.add_argument("--inputs",type=Path,required=True);p.add_argument("--inputs-sha256",required=True);p.add_argument("--output-dir",type=Path,required=True);args=p.parse_args()
assert hashlib.sha256(args.inputs.read_bytes()).hexdigest()==args.inputs_sha256
manifest=json.loads(args.inputs.read_bytes());assert manifest["limits"]=={"lifetime_seconds":900,"processed_cap":64,"event_cap":256,"live_cap":8}
for rel,wanted in manifest["modules"].items():
 path=Path(rel);assert not path.is_absolute()and".."not in path.parts
 assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==wanted
OUT=args.output_dir;OUT.mkdir(parents=True,exist_ok=False)
spec=importlib.util.spec_from_file_location("qa_passive_tap",ROOT/"observers/guardian_assessment_tap.py");m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
PARENT="11111111-2222-7333-8444-555555555555";REVIEWER="aaaaaaaa-bbbb-7ccc-8ddd-eeeeeeeeeeee";assert PARENT!=REVIEWER
ACTION={"tool":"exec_command","command":["mock-tool-not-executed","mock-argument"],"cwd":"mock-cwd-not-used","sandbox_permissions":"require_escalated","tty":False,"environment_id":"mock-environment"}
texts=[">>> TRANSCRIPT START\n","mock transcript only\n",">>> TRANSCRIPT END\n","Reviewed Codex session id: "+PARENT+"\n","The Codex agent has requested the following action:\n",">>> APPROVAL REQUEST START\n","Assess the exact planned action below. Use read-only tool checks when local state matters.\n","Planned action JSON:\n",json.dumps(ACTION,sort_keys=True,indent=2)+"\n",">>> APPROVAL REQUEST END\n"]
PAYLOAD={"input":[{"role":"user","content":[{"type":"input_text","text":x}for x in texts]}],"text":{"format":{"type":"json_schema","name":"codex_output_schema","schema":copy.deepcopy(m.SCHEMA)}}}
state={"thread_id":PARENT,"status":"consumed","case_id":"mock-only-case","call_id":"mock-only-call"}
item={"type":"message","role":"assistant","id":"mock-message","content":[{"type":"output_text","text":'{"outcome":"deny","rationale":"mock-only refusal"}'}]}
EVENTS=[{"type":"response.created","response":{"id":"mock-generation","status":"in_progress"}},{"type":"response.output_item.done","item":item},{"type":"response.completed","response":{"id":"mock-generation","status":"completed","output":[item]}}]
core=SimpleNamespace(sse_events=sse_events);tap=m.Tap(core,OUT/"assessments",time.time()+30);observed=[];emitted=[];before=[];watch=tap.observe
def spy(record,event):
 observed.append(event);before.append(copy.deepcopy(event));return watch(record,event)
tap.observe=spy
index=0;server_calls=0;BASE_URL=None;HTTP_CLIENT=None
async def upstream(request):
 global server_calls
 server_calls+=1
 response=web.StreamResponse(headers={"Content-Type":"text/event-stream"});await response.prepare(request)
 for event in EVENTS[:-1]if request.query.get("cut")else EVENTS:await response.write(encode_sse(event))
 await response.write_eof();return response
async def relay(request):
 global index,server_calls
 server_calls+=1;index+=1;raw=await request.read();payload=json.loads(raw)
 row={"body_observation":"complete","thread_sha256":m.digest(request.headers["X-CDAG-QA-Thread-ID"].encode()),"physical_request_index":index,"raw_body_sha256":m.digest(raw),"outerQARequestID":"a"*32,"upstreamRoleRequestIDs":["b"*32],"epoch":"mock-only"}
 response=web.StreamResponse(headers={"Content-Type":"text/event-stream"});await response.prepare(request)
 with tap.scope(request,payload,row,state):
  async with HTTP_CLIENT.get(BASE_URL+"/upstream",params={"cut":"1"}if request.query.get("cut")else{})as source:
   async for event in core.sse_events(source.content,2*1024*1024):
    emitted.append(event);await response.write(encode_sse(event))
 await response.write_eof();return response
async def main():
 global BASE_URL,HTTP_CLIENT
 app=web.Application();app.router.add_get("/upstream",upstream);app.router.add_post("/observe",relay)
 runner=web.AppRunner(app);await runner.setup();site=web.TCPSite(runner,"127.0.0.1",0);await site.start();BASE_URL="http://127.0.0.1:"+str(site._server.sockets[0].getsockname()[1])
 try:
  async with aiohttp.ClientSession(trust_env=False,timeout=aiohttp.ClientTimeout(total=10))as client:
   HTTP_CLIENT=client
   for cut in (False,True):
    async with client.post(BASE_URL+"/observe",json=PAYLOAD,headers={"X-CDAG-QA-Thread-ID":REVIEWER},params={"cut":"1"}if cut else{})as response:
     output=[e async for e in sse_events(response.content,2*1024*1024)]
    assert output==(EVENTS[:-1]if cut else EVENTS)
   assert len(observed)==len(emitted)==5 and all(x is y and x==z for x,y,z in zip(observed,emitted,before))
  records=[json.loads(p.read_bytes())for p in sorted(tap.output.glob("response-*.json"))];assert len(records)==2
  assert records[0]["upstream_terminal_completed"]and not records[1]["upstream_terminal_completed"]
  for record in records:
   assert record["actual_reviewer_native_thread_sha256"]==m.digest(REVIEWER.encode())and record["native_parent_session_sha256"]==m.digest(PARENT.encode())
   assert record["actual_reviewer_native_thread_sha256"]!=record["native_parent_session_sha256"]
   assert record["messages"][0]["literal_outcome"]=="deny"and record["messages"][0]["literal_risk_level"]is None
   assert record["parent_action_source_binding"]["native_environment_id_present"]and not record["parent_action_source_binding"]["wire_contains_native_review_or_target_ids"]
   assert record["native_decision_observed_by_this_tap"]is False
  negatives=[]
  wrong=copy.deepcopy(PAYLOAD);wrong["input"][-1]["content"][3]["text"]="prefix "+wrong["input"][-1]["content"][3]["text"];negatives.append(wrong)
  wrong=copy.deepcopy(PAYLOAD);wrong["input"][-1]["content"]=[{"type":"input_text","text":"".join(texts)}];negatives.append(wrong)
  wrong=copy.deepcopy(PAYLOAD);wrong["input"].append({"role":"user","content":[{"type":"input_text","text":"new-current-message"}]});negatives.append(wrong)
  wrong=copy.deepcopy(PAYLOAD);wrong["input"][-1]["content"][3]["text"]="Reviewed Codex session id: "+PARENT+" \n";negatives.append(wrong)
  for wrong in negatives:assert m.parent_action_binding(wrong)is None
  assert not(PARENT==REVIEWER),"Historical same-header-only candidate would miss this legitimate internal topology"
 finally:
  tap.close();await runner.cleanup()
 assert core.sse_events is sse_events and not hasattr(core,"_private_guardian_assessment_tap")
 result={"scope":"mock-only HTTP+original parser+passive tap; no native approval/model/GUI/provider pass","passed":True,"real_loopback_HTTP_requests":server_calls,"same_object_order_content":True,"distinct_internal_header_Parent_capture":True,"cut_no_terminal":True,"malformed_binding_controls":len(negatives),"no_fabricated_optional_fields":True,"parser_restored":True,"external_provider_credentials_App_native_actions":0}
 (OUT/"result.json").open("x",encoding="utf-8").write(json.dumps(result,indent=2)+"\n");print(json.dumps(result))
asyncio.run(main())
