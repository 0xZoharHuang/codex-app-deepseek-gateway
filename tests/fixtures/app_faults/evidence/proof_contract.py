"""Pure strict evidence joins for current native App QA. No model/tool/process actions."""
import hashlib,json,re
from pathlib import Path
def digest(v):return hashlib.sha256(v if isinstance(v,bytes)else v.encode()).hexdigest()
def canonical(v):return json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=False,allow_nan=False).encode()
def normalize(v):return v.replace("\r\n","\n").rstrip("\n")
def require(ok,msg):
 if not ok:raise ValueError(msg)
def exact_native_prompt(actual,expected):
 # Stock editor may append one LF. This is the complete allowed equivalence;
 # never remove arbitrarily many trailing newlines from the actual user item.
 if not isinstance(actual,str) or not isinstance(expected,str):return False
 actual=actual.replace("\r\n","\n");expected=expected.replace("\r\n","\n")
 return actual==expected or actual==expected+"\n"

def native_prompt(rows,root,prompt,model,*,return_text=False):
 metas=[x["payload"] for x in rows if x.get("type")=="session_meta"]
 require(len(metas)==1 and metas[0].get("id")==root,"Exact native session_meta identity required")
 matches=[]
 for x in rows:
  p=x.get("payload",{})
  if x.get("type")=="response_item"and p.get("type")=="message"and p.get("role")=="user":
   blocks=[v["text"]for v in p.get("content",[])if isinstance(v,dict)and isinstance(v.get("text"),str)]
   if blocks and exact_native_prompt(blocks[-1],prompt):matches.append((x,blocks[-1]))
 require(len(matches)==1,"One actual submitted native prompt required")
 item=matches[0][0]["payload"];turn=item.get("internal_chat_message_metadata_passthrough",{}).get("turn_id")
 require(isinstance(turn,str)and turn,"Native user-item turn identity required")
 contexts=[x["payload"]for x in rows if x.get("type")=="turn_context"and x["payload"].get("turn_id")==turn]
 require(contexts and all(x.get("model")==model for x in contexts),"Actual native model/turn context required")
 return (turn,matches[0][1]) if return_text else turn
def join_main(v4,role,*,root,prompt,model,status,native_prompt_text):
 require(exact_native_prompt(native_prompt_text,prompt),"Exact submitted native text required for wire join")
 wanted=digest(normalize(prompt))
 raw_wanted=digest(native_prompt_text)
 for observed in (v4,role):
  hashes=observed.get("last_user_text_block_raw_sha256")
  require(isinstance(hashes,list)and bool(hashes)and hashes[-1]==raw_wanted,"Lossless physical MAIN text must equal the exact original native prompt; normalized hashes cannot substitute")
 require(v4.get("thread_id")==root and role.get("thread_sha256")==digest(root),"Exact native root required")
 require(v4.get("requested_model")==role.get("requested_model")==model,"Exact MAIN model required")
 require(role.get("native_role")=="MAIN"and role.get("tool_profile_valid")is True and role.get("code_mode_main_shape")is True,"Actual unstructured CodeMode MAIN required")
 require(role.get("body_observation")=="complete","Complete observed body required")
 require(wanted in v4.get("last_user_text_block_sha256",[])and wanted in role.get("last_user_text_block_sha256",[]),"Actual submitted prompt binding required")
 require(v4.get("physical_request_index")is not None and v4["physical_request_index"]==role.get("physical_request_index"),"Physical attempt join required")
 require(v4.get("raw_body_sha256")and v4["raw_body_sha256"]==role.get("raw_body_sha256"),"Exact body join required")
 require(v4.get("http_status")==role.get("http_status")==status,"Observed status join required")
 req=v4.get("response_ids",{}).get("x-gateway-request-id")
 require(req and req==role.get("acceptedGatewayRequestID"),"Actual gateway request join required")
 return req
def paired_hashes(row,call_id):
 wanted=digest(call_id);items=row.get("native_input_items")
 require(isinstance(items,list),"Observed native input item projection required")
 calls=[x for x in items if x.get("call_id_sha256")==wanted and x.get("type")in {"custom_tool_call","function_call"}]
 outs=[x for x in items if x.get("call_id_sha256")==wanted and x.get("type")in {"custom_tool_call_output","function_call_output"}]
 require(len(calls)==len(outs)==1,"Exactly one original call and output required")
 require(type(calls[0].get("index"))is int and type(outs[0].get("index"))is int and calls[0]["index"]<outs[0]["index"],"Original call must precede output")
 require(all(isinstance(x.get("canonical_sha256"),str)and len(x["canonical_sha256"])==64 for x in [calls[0],outs[0]]),"Complete item hashes required")
 return [(x["type"],x["canonical_sha256"])for x in [calls[0],outs[0]]]
def preserved_pair(before,first_after,call_id):
 require(paired_hashes(before,call_id)==paired_hashes(first_after,call_id),"Original completed pair changed after restart")
