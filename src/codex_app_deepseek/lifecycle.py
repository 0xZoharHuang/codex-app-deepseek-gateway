"""Explicit per-user gateway installation and lifecycle; never owns the App runtime."""
from __future__ import annotations
from .paths import _is_link_or_reparse

import argparse
from contextlib import ExitStack, contextmanager
import base64
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request

PROJECT = "codex-app-deepseek-gateway"
SOURCE_REPOSITORY = "https://github.com/0xZoharHuang/codex-app-deepseek-gateway"


class LifecycleError(RuntimeError):
    pass



def _fs_path(path: Path) -> Path:
    """Use Win32 extended paths for already selected filesystem locations."""
    path = Path(path).absolute()
    if os.name != "nt":
        return path
    text = str(path)
    if text.startswith("\\\\?\\"):
        return path
    if text.startswith("\\\\"):
        return Path("\\\\?\\UNC\\" + text[2:])
    return Path("\\\\?\\" + text)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with _fs_path(path).open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def _json(path: Path) -> dict:
    try:
        value = json.loads(_fs_path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError, UnicodeError):
        raise LifecycleError("Cannot read lifecycle JSON record") from None
    if not isinstance(value, dict):
        raise LifecycleError("Lifecycle record must be a JSON object")
    return value


def _write(path: Path, value: dict) -> None:
    from .atomic_io import atomic_write, AtomicPublicationConflict
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    # Install/finalize bootstrap has only stdlib and the pinned gateway wheel.
    # No permission callback retains this producer's original inherited ACL.
    try:
        atomic_write(path, raw)
    except AtomicPublicationConflict as exc:
        raise LifecycleError(str(exc)) from exc


def _home(path: Path) -> Path:
    from .cli import resolve_service_home
    return resolve_service_home(path)


def _roots(install_root: Path, data_home: Path) -> tuple[Path, Path]:
    install, data = _home(install_root), _home(data_home)
    if data == install or data.is_relative_to(install):
        raise LifecycleError("Install root must not contain the persistent data home")
    return install, data


def _within(path: Path, root: Path) -> Path:
    resolved = Path(path).resolve()
    if resolved == root or not resolved.is_relative_to(root):
        raise LifecycleError("Owned path escapes its declared installation directory")
    return resolved


def validate_release(manifest_path: Path, expected_sha256: str, wheelhouse: Path) -> dict:
    """Verify a caller-trusted manifest digest and every offline artifact before execution."""
    if not re.fullmatch(r"[0-9a-fA-F]{64}", expected_sha256 or "") or digest(manifest_path) != expected_sha256.lower():
        raise LifecycleError("Release manifest SHA256 mismatch")
    release = _json(manifest_path)
    keys = {"schema_version", "project", "version", "source_repository", "source_commit", "python_version",
            "gateway_wheel", "files", "app_baseline"}
    if set(release) != keys or release["schema_version"] != 1 or release["project"] != PROJECT:
        raise LifecycleError("Unsupported release manifest schema")
    if release["source_repository"] != SOURCE_REPOSITORY:
        raise LifecycleError("Release must name the fixed gateway source repository")
    if not isinstance(release["source_commit"], str) or not re.fullmatch(r"[0-9a-f]{40}", release["source_commit"]):
        raise LifecycleError("Release must pin an exact source commit")
    version = release["version"]
    if not isinstance(version, str) or not re.fullmatch(r"[0-9][0-9A-Za-z.+]*", version):
        raise LifecycleError("Release version must be an exact wheel version")
    if not isinstance(release["python_version"], str) or not re.fullmatch(r"3\.(?:1[1-9]|[2-9][0-9])", release["python_version"]):
        raise LifecycleError("Release must pin a supported Python major.minor version")
    baseline = release["app_baseline"]
    if not isinstance(baseline, dict) or set(baseline) != {"app_version", "native_version", "native_sha256"}:
        raise LifecycleError("Release needs the complete qualified App/native baseline")
    if any(not isinstance(x, str) or not x or x != x.strip() or any(ord(c) < 32 for c in x) for x in baseline.values()):
        raise LifecycleError("Invalid App/native baseline")
    if not re.fullmatch(r"[0-9a-f]{64}", baseline["native_sha256"]):
        raise LifecycleError("Native baseline must pin SHA256")
    files = release["files"]
    if not isinstance(files, list) or not files:
        raise LifecycleError("Release must enumerate its complete offline wheel set")
    seen = set()
    root = Path(wheelhouse).resolve()
    for artifact in files:
        if not isinstance(artifact, dict) or set(artifact) != {"filename", "sha256"}:
            raise LifecycleError("Artifact records require filename and SHA256 only")
        name, sha = artifact["filename"], artifact["sha256"]
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.+-]+\.whl", name) or name.casefold() in seen:
            raise LifecycleError("Wheel names must be unique flat filenames")
        seen.add(name.casefold())
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha):
            raise LifecycleError("Each wheel requires a SHA256")
        file = _within(root / name, root)
        if not file.is_file() or digest(file) != sha:
            raise LifecycleError("Release wheel missing or SHA256 mismatch")
    gateway = release["gateway_wheel"]
    if not isinstance(gateway, str) or gateway.casefold() not in seen or not gateway.startswith("codex_app_deepseek_gateway-" + version + "-"):
        raise LifecycleError("Gateway wheel must match the pinned project version")
    return release


def _run(argv: list[str], *, cwd: Path | None = None, timeout: int = 600):
    command = list(argv)
    if (os.name == "nt" and command and Path(command[0]).is_absolute()
            and Path(command[0]).name.casefold() in {"python.exe", "pythonw.exe"}):
        # CPython preserves an extended executable path in sys.prefix. This lets
        # pip write long private venv metadata paths without machine-wide policy
        # changes; stored installation and scheduler paths keep their normal form.
        command[0] = str(_fs_path(Path(command[0])))
    # An explicitly selected interpreter/release must not import another
    # environment's package through PYTHONPATH/HOME or user startup settings.
    environment = {key: value for key, value in os.environ.items()
                   if not key.upper().startswith("PYTHON")}
    result = subprocess.run(command, cwd=cwd, env=environment, capture_output=True,
                            text=True, encoding="utf-8", errors="replace", timeout=timeout)
    if result.returncode:
        raise LifecycleError(f"Owned lifecycle command failed (exit {result.returncode}); no automatic replay performed")
    return result


def _inventory(directory: Path, *, owned_files: dict | None = None) -> dict[str, str]:
    directory = _fs_path(directory)
    result = {}
    for file in directory.rglob("*"):
        if _is_link_or_reparse(file):
            raise LifecycleError("Managed installation must not contain links or junctions")
        source = file.parent.parent / (file.name.split(".cpython-", 1)[0] + ".py")
        source_owned = source.is_file() or (owned_files is not None and source.is_relative_to(directory)
                                           and source.relative_to(directory).as_posix() in owned_files)
        generated_bytecode = (file.suffix == ".pyc" and file.parent.name == "__pycache__"
                              and ".cpython-" in file.name and source_owned)
        if file.is_file() and file != directory / "installation.json" and not generated_bytecode:
            result[file.relative_to(directory).as_posix()] = digest(file)
    return result


