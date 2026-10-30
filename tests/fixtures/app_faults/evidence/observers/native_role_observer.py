"""Metadata-only slice of reviewed role observer; no request or fault decisions."""
from pathlib import Path
import contextvars,hashlib,json,time,uuid,importlib.util,sys
import aiohttp
from aiohttp import web
from codex_app_deepseek.atomic_io import atomic_write
p=Path(__file__).with_name("a21_role_contract.py")
s=importlib.util.spec_from_file_location("r209_original_role_contract",p)
role=importlib.util.module_from_spec(s);sys.modules[s.name]=role;s.loader.exec_module(role)
def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
class NativeRoleObserver:
    """QA observer and slot selector, never an authenticator or provider runtime."""
    def __init__(self,harness,output,profiles_path,summary_prompt_sha256=None,summary_model=None):
        self.h=harness;self.output=Path(output);self.output.mkdir(parents=True,exist_ok=False)
        self.profiles_path=Path(profiles_path);raw=self.profiles_path.read_bytes()
        if len(raw)>65536:raise ValueError("Profile bound")
        self.profile_file_sha=hashlib.sha256(raw).hexdigest()
        self.profiles=role.profile_projection(json.loads(raw));self.profile_sha=role.profiles_sha256(self.profiles)
        if summary_prompt_sha256 is not None and (len(summary_prompt_sha256)!=64 or any(c not in"0123456789abcdef"for c in summary_prompt_sha256)):
            raise ValueError("Explicit effective compact prompt SHA required")
        self.summary_prompt_sha=summary_prompt_sha256
        if summary_model is not None and (not isinstance(summary_model,str) or not summary_model or len(summary_model)>256):raise ValueError("Explicit summary model invalid")
        self.summary_model=summary_model
        self.declared=time.time();self.epoch=uuid.uuid4().hex;self.context=contextvars.ContextVar("a09-role-"+self.epoch,default=None)
        self.rows={};self.by_trace={};self.capacity=None;self.original_upstream=harness.upstream
        self.state={"status":"complete_so_far","posts":0,"recorded":0,"failures":[],"deadline":harness.deadline,
                    "max_requests":64,"max_body_bytes":2*1024*1024,"epoch":self.epoch,
                    "profiles_sha256":self.profile_sha,"profiles_declared_at":self.declared,
                    "summary_prompt_sha256":summary_prompt_sha256,"summary_model":summary_model,
                    "fixture_nonce_sha256":role.digest(harness.fixture["nonce"].encode()),
                    "App_acceptance":False,"no_body_prompts_tokens_or_environment_saved":True}
        self.write("declaration.json",{"profiles":self.profiles,"profile_file_sha256":self.profile_file_sha,**self.state})
        self.save()
    def write(self,name,value):
        p=self.output/name
        # Installed writer captures target identity+SHA and repeats that CAS on
        # every bounded retry; keep its one-temp/finally cleanup semantics.
        atomic_write(p,(json.dumps(value,indent=2,ensure_ascii=True)+"\n").encode("utf-8"))
    def fail(self,kind,index=None):
        self.state["status"]="FAILED_INCONCLUSIVE"
        if len(self.state["failures"])<64:self.state["failures"].append({"kind":kind,"request_index":index})
    def save(self,row=None):
        if row is not None:self.write("request-%03d.json"%row["request_index"],row)
        self.write("coverage.json",self.state)
    def profiles_valid(self):
        try:ok=sha(self.profiles_path)==self.profile_file_sha
        except OSError:ok=False
        if not ok:self.fail("profiles_changed_after_declaration")
        return ok
    def classify(self,payload):
        facts=role.request_association(payload);facts.update(role.payload_role_facts(payload))
        facts["stream"]=payload.get("stream")
        if self.profiles_valid() and payload.get("stream") is True and any(role.matches_main_profile(facts,p)for p in self.profiles):
            return "MAIN",facts
        # Local compaction is distinct from MAIN. Its exact configured/native
        # compact prompt must be supplied before this observer starts; prefix-only
        # selection is still independently checked against actual checkpoint/rollout.
        simple=(payload.get("tools") in(None,[]) and payload.get("stream") is True
            and facts.get("purpose_metadata_valid")is True and facts.get("purpose_tags")=={}
            and facts.get("text_format_valid")is True and facts.get("response_format_valid")is True
            and facts.get("text_format_type")in(None,"text") and facts.get("response_format_type")in(None,"text")
            and not facts.get("text_format_schema_name") and not facts.get("response_format_schema_name")
            and payload.get("tool_choice")in(None,"auto","none"))
        if (self.profiles_valid() and simple and self.summary_prompt_sha is not None
            and facts.get("requested_model")in({self.summary_model}if self.summary_model else{p["requested_model"]for p in self.profiles})
            and facts.get("last_user_content_normalized_sha256")==self.summary_prompt_sha):
            return "NATIVE_COMPACT_PROMPT_CANDIDATE",facts
        return "UNKNOWN_OR_AUX",facts
    def capture_cached_native(self,request,row,stage="bridge_cached_body_before_upstream_trace"):
        raw=getattr(request,"_read_bytes",None)
        if not isinstance(raw,bytes):
            self.fail("native_handler_body_not_cached",row["request_index"]);return
        row.update(raw_body_sha256=role.digest(raw),raw_body_bytes=len(raw),captured_stage=stage)
        if len(raw)>2*1024*1024:self.fail("body_bound",row["request_index"]);return
        try:
            payload=json.loads(raw)
            if not isinstance(payload,dict):raise ValueError("Object required")
            label,facts=self.classify(payload);row.update(facts);row["native_role"]=label
            items=payload.get("input")
            if isinstance(items,list):
                row["native_input_items"]=[{"index":i,"canonical_sha256":role.digest(role.canonical(item)),
                    "type":item.get("type")if isinstance(item,dict)else None,
                    "role":item.get("role")if isinstance(item,dict)else None,
                    "call_id_sha256":role.digest(item["call_id"].encode())if isinstance(item,dict)and isinstance(item.get("call_id"),str)else None}
                    for i,item in enumerate(items)]
            row["body_observation"]="complete"
        except Exception as e:self.fail("native_projection_"+type(e).__name__,row["request_index"])
    def attach(self,app):
        @web.middleware
        async def observe(request,handler):
            if (request.method!="POST"or request.path!="/v1/responses"
                or request.headers.get("Authorization")!="Bearer "+self.h.fixture["gateway_token"]or request.headers.get("Origin")):
                return await handler(request)
            self.state["posts"]+=1;i=self.state["posts"];row=None
            if i<=64:
                row={"request_index":i,"at":time.time(),"outerQARequestID":uuid.uuid4().hex,
                     "physical_request_index":request.get("_cadsg_physical_http_attempt"),
                     "thread_sha256":role.digest(request.headers.get("thread-id","").encode()),
                     "epoch":self.epoch,"profiles_sha256":self.profile_sha,"profiles_declared_at":self.declared,
                     "body_observation":"not_observed","native_role":"UNKNOWN_OR_AUX","upstreamRoleRequestIDs":[]}
                self.rows[i]=row
            else:self.fail("metadata_budget",i)
            frame={"native_request":request,"row":row};token=self.context.set(frame)
            try:
                response=await handler(request)
                if row is not None:
                    row["http_status"]=response.status
                    row["acceptedGatewayRequestID"]=response.headers.get("X-Gateway-Request-ID")
                return response
            finally:
                self.context.reset(token)
                if row is not None:
                    # A cached-only after-handler fallback observes never-forwarded failures.
                    # No read(), clone(), auth decision or stream timing is changed.
                    if row["body_observation"]=="not_observed":self.capture_cached_native(request,row,"post_handler_cached_body_no_upstream")
                    row["finished"]=time.time();self.state["recorded"]+=1
                    if row["finished"]>self.h.deadline:self.fail("lifetime_bound",i)
                    self.save(row)
                else:self.save()
        app.middlewares.insert(0,observe)
        async def trace_context(application):
            sessions=[v for v in application.values()if isinstance(v,aiohttp.ClientSession)]
            if len(sessions)!=1:raise ValueError("Original Bridge session not unique")
            trace=aiohttp.TraceConfig()
            async def start_request(session,context,params):
                frame=self.context.get()
                if (frame is None or str(params.url).split("://",1)[-1].split("/",1)[0].split(":")[0]!="127.0.0.1"
                    or params.headers.get("Authorization")!="Bearer "+self.h.fixture["upstream_token"]):
                    return
                row=frame["row"]
                if row is None:return
                self.capture_cached_native(frame["native_request"],row)
                trace_id=uuid.uuid4().hex;row["upstreamRoleRequestIDs"].append(trace_id);self.by_trace[trace_id]=row
                # Same QA-only identity-header pattern as original native identity trace.
                params.headers["X-CDAG-QA-Role-Request-ID"]=trace_id;self.save(row)
            trace.on_request_start.append(start_request);trace.freeze();sessions[0].trace_configs.append(trace)
            try:yield
            finally:sessions[0].trace_configs.remove(trace)
        app.cleanup_ctx.append(trace_context)
