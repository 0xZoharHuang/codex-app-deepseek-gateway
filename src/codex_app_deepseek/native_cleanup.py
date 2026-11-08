"""Delegate this installation's Windows sandbox removal to its verified native CLI.

This module is loaded only by installed prepare-uninstall, never the independent
stdlib-only finalizer. It does not implement account, firewall, ACL or tool cleanup.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
import ctypes
from ctypes import wintypes
from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import time
import uuid

from . import lifecycle as lc
from .paths import _is_link_or_reparse, canonical_path

# The -m lifecycle entry has a distinct __main__ class. Expose only this known,
# safe diagnostic type for conversion at that entry, never arbitrary exceptions.
NativeCleanupError = lc.LifecycleError


@contextmanager
def _pinned_file(path, backend):
    """Deny replacement/writes of a verified cleanup input while it is admitted."""
    path = Path(path).absolute()
    if any(_is_link_or_reparse(part) for part in (path, *path.parents)):
        raise lc.LifecycleError("Native uninstall input is redirected")
    kernel = backend.kernel
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    handle = kernel.CreateFileW(str(path), 0x80000000, 1, None, 3, 0x00200000, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise lc.LifecycleError("Cannot pin native uninstall input")
    try:
        yield
    finally:
        kernel.CloseHandle(handle)


def _directory_identity(home, backend):
    """Read the native owner record's volume and 128-bit directory identity."""
    kernel = backend.kernel
    kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                 ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    kernel.CreateFileW.restype = wintypes.HANDLE
    handle = kernel.CreateFileW(str(home), 0x80, 3, None, 3, 0x02000000 | 0x00200000, None)
    if handle in (None, ctypes.c_void_p(-1).value):
        raise lc.LifecycleError("Cannot inspect native sandbox home identity")
    class FileIdInfo(ctypes.Structure):
        _fields_ = [("volume", ctypes.c_ulonglong), ("file_id", ctypes.c_ubyte * 16)]
    try:
        info = FileIdInfo()
        kernel.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                       ctypes.c_void_p, wintypes.DWORD]
        kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL
        if not kernel.GetFileInformationByHandleEx(handle, 18, ctypes.byref(info), ctypes.sizeof(info)):
            raise lc.LifecycleError("Cannot read native sandbox directory identity")
        return info.volume, list(info.file_id)
    finally:
        kernel.CloseHandle(handle)


def _namespace(home, backend, owner):
    owner_path = home / ".sandbox-secrets/namespace_owner.json"
    if not owner_path.exists():
        if any(p.exists() for p in (home / ".sandbox/setup_marker.json",
                                   home / ".sandbox/sandbox_users.json",
                                   home / ".sandbox-secrets/sandbox_users.json")):
            raise lc.LifecycleError("Sandbox state has no namespace owner reservation; native cleanup refused")
        return None
    value = lc._json(owner_path)
    volume, file_id = _directory_identity(home, backend)
    if (set(value) != {"schema", "namespace", "owner_sid", "home", "volume", "file_id",
                       "offline", "online", "group"} or value.get("schema") != 1
            or value.get("owner_sid") != owner["sid"]
            or value.get("home") != str(home.resolve()).lower()
            or value.get("volume") != volume or value.get("file_id") != file_id
            or not re.fullmatch("[0-9a-f]{64}", str(value.get("namespace", "")))
            or value.get("group") != "CodexAppDeepSeek-" + value["namespace"]
            or any(not re.fullmatch("Cdx[A-Za-z0-9]{17}", str(value.get(k, "")))
                   for k in ("offline", "online"))):
        raise lc.LifecycleError("Native sandbox namespace identity differs; no cleanup performed")
    return value


def _sid_text(pointer, backend):
    text = ctypes.c_void_p()
    if not backend.advapi.ConvertSidToStringSidW(pointer, ctypes.byref(text)):
        raise lc.LifecycleError("Cannot resolve Windows process/account SID")
    try:
        return ctypes.wstring_at(text)
    finally:
        backend.kernel.LocalFree(text)


