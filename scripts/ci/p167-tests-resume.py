"""Reuse the exact completed P167 six/18 evidence, then finish remaining gates."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile
from urllib.parse import urlencode

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("p167_preceding_resume", Path(__file__).with_name("p167-resume.py"))
previous = importlib.util.module_from_spec(spec)
spec.loader.exec_module(previous)
original_work = previous.original_work
original_v8_directory = previous.original_v8_directory
stage_v8_inputs = previous.stage_v8_inputs
need = previous.require

RUN = "42516370653"
COMMIT = "9924d70f11d9733ee495a00d45ac4f3a50f9f47b"
ARTIFACT = 13634163269
SIZE = 372046001
ZIP_SHA = "7d1d393fdf8585e75bf12d43126df15b635a98bc699ae438935b6bd60cccf62b"
CORE_SAME_RUNNER_SHA = "7eb236bf8a73d7b2900f91b61f752610ae61af28069afbca70982526fcc763b0"
CORE_WARM_OBSERVER_SHA = "84bd8f0c6790e85843cc56d9ebb807409ebf1ff46200e3ffcebb0a460990d4e2"
CORE_WARM_PROOF_SHA = "8f309ab17e4106378e0d591003cfec8c6eeb6b85e429d94882e2c25ac4498374"
GIT_RUN = "42504592572"
GIT_ZIP_SHA = "eb1c90aad09e7895b20b5782b1fbdba0a43f14cd14fdb18846c84c906783dcd2"
CORE_REPEAT_SHA = "092de3ce84a3bdd2d0742cb6f50418b9a9008089094515edf8a0697927b1414c"
PRODUCER_RUN = "42469680627"
PRODUCER_ZIP_SHA = "39ea2714f46b6af8959c7703c6528b767301c60d174694e8575934421d50b82f"
RETAINED_SHA = "999e250a6130739d8fd98d13dbe811f32a79fffb24b4d4b09647ddc5be2229be"
GIT_SHA = "c078884443ee9d8865497436620bb899abe0b9e131acccfb2700ec1b40169c30"
GIT_COMPILE_SHA = "48715fe8357b7b63ec3745409d5e556a7a44cead7e970243694f3daf2524d5cb"
CORE_TIMEOUT_SHA = "99b1ebcb1e9dce1add23c5e540b269eea3b7691ac4805efe1ad0b5c387bd9b63"
RAW_SHA = "3bd9b092b342b728d59db3a92b4200d70b981ae3f9d047ad36fb55f3c0bb9f38"
BUILD_SHA = "07f8f7bcf9fdc3247b62126e479734890c733e00af8665a8465b0c8e966a7575"
SANDBOX_SHA = "e43829cedfb262e6f67321c8ed3063f5e808c738df458402363c99158ef29b86"
GIT_FAILURE_SHA = "7dc4493fae2e9342195b3172f196df0a1afd79102a5d769eb3402fe9737190c7"
KEY = ("p167-native-v1-Windows-rust1.95.0-msvc14.44.35207-sdk10.0.26100.0-"
       "24dab591cfe8c61f3960f109f1a67c7d3e57d9dc371fe8ed09042d35cc695c6d-42516370653-1")
# The early-only lane recovers cache bytes from a failed formal attempt.
# The embedded historical ZIP still owns the original six and 30 case receipts.
EARLY_RUN, EARLY_JOB, EARLY_ARTIFACT = 42524403118, 128864678848, 13636662801
EARLY_HEAD = "66001c11e20bd6420f71fec72b9200f1be93b7cc"
EARLY_CACHE_ID = 10205833743
EARLY_CACHE_VERSION = "e358c3bf48200b610baabba2ae9dd2f435e636e75a5db4d1dd1d9d28e60c6214"
EARLY_KEY = KEY.removesuffix(f"-{RUN}-1") + f"-{EARLY_RUN}-1"
EARLY_ZIP_SIZE = 838096342
EARLY_ZIP_SHA = "06265f3c096be43fb2781ea61a415ca335233678eaaa1502f838b45412d3da29"
FORMAL_PACKAGER = "87ef2981f4f56dc61f9ca3d62885d30a3da52c9705f13e8deb52b35a625d97f8"
EARLY_PACKAGER = "353097e2833dd45e79dcaeaf5f2d591faa0d74946eea53c803dbf944cc70a29b"
EARLY_REF = "refs/heads/codex/p167-hosted-ci-r626"
FORMAL_STEPS = {
    "Restore exact completed P167 producer and test intermediates": "success",
    "Restore verified six outputs and original sandbox18 and Git12 receipts": "success",
    "Warm exact Core compile on the same runner": "success",
    "Exact native Guardian and request capacity tests": "failure",
    "Normal clean-source assembly with all native receipts": "skipped",
    "Preserve new P167 intermediates for exact packaging-only recovery if needed": "success",
    "Upload scoped candidate and original evidence": "success",
}
FORMAL_RECORDS = (
    "inputs.json", "p167-retained-producer.json", "p167-dependency-checkout.json",
    "p167-core-normal-resource-admission.json", "p167-core-warmup.json",
    "native-p167-core-warmup.json", "native-p167-core-warmup.log",
    "native-p167-codex-core-compile.json", "native-p167-codex-core-compile.log",
    "native-p167-codex-core-case-02.json", "native-p167-codex-core-case-02.log",
    "extra_unit-failure.json",
)
LEAF = "codex-windows-sandbox-p167-libtest.exe"
GIT_LEAF = "codex-core-plugins-p167-libtest.exe"
ORIGINAL_SANDBOX = Path(rf"C:\cagp167-{PRODUCER_RUN}-1\delivery\unit") / LEAF
ORIGINAL_GIT = Path(rf"C:\cagp167-{GIT_RUN}-1\delivery\unit") / GIT_LEAF
RETAINED_UNITS = {
    "native-unit18.json": (SANDBOX_SHA, ORIGINAL_SANDBOX, "sandbox_receipt_sha256", "original_sandbox_executable"),
    "native-git-unit12.json": (GIT_SHA, ORIGINAL_GIT, "git_receipt_sha256", "original_git_executable"),
}

def sha(path):
    with path.open("rb") as f:
        return hashlib.file_digest(f, "sha256").hexdigest()

def original_archive(root):
    return root / "evidence" / f"same-runner-core-p167-{RUN}.zip"

def verify_zip(path):
    need(path.is_file() and path.stat().st_size == SIZE and sha(path) == ZIP_SHA,
         "Exact prior producer/18 ZIP required")

def early_draft():
    return os.environ.get("CI_P167_EARLY_DRAFT") == "1"


def formal_archive(root):
    return root / "evidence" / f"p167-native-windows-{EARLY_RUN}-1.zip"


def formal_identity():
    return {
        "run": EARLY_RUN, "job": EARLY_JOB, "attempt": 1, "head": EARLY_HEAD,
        "conclusion": "failure", "steps": FORMAL_STEPS, "test_cases_credited": 0,
        "artifact_id": EARLY_ARTIFACT, "artifact_size": EARLY_ZIP_SIZE,
        "artifact_sha256": EARLY_ZIP_SHA, "cache_id": EARLY_CACHE_ID,
        "cache_key": EARLY_KEY, "cache_version": EARLY_CACHE_VERSION, "cache_ref": EARLY_REF,
        "historical_cache_key": KEY, "formal_packager_sha256": FORMAL_PACKAGER,
        "early_packager_sha256": EARLY_PACKAGER,
    }


def verify_formal_metadata(run, job, artifact, caches):
    branch = EARLY_REF.removeprefix("refs/heads/")
    need(all(run.get(k) == v for k, v in {
        "id": EARLY_RUN, "head_sha": EARLY_HEAD, "head_branch": branch,
        "status": "completed", "conclusion": "failure", "run_attempt": 1,
        "path": ".github/workflows/p20-native-windows.yml", "event": "workflow_dispatch",
    }.items()), "Exact failed formal cache-producing run required")
    need(all(job.get(k) == v for k, v in {
        "id": EARLY_JOB, "run_id": EARLY_RUN, "head_sha": EARLY_HEAD,
        "status": "completed", "conclusion": "failure",
    }.items()), "Exact failed formal cache-producing job required")
    steps = [(row["name"], row["conclusion"]) for row in job["steps"]
             if row["name"] in FORMAL_STEPS]
    need(len(steps) == len(FORMAL_STEPS) and dict(steps) == FORMAL_STEPS,
         "Formal test failure, skipped assembly and successful cache save required")
    need(all(artifact.get(k) == v for k, v in {
        "id": EARLY_ARTIFACT, "name": f"p167-native-windows-{EARLY_RUN}-1",
        "size_in_bytes": EARLY_ZIP_SIZE, "expired": False, "digest": "sha256:" + EARLY_ZIP_SHA,
    }.items()) and all(artifact["workflow_run"].get(k) == v for k, v in {
        "id": EARLY_RUN, "head_sha": EARLY_HEAD, "head_branch": branch,
        "repository_id": 1442389092, "head_repository_id": 1442389092,
    }.items()), "Exact formal raw artifact identity required")
    rows = caches["actions_caches"]
    need(caches["total_count"] == len(rows) == 1 and all(rows[0].get(k) == v for k, v in {
        "id": EARLY_CACHE_ID, "key": EARLY_KEY, "version": EARLY_CACHE_VERSION, "ref": EARLY_REF,
    }.items()), "Exact restored cache identity required")


def verify_formal_zip(path):
    need(path.is_file() and path.stat().st_size == EARLY_ZIP_SIZE and sha(path) == EARLY_ZIP_SHA,
         "Exact failed formal raw ZIP required")


def download_formal(root):
    def api(endpoint):
        return json.loads(subprocess.check_output(
            ["gh", "api", f"repos/{previous.REPOSITORY}/actions/{endpoint}"], timeout=30))

    verify_formal_metadata(api(f"runs/{EARLY_RUN}"), api(f"jobs/{EARLY_JOB}"),
                           api(f"artifacts/{EARLY_ARTIFACT}"),
                           api("caches?" + urlencode({"key": EARLY_KEY, "ref": EARLY_REF})))
    out = formal_archive(root)
    part = out.with_suffix(".part")
    with part.open("xb") as f:
        subprocess.run(["gh", "api", f"repos/{previous.REPOSITORY}/actions/artifacts/{EARLY_ARTIFACT}/zip"],
                       stdout=f, timeout=300, check=True)
    verify_formal_zip(part)
    part.rename(out)
    old = original_archive(root)
    with zipfile.ZipFile(out) as z, z.open(f"evidence/{old.name}") as src, old.open("xb") as dst:
        need(z.getinfo(f"evidence/{old.name}").file_size == SIZE, "Historical ZIP size differs")
        shutil.copyfileobj(src, dst, 1024 * 1024)
    verify_zip(old)
    receipt = root / "evidence/early-formal-cache-identity.json"
    with receipt.open("x", encoding="utf-8") as f:
        json.dump(formal_identity(), f, indent=2)


def download(root):
    need(os.environ.get("CI_P167_TEST_RESUME") == "1"
         and os.environ.get("GITHUB_REPOSITORY", "").casefold() == previous.REPOSITORY.casefold(),
         "Exact test-resume repository required")
    if early_draft():
        download_formal(root)
        return
    out = original_archive(root)
    part = out.with_suffix(".part")
    with part.open("xb") as f:
        subprocess.run(["gh", "api", f"repos/{previous.REPOSITORY}/actions/artifacts/{ARTIFACT}/zip"],
                       stdout=f, timeout=120, check=True)
    verify_zip(part)
    part.rename(out)

def read_origin(path, evidence, work, cache, target, lock, source_sha, lock_sha, *, early=False):
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
        old_core = exact("original-native-p167-codex-core-compile.json", CORE_TIMEOUT_SHA)
        repeat_core = exact("repeat-native-p167-codex-core-compile.json", CORE_REPEAT_SHA)
        core = exact("native-p167-codex-core-compile.json", CORE_SAME_RUNNER_SHA)
        warm = exact("native-p167-core-warmup.json", CORE_WARM_OBSERVER_SHA)
        warm_proof = exact("p167-core-warmup.json", CORE_WARM_PROOF_SHA)
        retained = exact("p167-retained-producer.json", RETAINED_SHA)
        sandbox = exact("native-unit18.json", SANDBOX_SHA)
        git_tests = exact("native-git-unit12.json", GIT_SHA)
        deps = exact("p167-dependency-checkout.json")
        bind = exact("normal-source-binding.json")
        inputs = exact("inputs.json")
        current = json.loads((evidence / "inputs.json").read_bytes())
        fields = ("lock_sha256", "source_sha256", "normal_builder_sha256", "build_jobs")
        packagers = inputs["normal_packager_sha256"], current["normal_packager_sha256"]
        need(packagers == (FORMAL_PACKAGER, EARLY_PACKAGER) if early else packagers[0] == packagers[1],
             "Original producer or current packager hash differs")
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
             and retained["native_producer_rerun"] is False and retained["original_sandbox_reexecuted"] is False
             and retained["retained_git_run"] == GIT_RUN and retained["retained_git_artifact_sha256"] == GIT_ZIP_SHA
             and retained["git_receipt_sha256"] == GIT_SHA and retained["original_git_reexecuted"] is False,
             "Original six/18/Git12 retained producer chain differs")
        need(old_git["timed_out"] is True and old_git["passed"] is False
             and git["passed"] is True and git["returncode"] == 0 and git["timed_out"] is False
             and not git["unknown_live"] and not git["remaining_after_cleanup"]
             and git["fixed_timeout_seconds"] == 1800
             and (git["observer_passed"] is True or (git.get("qualified_compiler_telemetry_cleanup") or {}).get(
                  "kind") == "observed_MSVC_VCTIP_telemetry_cleanup_only")
             and git["log_sha256"] == previous.digest(z.read("evidence/native-git-unit-compile.log")),
             "Original qualified Git compiler gate differs")
        need(old_core["timed_out"] is True and old_core["passed"] is False
             and repeat_core["timed_out"] is True and repeat_core["passed"] is False
             and retained["repeat_core_compile_sha256"] == CORE_REPEAT_SHA
             and warm_proof["status"] == "warm_compile_timed_out_without_survivors"
             and warm_proof["cases_credited"] == 0 and warm_proof["observer_sha256"] == CORE_WARM_OBSERVER_SHA
             and warm_proof["command"] == warm["command"] == core["command"],
             "Original and same-runner Core lineage differs")
        for row, stem in ((core, "native-p167-codex-core-compile"), (warm, "native-p167-core-warmup")):
            need(row["timed_out"] is True and row["fixed_timeout_seconds"] == 1800 and row["passed"] is False
                 and not any(row.get(k) for k in ("unknown_live","remaining_after_cleanup","resource_guard",
                                                  "wrapper_resource_guard","cleanup_errors","observation_errors"))
                 and row["log_sha256"] == previous.digest(z.read(f"evidence/{stem}.log")),
                 "Original same-runner bounded Core evidence differs")
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
            "same_runner_core_run": RUN, "same_runner_core_commit": COMMIT,
            "same_runner_core_artifact_sha256": ZIP_SHA, "same_runner_core_compile_sha256": CORE_SAME_RUNNER_SHA,
            "same_runner_core_warm_observer_sha256": CORE_WARM_OBSERVER_SHA,
            "same_runner_core_warm_proof_sha256": CORE_WARM_PROOF_SHA}


def read_formal(root, restored, source_sha, lock_sha):
    receipt = root / "evidence/early-formal-cache-identity.json"
    need(json.loads(receipt.read_bytes()) == formal_identity(), "Formal cache identity receipt differs")
    path = formal_archive(root)
    verify_formal_zip(path)
    with zipfile.ZipFile(path) as z:
        records = {name: z.read(f"evidence/{name}") for name in FORMAL_RECORDS}
        inputs = json.loads(records["inputs.json"])
        current = json.loads((root / "evidence/inputs.json").read_bytes())
        retained = json.loads(records["p167-retained-producer.json"])
        failure = json.loads(records["extra_unit-failure.json"])
        failed_case = json.loads(records["native-p167-codex-core-case-02.json"])
        need(inputs["source_commit"] == EARLY_HEAD and inputs["source_sha256"] == source_sha
             and inputs["lock_sha256"] == lock_sha and inputs["build_jobs"] == 2
             and inputs["normal_packager_sha256"] == FORMAL_PACKAGER
             and current["normal_packager_sha256"] == EARLY_PACKAGER
             and inputs["normal_builder_sha256"] == current["normal_builder_sha256"]
             and retained == restored
             and previous.digest(z.read("evidence/normal-raw-manifest.json")) == RAW_SHA
             and previous.digest(z.read("evidence/normal-six-build.json")) == BUILD_SHA
             and failure["original_failure_preserved"] is True and failure["App_GUI_qualified"] is False
             and failure["error"].startswith("native-p167-codex-core-case-02 failed;")
             and failed_case["passed"] is False
             and failed_case["log_sha256"] == previous.digest(records["native-p167-codex-core-case-02.log"]),
             "Failed formal raw evidence or original six lineage differs")
    return {**formal_identity(), "records": {name: previous.digest(data) for name, data in records.items()}}


def admit_cache(root, lock, source_sha, lock_sha, cache, target):
    early = early_draft()
    key = EARLY_KEY if early else KEY
    need(os.environ.get("CI_P167_TEST_RESUME") == "1" and os.environ.get("CI_P167_CACHE_HIT") == "true"
         and os.environ.get("CI_P167_CACHE_KEY") == key, "Exact completed P167 cache required")
    work = original_work(root)
    need(work.parent != root and not work.parent.exists() and target.resolve(strict=True) == target
         and not target.is_junction() and cache.resolve(strict=True) == cache,
         "Fresh runner, original source path and physical cache required")
    restored = read_origin(original_archive(root), root / "evidence", work, cache, target, lock,
                           source_sha, lock_sha, early=early)
    if early:
        formal = read_formal(root, restored, source_sha, lock_sha)
        return {**restored, "cache_key": key, "failed_formal_cache": formal}
    return restored

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
                     "original-git_unit-failure.json", "original-native-p167-codex-core-compile.json",
                     "original-native-p167-codex-core-compile.log", "original-extra_unit-failure.json",
                     "repeat-native-p167-codex-core-compile.json", "repeat-native-p167-codex-core-compile.log",
                     "repeat-extra_unit-failure.json"):
            copy("evidence/" + name, evidence / name)
        for name in ("native-p167-codex-core-compile.json", "native-p167-codex-core-compile.log", "extra_unit-failure.json",
                     "native-p167-core-warmup.json", "native-p167-core-warmup.log", "p167-core-warmup.json"):
            copy("evidence/" + name, evidence / ("same-runner-" + name))
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
