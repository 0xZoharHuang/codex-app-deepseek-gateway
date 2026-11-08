"""Offline App-owned image-cache format maintenance, without keys or HTTP.

The pinned pure cache owns ledger/salt transitions. This module supplies only the
App installation and actor-retirement boundary missing from the low-level API.
"""
from __future__ import annotations
from contextlib import closing
import math
from pathlib import Path
import re
import sqlite3
import os
import psutil

from .gateway_policy import ServiceConfigError, load_service_config
from . import app_delivery as delivery, isolated_app as app, lifecycle as lc
from .image_transport import ImageTransport, TransportPolicy
from .paths import _is_link_or_reparse, _sqlite_filename

class ImageCacheFormatError(ServiceConfigError):
    pass

def _fail(message):
    raise ImageCacheFormatError(message)

def _same(left, right):
    return app._profile_path(str(left)) == app._profile_path(str(right))

def _retired(identity, backend):
    if (not isinstance(identity, dict) or type(identity.get("pid")) is not int
            or identity["pid"] <= 0 or type(identity.get("created")) not in {int, float}
            or not math.isfinite(identity["created"])):
        _fail("Recorded App actor identity is invalid; preserve its launch evidence")
    try:
        alive = backend.live(identity)
    except psutil.NoSuchProcess:
        alive = False
    except (psutil.Error, OSError, app.LaunchError):
        _fail("Recorded App actor retirement is unknown; no format change")
    if alive is not False:
        _fail("A recorded App or native actor is live or unknown; quit it normally first")

def _launches_retired(paths, owner, backend):
    folder = paths["launches"]
    if not folder.exists():
        return 0, set()
    entries = list(folder.iterdir())
    if len(entries) > 4096:
        _fail("Too many App launch receipts; reconcile owned launch evidence first")
    total = 0
    native_paths = set()
    for run in entries:
        if not re.fullmatch(r"[0-9a-f]{32}", run.name):
            continue
        if _is_link_or_reparse(run) or run.resolve().parent != folder.resolve():
            _fail("App launch receipt directory is redirected")
        plan = app.load_json(run / "plan.json")
        validated = plan["validated"]
        environment = validated["environment"]
        if not _same(environment.get("CODEX_HOME", ""), paths["home"]):
            continue
        declared = validated.get("bundle", {}).get("files", {}).get("codex.exe", {}).get("path")
        if declared:
            native_paths.add(app._profile_path(declared))
        if validated["owner"]["sid"] != owner["sid"]:
            _fail("Dedicated App launch evidence belongs to a different user")
        receipt_path = run / "receipt.json"
        if not receipt_path.is_file():
            _fail("An admitted App launch has no terminal receipt; reconcile it first")
        receipt = app.load_json(receipt_path)
        if lc.digest(run / "plan.json") != receipt.get("plan_sha256"):
            _fail("Owned App launch plan differs from its retained receipt")
        identities = []
        if receipt.get("app") is not None:
            identities.append(receipt["app"])
        identities.extend(receipt.get("native_processes", []))
        identities.extend(receipt.get("excluded_auxiliary_processes", []))
        if not identities and receipt.get("created_pid") is not None:
            # A failed identity capture is not a false claim that nothing ran.
            # Absence or a later replacement proves the original PID retired.
            ended = receipt.get("finished_at")
            if type(ended) not in {int, float} or not math.isfinite(ended):
                _fail("Created App PID has no bounded terminal observation")
            try:
                current = backend.identity(receipt["created_pid"])
            except psutil.NoSuchProcess:
                current = None
            except (psutil.Error, OSError, app.LaunchError):
                _fail("Created App PID retirement is unknown")
            if current is not None and current["created"] <= ended:
                _fail("Created App PID may still be live; reconcile it first")
        elif not identities and receipt.get("status") != "failed_or_unverified":
            _fail("App launch outcome is unverified; no format change")
        for identity in identities:
            if identity.get("sid") != owner["sid"]:
                _fail("Recorded App actor belongs to a different user")
            _retired(identity, backend)
            total += 1
    return total, native_paths

def _native_path(binding):
    manifest = Path(binding["native_manifest"])
    if lc.digest(manifest) != binding["native_manifest_sha256"]:
        _fail("Bound native manifest changed; no actor classification")
    data = app.load_json(manifest)
    rows = [row for row in data["outputs"] if row.get("name") == "codex.exe"]
    if len(rows) != 1 or not Path(rows[0]["path"]).is_absolute():
        _fail("Bound native path is ambiguous")
    return Path(rows[0]["path"])