def _preflight_windows_wheel_paths(release: dict, wheelhouse: Path, directory: Path) -> None:
    """Admit ordinary Windows runtime paths before creating an installation.

    pip still installs wheels. This only checks their verified destination names;
    a layout whose path scheme is not known here fails explicitly.
    """
    if os.name != "nt":
        return
    import configparser
    import stat
    import zipfile
    venv = directory / "venv"
    library = venv / "Lib/site-packages"
    scripts = venv / "Scripts"
    seen: dict[str, str] = {}
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{x}" for x in "123456789¹²³"),
                *(f"LPT{x}" for x in "123456789¹²³")}

    def components(name: str) -> list[str]:
        parts = name.split("/")
        if any(not p or p in {".", ".."} or p[-1:] in {" ", "."}
               or any(ord(c) < 32 or c in '<>:"|?*' or c == chr(92) for c in p)
               or p.split(".")[0].upper() in reserved for p in parts):
            raise LifecycleError("Wheel contains an unsafe Windows installation path")
        return parts

    def admit(path: Path, wheel: str, *, generated: bool = False) -> None:
        try:
            units = len(str(path).encode("utf-16-le")) // 2
        except UnicodeError:
            raise LifecycleError("Wheel contains an invalid Windows path encoding") from None
        if units > 259:
            raise LifecycleError("Windows installation path exceeds 259 UTF-16 units for a permanent wheel file; choose a shorter install root (user project paths are unaffected)")
        key = str(path).casefold()
        if key in seen and (not generated or seen[key] != wheel):
            raise LifecycleError("Wheel installation paths collide on Windows")
        seen[key] = wheel

    for artifact in release["files"]:
        wheel = artifact["filename"]
        if not wheel.endswith(".whl"):
            raise LifecycleError("Cannot preflight a non-wheel installation layout")
        try:
            with zipfile.ZipFile(_fs_path(Path(wheelhouse) / wheel)) as archive:
                records = archive.infolist()
                dist_infos = set()
                for item in records:
                    name = item.filename.rstrip("/") if item.is_dir() else item.filename
                    parts = components(name)
                    if stat.S_ISLNK(item.external_attr >> 16):
                        raise LifecycleError("Wheel installation links are unsupported")
                    if item.is_dir():
                        continue
                    if parts[0].endswith(".data"):
                        if len(parts) < 3:
                            raise LifecycleError("Wheel data path is incomplete")
                        schemes = {"purelib": library, "platlib": library, "scripts": scripts, "data": venv}
                        if parts[1] not in schemes:
                            raise LifecycleError("Cannot determine this wheel .data installation scheme safely")
                        destination = schemes[parts[1]].joinpath(*parts[2:])
                        admit(destination, wheel)
                        if parts[1] == "scripts":
                            admit(Path(str(destination) + ".exe"), wheel, generated=True)
                    else:
                        destination = library.joinpath(*parts)
                        admit(destination, wheel)
                    if len(parts) == 2 and parts[0].endswith(".dist-info"):
                        dist_infos.add(parts[0])
                        if parts[1] == "entry_points.txt":
                            if item.file_size > 65536:
                                raise LifecycleError("Wheel entry point metadata exceeds its supported bound")
                            parser = configparser.ConfigParser(interpolation=None)
                            parser.optionxform = str
                            parser.read_string(archive.read(item).decode("utf-8"))
                            for group in ("console_scripts", "gui_scripts"):
                                if parser.has_section(group):
                                    for name in parser[group]:
                                        if len(components(name)) != 1:
                                            raise LifecycleError("Wheel script name must be one Windows filename")
                                        admit(scripts / (name + ".exe"), wheel, generated=True)
                if len(dist_infos) != 1:
                    raise LifecycleError("Cannot determine the wheel metadata directory")
                for name in ("INSTALLER", "REQUESTED", "RECORD", "direct_url.json"):
                    admit(library / next(iter(dist_infos)) / name, wheel, generated=True)
        except (OSError, ValueError, UnicodeError, zipfile.BadZipFile, configparser.Error):
            raise LifecycleError("Cannot inspect the verified wheel installation layout") from None


_ENTRYPOINT_PATH = "venv/Scripts/codex-app-deepseek.exe"
_ENTRYPOINT_GENERATOR = "pip._vendor.distlib.scripts.ScriptMaker"


def _entrypoint_proof(directory: Path) -> dict:
    """Verify the standard distlib launcher selects only this release with -I."""
    import zipfile
    command = directory / _ENTRYPOINT_PATH
    raw = _fs_path(command).read_bytes()
    try:
        with zipfile.ZipFile(_fs_path(command)) as archive:
            offset = min(item.header_offset for item in archive.infolist())
            archive.read("__main__.py")
        # distlib appends the shebang immediately after the PE stub, without
        # inserting an earlier newline. Select the final marker before the ZIP.
        start = raw.rfind(b"#!", 0, offset)
        line = raw[start:offset]
        if start < 0 or not line.endswith(b"\n") or line.count(b"\n") != 1:
            raise ValueError
        line = line[:-1]
        if not line.endswith(b" -I"):
            raise ValueError
        interpreter = line[2:-3].decode("utf-8")
        if interpreter.startswith('"') and interpreter.endswith('"'):
            interpreter = interpreter[1:-1]
        selected = Path(interpreter)
        if not selected.is_absolute() or _fs_path(selected.resolve()) != _fs_path((directory / "venv/Scripts/python.exe").resolve()):
            raise ValueError
    except (OSError, ValueError, KeyError, IndexError, zipfile.BadZipFile):
        raise LifecycleError("Installed gateway launcher is not isolated to this release's interpreter") from None
    return {"path": _ENTRYPOINT_PATH, "sha256": hashlib.sha256(raw).hexdigest(),
            "interpreter_args": ["-I"], "generator": _ENTRYPOINT_GENERATOR}


def _install_isolated_entrypoint(directory: Path, *, run) -> dict:
    # Use pip's bundled standard launcher generator; no custom business wrapper.
    script = """import base64,csv,hashlib,importlib.metadata,io,sys
from pathlib import Path
from pip._vendor.distlib.scripts import ScriptMaker
target=Path(sys.argv[1]).resolve()
maker=ScriptMaker(None,str(target))
maker.clobber=True
maker.variants={''}
generated=maker.make('codex-app-deepseek = codex_app_deepseek.cli:main',
                     options={'interpreter_args':['-I']})
command=target/'codex-app-deepseek.exe'
if len(generated)!=1 or Path(generated[0]).resolve()!=command:
 raise RuntimeError('Unexpected standard launcher output')
# Keep installed wheel RECORD consistent with the regenerated standard entry.
distribution=importlib.metadata.distribution('codex-app-deepseek-gateway')
records=[p for p in distribution.files if str(p).replace(chr(92),'/').endswith('.dist-info/RECORD')]
if len(records)!=1:raise RuntimeError('Expected one installed RECORD')
record=Path(distribution.locate_file(records[0]))
rows=list(csv.reader(io.StringIO(record.read_text(encoding='utf-8'))))
matching=[]
for row in rows:
 if len(row)!=3:raise RuntimeError('Unexpected installed RECORD row')
 candidate=Path(distribution.locate_file(row[0])).resolve()
 if candidate.is_file() and candidate.samefile(command):matching.append(row)
if len(matching)!=1:raise RuntimeError('Expected one recorded console entry')
raw=command.read_bytes()
matching[0][1]='sha256='+base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b'=').decode()
matching[0][2]=str(len(raw))
with record.open('w',encoding='utf-8',newline='') as stream:
 csv.writer(stream,lineterminator='\\n').writerows(rows)
"""
    runtime = directory / "venv/Scripts/python.exe"
    run([str(runtime), "-I", "-c", script, str(directory / "venv/Scripts")], timeout=30)
    return _entrypoint_proof(directory)



def _same_runtime_path(left, right) -> bool:
    """Compare an explicitly absolute runtime path across Win32 spellings."""
    try:
        return (isinstance(left, (str, os.PathLike)) and Path(left).is_absolute()
                and _fs_path(Path(left).resolve()) == _fs_path(Path(right).resolve()))
    except (OSError, RuntimeError, ValueError):
        return False


def _runtime_regular_file(path: Path) -> Path:
    """Inspect bootstrap runtime files using only the standard library."""
    import stat
    selected = _fs_path(Path(path).absolute())
    for part in (selected, *selected.parents):
        if _is_link_or_reparse(part):
            raise LifecycleError("Runtime provenance must not traverse a link or junction")
    metadata = selected.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise LifecycleError("Runtime provenance must use regular files without hard links")
    return selected


