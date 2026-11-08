"""Assemble the exact retained P167 bytes for an unpublished smoke-only draft."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request

REPO = Path(__file__).resolve().parents[2]
REPOSITORY = "0xZoharHuang/codex-app-deepseek-gateway"
LABEL = "EARLY_UNQUALIFIED"
ASSEMBLY_ASSET = f"cadsg-p167-{LABEL}-assembly-result.json"
ORIGINAL_RAW = "3bd9b092b342b728d59db3a92b4200d70b981ae3f9d047ad36fb55f3c0bb9f38"
ORIGINAL_BUILD = "07f8f7bcf9fdc3247b62126e479734890c733e00af8665a8465b0c8e966a7575"

def require(condition, message):
    if not condition:
        raise ValueError(message)

def controller():
    script = REPO / "scripts/ci/p20-windows.py"
    spec = importlib.util.spec_from_file_location("p167_early_normal_controller", script)
    ci = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ci)
    review = ci.load(REPO / "scripts/ci/p167-review.json")
    ci.CANDIDATE, ci.RUST_VERSION = "P167", "1.95.0"
    ci.P167_CASES = ci.select_p167(review)
    ci.LOCK, ci.SOURCE, ci.SOURCE_FILES = review["lock_sha256"], review["source_sha256"], review["source_files"]
    return ci

def admitted_context(ci, path):
    require(os.environ.get("CI_P167_EARLY_DRAFT") == "1"
            and os.environ.get("CI_P167_TEST_RESUME") == "1"
            and os.environ.get("GITHUB_REPOSITORY", "").casefold() == REPOSITORY.casefold(),
            "Exact early draft workflow required")
    root = ci.hosted_root(path)
    head = subprocess.check_output(["git.exe", "-C", str(REPO), "rev-parse", "HEAD"], text=True).strip()
    require(re.fullmatch(r"[0-9a-f]{40}", head) and head == os.environ.get("GITHUB_SHA"),
            "Exact dispatched early assembler head required")
    return root, head

def retained_identity(retained, resume):
    require(retained.get("same_runner_core_run") == resume.RUN
            and retained.get("same_runner_core_artifact_sha256") == resume.ZIP_SHA
            and retained.get("same_runner_core_compile_sha256") == resume.CORE_SAME_RUNNER_SHA
            and retained.get("repeat_core_compile_run") == "42511450429"
            and retained.get("repeat_core_artifact_sha256") ==
                "5c504baf7ae12bcf96073ff6b12567d15612110ceb2ae5a2daa38e0a854eb213"
            and retained.get("repeat_core_compile_sha256") == resume.CORE_REPEAT_SHA
            and retained.get("raw_manifest_sha256") == ORIGINAL_RAW
            and retained.get("native_producer_rerun") is False
            and retained.get("original_sandbox_reexecuted") is False
            and retained.get("original_git_reexecuted") is False,
            "Exact retained producer/18/Git12 chain required")


def assemble(ci, root, head):
    lock, builder, _ = ci.plan()
    retained = ci.load(root / "evidence/p167-retained-producer.json")
    retained_identity(retained, ci.resume_controller())
    raw = root / "native-producer/manifest.json"
    require(ci.sha(raw) == ORIGINAL_RAW and ci.sha(root / "evidence/normal-six-build.json") == ORIGINAL_BUILD,
            "Exact original normal producer manifest and observer required")
    manifest = ci.load(raw)
    ci.verify_normal_outputs(root, manifest)
    require(retained["six"] == {r["file"]: {"size":r["size"], "sha256":r["sha256"]}
                                  for r in manifest["binaries"]}, "Original six sizes and hashes differ")
    source = ci.work_path(root) / "source"
    before = builder.source_state(source)
    require(before["sha256"] == ci.SOURCE and before["count"] == ci.SOURCE_FILES, "Exact prepared source required")
    cache, _ = ci.cache_paths(root)
    toolchain = Path(os.environ["CI_RUST_TOOLCHAIN_BIN"])
    git = shutil.which("git.exe")
    require(git is not None, "Exact native Git required")
    args = [sys.executable, "-I", "-B", str(REPO / "scripts/package_native_release.py"),
            "--source-root", str(source), "--native-manifest", str(raw),
            "--native-manifest-sha256", ORIGINAL_RAW, "--build-lock", str(ci.native_lock_path()),
            "--cargo", str(toolchain / "cargo.exe"), "--cargo-home", str(cache / "cargo"),
            "--git", git, "--output", str(root / "assembled"), "--evidence-dir", str(root / "evidence/early-assembly")]
    environment = ci.clean_environment()
    environment["CARGO_NET_OFFLINE"] = "true"
    ci.observe(args, root, REPO, environment, "early-normal-package", 1800)
    assembly_path = root / "evidence/early-assembly/assembly-result.json"
    d = ci.load(assembly_path)
    archive = root / "assembled.tar.gz"
    require(Path(d["archive"]) == archive and d["kind"] == "clean_source_assembly"
            and d["status"] == "raw_native_assembled_not_App_qualified" and d["raw_binary_count"] == 6
            and d["no_compile_or_native_execution"] is True and d["no_official_App_or_prepared_sidecar"] is True
            and d["original_source_and_receipts_unchanged"] is True
            and d["archive_identity"]["sha256"] == ci.sha(archive)
            and d["archive_identity"]["size"] == archive.stat().st_size
            and builder.source_state(source) == before, "Ordinary clean-source early package evidence differs")
    ci.verify_normal_outputs(root, manifest)
    proof = {"status":"P167_EARLY_UNQUALIFIED_PRIVATE_SMOKE_ONLY", "label":LABEL,
             "head":head, "run":os.environ["GITHUB_RUN_ID"], "source_sha256":ci.SOURCE,
             "build_lock_sha256":ci.LOCK, "normal_assembly_receipt_sha256":ci.sha(assembly_path),
             "normal_assembly_receipt_size":assembly_path.stat().st_size, "normal_assembly_receipt_asset":ASSEMBLY_ASSET,
             "native_manifest_sha256":d["manifest_sha256"],
             "archive_sha256":d["archive_identity"]["sha256"], "archive_size":d["archive_identity"]["size"],
             "original_normal_manifest_sha256":ORIGINAL_RAW, "retained_producer":retained,
             "native_tests_remaining":13, "full_native_suite_qualified":False, "App_GUI_qualified":False,
             "public_stable_release":False, "native_compile_or_execution":False}
    ci.save(root / "evidence/EARLY_UNQUALIFIED.json", proof)
    print(json.dumps({"status":proof["status"], "sha256":proof["archive_sha256"], "size":proof["archive_size"]}), flush=True)

def diagnostic_tail(path, limit=4096):
    if not path.is_file():
        return None
    with path.open("rb") as stream:
        size = stream.seek(0, 2)
        stream.seek(max(0, size - limit))
        text = stream.read(limit).decode("utf-8", errors="replace")
    if size > limit:
        text = text.partition("\n")[2]
    text = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", text)
    text = re.sub(r"(?i)(https?://)[^/\s:@]+:[^/\s@]+@", r"\1[redacted]@", text)
    text = re.sub(r"(?i)(https?://[^\s?\"']+)\?[^\s\"'<>]+", r"\1?[redacted]", text)
    text = re.sub(r"(?i)\bBearer\s+[^\s,\"']+", "Bearer [redacted]", text)
    text = re.sub(r"(?i)\b(?:gh[pousr]_|github_pat_|sk-or-v1-)[A-Za-z0-9_-]+", "[redacted]", text)
    text = re.sub(r"(?im)\b([A-Z_]*(?:TOKEN|PASSWORD|SECRET|COOKIE))\s*[:=]\s*[^\r\n]+", r"\1=[redacted]", text)
    return {"bytes": size, "tail": text, "truncated": size > limit}


def assembly_failure(ci, root):
    evidence = root / "evidence"
    detail = {"packager_log": diagnostic_tail(evidence / "early-normal-package.log"),
              "cargo_metadata_stderr": diagnostic_tail(evidence / "early-assembly/cargo-metadata.stderr")}
    path = evidence / "early-normal-package.json"
    if path.is_file():
        try:
            raw = ci.load(path)
            keys = ("passed", "observer_passed", "returncode", "timed_out", "seconds",
                    "fixed_timeout_seconds", "wrapper_minimum_available_memory_bytes")
            detail["observer"] = {key:raw.get(key) for key in keys if type(raw.get(key)) in (int,float,bool)}
            for key in ("unknown_live", "remaining_after_cleanup", "cleanup_errors", "observation_errors"):
                detail["observer"][key+"_count"] = len(raw.get(key) or [])
            detail["observer"]["resource_guard_present"] = bool(raw.get("resource_guard") or raw.get("wrapper_resource_guard"))
            detail["observer"]["raw_sha256"] = ci.sha(path)
        except (OSError, ValueError, TypeError):
            detail["observer"] = {"readable":False}
    print("EARLY_UNQUALIFIED_DIAGNOSTIC " + json.dumps(detail, ensure_ascii=True), flush=True)


def gh_json(*args):
    return json.loads(subprocess.check_output(["gh", "api", *args], timeout=180))

def draft_state(value, release, head, expected_names):
    require(value["id"] == release and value["draft"] is True and value["prerelease"] is True
            and value["target_commitish"] == head and value["published_at"] is None
            and value["tag_name"] == "cadsg-p167-early-unqualified-" + head[:12]
            and LABEL in value["name"] and {asset["name"] for asset in value["assets"]} == set(expected_names),
            "Require the exact unpublished smoke-only draft and expected assets")

def anonymous_hidden(asset):
    url = f"https://api.github.com/repos/{REPOSITORY}/releases/assets/{asset}"
    request = urllib.request.Request(url, headers={"Accept":"application/vnd.github+json",
                                                 "User-Agent":"CADSG-draft-visibility-check"})
    try:
        with urllib.request.urlopen(request, timeout=20):
            raise ValueError("Draft asset metadata visible to anonymous access")
    except urllib.error.HTTPError as e:
        require(e.code == 404, "Anonymous draft visibility could not be proven")

def upload(ci, root, head, release):
    require(type(release) is int and release > 0 and bool(os.environ.get("GH_TOKEN")),
            "Exact authorized draft asset upload required")
    proof_file = root / "evidence/EARLY_UNQUALIFIED.json"
    proof = ci.load(proof_file)
    archive = root / "assembled.tar.gz"
    require(proof["status"] == "P167_EARLY_UNQUALIFIED_PRIVATE_SMOKE_ONLY"
            and proof["head"] == head and proof["run"] == os.environ["GITHUB_RUN_ID"]
            and proof["archive_sha256"] == ci.sha(archive) and proof["archive_size"] == archive.stat().st_size
            and proof["full_native_suite_qualified"] is False and proof["App_GUI_qualified"] is False
            and proof["public_stable_release"] is False, "Require exact unqualified archive proof")
    assembly_path = root / "evidence/early-assembly/assembly-result.json"
    require(proof["normal_assembly_receipt_asset"] == ASSEMBLY_ASSET
            and proof["normal_assembly_receipt_sha256"] == ci.sha(assembly_path)
            and proof["normal_assembly_receipt_size"] == assembly_path.stat().st_size,
            "Exact original same-run assembly receipt required")
    assembly = ci.load(assembly_path)
    require(Path(assembly["archive"]) == archive and assembly["kind"] == "clean_source_assembly"
            and assembly["status"] == "raw_native_assembled_not_App_qualified"
            and assembly["raw_binary_count"] == 6 and assembly["no_compile_or_native_execution"] is True
            and assembly["original_source_and_receipts_unchanged"] is True
            and assembly["manifest_sha256"] == proof["native_manifest_sha256"]
            and assembly["archive_identity"]["sha256"] == proof["archive_sha256"]
            and assembly["archive_identity"]["size"] == proof["archive_size"],
            "Same-run assembly receipt differs from archive or manifest")
    endpoint = f"repos/{REPOSITORY}/releases/{release}"
    uploaded = {}
    assets = [
        (archive, f"cadsg-p167-{LABEL}-{proof['archive_sha256'][:20]}.tar.gz", "application/gzip",
         proof["archive_size"], proof["archive_sha256"]),
        (assembly_path, ASSEMBLY_ASSET, "application/json",
         proof["normal_assembly_receipt_size"], proof["normal_assembly_receipt_sha256"]),
        (proof_file, f"cadsg-p167-{LABEL}-provenance.json", "application/json",
         proof_file.stat().st_size, ci.sha(proof_file)),
    ]
    for path, name, content_type, size, checksum in assets:
        draft_state(gh_json(endpoint), release, head, uploaded)
        require(path.stat().st_size == size and ci.sha(path) == checksum, "Draft input changed before upload")
        query = urllib.parse.urlencode({"name":name, "label":LABEL})
        url = f"https://uploads.github.com/{endpoint}/assets?{query}"
        asset = gh_json("--method", "POST", url, "-H", "Content-Type: "+content_type, "--input", str(path))
        identity = {"id":asset["id"], "size":size, "digest":"sha256:"+checksum}
        require(type(asset["id"]) is int and asset["id"] > 0 and asset["name"] == name
                and all(asset[k] == v for k,v in identity.items()), "Draft asset identity differs")
        uploaded[name] = identity
        value = gh_json(endpoint)
        draft_state(value, release, head, uploaded)
        actual = {row["name"]:{k:row[k] for k in ("id","size","digest")} for row in value["assets"]}
        require(actual == uploaded, "Draft assets changed during upload")
        anonymous_hidden(asset["id"])
    print(json.dumps({"status":"EARLY_UNQUALIFIED_DRAFT_ASSETS_VERIFIED", "draft_id":release,
                      "head":head, "assets":uploaded}), flush=True)

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("assemble", "upload"))
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--release", type=int)
    args = parser.parse_args()
    ci = controller()
    root, head = admitted_context(ci, args.root)
    if args.mode == "assemble":
        try:
            assemble(ci, root, head)
        except Exception:
            try:
                assembly_failure(ci, root)
            except Exception as diagnostic_error:
                print("EARLY_UNQUALIFIED_DIAGNOSTIC_UNAVAILABLE " + type(diagnostic_error).__name__, flush=True)
            raise
    else:
        upload(ci, root, head, args.release)

if __name__ == "__main__":
    main()
