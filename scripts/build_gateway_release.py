"""Build the Windows gateway bundle from explicit, offline, hash-pinned inputs.

This does not install, activate, configure, launch, qualify, or publish anything.
"""
from __future__ import annotations
import argparse
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile

REPOSITORY = "https://github.com/0xZoharHuang/codex-app-deepseek-gateway"

def require(value, message):
    if not value:
        raise ValueError(message)

def sha(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for part in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(part)
    return digest.hexdigest()

def write_json(path, value):
    Path(path).write_text(json.dumps(value, sort_keys=True, indent=2) + "\n", encoding="utf-8", newline="\n")

def environment(epoch):
    value = {k: v for k, v in os.environ.items()
             if not k.upper().startswith(("PYTHON", "PIP_", "PYTEST_", "OPENAI", "OPENROUTER", "CODEX", "GH_", "GITHUB_"))
             and k.upper() not in {"__PYVENV_LAUNCHER__", "SOURCE_DATE_EPOCH"}}
    value.update(SOURCE_DATE_EPOCH=str(epoch), PYTHONHASHSEED="0", PYTHONDONTWRITEBYTECODE="1",
                 PIP_CONFIG_FILE=os.devnull, PIP_NO_INDEX="1", PIP_DISABLE_PIP_VERSION_CHECK="1")
    return value

def _build_stderr(stderr, env):
    text = stderr.decode("utf-8", "replace") if isinstance(stderr, bytes) else (stderr or "")
    for name, value in env.items():
        if value and re.search(r"key|token|secret|password|passwd|credential|auth|proxy", name, re.I):
            text = text.replace(value, "[REDACTED]")
    text = re.sub(r"(?i)\b(?:sk-[a-z0-9_-]+|gh[pousr]_[a-z0-9]+|github_pat_[a-z0-9_]+)", "[REDACTED]", text)
    text = re.sub(r"(?i)(authorization:\s*bearer\s+)\S+", r"\1[REDACTED]", text)
    text = re.sub(r"(?i)(https?://)[^/\s@]+@", r"\1[REDACTED]@", text)
    text = "".join(c for c in text if ord(c) >= 32 or c in "\n\t").strip()
    return text[-1600:] or "no stderr was emitted"


def run(argv, *, cwd, env):
    try:
        result = subprocess.run([str(x) for x in argv], cwd=cwd, env=env, capture_output=True, timeout=300)
    except subprocess.TimeoutExpired as error:
        raise ValueError("Build command timed out after 300s: " + _build_stderr(error.stderr, env)) from None
    except OSError as error:
        raise ValueError(f"Build command could not start ({type(error).__name__}, errno={error.errno}, winerror={getattr(error, 'winerror', None)})") from None
    if result.returncode:
        raise ValueError(f"Build command failed (exit {result.returncode}): " + _build_stderr(result.stderr, env))
    return result.stdout


def copy_dependency(original, target, expected_sha256):
    """Publish independent bytes inheriting the output directory's permissions."""
    original, target = Path(original), Path(target)
    before = original.stat()
    require(sha(original) == expected_sha256, "Pinned dependency changed before bundle assembly")
    # A hard link carries the input ACL and remains coupled to future input writes.
    # Create a fresh file; do not use metadata/security-copying platform shortcuts.
    with original.open("rb") as incoming, target.open("xb") as outgoing:
        shutil.copyfileobj(incoming, outgoing, 1024 * 1024)
    after = original.stat()
    require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns, before.st_nlink)
            == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns, after.st_nlink),
            "Dependency identity changed during bundle assembly")
    require(not original.samefile(target) and target.stat().st_nlink == 1,
            "Bundled dependency must be an independent file")
    require(sha(original) == expected_sha256 and sha(target) == expected_sha256,
            "Dependency changed during bundle assembly")

def git_state(source, git, env):
    commit = run([git, "rev-parse", "HEAD"], cwd=source, env=env).decode().strip()
    epoch = int(run([git, "show", "-s", "--format=%ct", commit], cwd=source, env=env).decode())
    dirty = bool(run([git, "status", "--porcelain", "--untracked-files=all"], cwd=source, env=env).strip())
    return commit, epoch, dirty

def safe_name(name):
    part = PurePosixPath(name)
    require(not part.is_absolute() and ".." not in part.parts and chr(92) not in name,
            "Unsafe source or wheel entry")
    return part

