"""Native Markdown display destinations for exact retained renderer bytes."""
from pathlib import Path
import base64
import json
import os
import re
import subprocess
from hashlib import sha256
import pytest
from codex_app_deepseek import web_backend as wb
from codex_app_deepseek.web_backend import WebBackendError,WebSourceStore,WebBackend,RetrievedDocument,DomainPolicy
from test_web_backend import components,painted_pdf,commons_response,request,Search,Fetcher

def retained_png(store,session="native-session-A",*,backend="pdfium"):
 raw,metadata=wb._render_pdf_bytes(painted_pdf(),0)
 source={"content_type":"image/png","source_sha256":sha256(raw).hexdigest(),
         "url":"https://example.com/test.pdf","title":"synthetic","lines":[],"links":[],"excerpt":False,
         "image":{"width":metadata["width"],"height":metadata["height"]},
         "provenance":{"backend":backend,"parent_ref":"turn1view0"}}
 return store.put(session,source,raw,kind="image"),raw
def native_path(output):
 line=next(x for x in output.splitlines() if x.startswith("![PDF page "))
 return Path(re.fullmatch(r"!\[PDF page \d+\]\(<(.+)>\)",line).group(1))
async def test_actual_pdf_screenshot_returns_exact_native_file_markdown(components):
 backend,store,_,fetcher=components
 async def fetch(url,policy):return RetrievedDocument(url,url,"application/pdf",painted_pdf(),"now")
 fetcher.fetch=fetch
 result=await backend.handle(request({"screenshot":[{"ref_id":"https://example.com/test.pdf","pageno":0}]}))
 path=native_path(result["output"]);ref=result["results"][0]["ref_id"]
 assert path.is_absolute() and path.parent==store.path.absolute().parent/"generated-media"
 assert path.read_bytes()==store.raw("native-session-A",ref)
 assert path.name==sha256(path.read_bytes()).hexdigest()+".png"
 assert path.stat().st_nlink==1 and "base64" not in result["output"]
 evidence=json.loads(store.db.execute("SELECT evidence FROM web_receipts WHERE operation='screenshot'").fetchone()[0])
 assert evidence["generated_display"]=={"native_path":str(path),"sha256":sha256(path.read_bytes()).hexdigest()}
def test_existing_exact_pixels_survive_reopen_without_rewrite(tmp_path):
 db=tmp_path/"sources.sqlite";store=WebSourceStore(db)
 source,raw=retained_png(store);path=store.generated_png_path("native-session-A",source["ref_id"])
 before=(path.stat().st_ino,path.stat().st_mtime_ns);store.close()
 store=WebSourceStore(db)
 try:
  assert store.generated_png_path("native-session-A",source["ref_id"])==path
  assert path.read_bytes()==raw and (path.stat().st_ino,path.stat().st_mtime_ns)==before
 finally:store.close()
@pytest.mark.parametrize("mutation",["remote_png","body_hash","raw_hash"])
def test_only_own_pdfium_digest_verified_pixels_can_be_exposed(tmp_path,mutation):
 store=WebSourceStore(tmp_path/"s.sqlite")
 try:
  source,raw=retained_png(store,backend="public_http" if mutation=="remote_png" else "pdfium")
  if mutation=="body_hash":
   data=store.get("native-session-A",source["ref_id"]);data["source_sha256"]="0"*64
   store.db.execute("UPDATE web_sources SET body=? WHERE ref=?",(json.dumps(data),source["ref_id"]));store.db.commit()
  if mutation=="raw_hash":
   store.db.execute("UPDATE web_sources SET raw=? WHERE ref=?",(raw+b"bad",source["ref_id"]));store.db.commit()
  with pytest.raises(WebBackendError):
   store.generated_png_path("native-session-A",source["ref_id"])
  assert not (tmp_path/"generated-media").exists()
 finally:store.close()
