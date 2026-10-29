"""AA3-derived bounded Code Mode QA hooks; synthetic frames are not native proof."""
from __future__ import annotations
import argparse,asyncio,hashlib,importlib.util,json,secrets,sqlite3,sys,time,uuid
from pathlib import Path
REPO=Path(__file__).resolve().parents[3]
# Prefer the selected installed interpreter's production package. A source-only
# checkout may opt into its own src fallback when the package is absent.
try:
    import codex_app_deepseek.bridge
except ModuleNotFoundError as exc:
    if exc.name!="codex_app_deepseek" or sys.flags.isolated:raise
    sys.path.insert(0,str(REPO/"src"))
    import codex_app_deepseek.bridge
spec=importlib.util.spec_from_file_location("aa3_fault_fixture",Path(__file__).with_name("fault_server.py"))
base=importlib.util.module_from_spec(spec);sys.modules[spec.name]=base;spec.loader.exec_module(base)
base.ROOT=REPO
from aiohttp import web
from codex_deepseek.protocol import Registry
MARKER=base.MARKER
DISCOVERY="CDAG_CODEMODE_DISCOVERY_V1"
CUSTOM_PARAMETERS={"type":"object","properties":{"input":{"type":"string"}},"required":["input"],"additionalProperties":False}
AUTHORITY_KINDS={"codemode_preview_revision","codemode_invalid_final"}
FUNCTION_KINDS={"native_function_preview_revision"}
POST_COMPLETED_KINDS={"codemode_completed_revision"}
EFFECT_KINDS={"codemode_effect"}|AUTHORITY_KINDS|POST_COMPLETED_KINDS
EXTRA_KINDS={"codemode_discovery"}|EFFECT_KINDS|FUNCTION_KINDS
base.KINDS=base.KINDS|EXTRA_KINDS
def canonical(v):return json.dumps(v,sort_keys=True,separators=(",",":"),ensure_ascii=True)
def sha(v):return base.digest(canonical(v))
def exec_tool(payload):
    matches=[t for t in payload.get("tools",[]) if isinstance(t,dict)
        and t.get("type")=="function" and isinstance(t.get("name"),str)
        and t.get("parameters")==CUSTOM_PARAMETERS and isinstance(t.get("description"),str)
        and any(t.get("description","").startswith("Logical tool: "+n+".\n") for n in ("exec","functions.exec"))
        and "ALL_TOOLS" in t.get("description","")
        and "Native input format (validated before tool execution):" in t.get("description","")]
    return matches[0] if len(matches)==1 else None
def sleep_tool(payload):
    # Match the ordinary function from the current adapted actual declaration.
    matches=[t for t in payload.get("tools",[]) if isinstance(t,dict)
        and t.get("type")=="function" and isinstance(t.get("description"),str)
        and t["description"].startswith("Logical tool: clock.sleep.\n")
        and isinstance(t.get("name"),str) and t.get("strict") is False
        and isinstance(t.get("parameters"),dict)
        and t["parameters"].get("type")=="object"
        and t["parameters"].get("additionalProperties") is False
        and t["parameters"].get("required")==["duration_ms"]
        and set(t["parameters"].get("properties",{}))=={"duration_ms"}
        and t["parameters"]["properties"]["duration_ms"].get("type")=="number"]
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

def discovery_program():
    # Read only: no nested tool invocation and no assumed MCP name.
    return ('// Synthetic CDAG QA capability discovery; not model output.\n'
        'const found = ALL_TOOLS.filter(t => t.description.includes('+json.dumps(MARKER)+'));\n'
        'text({qa: '+json.dumps(DISCOVERY)+', entries: found.map(t => '
        '({name:t.name, description:t.description, callable:typeof tools[t.name] === "function"}))});')
def effect_program(name,case):
    # Fixed template; no eval, code parser, shell, network or arbitrary program argument.
    return ('// Synthetic CDAG QA counter program; not model output.\n'
        'const name = '+json.dumps(name)+';\n'
        'const matches = ALL_TOOLS.filter(t => t.name === name && t.description.includes('+json.dumps(MARKER)+'));\n'
        'if (matches.length !== 1 || typeof tools[name] !== "function") throw new Error("Counter capability unavailable");\n'
        'const result = await tools[name]({case_id:'+json.dumps(case)+'});\n'
        'text(result);')
def preview_effect_program(name,case):
    # Two visible effects distinguish preview-only (2), final-only (1), or both (3).
    # It is emitted as source only; the fixture never evaluates or invokes it.
    return effect_program(name,case)+'\nconst previewRepeat = await tools[name]({case_id:'+json.dumps(case)+'});\ntext(previewRepeat);'
