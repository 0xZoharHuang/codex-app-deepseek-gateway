"""Opt-in QA-only typed-image metadata trace for the existing bounded fault harness."""
from __future__ import annotations
import asyncio,base64,contextvars,hashlib,json,time,uuid
from pathlib import Path
from urllib.parse import urlsplit
import aiohttp
from aiohttp import web

_CTX=contextvars.ContextVar("cadsg_qa_image_trace",default=None)
_LOCAL_HEADER="X-CADSG-QA-Image-Trace"
UNSET=object()
_SAFE_KEYS={"input","output","content","items","response","results"}
def digest(v):return hashlib.sha256(v if isinstance(v,bytes) else v.encode("utf-8")).hexdigest()
def safe_key(k):return k if k in _SAFE_KEYS else "key-sha256-"+digest(k)[:12]

def image_summary(body):
    """Native H ImageReference union only. Never JSON-decode text/arguments."""
    found=[];visited=0;limited=False
    def walk(value,path=(),call=None,depth=0):
        nonlocal visited,limited
        visited+=1
        if visited>100000 or depth>128:limited=True;return
        if isinstance(value,list):
            for i,item in enumerate(value):walk(item,path+(i,),call,depth+1)
        elif isinstance(value,dict):
            kind=value.get("type")
            if kind in {"function_call","function_call_output","custom_tool_call","custom_tool_call_output"} and isinstance(value.get("call_id"),str):
                call=digest(value["call_id"])
            if kind=="input_image":
                image={"type":"input_image","path":list(path),"call_id_sha256":call}
                detail=value.get("detail")
                if detail is not None:image["detail"]=detail if detail in {"auto","low","high","original"} else "unsupported"
                url=value.get("image_url");fid=value.get("file_id")
                if isinstance(url,str) and fid is None:
                    scheme=urlsplit(url).scheme.lower()
                    image.update(reference="image_url",scheme=scheme if scheme in {"data","http","https","file"} else "other",
                        reference_sha256=digest(url),reference_chars=len(url))
                    if scheme=="data":
                        header,sep,payload=url.partition(",")
                        mime=header[5:].split(";",1)[0]
                        image["mime"]=mime if mime in {"image/png","image/jpeg","image/webp","image/gif"} else "other"
                        try:
                            if not sep or not header.endswith(";base64"):raise ValueError("Not base64")
                            decoded=base64.b64decode(payload,validate=True)
                            image.update(data_bytes=len(decoded),data_sha256=digest(decoded),data_decode="valid_base64")
                        except (ValueError,TypeError):image["data_decode"]="invalid_or_unsupported"
                elif isinstance(fid,str) and url is None:
                    image.update(reference="file_id",scheme="file_id",reference_sha256=digest(fid),reference_chars=len(fid))
                else:image.update(reference="invalid_or_ambiguous",scheme="unknown")
                found.append(image);return
            # Text content/arguments are never interpreted as image objects.
            if kind in {"input_text","output_text","reasoning_text","summary_text"}:return
            for key,child in value.items():
                if isinstance(child,(dict,list)):walk(child,path+(safe_key(str(key)),),call,depth+1)
    walk(body.get("input") if isinstance(body,dict) else None,("input",))
    return {"typed_image_count":len(found),"images":found,"scan_complete":not limited}

