"""Reuse the exact completed P167 six/18 evidence, then finish remaining gates."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("p167_preceding_resume", Path(__file__).with_name("p167-resume.py"))
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
original_work = previous.original_work
original_v8_directory = previous.original_v8_directory
stage_v8_inputs = previous.stage_v8_inputs
need = previous.require

RUN = "42504592572"
COMMIT = "86f39e40b0b4257b2cd0451f262a88e579463ac0"
ARTIFACT = 13629186832
SIZE = 141667502
ZIP_SHA = "eb1c90aad09e7895b20b5782b1fbdba0a43f14cd14fdb18846c84c906783dcd2"
PRODUCER_RUN = "42469680627"
PRODUCER_ZIP_SHA = "39ea2714f46b6af8959c7703c6528b767301c60d174694e8575934421d50b82f"
RETAINED_SHA = "8ae4bc01869a147fb9c895645ada9ef93405cee22a4bb62b4ad9caed1592857e"
GIT_SHA = "c078884443ee9d8865497436620bb899abe0b9e131acccfb2700ec1b40169c30"
GIT_COMPILE_SHA = "48715fe8357b7b63ec3745409d5e556a7a44cead7e970243694f3daf2524d5cb"
CORE_TIMEOUT_SHA = "99b1ebcb1e9dce1add23c5e540b269eea3b7691ac4805efe1ad0b5c387bd9b63"
RAW_SHA = "3bd9b092b342b728d59db3a92b4200d70b981ae3f9d047ad36fb55f3c0bb9f38"
BUILD_SHA = "07f8f7bcf9fdc3247b62126e479734890c733e00af8665a8465b0c8e966a7575"
SANDBOX_SHA = "e43829cedfb262e6f67321c8ed3063f5e808c738df458402363c99158ef29b86"
GIT_FAILURE_SHA = "7dc4493fae2e9342195b3172f196df0a1afd79102a5d769eb3402fe9737190c7"
KEY = ("p167-native-v1-Windows-rust1.95.0-msvc14.44.35207-sdk10.0.26100.0-"
       "24dab591cfe8c61f3960f109f1a67c7d3e57d9dc371fe8ed09042d35cc695c6d-42504592572-1")
LEAF = "codex-windows-sandbox-p167-libtest.exe"
GIT_LEAF = "codex-core-plugins-p167-libtest.exe"
ORIGINAL_SANDBOX = Path(rf"C:\cagp167-{PRODUCER_RUN}-1\delivery\unit") / LEAF
ORIGINAL_GIT = Path(rf"C:\cagp167-{RUN}-1\delivery\unit") / GIT_LEAF
RETAINED_UNITS = {
    "native-unit18.json": (SANDBOX_SHA, ORIGINAL_SANDBOX, "sandbox_receipt_sha256", "original_sandbox_executable"),
    "native-git-unit12.json": (GIT_SHA, ORIGINAL_GIT, "git_receipt_sha256", "original_git_executable"),
}

def sha(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()

def original_archive(root):
    return root / "evidence" / f"retained-git-p167-{RUN}.zip"

def verify_zip(path):
    need(path.is_file() and path.stat().st_size == SIZE and sha(path) == ZIP_SHA,
         "Exact prior producer/18 ZIP required")

def download(root):
    need(os.environ.get("CI_P167_TEST_RESUME") == "1"
         and os.environ.get("GITHUB_REPOSITORY", "").casefold() == previous.REPOSITORY.casefold(),
         "Exact test-resume repository required")
    out = original_archive(root)
    part = out.with_suffix(".part")
    with part.open("xb") as f:
        subprocess.run(["gh", "api", f"repos/{previous.REPOSITORY}/actions/artifacts/{ARTIFACT}/zip"],
                       stdout=f, timeout=120, check=True)
    verify_zip(part)
    part.rename(out)

def read_origin(path, evidence, work, cache, target, lock, source_sha, lock_sha):
    verify_zip(path)
    with zipfile.ZipFile(path) as z:
        def exact(name, digest=None):
            b = z.read("evidence/" + name)
            need(digest is None or previous.digest(b) == digest, f"Original receipt differs: {name}")
            return json.loads(b)
        raw = exact("normal-raw-manifest.json", RAW_SHA)
        build = exact("normal-six-build.json", BUILD_SHA)
        old_git = exact("original-native-git-unit-compile.json", GIT_FAILURE_SHA)
        git = exact("native-git-unit-compile.json", GIT_COMPILE_SHA)
        core = exact("native-p167-codex-core-compile.json", CORE_TIMEOUT_SHA)
        retained = exact("p167-retained-producer.json", RETAINED_SHA)
        sandbox = exact("native-unit18.json", SANDBOX_SHA)
        git_tests = exact("native-git-unit12.json", GIT_SHA)
        deps = exact("p167-dependency-checkout.json")
        bind = exact("normal-source-binding.json")
        inputs = exact("inputs.json")
        current = json.loads((evidence / "inputs.json").read_bytes())
        fields = ("lock_sha256", "source_sha256", "normal_builder_sha256", "normal_packager_sha256", "build_jobs")
        need(inputs["source_commit"] == COMMIT and inputs["build_jobs"] == 2
             and all(current[key] == inputs[key] for key in fields), "Normal source/builder/lock inputs differ")
        toolchain = exact("toolchain.json")
        current_tools = json.loads((evidence / "toolchain.json").read_bytes())
        need(all(current_tools[k] == toolchain[k] for k in ("rustc", "cargo", "VCToolsVersion", "WindowsSDKVersion",
             "UCRTVersion", "compiler", "linker", "ImageOS")), "Measured compiler/SDK differs")
        need(raw["work_dir"] == str(work) and raw["source"] == str(work / "source")
             and raw["target"] == str(target) and raw["cargo_home"] == str(cache / "cargo")
             and raw["source_sha256"] == source_sha and raw["build_lock_sha256"] == lock_sha
             and raw["native_version"] == lock["native_version"] and raw["status"] == "built-not-app-qualified"
             and raw["effective_build_jobs"] == 2 and raw["app_qualified"] is False
             and bind["normal_manifest_sha256"] == RAW_SHA and bind["native_build_executed"] is True
             and build["passed"] is True and build["returncode"] == 0 and build["timed_out"] is False
             and build["fixed_timeout_seconds"] == 7200 and not build["unknown_live"]
             and not build["remaining_after_cleanup"], "Completed normal six provenance differs")
        need(retained["completed_producer_run"] == PRODUCER_RUN
             and retained["artifact_sha256"] == PRODUCER_ZIP_SHA
             and retained["raw_manifest_sha256"] == RAW_SHA and retained["sandbox_receipt_sha256"] == SANDBOX_SHA
             and retained["native_producer_rerun"] is False and retained["original_sandbox_reexecuted"] is False,
             "Original six/18 retained producer chain differs")
        need(old_git["timed_out"] is True and old_git["passed"] is False
             and git["passed"] is True and git["returncode"] == 0 and git["timed_out"] is False
             and not git["unknown_live"] and not git["remaining_after_cleanup"]
             and git["fixed_timeout_seconds"] == 1800
             and (git["observer_passed"] is True or (git.get("qualified_compiler_telemetry_cleanup") or {}).get(
                  "kind") == "observed_MSVC_VCTIP_telemetry_cleanup_only")
             and git["log_sha256"] == previous.digest(z.read("evidence/native-git-unit-compile.log")),
             "Original qualified Git compiler gate differs")
        need(core["timed_out"] is True and core["fixed_timeout_seconds"] == 1800 and core["passed"] is False
             and not any(core.get(k) for k in ("unknown_live","remaining_after_cleanup","resource_guard",
                                              "wrapper_resource_guard","cleanup_errors","observation_errors"))
             and core["log_sha256"] == previous.digest(z.read("evidence/native-p167-codex-core-compile.log")),
             "Original Core compilation timeout differs")
        need(deps["status"] == "LOCKED_FETCH_CHECKOUT_PROVEN" and deps["source_sha256"] == source_sha
             and deps["build_lock_sha256"] == lock_sha and deps["cargo_lock_sha256"] == lock["cargo_lock_sha256"]
             and deps["cargo_home"] == str(cache / "cargo"), "Original locked dependency witness differs")
        names = json.loads((REPO / "scripts/ci/p20-native-unit-cases.json").read_bytes())
        need([x["name"] for x in sandbox["cases"]] == names, "Original sandbox18 selectors differ")
        for receipt, status, executable, prefix, count in (
            (sandbox, "P167_NATIVE_COMPONENT18_PASSED_NOT_APP_QUALIFIED", ORIGINAL_SANDBOX, "native-case", 18),
            (git_tests, "P167_NATIVE_GIT12_PASSED_NOT_APP_QUALIFIED", ORIGINAL_GIT, "native-git-case", 12),
        ):
            rows = receipt["cases"]
            need(receipt["status"] == status and receipt["source_sha256"] == source_sha
                 and receipt["lock_sha256"] == lock_sha and receipt["App_GUI_qualified"] is False
                 and len(rows) == len({x["name"] for x in rows}) == count
                 and receipt["executable"]["path"] == str(executable)
                 and receipt["executable"]["profile"]["test"] is True, "Original sealed unit group differs")
            with z.open(f"delivery/unit/{executable.name}") as f:
                need(hashlib.file_digest(f, "sha256").hexdigest() == receipt["executable"]["sha256"],
                     "Original sealed libtest differs")
            for i, row in enumerate(rows):
                stage = f"{prefix}-{i:02}"
                observed = exact(stage + ".json")
                need(observed["passed"] is True and observed["returncode"] == 0 and observed["timed_out"] is False
                     and not observed["unknown_live"] and not observed["remaining_after_cleanup"]
                     and observed["command"] == [str(executable), "--exact", row["name"], "--show-output", "--test-threads=1"]
                     and previous.digest(z.read(f"evidence/{stage}.log")) == row["log_sha256"] == observed["log_sha256"],
                     "Original native case evidence differs")
    return {**retained, "cache_key": KEY, "checkout": deps["checkout"],
            "retained_git_run": RUN, "retained_git_commit": COMMIT, "retained_git_artifact_sha256": ZIP_SHA,
            "git_receipt_sha256": GIT_SHA, "original_git_executable": str(ORIGINAL_GIT),
            "original_core_compile_sha256": CORE_TIMEOUT_SHA, "original_git_reexecuted": False}


def admit_cache(root, lock, source_sha, lock_sha, cache, target):
    need(os.environ.get("CI_P167_TEST_RESUME") == "1" and os.environ.get("CI_P167_CACHE_HIT") == "true"
         and os.environ.get("CI_P167_CACHE_KEY") == KEY, "Exact completed P167 cache required")
    work = original_work(root)
    need(work.parent != root and not work.parent.exists() and target.resolve(strict=True) == target
         and not target.is_junction() and cache.resolve(strict=True) == cache,
         "Fresh runner, original source path and physical cache required")
    return read_origin(original_archive(root), root / "evidence", work, cache, target, lock, source_sha, lock_sha)

def cache_binaries(target, raw, expected):
    rows = raw["binaries"]
    need(len(rows) == len(expected) == 6 and {x["file"] for x in rows} == expected, "Exact six raw native rows required")
    for row in rows:
        p = target / "release" / row["file"]
        need(p.is_file() and not p.is_symlink() and p.resolve(strict=True) == p
             and p.stat().st_size == row["size"] and sha(p) == row["sha256"],
             f"Cached producer differs: {row['file']}")
    return rows

def restore(root, lock, builder, cache, target, expected, restored, git):
    work = original_work(root)
    with zipfile.ZipFile(original_archive(root)) as z:
        raw = z.read("evidence/normal-raw-manifest.json")
        need(previous.digest(raw) == RAW_SHA, "Original six manifest changed")
        rows = cache_binaries(target, json.loads(raw), expected)
        v8 = stage_v8_inputs(root, builder, lock["target"], restored)
        builder.prepare_source(root / "inputs/upstream.tar.gz", work / "source", lock, git)
        source = builder.source_state(work / "source")
        need(source["sha256"] == lock["prepared_source_sha256"] and source["count"] == lock["prepared_source_files"],
             "Resumed prepared source differs")
        producer = root / "native-producer"
        producer.mkdir()
        for row in rows:
            destination = producer / row["file"]
            with (target / "release" / row["file"]).open("rb") as src, destination.open("xb") as dst:
                shutil.copyfileobj(src, dst, 1024*1024)
            need(destination.stat().st_nlink == 1 and destination.stat().st_size == row["size"]
                 and sha(destination) == row["sha256"],
                 "Restored native copy differs")
        with (producer / "manifest.json").open("xb") as f:
            f.write(raw)
        def copy(member, path):
            with z.open(member) as src, path.open("xb") as dst:
                shutil.copyfileobj(src, dst, 1024*1024)
        evidence = root / "evidence"
        for name in ("normal-six-build.json", "normal-six-build.log", "normal-raw-manifest.json",
                     "normal-source-binding.json", "build-report.json", "source-manifest.json", "build-owner.json",
                     "native-unit18.json", "native-unit-compile.json", "native-unit-compile.log",
                     "native-git-unit12.json", "native-git-unit-compile.json", "native-git-unit-compile.log",
                     "original-native-git-unit-compile.json", "original-native-git-unit-compile.log",
                     "original-git_unit-failure.json"):
            copy("evidence/" + name, evidence / name)
        for name in ("native-p167-codex-core-compile.json", "native-p167-codex-core-compile.log", "extra_unit-failure.json"):
            copy("evidence/" + name, evidence / ("original-" + name))
        for prefix, count in (("native-case", 18), ("native-git-case", 12)):
            for i in range(count):
                for suffix in (".json", ".log"):
                    name = f"{prefix}-{i:02}{suffix}"
                    copy("evidence/" + name, evidence / name)
        unit = root / "delivery/unit"
        unit.mkdir()
        for leaf in (LEAF, GIT_LEAF):
            copy("delivery/unit/" + leaf, unit / leaf)
    return v8, {**restored, "six": {x["file"]: {"size": x["size"], "sha256": x["sha256"]} for x in rows}}
