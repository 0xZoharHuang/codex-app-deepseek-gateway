"""Private A21 native role observations; pure V4/CodeMode QA predicates, not runtime auth."""
from pathlib import Path
import copy,hashlib,json
from codex_deepseek.protocol import Registry
MAX_BODY_BYTES=2*1024*1024
MAX_REQUESTS=64
def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(",",":"),allow_nan=False).encode()
def digest(value):return hashlib.sha256(value).hexdigest()
def normalize(value):return value.replace(chr(13)+chr(10),chr(10)).rstrip(chr(10))
def scalar(value,limit=256):return value[:limit]if isinstance(value,str)else None
CUSTOM_PARAMETERS={"type":"object","properties":{"input":{"type":"string"}},"required":["input"],"additionalProperties":False}

def request_association(payload,expected_submission_path=None):
        value=payload.get('input');last_user=None
        if isinstance(value,str):last_user=value
        elif isinstance(value,list):
            users=[item for item in value if isinstance(item,dict)and item.get('role')=='user']
            if users:last_user=users[-1].get('content')
        blocks=[]
        if isinstance(last_user,str):blocks=[last_user]
        elif isinstance(last_user,list):blocks=[item['text']for item in last_user if isinstance(item,dict)and isinstance(item.get('text'),str)]
        joined=chr(10).join(blocks)
        text=payload.get('text');fmt=text.get('format')if isinstance(text,dict)else None
        rf=payload.get('response_format');schema=rf.get('json_schema')if isinstance(rf,dict)else None
        meta=payload.get('metadata');tags={}
        if isinstance(meta,dict):
            tags={key:str(meta[key])[:128]for key in ('purpose','agent_role','originator','schema_name')if key in meta and isinstance(meta[key],(str,int,bool))}
        expected={}
        if expected_submission_path is not None:
            try:expected=json.loads(Path(expected_submission_path).read_text('utf-8'))
            except (OSError,ValueError):pass
        choice=payload.get('tool_choice')
        if isinstance(choice,str)and choice not in{'auto','none','required'}:choice={'type':'unknown_string','sha256':digest(choice.encode()),'length':len(choice)}
        if not isinstance(choice,str):
            choice={'type':scalar(choice.get('type')),'name':scalar(choice.get('name')),'canonical_sha256':digest(canonical(choice))}if isinstance(choice,dict)else None
        last=digest(normalize(joined).encode())if blocks else None
        return {'requested_model':scalar(payload.get('model')),'text_format_type':scalar(fmt.get('type'))if isinstance(fmt,dict)else None,
                'text_format_schema_name':scalar(fmt.get('name'))if isinstance(fmt,dict)else None,
                'response_format_schema_name':scalar(schema.get('name'))if isinstance(schema,dict)else None,
                'purpose_tags':tags,'tool_choice':choice,'last_user_content_normalized_sha256':last,
                'last_user_text_block_sha256':[digest(normalize(block).encode())for block in blocks[-16:]],'last_user_text_block_count':len(blocks),
                'last_user_text_block_raw_sha256':[digest(block.encode('utf-8'))for block in blocks[-16:]],
                'expected_submitted_prompt_sha256':expected.get('normalized_prompt_sha256'),
                'exact_submitted_prompt_match':bool(last and last==expected.get('normalized_prompt_sha256'))}

def exec_tool(payload):
    matches=[t for t in payload.get("tools",[]) if isinstance(t,dict)
        and t.get("type")=="function" and isinstance(t.get("name"),str)
        and t.get("parameters")==CUSTOM_PARAMETERS and isinstance(t.get("description"),str)
        and any(t.get("description","").startswith("Logical tool: "+n+".\n") for n in ("exec","functions.exec"))
        and "ALL_TOOLS" in t.get("description","")
        and "Native input format (validated before tool execution):" in t.get("description","")]
    return matches[0] if len(matches)==1 else None

def _code_mode_recovery_main(payload,*,selected_tool=None):
    """QA recovery accepts only an unstructured Code Mode continuation.

    Structured JSON MAIN requests are outside this QA profile; this is not a
    production model/tool capability restriction.
    """
    if not isinstance(payload,dict) or not isinstance(payload.get("tools"),list):return False
    declared_exec=exec_tool(payload)
    tool=selected_tool if selected_tool is not None else declared_exec
    if declared_exec is None or tool is None:return False
    for value in (payload.get("text",{}),payload.get("response_format",{})):
        if value is None:continue
        if not isinstance(value,dict):return False
        format_value=value.get("format",{}) if "format" in value else value
        if not isinstance(format_value,dict) or format_value.get("type") not in (None,"text"):return False
    choice=payload.get("tool_choice","auto")
    if choice is None or isinstance(choice,str) and choice in ("auto","required"):return True
    return isinstance(choice,dict) and choice.get("type")=="function" and choice.get("name")==tool["name"]