def test_wrong_existing_same_hash_name_is_not_overwritten(tmp_path):
 store=WebSourceStore(tmp_path/"s.sqlite")
 try:
  source,_=retained_png(store);directory=tmp_path/"generated-media";directory.mkdir()
  target=directory/(source["source_sha256"]+".png");target.write_bytes(b"wrong")
  with pytest.raises(WebBackendError)as e:store.generated_png_path("native-session-A",source["ref_id"])
  assert e.value.code=="generated_media_integrity_error" and target.read_bytes()==b"wrong"
 finally:store.close()
def test_hardlinked_same_hash_file_is_rejected(tmp_path):
 store=WebSourceStore(tmp_path/"s.sqlite")
 try:
  source,raw=retained_png(store);directory=tmp_path/"generated-media";directory.mkdir()
  outside=tmp_path/"other.png";outside.write_bytes(raw)
  os.link(outside,directory/(source["source_sha256"]+".png"))
  with pytest.raises(WebBackendError)as e:store.generated_png_path("native-session-A",source["ref_id"])
  assert e.value.code=="generated_media_path_unsafe" and outside.read_bytes()==raw
 finally:store.close()
def test_directory_link_or_windows_junction_is_rejected_without_following(tmp_path):
 store=WebSourceStore(tmp_path/"s.sqlite")
 try:
  source,_=retained_png(store);elsewhere=tmp_path/"elsewhere";elsewhere.mkdir()
  link=tmp_path/"generated-media"
  if os.name=="nt":
   q=subprocess.run([r"C:\Windows\System32\cmd.exe","/d","/c","mklink","/J",str(link),str(elsewhere)],capture_output=True)
   assert q.returncode==0,q.stderr
  else:link.symlink_to(elsewhere,target_is_directory=True)
  with pytest.raises(WebBackendError)as e:store.generated_png_path("native-session-A",source["ref_id"])
  assert e.value.code=="generated_media_path_unsafe" and list(elsewhere.iterdir())==[]
 finally:store.close()
@pytest.mark.parametrize("limit",["count","bytes","unexpected_file"])
def test_derived_cache_budget_is_finite_and_never_evicts(tmp_path,limit):
 store=WebSourceStore(tmp_path/"s.sqlite")
 try:
  source,raw=retained_png(store);directory=tmp_path/"generated-media";directory.mkdir()
  target=directory/("a"*64+".png" if limit!="unexpected_file" else "not-ours.txt")
  target.write_bytes(b"retained")
  if limit=="count":store.max_sources=1
  elif limit=="bytes":store.max_bytes=len(raw)
  with pytest.raises(WebBackendError)as e:store.generated_png_path("native-session-A",source["ref_id"])
  assert e.value.code==("generated_media_path_unsafe" if limit=="unexpected_file" else "generated_media_full")
  assert target.read_bytes()==b"retained" and len(list(directory.iterdir()))==1
 finally:store.close()
def test_atomic_no_replace_race_retains_winner_and_fails(tmp_path,monkeypatch):
 store=WebSourceStore(tmp_path/"s.sqlite")
 try:
  source,_=retained_png(store)
  def raced(source_path,target):
   Path(target).write_bytes(b"racing wrong pixels")
   raise FileExistsError("synthetic race")
  monkeypatch.setattr(wb.os,"rename" if os.name=="nt" else "link",raced)
  with pytest.raises(WebBackendError)as e:store.generated_png_path("native-session-A",source["ref_id"])
  assert e.value.code=="generated_media_integrity_error"
  directory=tmp_path/"generated-media";assert len(list(directory.iterdir()))==1
  assert next(directory.iterdir()).read_bytes()==b"racing wrong pixels"
 finally:store.close()
async def test_display_publication_failure_is_failed_receipt_not_render_success(components,monkeypatch):
 backend,store,_,fetcher=components
 async def fetch(url,policy):return RetrievedDocument(url,url,"application/pdf",painted_pdf(),"now")
 fetcher.fetch=fetch
 def fail(*a,**kw):raise OSError("synthetic owned disk error")
 monkeypatch.setattr(store,"generated_png_path",fail)
 with pytest.raises(WebBackendError):
  await backend.handle(request({"screenshot":[{"ref_id":"https://example.com/test.pdf","pageno":0}]}))
 outcome,evidence=store.db.execute("SELECT outcome,evidence FROM web_receipts WHERE operation='screenshot'").fetchone()
 assert outcome=="failed" and "generated_display" not in json.loads(evidence)
