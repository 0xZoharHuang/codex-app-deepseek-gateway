"""Admit only the exact interrupted hosted P167 cache as build input."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

RUN = "42458262916"
RUN_COMMIT = "c4363e98ca20de65cce47c8281b7bffa77e46d08"
CACHE_KEY = (
    "p167-native-v1-Windows-rust1.95.0-msvc14.44.35207-sdk10.0.26100.0-"
    "24dab591cfe8c61f3960f109f1a67c7d3e57d9dc371fe8ed09042d35cc695c6d-42458262916-1"
)
REPOSITORY = "0xZoharHuang/codex-app-deepseek-gateway"
ARTIFACT_ID = 13611939691
ARTIFACT_SIZE = 6270512
ARTIFACT_SHA256 = "6a252820e4836693a590a71d4ced9d5a231092681183135776fb4f12c196130d"
OBSERVER_SHA256 = "14527ab21fb931d62a2dfe2ea28ddaedd7eb6a011c921ba3334faee36e3e7215"
CHECKOUT_SHA256 = "6542cc8d4c02df67b5f8c7b523beef269625178313893bc163f967c2d352701c"


def require(condition, message):
    if not condition:
        raise ValueError(message)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def original_work(root):
    return Path(root.anchor) / f"cagp167-{RUN}-1" / "work"


def original_v8_directory(work):
    return work.parent / "inputs/rusty-v8"


def stage_v8_inputs(root, builder, target, restored):
    work = original_work(root)
    directory = original_v8_directory(work)
    require(restored.get("original_v8_directory") == str(directory),
            "Resume receipt does not bind the original V8 directory")
    source = root / "dependency-preflight/source"
    downloaded = root / "inputs/rusty-v8"
    verified = builder.verified_rusty_v8_inputs(source, downloaded, target)
    work.parent.mkdir(exist_ok=False)
    directory.mkdir(parents=True)
    for row in verified["files"]:
        original = Path(row["file"])
        with original.open("rb") as src, (directory / original.name).open("xb") as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
    copied = builder.verified_rusty_v8_inputs(source, directory, target)
    def identities(record):
        return {(Path(row["file"]).name, row["size"], row["sha256"]) for row in record["files"]}
    require(identities(copied) == identities(verified), "Restaged V8 input identity differs")
    return {"directory": str(directory), "files": copied["files"], "environment": copied["environment"]}


def original_archive(root):
    return root / "evidence" / f"original-p167-{RUN}.zip"


def download(root):
    require(os.environ.get("CI_P167_RESUME") == "1"
            and os.environ.get("GITHUB_REPOSITORY", "").casefold() == REPOSITORY.casefold(),
            "Exact reviewed hosted artifact repository and run required")
    archive = original_archive(root)
    partial = archive.with_suffix(".part")
    with partial.open("xb") as stream:
        subprocess.run(["gh", "api", f"repos/{REPOSITORY}/actions/artifacts/{ARTIFACT_ID}/zip"],
                       check=True, stdout=stream, timeout=90)
    verify_archive(partial)
    partial.rename(archive)


def verify_archive(archive):
    require(archive.is_file() and archive.stat().st_size == ARTIFACT_SIZE
            and digest(archive.read_bytes()) == ARTIFACT_SHA256,
            "Exact original failed-run artifact bytes required")


def admit_cache(root, lock, source_sha, lock_sha, cache, target):
    require(os.environ.get("CI_P167_RESUME") == "1"
            and os.environ.get("CI_P167_CACHE_HIT") == "true"
            and os.environ.get("CI_P167_CACHE_KEY") == CACHE_KEY,
            "Exact original P167 cache restore required, without fallback")
    work = original_work(root)
    require(work.parent != root and not work.parent.exists(),
            "Original source path must be absent on the disposable resume runner")
    require(cache.name == "c167" and cache.resolve(strict=True) == cache
            and target.resolve(strict=True) == target
            and (target / "release/.fingerprint").is_dir()
            and any((target / "release/.fingerprint").iterdir()),
            "Exact short cache and nonempty Cargo intermediates required")
    return verify_original_evidence(original_archive(root), root / "evidence",
                                    work, cache, target, lock, source_sha, lock_sha)


def verify_original_evidence(archive, current_evidence, work, cache, target,
                             lock, source_sha, lock_sha):
    verify_archive(archive)
    with zipfile.ZipFile(archive) as z:
        raw_observer = z.read("evidence/normal-six-build.json")
        raw_checkout = z.read("evidence/p167-dependency-checkout.json")
        raw_log = z.read("evidence/normal-six-build.log")
        toolchain = json.loads(z.read("evidence/toolchain.json"))
        inputs = json.loads(z.read("evidence/inputs.json"))
    current_inputs = json.loads((current_evidence / "inputs.json").read_bytes())
    require(inputs["source_commit"] == RUN_COMMIT and inputs["build_jobs"] == 2
            and all(current_inputs[key] == inputs[key] for key in (
                "lock_sha256", "source_sha256", "normal_builder_sha256", "normal_packager_sha256", "build_jobs")),
            "Original producer commit, source or product build recipe differs")
    require(digest(raw_observer) == OBSERVER_SHA256 and digest(raw_checkout) == CHECKOUT_SHA256,
            "Exact timeout and dependency observer receipts required")
    observer = json.loads(raw_observer)
    checkout = json.loads(raw_checkout)
    require(observer["timed_out"] is True and observer["passed"] is False
            and observer["fixed_timeout_seconds"] == 7200
            and observer["log_sha256"] == digest(raw_log)
            and observer["cleanup_errors"] == ["PermissionError"]
            and not any(observer.get(k) for k in ("resource_guard", "wrapper_resource_guard",
                "remaining_after_cleanup", "unknown_live", "observation_errors")),
            "Original pinned timeout, process readback and resource evidence differ")
    command = observer["command"]
    v8_directory = original_v8_directory(work)
    for flag, value in (("--work-dir", str(work)), ("--cache-dir", str(cache)),
                        ("--target-dir", str(target)), ("--jobs", "2"),
                        ("--rusty-v8-dir", str(v8_directory))):
        require(command.count(flag) == 1 and command[command.index(flag) + 1] == value,
                f"Original producer command differs at {flag}")
    current_toolchain = json.loads((current_evidence / "toolchain.json").read_bytes())
    require(all(current_toolchain[key] == toolchain[key] for key in (
        "rustc", "cargo", "VCToolsVersion", "WindowsSDKVersion", "UCRTVersion", "compiler", "linker", "ImageOS")),
        "Original measured native toolchain differs")
    require(checkout["status"] == "LOCKED_FETCH_CHECKOUT_PROVEN"
            and checkout["source_sha256"] == source_sha and checkout["build_lock_sha256"] == lock_sha
            and checkout["cargo_lock_sha256"] == lock["cargo_lock_sha256"]
            and checkout["target"] == lock["target"] and checkout["cargo_home"] == str(cache / "cargo"),
            "Original locked dependency checkout does not match this producer")
    return {"original_run": RUN, "original_artifact_sha256": ARTIFACT_SHA256,
            "original_observer_sha256": OBSERVER_SHA256, "original_checkout_sha256": CHECKOUT_SHA256,
            "original_absolute_work": str(work), "original_v8_directory": str(v8_directory),
            "cache_key": CACHE_KEY, "checkout": checkout["checkout"],
            "original_producer_passed": False, "original_timed_out": True,
            "original_cleanup_errors": observer["cleanup_errors"], "cache_is_build_input_only": True}