def json_objects(value,depth=0):
    # JSON content inspection only. Never parse or execute JavaScript.
    if depth>12:return
    if isinstance(value,dict):
        yield value
        for child in value.values():yield from json_objects(child,depth+1)
    elif isinstance(value,list):
        for child in value:yield from json_objects(child,depth+1)
    elif isinstance(value,str) and len(value)<=base.MAX_BODY:
        decoder=json.JSONDecoder();at=0
        while at<len(value):
            pos=value.find("{",at)
            if pos<0:break
            try:obj,end=decoder.raw_decode(value,pos)
            except ValueError:at=pos+1;continue
            yield from json_objects(obj,depth+1);at=end
def paired_calls(items):
    if not isinstance(items,list):return []
    calls={};outputs={}
    for i,item in enumerate(items):
        if not isinstance(item,dict):continue
        cid=item.get("call_id")
        if not isinstance(cid,str) or not cid:continue
        if item.get("type") in {"function_call","custom_tool_call"}:calls.setdefault(cid,[]).append((i,item))
        if item.get("type") in {"function_call_output","custom_tool_call_output"}:outputs.setdefault(cid,[]).append((i,item))
    return [(cid,c[0][1],outputs[cid][0][1]) for cid,c in calls.items()
        if len(c)==1 and len(outputs.get(cid,[]))==1 and c[0][0]<outputs[cid][0][0]]
def call_source(item):
    if item.get("type")=="custom_tool_call":return item.get("input")
    try:
        v=json.loads(item.get("arguments",""))
        return v["input"] if isinstance(v,dict) and set(v)=={"input"} and isinstance(v["input"],str) else None
    except (ValueError,KeyError,TypeError):return None

class NonceLedger(base.CounterLedger):
    def __init__(self,home):
        super().__init__(home)
        if "receipt_nonce" not in {r[1] for r in self.db.execute("PRAGMA table_info(effects)")}:
            self.db.execute("ALTER TABLE effects ADD COLUMN receipt_nonce TEXT");self.db.commit()
    def record(self,case_id):
        # Retains AA3's non-idempotent durable append and hard 64-effect budget.
        limits=json.loads((self.path.parent/"limits.json").read_text())
        if time.time()>=limits["created_at"]+900:raise ValueError("Fixture absolute lifetime exhausted")
        nonce=secrets.token_urlsafe(32)
        with self.db:
            self.db.execute("BEGIN IMMEDIATE")
            row=self.db.execute("SELECT thread_sha256 FROM cases WHERE id=?",(case_id,)).fetchone()
            if not row or row[0] is None:raise ValueError("Unknown or unbound fixture case")
            if self.db.execute("SELECT COUNT(*) FROM effects").fetchone()[0]>=64:raise ValueError("Fixture effect budget exhausted")
            self.db.execute("INSERT INTO effects(case_id,at,receipt_nonce) VALUES (?,?,?)",(case_id,time.time(),nonce))
            count=self.count(case_id)
        return {"fixture":True,"case_id":case_id,"effect_count":count,"receipt_nonce":nonce}
    def receipts(self,case_id,thread_sha):
        row=self.db.execute("SELECT thread_sha256 FROM cases WHERE id=?",(case_id,)).fetchone()
        if not row or row[0]!=thread_sha:return {}
        rows=self.db.execute("SELECT receipt_nonce FROM effects WHERE case_id=? ORDER BY id",(case_id,)).fetchall()
        return {r[0]:i for i,r in enumerate(rows,1) if r[0]}
base.CounterLedger=NonceLedger

def initialize(home):
    result=base.initialize(home)
    base._atomic_write(Path(home)/"limits.json",canonical({"created_at":time.time(),"lifetime_seconds":900,
        "processed_cap":64,"live_cap":8}).encode())
    return result

