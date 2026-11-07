"""Only assemble the exact six P21 native binaries after the already executed 18 and 12 tests."""
from pathlib import Path
import hashlib, json, os, re, shutil, subprocess, sys, zipfile

REPO = Path(__file__).resolve().parents[1]
ORIGIN_COMMIT = "73dba33e75c720193d9f64f31bfa0ceb89e4272b"
ORIGIN_RUN = 42361797405
ORIGIN_ARTIFACT = 13574335875
GIT_GATE_RUN = 42369712222
GIT_GATE_COMMIT = "4aab688eaa599d8eb8f7a5cf1164d9ede43c96c3"
ORIGIN_SHA = "9b136381a2d1cb957203de4740910e0109b22686969e4e41c9e7452dc5467e43"
ORIGIN_SIZE = 106230727
ORIGINAL_PACKAGER_SHA = "3ea29a1e23a66fc3aded1c24d4723853a0e238f15871cd84c544ef13797e399e"
REVIEWED_P21_PACKAGER_SHA = "e821b0e1a9672e737ad27863663c174a1c975735d06589839aaad7f1d8512dc5"
CACHE_KEY = "p21-native-v1-Windows-c784d939e4c5afbf5357c56313e14792d6e2939a4728fe77d80494d475a4b453-42361797405-1"
REPOSITORY = "0xZoharHuang/codex-app-deepseek-gateway"


