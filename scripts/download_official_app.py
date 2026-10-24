"""Download the official pinned MSIX in verified resumable ranges; Windows only."""
from __future__ import annotations
import concurrent.futures as futures
import hashlib,json,os,pathlib,re,threading,time,urllib.request,zipfile
from datetime import datetime,timezone
ROOT=pathlib.Path(__file__).resolve().parents[1]
OUT=ROOT/".artifacts"/"ChatGPT-x64.msix"
PART=OUT.with_name(OUT.name+".range-partial")
EVIDENCE=ROOT/".evidence"/"app-download"
URL="https://persistent.oaistatic.com/codex-app-prod/ChatGPT-x64.msix"
ETAG='"0x8DF3022F3EB50A4"'
TOTAL=968406828
CHUNK=4*1024*1024
EVIDENCE.mkdir(parents=True,exist_ok=True)
STATE=EVIDENCE/"state.json"
LOCK=threading.Lock()
def now():return datetime.now(timezone.utc).isoformat()
def save(path,value):
    tmp=path.with_name(path.name+".tmp")
    tmp.write_text(json.dumps(value,indent=2,ensure_ascii=True),encoding="utf-8")
    os.replace(tmp,path)
def hashfile(path):
    with path.open("rb") as stream:return hashlib.file_digest(stream,"sha256").hexdigest()
def main():
    if STATE.exists():
        state=json.loads(STATE.read_text())
        if (state["url"],state["etag"],state["total"],state["chunk_bytes"])!=(URL,ETAG,TOTAL,CHUNK):raise RuntimeError("Refuse incompatible saved download identity")
    else:
        state={"schema_version":1,"url":URL,"etag":ETAG,"total":TOTAL,"chunk_bytes":CHUNK,"started":now(),"completed":{},"errors":[],"status":"downloading"}
    if OUT.exists():
        if OUT.stat().st_size==TOTAL and state.get("sha256")==hashfile(OUT):
            print(json.dumps({"status":"already_verified","path":str(OUT),"sha256":state["sha256"]}),flush=True)
            return
        raise RuntimeError("Existing destination identity is not a verified match")
    if not PART.exists():
        if state["completed"]:raise RuntimeError("Checkpoint data is absent")
        with PART.open("wb") as f:f.truncate(TOTAL)
    if PART.stat().st_size!=TOTAL:raise RuntimeError("Partial size differs")
    with PART.open("rb") as f:
        for index,item in list(state["completed"].items()):
            f.seek(item["start"]);data=f.read(item["bytes"])
            if hashlib.sha256(data).hexdigest()!=item["sha256"]:
                del state["completed"][index]
                state["errors"].append({"at":now(),"chunk":index,"error":"Saved segment failed readback and will be fetched again"})
    state["status"]="downloading";save(STATE,state)
    count=(TOTAL+CHUNK-1)//CHUNK
    started=time.monotonic()
    def download(index):
        a=index*CHUNK;b=min(TOTAL,a+CHUNK)-1;size=b-a+1
        last=None
        for attempt in range(1,6):
            try:
                request=urllib.request.Request(URL,headers={"Range":f"bytes={a}-{b}","If-Match":ETAG,"Accept-Encoding":"identity","User-Agent":"Mozilla/5.0"})
                with urllib.request.urlopen(request,timeout=40) as response:
                    if response.status!=206:raise RuntimeError(f"Expected HTTP206, got {response.status}")
                    if response.headers.get("ETag")!=ETAG:raise RuntimeError("ETag changed")
                    expected=f"bytes {a}-{b}/{TOTAL}"
                    if response.headers.get("Content-Range")!=expected:raise RuntimeError("Content-Range differs")
                    if int(response.headers.get("Content-Length","-1"))!=size:raise RuntimeError("Content-Length differs")
                    if response.headers.get("Content-Encoding") not in (None,"identity"):raise RuntimeError("Unexpected compression")
                    payload=response.read(size+1)
                    if len(payload)!=size:raise RuntimeError(f"Truncated range: {len(payload)} of {size}")
                    version=response.headers.get("x-ms-meta-package_version")
                digest=hashlib.sha256(payload).hexdigest()
                with LOCK:
                    with PART.open("r+b") as f:f.seek(a);f.write(payload);f.flush()
                    state["completed"][str(index)]={"start":a,"bytes":size,"sha256":digest,"attempt":attempt,"package_version_header":version,"at":now()}
                    save(STATE,state)
                    done=len(state["completed"])
                    if done%16==0 or done==count:
                        print(json.dumps({"completed_chunks":done,"total_chunks":count,"percent":round(done/count*100,1),"elapsed_seconds":round(time.monotonic()-started,1)}),flush=True)
                return
            except Exception as exc:
                last=exc
                with LOCK:
                    state["errors"].append({"chunk":index,"attempt":attempt,"error":type(exc).__name__+": "+str(exc),"at":now()})
                    save(STATE,state)
                if attempt<5:time.sleep(min(attempt*2,10))
        raise RuntimeError(f"Chunk {index} failed after five attempts: {last}")
    pending=[i for i in range(count) if str(i) not in state["completed"]]
    try:
        with futures.ThreadPoolExecutor(max_workers=4) as pool:
            jobs=[pool.submit(download,i) for i in pending]
            for job in futures.as_completed(jobs):job.result()
        with PART.open("rb") as f:
            for index in range(count):
                item=state["completed"][str(index)]
                f.seek(item["start"]);data=f.read(item["bytes"])
                if hashlib.sha256(data).hexdigest()!=item["sha256"]:raise RuntimeError("Final segment readback failed")
        digest=hashfile(PART)
        with zipfile.ZipFile(PART) as archive:
            manifest=archive.read("AppxManifest.xml")
            entries=[{"name":i.filename,"bytes":i.file_size,"compressed_bytes":i.compress_size} for i in archive.infolist() if re.search(r"(codex[^/]*[.]exe|AppxManifest[.]xml|AppxSignature[.]p7x)$",i.filename,re.I)]
        (EVIDENCE/"AppxManifest.xml").write_bytes(manifest)
        save(EVIDENCE/"native-entries.json",entries)
        os.replace(PART,OUT)
        state.update(status="downloaded_and_readback_verified",finished=now(),sha256=digest,path=str(OUT),bytes=OUT.stat().st_size)
        save(STATE,state)
        print(json.dumps({k:state[k] for k in ("status","path","bytes","sha256","finished")}),flush=True)
    except BaseException as exc:
        state.update(status="failed",last_error=type(exc).__name__+": "+str(exc),failed_at=now())
        save(STATE,state)
        raise
if __name__=="__main__":main()
