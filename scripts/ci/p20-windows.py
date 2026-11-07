"""Build-only Windows CI adapter. No gateway, App, credential or runtime imports."""
from __future__ import annotations
import argparse, hashlib, importlib.util, json, os, re, shutil, subprocess, sys, tarfile, time, tomllib, urllib.request
from pathlib import Path, PurePosixPath

REPO = Path(__file__).resolve().parents[2]
LOCK = "3736e573e634180a1443b0c3e2cdbbf3d28304cf17d76fd3752aab344356b44c"
SOURCE = "37efcaa4a32880ecb16f3420f4efb6a39e9c82fb407a169ec67e0d74991763da"
CANDIDATE = "P20"
SOURCE_FILES = 9529
P21_LOCK = "1a80f6e1e4fdb2f5b255a416f70350b5fd764493322bc463137381900374cdab"
P21_SOURCE = "5b39fc728fcb025715fc5b6467c406b7190e7fbabd8b1342f86cc754b4458c1c"
P21_SOURCE_FILES = 9531
P20_WARM_CACHE_KEY = "p20-native-v1-Windows-rust1.99.1-msvc14.44.35207-sdk10.0.26100.0-2259036772c5d8d77d636305ab4f7f12795632b3dfa469d0df5e4a2ade51bbd7-42337939507-1"
P20_WARM_WORK = Path(r"C:\cagp20-42337939507-1\work")
P20_CACHED_OUTPUTS = {'codex.exe': (411710464, '6fe7756b2350feec002d84b4c14c223da7c8a7e4be13e96532b6401d7bf2356e'), 'codex-command-runner.exe': (10913280, 'debd0a1718d7bb218eaeb65ff6d0f09d432afee56a1008d0a00e7a880731f9ef'), 'codex-windows-sandbox-setup.exe': (19553792, '766a7cc44a4ef0f23eff78614cd2741520df810641a7a481cf4088860e20b8f1'), 'codex-windows-managed-deny-probe.exe': (251392, '3629969201ecb56a97c084ccd1aeda76f95ca4e294b8dabf6a68859cbd742536'), 'codex-windows-sandbox-service.exe': (68414976, 'ea4b103f4dd1789c5172a27f3fc2911b56fb248f72f28ecb8afd7fb9f35a1e7b'), 'codex-code-mode-host.exe': (79894016, 'e9cf0e71fe2ef785324a732435997253a9f6ac577be87a6f850fa5d789332154')}
P21_GIT_CASES = (
    "root_exit_reaps_descendants_before_collecting_output",
    "timeout_reaps_descendants_before_collecting_output",
    "nonzero_exit_preserves_status_and_both_output_streams",
    "spawn_failure_remains_an_error",
    "unrelated_inheritable_handle_is_excluded_from_git_child",
    "relative_git_preserves_explicit_path_argv_and_child_cwd",
)

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
    lock_path = native_lock_path()
    require(sha(lock_path) == LOCK, "Exact candidate lock bytes required")
    lock = load(lock_path)
    require(lock["prepared_source_sha256"] == SOURCE and lock["prepared_source_files"] == SOURCE_FILES,
            "Exact reviewed candidate source required")
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

