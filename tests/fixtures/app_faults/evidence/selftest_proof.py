from pathlib import Path
import copy,hashlib,importlib.util,json,time,argparse
ROOT=Path(__file__).parent
parser=argparse.ArgumentParser(description="Pure mock-only evidence negative controls; no App/model/provider.")
parser.add_argument("--inputs",type=Path,required=True)
parser.add_argument("--inputs-sha256",required=True)
parser.add_argument("--output",type=Path,required=True)
args=parser.parse_args()
assert hashlib.sha256(args.inputs.read_bytes()).hexdigest()==args.inputs_sha256
manifest=json.loads(args.inputs.read_bytes())
assert manifest["limits"]=={"lifetime_seconds":900,"processed_cap":64,"event_cap":256,"live_cap":8}
for rel,wanted in manifest["modules"].items():
 path=Path(rel);assert not path.is_absolute() and ".."not in path.parts
 assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest()==wanted
assert not args.output.exists()

def module(path,name):
 s=importlib.util.spec_from_file_location(name,path);m=importlib.util.module_from_spec(s);s.loader.exec_module(m);return m
new=module(ROOT/"proof_contract.py","new_strict_proof")
root="native-root-fixture";turn="native-turn-fixture";model="deepseek/deepseek-v4.1-flash-1017";prompt="finite raw413 nonce"
def native(error=None,actual=prompt):
 rows=[{"type":"session_meta","payload":{"id":root}},
 {"type":"turn_context","payload":{"turn_id":turn,"model":model}},
 {"type":"response_item","payload":{"type":"message","role":"user","content":[{"text":actual}],"internal_chat_message_metadata_passthrough":{"turn_id":turn}}},
 {"type":"event_msg","payload":{"type":"task_started","turn_id":turn}}]
 rows.append({"type":"event_msg","payload":{"type":"error",**(error or {"message":"provider rejected request","codex_error_info":{"http_connection_failed":{"http_status_code":413}}})}})
 rows.append({"type":"event_msg","payload":{"type":"task_complete","turn_id":turn}})
 return rows
def observed():
 h=new.digest(new.normalize(prompt))
 v={"thread_id":root,"requested_model":model,"last_user_text_block_sha256":[h],"last_user_text_block_raw_sha256":[new.digest(prompt)],"physical_request_index":3,"raw_body_sha256":"a"*64,"http_status":413,"response_ids":{"x-gateway-request-id":"gateway-physical-request-fixture"}}
 role={"thread_sha256":new.digest(root),"requested_model":model,"last_user_text_block_sha256":[h],"last_user_text_block_raw_sha256":[new.digest(prompt)],"physical_request_index":3,"raw_body_sha256":"a"*64,"http_status":413,"acceptedGatewayRequestID":"gateway-physical-request-fixture","native_role":"MAIN","tool_profile_valid":True,"code_mode_main_shape":True,"body_observation":"complete"}
 return v,role
cases=[]
def test(label,fn,accepted):
 try:fn();got=True;reason=None
 except ValueError as e:got=False;reason=str(e)
 assert got is accepted,(label,got,accepted,reason)
 cases.append({"name":label,"expected_accept":accepted,"actual_accept":got,"passed":True,"failure_reason":reason})
def check(rows=None,v=None,r=None,target=turn):
 vv,rr=observed()
 return new.raw413_native_error(rows if rows is not None else native(),target,main_observation=v if v is not None else vv,role_observation=r if r is not None else rr,root=root,prompt=prompt,model=model)
