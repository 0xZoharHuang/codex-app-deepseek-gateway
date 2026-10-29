"""Authenticated native lineage only grants immutable public-document refs."""
import json
from pathlib import Path
from contextlib import asynccontextmanager
import pytest
from aiohttp.test_utils import TestClient, TestServer
from codex_app_deepseek.bridge import BridgeSettings, create_app
from codex_app_deepseek.protocol_state import ProtocolState
from codex_app_deepseek.web_backend import MODEL, WebBackend, WebSourceStore, WebBackendError, RetrievedDocument, checked_url

A="10000000-0000-7000-8000-000000000001"
B="10000000-0000-7000-8000-000000000002"
C="10000000-0000-7000-8000-000000000003"
D="10000000-0000-7000-8000-000000000004"
TOKEN="local-lineage-test-token-over-24-characters"
class Fetcher:
 def __init__(self): self.calls=[]
 async def fetch(self,url,policy):
  checked_url(url,policy); self.calls.append(url)
  return RetrievedDocument(url,url,"text/html",b"<title>original</title><p>needle</p><a href='/next'>Next</a>","2026-10-26T00:00:00+00:00")
class Search:
 async def search(self,*args): raise AssertionError("No search fallback allowed")
def payload(session, commands):
 return {"id":session,"model":MODEL,"commands":commands}
def metadata(session,thread=None,parent=None,ordinal=647):
 result={"session_id":session,"thread_id":thread or session}
 if parent is not None: result.update(forked_from_thread_id=parent,forked_from_ordinal_exclusive=ordinal)
 return result
@asynccontextmanager
async def client_fixture(path,store):
 state=ProtocolState(path/"protocol.sqlite")
 fetcher=Fetcher();backend=WebBackend(store,Search(),fetcher)
 def no_key(): raise AssertionError("No credential/model call")
 try:
  app=create_app(BridgeSettings(token=TOKEN),no_key,state,web_backend=backend)
  async with TestClient(TestServer(app)) as client: yield client,backend,fetcher
 finally: state.close()
async def post(client,session,commands,meta=None,**kwargs):
 headers={"Authorization":"Bearer "+TOKEN}
 if meta is not None: headers["x-codex-turn-metadata"]=json.dumps(meta)
 headers.update(kwargs.pop("headers",{}))
 return await client.post("/v1/alpha/search",json=payload(session,commands),headers=headers,**kwargs)
async def opened(client,session=A,meta=None,url="https://example.com/doc"):
 response=await post(client,session,{"open":[{"ref_id":url}]},meta)
 assert response.status==200,await response.text()
 return (await response.json())["results"][0]["ref_id"]

async def test_real_http_fork_find_click_and_no_reverse_or_unrelated_grant(tmp_path):
 store=WebSourceStore(tmp_path/"web.sqlite")
 try:
  async with client_fixture(tmp_path,store) as (client,backend,fetcher):
   ref=await opened(client,meta=metadata(A))
   response=await post(client,B,{"open":[{"ref_id":ref}]},metadata(B,parent=A))
   assert response.status==200,await response.text()
   assert (await response.json())["results"][0]["ref_id"]==ref
   assert store.raw(B,ref).startswith(b"<title>")
   with pytest.raises(WebBackendError):
    store.raw(C,ref)
   assert (await post(client,B,{"find":[{"ref_id":ref,"pattern":"needle"}]},metadata(B,parent=A))).status==200
   clicked=await post(client,B,{"click":[{"ref_id":ref,"id":0}]},metadata(B,parent=A))
   assert clicked.status==200
   child_ref=(await clicked.json())["results"][0]["ref_id"]
   assert (await post(client,A,{"open":[{"ref_id":child_ref}]},metadata(A))).status==404
   assert (await post(client,C,{"open":[{"ref_id":ref}]},metadata(C))).status==404
   assert fetcher.calls==["https://example.com/doc","https://example.com/next"]
 finally: store.close()