class CodeModeHarness(base.FaultHarness):
    def __init__(self,home,*,allow_loopback_test_binding=False,finite_recovery=False,live_reader=None,proxy_url=None):
        # External access stays disabled unless the operator explicitly supplies a reader
        # and then uses the original authenticated enable_live control.
        if type(finite_recovery) is not bool:raise ValueError("finite_recovery must be boolean")
        super().__init__(Path(home),live_reader=live_reader,max_live_requests=8,lifetime_seconds=900,proxy_url=proxy_url)
        self.finite_recovery_enabled=finite_recovery
        self._code_mode_recovery_plan=None
        self.allow_loopback_test_binding=allow_loopback_test_binding
        self.binding=None
        self.limits=json.loads((self.home/"limits.json").read_text())
        if {k:self.limits.get(k) for k in ("lifetime_seconds","processed_cap","live_cap")}!={"lifetime_seconds":900,"processed_cap":64,"live_cap":8}:
            raise ValueError("Pinned QA limits changed")
        self.deadline=self.limits["created_at"]+900
        self.audit=sqlite3.connect(self.home/"http-audit.sqlite")
        self.audit.executescript("CREATE TABLE IF NOT EXISTS attempts(id INTEGER PRIMARY KEY,at REAL,status INTEGER,processed INTEGER,thread_sha TEXT,completion TEXT NOT NULL DEFAULT 'pending',headers_prepared INTEGER NOT NULL DEFAULT 0);")
        columns={row[1] for row in self.audit.execute("PRAGMA table_info(attempts)")}
        if "completion" not in columns:
            self.audit.execute("ALTER TABLE attempts ADD COLUMN completion TEXT NOT NULL DEFAULT 'legacy_unclassified'")
        if "headers_prepared" not in columns:
            self.audit.execute("ALTER TABLE attempts ADD COLUMN headers_prepared INTEGER NOT NULL DEFAULT 0")
        self.audit.commit();base._private(self.home/"http-audit.sqlite")
        self._database_resources.callback(self.audit.close)
        binding=self.home/"binding.json"
        if binding.exists():self.binding=json.loads(binding.read_text())
    def bind_evidence(self,evidence):
        if self.inflight:raise ValueError("Request in flight")
        level=evidence.get("evidence_level")
        if level!="actual_native" and not (self.allow_loopback_test_binding and level=="source_contract_loopback_mock"):
            raise ValueError("Actual native discovery records required")
        thread=evidence["thread_id"]
        if not base.valid_identity(thread):raise ValueError("Exact native thread required")
        catalog_source=evidence.get("catalog_source","native_mcp_server_status")
        if catalog_source=="native_mcp_server_status":
            req=evidence["mcp_status_request"];resp=evidence["mcp_status_response"]
            if req.get("method")!="mcpServerStatus/list" or req.get("params",{}).get("threadId")!=thread:
                raise ValueError("Canonical same-thread MCP status request required")
            server=req.get("params",{}).get("serverName")
            rows=[s for s in resp.get("data",[]) if s.get("name")==server and s.get("runtimeStatus")=="connected" and s.get("toolsError") is None]
            if len(rows)!=1:raise ValueError("Connected server catalog missing")
            tools=rows[0].get("tools",{})
        elif catalog_source=="independent_standard_MCP_tools_list":
            # A second app-server cannot read another process's active ThreadManager.
            # Do not manufacture an App RPC envelope. Actual App ALL_TOOLS history
            # below supplies the callable; this separate SDK catalog checks schema.
            server=evidence["server_name"]
            if not isinstance(server,str) or not server or len(server)>256:
                raise ValueError("Explicit registered server name required")
            resp=evidence["server_tools_list_response"]
            if not isinstance(resp,dict) or not isinstance(resp.get("tools"),list):
                raise ValueError("Standard MCP tools/list result required")
            tools={str(i):t for i,t in enumerate(resp["tools"])}
        else:raise ValueError("Unknown catalog evidence source")
        entries=[t for t in tools.values() if isinstance(t,dict) and t.get("name")=="record_effect" and MARKER in t.get("description","")]
        if len(entries)!=1:raise ValueError("One actual counter declaration required")
        tool=entries[0];schema=tool.get("inputSchema",{})
        if schema.get("type")!="object" or schema.get("required")!=["case_id"] or set(schema.get("properties",{}))!={"case_id"} or schema["properties"]["case_id"].get("type")!="string":
            raise ValueError("Counter schema differs")
        ann=tool.get("annotations",{})
        if ann.get("idempotentHint") is not False or ann.get("openWorldHint") is not False or ann.get("destructiveHint") is not False:
            raise ValueError("Counter annotations differ")
        declared_tools=Registry().tools(evidence["native_tools"])
        declared=exec_tool({"tools":declared_tools})
        declared_sleep=sleep_tool({"tools":declared_tools})
        if declared is None:raise ValueError("Actual native custom exec declaration missing")
        candidates=[]
        for cid,call,out in paired_calls(evidence["introspection_history"]):
            if call.get("status")!="completed" or call_source(call)!=discovery_program():continue
            if call.get("type")!="custom_tool_call" or call.get("name")!="exec" or call.get("namespace") not in (None,"functions"):continue
            for obj in json_objects(out.get("output")):
                if obj.get("qa")==DISCOVERY and isinstance(obj.get("entries"),list):candidates.append(obj["entries"])
        if evidence.get("introspection_thread_id")!=thread or len(candidates)!=1 or len(candidates[0])!=1:
            raise ValueError("Completed same-thread native introspection required")
        entry=candidates[0][0]
        name=entry.get("name")
        if not isinstance(name,str) or not base.re.fullmatch(r"[A-Za-z_$][A-Za-z0-9_$]{0,255}",name) or entry.get("callable") is not True or MARKER not in entry.get("description",""):
            raise ValueError("Counter CodeMode callable not discovered")
        # Name is copied from native ALL_TOOLS, never invented from a server label.
        if "record_effect" not in entry["description"]:raise ValueError("Introspection declaration missing record_effect")
        sources=evidence.get("raw_evidence",[])
        if level=="actual_native":
            if len(sources)<2:raise ValueError("Durable native evidence files required")
            for source in sources:
                path=base.checked_home(source["path"])
                if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest()!=source["sha256"]:
                    raise ValueError("Native evidence file missing or changed")
        self.binding={"schema":1,"evidence_level":level,"thread_sha256":base.digest(thread),
            "exec_wire_name":declared["name"],"exec_tool_sha256":sha(declared),
            "nested_mcp_tool_name":name,"nested_mcp_server_sha256":base.digest(server),
            "nested_mcp_schema_sha256":sha(tool),"evidence_sha256":sha(evidence),
            "raw_evidence":sources,"actual_App_acceptance":False,
            "catalog_source":catalog_source,"catalog_native_RPC_envelope":catalog_source=="native_mcp_server_status"}
        if declared_sleep is not None:
            self.binding["clock_sleep"]={"wire_name":declared_sleep["name"],"tool_sha256":sha(declared_sleep)}
        base._atomic_write(self.home/"binding.json",canonical(self.binding).encode())
        return {k:v for k,v in self.binding.items() if k!="raw_evidence"}
    def usable_binding(self,thread=None):
        b=self.binding
        return bool(b and (b["evidence_level"]=="actual_native" or self.allow_loopback_test_binding)
            and (thread is None or b["thread_sha256"]==base.digest(thread)))
    def bound_sleep(self,payload):
        observed=sleep_tool(payload);bound=self.binding.get("clock_sleep") if self.usable_binding() else None
        return observed if observed and bound and observed["name"]==bound["wire_name"] and sha(observed)==bound["tool_sha256"] else None
    def arm(self,kind,thread_id,**kwargs):
        if kind in {"tool_incomplete_cut","tool_done_cut","after_effect_503"}|EFFECT_KINDS|FUNCTION_KINDS and not self.usable_binding(thread_id):
            raise ValueError("CodeMode native discovery binding pending or wrong thread; inconclusive")
        if kind in FUNCTION_KINDS and not self.binding.get("clock_sleep"):
            raise ValueError("Actual bound ordinary clock.sleep declaration missing; inconclusive")
        previous=self.plan
        if (kind=="synthetic_success" and previous is not None and previous is self._code_mode_recovery_plan
                and thread_id==previous.thread_id and kwargs.get("case_id")==previous.case_id):
            raise ValueError("Code Mode synthetic recovery was already allocated for this case")
        code_mode_kinds={"codemode_discovery","tool_incomplete_cut","tool_done_cut","after_effect_503"}|EFFECT_KINDS|FUNCTION_KINDS
        recovery=(kind=="synthetic_success" and previous is not None and previous.kind in code_mode_kinds
                  and previous.remaining==0 and previous.fired==1 and thread_id==previous.thread_id
                  and kwargs.get("case_id")==previous.case_id)
        if recovery and kwargs.get("repeat",1)!=1:raise ValueError("Code Mode recovery permits exactly one synthetic response")
        result=super().arm(kind,thread_id,**kwargs)
        self._code_mode_recovery_plan=(self.plan if kind=="synthetic_success" and previous is not None
            and previous.kind in code_mode_kinds and previous.remaining==0 and previous.fired==1
            and previous.thread_id==self.plan.thread_id and previous.case_id==self.plan.case_id else None)
        return result
    def summary(self):
        result=super().summary()
        physical=self.audit.execute("SELECT COUNT(*) FROM attempts").fetchone()[0]
        result.update({"physical_http_attempts":physical,"physical_http_status_counts":dict(self.audit.execute(
            "SELECT CASE WHEN headers_prepared=1 THEN CAST(status AS TEXT) WHEN completion='legacy_unclassified' THEN 'legacy_unverified' ELSE 'unobserved' END AS observed,COUNT(*) FROM attempts GROUP BY observed").fetchall()),
            "physical_http_completion_counts":dict(self.audit.execute(
                "SELECT completion,COUNT(*) FROM attempts GROUP BY completion").fetchall()),
            "physical_status_scope":"aiohttp response headers prepared; peer receipt is not inferred",
            "processed_column_scope":"global request-counter delta during attempt, not per-request attribution",
            "native_discovery_binding":"bound" if self.usable_binding() else "pending",
            "binding_evidence_level":self.binding["evidence_level"] if self.binding else None,
            "absolute_deadline":self.deadline,"finite_recovery":self.finite_recovery_enabled,
            "native_invocations_by_fixture":0,"model_requests":self.live_calls,
            "model_request_count_semantics":"live attempts; confirm served generations using receipts"})
        return result
    def counter_names(self,payload):
        t=exec_tool(payload)
        return {t["name"]} if self.usable_binding() and t and sha(t)==self.binding["exec_tool_sha256"] else set()
    def case_result_call_ids(self,payload,case):
        if not self.plan or not self.usable_binding(self.plan.thread_id):return []
        names=self.counter_names(payload)
        durable=self.ledger.receipts(case,base.digest(self.plan.thread_id));valid=[]
        wanted=effect_program(self.binding["nested_mcp_tool_name"],case)
        for cid,call,out in paired_calls(payload.get("input")):
            if call.get("name") not in names or call.get("status")!="completed" or call_source(call)!=wanted:continue
            matches={(o.get("receipt_nonce"),o.get("effect_count")) for o in json_objects(out.get("output"))
                if o.get("fixture") is True and o.get("case_id")==case
                and isinstance(o.get("receipt_nonce"),str) and type(o.get("effect_count")) is int
                and durable.get(o.get("receipt_nonce"))==o.get("effect_count")}
            if len(matches)!=1:continue
            nonce,count=next(iter(matches));valid.append(cid)
            self.event("code_mode_receipt_match",parent_exec_call_id_sha256=base.digest(cid),
                nested_mcp_tool_name_sha256=base.digest(self.binding["nested_mcp_tool_name"]),
                nested_mcp_call_id_sha256=None,nested_mcp_identity_source="native_binding_plus_durable_ledger",
                receipt_nonce_sha256=base.digest(nonce),effect_count=count,case_sha256=base.digest(case))
        return valid
    async def finite_recovery(self,request):
        p=self.plan
        if (not self.finite_recovery_enabled or p is None
                or request.headers.get("Authorization")!="Bearer "+self.fixture["upstream_token"]
                or request.headers.get("X-CDAG-QA-Thread-ID")!=p.thread_id
                or p.remaining!=0 or p.fired!=1
                or p.kind not in {"codemode_discovery","tool_incomplete_cut","tool_done_cut",
                                  "codemode_effect","after_effect_503","http_413"}|AUTHORITY_KINDS):
            return None
        # No recovery may rearm past its original deadline or processed cap.
        if time.time()>=self.deadline or self.requests>=64:
            return None
        if p.kind!="http_413":
            try:payload=await request.json()
            except (ValueError,TypeError):return None
            if not _code_mode_recovery_main(payload):return None
        # Respect the existing global in-flight guard. Native auxiliary requests
        # may still be completing when the root retries a cut response.
        until=min(self.deadline,time.time()+5)
        while self.inflight and time.time()<until:
            await asyncio.sleep(.02)
        if self.inflight:
            self.event("finite_recovery_wait_timed_out",from_fault=p.kind,
                       thread_sha256=base.digest(p.thread_id))
            return web.json_response({"error":"Synthetic recovery is waiting for in-flight fixture requests"},
                                     status=503,headers={"Retry-After":"1"})
        if (self.plan is p and p.remaining==0 and p.fired==1
                and time.time()<self.deadline and self.requests<64):
            prior=p.kind
            slots=2 if prior=="http_413" else 1
            self.arm("synthetic_success",p.thread_id,case_id=p.case_id,repeat=slots)
            if prior=="http_413":self._capacity_recovery_plan=self.plan
            self.event("finite_recovery_transition",from_fault=prior,
                       thread_sha256=base.digest(p.thread_id),case_sha256=base.digest(p.case_id),
                       allocated_synthetic_responses=slots,fixture_recovery_not_model=True)
        return None
    async def upstream(self,request):
        try:
            recovery=await self.finite_recovery(request)
            if recovery is not None:
                status=recovery.status;return recovery
            authenticated=request.headers.get("Authorization")=="Bearer "+self.fixture["upstream_token"]
            if authenticated and time.time()>=self.deadline:
                status=503
                return web.json_response({"error":"Fixture absolute 900-second lifetime exhausted"},status=status)
            p=self.plan
            # Keep the allocated Code Mode slot protected after the rearm too,
            # including an explicit manual recovery. Capacity retains its own
            # separately qualified two-slot contract.
            if (authenticated and self.requests<64 and p is not None and p is self._code_mode_recovery_plan
                    and p.remaining>0 and request.headers.get("X-CDAG-QA-Thread-ID")==p.thread_id):
                try:payload=await request.json()
                except (ValueError,TypeError):payload=None
                if not _code_mode_recovery_main(payload):
                    self.requests+=1;self.threads.add(base.digest(p.thread_id))
                    self.event("code_mode_recovery_non_main",thread_sha256=base.digest(p.thread_id),
                               case_sha256=base.digest(p.case_id),recovery_slot_preserved=True)
                    status=503
                    return web.json_response({"error":"Code Mode synthetic recovery requires its declared unstructured MAIN continuation"},status=status)
            if (authenticated and self.requests<64 and p
                    and p.kind in EXTRA_KINDS|{"tool_incomplete_cut","tool_done_cut","after_effect_503"}
                    and request.headers.get("X-CDAG-QA-Thread-ID")==p.thread_id):
                try:data=await request.json()
                except (ValueError,TypeError):data=None
                selected=self.bound_sleep(data) if isinstance(data,dict) and p.kind in FUNCTION_KINDS else None
                if isinstance(data,dict) and ((p.kind in FUNCTION_KINDS and selected is None)
                        or not _code_mode_recovery_main(data,selected_tool=selected)
                        or (p.kind in EFFECT_KINDS and not self.counter_names(data))):
                    self.requests+=1
                    self.threads.add(base.digest(p.thread_id))
                    self.event("code_mode_declaration_inconclusive",fault=p.kind,thread_sha256=base.digest(p.thread_id))
                    status=409
                    return web.json_response({"error":"Actual declared exec, unstructured MAIN profile, or native discovery binding missing; inconclusive"},status=status)
            response=await super().upstream(request);status=response.status;return response
        finally:
            # Covers AA3's 64-processed refusal, including attempts after budget exhaustion.
            # Persist early-return counters; physical HTTP is counted by outer gateway middleware.
            base._atomic_write(self.home/"budget-totals.json",canonical({"processed":self.requests,
                "live":self.live_calls,"physical_http":self.audit.execute("SELECT COUNT(*) FROM attempts").fetchone()[0],
                "absolute_deadline":self.deadline}).encode())
    async def synthetic_function(self,request,payload,p):
        tool=self.bound_sleep(payload)
        if tool is None:raise ValueError("Current payload does not declare the bound ordinary clock.sleep")
        preview=json.dumps({"duration_ms":1000});final=json.dumps({"duration_ms":1})
        rid="synthetic-native-function-"+uuid.uuid4().hex
        item={"id":"synthetic-function-item-"+uuid.uuid4().hex,"type":"function_call",
            "call_id":"synthetic-function-call-"+uuid.uuid4().hex,"name":tool["name"],"arguments":"","status":"in_progress"}
        done={**item,"arguments":final,"status":"completed"}
        response=web.StreamResponse(headers={"Content-Type":"text/event-stream","X-Fixture-Mode":"synthetic-native-function"})
        await response.prepare(request)
        events=[{"type":"response.created","response":{"id":rid,"status":"in_progress","output":[]}},
            *base.synthetic_reasoning_prefix(rid),
            {"type":"response.output_item.added","output_index":1,"item":item},
            {"type":"response.function_call_arguments.delta","item_id":item["id"],"delta":preview},
            {"type":"response.function_call_arguments.done","item_id":item["id"],"arguments":preview},
            {"type":"response.output_item.done","output_index":1,"item":done},
            {"type":"response.completed","response":{"id":rid,"status":"completed","output":[base.synthetic_reasoning_item(rid),done]}}]
        for event in events:await response.write(base.encode_sse(event))
        self.event("synthetic_native_function_frames",fault=p.kind,parent_function_call_id_sha256=base.digest(item["call_id"]),
            native_namespace="clock",native_name="sleep",bound_tool_sha256=sha(tool),
            preview_arguments_sha256=base.digest(preview),final_arguments_sha256=base.digest(final),
            preview_duration_ms=1000,final_duration_ms=1,complete_item_exposed=True,terminal_exposed=True,actual_native_execution=False)
        await response.write_eof();return response
    async def synthetic(self,request,payload,p):
        if p.kind in FUNCTION_KINDS:return await self.synthetic_function(request,payload,p)
        if getattr(self,"_capacity_recovery_plan",None) is p:
            self.event("synthetic_capacity_recovery_slot",slot=2-p.remaining,allocated_slots=2,
                thread_sha256=base.digest(p.thread_id),case_sha256=base.digest(p.case_id),
                native_request_role_not_inferred=True)
        if p.kind not in EXTRA_KINDS|{"tool_incomplete_cut","tool_done_cut"}:
            return await super().synthetic(request,payload,p)
        tool=exec_tool(payload)
        source=discovery_program() if p.kind=="codemode_discovery" else effect_program(self.binding["nested_mcp_tool_name"],p.case_id)
        preview=preview_effect_program(self.binding["nested_mcp_tool_name"],p.case_id) if p.kind in AUTHORITY_KINDS else source
        arguments=json.dumps({"input":preview})
        final_arguments=json.dumps({"input":None}) if p.kind=="codemode_invalid_final" else json.dumps({"input":source})
        rid="synthetic-codemode-"+uuid.uuid4().hex
        item={"id":"synthetic-exec-item-"+uuid.uuid4().hex,"type":"function_call",
            "call_id":"synthetic-exec-call-"+uuid.uuid4().hex,"name":tool["name"],"arguments":"","status":"in_progress"}
        response=web.StreamResponse(headers={"Content-Type":"text/event-stream","X-Fixture-Mode":"synthetic-codemode"})
        await response.prepare(request)
        events=[{"type":"response.created","response":{"id":rid,"status":"in_progress","output":[]}},
            *base.synthetic_reasoning_prefix(rid),
            {"type":"response.output_item.added","output_index":1,"item":item},
            {"type":"response.function_call_arguments.delta","item_id":item["id"],"delta":arguments}]
        if p.kind in AUTHORITY_KINDS|POST_COMPLETED_KINDS:
            events.append({"type":"response.function_call_arguments.done","item_id":item["id"],"arguments":arguments})
        if p.kind!="tool_incomplete_cut":
            done={**item,"arguments":final_arguments,"status":"completed"}
            events.append({"type":"response.output_item.done","output_index":1,"item":done})
        terminal_done=done if p.kind!="tool_incomplete_cut" else None
        if p.kind in POST_COMPLETED_KINDS:
            terminal_done={**done,"arguments":json.dumps({"input":preview_effect_program(self.binding["nested_mcp_tool_name"],p.case_id)})}
        if p.kind in EXTRA_KINDS:
            events.append({"type":"response.completed","response":{"id":rid,"status":"completed","output":[base.synthetic_reasoning_item(rid),terminal_done]}})
        for event in events:
            if p.kind in POST_COMPLETED_KINDS and event["type"]=="response.completed":await asyncio.sleep(.2)
            await response.write(base.encode_sse(event))
        self.event("synthetic_code_mode_frames",fault=p.kind,parent_exec_call_id_sha256=base.digest(item["call_id"]),
            nested_mcp_tool_name_sha256=base.digest(self.binding["nested_mcp_tool_name"]) if p.kind!="codemode_discovery" else None,
            program_sha256=None if p.kind=="codemode_invalid_final" else base.digest(source),
            preview_program_sha256=base.digest(preview),final_arguments_valid=p.kind!="codemode_invalid_final",
            terminal_program_sha256=base.digest(preview_effect_program(self.binding["nested_mcp_tool_name"],p.case_id)) if p.kind in POST_COMPLETED_KINDS else None,
            terminal_contradicts_completed_item=p.kind in POST_COMPLETED_KINDS,
            complete_item_exposed=p.kind!="tool_incomplete_cut",
            terminal_exposed=p.kind in EXTRA_KINDS,actual_native_execution=False)
        if p.kind=="tool_done_cut":await asyncio.sleep(.2)
        await response.write_eof();return response
    async def start(self,*args,**kwargs):
        # AA3 already restores event counters; supplement early-return request totals.
        p=self.home/"budget-totals.json"
        if p.exists():
            previous=json.loads(p.read_text());self.requests=max(self.requests,previous["processed"]);self.live_calls=max(self.live_calls,previous["live"])
        original_factory=base.create_app
        def audited_factory(*factory_args,**factory_kwargs):
            app=original_factory(*factory_args,**factory_kwargs)
            @web.middleware
            async def physical_http(request,handler):
                if request.method!="POST" or request.path!="/v1/responses":
                    return await handler(request)
                before=self.requests;completion="pending"
                cursor=self.audit.execute("INSERT INTO attempts(at,thread_sha,completion) VALUES (?,?,?)",
                    (time.time(),base.digest(request.headers.get("thread-id","")),"pending"))
                seq=cursor.lastrowid;self.audit.commit()
                request["_cadsg_physical_http_attempt"]=seq
                try:
                    response=await handler(request);completion="handler_returned";return response
                except asyncio.CancelledError:
                    completion="cancelled";raise
                except web.HTTPException:
                    completion="http_exception";raise
                except Exception:
                    completion="handler_error";raise
                finally:
                    # This delta is a global observation under concurrency.
                    # It must never be summed as per-request processed work.
                    self.audit.execute("UPDATE attempts SET completion=?,processed=? WHERE id=?",
                        (completion,max(0,self.requests-before),seq));self.audit.commit()
            async def headers_prepared(request,response):
                seq=request.get("_cadsg_physical_http_attempt")
                if seq is not None:
                    self.audit.execute("UPDATE attempts SET status=?,headers_prepared=1 WHERE id=?",
                                       (response.status,seq));self.audit.commit()
            app.on_response_prepare.append(headers_prepared)
            app.middlewares.insert(0,physical_http)
            return app
        base.create_app=audited_factory
        try:result=await super().start(*args,**kwargs)
        finally:base.create_app=original_factory
        result.update({"fixture_extension":"CodeMode-QA-v3","live_enabled":False,"absolute_deadline":self.deadline})
        base._atomic_write(self.home/"ready.json",canonical(result).encode());return result