def _standalone_home(args, process):
    positions = [i for i, value in enumerate(args) if value == "--home"]
    inline = [value.split("=", 1)[1] for value in args if value.startswith("--home=")]
    if len(positions) + len(inline) > 1:
        _fail("Standalone cache command has ambiguous home selectors")
    if positions:
        i = positions[0]
        if i + 1 >= len(args):
            _fail("Standalone cache command has no home value")
        value = args[i + 1]
    elif inline:
        value = inline[0]
    else:
        local = process.environ().get("LOCALAPPDATA")
        if not isinstance(local, str) or not local:
            _fail("Standalone cache command default home is unknown")
        return Path(local) / "codex-app-deepseek-gateway"
    home = Path(value)
    return home if home.is_absolute() else Path(process.cwd()) / home

def _private_python_image(process, installation, paths):
    """A generic Python basename is not project evidence.

    Only an actual managed venv path, or the verified base image inside this
    project's private roots, makes an unreadable generic Python a possible
    managed actor. A shared system interpreter alone supplies no such evidence.
    """
    try:
        executable = app._profile_path(process.exe())
    except psutil.NoSuchProcess:
        raise
    except (psutil.Error, OSError, app.LaunchError):
        return False
    releases = app._profile_path(str(installation.root / "releases")).rstrip(chr(92))
    if executable.startswith(releases + chr(92)):
        parts = tuple(executable[len(releases) + 1:].split(chr(92)))
        if (len(parts) == 4 and re.fullmatch(r"[0-9a-f]{20}", parts[0])
                and parts[1:3] == ("venv", "scripts")
                and parts[3] in {"python.exe", "pythonw.exe", "codex-app-deepseek.exe"}):
            return True
    base = installation.receipt.get("runtime", {}).get("base_executable", {}).get("path")
    if not isinstance(base, str) or not Path(base).is_absolute() or executable != app._profile_path(base):
        return False
    private = (installation.root, paths["launches"].parent)
    return any(executable.startswith(app._profile_path(str(root)).rstrip(chr(92)) + chr(92)) for root in private)

def _scoped_writers_retired(data, native, paths, owner, backend, processes, installation):
    iterable = psutil.process_iter(["pid", "name"]) if processes is None else processes()
    for process in iterable:
        try:
            if process.pid == os.getpid():
                continue
            name = (process.name() or "").casefold()
            if name not in {"python.exe", "pythonw.exe", "codex-app-deepseek.exe", "codex.exe"}:
                continue
            if name == "codex.exe":
                try:
                    matched = app._profile_path(process.exe()) in native
                except psutil.NoSuchProcess:
                    continue
                except (psutil.Error, OSError, app.LaunchError):
                    # Recorded actors were checked before this scan. A generic
                    # unreadable codex basename adds no App/profile attribution.
                    continue
                if not matched:
                    continue
                identity = backend.identity(process.pid)
                environment = backend.selected_environment(identity)
                selected = environment.get("CODEX_HOME")
                if selected is not None and _same(selected, paths["home"]):
                    _fail("A dedicated native backend is still live; quit it normally")
                continue
            try:
                args = process.cmdline()
            except psutil.NoSuchProcess:
                continue
            except (psutil.Error, OSError):
                if name == "codex-app-deepseek.exe" or _private_python_image(process, installation, paths):
                    _fail("A project-image cache writer has unreadable command selectors")
                # No receipt, selector or private-image evidence: this observer
                # does not classify arbitrary embedded-library Python consumers.
                continue
            project = name == "codex-app-deepseek.exe" or any(
                value == "codex_app_deepseek.cli"
                or Path(value).name.casefold() == "codex-app-deepseek.exe" for value in args)
            if not project:
                continue
            if not any(value == "serve" or (value.startswith("image-cache-")
                       and value != "image-cache-format") for value in args):
                continue
            home = _standalone_home(args, process)
            if not _same(home, data):
                continue
            # Explicit selectors address our data. Different/unknown SID cannot
            # make an active writer safe; confirm the lifetime or reject.
            backend.identity(process.pid)
            _fail("A standalone gateway or image-cache writer still uses this data home")
        except psutil.NoSuchProcess:
            continue
        except (psutil.Error, OSError, app.LaunchError):
            _fail("A source-matched App cache writer could not be classified; no format change")

def _guard(installation, backend, paths, owner, processes):
    if (installation.data / "service.json").exists():
        _fail("Stop/reconcile the recorded gateway normally before format maintenance")
    app.require_disjoint_apps(backend, owner, paths)
    binding = delivery.read_binding(installation, backend)
    native = _native_path(binding)
    total, native_paths = _launches_retired(paths, owner, backend)
    native_paths.add(app._profile_path(str(native)))
    _scoped_writers_retired(installation.data, native_paths, paths, owner, backend, processes, installation)
    return total