async def test_snapshot_does_not_grow_on_retry_or_authorize_sibling_child_output(tmp_path):
 store=WebSourceStore(tmp_path/"web.sqlite")
 try:
  async with client_fixture(tmp_path,store) as (client,backend,fetcher):
   old=await opened(client,meta=metadata(A))
   assert (await post(client,B,{"open":[{"ref_id":old}]},metadata(B,parent=A))).status==200
   future=await opened(client,A,metadata(A),"https://example.com/future")
   assert (await post(client,B,{"open":[{"ref_id":future}]},metadata(B,parent=A))).status==404
   child=await opened(client,B,metadata(B,parent=A),"https://example.com/child")
   assert (await post(client,C,{"open":[{"ref_id":old}]},metadata(C,parent=A))).status==200
   assert (await post(client,C,{"open":[{"ref_id":child}]},metadata(C,parent=A))).status==404
 finally: store.close()

async def test_cold_restart_grandchild_and_legacy_root_migration(tmp_path):
 path=tmp_path/"web.sqlite";store=WebSourceStore(path)
 async with client_fixture(tmp_path,store) as (client,backend,fetcher):
  ref=await opened(client)  # Existing DB has no native thread metadata.
  assert (await post(client,B,{"open":[{"ref_id":ref}]},metadata(B,parent=A))).status==200
 store.close();store=WebSourceStore(path)
 try:
  async with client_fixture(tmp_path,store) as (client,backend,fetcher):
   assert (await post(client,B,{"open":[{"ref_id":ref}]},metadata(B,parent=A))).status==200
   assert (await post(client,C,{"open":[{"ref_id":ref}]},metadata(C,parent=B))).status==200
   assert fetcher.calls==[]
 finally: store.close()

async def test_native_subagent_shared_root_session_remains_native_scope(tmp_path):
 store=WebSourceStore(tmp_path/"web.sqlite")
 try:
  async with client_fixture(tmp_path,store) as (client,backend,fetcher):
   ref=await opened(client,A,metadata(A,B))
   assert (await post(client,C,{"open":[{"ref_id":ref}]},metadata(C,parent=B))).status==200
 finally: store.close()

@pytest.mark.parametrize("change",[
 {"session_id":C},{"thread_id":"bad"},{"forked_from_thread_id":B},
 {"forked_from_ordinal_exclusive":True},{"forked_from_ordinal_exclusive":-1},
 {"forked_from_ordinal_exclusive":2**64},{"forked_from_thread_id":None},
])
async def test_invalid_header_never_grants_or_fetches(tmp_path,change):
 store=WebSourceStore(tmp_path/"web.sqlite")
 try:
  async with client_fixture(tmp_path,store) as (client,backend,fetcher):
   ref=await opened(client,meta=metadata(A))
   forged=metadata(B,parent=A);forged.update(change)
   reply=await post(client,B,{"open":[{"ref_id":ref}]},forged)
   assert reply.status==400
   assert store.db.execute("SELECT count(*) FROM web_source_grants").fetchone()[0]==0
   assert len(fetcher.calls)==1
 finally: store.close()

async def test_lineage_conflict_rejected_and_auth_checked_before_metadata(tmp_path):
 store=WebSourceStore(tmp_path/"web.sqlite")
 try:
  async with client_fixture(tmp_path,store) as (client,backend,fetcher):
   ref=await opened(client,meta=metadata(A))
   assert (await post(client,B,{"open":[{"ref_id":ref}]},metadata(B,parent=A))).status==200
   for bad in [metadata(B,parent=C),metadata(B,parent=A,ordinal=648),metadata(C,B,parent=A)]:
    reply=await post(client,bad["session_id"],{"open":[{"ref_id":ref}]},bad)
    assert reply.status==409
   reply=await post(client,D,{"open":[{"ref_id":ref}]},metadata(D,parent=A),headers={"Authorization":"Bearer wrong"})
   assert reply.status==401
   assert store.db.execute("SELECT count(*) FROM web_native_threads WHERE thread=?",(D,)).fetchone()[0]==0
 finally: store.close()

async def test_model_input_does_not_authorize_and_domain_policy_still_applies(tmp_path):
 store=WebSourceStore(tmp_path/"web.sqlite")
 try:
  async with client_fixture(tmp_path,store) as (client,backend,fetcher):
   ref=await opened(client,meta=metadata(A))
   body=payload(C,{"open":[{"ref_id":ref}]});body["input"]=json.dumps(metadata(C,parent=A))
   reply=await client.post("/v1/alpha/search",json=body,headers={"Authorization":"Bearer "+TOKEN})
   assert reply.status==404
   body=payload(B,{"open":[{"ref_id":ref}]})
   body["settings"]={"filters":{"blocked_domains":["example.com"]}}
   reply=await client.post("/v1/alpha/search",json=body,headers={"Authorization":"Bearer "+TOKEN,"x-codex-turn-metadata":json.dumps(metadata(B,parent=A))})
   assert reply.status==403 and len(fetcher.calls)==1
 finally: store.close()