async def test_commons_tool_text_uses_actual_thumbnail_without_local_download(components):
 backend,store,_,fetcher=components
 class Images(wb.WikimediaImageSearch):
  async def search(self,q,policy,*,max_results,caption):
   data=commons_response();data["query"]["pages"][0]["imageinfo"][0]["extmetadata"]["ObjectName"]={"value":"](<https://evil.invalid>)"}
   return wb.WikimediaImageSearch.extract(data,policy,max_results=max_results,caption=caption),{}
 backend.image_search=Images()
 result=await backend.handle(request({"image_query":[{"q":"fixture"}]}))
 expected=result["results"][0]["image_result"]["thumbnail_url"]
 assert "![Web image](<"+expected+">)" in result["output"]
 assert "![Web image" in result["output"] and "Special:FilePath" not in result["output"]
 assert fetcher.calls==[] and not (store.path.parent/"generated-media").exists()
@pytest.mark.parametrize("target",["turn383image0","relative/image.png","/tmp/bad\nname.png","/tmp/a>evil.png"])
def test_native_markdown_rejects_bare_refs_and_control_or_delimiter_paths(target):
 with pytest.raises(WebBackendError):wb.native_image_markdown(target)
def test_native_markdown_fixed_caption_and_encoded_remote_delimiters():
 result=wb.native_image_markdown("https://upload.wikimedia.org/a.jpg?q=><x>")
 assert result=="![Web image](<https://upload.wikimedia.org/a.jpg?q=%3E%3Cx%3E>)"


@pytest.mark.parametrize("asset_host,blocked,expected",[
 ("upload.wikimedia.org",[],200),
 ("thumb.wikimedia.org",[],200),
 ("upload.wikimedia.org",["upload.wikimedia.org"],403),
 ("foreign-cdn.example",[],502),
])
async def test_query_landing_allowlist_is_distinct_from_known_asset_cdn_policy(components,asset_host,blocked,expected):
 backend,store,_,fetcher=components
 class Images(wb.WikimediaImageSearch):
  async def search(self,q,policy,*,max_results,caption):
   data=commons_response()
   item=data["query"]["pages"][0]["imageinfo"][0]
   item["thumburl"]="https://"+asset_host+"/wikipedia/commons/thumb/a/a1/fixture.jpg/200px-fixture.jpg"
   return wb.WikimediaImageSearch.extract(data,policy,max_results=max_results,caption=caption),{}
 backend.image_search=Images()
 body=request({"image_query":[{"q":"fixture","domains":["commons.wikimedia.org"]}]},
              settings={"filters":{"blocked_domains":blocked}})
 if expected==200:
  result=await backend.handle(body)
  assert "![Web image](<https://"+asset_host+"/" in result["output"]
  assert result["results"][0]["image_result"]["thumbnail_url"].startswith("https://"+asset_host+"/")
 else:
  with pytest.raises(WebBackendError)as error:
   await backend.handle(body)
  assert error.value.status==expected
 assert fetcher.calls==[] and not (store.path.parent/"generated-media").exists()

async def test_backend_display_rejects_foreign_asset_even_if_backend_skips_extract(components):
 backend,_,_,_=components
 class Images(wb.WikimediaImageSearch):
  async def search(self,q,policy,*,max_results,caption):
   source=wb.WikimediaImageSearch.extract(commons_response(),policy,max_results=max_results,caption=caption)[0]
   source["image"]["thumbnail_url"]="https://foreign-cdn.example/image.jpg"
   return [source],{}
 backend.image_search=Images()
 with pytest.raises(WebBackendError)as error:
  await backend.handle(request({"image_query":[{"q":"fixture","domains":["commons.wikimedia.org"]}]}))
 assert error.value.code=="image_search_protocol_error"
