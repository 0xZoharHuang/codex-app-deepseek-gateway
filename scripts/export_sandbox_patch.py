"""Export the fixed-source sandbox patch without executing any native runtime."""
from __future__ import annotations
import argparse
import difflib
import hashlib
import json
import re
from pathlib import Path
import tarfile
import tomllib

BASE_COMMIT = "680c4ba93ec79c292287fb9e0b627dc7a76e3b5e"
ARCHIVE_SHA256 = "b95f2de5822fa83b277207b8c88dd19404427d54a16e87726a4d2039c4619ebb"

def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", "--source-root", dest="source_root", type=Path, required=True)
    parser.add_argument("--archive",type=Path,required=True)
    parser.add_argument("--output",type=Path,default=Path(__file__).resolve().parents[1]/"native")
    args=parser.parse_args()
    assert hashlib.sha256(args.archive.read_bytes()).hexdigest()==ARCHIVE_SHA256,"upstream archive changed"
    originals={}
    with tarfile.open(args.archive) as archive:
        for member in archive.getmembers():
            if not member.isfile():continue
            relative=member.name.split("/",1)[1]
            if relative.startswith("codex-rs/windows-sandbox-rs/") or relative=="codex-rs/Cargo.lock":
                originals[relative]=archive.extractfile(member).read()
    current={str(p.relative_to(args.source_root)).replace("\\","/"):p.read_bytes()
             for p in (args.source_root/"codex-rs/windows-sandbox-rs").rglob("*") if p.is_file()}
    current["codex-rs/Cargo.lock"]=(args.source_root/"codex-rs/Cargo.lock").read_bytes()
    old_lock=tomllib.loads(originals["codex-rs/Cargo.lock"].decode())
    new_lock=tomllib.loads(current["codex-rs/Cargo.lock"].decode())
    external=lambda lock: sorted((p for p in lock["package"] if "source" in p),key=lambda p:(p["name"],p["version"],p["source"]))
    assert external(old_lock)==external(new_lock),"external dependency lock records changed"
    old_workspace={p["name"]:p for p in old_lock["package"] if "source" not in p}
    new_workspace={p["name"]:p for p in new_lock["package"] if "source" not in p}
    workspace_versions=[{"name":name,"before":p["version"],"after":new_workspace[name]["version"]}
        for name,p in old_workspace.items() if name in new_workspace and p["version"]!=new_workspace[name]["version"]]
    lock_note={"reason":"synchronize published workspace versions with the fixed release Cargo.toml; preserve all external pins",
        "external_records_unchanged":True,"external_records_count":len(external(old_lock)),"workspace_version_changes":workspace_versions,
        "added_existing_dependency":"codex-windows-sandbox -> sha2 0.10.9"}
    patch=[];files=[]
    for relative in sorted(originals.keys()|current.keys()):
        old=originals.get(relative);new=current.get(relative)
        if old==new:continue
        assert new is not None,"unexpected source deletion"
        before=(old or b"").decode("utf8").splitlines(keepends=True)
        after=new.decode("utf8").splitlines(keepends=True)
        patch.append(f"diff --git a/{relative} b/{relative}\n")
        if old is None:patch.append("new file mode 100644\n")
        patch.extend(difflib.unified_diff(before,after,fromfile="/dev/null" if old is None else "a/"+relative,tofile="b/"+relative))
        files.append({"path":relative,"before_sha256":None if old is None else hashlib.sha256(old).hexdigest(),"after_sha256":hashlib.sha256(new).hexdigest()})
    args.output.mkdir(parents=True,exist_ok=True)
    patch_bytes="".join(patch).encode()
    (args.output/"windows-sandbox-isolation.patch").write_bytes(patch_bytes)
    # A source change invalidates old pure-test evidence; do not hard-code a passing count.
    pure_evidence=None
    key_hash=hashlib.sha256(current["codex-rs/windows-sandbox-rs/src/namespace_key.rs"]).hexdigest()
    evidence_root=Path(__file__).resolve().parents[1]/".evidence"
    for report_path in sorted(evidence_root.glob("sandbox-namespace-pure-tests-*.json"),key=lambda p:p.stat().st_mtime,reverse=True):
        report=json.loads(report_path.read_text(encoding="utf8"))
        if report.get("source_sha256")==key_hash and report.get("exit_code")==0:
            match=re.search(r"test result: ok\. (\d+) passed",report.get("stdout",""))
            if match:
                pure_evidence={"report":report_path.name,"source_sha256":key_hash,"passed":int(match.group(1)),"scope":report.get("scope")}
                break
    manifest={"schema_version":1,"upstream_commit":BASE_COMMIT,"upstream_archive_sha256":ARCHIVE_SHA256,
              "patch_sha256":hashlib.sha256(patch_bytes).hexdigest(),"files":files,"lock_normalization":lock_note,
              "scope":"home-scoped resource identities and native ownership admission; original sandbox algorithms retained",
              "verification":{"pure_namespace_tests":None if pure_evidence is None else pure_evidence["passed"],"pure_evidence":pure_evidence,"complete_runtime_build":False,"native_setup_executed":False,"app_acceptance":False},
              "shared_acl_concurrency":"See docs/windows-sandbox-isolation.md; namespace isolation is not proof of overlapping ACL update safety."}
    (args.output/"windows-sandbox-isolation-source-manifest.json").write_text(json.dumps(manifest,indent=2)+"\n",encoding="utf8")
    print(json.dumps({"files":len(files),"patch_bytes":len(patch_bytes),"patch_sha256":manifest["patch_sha256"]}))

if __name__=="__main__":main()