def json_objects(value,depth=0):
 # Same bounded JSON inspection as the original CodeMode fixture; never parses JS.
 if depth>12:return
 if isinstance(value,dict):
  yield value
  for child in value.values():yield from json_objects(child,depth+1)
 elif isinstance(value,list):
  for child in value:yield from json_objects(child,depth+1)
 elif isinstance(value,str)and len(value)<=2*1024*1024:
  decoder=json.JSONDecoder();at=0
  while at<len(value):
   pos=value.find("{",at)
   if pos<0:break
   try:obj,end=decoder.raw_decode(value,pos)
   except ValueError:at=pos+1;continue
   yield from json_objects(obj,depth+1);at=end
def completed_counter_pair(items,*,call_id,program,case_id,receipt_nonce,ledger_rows):
 calls=[x for x in items if x.get("type")=="custom_tool_call"and x.get("call_id")==call_id]
 outs=[x for x in items if x.get("type")=="custom_tool_call_output"and x.get("call_id")==call_id]
 require(len(calls)==len(outs)==1,"Unique actual native completed pair required")
 c=calls[0]
 require(c.get("status")=="completed"and c.get("name")=="exec"and c.get("namespace")in(None,"functions")and c.get("input")==program,"Authoritative FINAL native source required")
 require(items.index(c)<items.index(outs[0]),"Native call must precede output")
 require(len(ledger_rows)==1 and ledger_rows[0].get("case_id")==case_id
  and ledger_rows[0].get("receipt_nonce")==receipt_nonce,"Exactly one original durable nonce effect required")
 matches=[o for o in json_objects(outs[0].get("output"))if o.get("fixture")is True
  and o.get("case_id")==case_id and o.get("receipt_nonce")==receipt_nonce
  and type(o.get("effect_count"))is int and o["effect_count"]==1]
 require(matches,"Typed native output/durable case/nonce/count1 join required")
 require(case_id in program,"Original bound case absent from source")
 return digest(call_id)
def raw413_native_error(rows,turn,*,main_observation,role_observation,root,prompt,model):
 # A status string is not enough: bind exact native prompt/turn/model, physical
 # MAIN request/body/gateway ID and both observed HTTP statuses before errors.
 actual_turn,actual_text=native_prompt(rows,root,prompt,model,return_text=True)
 require(actual_turn==turn,"Target turn does not match exact native submitted prompt")
 join_main(main_observation,role_observation,root=root,prompt=prompt,model=model,status=413,native_prompt_text=actual_text)
 # Stock0.165 ErrorEvent has no turn_id; retain the original active-turn window.
 starts=[i for i,x in enumerate(rows)if x.get("type")=="event_msg"and x.get("payload",{}).get("type")=="task_started"and x["payload"].get("turn_id")==turn]
 require(len(starts)==1,"One actual target task_started required")
 first=starts[0];last=len(rows)
 for i in range(first+1,len(rows)):
  p=rows[i].get("payload",{})
  if rows[i].get("type")!="event_msg":continue
  if (p.get("type")=="task_started"and p.get("turn_id")!=turn)or(p.get("type")in {"task_complete","turn_aborted"}and p.get("turn_id")==turn):
   last=i+1;break
 errors=[]
 for x in rows[first:last]:
  p=x.get("payload",{})
  if x.get("type")!="event_msg":continue
  if p.get("type")=="error":
   if p.get("turn_id")not in(None,turn):continue
  elif p.get("type")=="task_complete":
   # Current native also persists terminal failures inside TaskComplete.error.
   # The containing real turn binds it; never fill an absent inner turn field.
   if p.get("turn_id")!=turn or not isinstance(p.get("error"),dict):continue
   p=p["error"]
  else:continue
  info=p.get("codex_error_info")
  structured=False
  if isinstance(info,dict)and len(info)==1:
   kind,detail=next(iter(info.items()))
   if kind in {"http_connection_failed","response_too_many_failed_attempts"}and isinstance(detail,dict):
    code=detail.get("http_status_code")
    structured=type(code)is int and code==413
  # Only absent classification can use the exact stock UnexpectedResponseError
  # display prefix. A conflicting structured status never falls through.
  message=p.get("message")
  stock_message=info is None and isinstance(message,str)and re.match(r"\Aunexpected status 413(?: Payload Too Large)?: ",message)is not None
  if structured or stock_message:errors.append(x)
 require(errors,"Native HTTP413 classification or exact stock display required inside matched active MAIN turn; unrelated payload digits cannot pass")
 return errors

def causal_cancel(start,stop,cancel,*,hold_seconds=90):
 require(type(start)in(int,float)and type(stop)in(int,float)and type(cancel)in(int,float)
  and start<stop<cancel<start+hold_seconds,"Hold -> actual GUI Stop -> transport cancellation before natural bound required")
