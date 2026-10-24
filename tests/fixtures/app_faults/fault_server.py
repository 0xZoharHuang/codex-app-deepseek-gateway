"""Bounded, opt-in App fault fixture. Does not own an App or native runtime."""
from __future__ import annotations
import argparse, asyncio, contextvars, hashlib, json, os, re, secrets, sqlite3, sys, time, uuid
from contextlib import ExitStack, AsyncExitStack
from urllib.parse import urlsplit
import psutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import aiohttp
from aiohttp import web

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "src"))
from codex_app_deepseek.app_config import _atomic_write, _private
from codex_app_deepseek.bridge import BridgeSettings, create_app, sse_events, OPENROUTER_RESPONSES
from codex_app_deepseek.protocol_state import ProtocolState
from codex_deepseek.protocol import MODEL, encode_sse

MARKER = "CDAG_FAULT_COUNTER_V1"
KINDS = {"http_429", "http_503", "http_401", "text_cut", "tool_incomplete_cut",
         "tool_done_cut", "after_effect_503", "live_tool_incomplete_cut", "live_tool_done_cut",
         "synthetic_success", "passthrough"}
MAX_BODY = 2 * 1024 * 1024
_NATIVE_CONTEXT = contextvars.ContextVar("cdag_qa_native_identity", default=None)

def valid_identity(value):
    return isinstance(value,str) and re.fullmatch(r"[A-Za-z0-9._:/-]{1,256}",value) is not None