def _runtime_proof(directory: Path, bootstrap_python: Path) -> dict:
    """Bind a standard private venv redirector to its selected base image."""
    base = Path(bootstrap_python)
    executable = directory / "venv/Scripts/python.exe"
    config = directory / "venv/pyvenv.cfg"
    if not base.is_absolute() or base.resolve().is_relative_to(directory.parent.resolve()):
        raise LifecycleError("Independent Python must remain outside managed releases")
    try:
        _runtime_regular_file(base)
        _runtime_regular_file(executable)
        _runtime_regular_file(config)
        raw = _fs_path(config).read_bytes()
        values = {}
        for line in raw.decode("utf-8-sig").splitlines():
            if not line.strip():
                continue
            key, separator, value = line.partition("=")
            key = key.strip().casefold()
            if not separator or key in values:
                raise ValueError("Invalid venv configuration")
            values[key] = value.strip()
        if (not _same_runtime_path(values.get("executable"), base)
                or not _same_runtime_path(values.get("home"), base.parent)
                or values.get("include-system-site-packages", "").casefold() != "false"):
            raise ValueError("Private venv selected another base environment")
        return {
            "schema_version": 1,
            "venv_executable": {"path": "venv/Scripts/python.exe", "sha256": digest(executable)},
            "venv_config": {"path": "venv/pyvenv.cfg", "sha256": hashlib.sha256(raw).hexdigest()},
            "base_executable": {"path": str(base), "sha256": digest(base)},
        }
    except (OSError, ValueError, RuntimeError):
        raise LifecycleError("Private venv/base Python provenance cannot be verified") from None


def _verified_runtime(directory: Path, receipt: dict) -> dict:
    proof = receipt.get("runtime")
    base = receipt.get("bootstrap_python")
    if not isinstance(proof, dict) or not isinstance(base, str) or not base:
        raise LifecycleError("Installed Python runtime proof is missing; explicitly reinstall this candidate")
    expected = _runtime_proof(directory, Path(base))
    if proof != expected:
        raise LifecycleError("Installed Python runtime provenance changed; explicitly reinstall the qualified release")
    return expected


def install(manifest_path: Path, manifest_sha256: str, wheelhouse: Path, python_exe: Path,
            install_root: Path, data_home: Path, *, run=_run) -> dict:
    """Install immutable wheel artifacts; never activate, read credentials or configure App."""
    release = validate_release(manifest_path, manifest_sha256, wheelhouse)
    install_root, data_home = _roots(install_root, data_home)
    identifier = manifest_sha256.lower()[:20]
    directory = install_root / "releases" / identifier
    _preflight_windows_wheel_paths(release, wheelhouse, directory)
    python = Path(python_exe).resolve()
    if not python.is_file():
        raise LifecycleError("An independent existing Python executable is required")
    # Python must survive removal of any managed release and must not be App/CDS bundled.
    if python.is_relative_to(install_root / "releases"):
        raise LifecycleError("Bootstrap Python cannot belong to a managed release")
    from .cli import resolve_service_home
    resolve_service_home(python.parent)
    check = run([str(python), "-I", "-c", "import sys,json,platform;print(json.dumps({'python':f'{sys.version_info.major}.{sys.version_info.minor}','platform':sys.platform,'bits':platform.architecture()[0],'is_venv':sys.prefix!=sys.base_prefix}))"], timeout=30)
    try:
        actual = json.loads(check.stdout)
    except (TypeError, ValueError):
        raise LifecycleError("Cannot identify independent Python") from None
    if actual != {"python": release["python_version"], "platform": "win32", "bits": "64bit", "is_venv": False}:
        raise LifecycleError("Independent Python does not match this Windows x64 release")
    receipt_path = directory / "installation.json"
    if directory.exists():
        receipt = _json(receipt_path)
        if receipt.get("manifest_sha256") != manifest_sha256.lower() or receipt.get("data_home") != str(data_home):
            raise LifecycleError("Existing installation identity differs; refusing overwrite")
        _verify_installation(directory, receipt)
        return {"status": "already_installed", "release_id": identifier, "release_dir": str(directory),
                    "command": str(directory / "venv/Scripts/codex-app-deepseek.exe")}
    directory.mkdir(parents=True)
    try:
        venv = directory / "venv"
        # Keep venv paths canonical in configuration, then bootstrap pip through
        # our isolated Python runner so venv's internal unprefixed child cannot
        # hit MAX_PATH before the actual offline wheel installation starts.
        run([str(python), "-I", "-m", "venv", "--copies", "--without-pip", str(venv)])
        runtime = venv / "Scripts" / "python.exe"
        run([str(runtime), "-I", "-m", "ensurepip", "--upgrade", "--default-pip"])
        lines = []
        for artifact in release["files"]:
            uri = (Path(wheelhouse).resolve() / artifact["filename"]).as_uri()
            lines.append(uri + " --hash=sha256:" + artifact["sha256"])
        lock = directory / "requirements.lock"
        lock.write_text("\n".join(lines) + "\n", encoding="utf-8")
        run([str(runtime), "-I", "-m", "pip", "--isolated", "--disable-pip-version-check", "install", "--no-cache-dir", "--no-index", "--no-deps", "--require-hashes", "-r", str(lock)])
        run([str(runtime), "-I", "-m", "pip", "--isolated", "check"])
        probe = run([str(runtime), "-I", "-c", "import importlib.metadata;print(importlib.metadata.version('codex-app-deepseek-gateway'))"], timeout=30)
        if probe.stdout.strip() != release["version"]:
            raise LifecycleError("Installed gateway version does not match manifest")
        runtime_proof = _runtime_proof(directory, python)
        entrypoint = _install_isolated_entrypoint(directory, run=run)
        command = venv / "Scripts/codex-app-deepseek.exe"
        # Verify the public installed entrypoint itself, not only distribution metadata.
        entry_probe = run([str(command), "--version"], timeout=30)
        if entry_probe.stdout.strip() != release["version"]:
            raise LifecycleError("Installed gateway command does not match manifest")
        shutil.copyfile(manifest_path, directory / "release.json")
        bootstrap = install_root / "bootstrap" / (identifier + ".whl")
        bootstrap.parent.mkdir(parents=True, exist_ok=True)
        gateway_sha = next(item["sha256"] for item in release["files"] if item["filename"] == release["gateway_wheel"])
        if bootstrap.exists() and digest(bootstrap) != gateway_sha:
            raise LifecycleError("Existing recovery bootstrap differs; refusing overwrite")
        if not bootstrap.exists():
            shutil.copyfile(Path(wheelhouse) / release["gateway_wheel"], bootstrap)
        # Package provenance stays pinned; lock paths do not require retained wheelhouse.
        receipt = {"schema_version": 1, "project": PROJECT, "release_id": identifier,
                   "manifest_sha256": manifest_sha256.lower(), "data_home": str(data_home),
                   "bootstrap_python": str(python), "bootstrap_wheel": str(bootstrap), "bootstrap_sha256": gateway_sha,
                   "version": release["version"], "entrypoint": entrypoint, "runtime": runtime_proof,
                   "files": _inventory(directory)}
        _write(receipt_path, receipt)
    except Exception:
        # This directory was created by this invocation and was never activated.
        shutil.rmtree(_fs_path(directory))
        raise
    return {"status": "installed_not_activated", "release_id": identifier, "release_dir": str(directory),
            "manifest_sha256": manifest_sha256.lower(), "version": release["version"],
            "command": str(directory / "venv/Scripts/codex-app-deepseek.exe")}


def _verify_installation(directory: Path, receipt: dict) -> None:
    if receipt.get("schema_version") != 1 or receipt.get("project") != PROJECT or receipt.get("release_id") != directory.name:
        raise LifecycleError("Installation receipt ownership mismatch")
    if digest(directory / "release.json") != receipt.get("manifest_sha256"):
        raise LifecycleError("Installed release manifest changed")
    bootstrap = _within(Path(receipt.get("bootstrap_wheel", "")), directory.parent.parent / "bootstrap")
    if not bootstrap.is_file() or digest(bootstrap) != receipt.get("bootstrap_sha256"):
        raise LifecycleError("Pinned recovery bootstrap is missing or changed")
    _verified_runtime(directory, receipt)
    if receipt.get("entrypoint") != _entrypoint_proof(directory):
        raise LifecycleError("Installed launcher proof is missing or changed; explicitly reinstall the qualified release")
    inventory = receipt.get("files")
    if not isinstance(inventory, dict) or _inventory(directory) != inventory:
        raise LifecycleError("Installation contains modified, missing or unknown files")