def _account_sid(name, backend):
    """Read one local principal, never enumerate or alter accounts."""
    function = backend.advapi.LookupAccountNameW
    function.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR, ctypes.c_void_p,
                         ctypes.POINTER(wintypes.DWORD), wintypes.LPWSTR,
                         ctypes.POINTER(wintypes.DWORD), ctypes.POINTER(wintypes.DWORD)]
    function.restype = wintypes.BOOL
    size, domain_size, use = wintypes.DWORD(), wintypes.DWORD(), wintypes.DWORD()
    qualified = os.environ["COMPUTERNAME"] + "\\" + name
    ctypes.set_last_error(0)
    function(None, qualified, None, ctypes.byref(size), None, ctypes.byref(domain_size), ctypes.byref(use))
    error = ctypes.get_last_error()
    if error == 1332:
        return None
    if error != 122 or not size.value:
        raise lc.LifecycleError("Cannot inspect the exact local sandbox principal")
    sid, domain = ctypes.create_string_buffer(size.value), ctypes.create_unicode_buffer(domain_size.value)
    if not function(None, qualified, sid, ctypes.byref(size), domain, ctypes.byref(domain_size), ctypes.byref(use)):
        if ctypes.get_last_error() == 1332:
            return None
        raise lc.LifecycleError("Local sandbox principal changed during inspection")
    return _sid_text(sid, backend)


def _process_owners(backend):
    """Windows supplies primary-token SIDs without opening protected processes."""
    class ProcessInfo(ctypes.Structure):
        _fields_ = [("session", wintypes.DWORD), ("pid", wintypes.DWORD),
                    ("name", wintypes.LPWSTR), ("sid", ctypes.c_void_p)]
    wts = ctypes.WinDLL("wtsapi32", use_last_error=True)
    wts.WTSEnumerateProcessesW.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                          ctypes.POINTER(ctypes.POINTER(ProcessInfo)),
                                          ctypes.POINTER(wintypes.DWORD)]
    wts.WTSEnumerateProcessesW.restype = wintypes.BOOL
    wts.WTSFreeMemory.argtypes = [ctypes.c_void_p]
    rows, count = ctypes.POINTER(ProcessInfo)(), wintypes.DWORD()
    if not wts.WTSEnumerateProcessesW(None, 0, 1, ctypes.byref(rows), ctypes.byref(count)):
        raise lc.LifecycleError("Cannot establish Windows sandbox workload ownership")
    try:
        return [{"pid": row.pid, "name": row.name or "",
                 "sid": _sid_text(row.sid, backend) if row.sid else None}
                for row in rows[:count.value]]
    finally:
        wts.WTSFreeMemory(rows)


def _drained(home, executable, namespace, backend):
    """Reject live App/native actors and the exact namespace's primary tokens."""
    import psutil
    owner = backend.current()
    principals = {_account_sid(namespace[key], backend) for key in ("offline", "online")} if namespace else set()
    principals.discard(None)
    rows = _process_owners(backend)
    if any(row["sid"] in principals for row in rows):
        raise lc.LifecycleError("This namespace still has a live sandbox workload; quit its work before uninstall")
    home, executable = canonical_path(home), canonical_path(executable)
    for row in rows:
        name = row["name"].casefold()
        helper = name.startswith(("codex-command-runner", "codex-windows-sandbox-setup",
                                  "codex-code-mode-host", "codex-windows-managed-deny-probe"))
        if name not in {"chatgpt.exe", "codex.exe"} and not helper:
            continue
        if row["sid"] is None:
            raise lc.LifecycleError("Cannot establish ownership of a potential App/native actor")
        if row["sid"] != owner["sid"]:
            continue
        try:
            process = psutil.Process(row["pid"])
            identity = backend.identity(process.pid)
            environment = process.environ()
            selected = environment.get("CODEX_HOME")
            if selected and canonical_path(selected) == home:
                raise lc.LifecycleError("This App home still has a live frontend or native backend/helper; quit it before uninstall")
            image = canonical_path(identity["exe"])
            if selected is None and ((name == "codex.exe" and image == executable)
                                     or (helper and (image.parent == executable.parent
                                                     or image.is_relative_to(home)))):
                raise lc.LifecycleError("Bound native process has no verifiable home; uninstall refused")
        except psutil.NoSuchProcess:
            continue
        except (psutil.AccessDenied, OSError) as error:
            raise lc.LifecycleError("Cannot verify a potential App/native process is unrelated") from error