def digest(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()

def checked_home(path: str | Path) -> Path:
    path = Path(path).absolute()
    for node in (path, *path.parents):
        if node.is_symlink() or (hasattr(node, "is_junction") and node.is_junction()):
            raise ValueError("Fixture paths cannot use redirected directories")
    if path.resolve() != path:
        raise ValueError("Fixture path must be canonical")
    return path

def read_fixture(home: str | Path) -> dict:
    home = checked_home(home)
    path = home / "fixture.json"
    if path.is_symlink() or path.stat().st_nlink != 1:
        raise ValueError("Fixture manifest must be a private regular file")
    data = json.loads(path.read_text())
    if data.get("schema") != 1 or data.get("fixture") != "cdag-app-faults":
        raise ValueError("Not an owned fault fixture")
    for key in ("gateway_token", "upstream_token", "control_token"):
        if not isinstance(data.get(key), str) or len(data[key]) < 32:
            raise ValueError("Invalid fixture authentication")
    return data

def initialize(home: str | Path) -> dict:
    home = checked_home(home)
    home.mkdir(parents=True, exist_ok=False)
    _private(home, directory=True)
    data = {"schema": 1, "fixture": "cdag-app-faults", "nonce": uuid.uuid4().hex,
            **{key: secrets.token_urlsafe(36) for key in
               ("gateway_token", "upstream_token", "control_token")}}
    _atomic_write(home / "fixture.json", json.dumps(data).encode())
    ledger = CounterLedger(home)
    ledger.close()
    return {"state_dir": str(home), "nonce_sha256": digest(data["nonce"]), "live_enabled": False}

class CounterLedger:
    def __init__(self, home: Path):
        self.path = checked_home(home) / "counter.sqlite"
        if self.path.exists() and (self.path.is_symlink() or self.path.stat().st_nlink != 1):
            raise ValueError("Counter ledger must be an owned regular file")
        self.db = sqlite3.connect(self.path, timeout=5)
        try:
            self.db.executescript("CREATE TABLE IF NOT EXISTS cases(id TEXT PRIMARY KEY, thread_sha256 TEXT);"
                "CREATE TABLE IF NOT EXISTS effects(id INTEGER PRIMARY KEY AUTOINCREMENT,"
                "case_id TEXT NOT NULL, at REAL NOT NULL);")
            if "thread_sha256" not in {r[1] for r in self.db.execute("PRAGMA table_info(cases)")}:
                self.db.execute("ALTER TABLE cases ADD COLUMN thread_sha256 TEXT")
            self.db.commit()
            _private(self.path)
        except BaseException:
            self.db.close()
            raise

    def new_case(self, thread_sha256: str) -> str:
        value = uuid.uuid4().hex
        with self.db: self.db.execute("INSERT INTO cases VALUES (?,?)", (value,thread_sha256))
        return value

    def bind_thread(self, case_id, thread_sha256):
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row=self.db.execute("SELECT thread_sha256 FROM cases WHERE id=?",(case_id,)).fetchone()
            if row is None:raise ValueError("Unknown fixture case")
            if row[0] is None:
                if self.count(case_id):raise ValueError("Old effects have no proven thread ownership")
                self.db.execute("UPDATE cases SET thread_sha256=? WHERE id=?",(thread_sha256,case_id))
            elif row[0]!=thread_sha256:
                raise ValueError("Fixture case belongs to another native thread")

    def exists(self, case_id: str) -> bool:
        return isinstance(case_id, str) and self.db.execute(
            "SELECT 1 FROM cases WHERE id=?", (case_id,)).fetchone() is not None

    def count(self, case_id: str) -> int:
        return self.db.execute("SELECT COUNT(*) FROM effects WHERE case_id=?", (case_id,)).fetchone()[0]

    def record(self, case_id: str) -> dict:
        # No deduplication: replay is visible as another side effect.
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            if not self.exists(case_id):
                raise ValueError("Unknown fixture case")
            if self.db.execute("SELECT thread_sha256 FROM cases WHERE id=?",(case_id,)).fetchone()[0] is None:
                raise ValueError("Fixture case must be armed for one native thread")
            if self.db.execute("SELECT COUNT(*) FROM effects").fetchone()[0] >= 64:
                raise ValueError("Fixture effect budget exhausted")
            self.db.execute("INSERT INTO effects(case_id,at) VALUES (?,?)", (case_id,time.time()))
        return {"fixture": True, "case_id": case_id, "effect_count": self.count(case_id)}

    def close(self):
        self.db.close()

@dataclass
class Plan:
    kind: str
    thread_id: str
    case_id: str
    remaining: int = 1
    fired: int = 0

class FaultHarness:
    def __init__(self, home: Path, *, live_reader=None, max_live_requests=8, lifetime_seconds=900, proxy_url=None):
        self.home = checked_home(home)
        self.fixture = read_fixture(home)
        self.plan: Plan | None = None
        self.live_reader = live_reader
        if type(max_live_requests) is not int or not 1 <= max_live_requests <= 16:
            raise ValueError("Live request budget must be 1 through 16")
        if type(lifetime_seconds) is not int or not 1 <= lifetime_seconds <= 1800:
            raise ValueError("Fixture lifetime must be 1 through 1800 seconds")
        if proxy_url:
            proxy = urlsplit(proxy_url)
            if proxy.scheme not in {"http","https"} or not proxy.hostname or proxy.username or proxy.password:
                raise ValueError("Fixture proxy must be explicit HTTP(S) without embedded credentials")
        self.proxy_url = proxy_url
        with ExitStack() as resources:
            self.ledger = CounterLedger(home)
            resources.callback(self.ledger.close)
            self.state = ProtocolState(home / "protocol.sqlite")
            resources.callback(self.state.close)
            self.live_enabled = False
            self.max_live_requests = max_live_requests
            self.lifetime_seconds = lifetime_seconds
            prior = self.home / "events.json"
            saved = json.loads(prior.read_text()) if prior.exists() else {}
            if saved and (saved.get("nonce_sha256") != digest(self.fixture["nonce"])
                          or not isinstance(saved.get("events"),list) or len(saved["events"]) > 256):
                raise ValueError("Fixture event history belongs to another run")
            self.events: list[dict] = saved.get("events",[])
            self.live_calls = max((row.get("live_request_count",0) for row in self.events),default=0)
            self.requests = max((row.get("request_sequence",0) for row in self.events),default=0)
            self.threads: set[str] = set()
            self.inflight = 0
            self.active_threads = set()
            self.stop = asyncio.Event()
            self.runners = []
            self.client = None
            self.descriptor = None
            self._database_resources = resources.pop_all()

    def event(self, kind: str, **fields):
        if len(self.events) >= 256:
            raise ValueError("Fixture event budget exhausted")
        self.events.append({"at": time.time(), "event": kind, "request_sequence":self.requests,
                            "live_request_count":self.live_calls, **fields})
        _atomic_write(self.home / "events.json", json.dumps({
            "fixture": True, "nonce_sha256": digest(self.fixture["nonce"]),
            "events": self.events}).encode())

    def arm(self, kind: str, thread_id: str, *, case_id=None, repeat=1) -> dict:
        if self.inflight:
            raise ValueError("Cannot change a fault plan during an upstream request")
        if kind not in KINDS or not valid_identity(thread_id):
            raise ValueError("Explicit exact session key and known fault kind are required")
        if type(repeat) is not int or not 1 <= repeat <= 5:
            raise ValueError("Fault repeat must be from 1 through 5")
        if (kind.startswith("live_") or kind=="passthrough") and not self.live_enabled:
            raise ValueError("Live pass-through must be explicitly enabled first")
        case_id = self.ledger.new_case(digest(thread_id)) if case_id is None else case_id
        self.ledger.bind_thread(case_id,digest(thread_id))
        self.plan = Plan(kind,thread_id,case_id,0 if kind=="passthrough" else repeat)
        self.event("armed", fault=kind, thread_sha256=digest(thread_id), case_sha256=digest(case_id))
        return {"fault":kind, "thread_sha256":digest(thread_id), "case_id":case_id,
                "effect_count":self.ledger.count(case_id)}

    def summary(self):
        p = self.plan
        return {"fixture":True, "live_enabled":self.live_enabled, "live_calls":self.live_calls,
                "max_live_requests":self.max_live_requests, "requests":self.requests,
                "observed_thread_sha256":sorted(self.threads), "inflight":self.inflight,
                "plan":None if p is None else {"fault":p.kind,"thread_sha256":digest(p.thread_id),
                    "case_id":p.case_id,"remaining":p.remaining,"fired":p.fired,
                    "effect_count":self.ledger.count(p.case_id)},
                "actual_App_acceptance":False}

    @staticmethod
    def counter_names(payload):
        return {tool["name"] for tool in payload.get("tools", [])
                if isinstance(tool,dict) and tool.get("type")=="function"
                and isinstance(tool.get("name"),str)
                and MARKER in str(tool.get("description",""))}

    @staticmethod
    def case_result_call_ids(payload, case_id):
        def contains(value, depth=0):
            if depth>12:return False
            if isinstance(value,dict):
                if value.get("fixture") is True and value.get("case_id")==case_id and type(value.get("effect_count")) is int and value["effect_count"]>0:
                    return True
                return any(contains(v,depth+1) for v in value.values())
            if isinstance(value,list):return any(contains(v,depth+1) for v in value)
            if isinstance(value,str):
                try:return contains(json.loads(value),depth+1)
                except (ValueError,TypeError):return False
            return False
        items=payload.get("input",[])
        if not isinstance(items,list):return []
        names=FaultHarness.counter_names(payload)
        calls={}
        for item in items:
            if not isinstance(item,dict) or item.get("type")!="function_call":
                continue
            call_id=item.get("call_id")
            if not isinstance(call_id,str) or not call_id:continue
            valid=False
            if item.get("name") in names:
                try:
                    arguments=json.loads(item.get("arguments",""))
                    valid=isinstance(arguments,dict) and arguments.get("case_id")==case_id
                except (ValueError,TypeError):pass
            # An ambiguous repeated ID is not evidence of one specific counter call.
            calls[call_id]=valid if call_id not in calls else False
        return [item["call_id"] for item in items if isinstance(item,dict)
                and item.get("type")=="function_call_output"
                and calls.get(item.get("call_id")) is True and contains(item.get("output"))]

    async def control(self, request):
        if request.headers.get("Authorization") != "Bearer " + self.fixture["control_token"] or request.headers.get("Origin"):
            return web.json_response({"error":"Unauthorized fixture control"},status=401)
        try:
            data = await request.json()
            if not isinstance(data,dict): raise ValueError("Object required")
            action = data.get("action")
            if action == "status" and set(data)=={"action"}:
                return web.json_response(self.summary())
            if action == "arm" and set(data)<={"action","fault","thread_id","case_id","repeat"}:
                return web.json_response(self.arm(data["fault"],data["thread_id"],
                    case_id=data.get("case_id"),repeat=data.get("repeat",1)))
            if action == "disarm" and set(data)=={"action"}:
                if self.inflight: raise ValueError("Request still in flight")
                self.plan = None
                return web.json_response(self.summary())
            if action == "enable_live" and set(data)=={"action"}:
                if self.live_reader is None: raise ValueError("No interactive WinCred reader configured")
                from codex_app_deepseek.isolated_app import WindowsBackend
                identity=WindowsBackend().current()
                if identity["session"]<=0 or identity["elevated"]:
                    raise ValueError("Live mode requires a Limited interactive Windows session")
                self.live_enabled=True
                self.event("live_enabled", max_requests=self.max_live_requests)
                return web.json_response(self.summary())
            if action == "stop" and set(data)=={"action"}:
                self.stop.set()
                return web.json_response({"stopping":True})
            raise ValueError("Unknown control command")
        except (ValueError,KeyError,TypeError):
            return web.json_response({"error":"Invalid fixture control command or unmet condition"},status=400)

    async def upstream(self, request):
        if request.headers.get("Authorization") != "Bearer " + self.fixture["upstream_token"]:
            return web.json_response({"error":"Unauthorized fixture upstream"},status=401)
        if self.requests>=64:
            return web.json_response({"error":"Fixture request budget exhausted"},status=503)
        try:
            payload=await request.json()
            if not isinstance(payload,dict):raise ValueError("Object required")
        except (ValueError,TypeError):
            return web.json_response({"error":"Fixture upstream expects a JSON object"},status=400)
        key=request.headers.get("X-CDAG-QA-Thread-ID")
        session_id=request.headers.get("X-CDAG-QA-Session-ID")
        cache_key=payload.get("prompt_cache_key")
        self.requests+=1
        if not valid_identity(key):
            if not self.live_enabled:
                return web.json_response({"error":"Fixture requires authenticated native thread-id binding"},status=400)
            key = "<unbound-"+uuid.uuid4().hex+">"
        else:
            self.threads.add(digest(key))
        p=self.plan
        if p is None or key!=p.thread_id:
            self.event("session_not_armed",thread_sha256=digest(key))
            if not self.live_enabled:
                return web.json_response({"error":"This exact fixture session is not armed; no model contacted"},status=409)
            # Auxiliary native sessions are never fault targets; the same finite
            # live budget applies. Do not turn session filtering into feature loss.
            p = Plan("pass_through", key, "", 0)
        if key in self.active_threads:
            return web.json_response({"error":"Fixture target thread already has an in-flight request"},status=409)
        if self.inflight>=4:
            return web.json_response({"error":"Fixture concurrent request budget exhausted"},status=503)
        self.inflight+=1
        self.active_threads.add(key)
        try:
            result_calls=self.case_result_call_ids(payload,p.case_id)
            self.event("request",thread_sha256=digest(key),case_sha256=digest(p.case_id),
                       fault=p.kind, effect_count=self.ledger.count(p.case_id),
                       native_session_sha256=digest(session_id) if valid_identity(session_id) else None,
                       cache_affinity_sha256=digest(cache_key) if isinstance(cache_key,str) else None,
                       completed_effect_result_call_sha256=[digest(x) for x in result_calls])
            fire=p.remaining>0 and (p.kind!="after_effect_503"
                or self.ledger.count(p.case_id)>0 and bool(result_calls))
            if fire and not p.kind.startswith("live_"):
                if p.kind in {"tool_incomplete_cut","tool_done_cut"} and len(self.counter_names(payload))!=1:
                    return web.json_response({"error":"Exactly one advertised fixture counter function is required; no other tool synthesized"},status=409)
                p.remaining-=1;p.fired+=1
                self.event("synthetic_fault",fault=p.kind,case_sha256=digest(p.case_id))
                return await self.synthetic(request,payload,p)
            if not self.live_enabled:
                return web.json_response({"error":"Live pass-through is disabled; no model contacted"},status=503)
            return await self.relay(request,payload,p)
        finally:
            self.active_threads.discard(key)
            self.inflight-=1

    async def synthetic(self, request,payload,p):
        if p.kind.startswith("http_") or p.kind=="after_effect_503":
            status=503 if p.kind=="after_effect_503" else int(p.kind[5:])
            return web.json_response({"error":{"type":"synthetic_fixture_fault","message":"Synthetic upstream fault"}},
                status=status,headers={"Retry-After":"1"} if status==429 else {})
        rid="fixture-response-"+uuid.uuid4().hex
        response=web.StreamResponse(headers={"Content-Type":"text/event-stream","X-Fixture-Mode":"synthetic"})
        await response.prepare(request)
        await response.write(encode_sse({"type":"response.created","response":{"id":rid,"status":"in_progress","output":[]}}))
        if p.kind=="synthetic_success":
            message={"id":"fixture-message","type":"message","role":"assistant","status":"completed",
                     "content":[{"type":"output_text","text":"Synthetic fixture recovery only.","annotations":[]}]}
            await response.write(encode_sse({"type":"response.completed","response":{"id":rid,"status":"completed","output":[message]}}))
        elif p.kind=="text_cut":
            await response.write(encode_sse({"type":"response.output_item.added","output_index":0,
                "item":{"id":"fixture-message","type":"message","role":"assistant","status":"in_progress","content":[]}}))
            await response.write(encode_sse({"type":"response.content_part.added","output_index":0,
                "item_id":"fixture-message","content_index":0,"part":{"type":"output_text","text":"","annotations":[]}}))
            await response.write(encode_sse({"type":"response.output_text.delta","item_id":"fixture-message",
                "output_index":0,"content_index":0,"delta":"Synthetic partial output"}))
        else:
            name=next(iter(self.counter_names(payload)))
            arguments=json.dumps({"case_id":p.case_id})
            item={"id":"fixture-call-item-"+uuid.uuid4().hex,"type":"function_call","call_id":"fixture-call-"+uuid.uuid4().hex,
                  "name":name,"arguments":"","status":"in_progress"}
            await response.write(encode_sse({"type":"response.output_item.added","output_index":0,"item":item}))
            await response.write(encode_sse({"type":"response.function_call_arguments.delta","item_id":item["id"],"delta":arguments}))
            if p.kind=="tool_done_cut":
                done={**item,"arguments":arguments,"status":"completed"}
                await response.write(encode_sse({"type":"response.output_item.done","output_index":0,"item":done}))
                self.event("complete_tool_item_exposed",call_id_sha256=digest(item["call_id"]))
                await asyncio.sleep(.2)
        # Intentional EOF: no response terminal in cut cases, never synthesized success.
        await response.write_eof()
        return response

    async def relay(self, request,payload,p):
        if self.live_calls>=self.max_live_requests:
            return web.json_response({"error":"Explicit live-request budget exhausted"},status=503)
        route=payload.get("provider",{})
        if payload.get("model")!=MODEL or route.get("allow_fallbacks") is not False or len(route.get("only",[]))!=1 or route["only"][0] not in {"deepseek","morph/fp8","primeintellect"}:
            return web.json_response({"error":"Unexpected production model/provider route"},status=422)
        self.live_calls+=1
        # This callback reads only the dedicated WinCred target, in this interactive process.
        try:
            credential=self.live_reader()
            if not isinstance(credential,str) or not credential or any(c.isspace() for c in credential):
                raise ValueError("Missing credential")
        except Exception:
            return web.json_response({"error":"Interactive fixture credential unavailable"},status=503)
        self.event("passthrough_request",requested_provider=route["only"][0],thread_sha256=digest(p.thread_id),
                   actual_model_acceptance=False)
        names=self.counter_names(payload)
        tracked={}
        generation_id=None
        async with self.client.post(OPENROUTER_RESPONSES,json=payload,allow_redirects=False,proxy=self.proxy_url,
            headers={"Authorization":"Bearer "+credential,"Content-Type":"application/json",
                     "X-Title":"CDAG isolated fault acceptance"}) as upstream:
            headers={k:v for k,v in upstream.headers.items() if k.lower() in
                     {"content-type","retry-after","x-request-id","x-ratelimit-remaining"}}
            if upstream.status>=300 or not payload.get("stream",False):
                chunks=[]; size=0
                async for chunk in upstream.content.iter_chunked(65536):
                    size+=len(chunk)
                    if size>MAX_BODY:raise ValueError("Fixture upstream body budget exceeded")
                    chunks.append(chunk)
                raw=b"".join(chunks).replace(credential.encode(),b"[REDACTED]")
                self.event("passthrough_http_result",status=upstream.status)
                return web.Response(body=raw,status=upstream.status,headers=headers)
            response=web.StreamResponse(status=upstream.status,headers=headers)
            await response.prepare(request)
            async for event in sse_events(upstream.content,MAX_BODY):
                if event.get("type")=="response.created":
                    value=event.get("response",{}).get("id")
                    if isinstance(value,str) and value:generation_id=value
                item=event.get("item")
                if (event.get("type")=="response.output_item.added" and isinstance(item,dict)
                        and item.get("type")=="function_call" and item.get("name") in names
                        and isinstance(item.get("id"),str) and isinstance(item.get("call_id"),str)):
                    tracked[item["id"]]={**item,"arguments":item.get("arguments","")}
                if event.get("type")=="response.function_call_arguments.delta" and event.get("item_id") in tracked:
                    tracked[event["item_id"]]["arguments"]+=event.get("delta","")
                await response.write(encode_sse(event).replace(credential.encode(),b"[REDACTED]"))
                candidate=item if isinstance(item,dict) else tracked.get(event.get("item_id"))
                bound=False
                if (generation_id and isinstance(candidate,dict) and candidate.get("type")=="function_call"
                        and candidate.get("name") in names and isinstance(candidate.get("id"),str)
                        and isinstance(candidate.get("call_id"),str)):
                    prior=tracked.get(candidate["id"])
                    if prior and prior.get("call_id")==candidate["call_id"] and prior.get("name")==candidate["name"]:
                        try:bound=json.loads(candidate.get("arguments","")).get("case_id")==p.case_id
                        except (ValueError,AttributeError,TypeError):pass
                cut=(p.remaining>0 and bound and
                    ((p.kind=="live_tool_done_cut" and event.get("type")=="response.output_item.done")
                     or (p.kind=="live_tool_incomplete_cut" and event.get("type") in
                         {"response.output_item.added","response.function_call_arguments.delta"})))
                if cut:
                    p.remaining-=1;p.fired+=1
                    self.event("passthrough_stream_cut",fault=p.kind,case_sha256=digest(p.case_id),
                               generation_id_sha256=digest(generation_id),
                               item_id_sha256=digest(candidate["id"]),call_id_sha256=digest(candidate["call_id"]),
                               frame_type=event["type"])
                    break
            await response.write_eof()
            return response

    async def start(self, host="127.0.0.1", bridge_port=0):
        if host!="127.0.0.1":raise ValueError("Fixture binds IPv4 loopback only")
        if type(bridge_port) is not int or not 0<=bridge_port<=65535:
            raise ValueError("Invalid explicit fixture bridge port")
        upstream=web.Application(client_max_size=MAX_BODY)
        upstream.router.add_post("/responses",self.upstream)
        upstream.router.add_post("/control",self.control)
        runner=web.AppRunner(upstream,handler_cancellation=True,access_log=None)
        await runner.setup(); self.runners.append(runner)
        site=web.TCPSite(runner,host,0);await site.start()
        port=site._server.sockets[0].getsockname()[1]
        settings=BridgeSettings(token=self.fixture["gateway_token"],
            accepted_models=(MODEL,"gpt-5.6-luna"),upstream_url=f"http://{host}:{port}/responses",
            connect_timeout=5,read_timeout=60,max_body_bytes=MAX_BODY)
        bridge=create_app(settings,lambda:self.fixture["upstream_token"],self.state,test_upstream=True)
        # QA-only transport instrumentation. The production bridge/body is unchanged.
        # Native thread-id is authoritative; prompt_cache_key is only cache affinity.
        @web.middleware
        async def native_identity(request,handler):
            identity=None
            if (request.method=="POST" and request.path=="/v1/responses"
                    and request.headers.get("Authorization")=="Bearer "+self.fixture["gateway_token"]
                    and not request.headers.get("Origin")):
                thread_id=request.headers.get("thread-id")
                session_id=request.headers.get("session-id")
                if valid_identity(thread_id):
                    identity={"thread_id":thread_id,"session_id":session_id if valid_identity(session_id) else None}
            token=_NATIVE_CONTEXT.set(identity)
            try:return await handler(request)
            finally:_NATIVE_CONTEXT.reset(token)
        bridge.middlewares.append(native_identity)
        async def identity_trace(application):
            sessions=[value for value in application.values() if isinstance(value,aiohttp.ClientSession)]
            if len(sessions)!=1:
                raise RuntimeError("Fixture cannot identify the production bridge's own upstream session")
            trace=aiohttp.TraceConfig()
            async def start_request(session,context,params):
                identity=_NATIVE_CONTEXT.get()
                if str(params.url)==settings.upstream_url and identity is not None:
                    params.headers["X-CDAG-QA-Thread-ID"]=identity["thread_id"]
                    if identity["session_id"] is not None:
                        params.headers["X-CDAG-QA-Session-ID"]=identity["session_id"]
            trace.on_request_start.append(start_request)
            trace.freeze()
            sessions[0].trace_configs.append(trace)
            try:yield
            finally:sessions[0].trace_configs.remove(trace)
        bridge.cleanup_ctx.append(identity_trace)
        runner=web.AppRunner(bridge,handler_cancellation=True,access_log=None)
        await runner.setup(); self.runners.append(runner)
        site=web.TCPSite(runner,host,bridge_port);await site.start()
        bridge_port=site._server.sockets[0].getsockname()[1]
        self.client=aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=90),trust_env=False,
                                         cookie_jar=aiohttp.DummyCookieJar())
        self.descriptor={"fixture":True,"gateway_url":f"http://{host}:{bridge_port}/v1",
             "control_url":f"http://{host}:{port}/control","pid":os.getpid(),
             "process_created":psutil.Process().create_time(),
             "nonce_sha256":digest(self.fixture["nonce"]),"live_enabled":False,
             "lifetime_seconds":self.lifetime_seconds,
             "bridge_source_sha256":hashlib.sha256((ROOT/"src/codex_app_deepseek/bridge.py").read_bytes()).hexdigest(),
             "actual_App_acceptance":False}
        _atomic_write(self.home/"ready.json",json.dumps(self.descriptor).encode())
        return self.descriptor

    async def close(self):
        # Every owned resource is attempted even when an earlier close fails or
        # is cancelled; AsyncExitStack preserves the exception for the caller.
        async with AsyncExitStack() as resources:
            resources.callback(self._database_resources.close)
            if self.client: resources.push_async_callback(self.client.close)
            for runner in self.runners: resources.push_async_callback(runner.cleanup)