def _legacy_enrollment(cache, allowed):
    if not cache.exists():
        return []
    legacy = []
    for scope in cache.iterdir():
        if not re.fullmatch(r"[0-9a-f]{64}", scope.name):
            continue
        if _is_link_or_reparse(scope) or not scope.is_dir():
            _fail("Credential scope directory is redirected or invalid")
        path = scope / "files.sqlite"
        if not path.exists():
            continue
        if _is_link_or_reparse(path):
            _fail("Legacy ledger cannot be a link or junction")
        with closing(sqlite3.connect(_sqlite_filename(path, read_only=True), uri=True)) as db:
            tables = {row[0] for row in db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
            if "cache_identity" not in tables and tables:
                if not allowed:
                    _fail("Legacy unbound cache needs explicit --enroll-empty-legacy")
                if tables != {"assets", "pins", "receipts"}:
                    _fail("Only the known empty legacy App schema may be enrolled")
                if any(db.execute('SELECT 1 FROM "' + name + '" LIMIT 1').fetchone()
                       for name in tables):
                    _fail("Nonempty unbound legacy cache must preserve ownership state")
                legacy.append(scope.name)
    return legacy

async def _maintain(install_root, target_version, *, enroll_empty_legacy=False,
                   backend=None, processes=None):
    if type(target_version) is not int or target_version not in {2, 3}:
        _fail("Image cache target format must be 2 or 3")
    if type(enroll_empty_legacy) is not bool:
        _fail("Empty legacy enrollment must be an explicit boolean")
    installation = delivery.installed(install_root)
    backend = backend or app.WindowsBackend()
    owner = backend.current()
    paths = app.defaults()
    app.validate_state_paths(paths)
    # Cheap immutable preconditions precede even creation of lifecycle lockfiles.
    _guard(installation, backend, paths, owner, processes)
    from .cli import _service_lease, _image_cache_home
    with lc._operation_lease(installation.root), backend.launch_lock(owner["sid"]), _service_lease(installation.data):
        actors = _guard(installation, backend, paths, owner, processes)
        config = load_service_config(installation.data)
        if config.image_transport is None:
            _fail("Configure image_transport before explicit cache-format maintenance")
        cache = _image_cache_home(installation.data)
        legacy = set(_legacy_enrollment(cache, enroll_empty_legacy))
        def no_credentials():
            _fail("Offline format maintenance cannot read credentials")
        publication_checked = False
        class OfflineTransport(ImageTransport):
            def _write_private_state(self, *args, **kwargs):
                nonlocal publication_checked
                if not publication_checked:
                    # This also precedes a fresh root's first scope.salt, which
                    # the core publishes before its first format marker. The
                    # core already holds initialize and all existing scope/
                    # asset locks; no cold legacy reader can acquire a scope
                    # between this check and the durable salt fence.
                    _guard(installation, backend, paths, owner, processes)
                    legacy.update(_legacy_enrollment(cache, enroll_empty_legacy))
                    publication_checked = True
                return super()._write_private_state(*args, **kwargs)
        transport = OfflineTransport(cache, no_credentials, None, TransportPolicy(**config.image_transport))
        result = await transport.migrate_cache_format(target_version)
        _guard(installation, backend, paths, owner, processes)
        if not isinstance(result, dict) or type(result.get("complete")) is not bool:
            _fail("Cache migration returned no definite completion status")
        if (result.get("credentials_read") is not False or type(result.get("inference_requests")) is not int or result["inference_requests"] != 0
                or type(result.get("external_HTTP_requests")) is not int or result["external_HTTP_requests"] != 0):
            _fail("Offline format maintenance crossed its metadata-only boundary")
        enrolled = sorted(row["scope"] for row in result.get("scope_results", [])
                          if row.get("scope") in legacy and row.get("before_version") is None
                          and row.get("after_version") == target_version)
        return {**result, "managed_gateway_retirement_verified": True,
                "App_actor_retirement_verified": True, "retired_recorded_actors": actors,
                "explicit_empty_legacy_enrollment": enrolled,
                "retirement_scope": "managed App launch sources/selectors, bound native images and standalone App CLI; not arbitrary embedded-library consumers",
                "App_runtime_acceptance": False}

async def maintain(install_root, target_version, *, enroll_empty_legacy=False,
                   backend=None, processes=None):
    try:
        return await _maintain(install_root, target_version,
                               enroll_empty_legacy=enroll_empty_legacy,
                               backend=backend, processes=processes)
    except ImageCacheFormatError:
        raise
    except (delivery.DeliveryError, lc.LifecycleError, app.LaunchError, psutil.Error,
            OSError, sqlite3.Error, ValueError, KeyError, TypeError) as error:
        raise ImageCacheFormatError(
            "Cannot verify the owned offline cache boundary: " + type(error).__name__) from None