class ImageTrace:
    def __init__(self,core,path,max_capture_bytes=2*1024*1024):
        self.core=core;self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True)
        self.path.touch(exist_ok=False);core._private(self.path)
        self.max_capture_bytes=max_capture_bytes;self.sequence=0;self.failures=[];self.owners={}
        self.active={};self.clients=[];self.factory_facts=[]
    def emit(self,entry):
        try:
            if self.sequence>=512:
                if "event_budget_exhausted" not in self.failures:self.failures.append("event_budget_exhausted")
                return
            self.sequence+=1
            record={"schema":1,"sequence":self.sequence,"at":time.time(),**entry}
            with self.path.open("ab") as out:out.write((json.dumps(record,separators=(",",":"))+"\n").encode())
        except Exception as exc:self.failures.append(type(exc).__name__)
    def identity(self,obs):return {"trace_id":obs["id"],"thread_id_sha256":obs["thread"],"session_id_sha256":obs["session"]}
    def trace_config(self,role,url):
        trace=aiohttp.TraceConfig()
        async def start(session,context,params):
            obs=_CTX.get()
            context.observation=obs if obs and str(params.url)==url else None
            context.buffer=bytearray();context.total=0
            if context.observation and role=="bridge_wire":
                params.headers[_LOCAL_HEADER]=obs["id"] # loopback fixture only
        async def chunk(session,context,params):
            if not getattr(context,"observation",None):return
            data=params.chunk;context.total+=len(data)
            if context.total<=self.max_capture_bytes:context.buffer.extend(data)
            else:context.buffer.clear()
        async def finish(session,context,params):
            obs=getattr(context,"observation",None)
            if not obs:return
            if context.total<=self.max_capture_bytes:
                try:summary=image_summary(json.loads(context.buffer))
                except Exception as exc:summary={"scan_complete":False,"parse_error_type":type(exc).__name__}
            else:summary={"scan_complete":False,"capture_budget_exceeded":True}
            self.emit({**self.identity(obs),"stage":role,"body_bytes":context.total,**summary})
        trace.on_request_start.append(start);trace.on_request_chunk_sent.append(chunk)
        trace.on_request_end.append(finish);trace.on_request_exception.append(finish);trace.freeze()
        return trace
    def attach_client(self,client,role,url):
        trace=self.trace_config(role,url);client.trace_configs.append(trace);self.clients.append((client,trace))
    def create_app(self,original,*args,**kwargs):
        settings=args[0]
        self.factory_facts.append({"image_transport_factory_supplied":kwargs.get("image_transport_factory") is not None,
            "test_upstream":kwargs.get("test_upstream",False),"trace_capture_budget":self.max_capture_bytes})
        app=original(*args,**kwargs)
        @web.middleware
        async def incoming(request,handler):
            headers=request.headers
            admitted=(request.method=="POST" and request.path=="/v1/responses"
                and headers.get("Authorization")=="Bearer "+settings.token and not headers.get("Origin")
                and len(headers.getall("thread-id",[]))==1 and self.core.valid_identity(headers.get("thread-id")))
            if not admitted:return await handler(request)
            obs={"id":uuid.uuid4().hex,"thread":digest(headers["thread-id"]),
                "session":digest(headers["session-id"]) if self.core.valid_identity(headers.get("session-id")) else None}
            self.active[obs["id"]]=obs;token=_CTX.set(obs)
            try:
                try:summary=image_summary(await request.json())
                except Exception as exc:summary={"scan_complete":False,"parse_error_type":type(exc).__name__}
                self.emit({**self.identity(obs),"stage":"native_incoming",**summary})
                response=await handler(request)
                receipt=response.headers.get("X-Gateway-Request-Id")
                self.emit({**self.identity(obs),"stage":"response_receipt","gateway_request_id":receipt,
                    "http_status":response.status,"receipt_present":isinstance(receipt,str) and bool(receipt)})
                return response
            finally:
                _CTX.reset(token);self.active.pop(obs["id"],None)
        app.middlewares.append(incoming)
        async def traces(application):
            clients=[v for v in application.values() if isinstance(v,aiohttp.ClientSession)]
            if len(clients)!=1:raise RuntimeError("QA trace cannot identify one existing Bridge session")
            self.attach_client(clients[0],"bridge_wire",settings.upstream_url)
            yield
        app.cleanup_ctx.append(traces);return app
    async def start(self,harness,*,image_transport_factory=UNSET,provider_url=None):
        core=self.core;original_factory=core.create_app;original_upstream=harness.upstream
        async def traced_upstream(request):
            obs=self.active.get(request.headers.get(_LOCAL_HEADER))
            token=_CTX.set(obs)
            try:return await original_upstream(request)
            finally:_CTX.reset(token)
        harness.upstream=traced_upstream
        def factory(*args,**kwargs):
            if image_transport_factory is not UNSET:kwargs["image_transport_factory"]=image_transport_factory
            return self.create_app(original_factory,*args,**kwargs)
        core.create_app=factory
        try:ready=await harness.start()
        finally:core.create_app=original_factory
        self.attach_client(harness.client,"provider_wire",provider_url or core.OPENROUTER_RESPONSES)
        return ready
    def snapshot(self):
        return {"events":self.sequence,"trace_failures":self.failures,"factory_facts":self.factory_facts,
            "active_requests":len(self.active),"raw_body_prompt_CoT_image_URL_header_logged":False}