def snapshot(source, destination, *, git, commit, development, env):
    destination.mkdir()
    if development:
        names = run([git, "ls-files", "--cached", "--others", "--exclude-standard", "-z"], cwd=source, env=env)
        for raw in names.decode("utf-8").split(chr(0)):
            if not raw:
                continue
            part = safe_name(raw)
            original = source.joinpath(*part.parts)
            require(original.is_file() and not original.is_symlink(), "Development source contains a missing file or link")
            target = destination.joinpath(*part.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(original, target)
    else:
        archive = run([git, "archive", "--format=tar", commit], cwd=source, env=env)
        with tarfile.open(fileobj=io.BytesIO(archive)) as stream:
            for member in stream:
                part = safe_name(member.name)
                if member.isdir():
                    continue
                require(member.isfile(), "Release source links are not supported")
                target = destination.joinpath(*part.parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                handle = stream.extractfile(member)
                require(handle is not None, "Source archive member has no data")
                with handle, target.open("wb") as output:
                    shutil.copyfileobj(handle, output)
    return {p.relative_to(destination).as_posix(): sha(p) for p in sorted(destination.rglob("*")) if p.is_file()}

def locate(row, directories):
    require(re.fullmatch(r"[A-Za-z0-9_.+-]+\.whl", row["filename"]) is not None, "Invalid pinned wheel filename")
    require(re.fullmatch(r"[0-9a-f]{64}", row["sha256"]) is not None, "Invalid pinned wheel SHA256")
    matches = [Path(folder) / row["filename"] for folder in directories if (Path(folder) / row["filename"]).is_file()]
    require(matches, "Pinned offline wheel is missing: " + row["filename"])
    require(all(sha(path) == row["sha256"] for path in matches), "Pinned offline wheel differs: " + row["filename"])
    return matches[0]

def verify_tools(python, tools, env, cwd):
    # Compare installed build tool contents with the caller-supplied official
    # wheel bytes, rather than trusting distribution version strings alone.
    code = """import sys,json,zipfile,hashlib,importlib.metadata as m,platform
rows=json.loads(sys.argv[1]);out=[]
for row in rows:
 dist=m.distribution(row['distribution'])
 assert dist.version==row['version'], 'Build tool version differs'
 count=0
 with zipfile.ZipFile(row['path']) as archive:
  for name in archive.namelist():
   if name.endswith('/') or name.endswith('.dist-info/RECORD'):continue
   assert '.data/' not in name, 'Unsupported relocated build tool'
   path=dist.locate_file(name)
   assert path.is_file() and hashlib.sha256(path.read_bytes()).digest()==hashlib.sha256(archive.read(name)).digest(), 'Installed build tool content differs'
   count+=1
 out.append({'distribution':row['distribution'],'version':dist.version,'verified_files':count,'wheel_sha256':row['sha256']})
print(json.dumps({'python_version':platform.python_version(),'platform':sys.platform,'machine':platform.machine(),'tools':out}))
"""
    return json.loads(run([python, "-I", "-B", "-c", code, json.dumps(tools)], cwd=cwd, env=env))

def build_one(python, source, output, env, log):
    output.mkdir()
    argv = [python, "-I", "-B", "-m", "pip", "--isolated", "--disable-pip-version-check",
            "wheel", "--no-index", "--no-deps", "--no-build-isolation", "--no-cache-dir",
            "--wheel-dir", output, source]
    result = subprocess.run([str(x) for x in argv], cwd=output.parent, env=env, capture_output=True, timeout=300)
    log.write_bytes(result.stdout + result.stderr)
    require(result.returncode == 0, "PEP517 wheel build failed; inspect private build log")
    wheels = list(output.glob("*.whl"))
    require(len(wheels) == 1, "PEP517 did not produce exactly one gateway wheel")
    wheel = wheels[0]
    expected = {p.relative_to(source / "src").as_posix(): sha(p)
                for p in (source / "src/codex_app_deepseek").rglob("*") if p.is_file()}
    with zipfile.ZipFile(wheel) as archive:
        actual = {name: hashlib.sha256(archive.read(name)).hexdigest()
                  for name in archive.namelist() if name.startswith("codex_app_deepseek/") and not name.endswith("/")}
    require(actual == expected, "Wheel payload differs from its exact source snapshot")
    return wheel, expected

def verify_cds_dependency(path, lock):
    """Verify only the admitted protocol and transport helper wheel members."""
    with zipfile.ZipFile(path) as archive:
        for name, expected in lock["protocol_boundary"]["pure_files"].items():
            require(hashlib.sha256(archive.read("codex_deepseek/" + name)).hexdigest() == expected,
                    "Pinned pure CDS protocol differs")
        compatibility = lock["transport_compatibility"]
        require(compatibility["source_commit"] == lock["protocol_boundary"]["source_commit"],
                "Transport helper source differs from pinned protocol source")
        require(set(compatibility["files"]) == {"windows_asyncio.py"},
                "Unexpected CDS transport helper boundary")
        for name, expected in compatibility["files"].items():
            require(hashlib.sha256(archive.read("codex_deepseek/" + name)).hexdigest() == expected,
                    "Pinned CDS transport helper differs")
        require(set(compatibility["license_files"]) == {
                    "codex_deepseek_runtime-0.6.9.dist-info/licenses/licenses/cpython-proactor-PSF.txt"},
                "Unexpected CDS transport license boundary")
        for name, expected in compatibility["license_files"].items():
            require(hashlib.sha256(archive.read(name)).hexdigest() == expected,
                    "Pinned CDS transport helper license differs")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--source-date-epoch", type=int, required=True)
    parser.add_argument("--build-python", type=Path, required=True)
    parser.add_argument("--dependency-lock", type=Path, default=Path(__file__).resolve().parents[1] / "release/dependencies.windows-py313.json")
    parser.add_argument("--dependency-wheelhouse", type=Path, action="append", required=True)
    parser.add_argument("--tool-wheelhouse", type=Path, action="append", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--git", default=shutil.which("git"))
    parser.add_argument("--development-qualification", action="store_true")
    args = parser.parse_args(argv)
    require(args.git and re.fullmatch(r"[0-9a-f]{40}", args.source_commit), "Exact Git commit and Git executable required")
    require(args.source_date_epoch >= 315532800, "Explicit SOURCE_DATE_EPOCH must be representable in wheel ZIP timestamps")
    source, output = args.source_root.resolve(), args.output.resolve()
    require(not output.exists() and not output.is_relative_to(source), "Choose a new output directory outside the source checkout")
    require(args.build_python.is_absolute() and args.build_python.is_file(), "An explicit existing build Python is required")
    env = environment(args.source_date_epoch)
    initial = git_state(source, args.git, env)
    require(initial[0] == args.source_commit and initial[1] == args.source_date_epoch, "Commit or SOURCE_DATE_EPOCH differs from Git")
    require(args.development_qualification or not initial[2], "Public release requires a clean checkout, including untracked files")
    lock_bytes = args.dependency_lock.read_bytes()
    lock_digest = hashlib.sha256(lock_bytes).hexdigest()
    lock = json.loads(lock_bytes)
    require(lock["schema_version"] == 1, "Unsupported dependency lock")
    dependencies = [(row, locate(row, args.dependency_wheelhouse)) for row in lock["dependencies"]]
    require(len({row["filename"].casefold() for row, _ in dependencies}) == len(dependencies), "Duplicate dependency wheel")
    tools = [{**row, "path": str(locate(row, args.tool_wheelhouse))} for row in lock["build_tools"]]
    require({row["distribution"].lower() for row in tools} == {"pip", "setuptools", "wheel", "packaging"}, "Complete fixed build-tool set required")
    tool_proof = verify_tools(args.build_python, tools, env, source)
    require(tool_proof["platform"] == "win32" and tool_proof["machine"].lower() in {"amd64", "x86_64"}, "This lock qualifies Windows x64 only")
    require(".".join(tool_proof["python_version"].split(".")[:2]) == lock["target"]["python_version"], "Build Python differs from the target lock")
    for row, path in dependencies:
        if row["distribution"] == "codex-deepseek-runtime":
            verify_cds_dependency(path, lock)
    output.mkdir()
    work = output / ".build"; work.mkdir()
    logs = output / ".logs"; logs.mkdir()
    before_a = snapshot(source, work / "a", git=args.git, commit=args.source_commit, development=args.development_qualification, env=env)
    before_b = snapshot(source, work / "b", git=args.git, commit=args.source_commit, development=args.development_qualification, env=env)
    require(before_a == before_b, "Independent source snapshots differ")
    require(before_a.get("release/dependencies.windows-py313.json") == lock_digest, "Dependency lock is not the committed source input")
    require(before_a.get("scripts/build_gateway_release.py") == sha(Path(__file__)), "Build controller differs from the committed source input")
    project = tomllib.loads((work / "a/pyproject.toml").read_text(encoding="utf-8"))
    require(project["project"]["name"] == "codex-app-deepseek-gateway", "Unexpected project identity")
    require(project["build-system"] == {"requires": ["setuptools==84.2.0", "wheel==0.48.0"], "build-backend": "setuptools.build_meta"}, "Build requirements differ from this locked recipe")
    wheel_a, payload = build_one(args.build_python, work / "a", work / "wheel-a", env, logs / "build-a.log")
    wheel_b, payload_b = build_one(args.build_python, work / "b", work / "wheel-b", env, logs / "build-b.log")
    require(wheel_a.name == wheel_b.name and sha(wheel_a) == sha(wheel_b) and payload == payload_b, "Independent wheels are not byte reproducible")
    for folder in ("a", "b"):
        require(all(sha(work / folder / name) == digest for name, digest in before_a.items()), "Build modified an original source input")
    require(git_state(source, args.git, env) == initial, "Source checkout changed while building")
    wheelhouse = output / "wheels"; wheelhouse.mkdir()
    for row, original in dependencies:
        target = wheelhouse / row["filename"]
        copy_dependency(original, target, row["sha256"])
    shutil.copyfile(wheel_a, wheelhouse / wheel_a.name)
    baseline = json.loads((work / "a/src/codex_app_deepseek/assets/app-launch-baseline.json").read_text(encoding="utf-8"))
    release = {"schema_version": 1, "project": "codex-app-deepseek-gateway", "version": project["project"]["version"],
               "source_repository": REPOSITORY, "source_commit": args.source_commit,
               "python_version": lock["target"]["python_version"], "gateway_wheel": wheel_a.name,
               "files": sorted([{"filename": path.name, "sha256": sha(path)} for path in wheelhouse.glob("*.whl")], key=lambda row: row["filename"]),
               "app_baseline": {"app_version": baseline["version"], "native_version": baseline["native_version"],
                                "native_sha256": baseline["files"]["app/resources/codex.exe"]}}
    name = "qualification-release.json" if args.development_qualification else "release.json"
    manifest = output / name
    release_b = dict(release)
    release_b["gateway_wheel"] = wheel_b.name
    release_b["files"] = [{"filename": row["filename"], "sha256": sha(wheel_b) if row["filename"] == wheel_a.name else row["sha256"]} for row in release["files"]]
    encoded = lambda value: (json.dumps(value, sort_keys=True, indent=2) + chr(10)).encode("utf-8")
    require(encoded(release) == encoded(release_b), "Independent release manifests differ")
    write_json(manifest, release)
    code = "import sys;from pathlib import Path;sys.path.insert(0,sys.argv[1]);from codex_app_deepseek.lifecycle import validate_release;validate_release(Path(sys.argv[2]),sys.argv[3],Path(sys.argv[4]))"
    run([args.build_python, "-I", "-S", "-B", "-c", code, work / "a/src", manifest, sha(manifest), wheelhouse], cwd=output, env=env)
    installer = output / "install_gateway.ps1"
    shutil.copyfile(work / "a/scripts/install_gateway.ps1", installer)
    (output / "dependencies.windows-py313.json").write_bytes(lock_bytes)
    proof = {"schema_version": 1, "status": "built_reproducible_not_App_qualified",
             "kind": "development_qualification" if args.development_qualification else "public_source_build",
             "build_controller_sha256": sha(Path(__file__)), "source_commit": args.source_commit, "source_commit_kind": "base_commit" if args.development_qualification else "exact_clean_source",
             "dirty": initial[2], "SOURCE_DATE_EPOCH": args.source_date_epoch,
             "dependency_lock_sha256": lock_digest, "source_files": before_a,
             "source_inventory_sha256": hashlib.sha256(json.dumps(before_a, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
             "build_python_sha256": sha(args.build_python), "toolchain": tool_proof,
             "independent_gateway_wheel_sha256": [sha(wheel_a), sha(wheel_b)],
             "independent_manifest_sha256": [hashlib.sha256(encoded(release)).hexdigest(), hashlib.sha256(encoded(release_b)).hexdigest()],
             "payload": payload, "manifest": name, "manifest_sha256": sha(manifest),
             "bootstrap_wheel": wheel_a.name, "bootstrap_wheel_sha256": sha(wheel_a),
             "installer_sha256": sha(installer), "dependencies": lock["dependencies"],
             "scope": "Reproduces this gateway wheel and manifest from the same immutable offline dependency wheels; does not rebuild third-party binaries or qualify App behavior"}
    write_json(output / "build-provenance.json", proof)
    shutil.rmtree(work)
    print(json.dumps({"status": proof["status"], "kind": proof["kind"], "output": str(output),
                      "manifest": name, "manifest_sha256": sha(manifest),
                      "build_provenance_sha256": sha(output / "build-provenance.json")}))
    return 0

if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (ValueError, OSError, subprocess.SubprocessError, KeyError, json.JSONDecodeError) as error:
        print("Gateway build rejected: " + str(error), file=sys.stderr)
        raise SystemExit(1)