async def test_duplicate_metadata_fields_rejected_before_store_binding(tmp_path):
 store=WebSourceStore(tmp_path/"web.sqlite")
 try:
  async with client_fixture(tmp_path,store) as (client,backend,fetcher):
   ref=await opened(client,meta=metadata(A))
   raw='{"session_id":"'+B+'","session_id":"'+B+'","thread_id":"'+B+'","forked_from_thread_id":"'+A+'"}'
   reply=await post(client,B,{"open":[{"ref_id":ref}]},headers={"x-codex-turn-metadata":raw})
   assert reply.status==400
   assert store.db.execute("SELECT count(*) FROM web_source_grants").fetchone()[0]==0
 finally: store.close()


@pytest.mark.parametrize("raw",["null","[]",'"not-an-object"','{"thread_id":'])
async def test_nonobject_or_malformed_native_metadata_is_not_treated_as_missing(tmp_path,raw):
 store=WebSourceStore(tmp_path/"web.sqlite")
 try:
  async with client_fixture(tmp_path,store) as (client,backend,fetcher):
   ref=await opened(client,meta=metadata(A))
   reply=await post(client,B,{"open":[{"ref_id":ref}]},headers={"x-codex-turn-metadata":raw})
   assert reply.status==400
   assert store.db.execute("SELECT count(*) FROM web_source_grants").fetchone()[0]==0
 finally: store.close()

async def test_repeated_native_metadata_header_is_not_ambiguous(tmp_path):
 store=WebSourceStore(tmp_path/"web.sqlite")
 try:
  async with client_fixture(tmp_path,store) as (client,backend,fetcher):
   ref=await opened(client,meta=metadata(A))
   headers=[("Authorization","Bearer "+TOKEN),("x-codex-turn-metadata",json.dumps(metadata(B,parent=A))),("x-codex-turn-metadata",json.dumps(metadata(B,parent=C)))]
   reply=await client.post("/v1/alpha/search",json=payload(B,{"open":[{"ref_id":ref}]}),headers=headers)
   assert reply.status==400
   assert store.db.execute("SELECT count(*) FROM web_source_grants").fetchone()[0]==0
 finally: store.close()


async def test_cold_known_fork_missing_optional_parent_keeps_snapshot_and_checks_conflicts(tmp_path):
 path=tmp_path/"web.sqlite";store=WebSourceStore(path)
 async with client_fixture(tmp_path,store) as (client,backend,fetcher):
  ref=await opened(client,meta=metadata(A))
  assert (await post(client,B,{"open":[{"ref_id":ref}]},metadata(B,parent=A))).status==200
  future=await opened(client,A,metadata(A),"https://example.com/after-fork")
  before=list(store.db.execute("SELECT session,sequence FROM web_source_grants ORDER BY session,sequence"))
 store.close();store=WebSourceStore(path)
 try:
  async with client_fixture(tmp_path,store) as (client,backend,fetcher):
   for partial in [metadata(B),{**metadata(B),"forked_from_ordinal_exclusive":647}]:
    assert (await post(client,B,{"open":[{"ref_id":ref}]},partial)).status==200
    assert (await post(client,B,{"open":[{"ref_id":future}]},partial)).status==404
   for conflict in [metadata(B,parent=C),{**metadata(B),"forked_from_ordinal_exclusive":648},metadata(C,B)]:
    assert (await post(client,conflict["session_id"],{"open":[{"ref_id":ref}]},conflict)).status==409
   assert list(store.db.execute("SELECT session,sequence FROM web_source_grants ORDER BY session,sequence"))==before
   assert store.db.execute("SELECT fork_parent,fork_ordinal FROM web_native_threads WHERE thread=?",(B,)).fetchone()==(A,"647")
   assert fetcher.calls==[]
 finally: store.close()