def counter_server(home):
    from mcp.server import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from mcp.types import ToolAnnotations
    read_fixture(home)
    mcp=MCPServer("CDAG fault counter",version="1.0.0",log_level="WARNING")
    @mcp.tool(annotations=ToolAnnotations(readOnlyHint=False,destructiveHint=False,
               idempotentHint=False,openWorldHint=False),structured_output=True)
    def record_effect(case_id: str) -> dict[str,Any]:
        """CDAG_FAULT_COUNTER_V1: append one real fixture effect; every retry increments the count. Only pre-registered case IDs are accepted."""
        ledger=CounterLedger(Path(home))
        try:return ledger.record(case_id)
        except ValueError:raise ToolError("Invalid case or effect budget exhausted") from None
        finally:ledger.close()
    return mcp

async def serve(home,proxy_url=None,reuse_port=False):
    from codex_app_deepseek.credentials import read,UPSTREAM_TARGET
    harness=FaultHarness(Path(home),live_reader=lambda:read(UPSTREAM_TARGET),proxy_url=proxy_url)
    try:
        port=0
        if reuse_port:
            previous=json.loads((Path(home)/"ready.json").read_text())
            endpoint=urlsplit(previous["gateway_url"])
            if previous.get("nonce_sha256")!=digest(harness.fixture["nonce"]) or endpoint.hostname!="127.0.0.1" or endpoint.scheme!="http" or not endpoint.port:
                raise ValueError("Cannot reuse a foreign fixture endpoint")
            port=endpoint.port
        print(json.dumps(await harness.start(bridge_port=port)),flush=True)
        try:await asyncio.wait_for(harness.stop.wait(),harness.lifetime_seconds)
        except asyncio.TimeoutError:pass
        harness.event("stopped",requests=harness.requests,live_calls=harness.live_calls,actual_model_acceptance=False)
    finally:await harness.close()

