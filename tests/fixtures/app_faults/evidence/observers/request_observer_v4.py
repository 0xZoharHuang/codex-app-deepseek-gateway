"""Private QA request observer; no product/fixture modification or body rewrite."""
from pathlib import Path
import hashlib,json,time
from aiohttp import web
from codex_app_deepseek.atomic_io import atomic_write

MAX_REQUESTS=64
MAX_TOOL_ARRAYS=8
MAX_TOOL_BYTES=4*1024*1024
MAX_BODY_BYTES=2*1024*1024

def canonical(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode('utf-8')
def digest(value):return hashlib.sha256(value).hexdigest()
def normalize(value):return value.replace(chr(13)+chr(10),chr(10)).rstrip(chr(10))
def scalar(value,limit=256):return value[:limit]if isinstance(value,str)else None

def attach_observer(app,*,expected_bearer,output_dir,expected_submission_path=None,
                    max_requests=MAX_REQUESTS,max_tool_arrays=MAX_TOOL_ARRAYS,max_tool_bytes=MAX_TOOL_BYTES):
    out=Path(output_dir);out.mkdir(parents=True,exist_ok=False)
    state={'authenticated_posts':0,'recorded_requests':0,'unique_tool_arrays':0,'tool_bytes':0,
           'coverage':'complete_so_far','coverage_failures':[],'no_prompts_history_headers_auth_saved':True}
    blobs={};rows={}
    def fail(kind,index):
        state['coverage']='FAILED_INCONCLUSIVE'
        if len(state['coverage_failures'])<64:state['coverage_failures'].append({'kind':kind,'request_index':index})
    def write(path,value):
        try:
            atomic_write(path,json.dumps(value,ensure_ascii=False,indent=2).encode('utf-8'))
        except Exception:fail('observer_output_unavailable',None)
    def summary():write(out/'coverage.json',state)
    def save(row):write(out/('request-%03d.json'%row['request_index']),row);summary()
    def ids(headers):return {name:scalar(headers.get(name),128)for name in ('x-request-id','x-gateway-request-id','x-codex-gateway-request-id','x-cdag-request-id','openai-request-id')if headers.get(name)}
    def association(payload):
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
    @web.middleware
    async def observe(request,handler):
        if request.method!='POST'or request.path!='/v1/responses'or request.headers.get('Authorization')!='Bearer '+expected_bearer or request.headers.get('Origin'):
            return await handler(request)
        state['authenticated_posts']+=1;index=state['authenticated_posts']
        # One authoritative per-observer physical POST index, before any await.
        request['_cadsg_physical_http_attempt']=index
        # Reserve request index before the first await; never discard MAIN after auxiliary requests.
        if index>max_requests:
            fail('request_metadata_budget_exceeded',index);summary();return await handler(request)
        row={'request_index':index,'at':time.time(),'thread_id':scalar(request.headers.get('thread-id')),
             'physical_request_index':request.get('_cadsg_physical_http_attempt'),'http_status':None,
             'request_ids':ids(request.headers),'response_ids':{},'stage':'observing'};rows[index]=row;request['_a20_v4_observer_index']=index
        try:
            if request.content_length is not None and request.content_length>MAX_BODY_BYTES:
                fail('body_exceeds_observation_bound',index);row['body_observation']='oversize_not_read'
            else:
                raw=await request.read();row['raw_body_sha256']=digest(raw);row['raw_body_bytes']=len(raw)
                if len(raw)>MAX_BODY_BYTES:fail('body_exceeds_observation_bound',index)
                else:
                    payload=json.loads(raw)
                    if not isinstance(payload,dict):fail('non_object_request',index)
                    else:
                        row.update(association(payload));tools=payload.get('tools');row['tools_present']='tools'in payload
                        row['tools_count']=len(tools)if isinstance(tools,list)else None
                        if isinstance(tools,list):
                            data=canonical(tools);key=digest(data);row['tools_content_sha256']=key
                            if key not in blobs:
                                if len(blobs)>=max_tool_arrays or state['tool_bytes']+len(data)>max_tool_bytes:
                                    fail('unique_tool_content_budget_exceeded',index);row['tools_stored']=False
                                else:
                                    name='tools-'+key+'.json'
                                    try:
                                        with(out/name).open('xb')as f:f.write(data)
                                        blobs[key]=name;state['tool_bytes']+=len(data);state['unique_tool_arrays']=len(blobs)
                                    except Exception:fail('tool_array_output_unavailable',index)
                            row['tools_stored']=key in blobs;row['tools_file']=blobs.get(key)
        except web.HTTPException:
            # Same aiohttp body limit as the downstream application; keep its rejection.
            fail('body_read_rejected',index);save(row);raise
        except Exception as error:
            fail('metadata_observation_'+type(error).__name__,index)
        state['recorded_requests']+=1;row['stage']='before_handler';save(row)
        try:
            response=await handler(request);row['http_status']=response.status;row['response_ids']=ids(response.headers);return response
        except web.HTTPException as error:row['http_status']=error.status;row['handler_outcome']='http_exception';raise
        except BaseException as error:row['handler_outcome']=type(error).__name__;raise
        finally:row['finished']=time.time();row['stage']='handler_finished';save(row)
    async def prepared(request,response):
        # HTTP status/IDs are transport observations, not a semantic completion verdict.
        if request.method=='POST'and request.path=='/v1/responses':
            row=rows.get(request.get('_a20_v4_observer_index'))
            if row is not None:row['http_status']=response.status;row['response_ids']=ids(response.headers);save(row)
    app.middlewares.insert(0,observe)
    app.on_response_prepare.append(prepared)
    summary()
    return state
