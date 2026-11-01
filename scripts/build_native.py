"""Build the pinned Windows native candidate in a private working directory.

Run from an x64 MSVC Developer shell with Python 3.11+, Git and the specified
Rust 1.99.1 toolchain installed. This script never installs or starts an App,
runtime, setup helper, service or scheduled task. --dry-run writes nothing.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import tarfile
import time
import uuid

REPO = Path(__file__).resolve().parents[1]
PROFILE = {
    "CARGO_BUILD_JOBS": "2", "CARGO_INCREMENTAL": "0",
    "CARGO_PROFILE_RELEASE_INCREMENTAL": "false", "CARGO_PROFILE_RELEASE_DEBUG": "0",
    "CARGO_PROFILE_RELEASE_STRIP": "symbols", "CARGO_PROFILE_RELEASE_LTO": "false",
    "CARGO_PROFILE_RELEASE_CODEGEN_UNITS": "16",
}

# Fixed Cargo targets keep CLI-only builds separate from the J sibling producer.
NATIVE_TARGETS = {
    "codex.exe": ("codex-cli", "codex"),
    "codex-command-runner.exe": ("codex-windows-sandbox", "codex-command-runner"),
    "codex-windows-sandbox-setup.exe": ("codex-windows-sandbox", "codex-windows-sandbox-setup"),
    "codex-windows-managed-deny-probe.exe": ("codex-windows-sandbox", "codex-windows-managed-deny-probe"),
}

def build_jobs_policy(override: int | None = None) -> dict:
    maximum = int(PROFILE["CARGO_BUILD_JOBS"])
    require(override is None or type(override) is int and 1 <= override <= maximum,
            "Build jobs override must be an integer within the locked maximum")
    return {"locked_max_build_jobs": maximum,
            "effective_build_jobs": maximum if override is None else override,
            "build_jobs_override": override}


def build_commands(toolchain: Path, binaries: list[str], *, jobs: int | None = None) -> list[list[str]]:
    require(isinstance(binaries, list) and bool(binaries)
            and all(isinstance(name, str) and name in NATIVE_TARGETS for name in binaries)
            and len(binaries) == len(set(binaries)), "Unsupported or duplicate native binary targets")
    packages = list(dict.fromkeys(NATIVE_TARGETS[name][0] for name in binaries))
    build_jobs_policy(jobs)
    command = [str(toolchain / "cargo.exe"), "build", "--locked", "--release"]
    if jobs is not None:
        command.extend(["--jobs", str(jobs)])
    for package in packages:
        command.extend(["-p", package])
    for name in binaries:
        command.extend(["--bin", NATIVE_TARGETS[name][1]])
    return [command]


def sha(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()

def extended(path: Path) -> Path:
    prefix = chr(92) * 2 + "?" + chr(92)
    return path if str(path).startswith(prefix) else Path(prefix + str(path.absolute()))

def source_state(source: Path) -> dict:
    root = extended(source)
    files = {p.relative_to(root).as_posix(): {"sha256": sha(p), "size": p.stat().st_size}
             for p in root.rglob("*") if p.is_file() and ".git" not in p.relative_to(root).parts}
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"sha256": digest, "count": len(files), "files": files}


def source_changes(archive: Path, commit: str, prepared: dict) -> list[dict]:
    # Derive the summary from full inventories, never an inherited manual path list.
    upstream = {}
    prefix = "codex-" + commit + "/"
    with tarfile.open(archive) as stream:
        for member in stream.getmembers():
            if member.isdir():
                continue
            require(member.name.startswith(prefix), "archive member has an unexpected root")
            relative = PurePosixPath(member.name[len(prefix):])
            require(not relative.is_absolute() and ".." not in relative.parts, "unsafe archive path")
            name = relative.as_posix()
            require(name not in upstream, "duplicate archive member")
            handle = stream.extractfile(member)
            require(handle is not None, "archive member has no data")
            digest = hashlib.sha256()
            size = 0
            with handle:
                for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                    digest.update(chunk)
                    size += len(chunk)
            upstream[name] = {"sha256": digest.hexdigest(), "size": size}
    changes = []
    for name in sorted(upstream.keys() | prepared.keys()):
        original, current = upstream.get(name), prepared.get(name)
        if original != current:
            changes.append({"path": name,
                            "kind": "added" if original is None else "deleted" if current is None else "modified",
                            "before": original, "after": current})
    return changes


def atomic(path: Path, data: dict) -> None:
    temp = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    temp.write_text(json.dumps(data, indent=2) + chr(10), encoding="utf8")
    temp.replace(path)

def checked_output(command: list[str]) -> str:
    return subprocess.check_output(command, text=True, stderr=subprocess.STDOUT).strip()

def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)

def prepare_source(archive: Path, source: Path, lock: dict, git: str) -> None:
    if source.exists():
        state = source_state(source)
        require(state["sha256"] == lock["prepared_source_sha256"],
                "existing source is incomplete or modified; preserve it and choose a new work directory")
        return
    source.mkdir(parents=True)
    root = extended(source)
    prefix = "codex-" + lock["upstream_commit"] + "/"
    with tarfile.open(archive) as stream:
        for member in stream.getmembers():
            if member.isdir():
                continue
            require(member.name.startswith(prefix), "archive member has an unexpected root")
            relative = PurePosixPath(member.name[len(prefix):])
            require(not relative.is_absolute() and ".." not in relative.parts, "unsafe archive path")
            require(member.isfile() or member.issym() or member.islnk(), "unsupported archive member")
            # The fixed archive contains a LICENSE -> COPYING link. Materialize its exact
            # archive bytes on Windows; no developer-mode symlink privilege is required.
            if member.issym() or member.islnk():
                link = PurePosixPath(member.linkname)
                require(not link.is_absolute() and ".." not in link.parts, "unsafe archive link")
            destination = root.joinpath(*relative.parts)
            destination.parent.mkdir(parents=True, exist_ok=True)
            handle = stream.extractfile(member)
            require(handle is not None, "archive member has no data")
            with handle, destination.open("wb") as target:
                shutil.copyfileobj(handle, target)
    subprocess.run([git, "init", "--quiet", "--initial-branch=codex/native-build"], cwd=source, check=True)
    for patch in lock["patches"]:
        path = REPO / "native" / patch["file"]
        command = [git, "-c", "core.autocrlf=false", "-c", "core.longpaths=true", "apply"]
        subprocess.run([*command, "--check", str(path)], cwd=source, check=True)
        subprocess.run([*command, str(path)], cwd=source, check=True)
    state = source_state(source)
    require(state["sha256"] == lock["prepared_source_sha256"], "patched source checksum mismatch")
    require(state["count"] == lock["prepared_source_files"], "patched source file count mismatch")
    require(sha(source / "codex-rs" / "Cargo.lock") == lock["cargo_lock_sha256"], "Cargo.lock checksum mismatch")


def cache_directory(work: Path, source: Path, output: Path, selected: Path | None, *, external_target: bool = False) -> Path:
    """Select an operator-owned cache without moving source or deployment state."""
    cache = selected.resolve() if selected is not None else work
    if selected is None:
        return cache
    require(cache.is_dir(), "explicit cache directory must already exist")
    for protected in (REPO.resolve(), source.resolve(), output.resolve()):
        # The ordinary work layout is retained when explicitly selected too.
        if cache == work and protected in (source.resolve(), output.resolve()):
            continue
        require(cache != protected and cache not in protected.parents and protected not in cache.parents,
                "cache directory must not overlap the checkout, current source or output")
    for name in (("cargo",) if external_target else ("cargo", "target")):
        child = cache / name
        require(not child.is_symlink() and not (hasattr(child, "is_junction") and child.is_junction()),
                "cache entries must not be redirected")
        require(not child.exists() or child.is_dir(), "cache entries must be directories")
    return cache


def target_directory(cache: Path, source: Path, output: Path, selected: Path | None) -> Path:
    """An explicit ordinary Cargo target directory; never infer a redirected root."""
    if selected is None:
        return cache / "target"
    absolute = selected.absolute()
    for part in (absolute, *absolute.parents):
        require(not part.is_symlink() and not (hasattr(part, "is_junction") and part.is_junction()),
                "explicit target directory must not traverse a redirect")
    target = absolute.resolve()
    require(target.is_dir(), "explicit target directory must already exist")
    for protected in (REPO.resolve(), source.resolve(), output.resolve(), (cache / "cargo").resolve()):
        require(target != protected and target not in protected.parents and protected not in target.parents,
                "target directory must not overlap source, output, checkout or Cargo home")
    return target


def build_environment(work: Path, toolchain: Path, cache_dir: Path | None = None, *, target_dir: Path | None = None) -> dict[str, str]:
    cache = cache_dir if cache_dir is not None else work
    environment = dict(os.environ)
    for key in list(environment):
        if key.startswith("CARGO_PROFILE_RELEASE_") or key in {
            "RUSTFLAGS", "CARGO_ENCODED_RUSTFLAGS", "RUSTC_WRAPPER", "RUSTC_WORKSPACE_WRAPPER",
            "CARGO_TARGET_X86_64_PC_WINDOWS_MSVC_RUSTFLAGS",
        }:
            environment.pop(key)
    environment.update(PROFILE)
    environment.update({"CARGO_HOME": str(cache / "cargo"), "CARGO_TARGET_DIR": str(target_dir if target_dir is not None else cache / "target"),
                        "RUSTC": str(toolchain / "rustc.exe"), "RUSTDOC": str(toolchain / "rustdoc.exe")})
    return environment

def preflight_windows_msvc(environment: dict[str, str], target: str) -> dict:
    """Compile/link (never run) a tiny probe before source or output mutation."""
    import tempfile

    macros = {"x86_64-pc-windows-msvc": "_M_X64", "aarch64-pc-windows-msvc": "_M_ARM64"}
    require(target in macros, "unsupported MSVC preflight target")
    compiler = shutil.which("cl.exe", path=environment.get("PATH", ""))
    require(compiler is not None, "MSVC cl.exe is unavailable; open a complete Developer shell")
    source = ("#include <stddef.h>\n#include <windows.h>\n"
              f"#ifndef {macros[target]}\n#error Wrong MSVC target architecture\n#endif\n"
              "int main(void) { return GetCurrentProcessId() == 0; }\n")
    with tempfile.TemporaryDirectory(prefix="cadsg-msvc-preflight-") as temporary:
        root = Path(temporary)
        src, obj, exe = [root / name for name in ("probe.c", "probe.obj", "probe.exe")]
        src.write_text(source, encoding="ascii")
        try:
            result = subprocess.run(
                [compiler, "/nologo", "/MT", "/TC", str(src), f"/Fo{obj}", f"/Fe{exe}",
                 "/link", "kernel32.lib"],
                cwd=root, env=dict(environment), capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ValueError(f"MSVC/SDK preflight could not complete: {type(error).__name__}") from None
        detail = (result.stdout + result.stderr)[-2500:]
        require(result.returncode == 0, f"MSVC/Windows SDK compile-link preflight failed:\n{detail}")
        require(all(path.is_file() and path.stat().st_size > 0 for path in (obj, exe)),
                "MSVC/SDK preflight did not produce a complete object and executable")
    return {"status": "compile-link-passed", "target": target, "probe_program_executed": False}


def build_record_path(path: Path, *, existing: bool = False) -> Path:
    """Do not follow redirected build metadata or mutate hardlinked records."""
    for part in (path, *path.parents):
        try:
            info = extended(part).lstat()
        except FileNotFoundError:
            continue
        require(not part.is_symlink()
                and not (hasattr(part, "is_junction") and part.is_junction())
                and not (getattr(info, "st_file_attributes", 0) & 0x400),
                "build metadata path must not be redirected")
    selected = extended(path)
    if selected.exists():
        require(selected.is_file() and selected.stat().st_nlink == 1,
                "build metadata record must be a single regular file")
    else:
        require(not existing, "required build metadata record is missing")
    return selected


def transition_owned_build(work: Path, lock: dict, plan: dict, expected_old: str) -> dict:
    """Advance one owned, already prepared tree without rewriting source timestamps."""
    marker = build_record_path(work / "build-owner.json", existing=True)
    original_marker = marker.lstat()
    require(isinstance(expected_old, str) and len(expected_old) == 64
            and all(c in "0123456789abcdef" for c in expected_old),
            "transition requires the exact previous build-lock SHA256")
    # Hold an OS lock on the owner record itself. Other readers cannot consume a
    # partly written record; an interrupted transition fails closed with its backup.
    with marker.open("r+b", buffering=0) as handle:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            opened = os.fstat(handle.fileno())
            require((opened.st_dev, opened.st_ino, opened.st_nlink)
                    == (original_marker.st_dev, original_marker.st_ino, 1),
                    "build owner identity changed before transition")
            raw = handle.read()
            owner = json.loads(raw)
            require(owner == {"schema_version": 1, "build_lock_sha256": expected_old},
                    "build owner changed; no input transition performed")
            require(expected_old != plan["build_lock_sha256"], "build inputs are already selected")
            require(not Path(plan["output"]).exists() or not any(Path(plan["output"]).iterdir()),
                    "new build output must be empty")
            records = {"build-owner.json": raw,
                       "build-report.json": build_record_path(work / "build-report.json", existing=True).read_bytes(),
                       "source-manifest.json": build_record_path(work / "source-manifest.json", existing=True).read_bytes()}
            previous = json.loads(records["build-report.json"])
            require(previous.get("status") in {"passed", "failed"}
                    and previous.get("build_lock_sha256") == expected_old,
                    "previous build is not terminal under the expected lock")
            require(previous.get("profile") == plan["profile"]
                    and Path(previous["source"]).resolve() == Path(plan["source"]).resolve()
                    and Path(previous["cache_dir"]).resolve() == Path(plan["cache_dir"]).resolve(),
                    "input transition must retain the source path, Cargo cache and profile")
            state = source_state(Path(plan["source"]))
            require(state["sha256"] == lock["prepared_source_sha256"]
                    and state["count"] == lock["prepared_source_files"],
                    "prepared source does not match the new build lock")
            require(sha(Path(plan["source"]) / "codex-rs/Cargo.lock") == lock["cargo_lock_sha256"],
                    "prepared Cargo.lock does not match the new build lock")
            saved = {name: hashlib.sha256(content).hexdigest() for name, content in records.items()}
            snapshot = hashlib.sha256(json.dumps(saved, sort_keys=True).encode()).hexdigest()
            directory = work / "previous-builds" / snapshot
            # Check every ancestor before mkdir and every target before opening it.
            build_record_path(directory / "build-owner.json")
            backup = extended(directory)
            backup.mkdir(parents=True, exist_ok=True)
            for name, content in records.items():
                target = build_record_path(directory / name)
                if target.exists():
                    require(target.read_bytes() == content, "previous build backup differs")
                else:
                    with target.open("xb") as output:
                        require(output.write(content) == len(content), "previous build backup write was incomplete")
                        output.flush(); os.fsync(output.fileno())
            new = (json.dumps({"schema_version": 1, "build_lock_sha256": plan["build_lock_sha256"]},
                              indent=2) + chr(10)).encode("utf8")
            require((marker.lstat().st_dev, marker.lstat().st_ino, marker.lstat().st_nlink)
                    == (opened.st_dev, opened.st_ino, 1), "build owner path changed before publication")
            handle.seek(0)
            require(handle.write(new) == len(new), "build owner write was incomplete")
            handle.truncate(); handle.flush(); os.fsync(handle.fileno())
            handle.seek(0)
            require(handle.read() == new, "build owner readback differs")
            require((marker.lstat().st_dev, marker.lstat().st_ino, marker.lstat().st_nlink)
                    == (opened.st_dev, opened.st_ino, 1), "build owner path changed after publication")
            return {"old_lock_sha256": expected_old, "new_lock_sha256": plan["build_lock_sha256"],
                    "preserved_records": saved, "backup_directory": str(backup),
                    "prepared_source_sha256": state["sha256"], "source_rewritten": False,
                    "source_timestamps_rewritten": False}
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True, help="Fixed upstream source tar.gz")
    parser.add_argument("--work-dir", type=Path, required=True, help="New directory, or a matching owned build directory")
    parser.add_argument("--toolchain-bin", type=Path, required=True, help="Installed Rust 1.99.1 bin directory")
    parser.add_argument("--output", type=Path, help="Empty candidate output directory; defaults to WORK/candidate")
    parser.add_argument("--cache-dir", type=Path, help="Explicit existing owned Cargo/target cache root; defaults to WORK")
    parser.add_argument("--target-dir", type=Path, help="Explicit existing owned CARGO_TARGET_DIR, separate from Cargo home")
    parser.add_argument("--transition-from-lock", help="Explicit CAS of a terminal owned build to this lock, only when its existing prepared source matches exactly; select a new empty --output")
    parser.add_argument("--build-lock", type=Path, default=REPO / "native/build-lock.json", help="Explicit pinned source/target lock; defaults to the current CLI candidate")
    parser.add_argument("--jobs", type=int, choices=range(1, int(PROFILE["CARGO_BUILD_JOBS"]) + 1),
                        help="Official Cargo concurrency override, bounded by the locked maximum")
    parser.add_argument("--dry-run", action="store_true", help="Verify inputs/tool versions and print the plan without writing")
    args = parser.parse_args(argv)
    require(os.name == "nt", "this candidate targets Windows; use a Windows x64 MSVC Developer shell")
    archive, work, toolchain = (p.resolve() for p in (args.archive, args.work_dir, args.toolchain_bin))
    output = args.output.resolve() if args.output else work / "candidate"
    source = work / "source"
    require(work != REPO and work not in REPO.parents and REPO not in work.parents,
            "use a separate build directory outside the gateway checkout")
    require(output != source and source not in output.parents and output not in source.parents,
            "candidate output must not overlap the source directory")
    cache = cache_directory(work, source, output, args.cache_dir, external_target=args.target_dir is not None)
    target = target_directory(cache, source, output, args.target_dir)
    lock_path = args.build_lock.resolve()
    lock = json.loads(lock_path.read_text(encoding="utf8"))
    require(lock["schema_version"] == 1 and lock["release_environment"] == PROFILE, "unsupported build lock/profile")
    require(sha(archive) == lock["archive_sha256"], "upstream archive checksum mismatch")
    for patch in lock["patches"]:
        require(Path(patch["file"]).name == patch["file"], "patch must be a native directory filename")
        require(sha(REPO / "native" / patch["file"]) == patch["sha256"], "patch checksum mismatch: " + patch["file"])
    require(all(Path(name).name == name and name.endswith(".exe") for name in lock["binaries"]),
            "binary outputs must be executable filenames")
    rust = checked_output([str(toolchain / "rustc.exe"), "--version", "--verbose"])
    require(rust.startswith("rustc " + lock["rust_version"] + " ") and "host: " + lock["target"] in rust,
            "Rust version/host differs from the build lock")
    cargo = checked_output([str(toolchain / "cargo.exe"), "--version"])
    require(cargo.startswith("cargo " + lock["rust_version"] + " "), "Cargo version differs from the build lock")
    git, linker = shutil.which("git.exe"), shutil.which("link.exe")
    require(git is not None and linker is not None and bool(os.environ.get("LIB")),
            "Git and the MSVC linker/LIB environment are required; open an x64 Developer shell")
    # One invocation for exactly the locked targets; never --bins.
    commands = build_commands(toolchain, lock["binaries"], jobs=args.jobs)
    plan = {"schema_version": 1, "mode": "dry-run" if args.dry_run else "build",
            "build_lock_sha256": sha(lock_path), "upstream_commit": lock["upstream_commit"],
            "native_version": lock["native_version"],
            "candidate_revision": lock.get("candidate_revision"),
            "archive": str(archive), "archive_sha256": lock["archive_sha256"], "patches": lock["patches"],
            "source": str(source), "source_sha256": lock["prepared_source_sha256"],
            "work_dir": str(work), "cache_dir": str(cache), "target": str(target), "cargo_home": str(cache / "cargo"),
            "output": str(output), "profile": PROFILE, **build_jobs_policy(args.jobs),
            "below_normal_priority": True, "commands": commands, "rustc": rust, "cargo": cargo,
            "linker": linker, "runtime_executed": False, "app_qualified": False}
    marker = work / "build-owner.json"
    if work.exists() and any(work.iterdir()):
        require(marker.is_file(), "non-empty work directory has no build ownership record")
        previous_lock = json.loads(marker.read_text())["build_lock_sha256"]
        require(previous_lock == plan["build_lock_sha256"] or args.transition_from_lock == previous_lock,
                "work directory belongs to a different input set; an explicit verified transition is required")
        require(args.transition_from_lock is None or previous_lock != plan["build_lock_sha256"],
                "build inputs are already selected; inspect the existing run before retrying")
    require(not output.exists() or not any(output.iterdir()), "output directory must be empty; existing candidates are preserved")
    if args.transition_from_lock is not None:
        require(marker.is_file(), "input transition requires an existing owned build")
        plan["transition_from_lock"] = args.transition_from_lock
    plan["msvc_sdk_preflight"] = {"status": "not-executed-in-dry-run"}
    if args.dry_run:
        print(json.dumps(plan, indent=2))
        return 0
    environment = build_environment(work, toolchain, cache, target_dir=target)
    plan["msvc_sdk_preflight"] = preflight_windows_msvc(environment, lock["target"])
    work.mkdir(parents=True, exist_ok=True)
    if not marker.exists():
        atomic(marker, {"schema_version": 1, "build_lock_sha256": plan["build_lock_sha256"]})
    transition = transition_owned_build(work, lock, plan, args.transition_from_lock) if args.transition_from_lock else None
    report = {**plan, "status": "preparing_source", "started_at": time.time(), "phases": []}
    if transition is not None:
        report["owner_transition"] = transition
    report_path = work / "build-report.json"
    atomic(report_path, report)
    try:
        prepare_source(archive, source, lock, git)
        before = source_state(source)
        changes = source_changes(archive, lock["upstream_commit"], before["files"])
        summary = {"changed_files": [change["path"] for change in changes], "source_changes": changes}
        report.update(summary)
        atomic(work / "source-manifest.json", {**before, **summary})
        # Foreground subprocesses, exactly as in an ordinary developer shell; no service or runner.
        for command in commands:
            phase = {"command": command, "status": "building"}
            report["phases"].append(phase)
            report["status"] = "building"
            atomic(report_path, report)
            completed = subprocess.run(command, cwd=source / "codex-rs", env=environment,
                                       creationflags=subprocess.BELOW_NORMAL_PRIORITY_CLASS)
            phase.update(exit_code=completed.returncode, status="passed" if completed.returncode == 0 else "failed")
            require(completed.returncode == 0, "native compiler failed; inspect its output and the retained build directory")
        after = source_state(source)
        require(before == after, "source changed during build; outputs are invalid and will not be copied")
        require(sha(archive) == plan["archive_sha256"], "upstream archive changed during compilation")
        require(sha(lock_path) == plan["build_lock_sha256"], "build lock changed during compilation")
        for patch in lock["patches"]:
            require(sha(REPO / "native" / patch["file"]) == patch["sha256"], "patch changed during compilation")
        output.mkdir(parents=True, exist_ok=True)
        binaries = []
        for name in lock["binaries"]:
            compiled = target / "release" / name
            expected = sha(compiled)
            temporary = output / (name + ".partial")
            shutil.copy2(compiled, temporary)
            require(sha(temporary) == expected, "candidate copy checksum mismatch")
            destination = output / name
            temporary.replace(destination)
            binaries.append({"file": name, "sha256": expected, "size": destination.stat().st_size})
        report.update(status="passed", finished_at=time.time(), binaries=binaries)
        atomic(output / "manifest.json", {**plan, **summary, "binaries": binaries, "status": "built-not-app-qualified"})
        atomic(report_path, report)
        print(json.dumps({"output": str(output), "binaries": binaries}, indent=2))
        return 0
    except Exception as error:
        report.update(status="failed", error_type=type(error).__name__, error=str(error), finished_at=time.time())
        atomic(report_path, report)
        raise

if __name__ == "__main__":
    raise SystemExit(main())