def payload_role_facts(payload):
    """Normalize only tool declarations/choice using the published pure Registry."""
    result={"tool_profile_valid":False,"code_mode_main_shape":False,"exec_declarations":0,
            "text_format_valid":False,"response_format_valid":False,"response_format_type":None}
    if not isinstance(payload,dict):return result
    meta=payload.get("metadata")
    result["purpose_metadata_valid"]=meta is None or isinstance(meta,dict)and all(meta.get(k)is None or isinstance(meta[k],(str,int,bool))for k in("purpose","agent_role","originator","schema_name")if k in meta)
    def format_fact(value):
        if value is None:return True,None
        if not isinstance(value,dict):return False,None
        v=value.get("format",{})if"format"in value else value
        return (isinstance(v,dict),scalar(v.get("type"))if isinstance(v,dict)else None)
    result["text_format_valid"],_=format_fact(payload.get("text",{}))
    result["response_format_valid"],result["response_format_type"]=format_fact(payload.get("response_format",{}))
    tools=payload.get("tools")
    if not isinstance(tools,list):return result
    result["native_tools_sha256"]=digest(canonical(tools))
    try:
        registry=Registry();normalized=registry.tools(tools)
        result["normalized_tools_sha256"]=digest(canonical(normalized))
        selected=exec_tool({"tools":normalized})
        result["exec_declarations"]=sum(1 for t in normalized if exec_tool({"tools":[t]})is not None)
        if selected is None:return result
        result["exec_wire_name"]=selected["name"]
        result["exec_declaration_sha256"]=digest(canonical(selected))
        view={"tools":normalized,"text":copy.deepcopy(payload.get("text",{})),
              "response_format":copy.deepcopy(payload.get("response_format",{}))}
        choice=payload.get("tool_choice","auto")
        if isinstance(choice,dict)and choice.get("type")in{"function","custom"}:
            tool=registry.by_identity.get((choice.get("namespace"),choice.get("name")))
            if tool is None or tool.wire not in registry.active:return result
            choice={"type":"function","name":tool.wire}
        view["tool_choice"]=choice
        result["tool_profile_valid"]=True
        result["code_mode_main_shape"]=_code_mode_recovery_main(view)
    except Exception as error:
        result["normalization_error_type"]=type(error).__name__
    return result

def matches_main_profile(row,profile):
    if not isinstance(profile,dict)or not isinstance(row,dict):return False
    if profile.get("profile_id")!="a21-unstructured-codemode-main-v1":return False
    if profile.get("prompt_source")!="actual_gui_submission":return False
    if row.get("requested_model")!=profile.get("requested_model"):return False
    if not row.get("requested_model")or row.get("code_mode_main_shape")is not True or type(row.get("exec_declarations"))is not int or row["exec_declarations"]!=1:return False
    if row.get("text_format_valid")is not True or row.get("response_format_valid")is not True:return False
    if row.get("text_format_type")not in(None,"text")or row.get("response_format_type")not in(None,"text"):return False
    if row.get("text_format_schema_name")or row.get("response_format_schema_name"):return False
    if row.get("purpose_metadata_valid")is not True or row.get("purpose_tags")!={}or profile.get("permitted_purpose_tags",{})!={}:return False
    wanted=profile.get("normalized_prompt_sha256")
    if not isinstance(wanted,str)or len(wanted)!=64:return False
    if profile.get("prompt_mode")=="whole_last_user":return row.get("last_user_content_normalized_sha256")==wanted
    if profile.get("prompt_mode")=="last_text_block":
        blocks=row.get("last_user_text_block_sha256")
        return isinstance(blocks,list)and bool(blocks)and blocks[-1]==wanted
    return False

def adapted_payload_role_facts(payload):
    """Use the existing CodeMode predicate directly after Bridge normalization."""
    result={"tool_profile_valid":False,"code_mode_main_shape":False,"exec_declarations":0,
            "text_format_valid":False,"response_format_valid":False,"response_format_type":None}
    if not isinstance(payload,dict)or not isinstance(payload.get("tools"),list):return result
    meta=payload.get("metadata")
    result["purpose_metadata_valid"]=meta is None or isinstance(meta,dict)and all(meta.get(k)is None or isinstance(meta[k],(str,int,bool))for k in("purpose","agent_role","originator","schema_name")if k in meta)
    result["text_format_valid"]=payload.get("text")is None or isinstance(payload.get("text",{}),dict)
    result["response_format_valid"]=payload.get("response_format")is None or isinstance(payload.get("response_format",{}),dict)
    fmt=payload.get("response_format")or{}
    if isinstance(fmt,dict):result["response_format_type"]=scalar((fmt.get("format")or fmt).get("type"))
    result["exec_declarations"]=sum(1 for t in payload["tools"]if exec_tool({"tools":[t]})is not None)
    result["tool_profile_valid"]=True
    result["code_mode_main_shape"]=_code_mode_recovery_main(payload)
    return result

PROFILE_DECISION_KEYS=("profile_id","requested_model","normalized_prompt_sha256","prompt_mode","prompt_source")
def profile_projection(profiles):
    if not isinstance(profiles,list)or not profiles or len(profiles)>64:raise ValueError("Explicit frozen profile list required")
    result=[]
    for p in profiles:
        if not isinstance(p,dict)or set(p)-set(PROFILE_DECISION_KEYS)-{"prompt_source_ref_sha256","permitted_purpose_tags"}:
            raise ValueError("Unknown profile fields")
        if p.get("permitted_purpose_tags",{})!={}:raise ValueError("Auxiliary purpose opt-in forbidden")
        v={k:p.get(k)for k in PROFILE_DECISION_KEYS}
        if v["profile_id"]!="a21-unstructured-codemode-main-v1"or v["prompt_source"]!="actual_gui_submission":
            raise ValueError("Explicit current-GUI CodeMode MAIN profile required")
        if v["prompt_mode"]not in{"whole_last_user","last_text_block"}:raise ValueError("Invalid prompt binding")
        if not isinstance(v["requested_model"],str)or not v["requested_model"]:raise ValueError("Selected model required")
        sha=v["normalized_prompt_sha256"]
        if not isinstance(sha,str)or len(sha)!=64 or any(c not in"0123456789abcdef"for c in sha):raise ValueError("Prompt SHA required")
        result.append(v)
    return result
def profiles_sha256(profiles):return digest(canonical(profile_projection(profiles)))