test("native_exact_prompt",lambda:new.native_prompt(native(),root,prompt,model),True)
test("native_editor_one_LF",lambda:new.native_prompt(native(actual=prompt+"\n"),root,prompt,model),True)
test("native_CRLF_canonical_one_LF",lambda:new.native_prompt(native(actual=prompt+"\r\n"),root,prompt,model),True)
test("native_two_added_LFs_rejected",lambda:new.native_prompt(native(actual=prompt+"\n\n"),root,prompt,model),False)
test("native_unexpected_trailing_text_rejected",lambda:new.native_prompt(native(actual=prompt+" altered"),root,prompt,model),False)
test("native_declared_final_LF_plus_editor_one",lambda:new.native_prompt(native(actual=prompt+"\n\n"),root,prompt+"\n",model),True)
test("native_model_mismatch_rejected",lambda:new.native_prompt(native(),root,prompt,"another-model"),False)
test("structured_current_HTTP413",lambda:check(),True)
test("structured_retry_exhaustion413",lambda:check(native({"message":"request retry limit","codex_error_info":{"response_too_many_failed_attempts":{"http_status_code":413}}})),True)
test("optional_classification_stock_display413",lambda:check(native({"message":"unexpected status 413 Payload Too Large: owned fixture rejection","codex_error_info":None})),True)
test("optional_classification_stock_numeric_display413",lambda:check(native({"message":"unexpected status 413: owned fixture rejection"})),True)
test("incidental_413_in_payload_rejected",lambda:check(native({"message":"request failed","codex_error_info":{"http_connection_failed":{"http_status_code":503}},"diagnostic":"unrelated trace 413"})),False)
test("conflicting_structured503_not_text_fallback",lambda:check(native({"message":"unexpected status 413 Payload Too Large: quoted body","codex_error_info":{"http_connection_failed":{"http_status_code":503}}})),False)
test("generic_message_digits413_rejected",lambda:check(native({"message":"request failed after 413 ms","codex_error_info":None})),False)
test("wrong_error_variant_context_overflow_rejected",lambda:check(native({"message":"unexpected status 413 Payload Too Large: fixture","codex_error_info":"context_window_exceeded"})),False)
test("structured_status_string413_rejected",lambda:check(native({"message":"request failed","codex_error_info":{"http_connection_failed":{"http_status_code":"413"}}})),False)
test("wrong_target_turn_rejected",lambda:check(target="another-turn"),False)
v,r=observed();v["http_status"]=503
test("raw_HTTP503_not413_rejected",lambda:check(v=v),False)
v,r=observed();r["raw_body_sha256"]="b"*64
test("physical_body_mismatch_rejected",lambda:check(r=r),False)
v,r=observed();r["acceptedGatewayRequestID"]="another-request"
test("physical_gateway_request_mismatch_rejected",lambda:check(r=r),False)
v,r=observed();r["physical_request_index"]=4
test("physical_attempt_index_mismatch_rejected",lambda:check(r=r),False)
test("exact_prompt_required_even_with_matching_normalized_MAIN_hash",lambda:check(native(actual=prompt+"\n\n")),False)
rows=native();err=rows.pop(4);rows.insert(3,err)
test("earlier_error_outside_active_turn_rejected",lambda:check(rows),False)
rows=native();err=rows.pop(4);rows.append(err)
test("error_after_task_complete_rejected",lambda:check(rows),False)
rows=native();rows.insert(4,{"type":"event_msg","payload":{"type":"task_started","turn_id":"other-turn"}})
test("error_in_other_active_turn_rejected",lambda:check(rows),False)
rows=native();rows[4]["payload"]["turn_id"]="other-turn"
test("explicit_conflicting_error_turn_rejected",lambda:check(rows),False)
v,r=observed();v["last_user_text_block_raw_sha256"]=[new.digest(prompt+"\n\n")];r["last_user_text_block_raw_sha256"]=v["last_user_text_block_raw_sha256"]
test("wire_extra_LF_with_same_lossy_hash_rejected",lambda:check(v=v,r=r),False)
v,r=observed();r.pop("last_user_text_block_raw_sha256")
test("missing_lossless_wire_metadata_rejected",lambda:check(r=r),False)
v,r=observed();v["last_user_text_block_raw_sha256"]=[new.digest(prompt+"\n")];r["last_user_text_block_raw_sha256"]=v["last_user_text_block_raw_sha256"]
test("wire_equals_actual_editor_LF_native_item",lambda:check(native(actual=prompt+"\n"),v=v,r=r),True)
v,r=observed();v["last_user_text_block_raw_sha256"]=[new.digest(prompt+"\r\n")];r["last_user_text_block_raw_sha256"]=v["last_user_text_block_raw_sha256"]
test("wire_CRLF_difference_from_LF_native_rejected",lambda:check(native(actual=prompt+"\n"),v=v,r=r),False)
rows=native();error=rows.pop(4)["payload"];error={k:v for k,v in error.items()if k!="type"};rows[-1]["payload"]["error"]=error
test("native_taskComplete_nested_HTTP413_accepted",lambda:check(rows),True)
rows=native();rows.pop(4)
test("bare_taskComplete_without_error_rejected",lambda:check(rows),False)
rows=native();rows.pop(4);rows[-1]["payload"]["error"]={"message":"unexpected status 413 Payload Too Large: oldbody","codex_error_info":{"http_connection_failed":{"http_status_code":503}}}
test("taskComplete_conflicting503_rejected",lambda:check(rows),False)
rows=native();rows.pop(4);rows[-1]["payload"]["turn_id"]="other-turn";rows[-1]["payload"]["error"]={"message":"unexpected status 413 Payload Too Large: fake","codex_error_info":{"http_connection_failed":{"http_status_code":413}}}
test("taskComplete_wrong_actual_turn_rejected",lambda:check(rows),False)
# Deliberately synthetic native-shaped409, not exported actual user/session data.
actual_error={"message":"Fixture target thread already has an in-flight request","codex_error_info":{"http_connection_failed":{"http_status_code":409}}}
rows=native();rows.pop(4);rows[-1]["payload"]["error"]=actual_error
test("synthetic_native_shaped409_never_counts_as413",lambda:check(rows),False)
report={"schema":"finite-fault-strict-proof-pure-negative-controls/v1","at":time.time(),"proof_sha256":hashlib.sha256((ROOT/"proof_contract.py").read_bytes()).hexdigest(),"passed":len(cases),"cases":cases,"historical_private_failures_exported":False,"actual_App_fixture_server_model_or_credential_actions":0,"qualification_scope":"Public pure mock evidence validator only; no actual App/runtime acceptance claim."}
out=args.output;out.parent.mkdir(parents=True,exist_ok=True);out.open("xb").write((json.dumps(report,indent=2)+"\n").encode())
print(json.dumps({"passed":len(cases),"result":str(out),"sha256":hashlib.sha256(out.read_bytes()).hexdigest(),"App_action":False}))
