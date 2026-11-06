"""Build-only Windows CI adapter. No gateway, App, credential or runtime imports."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, os, re, shutil, subprocess, sys, tarfile, time, tomllib, urllib.request
from pathlib import Path, PurePosixPath

REPO = Path(__file__).resolve().parents[2]
LOCK = "3736e573e634180a1443b0c3e2cdbbf3d28304cf17d76fd3752aab344356b44c"
SOURCE = "37efcaa4a32880ecb16f3420f4efb6a39e9c82fb407a169ec67e0d74991763da"
CDS_CI = "17917162b707574ce86765149ab8b06acbdc3c96"
OBSERVER_FILES = {
    "ci/qualify_native.py": "13a771b041aaadcdd7d37ba129bf9e82f2dff73b57abfbbc5f0812ac17c62deb",
    "src/codex_deepseek/processes.py": "3ca59198e616ef7c7929b6a54645219e61c278da3b541e4d8872d6bdb95968f3",
    "src/codex_deepseek/__init__.py": "1569b137124cf6975e3530cdfd62ff6e7772697370061ad5462891ec7cebb127",
}
FLOOR = 4 * 1024**3
BINS = {"codex.exe", "codex-command-runner.exe", "codex-windows-sandbox-setup.exe",
        "codex-windows-managed-deny-probe.exe", "codex-windows-sandbox-service.exe",
        "codex-code-mode-host.exe"}

def require(ok, message):
    if not ok:
        raise ValueError(message)

def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(part)
    return value.hexdigest()

def load(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))

def save(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf8") as stream:
        json.dump(value, stream, indent=2)
        stream.write("\n")

def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

def plan():
    lock_path = REPO / "native/build-lock.O165-P20-source.json"
    require(sha(lock_path) == LOCK, "Exact P20 lock bytes required")
    lock = load(lock_path)
    require(lock["prepared_source_sha256"] == SOURCE and lock["prepared_source_files"] == 9529,
            "Exact reviewed P20 source required")
    require(lock["rust_version"] == "1.99.1" and set(lock["binaries"]) == BINS
            and len(lock["binaries"]) == 6 and lock["package_mode"] == "cadsg-native-msix",
            "Pinned toolchain and six-target source bundle required")
    builder = load_module("cadsg_ci_normal_native", REPO / "scripts/build_native.py")
    require(builder.PROFILE == lock["release_environment"], "Release profile differs from reviewed lock")
    cases = load(REPO / "scripts/ci/p20-native-unit-cases.json")
    require(len(cases) == len(set(cases)) == 18 and all(isinstance(x, str) for x in cases),
            "Exact eighteen unique component cases required")
    return lock, builder, cases

def hosted_context(environment, platform_name):
    require(platform_name == "nt", "Windows only")
    required = {"GITHUB_ACTIONS": "true", "RUNNER_ENVIRONMENT": "github-hosted",
                "RUNNER_OS": "Windows", "RUNNER_ARCH": "X64",
                "CI_REPOSITORY_VISIBILITY": "public"}
    require(all(environment.get(k) == v for k, v in required.items()),
            "Only a disposable public GitHub-hosted Windows x64 runner is admitted")
    require(all(re.fullmatch(r"[0-9]+", environment.get(k, "")) for k in
                ("GITHUB_RUN_ID", "GITHUB_RUN_ATTEMPT")), "Missing canonical run identity")
    require(environment.get("GITHUB_REF", "").startswith("refs/heads/codex/p20-hosted-ci-"),
            "Candidate branch only; no main/tag stable publication")
    return environment["GITHUB_RUN_ID"], environment["GITHUB_RUN_ATTEMPT"]

def hosted_root(path):
    run_id, attempt = hosted_context(os.environ, os.name)
    expected = Path(os.environ["SystemDrive"] + "\\") / f"cagp20-{run_id}-{attempt}"
    require(path.absolute() == expected.absolute(), "Unexpected hosted CI root")
    require(path.is_dir() and (path / "evidence/runner-space.json").is_file(),
            "Run hosted-only preflight first")
    return path

def download(url, destination, expected):
    destination = Path(destination)
    require(url.startswith("https://") and not destination.exists(), "Fresh HTTPS input required")
    temporary = destination.with_name(destination.name + ".part")
    destination.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(url, headers={"User-Agent": "CADSG-pinned-native-CI"})
    with urllib.request.urlopen(request, timeout=90) as response, temporary.open("xb") as stream:
        require(response.url.startswith("https://"), "HTTPS redirect required")
        shutil.copyfileobj(response, stream, 1024 * 1024)
    actual = sha(temporary)
    require(actual == expected, "Pinned public input SHA256 mismatch")
    temporary.rename(destination)
    return {"url": url, "path": str(destination), "sha256": actual, "bytes": destination.stat().st_size}

def checksum_lines(data):
    result = {}
    for line in data.decode("utf8").splitlines():
        if not line.strip():
            continue
        fields = line.split()
        require(len(fields) == 2 and re.fullmatch(r"[a-f0-9]{64}", fields[0])
                and PurePosixPath(fields[1]).name == fields[1] and fields[1] not in result,
                "Malformed or duplicate public checksum")
        result[fields[1]] = fields[0]
    return result

def prepare(root):
    lock, builder, cases = plan()
    for name in ("inputs", "delivery"):
        (root / name).mkdir(exist_ok=False)
    cache, target = cache_paths(root)
    for path in (cache, cache / "cargo", target):
        require(path.resolve() == path.absolute() and not path.is_symlink(), "Build cache redirected")
        path.mkdir(parents=True, exist_ok=True)
    require(shutil.disk_usage(root).free >= 24 * 1024**3, "Twenty-four GiB disk admission required")
    commit = lock["upstream_commit"]
    archive = root / "inputs/upstream.tar.gz"
    records = [download(f"https://codeload.github.com/openai/codex/tar.gz/{commit}",
                        archive, lock["archive_sha256"])]
    prefix = f"codex-{commit}/"
    with tarfile.open(archive) as stream:
        def member(name):
            entry = stream.getmember(prefix + name)
            require(entry.isfile(), "Public pinned archive member must be regular")
            return stream.extractfile(entry).read()
        cargo = tomllib.loads(member("codex-rs/Cargo.lock").decode())
        versions = {x["version"] for x in cargo["package"] if x["name"] == "v8"}
        require(len(versions) == 1, "Exactly one V8 crate version required")
        version = versions.pop()
        trusted = checksum_lines(member(
            f"third_party/v8/rusty_v8_{version.replace('.', '_')}_release_manifests.sha256"))
    v8 = root / "inputs/rusty-v8"
    v8.mkdir()
    suffix = "ptrcomp_sandbox_release_x86_64-pc-windows-msvc"
    manifest_name = f"rusty_v8_{suffix}.sha256"
    base = f"https://github.com/openai/codex/releases/download/rusty-v8-v{version}"
    records.append(download(f"{base}/{manifest_name}", v8 / manifest_name, trusted[manifest_name]))
    pair = checksum_lines((v8 / manifest_name).read_bytes())
    require(set(pair) == {f"rusty_v8_{suffix}.lib.gz", f"src_binding_{suffix}.rs"},
            "Only the official sandbox V8 archive and binding are admitted")
    for name, digest in pair.items():
        records.append(download(f"{base}/{name}", v8 / name, digest))
    observer = root / "inputs/build-observer"
    for name, digest in OBSERVER_FILES.items():
        records.append(download(
            f"https://raw.githubusercontent.com/0xZoharHuang/codex-deepseek-runtime/{CDS_CI}/{name}",
            observer / name, digest))
    toolchain = Path(os.environ["CI_RUST_TOOLCHAIN_BIN"])
    rust = subprocess.check_output([str(toolchain / "rustc.exe"), "--version", "--verbose"], text=True)
    cargo_version = subprocess.check_output([str(toolchain / "cargo.exe"), "--version"], text=True)
    require(rust.startswith("rustc 1.99.1 ") and cargo_version.startswith("cargo 1.99.1 "),
            "Exact Rust/Cargo 1.99.1, no version fallback")
    import psutil
    require(psutil.cpu_count() >= 4 and psutil.virtual_memory().total >= 15_000_000_000,
            "Standard public four-CPU/sixteen-GB runner required")
    require(psutil.virtual_memory().available >= 8 * 1024**3,
            "Eight-GiB initial available memory admission required")
    save(root / "evidence/inputs.json", {
        "kind": "public-pinned-build-only-inputs", "source_commit": subprocess.check_output(
            ["git", "-C", str(REPO), "rev-parse", "HEAD"], text=True).strip(),
        "lock_sha256": LOCK, "source_sha256": SOURCE, "inputs": records,
        "normal_builder_sha256": sha(REPO / "scripts/build_native.py"),
        "normal_packager_sha256": sha(REPO / "scripts/package_native_release.py"),
        "CDS_observer_only_commit": CDS_CI, "observer_runtime_imported": False,
        "rustc": rust, "cargo": cargo_version,
        "performance_cache": {"root": str(cache), "target": str(target), "authoritative_build_evidence": False},
        "build_jobs": build_jobs(),
        "initial_memory": psutil.virtual_memory()._asdict(), "unit_cases": cases,
        "App_GUI_qualified": False, "stable_publication": False})

def observe(command, root, cwd, environment, name, timeout):
    """Delegate process ownership/retirement to the pinned existing CI helper."""
    import psutil
    observer = root / "inputs/build-observer"
    for name_, expected in OBSERVER_FILES.items():
        require(sha(observer / name_) == expected, "Build-only observer changed")
    sys.path[:0] = [str(observer), str(observer / "src")]
    module = load_module("cadsg_ci_lifecycle_observer", observer / "ci/qualify_native.py")
    original = module._observe_process_family
    minimum = psutil.virtual_memory().available
    guard = None
    def guarded(*args, **kwargs):
        nonlocal minimum, guard
        original(*args, **kwargs)
        available = psutil.virtual_memory().available
        minimum = min(minimum, available)
        if available < FLOOR:
            guard = {"available_memory_bytes": available, "minimum_bytes": FLOOR}
            raise MemoryError("Original four-GiB available-memory floor crossed")
    module._observe_process_family = guarded
    log_path = root / f"evidence/{name}.log"
    with log_path.open("xb") as log:
        result = module.run_observed(command, cwd=cwd, env=environment, log=log, timeout=timeout)
    # Keep the pinned observer's original guard/minimum fields intact.
    result["observer_passed"] = result["passed"]
    result["wrapper_resource_guard"] = guard
    result["wrapper_minimum_available_memory_bytes"] = minimum
    result.setdefault("resource_guard", guard)
    result.setdefault("minimum_available_memory_bytes", minimum)
    result.update(command=command, fixed_timeout_seconds=timeout,
                  log_sha256=sha(log_path), build_controller_only=True)
    result["passed"] = bool(result["observer_passed"] and
                            result.get("resource_guard") is None and guard is None)
    save(root / f"evidence/{name}.json", result)
    require(result["passed"], f"{name} failed; preserve its original lifecycle evidence")
    return result

def clean_environment(environment=None):
    result = dict(os.environ if environment is None else environment)
    for key in list(result):
        if key.startswith(("CODEX_", "CDS_", "OPENAI_", "OPENROUTER_", "ANTHROPIC_",
                           "GH_", "GITHUB_", "ACTIONS_", "OTEL_")):
            result.pop(key)
    result["OTEL_SDK_DISABLED"] = "true"
    result["VSCMD_SKIP_SENDTELEMETRY"] = "1"
    return result


def cache_paths(root):
    # Source, build-owner records, final outputs and evidence stay fresh per run.
    cache = Path(root.anchor) / "cagp20-native-cache-v1"
    return cache, cache / "target"

def build_jobs(environment=None):
    value = (os.environ if environment is None else environment).get("CI_P20_BUILD_JOBS", "2")
    require(value in {"1", "2"}, "Only one or two native build jobs are allowed")
    return int(value)

def verify_normal_outputs(root, manifest):
    require(manifest["source_sha256"] == SOURCE and manifest["build_lock_sha256"] == LOCK
            and manifest["status"] == "built-not-app-qualified",
            "Normal producer source or status differs")
    rows = manifest["binaries"]
    require(len(rows) == 6 and {row["file"] for row in rows} == BINS,
            "All six raw same-source binaries required")
    for row in rows:
        require(sha(root / "native-producer" / row["file"]) == row["sha256"],
                "Normal native bytes differ")


def build(root):
    lock, builder, cases = plan()
    require((root / "evidence/inputs.json").is_file(), "Verified inputs required")
    require(not (root / "work").exists() and not (root / "native-producer").exists(),
            "A fresh owned build is required; never overwrite a previous candidate")
    toolchain = Path(os.environ["CI_RUST_TOOLCHAIN_BIN"])
    cache, target = cache_paths(root); jobs = build_jobs()
    command = [sys.executable, "-I", "-B", str(REPO / "scripts/build_native.py"),
               "--archive", str(root / "inputs/upstream.tar.gz"),
               "--work-dir", str(root / "work"), "--toolchain-bin", str(toolchain),
               "--cache-dir", str(cache), "--target-dir", str(target),
               "--build-lock", str(REPO / "native/build-lock.O165-P20-source.json"),
               "--jobs", str(jobs), "--rusty-v8-dir", str(root / "inputs/rusty-v8"),
               "--output", str(root / "native-producer")]
    observe(command, root, REPO, clean_environment(), "normal-six-build", 7200)
    report = load(root / "work/build-report.json")
    require(report["status"] == "passed" and report["build_lock_sha256"] == LOCK
            and report["effective_build_jobs"] == jobs, "Normal producer did not pass")
    manifest = load(root / "native-producer/manifest.json")
    verify_normal_outputs(root, manifest)
    for name in ("build-report.json", "source-manifest.json", "build-owner.json"):
        shutil.copyfile(root / "work" / name, root / "evidence" / name)
    save(root / "evidence/normal-source-binding.json", {
        "normal_manifest_sha256": sha(root / "native-producer/manifest.json"),
        "source_sha256": SOURCE, "lock_sha256": LOCK, "native_build_executed": True,
        "App_GUI_qualified": False})

def unit(root):
    lock, builder, cases = plan()
    require(load(root / "evidence/normal-six-build.json")["passed"], "Normal six-target producer required")
    toolchain = Path(os.environ["CI_RUST_TOOLCHAIN_BIN"])
    cache, target = cache_paths(root); jobs = build_jobs()
    environment = clean_environment(builder.build_environment(
        root / "work", toolchain, cache, target_dir=target))
    environment["CARGO_BUILD_JOBS"] = str(jobs)
    environment.update(builder.verified_rusty_v8_inputs(
        root / "work/source", root / "inputs/rusty-v8", lock["target"])["environment"])
    command = [str(toolchain / "cargo.exe"), "test", "--locked", "--release", "--jobs", str(jobs),
               "-p", "codex-windows-sandbox", "--lib", "--no-run",
               "--message-format=json-render-diagnostics"]
    observe(command, root, root / "work/source/codex-rs", environment, "native-unit-compile", 1800)
    artifacts, finished = [], []
    for line in (root / "evidence/native-unit-compile.log").read_text(
            encoding="utf8", errors="replace").splitlines():
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("reason") == "compiler-artifact" and message.get("executable") and \
                message.get("target", {}).get("name") == "codex_windows_sandbox" and \
                message.get("profile", {}).get("test") is True:
            artifacts.append(message)
        if message.get("reason") == "build-finished":
            finished.append(message.get("success"))
    require(len(artifacts) == 1 and finished == [True], "Unique genuine native libtest artifact required")
    executable = Path(artifacts[0]["executable"])
    require(executable.is_relative_to(target) and executable.is_file(), "Unit artifact outside target")
    sealed = root / "delivery/unit/codex-windows-sandbox-p20-libtest.exe"
    sealed.parent.mkdir(parents=True)
    shutil.copyfile(executable, sealed)
    require(sha(sealed) == sha(executable), "Sealed native test copy differs")
    observed = []
    for index, case in enumerate(cases):
        name = f"native-case-{index:02}"
        observe([str(sealed), "--exact", case, "--show-output", "--test-threads=1"],
                root, root / "work/source/codex-rs", environment, name, 180)
        text = (root / f"evidence/{name}.log").read_text(encoding="utf8", errors="replace")
        require(len(re.findall(r"(?m)^test " + re.escape(case) + r" \.\.\. ok$", text)) == 1
                and len(re.findall(r"(?m)^test result: ok\. 1 passed; 0 failed; 0 ignored;", text)) == 1,
                "Exact native test must actually execute once; no filtered-out pass")
        observed.append({"name": case, "log_sha256": sha(root / f"evidence/{name}.log")})
    require(builder.source_state(root / "work/source")["sha256"] == SOURCE,
            "Native source changed during unit qualification")
    manifest = load(root / "native-producer/manifest.json")
    for entry in manifest["outputs"]:
        require(sha(root / "native-producer" / entry["name"]) == entry["sha256"],
                "Native units modified a production output")
    save(root / "evidence/native-unit18.json", {
        "status": "P20_NATIVE_COMPONENT18_PASSED_NOT_APP_QUALIFIED",
        "cases": observed, "source_sha256": SOURCE, "lock_sha256": LOCK,
        "executable": {"path": str(sealed), "sha256": sha(sealed),
                       "profile": artifacts[0]["profile"]}, "App_GUI_qualified": False})

def package(root):
    plan()
    require(load(root / "evidence/normal-six-build.json")["passed"]
            and load(root / "evidence/native-unit18.json")["status"] ==
            "P20_NATIVE_COMPONENT18_PASSED_NOT_APP_QUALIFIED", "Build and exact unit gates required")
    toolchain = Path(os.environ["CI_RUST_TOOLCHAIN_BIN"])
    manifest = root / "native-producer/manifest.json"
    command = [sys.executable, "-I", "-B", str(REPO / "scripts/package_native_release.py"),
               "--source-root", str(root / "work/source"),
               "--native-manifest", str(manifest), "--native-manifest-sha256", sha(manifest),
               "--build-lock", str(REPO / "native/build-lock.O165-P20-source.json"),
               "--cargo", str(toolchain / "cargo.exe"), "--cargo-home", str(cache_paths(root)[0] / "cargo"),
               "--git", shutil.which("git.exe"), "--output", str(root / "assembled"),
               "--evidence-dir", str(root / "evidence/normal-assembly")]
    observe(command, root, REPO, clean_environment(), "normal-package", 1800)
    report = load(root / "evidence/normal-assembly/assembly-result.json")
    require(report["raw_binary_count"] == 6 and report["kind"] == "clean_source_assembly"
            and report["original_source_and_receipts_unchanged"] and report["no_compile_or_native_execution"],
            "Normal independent clean-source assembly required")
    archive = Path(report["archive"])
    require(archive.is_file() and archive == root / "assembled.tar.gz", "Unexpected native archive")
    shutil.copyfile(archive, root / "delivery" / archive.name)
    shutil.copyfile(root / "assembled/manifest.json", root / "delivery/manifest.json")
    save(root / "delivery/candidate.json", {
        "status": "P20_NORMAL_SIX_AND_COMPONENT18_ASSEMBLED_NOT_APP_QUALIFIED",
        "source_sha256": SOURCE, "lock_sha256": LOCK,
        "archive": archive.name, "archive_sha256": sha(archive),
        "raw_manifest_sha256": sha(root / "delivery/manifest.json"),
        "normal_build_receipt_sha256": sha(root / "evidence/normal-six-build.json"),
        "normal_assembly_receipt_sha256": sha(root / "evidence/normal-assembly/assembly-result.json"),
        "native_unit18_sha256": sha(root / "evidence/native-unit18.json"),
        "App_GUI_qualified": False, "public_stable_release": False,
        "required_next": "Windows Limited Session3 helper/network migration and real official App acceptance"})

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("check-only", "prepare", "build", "unit", "package"))
    parser.add_argument("--root", type=Path)
    args = parser.parse_args()
    if args.mode == "check-only":
        lock, _, cases = plan()
        print(json.dumps({"source": SOURCE, "lock": LOCK, "binaries": lock["binaries"],
                          "cases": len(cases), "host_changes": False, "native_execution": False}))
        return 0
    root = hosted_root(args.root)
    try:
        globals()[args.mode](root)
    except BaseException as error:
        import traceback
        save(root / f"evidence/{args.mode}-failure.json", {
            "error_type": type(error).__name__, "error": str(error),
            "traceback": traceback.format_exc(), "App_GUI_qualified": False,
            "original_failure_preserved": True})
        raise
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