def classify_compiler_telemetry_cleanup(result, toolchain):
    """Accept one already-retired MSVC telemetry child by observed identity."""
    if (type(result.get("returncode")) is not int or result["returncode"] != 0
            or result.get("timed_out") is not False
            or any(result.get(key) for key in ("error", "error_type", "resource_guard",
                "wrapper_resource_guard", "remaining_after_cleanup", "unknown_live",
                "cleanup_errors", "observation_errors"))):
        return None
    compiler = Path(toolchain["compiler"]).resolve()
    linker = Path(toolchain["linker"]).resolve()
    if (compiler.name.casefold() != "cl.exe" or linker.name.casefold() != "link.exe"
            or compiler.parent != linker.parent):
        return None
    telemetry = compiler.with_name("vctip.exe")
    if not telemetry.is_file() or telemetry.resolve() != telemetry:
        return None
    # Microsoft's BuildXL identifies this exact tool as a surviving telemetry
    # child without meaningful build outputs. Still require observed ancestry
    # and successful exact-lifetime cleanup, never accept an unknown survivor.
    remaining = result.get("remaining_before_cleanup")
    cleanup = result.get("forced_cleanup")
    if not isinstance(remaining, list) or len(remaining) != 1 or remaining != cleanup:
        return None
    row = remaining[0]
    if (not row.get("stable") or row.get("ownership") != "stable_direct_parent_edge"
            or not re.fullmatch(r"windows:[0-9]+", row.get("birth") or "")
            or row.get("birth_after") != row["birth"] or row.get("ppid") != row.get("parent_pid")
            or not isinstance(row.get("argv"), list) or len(row["argv"]) != 1
            or not isinstance(row.get("exe"), str) or not row["exe"]
            or Path(row["exe"]).resolve() != telemetry or Path(row["argv"][0]).resolve() != telemetry):
        return None
    observed = result.get("observed", [])
    parents = [parent for parent in observed if parent.get("pid") == row["parent_pid"]
               and parent.get("birth") == row.get("parent_birth")]
    if len(parents) != 1 or not parents[0].get("stable") or row not in observed:
        return None
    if Path(parents[0].get("exe") or "").resolve() not in {compiler, linker}:
        return None
    return {"kind": "observed_MSVC_VCTIP_telemetry_cleanup_only",
            "pid": row["pid"], "birth": row["birth"], "image": str(telemetry),
            "image_sha256": sha(telemetry), "compiler_parent_pid": row["parent_pid"],
            "compiler_parent_birth": row["parent_birth"], "remaining_after_cleanup": []}


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
    telemetry = None
    if not result["observer_passed"] and name in {"normal-six-build", "native-unit-compile"}:
        telemetry = classify_compiler_telemetry_cleanup(result, load(root / "evidence/toolchain.json"))
    result["qualified_compiler_telemetry_cleanup"] = telemetry
    result["passed"] = bool((result["observer_passed"] or telemetry is not None) and
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


def native_lock_path():
    return REPO / f"native/build-lock.O165-{CANDIDATE}-source.json"


def work_path(root):
    return P20_WARM_WORK if CANDIDATE == "P21" else root / "work"


def verify_p21_cache(root):
    require(os.environ.get("CI_P20_CACHE_HIT") == "true"
            and os.environ.get("CI_P20_CACHE_KEY") == P20_WARM_CACHE_KEY,
            "Exact original P20 cache restore required; no fallback or cold build")
    require(os.environ.get("VCToolsVersion") == "14.44.35207"
            and os.environ.get("UCRTVersion") == "10.0.26100.0",
            "Restored cache requires the exact original MSVC and SDK")
    require(Path(root.anchor) == Path(P20_WARM_WORK.anchor)
            and not P20_WARM_WORK.parent.exists(),
            "Original absolute work root must be absent on the fresh runner")
    _, target = cache_paths(root)
    require(not target.is_symlink() and not target.is_junction()
            and target.resolve(strict=True) == target,
            "Restored target path is not the original physical cache")
    outputs = {}
    for name, (size, expected) in P20_CACHED_OUTPUTS.items():
        p = target / "release" / name
        require(p.is_file() and not p.is_symlink() and p.stat().st_size == size
                and sha(p) == expected, f"Original cached P20 output differs: {name}")
        outputs[name] = expected
    save(root / "evidence/exact-p20-cache-admission.json", {
        "original_hosted_run": 42337939507, "original_attempt": 1,
        "cache_key": P20_WARM_CACHE_KEY, "original_source_path": str(P20_WARM_WORK / "source"),
        "old_output_sha256": outputs, "cache_is_build_input_only": True})


def build(root):
    lock, builder, cases = plan()
    require((root / "evidence/inputs.json").is_file(), "Verified inputs required")
    work = work_path(root)
    require(not work.exists() and not (root / "native-producer").exists(),
            "A fresh owned build is required; never overwrite a previous candidate")
    if CANDIDATE == "P21":
        verify_p21_cache(root)
    toolchain = Path(os.environ["CI_RUST_TOOLCHAIN_BIN"])
    cache, target = cache_paths(root); jobs = build_jobs()
    command = [sys.executable, "-I", "-B", str(REPO / "scripts/build_native.py"),
               "--archive", str(root / "inputs/upstream.tar.gz"),
               "--work-dir", str(work), "--toolchain-bin", str(toolchain),
               "--cache-dir", str(cache), "--target-dir", str(target),
               "--build-lock", str(native_lock_path()),
               "--jobs", str(jobs), "--rusty-v8-dir", str(root / "inputs/rusty-v8"),
               "--output", str(root / "native-producer")]
    observe(command, root, REPO, clean_environment(), "normal-six-build", 7200)
    report = load(work / "build-report.json")
    require(report["status"] == "passed" and report["build_lock_sha256"] == LOCK
            and report["effective_build_jobs"] == jobs, "Normal producer did not pass")
    manifest = load(root / "native-producer/manifest.json")
    verify_normal_outputs(root, manifest)
    if CANDIDATE == "P21":
        require(next(r["sha256"] for r in manifest["binaries"] if r["file"] == "codex.exe")
                != P20_CACHED_OUTPUTS["codex.exe"][1], "P21 executable must differ from old P20")
    shutil.copyfile(root / "native-producer/manifest.json", root / "evidence/normal-raw-manifest.json")
    for name in ("build-report.json", "source-manifest.json", "build-owner.json"):
        shutil.copyfile(work / name, root / "evidence" / name)
    save(root / "evidence/normal-source-binding.json", {
        "normal_manifest_sha256": sha(root / "native-producer/manifest.json"),
        "source_sha256": SOURCE, "lock_sha256": LOCK, "native_build_executed": True,
        "App_GUI_qualified": False})

def unit(root, *, plugin_git=False):
    lock, builder, cases = plan()
    require(load(root / "evidence/normal-six-build.json")["passed"], "Normal six-target producer required")
    toolchain = Path(os.environ["CI_RUST_TOOLCHAIN_BIN"])
    cache, target = cache_paths(root); jobs = build_jobs()
    work = work_path(root)
    source = work / "source"
    environment = clean_environment(builder.build_environment(
        work, toolchain, cache, target_dir=target))
    environment["CARGO_BUILD_JOBS"] = str(jobs)
    environment.update(builder.verified_rusty_v8_inputs(
        source, root / "inputs/rusty-v8", lock["target"])["environment"])
    package_name = "codex-core-plugins" if plugin_git else "codex-windows-sandbox"
    target_name = package_name.replace("-", "_")
    compile_name = "native-git-unit-compile" if plugin_git else "native-unit-compile"
    if plugin_git:
        cases = [f"{module}::git_lifetime_windows_tests::{case}"
                 for module in ("startup_sync", "marketplace_upgrade::git")
                 for case in P21_GIT_CASES]
    command = [str(toolchain / "cargo.exe"), "test", "--locked", "--release", "--jobs", str(jobs),
               "-p", package_name, "--lib", "--no-run",
               "--message-format=json-render-diagnostics"]
    observe(command, root, source / "codex-rs", environment, compile_name, 1800)
    artifacts, finished = [], []
    for line in (root / "evidence" / f"{compile_name}.log").read_text(
            encoding="utf8", errors="replace").splitlines():
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            continue
        if message.get("reason") == "compiler-artifact" and message.get("executable") and \
                message.get("target", {}).get("name") == target_name and \
                message.get("profile", {}).get("test") is True:
            artifacts.append(message)
        if message.get("reason") == "build-finished":
            finished.append(message.get("success"))
    require(len(artifacts) == 1 and finished == [True], "Unique genuine native libtest artifact required")
    executable = Path(artifacts[0]["executable"])
    require(executable.is_relative_to(target) and executable.is_file(), "Unit artifact outside target")
    sealed = root / "delivery/unit" / f"{package_name}-{CANDIDATE.lower()}-libtest.exe"
    sealed.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(executable, sealed)
    require(sha(sealed) == sha(executable), "Sealed native test copy differs")
    observed = []
    for index, case in enumerate(cases):
        name = f"native-git-case-{index:02}" if plugin_git else f"native-case-{index:02}"
        observe([str(sealed), "--exact", case, "--show-output", "--test-threads=1"],
                root, source / "codex-rs", environment, name, 180)
        text = (root / f"evidence/{name}.log").read_text(encoding="utf8", errors="replace")
        require(len(re.findall(r"(?m)^test " + re.escape(case) + r" \.\.\. ok$", text)) == 1
                and len(re.findall(r"(?m)^test result: ok\. 1 passed; 0 failed; 0 ignored;", text)) == 1,
                "Exact native test must actually execute once; no filtered-out pass")
        observed.append({"name": case, "log_sha256": sha(root / f"evidence/{name}.log")})
    require(builder.source_state(source)["sha256"] == SOURCE,
            "Native source changed during unit qualification")
    manifest = load(root / "native-producer/manifest.json")
    verify_normal_outputs(root, manifest)
    receipt = "native-git-unit12.json" if plugin_git else "native-unit18.json"
    status = "P21_NATIVE_GIT12_PASSED_NOT_APP_QUALIFIED" if plugin_git else "P20_NATIVE_COMPONENT18_PASSED_NOT_APP_QUALIFIED"
    save(root / "evidence" / receipt, {
        "status": status,
        "cases": observed, "source_sha256": SOURCE, "lock_sha256": LOCK,
        "executable": {"path": str(sealed), "sha256": sha(sealed),
                       "profile": artifacts[0]["profile"]}, "App_GUI_qualified": False})

def git_unit(root):
    require(CANDIDATE == "P21", "Focused Git cases are specific to P21")
    unit(root, plugin_git=True)


def package(root):
    lock, builder, _ = plan()
    source = work_path(root) / "source"
    if CANDIDATE == "P21":
        git_result = load(root / "evidence/native-git-unit12.json")
        require(git_result["status"] == "P21_NATIVE_GIT12_PASSED_NOT_APP_QUALIFIED"
                and git_result["source_sha256"] == SOURCE and git_result["lock_sha256"] == LOCK
                and len(git_result["cases"]) == 12, "Exact new Git lifetime cases required")
    require(load(root / "evidence/normal-six-build.json")["passed"]
            and load(root / "evidence/native-unit18.json")["status"] ==
            "P20_NATIVE_COMPONENT18_PASSED_NOT_APP_QUALIFIED", "Build and exact unit gates required")
    toolchain = Path(os.environ["CI_RUST_TOOLCHAIN_BIN"])
    manifest = root / "native-producer/manifest.json"
    if CANDIDATE == "P21":
        # Cargo metadata may require locked sources that compilation did not fetch.
        # Reuse the original resumed assembler's target-specific source prefetch.
        env = clean_environment()
        env.update(CARGO_HOME=str(cache_paths(root)[0] / "cargo"), CARGO_NET_OFFLINE="false")
        before = builder.source_state(source)
        observe([str(toolchain / "cargo.exe"), "fetch", "--locked", "--target", lock["target"]],
                root, source / "codex-rs", env, "stage-locked-windows-metadata", 180)
        require(builder.source_state(source) == before and before["sha256"] == SOURCE,
                "Locked dependency prefetch changed prepared source")
    command = [sys.executable, "-I", "-B", str(REPO / "scripts/package_native_release.py"),
               "--source-root", str(source),
               "--native-manifest", str(manifest), "--native-manifest-sha256", sha(manifest),
               "--build-lock", str(native_lock_path()),
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
        "status": ("P21_NORMAL_SIX_AND_COMPONENT30_ASSEMBLED_NOT_APP_QUALIFIED"
                   if CANDIDATE == "P21" else "P20_NORMAL_SIX_AND_COMPONENT18_ASSEMBLED_NOT_APP_QUALIFIED"),
        "source_sha256": SOURCE, "lock_sha256": LOCK,
        "archive": archive.name, "archive_sha256": sha(archive),
        "raw_manifest_sha256": sha(root / "delivery/manifest.json"),
        "normal_build_receipt_sha256": sha(root / "evidence/normal-six-build.json"),
        "normal_assembly_receipt_sha256": sha(root / "evidence/normal-assembly/assembly-result.json"),
        "native_unit18_sha256": sha(root / "evidence/native-unit18.json"),
        **({"native_git_unit12_sha256": sha(root / "evidence/native-git-unit12.json")}
           if CANDIDATE == "P21" else {}),
        "App_GUI_qualified": False, "public_stable_release": False,
        "required_next": "Windows Limited Session3 helper/network migration and real official App acceptance"})

def main():
    global CANDIDATE, LOCK, SOURCE, SOURCE_FILES
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("check-only", "prepare", "build", "unit", "git_unit", "package"))
    parser.add_argument("--root", type=Path)
    parser.add_argument("--candidate", choices=("P20", "P21"), default="P20")
    args = parser.parse_args()
    if args.candidate == "P21":
        CANDIDATE, LOCK, SOURCE, SOURCE_FILES = "P21", P21_LOCK, P21_SOURCE, P21_SOURCE_FILES
        if args.mode != "check-only":
            require(os.environ.get("GITHUB_REF") == "refs/heads/codex/p20-hosted-ci-r570"
                    and os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch",
                    "P21 manual gate on the existing disposable cache branch required")
    if args.mode == "check-only":
        lock, _, cases = plan()
        print(json.dumps({"source": SOURCE, "lock": LOCK, "binaries": lock["binaries"],
                          "cases": len(cases), "host_changes": False, "native_execution": False,
                          **({"git_cases": 2 * len(P21_GIT_CASES)} if CANDIDATE == "P21" else {})}))
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