async def control_command(home,command):
    fixture=read_fixture(home)
    ready=json.loads((Path(home)/"ready.json").read_text())
    target=urlsplit(ready["control_url"])
    if (ready.get("nonce_sha256")!=digest(fixture["nonce"]) or target.scheme!="http"
            or target.hostname!="127.0.0.1" or not target.port or target.path!="/control"
            or target.username or target.password or target.query or target.fragment):
        raise ValueError("Control descriptor does not identify this loopback fixture")
    async with aiohttp.ClientSession(trust_env=False) as client:
        async with client.post(ready["control_url"],json=command,
            headers={"Authorization":"Bearer "+fixture["control_token"]}) as response:
            result=await response.json()
            if response.status!=200:raise ValueError("Fixture control rejected command")
            print(json.dumps(result))

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode",choices=["init","serve","auth-token","counter","control"])
    parser.add_argument("--state-dir",required=True)
    parser.add_argument("--command-json")
    parser.add_argument("--proxy-url")
    parser.add_argument("--reuse-port",action="store_true")
    args=parser.parse_args()
    if args.mode=="init":print(json.dumps(initialize(args.state_dir)))
    elif args.mode=="auth-token":print(read_fixture(args.state_dir)["gateway_token"])
    elif args.mode=="counter":counter_server(args.state_dir).run(transport="stdio")
    elif args.mode=="control":asyncio.run(control_command(args.state_dir,json.loads(args.command_json)))
    else:asyncio.run(serve(args.state_dir,args.proxy_url,args.reuse_port))

if __name__=="__main__":main()