def _cleanup_bundle(manifest, digest, expected, backend, owner, home):
    """Legacy cleanup stays native; Core teardown stays with its package lifecycle."""
    from . import isolated_app as app, runtime_package as packages
    app.require(app.sha(manifest) == digest, "native manifest checksum mismatch")
    document = app.load_json(manifest)
    if document.get("kind") == packages.KIND:
        # Never reinterpret Core activation as the Legacy sandbox-uninstall API.
        # The native service owns Core account teardown in package removal.
        if document.get("mode") == "registered_core":
            raise lc.LifecycleError("RegisteredCore requires native runtime package teardown; no Legacy sandbox cleanup or gateway retirement performed")
        bundle = app.verify_launch_bundle(manifest, digest, expected, backend, owner, home)
        bundle["cleanup_verification"] = {
            "full_bundle_verified": True, "method": "installed-signed-runtime-package",
            "mode": "legacy", "unexecuted_components": {},
        }
        return bundle
    bundle = app.verify_bundle(manifest, digest, expected, cleanup_only=True)
    snapshot = getattr(backend, "native_runtime_owner_snapshot", None)
    if snapshot is not None:
        packages.require_mode_available(None, snapshot(), owner, home)
    return bundle


@dataclass
class Cleanup:
    root: Path
    home: Path | None
    executable: Path | None
    namespace: dict | None
    backend: object | None
    inputs: dict
    status: str
    bundle_verification: dict | None = None
    runtime_package: dict | None = None

    def verify(self):
        for path, expected in self.inputs.items():
            if lc.digest(Path(path)) != expected:
                raise lc.LifecycleError("Native uninstall input changed after preflight")
        if self.home is not None:
            owner = self.backend.current()
            current = _namespace(self.home, self.backend, owner)
            if current != self.namespace:
                raise lc.LifecycleError("Native namespace changed after uninstall preflight")
            _drained(self.home, self.executable, self.namespace, self.backend)
            if self.runtime_package is not None:
                from . import app_delivery as delivery, isolated_app as app
                expected = app.baseline(delivery.assets()[0])
                bundle = _cleanup_bundle(Path(self.runtime_package["descriptor_path"]),
                    self.runtime_package["descriptor_sha256"], expected, self.backend, owner, self.home)
                if bundle.get("runtime_package") != self.runtime_package:
                    raise lc.LifecycleError("Native runtime activation changed after uninstall preflight")
            else:
                from . import runtime_package as packages
                snapshot = getattr(self.backend, "native_runtime_owner_snapshot", None)
                if snapshot is not None:
                    packages.require_mode_available(None, snapshot(), owner, self.home)

    def check_recovery(self, *, retry_unconfirmed=False):
        evidence = self.root / "native-uninstall.json"
        if self.status == "native_cleanup_required" and evidence.exists():
            previous = lc._json(evidence)
            if previous.get("project") != lc.PROJECT or previous.get("schema_version") != 1:
                raise lc.LifecycleError("Native uninstall recovery record is unrecognized")
            if previous.get("status") not in {"started", "native_cleanup_outcome_unconfirmed", "native_cleanup_failed", "native_cleanup_verification_failed", "native_cleanup_completed"}:
                raise lc.LifecycleError("Native cleanup recovery status is unrecognized")
            if previous.get("status") != "native_cleanup_completed" and previous.get("namespace") != self.namespace["namespace"]:
                raise lc.LifecycleError("Incomplete native cleanup belongs to a different namespace; retain its original ownership evidence")
            if previous.get("status") in {"started", "native_cleanup_outcome_unconfirmed"} and not retry_unconfirmed:
                raise lc.LifecycleError("Prior native cleanup outcome is unconfirmed; inspect its receipt and explicitly use --retry-native-cleanup after draining its actors")

    def execute(self, *, run=subprocess.run, retry_unconfirmed=False):
        self.verify()
        self.check_recovery(retry_unconfirmed=retry_unconfirmed)
        if self.status != "native_cleanup_required":
            return {"status": self.status, "native_command_performed": False,
                    "ordinary_filesystem_acl_removed": False,
                    "bundle_verification": self.bundle_verification}
        evidence = self.root / "native-uninstall.json"
        if evidence.exists():
            previous = lc._json(evidence)
            if previous.get("project") != lc.PROJECT or previous.get("schema_version") != 1:
                raise lc.LifecycleError("Native uninstall recovery record is unrecognized")
            if previous.get("status") not in {"started", "native_cleanup_outcome_unconfirmed", "native_cleanup_failed", "native_cleanup_verification_failed", "native_cleanup_completed"}:
                raise lc.LifecycleError("Native cleanup recovery status is unrecognized")
            if previous.get("status") != "native_cleanup_completed" and previous.get("namespace") != self.namespace["namespace"]:
                raise lc.LifecycleError("Incomplete native cleanup belongs to a different namespace; retain its original ownership evidence")
            if previous.get("status") in {"started", "native_cleanup_outcome_unconfirmed"} and not retry_unconfirmed:
                raise lc.LifecycleError("Prior native cleanup outcome is unconfirmed; inspect its receipt and explicitly use --retry-native-cleanup after draining its actors")
            if (previous.get("status") == "native_cleanup_completed"
                    and previous.get("inputs") == self.inputs
                    and previous.get("namespace") == self.namespace["namespace"]
                    and all(_account_sid(self.namespace[k], self.backend) is None
                            for k in ("offline", "online", "group"))):
                return {"status": "native_cleanup_completed", "native_command_performed": True,
                        "reused_completion": True, "receipt": str(evidence),
                        "receipt_sha256": lc.digest(evidence), "home": str(self.home),
                        "namespace": self.namespace["namespace"],
                        "ordinary_filesystem_acl_removed": False, "user_files_deleted": False,
                        "bundle_verification": self.bundle_verification}
            # Preserve even legacy attempts before replacing the current pointer.
            archive = self.root / "native-uninstall-attempts"
            archive.mkdir(exist_ok=True)
            prior_id = previous.get("attempt_id")
            if not isinstance(prior_id, str) or not re.fullmatch("[0-9a-f]{32}", prior_id):
                prior_id = "legacy-" + lc.digest(evidence)
            prior_path = archive / (prior_id + ".json")
            if prior_path.exists() and lc._json(prior_path) != previous:
                raise lc.LifecycleError("Native cleanup attempt history differs; recovery refused")
            if not prior_path.exists():
                lc._write(prior_path, previous)
        attempt_id = uuid.uuid4().hex
        attempt_path = self.root / "native-uninstall-attempts" / (attempt_id + ".json")
        def publish(value):
            lc._write(attempt_path, value)
            lc._write(evidence, value)
        record = {"schema_version": 1, "project": lc.PROJECT, "status": "started",
                  "attempt_id": attempt_id, "attempt_receipt": str(attempt_path),
                  "started_at": time.time(), "home": str(self.home),
                  "namespace": self.namespace["namespace"], "owner_sid": self.namespace["owner_sid"],
                  "executable": str(self.executable), "inputs": self.inputs,
                  "bundle_verification": self.bundle_verification,
                  "argv": [self.runtime_package["alias_path"] if self.runtime_package else str(self.executable), "sandbox", "uninstall"],
                  "ordinary_filesystem_acl_removed": False, "user_files_deleted": False}
        publish(record)
        environment = {k: v for k, v in os.environ.items()
                       if k.upper() in {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "TEMP", "TMP",
                                        "COMPUTERNAME", "USERNAME", "USERDOMAIN", "USERPROFILE",
                                        "LOCALAPPDATA", "APPDATA", "PROGRAMDATA"}}
        environment["CODEX_HOME"] = str(self.home)
        if self.runtime_package is not None:
            environment["CODEX_WINDOWS_REGISTERED_CORE"] = "0"
        if environment["CODEX_HOME"] != str(self.home):
            raise lc.LifecycleError("Native uninstall requires the exact bound App home")
        try:
            result = run(record["argv"], env=environment, cwd=self.home,
                         stdin=subprocess.DEVNULL, capture_output=True, timeout=120)
            record.update(returncode=result.returncode, completed_at=time.time(),
                          stdout_sha256=__import__("hashlib").sha256(result.stdout).hexdigest(),
                          stderr_sha256=__import__("hashlib").sha256(result.stderr).hexdigest())
            # Native output is bounded diagnostic text, not model/history/key data.
            record["stdout"] = result.stdout[:65536].decode("utf-8", "replace")
            record["stderr"] = result.stderr[:65536].decode("utf-8", "replace")
            if result.returncode:
                record["status"] = "native_cleanup_failed"
                publish(record)
                raise lc.LifecycleError("Native sandbox cleanup failed; retain native-uninstall.json and explicitly reconcile before retry")
            remaining = {key: _account_sid(self.namespace[key], self.backend) for key in ("offline", "online", "group")}
            record["principals_after"] = remaining
            if any(remaining.values()):
                record["status"] = "native_cleanup_verification_failed"
                publish(record)
                raise lc.LifecycleError("Native cleanup returned success but namespace principals remain; release retirement refused")
            record["status"] = "native_cleanup_completed"
            publish(record)
        except (OSError, subprocess.TimeoutExpired) as error:
            record.update(status="native_cleanup_outcome_unconfirmed", completed_at=time.time(),
                          error_type=type(error).__name__)
            publish(record)
            raise lc.LifecycleError("Native sandbox cleanup outcome is unconfirmed; no automatic replay or release retirement") from None
        return {"status": record["status"], "native_command_performed": True,
                "receipt": str(evidence), "receipt_sha256": lc.digest(evidence),
                "home": str(self.home), "namespace": self.namespace["namespace"],
                "ordinary_filesystem_acl_removed": False, "user_files_deleted": False,
                "bundle_verification": self.bundle_verification}