def _active(install_root: Path) -> tuple[Path, dict, dict]:
    root = _home(install_root)
    active = _json(root / "active.json")
    if active.get("project") != PROJECT or not re.fullmatch(r"[0-9a-f]{20}", str(active.get("release_id", ""))):
        raise LifecycleError("Active installation identity invalid")
    directory = _within(root / "releases" / active["release_id"], root / "releases")
    receipt_path = directory / "installation.json"
    if digest(receipt_path) != active.get("receipt_sha256"):
        raise LifecycleError("Active installation receipt changed")
    receipt = _json(receipt_path)
    if receipt.get("project") != PROJECT:
        raise LifecycleError("Active installation belongs to another project")
    _roots(root, Path(receipt["data_home"]))
    return directory, receipt, active


def activate(install_root: Path, release_id: str) -> dict:
    root = _home(install_root)
    with _operation_lease(root):
        return _activate_locked(root, release_id)


def _activate_locked(install_root: Path, release_id: str) -> dict:
    root = _home(install_root)
    if not re.fullmatch(r"[0-9a-f]{20}", release_id):
        raise LifecycleError("Invalid release id")
    directory = _within(root / "releases" / release_id, root / "releases")
    receipt = _json(directory / "installation.json")
    _verify_installation(directory, receipt)
    data = _home(Path(receipt["data_home"]))
    if (data / "service.json").exists():
        raise LifecycleError("Service record exists; reconcile/stop the recorded service before activation")
    from .gateway_policy import ensure_app_policy, ServiceConfigError
    try:
        _, policy = ensure_app_policy(data)
    except ServiceConfigError as error:
        raise LifecycleError(str(error)) from None
    _write(root / "active.json", {"project": PROJECT, "release_id": release_id,
                                  "receipt_sha256": digest(directory / "installation.json")})
    return {"status": "activated_not_started", "release_id": release_id, "data_home": str(data),
            "command": str(directory / "venv/Scripts/codex-app-deepseek.exe"), "gateway_policy": policy}


# Version provenance is the previously verified official signed binary, never
# the caller's requested baseline. Unknown updates require fresh qualification.
KNOWN_OFFICIAL_NATIVE_VERSIONS = {
    "0c09e45bc76433756eb2d492f6db0ce6f269967e6618f4a6ae73e2fa239694b8": "0.165.0-alpha.1.2",
    "e757246b4de87359e660647fa9514955a0971ff6a30affdd8c145d1d5ece7b84": "0.163.0-alpha.11.1",
}


def observe_app(*, run=_run) -> dict:
    if sys.platform != "win32":
        raise LifecycleError("Automatic App discovery requires Windows")
    code = "$ProgressPreference='SilentlyContinue';$p=@(Get-AppxPackage -Name OpenAI.Codex);if($p.Count -ne 1){throw 'Expected one current-user Codex package'};@{app_version=$p[0].Version.ToString();location=$p[0].InstallLocation;publisher_id=$p[0].PublisherId;signature_kind=$p[0].SignatureKind.ToString();status=$p[0].Status.ToString()}|ConvertTo-Json -Compress"
    response = _powershell(code, run=run)
    try:
        package = json.loads(response.stdout)
        if (not isinstance(package, dict) or package.get("publisher_id") != "2p2nqsd0c76g0"
                or package.get("signature_kind") != "Store" or package.get("status") != "Ok"
                or not isinstance(package.get("app_version"), str)
                or not isinstance(package.get("location"), str)):
            raise ValueError("Package identity differs")
    except (TypeError, ValueError):
        raise LifecycleError("Cannot observe the expected signed current-user Codex package") from None
    native = Path(package["location"]) / "app" / "resources" / "codex.exe"
    native_sha = digest(native)
    version = KNOWN_OFFICIAL_NATIVE_VERSIONS.get(native_sha)
    return {"app_version": package["app_version"], "native_version": version,
            "native_sha256": native_sha,
            "native_version_source": "hash-verified-reference" if version else "unrecognized-official-sha256"}


def diagnose(install_root: Path, *, observed_baseline: dict | None = None) -> dict:
    directory, receipt, _ = _active(install_root)
    _verify_installation(directory, receipt)
    expected = _json(directory / "release.json")["app_baseline"]
    observed = observe_app() if observed_baseline is None else observed_baseline
    differences = [key for key in ("app_version", "native_version", "native_sha256")
                   if str(observed.get(key, "")).removeprefix("codex-cli ") != str(expected[key]).removeprefix("codex-cli ")]
    return {"status": "compatible" if not differences else "compatibility_revalidation_required",
            "release_id": receipt["release_id"], "expected_baseline": expected,
            "observed_baseline": observed, "mismatched_fields": differences,
            "model_request_performed": False, "credentials_read": False}