async def serve(home,reuse_port=False,*,finite_recovery=False,enable_live_reader=False,
                gateway_policy_home=None,trace_images=False):
    policy=None;reader=None
    if gateway_policy_home is not None:
        from codex_app_deepseek.gateway_policy import load_service_config
        policy=load_service_config(Path(gateway_policy_home))
        if base.MODEL not in policy.accepted_models:
            raise ValueError("The QA fixture requires the fixed DeepSeek primary model")
        # The selected production loader validates the explicit policy. Do not
        # maintain a second App-alias whitelist here, or reject user aliases.
    if enable_live_reader:
        if policy is None:raise ValueError("Live QA requires the explicit production gateway policy home")
        from codex_app_deepseek.credentials import read,UPSTREAM_TARGET
        reader=lambda:read(UPSTREAM_TARGET)
    harness=CodeModeHarness(Path(home),finite_recovery=finite_recovery,live_reader=reader,
                            proxy_url=policy.upstream_proxy if policy is not None else None)
    observer=None
    try:
        port=0
        if reuse_port:
            ready=json.loads((Path(home)/"ready.json").read_text())
            url=base.urlsplit(ready["gateway_url"])
            if ready.get("nonce_sha256")!=base.digest(harness.fixture["nonce"]) or url.hostname!="127.0.0.1" or not url.port:
                raise ValueError("Cannot reuse foreign port")
            port=url.port
        original_start=harness.start
        async def chosen_start():
            ready=await original_start(bridge_port=port)
            if policy is not None:
                import aiohttp,ssl,truststore
                await harness.client.close()
                harness.client=aiohttp.ClientSession(
                    connector=aiohttp.TCPConnector(ssl=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)),
                    timeout=aiohttp.ClientTimeout(total=90),trust_env=False,
                    cookie_jar=aiohttp.DummyCookieJar())
            return ready
        harness.start=chosen_start
        factory=None
        if policy is not None:
            from codex_app_deepseek.cli import image_transport_factory
            factory=image_transport_factory(Path(home)/"private-image-policy",policy)
        selected_factory=base.create_app
        if policy is not None:
            from dataclasses import replace
            def policy_factory(*args,**kwargs):
                settings=replace(args[0],allowed_providers=policy.allowed_providers,
                                 accepted_models=policy.accepted_models,config_sha256=policy.config_sha256)
                return selected_factory(settings,*args[1:],**kwargs)
            base.create_app=policy_factory
        try:
            if trace_images:
                spec=importlib.util.spec_from_file_location("cdag_qa_image_trace",Path(__file__).with_name("image_trace.py"))
                module=importlib.util.module_from_spec(spec);sys.modules[spec.name]=module;spec.loader.exec_module(module)
                observer=module.ImageTrace(base,Path(home)/("image-trace-"+uuid.uuid4().hex+".jsonl"))
                ready=await observer.start(harness,image_transport_factory=factory)
                ready["qa_image_trace"]={"path":str(observer.path),**observer.snapshot()}
            elif factory is not None:
                original_factory=base.create_app
                def configured_factory(*args,**kwargs):
                    kwargs["image_transport_factory"]=factory
                    return original_factory(*args,**kwargs)
                base.create_app=configured_factory
                try:ready=await harness.start()
                finally:base.create_app=original_factory
            else:ready=await harness.start()
        finally:base.create_app=selected_factory
        ready.update(real_reader_explicit_optin=enable_live_reader,finite_recovery=finite_recovery,
                     production_bridge_module=str(Path(base.create_app.__code__.co_filename)))
        base._atomic_write(Path(home)/"ready.json",canonical(ready).encode())
        print(json.dumps(ready),flush=True)
        try:await asyncio.wait_for(harness.stop.wait(),max(0,harness.deadline-time.time()))
        except asyncio.TimeoutError:pass
    finally:
        if observer is not None:
            base._atomic_write(Path(home)/("trace-final-"+uuid.uuid4().hex+".json"),canonical(observer.snapshot()).encode())
        await harness.close()

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument("mode",choices=["init","serve","counter","auth-token","control","bind","discovery-program"])
    p.add_argument("--state-dir");p.add_argument("--command-json");p.add_argument("--evidence-file");p.add_argument("--reuse-port",action="store_true")
    p.add_argument("--finite-recovery",action="store_true",help="One explicit synthetic recovery after each consumed scripted fault")
    p.add_argument("--enable-live-reader",action="store_true",help="Opt in to the dedicated Windows credential reader; live remains disabled until control enable_live")
    p.add_argument("--gateway-policy-home",type=Path)
    p.add_argument("--trace-images",action="store_true",help="Bounded metadata only; never record request bodies or image URLs")
    a=p.parse_args()
    if a.mode=="discovery-program":print(discovery_program());return
    if not a.state_dir:p.error("--state-dir required")
    if a.mode=="init":print(json.dumps(initialize(a.state_dir)))
    elif a.mode=="serve":asyncio.run(serve(a.state_dir,a.reuse_port,finite_recovery=a.finite_recovery,
        enable_live_reader=a.enable_live_reader,gateway_policy_home=a.gateway_policy_home,trace_images=a.trace_images))
    elif a.mode=="counter":base.counter_server(a.state_dir).run(transport="stdio")
    elif a.mode=="auth-token":print(base.read_fixture(a.state_dir)["gateway_token"])
    elif a.mode=="control":asyncio.run(base.control_command(a.state_dir,json.loads(a.command_json)))
    elif a.mode=="bind":
        h=CodeModeHarness(Path(a.state_dir))
        try:print(json.dumps(h.bind_evidence(json.loads(Path(a.evidence_file).read_text()))))
        finally:asyncio.run(h.close())
if __name__=="__main__":main()