@contextmanager
def admission(root, directory, receipt, *, backend=None, retry_unconfirmed=False):
    """Pin provenance and require an idle, correctly owned home before mutations."""
    data = Path(receipt["data_home"])
    path = data / "app-binding.json"
    if not path.exists():
        if (root / "native-uninstall.json").exists():
            raise lc.LifecycleError("Native namespace cleanup history exists but its App binding is missing")
        # A gateway-only custom installation owns no other installation's App.
        # A missing binding at the managed default home cannot hide its namespace.
        local = Path(os.environ.get("LOCALAPPDATA", ""))
        private_home = local / "codex-app-deepseek-app"
        if (root / "checkpoints.json").exists():
            _, checkpoints, _ = lc._read_registry(root, data)
            if any(detail["config"].resolve() == (private_home / "config.toml").resolve()
                   for detail in checkpoints.values()):
                raise lc.LifecycleError("Owned private App checkpoint exists but its runtime binding is missing")
        if (data.resolve() == (local / lc.PROJECT).resolve()
                and (private_home / ".sandbox-secrets/namespace_owner.json").exists()):
            raise lc.LifecycleError("Private App namespace exists but its runtime binding is missing")
        yield Cleanup(root, None, None, None, None, {}, "not_app_bound")
        return
    from . import app_delivery as delivery
    from . import isolated_app as app
    backend = backend or app.WindowsBackend()
    owner = backend.current()
    installation = delivery.Installation(root, directory, data, receipt)
    value = delivery.read_binding(installation, backend, verify_catalog=False)
    paths = app.defaults()
    app.validate_state_paths(paths)
    home = paths["home"]
    if not home.exists():
        raise lc.LifecycleError("Bound App home is missing; namespace cleanup ownership cannot be verified")
    with ExitStack() as resources:
        resources.enter_context(backend.launch_lock(owner["sid"]))
        resources.enter_context(backend.home_guard(home, owner["sid"]))
        resources.enter_context(_pinned_file(path, backend))
        value = delivery.read_binding(installation, backend, verify_catalog=False)
        manifest = Path(value["native_manifest"])
        resources.enter_context(_pinned_file(manifest, backend))
        baseline, _ = delivery.assets()
        bundle = _cleanup_bundle(manifest, value["native_manifest_sha256"], app.baseline(baseline), backend, owner, home)
        cli = bundle["files"]["codex.exe"]
        executable = Path(cli["path"])
        resources.enter_context(_pinned_file(executable, backend))
        namespace = _namespace(home, backend, owner)
        # The native engine owns its namespace state/ledger writes under its
        # machine setup lock. Python verifies it again immediately before exec,
        # but does not hold a file-sharing lock across native state mutation.
        if namespace and not owner["elevated"]:
            raise lc.LifecycleError("Native sandbox uninstall requires administrator privileges for this same Windows user; no rollback or service stop performed")
        _drained(home, executable, namespace, backend)
        inputs = {str(path): lc.digest(path), str(manifest): value["native_manifest_sha256"],
                  cli["path"]: cli["sha256"]}
        if namespace:
            inputs[str(home / ".sandbox-secrets/namespace_owner.json")] = lc.digest(home / ".sandbox-secrets/namespace_owner.json")
        plan = Cleanup(root, home, executable, namespace, backend, inputs,
                       "native_cleanup_required" if namespace else "app_home_not_provisioned",
                       bundle["cleanup_verification"])
        plan.verify()
        plan.check_recovery(retry_unconfirmed=retry_unconfirmed)
        yield plan