def _powershell(code: str, *, run=_run):
    binary = Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32/WindowsPowerShell/v1.0/powershell.exe"
    encoded = base64.b64encode(code.encode("utf-16-le")).decode("ascii")
    return run([str(binary), "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded], timeout=30)



_TASK_PRINCIPAL_SID = r"""
function Resolve-CadsgTaskSid([string]$Value) {
 if([string]::IsNullOrWhiteSpace($Value)){throw 'Task principal cannot be resolved'}
 try {
  if($Value -match '^S-'){
   return (New-Object Security.Principal.SecurityIdentifier -ArgumentList $Value).Value
  }
  return (New-Object Security.Principal.NTAccount -ArgumentList $Value).Translate([Security.Principal.SecurityIdentifier]).Value
 } catch {throw 'Task principal cannot be resolved'}
}
function Test-CadsgTaskOwner($Definition,[string]$Marker,[string]$Sid) {
 return ($Definition.RegistrationInfo.Description -eq $Marker -and
         (Resolve-CadsgTaskSid $Definition.Principal.UserId) -eq $Sid -and
         $Definition.Principal.LogonType -eq 3 -and $Definition.Principal.RunLevel -eq 0)
}
"""

def _task(root: Path, directory: Path, data: Path, port: int, operation: str, at_logon: bool = False, *, run=_run) -> dict:
    if sys.platform != "win32":
        raise LifecycleError("User tasks require Windows")
    name = "CodexAppDeepSeek-Service-" + hashlib.sha256(str(root).casefold().encode()).hexdigest()[:16]
    marker = PROJECT + " managed service " + str(root)
    request = {"name": name, "marker": marker, "operation": operation, "at_logon": at_logon,
               "executable": str(directory / "venv/Scripts/python.exe"), "cwd": str(directory),
               "arguments": subprocess.list2cmdline(["-I", "-m", "codex_app_deepseek.cli", "serve", "--home", str(data), "--port", str(port)])}
    encoded = base64.b64encode(json.dumps(request).encode()).decode()
    script = _TASK_PRINCIPAL_SID + r"""$ErrorActionPreference='Stop';[Console]::OutputEncoding=[Text.Encoding]::UTF8
$r=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__REQUEST__'))|ConvertFrom-Json
$id=[Security.Principal.WindowsIdentity]::GetCurrent();$sid=$id.User.Value
$s=New-Object -ComObject Schedule.Service;$s.Connect();$f=$s.GetFolder('\');$old=$null
try{$old=$f.GetTask($r.name)}catch{}
if($null -ne $old -and -not (Test-CadsgTaskOwner $old.Definition $r.marker $sid)){throw 'Task ownership mismatch'}
if($r.operation -eq 'remove'){
 if($null -ne $old){$f.DeleteTask($r.name,0)}
 @{task_name=$r.name;removed=($null -ne $old)}|ConvertTo-Json -Compress;exit 0
}
if($null -ne $old -and $old.State -eq 4){throw 'Existing managed task still running; reconcile it before replay'}
if((Get-Process -Id $PID).SessionId -eq 0){throw 'Start requires the intended current-user interactive session'}
$t=$s.NewTask(0);$t.RegistrationInfo.Description=$r.marker
$t.Principal.UserId=$sid;$t.Principal.LogonType=3;$t.Principal.RunLevel=0
$t.Settings.Enabled=$true;$t.Settings.AllowDemandStart=$true;$t.Settings.ExecutionTimeLimit='PT0S'
$t.Settings.DisallowStartIfOnBatteries=$false;$t.Settings.StopIfGoingOnBatteries=$false;$t.Settings.MultipleInstances=2
if($r.at_logon){$trigger=$t.Triggers.Create(9);$trigger.UserId=$sid}
$a=$t.Actions.Create(0);$a.Path=$r.executable;$a.Arguments=$r.arguments;$a.WorkingDirectory=$r.cwd
$sddl='D:P(A;;GA;;;SY)(A;;GA;;;'+$sid+')'
$registered=$f.RegisterTaskDefinition($r.name,$t,6,$sid,$null,3,$sddl)
$instance=$registered.RunEx($null,4,(Get-Process -Id $PID).SessionId,$null)
@{task_name=$r.name;sid=$sid;session_id=(Get-Process -Id $PID).SessionId;admission='accepted';instance_guid=$instance.InstanceGuid;at_logon=[bool]$r.at_logon}|ConvertTo-Json -Compress
""".replace("__REQUEST__", encoded)
    return json.loads(_powershell(script, run=run).stdout)


def health(port: int, *, token_provider=None) -> dict:
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        raise LifecycleError("Invalid loopback health port")
    if token_provider is None:
        from .credentials import LOCAL_TARGET, read
        token_provider = lambda: read(LOCAL_TARGET)
    request = urllib.request.Request(f"http://127.0.0.1:{port}/healthz", headers={"Authorization": "Bearer " + token_provider()})
    # Local token must never be sent through an inherited system/environment proxy.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    with opener.open(request, timeout=5) as response:
        raw = response.read(65537)
    if len(raw) > 65536:
        raise LifecycleError("Unexpected local health response size")
    result = json.loads(raw)
    if not isinstance(result, dict) or result.get("status") != "ready":
        raise LifecycleError("Local gateway is not ready")
    from .gateway_policy import _config_names, DEFAULT_PROVIDERS, ServiceConfigError
    try:
        models = _config_names(result, "accepted_models", ())
        providers = _config_names(result, "allowed_providers", ())
    except ServiceConfigError:
        raise LifecycleError("Local gateway health has invalid model/provider names") from None
    fingerprint = result.get("config_sha256")
    if (not isinstance(result.get("model"), str) or not result["model"]
            or not set(providers) <= set(DEFAULT_PROVIDERS)
            or (fingerprint is not None and (not isinstance(fingerprint, str)
                or re.fullmatch("[0-9a-f]{64}", fingerprint) is None))):
        raise LifecycleError("Local gateway health has invalid policy identity")
    return {"status": "ready", "model": result["model"], "accepted_models": list(models),
            "allowed_providers": list(providers), "config_sha256": fingerprint,
            "upstream_model_request_performed": False}


def _process_identity(process, record: dict, directory: Path, data: Path, owner: str) -> None:
    runtime = _verified_runtime(directory, _json(directory / "installation.json"))
    expected = directory / "venv/Scripts/python.exe"
    if not _same_runtime_path(record.get("executable"), expected):
        raise LifecycleError("Recorded service executable is not this installed release")
    port = record.get("port")
    if type(port) is not int or not 1024 <= port <= 65535 or record.get("host", "127.0.0.1") != "127.0.0.1":
        raise LifecycleError("Recorded service has an invalid loopback listener")
    actual_birth = process.create_time()
    if (process.pid != record["pid"] or type(actual_birth) not in (int, float)
            or not math.isfinite(actual_birth) or abs(actual_birth - record["created"]) > 0.001):
        raise LifecycleError("Service PID was reused or its creation time differs")
    image = process.exe()
    if not any(_same_runtime_path(image, candidate) for candidate in
               (expected, runtime["base_executable"]["path"])):
        raise LifecycleError("Service executable differs from the verified private venv/base runtime")
    if process.username().casefold() != owner.casefold():
        raise LifecycleError("Recorded service belongs to another Windows user")
    argv = process.cmdline()
    if (not isinstance(argv, (list, tuple)) or len(argv) != 9
            or not _same_runtime_path(argv[0], expected)
            or list(argv[1:6]) != ["-I", "-m", "codex_app_deepseek.cli", "serve", "--home"]
            or not _same_runtime_path(argv[6], data)
            or list(argv[7:]) != ["--port", str(port)]):
        raise LifecycleError("Recorded process is not the isolated private gateway command/home/port")


def _owned_process(directory: Path, data: Path, *, process_factory=None):
    record_path = data / "service.json"
    if not record_path.exists():
        return None, None
    import psutil
    record = _json(record_path)
    pid, created = record.get("pid"), record.get("created")
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 0 or not isinstance(created, (int, float)) or isinstance(created, bool) or not math.isfinite(created):
        raise LifecycleError("Service process identity record is invalid")
    factory = process_factory or psutil.Process
    try:
        process = factory(pid)
        _process_identity(process, record, directory, data, factory(os.getpid()).username())
        return process, digest(record_path)
    except psutil.NoSuchProcess:
        return None, digest(record_path)


def _owned_health(process, data: Path, port: int) -> dict:
    record = _json(data / "service.json")
    import psutil
    logical = record.get("executable")
    if not isinstance(logical, str) or not Path(logical).is_absolute():
        raise LifecycleError("Owned service has no absolute private interpreter")
    _process_identity(process, record, Path(logical).parent.parent.parent, data, psutil.Process().username())
    if record.get("port") != port or record.get("host", "127.0.0.1") != "127.0.0.1":
        raise LifecycleError("Owned service uses a different listener than requested")
    connections = process.net_connections(kind="tcp")
    if not any(connection.status == "LISTEN" and connection.laddr
               and connection.laddr.ip == "127.0.0.1" and connection.laddr.port == port
               for connection in connections):
        raise LifecycleError("Expected loopback listener is not owned by the recorded gateway process")
    return health(port)


@contextmanager
def _operation_lease(root: Path):
    """Serialize this installation's short start/stop operations, never its App."""
    from .cli import ServiceConfigError, _service_lease
    with ExitStack() as resources:
        try:
            resources.enter_context(_service_lease(root, lock_name="lifecycle.lock"))
        except ServiceConfigError:
            raise LifecycleError("Another lifecycle operation owns this installation; no task change was attempted") from None
        yield


def start(install_root: Path, port: int = 42817, *, at_logon: bool = False, observed_baseline=None) -> dict:
    root = _home(install_root)
    with _operation_lease(root):
        return _start_locked(root, port, at_logon=at_logon, observed_baseline=observed_baseline)


def _start_locked(install_root: Path, port: int = 42817, *, at_logon: bool = False, observed_baseline=None) -> dict:
    root = _home(install_root)
    directory, receipt, _ = _active(root)
    if diagnose(root, observed_baseline=observed_baseline)["status"] != "compatible":
        raise LifecycleError("App/native version combination changed; revalidation required before start")
    data = _home(Path(receipt["data_home"]))
    from .cli import load_service_config
    load_service_config(data)
    if not isinstance(port, int) or isinstance(port, bool) or not 1024 <= port <= 65535:
        raise LifecycleError("Use an unprivileged loopback port")
    if (data / "service.json").exists():
        process, _ = _owned_process(directory, data)
        if process is not None:
            return {"status": "already_running", "health": _owned_health(process, data, port)}
        raise LifecycleError("Stale service record requires an explicit stop/reconciliation first")
    admission = _task(root, directory, data, port, "start", at_logon)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if (data / "service.json").exists():
            process, _ = _owned_process(directory, data)
            if process is not None:
                try:
                    return {"status": "started", "task": admission, "health": _owned_health(process, data, port)}
                except Exception:
                    pass
        time.sleep(0.2)
    raise LifecycleError(f"Start outcome is not confirmed for {admission['task_name']}; inspect it and the service record, do not blindly replay")


def stop(install_root: Path, *, process_factory=None, remove_task=True) -> dict:
    root = _home(install_root)
    with _operation_lease(root):
        return _stop_locked(root, process_factory=process_factory, remove_task=remove_task)


def _stop_locked(install_root: Path, *, process_factory=None, remove_task=True) -> dict:
    root = _home(install_root)
    directory, receipt, _ = _active(root)
    data = _home(Path(receipt["data_home"]))
    process, record_hash = _owned_process(directory, data, process_factory=process_factory)
    if process is not None:
        process.terminate()
        try:
            process.wait(timeout=10)
        except Exception:
            # Do not guess at children or escalate to killing arbitrary process trees.
            raise LifecycleError("Owned gateway did not stop; no release files removed") from None
    # A new service may start as soon as the old process exits. Hold the same
    # lease used by serve while checking/removing its record and scheduled task.
    # Acquire only after wait: the old process owns this lease until it exits.
    from .cli import ServiceConfigError, _service_lease
    from .app_config import _private
    data.mkdir(parents=True, exist_ok=True)
    _private(data, directory=True)
    try:
        with _service_lease(data):
            record_path = data / "service.json"
            if record_path.exists():
                if record_hash is None or digest(record_path) != record_hash:
                    raise LifecycleError("Service record changed while stopping; refusing to remove it")
                record_path.unlink()
            task = _task(root, directory, data, 42817, "remove") if remove_task else None
    except ServiceConfigError:
        raise LifecycleError("The data home is now owned by another gateway; its record and task were preserved") from None
    return {"status": "stopped" if process is not None else "already_stopped", "task": task}


def _checkpoint_details(path: Path) -> dict:
    """Verify immutable restore inputs without changing the config or manifest."""
    from . import app_config as ac
    path = ac._regular_path(path, "checkpoint manifest")
    manifest = _json(path)
    version = manifest.get("schema_version")
    if type(version) is not int or version not in {1, 2}:
        raise LifecycleError("Unsupported configuration checkpoint schema")
    immutable_fields = {"schema_version", "config_path", "was_present", "before_sha256",
                        "applied_sha256", "backup_file", "changed_settings", "created_at"}
    if version == 2:
        immutable_fields.add("applied_file")
    mutable_fields = {"state", "restored_sha256", "restored_at", "restore_input_file", "restore_input_sha256", "restored_file", "restore_prepared_at"}
    if set(manifest) - immutable_fields - mutable_fields or immutable_fields - set(manifest):
        raise LifecycleError("Unknown or missing configuration checkpoint fields")
    state = manifest.get("state")
    if state not in {"applied", "restore_prepared", "restored"}:
        raise LifecycleError("Only completed configuration checkpoints may be registered")
    if type(manifest["was_present"]) is not bool:
        raise LifecycleError("Invalid checkpoint existence flag")
    if not isinstance(manifest["applied_sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", manifest["applied_sha256"]):
        raise LifecycleError("Invalid checkpoint snapshot hash")
    if not isinstance(manifest["created_at"], str) or not manifest["created_at"]:
        raise LifecycleError("Invalid checkpoint creation time")
    target = manifest["config_path"]
    if not isinstance(target, str) or not Path(target).is_absolute() or Path(target).suffix.lower() != ".toml":
        raise LifecycleError("Checkpoint must name an absolute TOML config path")
    config = ac._regular_path(target, "checkpoint config")
    original = None
    if manifest["was_present"]:
        if manifest["backup_file"] != "original.toml":
            raise LifecycleError("Invalid checkpoint backup location")
        original = ac._regular_path(path.parent / "original.toml", "checkpoint original").read_bytes()
        if ac._digest(original) != manifest["before_sha256"]:
            raise LifecycleError("Checkpoint original snapshot hash mismatch")
    elif manifest["before_sha256"] is not None or manifest["backup_file"] is not None:
        raise LifecycleError("Invalid checkpoint absent-original metadata")
    applied = None
    if version == 2:
        if manifest["applied_file"] != "applied.toml":
            raise LifecycleError("Invalid checkpoint applied snapshot location")
        applied = ac._regular_path(path.parent / "applied.toml", "checkpoint applied").read_bytes()
        if ac._digest(applied) != manifest["applied_sha256"]:
            raise LifecycleError("Checkpoint applied snapshot hash mismatch")
        ac._three_way_bytes(original, applied, applied, manifest["changed_settings"])
    else:
        declared = manifest["changed_settings"]
        if (not isinstance(declared, list) or any(not isinstance(x, str) or x not in ac._MANAGED_GROUPS for x in declared)
                or len(set(declared)) != len(declared)):
            raise LifecycleError("Invalid legacy checkpoint owned settings")
    if state == "applied" and any(name in manifest for name in mutable_fields - {"state"}):
        raise LifecycleError("Applied checkpoint contains restored-state metadata")
    if state == "restored":
        restored_hash = manifest.get("restored_sha256", manifest["before_sha256"])
        if restored_hash is not None and (not isinstance(restored_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", restored_hash)):
            raise LifecycleError("Invalid restored config hash")
        if not isinstance(manifest.get("restored_at"), str) or not manifest["restored_at"]:
            raise LifecycleError("Restored checkpoint has no completion time")
    restore_evidence = ac._restore_evidence(path, manifest, original, applied)
    if state == "restore_prepared" and restore_evidence is None:
        raise LifecycleError("Prepared restore requires verified input/output evidence")
    immutable = {name: manifest[name] for name in sorted(immutable_fields)}
    return {"path": path, "manifest": manifest, "immutable": immutable,
            "original": original, "applied": applied, "config": config, "restore_evidence": restore_evidence}


def _immutable_digest(value: dict) -> str:
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _confirm_restored(details: dict) -> None:
    """Confirm an externally completed restore, not just a mutable state label."""
    from . import app_config as ac
    # New checkpoints capture the actual C and R. Their exact three-way
    # recomputation permits later legitimate restores/edits without rehashing.
    if details["restore_evidence"] is not None:
        return
    current = ac._read_optional(details["config"])
    actual = ac._digest(current) if current is not None else None
    expected = details["manifest"].get("restored_sha256", details["manifest"]["before_sha256"])
    if actual != expected:
        raise LifecycleError("Unreconciled restored checkpoint does not match the current config")
    if details["applied"] is None:
        if actual != details["manifest"]["before_sha256"]:
            raise LifecycleError("Legacy restore does not match its exact original snapshot")
    else:
        planned = ac._three_way_bytes(details["original"], details["applied"], current,
                                      details["manifest"]["changed_settings"])
        import tomllib
        read = lambda raw: {} if raw is None else tomllib.loads(raw.decode("utf-8-sig"))
        if not ac._equal(read(planned), read(current)):
            raise LifecycleError("Checkpoint claims restore but owned settings remain applied")


def _confirm_restore_prepared(details: dict) -> None:
    from . import app_config as ac
    captured, restored = details["restore_evidence"]
    current = ac._read_optional(details["config"])
    if current != captured and current != restored:
        raise LifecycleError("Config changed after captured restore preparation")


def _checkpoint_entry(details: dict) -> dict:
    manifest = details["manifest"]
    return {"path": str(details["path"]), "immutable_checkpoint": details["immutable"],
            "immutable_sha256": _immutable_digest(details["immutable"]),
            "sha256": digest(details["path"]), "state": manifest["state"]}


def _write_registry(path: Path, registry: dict) -> None:
    from .app_config import _atomic_write
    _atomic_write(path, (json.dumps(registry, ensure_ascii=False, indent=2) + "\n").encode())


def _read_registry(root: Path, data: Path) -> tuple[dict, dict, bool]:
    """Read all anchors first; only provable applied->restored transitions reconcile."""
    from .app_config import _equal, _regular_path
    registry = _json(root / "checkpoints.json")
    legacy = "schema_version" not in registry
    expected_fields = {"project", "checkpoints"} | (set() if legacy else {"schema_version"})
    if (set(registry) != expected_fields or registry.get("project") != PROJECT
            or not isinstance(registry.get("checkpoints"), list)
            or not legacy and (type(registry.get("schema_version")) is not int or registry["schema_version"] != 2)):
        raise LifecycleError("Checkpoint registry ownership or schema mismatch")
    details_by_path, seen, changed = {}, set(), legacy
    backups = _within(data / "config-backups", data)
    for entry in registry["checkpoints"]:
        fields = {"path", "sha256", "state"} | (set() if legacy else {"immutable_checkpoint", "immutable_sha256"})
        if not isinstance(entry, dict) or set(entry) != fields or not isinstance(entry.get("path"), str):
            raise LifecycleError("Invalid checkpoint registry entry")
        if entry["state"] not in {"applied", "restore_prepared", "restored"} or not re.fullmatch(r"[0-9a-f]{64}", str(entry["sha256"])):
            raise LifecycleError("Invalid checkpoint registry state or hash")
        raw = _regular_path(entry["path"], "registered checkpoint")
        path = _within(raw, backups)
        if path.name != "checkpoint.json" or str(path).casefold() in seen:
            raise LifecycleError("Duplicate or invalid registered checkpoint path")
        seen.add(str(path).casefold())
        details = _checkpoint_details(path)
        details_by_path[str(path)] = details
        current_hash = digest(path)
        manifest = details["manifest"]
        if legacy:
            if current_hash != entry["sha256"] or manifest["state"] != entry["state"]:
                raise LifecycleError("Legacy registered checkpoint changed; immutable anchor cannot be reconstructed")
            if entry["state"] == "restored":
                _confirm_restored(details)
            entry.update(_checkpoint_entry(details))
            continue
        anchor = entry["immutable_checkpoint"]
        if (not isinstance(anchor, dict) or _immutable_digest(anchor) != entry["immutable_sha256"]
                or not _equal(anchor, details["immutable"])):
            raise LifecycleError("Registered checkpoint immutable inputs changed; no rollback performed")
        if current_hash == entry["sha256"]:
            if manifest["state"] == "restore_prepared":
                _confirm_restore_prepared(details)
            if manifest["state"] != entry["state"]:
                raise LifecycleError("Registered checkpoint state mismatch")
            continue
        transition = (entry["state"], manifest["state"])
        if transition not in {("applied", "restore_prepared"), ("applied", "restored"), ("restore_prepared", "restored")}:
            raise LifecycleError("Registered checkpoint changed outside a valid restore transition")
        if manifest["state"] == "restore_prepared":
            _confirm_restore_prepared(details)
        else:
            _confirm_restored(details)
        entry.update(sha256=current_hash, state=manifest["state"])
        changed = True
    registry["schema_version"] = 2
    return registry, details_by_path, changed


def register_checkpoint(install_root: Path, checkpoint_path: Path) -> dict:
    from .app_config import _private, _regular_path
    root = _home(install_root)
    _, receipt, _ = _active(root)
    data = _home(Path(receipt["data_home"]))
    backups = _within(data / "config-backups", data)
    path = _within(_regular_path(checkpoint_path, "checkpoint manifest"), backups)
    if path.name != "checkpoint.json":
        raise LifecycleError("Checkpoint registration requires the owned checkpoint manifest")
    details = _checkpoint_details(path)
    from .native_app_tools import POLICY_KEY
    if POLICY_KEY in details["manifest"]["changed_settings"]:
        raise LifecycleError("Native MCP policy checkpoints are owned by their native installation receipt")
    if details["manifest"]["state"] == "restore_prepared":
        raise LifecycleError("Finish the prepared restore before new registration")
    if details["manifest"]["state"] == "restored":
        _confirm_restored(details)
    registry_path = root / "checkpoints.json"
    registry, _, _ = _read_registry(root, data) if registry_path.exists() else (
        {"schema_version": 2, "project": PROJECT, "checkpoints": []}, {}, False)
    if any(Path(item["path"]) == path for item in registry["checkpoints"]):
        raise LifecycleError("Checkpoint already registered; refusing to rewrite its trust anchor")
    _private(path.parent, directory=True)
    for file in [path, *(path.parent / name for name in ("original.toml", "applied.toml", "restore-input.toml", "restored.toml") if (path.parent / name).exists())]:
        _private(file)
    registry["checkpoints"].append(_checkpoint_entry(details))
    _write_registry(registry_path, registry)
    return {"status": "registered", "checkpoint": str(path)}


def rollback(install_root: Path) -> dict:
    from .native_app_tools import rollback_admission
    root = _home(install_root)
    _, receipt, _ = _active(root)
    data = _home(Path(receipt["data_home"]))
    with rollback_admission(data) as native_rollback:
        return _rollback_registered(root, native_rollback)


def _rollback_registered(install_root: Path, native_rollback) -> dict:
    from . import app_config as ac
    root = _home(install_root)
    _, receipt, _ = _active(root)
    data = _home(Path(receipt["data_home"]))
    registry_path = root / "checkpoints.json"
    if not registry_path.exists():
        native = native_rollback()
        if native["status"] != "not_configured":
            return {"status": "rolled_back", "restored_checkpoints": [], "native_app_tools": native,
                    "persistent_data_deleted": False, "credentials_deleted": False}
        raise LifecycleError("No registered owned checkpoint; no App configuration was changed")
    registry, details, reconciled = _read_registry(root, data)
    records = registry["checkpoints"]
    if reconciled:
        _write_registry(registry_path, registry)
    # Validate the entire reverse chain in memory before undoing the first step.
    virtual = {}
    for item in reversed(records):
        if item["state"] == "restored":
            continue
        detail = details[item["path"]]
        config = detail["config"]
        current = virtual[config] if config in virtual else ac._read_optional(config)
        if detail["manifest"]["state"] == "restore_prepared":
            captured, restored = detail["restore_evidence"]
            if current != captured and current != restored:
                raise LifecycleError("Earlier checkpoint conflicts with a prepared restore; no rollback performed")
            virtual[config] = restored
        elif detail["applied"] is None:
            current_hash = ac._digest(current) if current is not None else None
            if current_hash != detail["manifest"]["applied_sha256"]:
                raise LifecycleError("Legacy checkpoint cannot restore a changed config; no rollback performed")
            virtual[config] = detail["original"]
        else:
            virtual[config] = ac._three_way_bytes(detail["original"], detail["applied"], current,
                                                detail["manifest"]["changed_settings"])
    # Native software owns its own fields; remove only unchanged project-created
    # objects before the existing reverse provider checkpoint chain runs.
    native_tools = native_rollback()
    restored = []
    try:
        for item in reversed(records):
            if item["state"] == "restored":
                continue
            path = Path(item["path"])
            if digest(path) != item["sha256"]:
                raise LifecycleError("Checkpoint changed after rollback preflight")
            ac.restore(path)
            restored.append(str(path))
            item["state"] = "restored"; item["sha256"] = digest(path)
            _write_registry(registry_path, registry)
    except Exception as error:
        if restored:
            raise LifecycleError(f"Rollback stopped after {len(restored)} completed checkpoint(s); reconcile recorded restore states before continuing") from error
        raise
    return {"status": "rolled_back", "restored_checkpoints": restored,
            "native_app_tools": native_tools, "reconciled_completed_restores": reconciled,
            "persistent_data_deleted": False, "credentials_deleted": False}


def prepare_uninstall(install_root: Path, *, remove_task=True, process_factory=None, retry_native_cleanup=False) -> dict:
    """Run under the production venv, then exit before external file deletion."""
    root = _home(install_root)
    with _operation_lease(root):
        directory, receipt, _ = _active(root)
        _verify_installation(directory, receipt)
        # Native admission precedes every config/service mutation. Existing native
        # code owns all namespace cleanup; an active App/workload or wrong token
        # cannot be disguised by first stopping the gateway or undoing its config.
        from .native_cleanup import admission, NativeCleanupError
        try:
            with admission(root, directory, receipt, retry_unconfirmed=retry_native_cleanup) as native:
                from .native_app_tools import receipt_paths
                if (root / "checkpoints.json").exists() or receipt_paths(_home(Path(receipt["data_home"])))[1].exists():
                    rollback(root)
                native_result = native.execute(retry_unconfirmed=retry_native_cleanup)
                stopped = _stop_locked(root, process_factory=process_factory, remove_task=remove_task)
                data = _home(Path(receipt["data_home"]))
                if (data / "service.json").exists():
                    raise LifecycleError("Service identity remains unresolved; uninstall is not prepared")
                proof = {"schema_version": 1, "project": PROJECT, "stage": "prepared", "release_id": directory.name,
                         "receipt_sha256": digest(directory / "installation.json"),
                         "active_sha256": digest(root / "active.json"), "data_home": str(data),
                         "bootstrap_wheel": receipt["bootstrap_wheel"], "bootstrap_sha256": receipt["bootstrap_sha256"],
                         "service_result": stopped["status"], "native_cleanup": native_result,
                         "installation": receipt, "nonce": os.urandom(16).hex()}
                path = root / "uninstall-ready.json"
                _write(path, proof)
                return {"status": "ready_for_external_cleanup", "receipt": str(path), "receipt_sha256": digest(path),
                        "bootstrap_python": receipt["bootstrap_python"], "native_cleanup": native_result}
        except NativeCleanupError as error:
            raise LifecycleError(str(error)) from error


def finalize_uninstall(install_root: Path, proof_sha256: str) -> dict:
    """Run from independent Python. Keep recovery wheel/data; never erase user data."""
    root = _home(install_root)
    proof_path = root / "uninstall-ready.json"
    if digest(proof_path) != proof_sha256:
        raise LifecycleError("Uninstall preparation receipt changed")
    proof = _json(proof_path)
    if proof.get("project") != PROJECT or proof.get("stage") not in {"prepared", "retired", "removed"}:
        raise LifecycleError("Uninstall is not prepared for this project")
    if not re.fullmatch(r"[0-9a-f]{20}", str(proof.get("release_id", ""))) or not re.fullmatch(r"[0-9a-f]{32}", str(proof.get("nonce", ""))):
        raise LifecycleError("Uninstall identity is invalid")
    cleanup = proof.get("native_cleanup")
    if cleanup is not None:
        if (not isinstance(cleanup, dict)
                or cleanup.get("status") not in {"native_cleanup_completed", "not_app_bound", "app_home_not_provisioned"}
                or cleanup.get("native_command_performed") is not (cleanup.get("status") == "native_cleanup_completed")):
            raise LifecycleError("Native uninstall cleanup is not verified")
        if cleanup["status"] == "native_cleanup_completed":
            cleanup_path = root / "native-uninstall.json"
            if (cleanup.get("receipt") != str(cleanup_path)
                    or digest(cleanup_path) != cleanup.get("receipt_sha256")
                    or _json(cleanup_path).get("status") != "native_cleanup_completed"):
                raise LifecycleError("Native cleanup receipt changed before release retirement")
    elif (Path(proof["data_home"]) / "app-binding.json").exists():
        raise LifecycleError("Bound App uninstall needs native cleanup admission before release retirement")
    directory = root / "releases" / proof["release_id"]
    retired = _within(root / "retired" / proof["nonce"] / proof["release_id"], root / "retired")
    if proof["stage"] == "removed":
        return {"status": "already_uninstalled", "preserved_data_home": proof["data_home"], "credentials_deleted": False}
    if proof["stage"] == "prepared" and not directory.exists() and retired.exists():
        # Recover a crash after atomic retirement but before receipt publication.
        if digest(retired / "installation.json") != proof["receipt_sha256"]:
            raise LifecycleError("Retired installation differs from preparation receipt")
        active_path = root / "active.json"
        if active_path.exists():
            if digest(active_path) != proof["active_sha256"]:
                raise LifecycleError("Active installation changed during recovery")
            active_path.unlink()
        proof["stage"] = "retired"; _write(proof_path, proof)
    if proof["stage"] == "prepared":
        if Path(sys.executable).resolve().is_relative_to(directory.resolve()):
            raise LifecycleError("Final removal requires independent Python, not the venv being removed")
        if digest(root / "active.json") != proof["active_sha256"] or (Path(proof["data_home"]) / "service.json").exists():
            raise LifecycleError("Installation or service changed after uninstall preparation")
        if digest(directory / "installation.json") != proof["receipt_sha256"]:
            raise LifecycleError("Installation changed before uninstall")
        receipt = _json(directory / "installation.json")
        _verify_installation(directory, receipt)
        retired.parent.mkdir(parents=True, exist_ok=True)
        directory.rename(retired)
        (root / "active.json").unlink()
        proof["stage"] = "retired"
        _write(proof_path, proof)
    # A failed Windows deletion is retryable, but only unchanged owned files are
    # removed. Missing files from a previous partial cleanup are permitted.
    receipt_path = retired / "installation.json"
    if retired.exists():
        receipt = proof["installation"]
        if receipt_path.exists() and digest(receipt_path) != proof["receipt_sha256"]:
            raise LifecycleError("Retired installation receipt was modified")
        present = _inventory(retired, owned_files=receipt["files"])
        if any(receipt["files"].get(name) != sha for name, sha in present.items()):
            raise LifecycleError("Retired release has modified or unknown files; cleanup refused")
        try:
            shutil.rmtree(_fs_path(retired))
        except OSError as error:
            return {"status": "uninstalled_cleanup_pending", "cleanup_error_code": getattr(error, "winerror", error.errno), "receipt": str(proof_path), "receipt_sha256": digest(proof_path),
                    "preserved_data_home": proof["data_home"], "credentials_deleted": False}
    proof["stage"] = "removed"; _write(proof_path, proof)
    return {"status": "uninstalled_active_release", "preserved_data_home": proof["data_home"],
            "credentials_deleted": False, "other_releases_removed": False, "recovery_bootstrap_retained": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    command = commands.add_parser("install")
    for name in ("manifest", "wheelhouse", "python", "install-root", "data-home"):
        command.add_argument("--" + name, type=Path, required=True)
    command.add_argument("--manifest-sha256", required=True)
    for name in ("activate", "diagnose", "start", "stop", "register-checkpoint", "rollback", "prepare-uninstall", "finalize-uninstall"):
        command = commands.add_parser(name); command.add_argument("--install-root", type=Path, required=True)
        if name == "activate": command.add_argument("--release-id", required=True)
        if name == "start":
            command.add_argument("--port", type=int, default=42817); command.add_argument("--at-logon", action="store_true")
        if name == "register-checkpoint": command.add_argument("checkpoint", type=Path)
        if name == "prepare-uninstall": command.add_argument("--retry-native-cleanup", action="store_true",
                                                               help="Explicitly retry a recorded unconfirmed native cleanup after inspecting and draining it")
        if name == "finalize-uninstall": command.add_argument("--receipt-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "install": result = install(args.manifest, args.manifest_sha256, args.wheelhouse, args.python, args.install_root, args.data_home)
        elif args.command == "activate": result = activate(args.install_root, args.release_id)
        elif args.command == "register-checkpoint": result = register_checkpoint(args.install_root, args.checkpoint)
        elif args.command == "start": result = start(args.install_root, args.port, at_logon=args.at_logon)
        elif args.command == "prepare-uninstall": result = prepare_uninstall(args.install_root, retry_native_cleanup=args.retry_native_cleanup)
        elif args.command == "finalize-uninstall": result = finalize_uninstall(args.install_root, args.receipt_sha256)
        else: result = globals()[args.command](args.install_root)
        print(json.dumps(result)); return 0
    except Exception as error:
        # Exceptions can originate from credential APIs or subprocesses. Keep CLI
        # output free from command stdout/stderr, URLs and credential values.
        detail = str(error) if isinstance(error, LifecycleError) else "Lifecycle operation failed; inspect the owned local receipt"
        print(json.dumps({"error": type(error).__name__, "detail": detail}), file=sys.stderr); return 2


if __name__ == "__main__":
    raise SystemExit(main())