def load_ci():
    import importlib.util
    spec = importlib.util.spec_from_file_location("cadsg_ci_p20", REPO / "scripts/ci/p20-windows.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    module.CANDIDATE, module.LOCK = "P21", module.P21_LOCK
    module.SOURCE, module.SOURCE_FILES = module.P21_SOURCE, module.P21_SOURCE_FILES
    return module


def require(value, reason):
    if not value:
        raise ValueError(reason)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def extract_exact(archive, name, path):
    with archive.open(name) as src, path.open("xb") as dst:
        shutil.copyfileobj(src, dst, 1024 * 1024)


def read_origin(ci, archive):
    names = archive.namelist()
    require(len(names) == len({name.casefold() for name in names}), "Duplicate original artifact member")
    origin = json.loads(archive.read("evidence/original-inputs.json"))
    inputs = json.loads(archive.read("evidence/inputs.json"))
    require(origin["source_commit"] == ORIGIN_COMMIT and inputs["source_commit"] == GIT_GATE_COMMIT
            and origin["source_sha256"] == inputs["source_sha256"] == ci.SOURCE
            and origin["lock_sha256"] == inputs["lock_sha256"] == ci.LOCK,
            "Original producer and completed Git gate source/lock provenance differs")
    require(origin["normal_builder_sha256"] == inputs["normal_builder_sha256"] == ci.sha(REPO / "scripts/build_native.py")
            and origin["normal_packager_sha256"] == inputs["normal_packager_sha256"] == ORIGINAL_PACKAGER_SHA
            and ci.sha(REPO / "scripts/package_native_release.py") == REVIEWED_P21_PACKAGER_SHA,
            "Original builder or exact reviewed new packaging admission differs")
    build = json.loads(archive.read("evidence/normal-six-build.json"))
    require(build["passed"] is True and build["returncode"] == 0 and build["timed_out"] is False
            and not any(build.get(k) for k in ("error", "resource_guard", "unknown_live", "remaining_after_cleanup")),
            "Original normal build was not qualified")
    require(sha(archive.read("evidence/normal-six-build.log")) == build["log_sha256"],
            "Original native build log differs")
    raw = archive.read("evidence/normal-raw-manifest.json")
    source = json.loads(archive.read("evidence/normal-source-binding.json"))
    require(source["normal_manifest_sha256"] == sha(raw) and source["source_sha256"] == ci.SOURCE
            and source["lock_sha256"] == ci.LOCK, "Original raw manifest differs")
    unit = json.loads(archive.read("evidence/native-unit18.json"))
    _, _, cases = ci.plan()
    require(unit["status"] == "P20_NATIVE_COMPONENT18_PASSED_NOT_APP_QUALIFIED"
            and unit["source_sha256"] == ci.SOURCE and unit["lock_sha256"] == ci.LOCK
            and [row["name"] for row in unit["cases"]] == cases, "Original exact native 18 gate differs")
    for i, row in enumerate(unit["cases"]):
        stem = f"evidence/native-case-{i:02}"
        log = archive.read(stem + ".log")
        text = log.decode("utf8").replace("\r\n", "\n")
        run = json.loads(archive.read(stem + ".json"))
        require(sha(log) == row["log_sha256"] == run["log_sha256"] and run["passed"] is True
                and run["returncode"] == 0 and not run["timed_out"]
                and text.count(f"test {row['name']} ... ok\n") == 1
                and text.count("test result: ok. 1 passed; 0 failed; 0 ignored;") == 1,
                "Original exact native test proof differs")
    libtest = "delivery/unit/codex-windows-sandbox-p21-libtest.exe"
    with archive.open(libtest) as f:
        require(hashlib.file_digest(f, "sha256").hexdigest() == unit["executable"]["sha256"],
                "Original qualified libtest bytes differ")
    git = json.loads(archive.read("evidence/native-git-unit12.json"))
    expected_git = [f"{module}::git_lifetime_windows_tests::{case}"
                    for module in ("startup_sync", "marketplace_upgrade::git")
                    for case in ci.P21_GIT_CASES]
    require(git["status"] == "P21_NATIVE_GIT12_PASSED_NOT_APP_QUALIFIED"
            and git["source_sha256"] == ci.SOURCE and git["lock_sha256"] == ci.LOCK
            and [row["name"] for row in git["cases"]] == expected_git
            and git["executable"]["profile"]["opt_level"] == "3",
            "Original twelve exact Git lifetime cases differ")
    for i, row in enumerate(git["cases"]):
        stem = f"evidence/native-git-case-{i:02}"
        log = archive.read(stem + ".log")
        text = log.decode("utf8").replace("\r\n", "\n")
        run = json.loads(archive.read(stem + ".json"))
        require(sha(log) == row["log_sha256"] == run["log_sha256"] and run["passed"] is True
                and run["returncode"] == 0 and not run["timed_out"]
                and text.count(f"test {row['name']} ... ok\n") == 1
                and text.count("test result: ok. 1 passed; 0 failed; 0 ignored;") == 1,
                "Original exact Git case proof differs")
    git_libtest = "delivery/unit/codex-core-plugins-p21-libtest.exe"
    with archive.open(git_libtest) as f:
        require(hashlib.file_digest(f, "sha256").hexdigest() == git["executable"]["sha256"],
                "Original completed Git libtest bytes differ")
    rejected = archive.read("evidence/normal-package.log")
    failed = json.loads(archive.read("evidence/normal-package.json"))
    require(failed["passed"] is False and failed["returncode"] == 1 and failed["log_sha256"] == sha(rejected)
            and b"Six-target source bundle must match an explicitly reviewed P16 lock" in rejected
            and b"reviewed P20 lock" in rejected and b"P21" not in rejected,
            "Original packaging admission failure differs")
    return raw, libtest, git_libtest


def download(ci, root, path):
    require(os.environ.get("GITHUB_REPOSITORY", "").casefold() == REPOSITORY.casefold(), "Exact repository required")
    require(bool(os.environ.get("GH_TOKEN")), "Read-only Actions download token is missing")
    with path.open("xb") as out:
        result = subprocess.run(["gh", "api", f"repos/{REPOSITORY}/actions/artifacts/{ORIGIN_ARTIFACT}/zip"],
                                stdout=out, stderr=subprocess.PIPE, timeout=120)
    require(result.returncode == 0 and path.stat().st_size == ORIGIN_SIZE and ci.sha(path) == ORIGIN_SHA,
            "Original Actions artifact download differs")


def resume(ci, root, path):
    require(os.environ.get("CI_P20_CACHE_HIT") == "true"
            and os.environ.get("CI_P20_CACHE_KEY") == CACHE_KEY, "Exact original cache restore required")
    require(path.stat().st_size == ORIGIN_SIZE and ci.sha(path) == ORIGIN_SHA,
            "Original Actions artifact SHA differs")
    require(os.environ.get("VCToolsVersion") == "14.44.35207"
            and os.environ.get("UCRTVersion") == "10.0.26100.0",
            "Exact original MSVC and SDK required")
    work = ci.work_path(root)
    require(Path(root.anchor) == Path(work.anchor) and not work.parent.exists(),
            "Original absolute work root must be absent on a fresh runner")
    ci.prepare(root)
    lock, builder, _ = ci.plan()
    cache, target = ci.cache_paths(root)
    with zipfile.ZipFile(path) as archive:
        raw, libtest, git_libtest = read_origin(ci, archive)
        manifest = json.loads(raw)
        require(manifest["target"] == str(target) and manifest["cargo_home"] == str(cache / "cargo")
                and manifest["source"] == str(work / "source")
                and target.resolve(strict=True) == target and not target.is_junction(),
                "Restored physical target and original source path required")
        producer = root / "native-producer"
        producer.mkdir()
        rows = manifest["binaries"]
        require(len(rows) == len(ci.BINS) and {row["file"] for row in rows} == ci.BINS,
                "Original manifest lacks the exact six outputs")
        for row in rows:
            original = target / "release" / row["file"]
            require(original.is_file() and original.stat().st_size == row["size"]
                    and ci.sha(original) == row["sha256"], f"Restored native byte mismatch: {row['file']}")
            shutil.copyfile(original, producer / row["file"])
        (producer / "manifest.json").write_bytes(raw)
        ci.verify_normal_outputs(root, manifest)
        for name in ("normal-six-build.json", "normal-six-build.log", "build-report.json",
                     "normal-source-binding.json", "normal-raw-manifest.json", "native-unit18.json",
                     "exact-p20-cache-admission.json", "native-git-unit12.json",
                     "native-git-unit-compile.json", "native-git-unit-compile.log"):
            extract_exact(archive, "evidence/" + name, root / "evidence" / name)
        for i in range(18):
            for suffix in (".log", ".json"):
                name = f"native-case-{i:02}" + suffix
                extract_exact(archive, "evidence/" + name, root / "evidence" / name)
        for i in range(12):
            for suffix in (".log", ".json"):
                name = f"native-git-case-{i:02}" + suffix
                extract_exact(archive, "evidence/" + name, root / "evidence" / name)
        for name in ("original-inputs.json", "original-git-unit-failure.json",
                     "original-native-git-unit-compile.json", "original-native-git-unit-compile.log"):
            extract_exact(archive, "evidence/" + name, root / "evidence" / name)
        for name, member in (("completed-git-gate-inputs.json", "inputs.json"),
                             ("original-package-rejection.json", "normal-package.json"),
                             ("original-package-rejection.log", "normal-package.log")):
            extract_exact(archive, "evidence/" + member, root / "evidence" / name)
        unit_dir = root / "delivery/unit"
        unit_dir.mkdir()
        for member in (libtest, git_libtest):
            extract_exact(archive, member, unit_dir / Path(member).name)
    source = work / "source"
    builder.prepare_source(root / "inputs/upstream.tar.gz", source, lock, shutil.which("git.exe"))
    before = builder.source_state(source)
    require(before["sha256"] == ci.SOURCE and before["count"] == lock["prepared_source_files"],
            "Resumed prepared source differs")
    ci.verify_normal_outputs(root, manifest)
    ci.package(root)
    ci.verify_normal_outputs(root, manifest)
    chain = {"status": "EXACT_HOSTED_P21_PRODUCER_GIT_TESTED_AND_REASSEMBLED",
             "original_commit": ORIGIN_COMMIT, "original_run": ORIGIN_RUN,
             "completed_git_gate_commit": GIT_GATE_COMMIT, "completed_git_gate_run": GIT_GATE_RUN,
             "completed_git_gate_artifact_id": ORIGIN_ARTIFACT, "completed_git_gate_artifact_sha256": ORIGIN_SHA,
             "exact_cache_key": CACHE_KEY, "source_sha256": ci.SOURCE, "lock_sha256": ci.LOCK,
             "original_raw_manifest_sha256": sha(raw),
             "restored_six": {row["file"]: row["sha256"] for row in rows},
             "original_native_unit18_sha256": ci.sha(root / "evidence/native-unit18.json"),
             "original_rejected_git_compile_sha256": ci.sha(root / "evidence/original-native-git-unit-compile.json"),
             "original_packaging_rejection_sha256": ci.sha(root / "evidence/original-package-rejection.log"),
             "completed_native_git_unit12_sha256": ci.sha(root / "evidence/native-git-unit12.json"),
             "metadata_fetch_observation_sha256": ci.sha(root / "evidence/stage-locked-windows-metadata.json"),
             "clean_assembly_candidate_sha256": ci.sha(root / "delivery/candidate.json"),
             "native_six_rebuilt": False, "original_unit_tests_reexecuted": False,
             "git_libtest_recompiled_or_reexecuted": False,
             "App_GUI_qualified": False}
    ci.save(root / "delivery/assembly-resume-chain.json", chain)


def main():
    require(len(sys.argv) == 3 and sys.argv[1] in {"download", "resume"}, "Expected phase and hosted root")
    ci = load_ci()
    root = ci.hosted_root(Path(sys.argv[2]))
    require(os.environ.get("GITHUB_REF") == "refs/heads/codex/p20-hosted-ci-r570"
            and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch",
            "Manual continuation on the original exact cache branch required")
    path = root / "original-producer-artifact.zip"
    if sys.argv[1] == "download":
        download(ci, root, path)
    else:
        resume(ci, root, path)


if __name__ == "__main__":
    main()
