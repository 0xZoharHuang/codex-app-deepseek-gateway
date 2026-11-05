"""One-shot, process-scoped launch admission for the fixed Windows Codex App.
No runtime loop, setup, App replacement, global environment edits, restart or fallback.
The DesktopPackage transport requires separate actual App acceptance.
"""
from __future__ import annotations
from .paths import _is_link_or_reparse
import argparse, base64, ctypes, hashlib, json, ntpath, os, re, shutil, subprocess, sys, time, tomllib, uuid
from contextlib import ExitStack, contextmanager
from ctypes import wintypes
from pathlib import Path
import psutil
from .app_config import _atomic_write, _private

BASELINE_SHA256 = "725d53f2db45d33efbe4f5babe9b8a4479887c4d18434d6d9f6fd836dc5b9536"
CONTROLLED = {"CODEX_HOME", "CODEX_CLI_PATH", "CODEX_ELECTRON_USER_DATA_PATH", "CODEX_APP_SERVER_FORCE_CLI"}
REMOVED = {
    "OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "OPENROUTER_API_KEY",
    "OPENAI_BASE_URL", "OPENAI_API_BASE", "OPENAI_ORGANIZATION", "OPENAI_PROJECT",
    "OPENAI_ORG_ID", "OPENAI_PROJECT_ID", "CODEX_AUTHAPI_BASE_URL",
    "CODEX_REFRESH_TOKEN_URL_OVERRIDE", "CODEX_REVOKE_TOKEN_URL_OVERRIDE",
    "CODEX_APP_SERVER_LOGIN_ISSUER", "CODEX_APP_SERVER_LOGIN_CLIENT_ID",
    "CODEX_SQLITE_HOME", "CODEX_APP_SERVER_WS_URL", "CODEX_APP_SERVER_USE_LOCAL_DAEMON",
    "CODEX_WINDOWS_REGISTERED_CORE", "CODEX_WINDOWS_SANDBOX_PACKAGE_FAMILY",
    # Only an explicit one-shot validated plan may enable diagnostic output.
    "CODEX_ROLLOUT_TRACE_ROOT",
}
TRACE_ROOT_ENV = "CODEX_ROLLOUT_TRACE_ROOT"
SAFE_APP_ARGS = {"--force-renderer-accessibility"}
CONTROL_PLANE_SCOPE = "app_frontend_and_verified_native_backends"
NATIVE_WORKLOAD_SCOPE = "owned_by_native_runtime_not_enumerated"

class LaunchError(RuntimeError):
    pass

def require(value, message):
    if not value: raise LaunchError(message)

def sha(path):
    value = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""): value.update(chunk)
    return value.hexdigest()

def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8-sig"))

def save(path, value):
    _atomic_write(Path(path), (json.dumps(value, indent=2) + chr(10)).encode())

def _save_receipt(path, value):
    """Publish owned progress through the common bounded atomic writer."""
    save(path, value)


def defaults(environment=None):
    environment = os.environ if environment is None else environment
    local = Path(environment["LOCALAPPDATA"]).resolve()
    home = local / "codex-app-deepseek-app"
    return {"home": home, "frontend": home / "electron-user-data",
            "launches": local / "codex-app-deepseek-gateway" / "app-launches",
            "native_logs": local / "Codex" / "Logs"}

def validate_state_paths(paths):
    # These are fixed dedicated profile locations, not user-selectable aliases.
    # Resolve every existing ancestor too: a junction on gateway/home must not
    # redirect launch receipts or Electron state into another installation.
    for name in ("home", "frontend", "launches"):
        path = Path(paths[name])
        require(path.resolve() == path.absolute(), "dedicated App state path is redirected: " + name)
        require(not _is_link_or_reparse(path),
                "dedicated App state path is a link: " + name)
        require(not path.exists() or path.is_dir(), "dedicated App state path is not a directory: " + name)
    require(paths["frontend"].resolve().is_relative_to(paths["home"].resolve()),
            "Electron state escapes the dedicated native home")


def validated_trace_root(value, paths, backend, owner_sid):
    """Validate only the explicit diagnostic container; never repurpose App state."""
    require(isinstance(value, str) and value and not any(ord(c) < 32 for c in value),
            "trace root must be a nonempty local absolute path")
    path = Path(value)
    require(path.is_absolute() and path.parent != path and not value.startswith("\\\\"),
            "trace root must be a local absolute directory, not a volume or UNC root")
    require(path.resolve() == path.absolute() and not any(_is_link_or_reparse(p) for p in (path, *path.parents)),
            "trace root or an ancestor is redirected")
    protected = [Path(paths[key]).resolve() for key in ("home", "frontend", "launches")]
    protected.extend([(Path.home() / ".codex").resolve(),
                      (Path(os.environ.get("LOCALAPPDATA", str(Path.home()))) / "codex-deepseek").resolve()])
    require(not any(path == p or path.is_relative_to(p) or p.is_relative_to(path) for p in protected),
            "trace root must not overlap App, CDS, or launch state")
    require(path.parent.is_dir(), "trace root parent must already exist")
    if path.exists():
        require(path.is_dir(), "trace root is not a directory")
        with backend.home_guard(path, owner_sid):
            pass
    return str(path.absolute())


def prepare_trace_directory(root, run_name, backend, owner_sid):
    """Only a fresh per-launch child is made private; existing container ACLs stay intact."""
    root = Path(root)
    with backend.home_guard(root, owner_sid, create=True) as root_identity:
        if root_identity.get("created"):
            _private(root, directory=True)
        directory = root / ("cadsg-" + run_name)
        require(not directory.exists(), "per-launch trace directory already exists")
        with backend.home_guard(directory, owner_sid, create=True):
            _private(directory, directory=True)
            stat = directory.stat()
            require(stat.st_ino != 0, "trace directory has no stable file identity")
            return {"requested_root": str(root), "directory": str(directory),
                    "environment_name": TRACE_ROOT_ENV, "owner_sid": owner_sid,
                    "directory_identity": {"volume": stat.st_dev, "file_id": stat.st_ino},
                    "private_raw_diagnostics": True, "App_trace_contents_verified": False}


def child_environment(base, values, *, trace_directory=None):
    require(set(values) == CONTROLLED and values["CODEX_APP_SERVER_FORCE_CLI"] == "1", "invalid controlled launch environment")
    names = sorted(key for key in base if key.upper() in CONTROLLED | REMOVED or key.upper().startswith("CODEX_DEEPSEEK_"))
    result = {key: value for key, value in base.items() if key not in names}
    result.update(values)
    if trace_directory is not None:
        result = {key: value for key, value in result.items() if key.upper() != TRACE_ROOT_ENV}
        result[TRACE_ROOT_ENV] = str(trace_directory)
    return result, names  # Names only; never record inherited selector values.

def path_contains_directory(environment, directory):
    """Compare Windows PATH entries without resolving arbitrary caller paths."""
    target = ntpath.normcase(ntpath.normpath(str(directory)))
    return any(ntpath.normcase(ntpath.normpath(part.strip().strip('"'))) == target
               for key, value in environment.items() if key.upper() == "PATH"
               for part in value.split(";") if part.strip())


def git_child_environment(base, dependency):
    """Append a trusted fallback only to the new child's copied environment."""
    result = dict(base)
    record = {**dependency, "path_policy": "append_preserving_existing_priority",
              "path_appended": False, "child_path_contains": None,
              "effective_git_inferred": False, "worktree_gate": "pending_app_validation"}
    if dependency["status"] != "available":
        return result, record
    directory = dependency["directory"]
    keys = [key for key in result if key.upper() == "PATH"]
    require(len(keys) <= 1, "ambiguous inherited Windows PATH keys")
    if not path_contains_directory(result, directory):
        key = keys[0] if keys else "PATH"
        previous = result.get(key, "")
        result[key] = previous + (";" if previous else "") + directory
        record["path_appended"] = True
    record["child_path_contains"] = path_contains_directory(result, directory)
    return result, record


def trusted_git_from_roots(roots, probe):
    """Follow native installation trust; never search PATH or the workspace."""
    rejected = 0
    for root in roots:
        try:
            root = Path(root).resolve(strict=True)
        except OSError:
            continue
        candidate = root / "Git" / "cmd" / "git.exe"
        try:
            if not candidate.is_file():
                continue
            executable = candidate.resolve(strict=True)
            directory = candidate.parent.resolve(strict=True)
            if (not executable.is_relative_to(root) or not directory.is_relative_to(root)
                    or executable.parent != directory):
                rejected += 1
                continue
            before = sha(executable)
            version = probe(executable)
            if not re.fullmatch(r"git version [A-Za-z0-9.+_-]{1,100}", version):
                raise ValueError("invalid version response")
            if sha(executable) != before:
                raise ValueError("executable changed during version probe")
            return {"status": "available", "path": str(executable), "directory": str(directory),
                    "version": version, "sha256": before,
                    "source": "Windows KnownFolder ProgramFiles Git/cmd",
                    "worktree_gate": "pending_app_validation"}
        except (OSError, ValueError, subprocess.SubprocessError):
            return {"status": "probe_failed", "path": str(candidate),
                    "version": None, "diagnostic": "trusted Git could not be verified",
                    "worktree_gate": "pending_app_validation"}
    return {"status": "unavailable", "path": None, "version": None,
            "diagnostic": "no Git executable in trusted ProgramFiles installation locations",
            "rejected_redirects": rejected, "worktree_gate": "pending_app_validation"}


def baseline(path):
    require(sha(path) == BASELINE_SHA256, "App baseline changed; compatibility must be requalified")
    value = load_json(path)
    require(value["schema_version"] == 1 and value["package_name"] == "OpenAI.Codex", "invalid App baseline")
    return value

def verify_package(observed, expected):
    package = observed["package"]
    for actual, fixed in [("full_name", "package_full_name"), ("family", "package_family_name"), ("version", "version"), ("publisher", "publisher")]:
        require(package.get(actual) == expected[fixed], "installed App identity differs from the qualified baseline")
    require(package.get("status") == "Ok" and package.get("signature_kind") == expected["required_signature_kind"] and package.get("development") is False, "App package is not a healthy signed production installation")
    require(observed["exe_signature"]["status"] == "Valid" and observed["exe_signature"]["thumbprint"].upper() == expected["executable_signer_thumbprint"], "App executable signature differs")
    require(observed["package_signature_status"] == "Valid", "App package signature is not valid")
    files = {**expected["files"], **{name: item["sha256"] for name, item in expected["app_resources"].items()}}
    require(observed["files"] == files, "App or official runtime resource bytes differ")
    return observed

def verify_bundle(path, expected_sha, expected, *, prepared=True, cleanup_only=False):
    """Validate all declarations; cleanup alone only requires the in-process CLI.

    Missing or damaged unused helpers must not prevent native uninstall. This
    narrow admission is never used by App launch/check or runtime preparation.
    """
    path = Path(path)
    require(isinstance(expected_sha, str) and len(expected_sha) == 64 and sha(path) == expected_sha, "native manifest checksum mismatch")
    manifest = load_json(path)
    require(manifest.get("schema_version") == 1, "unsupported native manifest")
    require(manifest.get("native_version") == expected["native_version"] and manifest.get("upstream_commit") == expected["native_upstream_commit"], "native manifest must identify the matching fixed upstream version")
    source_sha = manifest.get("source_sha256")
    require(isinstance(source_sha, str) and len(source_sha) == 64 and all(c in "0123456789abcdef" for c in source_sha), "native source provenance is missing")
    statuses = {"prepared-not-app-qualified"} if prepared else {"passed", "built-not-app-qualified"}
    require(manifest.get("status") in statuses, "native bundle is not in the required preparation state")
    if prepared:
        require(manifest.get("app_baseline_sha256") == BASELINE_SHA256 and manifest.get("app_package_full_name") == expected["package_full_name"], "native sidecars belong to another App package")
    rows = manifest.get("outputs", manifest.get("binaries"))
    require(isinstance(rows, list), "native manifest has no binary inventory")
    root, files, unused = path.parent.resolve(), {}, {}
    for row in rows:
        require(isinstance(row, dict), "invalid native binary declaration")
        name = row.get("name", row.get("file"))
        require(isinstance(name, str) and Path(name).name == name and name not in files, "invalid or duplicate native binary name")
        size, checksum = row.get("size"), row.get("sha256")
        require(type(size) is int and size >= 0 and isinstance(checksum, str)
                and re.fullmatch("[0-9a-f]{64}", checksum), "invalid native binary checksum/size declaration")
        require("path" not in row or isinstance(row["path"], str), "invalid native binary path declaration")
        file = Path(row["path"]) if "path" in row else root / name
        # Do not resolve or read an unused, possibly quarantined helper to prove
        # the CLI's provenance. Its declared path must still be exactly local.
        if cleanup_only and name != "codex.exe":
            require(file.is_absolute() and file.parent == root and file.name == name, "native file escapes its owned bundle")
            try:
                if any(_is_link_or_reparse(part) for part in (file, *file.parents)):
                    observed = "redirected"
                elif not file.is_file():
                    observed = "missing"
                elif file.stat().st_size != size or sha(file) != checksum:
                    observed = "checksum_mismatch"
                else:
                    observed = "observed_bytes_match"
            except OSError:
                observed = "unreadable"
            unused[name] = {"status": observed, "required_for_cleanup": False}
            admitted = file
        else:
            require(file.resolve().parent == root and file.resolve().name == name and not file.is_symlink(), "native file escapes its owned bundle")
            require(file.is_file() and file.stat().st_size == size and sha(file) == checksum, "native binary checksum mismatch: " + name)
            admitted = file.resolve()
        files[name] = {"name": name, "path": str(admitted), "size": size, "sha256": checksum}
    own_host = manifest.get("kind") == "cadsg-native-source-bundle"
    require(manifest.get("kind") in (None, "cadsg-native-source-bundle"), "unknown native file bundle kind")
    required = set(expected["native_binaries"])
    if own_host:
        require(manifest.get("runtime_mode") == "legacy"
                and re.fullmatch("[0-9a-f]{64}", manifest.get("build_lock_sha256", ""))
                and manifest.get("candidate_revision") == "O165",
                "source-built native bundle mode or producer is missing")
        required.update({"codex-code-mode-host.exe", "codex-windows-sandbox-service.exe"})
        require(all(row.get("producer_id") == "O165" for row in rows), "mixed native source producers")
    elif prepared:
        required.update(expected["required_private_sidecars"])
    require(required == files.keys(), "native bundle has missing or unexpected binaries/Code Mode Host")
    for name, resource in expected["required_private_sidecars"].items():
        if prepared and not own_host: require(files[name]["sha256"] == expected["app_resources"][resource]["sha256"], "Code Mode Host differs from the fixed App")
    result = {"manifest": str(path.resolve()), "manifest_sha256": expected_sha, "source_sha256": source_sha,
              "native_version": manifest["native_version"], "upstream_commit": manifest["upstream_commit"], "files": files}
    if cleanup_only:
        result["cleanup_verification"] = {"scope": "cleanup_cli_only", "full_bundle_verified": False,
                                          "unexecuted_components": unused}
    return result

def verify_launch_bundle(path, digest, expected, backend, owner, home):
    """Keep legacy file bundles and own installed MSIX descriptors as strict branches."""
    require(isinstance(digest, str) and len(digest) == 64 and sha(path) == digest,
            "native manifest checksum mismatch")
    from . import runtime_package as packages
    if load_json(path).get("kind") == packages.KIND:
        bundle = packages.verify(path, digest, expected, owner, home, backend)
    else:
        bundle = verify_bundle(path, digest, expected)
    snapshot = getattr(backend, "native_runtime_owner_snapshot", None)
    if snapshot is not None:
        packages.require_mode_available(bundle.get("runtime_package"), snapshot(), owner, home)
    return bundle


def is_app_server_command(arguments):
    # Recognize the pinned App backend; version/config probes are not servers.
    args = list(arguments[1:])
    while args:
        if args[0] in ("-c", "--config"):
            if len(args) < 2: return False
            args = args[2:]
        elif args[0].startswith("--config="):
            args = args[1:]
        else:
            if args[0] != "app-server": return False
            # Generators/help/daemon commands exit or serve another role. A config
            # value matching a subcommand is not itself a subcommand.
            tail = args[1:]
            while tail:
                if tail[0] in ("-c", "--config"):
                    if len(tail) < 2: return False
                    tail = tail[2:]
                    continue
                if tail[0] in ("--help", "-h", "--version", "-V", "generate-ts",
                                "generate-json-schema", "generate-internal-json-schema",
                                "daemon", "proxy"):
                    return False
                tail = tail[1:]
            return True
    return False


class WindowsBackend:
    def __init__(self):
        require(os.name == "nt", "the isolated App launcher is Windows-only")
        self.kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        self.advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        self.kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        self.kernel.OpenProcess.restype = wintypes.HANDLE
        self.kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        self.kernel.ProcessIdToSessionId.argtypes = [wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        self.advapi.OpenProcessToken.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
        self.advapi.GetTokenInformation.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
        self.advapi.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
        self.advapi.ConvertSidToStringSidW.restype = wintypes.BOOL
        self.kernel.LocalFree.argtypes = [ctypes.c_void_p]
        self.kernel.LocalFree.restype = ctypes.c_void_p

    def identity(self, pid):
        process = psutil.Process(pid)
        created = process.create_time()
        handle = self.kernel.OpenProcess(0x1000, False, pid)
        require(bool(handle), "cannot open process identity")
        token = wintypes.HANDLE()
        try:
            require(bool(self.advapi.OpenProcessToken(handle, 8, ctypes.byref(token))), "cannot query process token")
            needed = wintypes.DWORD()
            self.advapi.GetTokenInformation(token, 1, None, 0, ctypes.byref(needed))
            buffer = ctypes.create_string_buffer(needed.value)
            require(bool(self.advapi.GetTokenInformation(token, 1, buffer, needed.value, ctypes.byref(needed))), "cannot read process owner SID")
            sid_pointer = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
            sid = ctypes.c_void_p()
            require(bool(self.advapi.ConvertSidToStringSidW(sid_pointer, ctypes.byref(sid))), "cannot format process owner SID")
            try: owner = ctypes.wstring_at(sid)
            finally: self.kernel.LocalFree(sid)
            elevation = wintypes.DWORD()
            require(bool(self.advapi.GetTokenInformation(token, 20, ctypes.byref(elevation), ctypes.sizeof(elevation), ctypes.byref(needed))), "cannot read elevation state")
            session = wintypes.DWORD()
            require(bool(self.kernel.ProcessIdToSessionId(pid, ctypes.byref(session))), "cannot read process session")
            result = {"pid": pid, "created": created, "sid": owner, "session": session.value,
                      "elevated": bool(elevation.value), "exe": process.exe(), "ppid": process.ppid()}
            self._require_live(result, "process identity changed during inspection")
            return result
        finally:
            if token: self.kernel.CloseHandle(token)
            self.kernel.CloseHandle(handle)

    def current(self):
        value = self.identity(os.getpid())
        function = self.kernel.GetCurrentPackageFullName
        function.argtypes = [ctypes.POINTER(wintypes.UINT), wintypes.LPWSTR]
        function.restype = ctypes.c_long
        size = wintypes.UINT()
        status = function(ctypes.byref(size), None)
        if status == 15700: value["package_full_name"] = None
        else:
            require(status == 122, "cannot determine package identity")
            buffer = ctypes.create_unicode_buffer(size.value)
            require(function(ctypes.byref(size), buffer) == 0, "cannot read package identity")
            value["package_full_name"] = buffer.value
        return value

    @contextmanager
    def home_guard(self, path, expected_owner, *, create=False):
        """Pin the dedicated directory; never take ownership or rewrite its ACL."""
        path = Path(path).absolute()
        require(path.resolve() == path and not _is_link_or_reparse(path),
                "dedicated App home is redirected")
        parent = path.parent
        require(parent.is_dir() and parent.resolve() == parent and not _is_link_or_reparse(parent),
                "dedicated App home parent is redirected or missing")
        if create:
            current = self.identity(os.getpid())
            require(not current["elevated"] and current["sid"] == expected_owner,
                    "create the dedicated App home from the intended non-elevated account")

        kernel, advapi = self.kernel, self.advapi
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
                                     ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                       ctypes.c_void_p, wintypes.DWORD]
        kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL
        advapi.GetSecurityInfo.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
                                          ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p,
                                          ctypes.c_void_p, ctypes.c_void_p,
                                          ctypes.POINTER(ctypes.c_void_p)]
        advapi.GetSecurityInfo.restype = wintypes.DWORD
        invalid = ctypes.c_void_p(-1).value
        def pin(directory):
            # No FILE_SHARE_DELETE: neither admitted directory may be replaced
            # while configuration/preflight is using this guard.
            handle = kernel.CreateFileW(str(directory), 0x20080, 3, None, 3,
                                        0x02000000 | 0x00200000, None)
            require(handle not in (None, invalid), "cannot pin dedicated App home directory")
            try:
                attributes = (wintypes.DWORD * 2)()
                require(bool(kernel.GetFileInformationByHandleEx(handle, 9, attributes, 8)),
                        "cannot inspect dedicated App home directory")
                require(attributes[0] & 0x10 and not attributes[0] & 0x400,
                        "dedicated App home is a link or not a directory")
                return handle
            except BaseException:
                kernel.CloseHandle(handle)
                raise

        parent_handle = pin(parent)
        home_handle = None
        created = False
        try:
            if create:
                try:
                    path.mkdir()
                    created = True
                except FileExistsError:
                    pass  # The existing object's handle, type and owner are checked below.
            home_handle = pin(path)
            descriptor, owner_pointer = ctypes.c_void_p(), ctypes.c_void_p()
            try:
                require(advapi.GetSecurityInfo(home_handle, 1, 1, ctypes.byref(owner_pointer),
                                              None, None, None, ctypes.byref(descriptor)) == 0,
                        "cannot read dedicated App home owner")
                text = ctypes.c_void_p()
                require(bool(advapi.ConvertSidToStringSidW(owner_pointer, ctypes.byref(text))),
                        "cannot resolve dedicated App home owner")
                try:
                    actual_owner = ctypes.wstring_at(text)
                finally:
                    kernel.LocalFree(text)
            finally:
                if descriptor:
                    kernel.LocalFree(descriptor)
            require(actual_owner == expected_owner,
                    "dedicated App home owner differs from the intended Windows user; "
                    "quit the App and repair only this dedicated directory's owner, "
                    "preserving its existing permissions and child state")
            require(path.resolve() == path and not _is_link_or_reparse(path),
                    "dedicated App home changed during admission")
            yield {"owner_sid": actual_owner, "created": created,
                   "scope": "dedicated_home_directory_only", "permissions_changed": False}
        finally:
            if home_handle:
                kernel.CloseHandle(home_handle)
            kernel.CloseHandle(parent_handle)

    def package(self, expected):
        names = [*expected["files"], *expected["app_resources"]]
        payload = base64.b64encode(json.dumps(names).encode()).decode()
        script = r"""$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$p=Get-AppxPackage -Name OpenAI.Codex
if(@($p).Count -ne 1){throw 'Expected one installed current-user Codex package'}
$names=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('PAYLOAD'))|ConvertFrom-Json
$files=@{};foreach($name in $names){$files[$name]=(Get-FileHash -LiteralPath (Join-Path $p.InstallLocation $name) -Algorithm SHA256).Hash.ToLower()}
$exe=Get-AuthenticodeSignature -LiteralPath (Join-Path $p.InstallLocation 'app/ChatGPT.exe')
$signature=Get-AuthenticodeSignature -LiteralPath (Join-Path $p.InstallLocation 'AppxSignature.p7x')
[ordered]@{package=[ordered]@{full_name=$p.PackageFullName;family=$p.PackageFamilyName;version=$p.Version.ToString();publisher=$p.Publisher;location=$p.InstallLocation;signature_kind=$p.SignatureKind.ToString();status=$p.Status.ToString();development=$p.IsDevelopmentMode};exe_signature=@{status=$exe.Status.ToString();thumbprint=$exe.SignerCertificate.Thumbprint};package_signature_status=$signature.Status.ToString();files=$files}|ConvertTo-Json -Depth 6 -Compress
""".replace("PAYLOAD", payload)
        powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        command = [str(powershell), "-NoProfile", "-NonInteractive", "-EncodedCommand", base64.b64encode(script.encode("utf-16le")).decode()]
        try:
            output = subprocess.run(command, capture_output=True, timeout=300,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            # Do not expose the encoded command or captured subprocess output.
            raise LaunchError(
                "Installed App package file/signature verification timed out after 300 seconds; "
                "verification did not pass. Check Windows storage and certificate services "
                "before retrying the requested operation."
            ) from None
        require(output.returncode == 0, "cannot verify installed App package/signature/resources")
        return json.loads(output.stdout.decode("utf-8-sig"))

    def runtime_package_document(self, descriptor):
        """Read only this current user's installed own runtime and OS alias locator."""
        from .runtime_package import MODES
        application_id, alias = MODES[descriptor["mode"]]
        payload = base64.b64encode(json.dumps({"application_id": application_id, "alias": alias,
            "names": ["AppxManifest.xml", "AppxSignature.p7x"] +
                ["runtime/" + row["name"] for row in descriptor["outputs"]]}).encode()).decode()
        script = r"""$ErrorActionPreference='Stop'
[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false)
$d=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('PAYLOAD'))|ConvertFrom-Json
$p=Get-AppxPackage -Name CADSG.NativeRuntime
if(@($p).Count -ne 1){throw 'Expected one installed current-user CADSG runtime package'}
$files=@{};foreach($name in $d.names){$files[$name]=(Get-FileHash -LiteralPath (Join-Path $p.InstallLocation $name) -Algorithm SHA256).Hash.ToLower()}
$signature=Get-AuthenticodeSignature -LiteralPath (Join-Path $p.InstallLocation 'AppxSignature.p7x')
[xml]$m=Get-Content -LiteralPath (Join-Path $p.InstallLocation 'AppxManifest.xml') -Raw
$apps=@($m.SelectNodes("//*[local-name()='Application']")|Where-Object {$_.Id -eq $d.application_id})
if($apps.Count -ne 1){throw 'Runtime application ID differs'}
$matches=@($apps[0].SelectNodes(".//*[local-name()='ExecutionAlias']")|Where-Object {$_.Alias -eq $d.alias})
if($matches.Count -ne 1){throw 'Runtime execution alias differs'}
$directory=Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) ('Microsoft\WindowsApps\'+$p.PackageFamilyName)
[ordered]@{package=[ordered]@{name=$p.Name;full_name=$p.PackageFullName;family=$p.PackageFamilyName;version=$p.Version.ToString();publisher=$p.Publisher;location=$p.InstallLocation;signature_kind=$p.SignatureKind.ToString();status=$p.Status.ToString();development=$p.IsDevelopmentMode};package_signature_status=$signature.Status.ToString();signer_thumbprint=$signature.SignerCertificate.Thumbprint;files=$files;activation=@{alias=$d.alias;application_id=$d.application_id;executable=$apps[0].Executable;path=(Join-Path $directory $d.alias);known_alias_directory=$directory}}|ConvertTo-Json -Depth 7 -Compress
""".replace("PAYLOAD", payload)
        powershell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
        command = [str(powershell), "-NoProfile", "-NonInteractive", "-EncodedCommand",
                   base64.b64encode(script.encode("utf-16le")).decode()]
        try:
            result = subprocess.run(command, capture_output=True, timeout=300,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
        except subprocess.TimeoutExpired:
            raise LaunchError("Own runtime package verification timed out; no launch") from None
        require(result.returncode == 0, "cannot verify installed own runtime package/signature/alias")
        value = json.loads(result.stdout.decode("utf-8-sig"))
        alias_path = value["activation"]["path"]
        kernel = self.kernel
        kernel.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD,
            ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
        kernel.CreateFileW.restype = wintypes.HANDLE
        kernel.GetFileInformationByHandleEx.argtypes = [wintypes.HANDLE, ctypes.c_int,
                                                       ctypes.c_void_p, wintypes.DWORD]
        kernel.GetFileInformationByHandleEx.restype = wintypes.BOOL
        class TagInfo(ctypes.Structure):
            _fields_ = [("attributes", wintypes.DWORD), ("tag", wintypes.DWORD)]
        handle = kernel.CreateFileW(alias_path, 0x80, 7, None, 3, 0x00200000, None)
        require(handle and handle != ctypes.c_void_p(-1).value, "cannot pin own runtime execution alias")
        try:
            info = TagInfo()
            require(kernel.GetFileInformationByHandleEx(handle, 9, ctypes.byref(info), ctypes.sizeof(info)),
                    "cannot query runtime execution alias tag")
            require(info.attributes & 0x400 and not info.attributes & 0x10,
                    "runtime alias is not an OS application execution link")
            value["activation"]["reparse_tag"] = info.tag
        finally:
            kernel.CloseHandle(handle)
        return value

    def native_runtime_owner_snapshot(self):
        """Read the project's exact native owner value; export no other user's home."""
        import winreg
        sid = self.current()["sid"]
        for key_name in (r"SOFTWARE\CADSG\WindowsSandboxService\RegisteredCore",
                         r"SOFTWARE\CADSG\WindowsSandboxService"):
            try:
                with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_name, 0,
                                   winreg.KEY_QUERY_VALUE | winreg.KEY_WOW64_64KEY) as key:
                    raw, kind = winreg.QueryValueEx(key, "ProvisionedInstallation")
            except FileNotFoundError:
                continue
            require(kind == winreg.REG_SZ and isinstance(raw, str)
                    and len(raw.encode("utf-16-le")) <= 8192,
                    "project native owner record is invalid")
            value = json.loads(raw)
            require(isinstance(value, dict) and isinstance(value.get("user_sid"), str)
                    and isinstance(value.get("codex_home"), str),
                    "project native owner record has no bound owner/home")
            owned = value["user_sid"] == sid
            runtime = value.get("runtime")
            require(runtime is None or isinstance(runtime, dict), "project native runtime record is invalid")
            if key_name.endswith("RegisteredCore"):
                require(runtime is not None, "RegisteredCore owner key has no runtime binding")
            return {"state_absent": False, "owner_sid": sid if owned else None,
                    "home": value["codex_home"] if owned else None,
                    "runtime_present": runtime is not None,
                    "namespace_id": value.get("namespace_id") if owned else None,
                    "owned_by_current_user": owned}
        return {"state_absent": True, "owner_sid": None, "home": None, "runtime_present": False}

    def runtime_process_activation(self, identity):
        """Query only a newly owned, birth-checked process through a held handle."""
        self._require_live(identity, "runtime PID changed before package query")
        handle = self.kernel.OpenProcess(0x1000, False, identity["pid"])
        require(bool(handle), "cannot pin runtime package identity")
        try:
            value = {}
            for api, field in (("GetPackageFamilyName", "family"),
                               ("GetPackageFullName", "full_name"),
                               ("GetApplicationUserModelId", "application_id")):
                function = getattr(self.kernel, api)
                function.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.UINT), wintypes.LPWSTR]
                function.restype = ctypes.c_long
                size = wintypes.UINT()
                status = function(handle, ctypes.byref(size), None)
                require(status == 122 and 1 < size.value <= 32768,
                        "runtime has no valid kernel " + field)
                buffer = ctypes.create_unicode_buffer(size.value)
                require(function(handle, ctypes.byref(size), buffer) == 0,
                        "cannot read runtime kernel " + field)
                value[field] = buffer.value
            self._require_live(identity, "runtime PID changed during package query")
            return value
        finally:
            self.kernel.CloseHandle(handle)

    def program_files_roots(self):
        # Match the native runtime's OS-owned installation-root discovery.
        shell = ctypes.WinDLL("shell32", use_last_error=True)
        ole = ctypes.WinDLL("ole32", use_last_error=True)
        shell.SHGetKnownFolderPath.argtypes = [ctypes.c_void_p, wintypes.DWORD,
                                               wintypes.HANDLE, ctypes.POINTER(ctypes.c_void_p)]
        shell.SHGetKnownFolderPath.restype = ctypes.c_long
        ole.CoTaskMemFree.argtypes = [ctypes.c_void_p]
        roots = []
        for identifier in ("905e63b6-c1bf-494e-b29c-65b732d3d21a",
                           "7c5a40ef-a0fb-4bfc-874a-c0f2e0b9fa8e"):
            guid = ctypes.create_string_buffer(uuid.UUID(identifier).bytes_le)
            pointer = ctypes.c_void_p()
            status = shell.SHGetKnownFolderPath(guid, 0x400, None, ctypes.byref(pointer))
            try:
                if status == 0 and pointer:
                    path = Path(ctypes.wstring_at(pointer))
                    if path not in roots:
                        roots.append(path)
            finally:
                if pointer:
                    ole.CoTaskMemFree(pointer)
        return roots

    def trusted_git(self):
        def probe(path):
            # Git --version performs no network/authentication or repository work.
            # Do not pass inherited provider, Git or credential-helper selectors.
            environment = {key: value for key, value in os.environ.items()
                           if key.upper() in {"SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "TEMP", "TMP"}}
            result = subprocess.run([str(path), "--version"], env=environment, cwd=path.parent,
                                    stdin=subprocess.DEVNULL, capture_output=True, timeout=5,
                                    creationflags=subprocess.CREATE_NO_WINDOW)
            if result.returncode != 0:
                raise ValueError("version probe failed")
            return result.stdout.decode("ascii").strip()
        try:
            return trusted_git_from_roots(self.program_files_roots(), probe)
        except OSError:
            return {"status": "discovery_failed", "path": None, "version": None,
                    "diagnostic": "Windows KnownFolder lookup failed",
                    "worktree_gate": "pending_app_validation"}

    def git_path_contains(self, identity, directory):
        self._require_live(identity, "owned process exited or PID was reused")
        values = psutil.Process(identity["pid"]).environ()
        found = path_contains_directory(values, directory)
        self._require_live(identity, "owned process changed during Git environment observation")
        return found

    def existing_apps(self, owner):
        result = []
        for process in psutil.process_iter(["pid", "name"]):
            if (process.info.get("name") or "").lower() != "chatgpt.exe": continue
            try: identity = self.identity(process.pid)
            except psutil.NoSuchProcess: continue
            except (psutil.Error, OSError, LaunchError) as error:
                raise LaunchError("cannot establish ownership of an existing ChatGPT process; no launch") from error
            if identity["sid"] == owner["sid"] and "openai.codex_" in identity["exe"].lower(): result.append(identity)
        return result

    def live(self, identity):
        try: return psutil.Process(identity["pid"]).create_time() == identity["created"]
        except psutil.NoSuchProcess: return False

    def _require_live(self, identity, message):
        # A new Process instance avoids psutil's cached create_time. Natural
        # disappearance stays NoSuchProcess; PID reuse remains a hard mismatch.
        require(psutil.Process(identity["pid"]).create_time() == identity["created"], message)

    def selected_environment(self, identity):
        self._require_live(identity, "owned process exited or PID was reused")
        values = psutil.Process(identity["pid"]).environ()
        result = dict.fromkeys(CONTROLLED)
        selected = set()
        for key, value in values.items():
            name = key.upper()
            if name in CONTROLLED:
                require(name not in selected, "ambiguous controlled Windows environment selector")
                selected.add(name); result[name] = value
        self._require_live(identity, "owned process changed during environment observation")
        return result

    def diagnostic_environment(self, identity):
        self._require_live(identity, "owned process changed before diagnostic environment observation")
        values = [value for key, value in psutil.Process(identity["pid"]).environ().items()
                  if key.upper() == TRACE_ROOT_ENV]
        require(len(values) <= 1, "ambiguous official trace environment")
        self._require_live(identity, "owned process changed during diagnostic environment observation")
        return {TRACE_ROOT_ENV: values[0] if values else None}

    def profile_arguments(self, identity):
        self._require_live(identity, "owned App exited or PID was reused")
        args = psutil.Process(identity["pid"]).cmdline()[1:]
        self._require_live(identity, "owned App changed during argument observation")
        return [arg for arg in args if arg.lstrip("-/").casefold().startswith("user-data-dir")]

    def native_role(self, identity):
        try:
            process = psutil.Process(identity["pid"])
            self._require_live(identity, "owned native PID was reused")
            args = process.cmdline()
            self._require_live(identity, "owned native identity changed")
        except psutil.NoSuchProcess:
            return "exited-native-command"
        return "app-server" if is_app_server_command(args) else "other-native-command"

    @contextmanager
    def launch_lock(self, sid):
        name = "Global" + chr(92) + "CodexAppDeepSeekLaunch-" + hashlib.sha256(sid.encode()).hexdigest()
        self.kernel.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
        self.kernel.CreateMutexW.restype = wintypes.HANDLE
        self.kernel.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self.kernel.ReleaseMutex.argtypes = [wintypes.HANDLE]
        handle = self.kernel.CreateMutexW(None, False, name)
        require(bool(handle), "cannot acquire owned launch lock")
        acquired = False
        try:
            acquired = self.kernel.WaitForSingleObject(handle, 0) in (0, 0x80)
            require(acquired, "another launch attempt is active for this user")
            yield
        finally:
            if acquired: self.kernel.ReleaseMutex(handle)
            self.kernel.CloseHandle(handle)

    def spawn(self, executable, arguments, environment, cwd):
        return subprocess.Popen([str(executable), *arguments], cwd=cwd, env=environment,
                                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def descendants(self, root):
        """Strict full-tree inspection; do not treat sandbox accounts as the App user."""
        return self._descendants(root)

    def native_control_plane(self, root, *, executable, expected_sha256, environment, auxiliary_exits=None, runtime_package=None):
        """Discover App backends, stopping only at fully bound native main loops.

        The native runtime owns its tool/MCP/sandbox workload tree. Its restricted
        accounts are not identities of the App frontend or app-server itself.
        This method neither adopts nor validates those workload descendants.
        """
        require(isinstance(expected_sha256, str)
                and re.fullmatch("[0-9a-f]{64}", expected_sha256) is not None,
                "invalid native control-plane checksum")
        require(isinstance(environment, dict) and set(environment) == CONTROLLED
                and all(isinstance(value, str) and value for value in environment.values())
                and environment["CODEX_APP_SERVER_FORCE_CLI"] == "1",
                "invalid native control-plane environment")
        native_path = Path(executable)
        if runtime_package is None:
            require(native_path.is_absolute()
                    and Path(environment["CODEX_CLI_PATH"]).resolve() == native_path.resolve(),
                    "native control-plane executable binding differs")
        else:
            from . import runtime_package as packages
            packages.validate_control_binding(runtime_package, executable, environment)

        def check_path():
            require(not any(_is_link_or_reparse(part)
                            for part in (native_path, *native_path.parents)),
                    "native control-plane path is redirected")
            require(native_path.is_file(), "native control-plane image is missing")

        check_path()
        self._require_live(root, "owned App changed before control-plane observation")
        require(self.identity(root["pid"]) == root and root.get("elevated") is False,
                "owned App identity differs before control-plane observation")
        require(self.selected_environment(root) == environment,
                "App control-plane environment differs")
        boundaries = []

        def verify_boundary(identity, *, role_checked=False):
            self._require_live(identity, "native boundary exited or PID was reused")
            require(identity["sid"] == root["sid"] and identity["session"] == root["session"]
                    and identity.get("elevated") is False,
                    "native boundary owner/session/elevation differs")
            require(Path(identity["exe"]).resolve() == native_path.resolve(),
                    "native control-plane selected an unexpected executable")
            check_path()
            if not role_checked:
                require(self.native_role(identity) == "app-server",
                        "native boundary is not the app-server main loop")
            require(sha(native_path) == expected_sha256,
                    "native control-plane image checksum differs")
            require(self.selected_environment(identity) == environment,
                    "native control-plane environment differs")
            if runtime_package is not None:
                packages.verify_activation(runtime_package, self.runtime_process_activation(identity))
            require(self.identity(identity["pid"]) == identity,
                    "native boundary identity changed")
            self._require_live(identity, "native boundary changed during validation")

        def stop_at(identity):
            if Path(identity["exe"]).name.casefold() != "codex.exe":
                return False
            role = self.native_role(identity)
            require(role in {"app-server", "other-native-command", "exited-native-command"},
                    "native boundary role is unverified")
            if role != "app-server":
                return False
            verify_boundary(identity, role_checked=True)
            boundaries.append(identity)
            return True

        require(auxiliary_exits is None or (isinstance(auxiliary_exits, list) and not auxiliary_exits),
                "invalid auxiliary exit diagnostic collector")
        found = self._descendants(root, stop_at=stop_at, auxiliary_exits=auxiliary_exits)
        for identity in boundaries:
            verify_boundary(identity)
        require(self.identity(root["pid"]) == root and self.live(root)
                and self.selected_environment(root) == environment,
                "owned App changed after control-plane observation")
        return found

    def _descendants(self, root, *, stop_at=None, auxiliary_exits=None):
        self._require_live(root, "owned App exited; refusing to adopt a replacement")
        found = [root]
        edges = []
        seen = {root["pid"]: root["created"]}
        native_parents = {}
        retired = {}

        def retire_auxiliary(identity, error):
            # Only the explicit control-plane projection may ignore a confirmed
            # natural auxiliary exit. Generic tree inspection remains strict.
            if (auxiliary_exits is None or stop_at is None or error.pid != identity["pid"]
                    or identity["pid"] == root["pid"]
                    or Path(identity["exe"]).name.casefold() == "codex.exe"):
                return False
            require(identity["sid"] == root["sid"] and identity["session"] == root["session"]
                    and identity.get("elevated") is False,
                    "exited auxiliary owner/session/elevation is unverified")
            try:
                self._require_live(identity, "App auxiliary PID was reused")
            except psutil.NoSuchProcess as confirmed:
                if confirmed.pid != identity["pid"]:
                    raise
                retired[identity["pid"]] = (identity, error)
                require(len(retired) <= 64, "too many auxiliary exits in one observation")
                return True
            # A transient failure or a replacement never authorizes retirement.
            raise error

        for parent in found:
            try:
                self._require_live(parent, "owned App ancestor changed before observation")
                if stop_at is not None and stop_at(parent):
                    continue
                process = psutil.Process(parent["pid"])
                require(process.create_time() == parent["created"], "owned App ancestor PID was reused")
                try:
                    children = process.children(recursive=False)
                except psutil.NoSuchProcess:
                    self._require_live(parent, "owned App ancestor changed during child enumeration")
                    raise
                self._require_live(parent, "owned App ancestor changed during child enumeration")
                for child in children:
                    self._require_live(parent, "owned App ancestor changed before child inspection")
                    created = None
                    identity = None
                    try:
                        created = child.create_time()
                        candidate = psutil.Process(child.pid)
                        require(candidate.create_time() == created, "App child PID changed after enumeration")
                        ppid = candidate.ppid()
                        require(ppid == parent["pid"] and created >= parent["created"],
                                "App child does not have the observed direct parent")
                        identity = self.identity(child.pid)
                        require(identity["created"] == created and identity["ppid"] == parent["pid"],
                                "App child changed during direct edge observation")
                        require(identity["sid"] == root["sid"] and identity["session"] == root["session"],
                                "unexpected owner/session in App process tree")
                        if Path(identity["exe"]).name.casefold() == "codex.exe":
                            # Protect this edge before final admission too: losing
                            # a native candidate's parent must never prune it.
                            native_parents[identity["pid"]] = parent["pid"]
                        self._require_live(parent, "App parent changed during observation")
                        self._require_live(identity, "App child changed during observation")
                    except psutil.NoSuchProcess as error:
                        # Only a still-unadmitted child may disappear. Never turn
                        # a lost parent or already identified native actor into a
                        # skipped auxiliary, and recheck birth to reject PID reuse.
                        if error.pid != child.pid or (identity is not None
                                and Path(identity["exe"]).name.casefold() == "codex.exe"):
                            raise
                        if created is not None:
                            try: self._require_live({"pid": child.pid, "created": created}, "App child PID was reused")
                            except psutil.NoSuchProcess: continue
                        else:
                            try: psutil.Process(child.pid)
                            except psutil.NoSuchProcess: continue
                        raise LaunchError("App child identity could not be established")
                    require(identity["pid"] not in seen, "duplicate or cyclic App descendant")
                    seen[identity["pid"]] = identity["created"]
                    found.append(identity)
                    edges.append((parent, identity))
            except psutil.NoSuchProcess as error:
                if not retire_auxiliary(parent, error):
                    raise

        # Close the cached native ancestry before accepting any provisional
        # retirement, including native edges observed but not yet admitted.
        parents = {child["pid"]: parent["pid"] for parent, child in edges}
        parents.update(native_parents)
        protected = {root["pid"], *native_parents}
        for pid in tuple(protected):
            while pid in parents and parents[pid] not in protected:
                pid = parents[pid]
                protected.add(pid)
        for pid in protected:
            if pid in retired:
                raise retired[pid][1]
        for parent, child in edges:
            for identity in (parent, child):
                if identity["pid"] in retired:
                    continue
                try:
                    self._require_live(identity, "App process expired before readiness")
                except psutil.NoSuchProcess as error:
                    if identity["pid"] in protected or not retire_auxiliary(identity, error):
                        raise
        self._require_live(root, "owned App exited during observation")
        if auxiliary_exits is not None:
            auxiliary_exits.extend({"pid": identity["pid"], "created": identity["created"],
                                    "ppid": identity["ppid"], "executable_name": Path(identity["exe"]).name,
                                    "reason": "natural_exit_outside_native_ancestry"}
                                   for identity, _ in retired.values())
        return [identity for identity in found if identity["pid"] not in retired]



def _profile_path(value):
    require(isinstance(value, str) and value and not any(c in value for c in "\x00\r\n"),
            "existing App has an ambiguous profile path")
    path = Path(value)
    require(path.is_absolute(), "existing App has a relative profile path")
    value = str(path.resolve())
    if value.startswith("\\\\?\\UNC\\"):
        value = "\\\\" + value[8:]
    elif value.startswith("\\\\?\\"):
        value = value[4:]
    return ntpath.normcase(ntpath.normpath(value))


def require_disjoint_apps(backend, owner, paths):
    """Inspect identity and four selectors only; never adopt or control another App."""
    wanted_home = _profile_path(str(paths["home"]))
    wanted_frontend = _profile_path(str(paths["frontend"]))
    keys = ("pid", "created", "sid", "session", "exe")
    for observed in backend.existing_apps(owner):
        try:
            require(all(key in observed for key in keys), "existing App identity is incomplete")
            actual = backend.identity(observed["pid"])
            require(all(actual[key] == observed[key] for key in keys)
                    and actual["sid"] == owner["sid"] and backend.live(observed),
                    "existing App identity changed; no launch")
            environment = backend.selected_environment(observed)
            require(isinstance(environment, dict) and set(environment) == CONTROLLED
                    and all(value is None or isinstance(value, str) and bool(value)
                            for value in environment.values()),
                    "existing App selectors could not be established")
            arguments = backend.profile_arguments(observed)
            require(isinstance(arguments, list) and len(arguments) <= 1
                    and all(isinstance(arg, str) and arg.startswith("--user-data-dir=")
                            for arg in arguments), "existing App browser selector is ambiguous")
            home = environment["CODEX_HOME"]
            frontend = environment["CODEX_ELECTRON_USER_DATA_PATH"]
            browser = arguments[0].split("=", 1)[1] if arguments else None
            require(home is None or _profile_path(home) != wanted_home,
                    "an existing App uses the dedicated native home")
            require(all(value is None or _profile_path(value) != wanted_frontend
                        for value in (frontend, browser)),
                    "an existing App uses the dedicated frontend or browser profile")
            # The fixed production App's unset selectors use stock default homes;
            # these differ from our validated dedicated local-data directories.
            after = backend.identity(observed["pid"])
            require(all(after[key] == observed[key] for key in keys)
                    and backend.live(observed), "existing App changed during profile observation")
        except psutil.NoSuchProcess:
            # A second fresh identity read proves disappearance. A replacement
            # (even at the same PID) is uncertain and never treated as that App.
            try:
                backend.identity(observed["pid"])
            except psutil.NoSuchProcess:
                continue
            raise LaunchError("existing App disappeared or was replaced during observation")
        except (psutil.Error, OSError, KeyError, TypeError, AttributeError) as error:
            raise LaunchError("cannot establish an existing App's profile; no launch") from error


def validate_request(request, backend=None):
    require(set(request) <= {"baseline", "native_manifest", "native_manifest_sha256", "app_args", "timeout_seconds", "expected_config_sha256", "trace_root"}, "unknown launch request field")
    backend = backend or WindowsBackend()
    owner = backend.current()
    require(owner["session"] > 0 and not owner["elevated"], "use the current non-elevated interactive desktop session")
    expected = baseline(Path(request["baseline"]).resolve())
    package = verify_package(backend.package(expected), expected)
    paths = defaults()
    trace_root = validated_trace_root(request["trace_root"], paths, backend, owner["sid"]) if "trace_root" in request else None
    manifest_path = Path(request["native_manifest"]).resolve()
    bundle = verify_launch_bundle(manifest_path, request["native_manifest_sha256"],
                                  expected, backend, owner, paths["home"])
    validate_state_paths(paths)
    require(paths["home"].is_dir() and (paths["home"] / "config.toml").is_file(), "dedicated App home must be configured before launch")
    with backend.home_guard(paths["home"], owner["sid"]) as home_identity:
        config_bytes = (paths["home"] / "config.toml").read_bytes()
    config_sha256 = hashlib.sha256(config_bytes).hexdigest()
    if "expected_config_sha256" in request:
        expected_config = request["expected_config_sha256"]
        require(isinstance(expected_config, str) and re.fullmatch(r"[0-9a-f]{64}", expected_config), "invalid expected configuration checksum")
        require(config_sha256 == expected_config, "configuration changed after installed preflight")
    config = tomllib.loads(config_bytes.decode("utf-8-sig"))
    require(config.get("model_provider") == "codex_app_deepseek", "dedicated home does not select this gateway")
    # A config override must not redirect sessions out of the dedicated native home.
    if config.get("sqlite_home") is not None:
        require(isinstance(config["sqlite_home"], str), "sqlite_home must be a path")
        database = Path(config["sqlite_home"])
        if not database.is_absolute(): database = paths["home"] / database
        require(database.resolve().is_relative_to(paths["home"].resolve()), "sqlite_home escapes the dedicated App home")
    require_disjoint_apps(backend, owner, paths)
    arguments = request.get("app_args", [])
    require(isinstance(arguments, list) and all(isinstance(arg, str) and arg in SAFE_APP_ARGS for arg in arguments) and len(arguments) == len(set(arguments)), "unsupported or duplicate App argument")
    timeout = request.get("timeout_seconds", 60)
    require(type(timeout) is int and 5 <= timeout <= 120, "invalid launch observation timeout")
    selector = bundle.get("runtime_package", {}).get("alias_path", bundle["files"]["codex.exe"]["path"])
    environment = {"CODEX_HOME": str(paths["home"]), "CODEX_CLI_PATH": selector,
                   "CODEX_ELECTRON_USER_DATA_PATH": str(paths["frontend"]), "CODEX_APP_SERVER_FORCE_CLI": "1"}
    return {"owner": {key: owner[key] for key in ("sid", "session")}, "package": package, "bundle": bundle,
            "environment": environment, "app_args": ["--user-data-dir=" + str(paths["frontend"]), *arguments], "timeout_seconds": timeout,
            "config_sha256": config_sha256, "home_identity": home_identity,
            "native_log_directory": str(paths["native_logs"]),
            **({"trace_root": trace_root} if trace_root is not None else {}),
            "git_dependency": backend.trusted_git(),
            "scope": "validated inputs only; actual App/frontend behavior requires acceptance"}

def prepare_launch(request, backend=None):
    checked = validate_request(request, backend)
    root = defaults()["launches"]
    root.mkdir(parents=True, exist_ok=True); _private(root, directory=True)
    run = root / uuid.uuid4().hex
    run.mkdir(); _private(run, directory=True)
    if "trace_root" in checked:
        backend = backend or WindowsBackend()
        checked["trace"] = prepare_trace_directory(checked["trace_root"], run.name, backend, checked["owner"]["sid"])
    plan = {"schema_version": 1, "request": request, "validated": checked, "launcher_sha256": sha(__file__),
            "created_at": time.time(), "replay_allowed": False}
    path = run / "plan.json"; save(path, plan)
    return {"run_dir": str(run), "plan": str(path), "plan_sha256": sha(path),
            "package_family_name": checked["package"]["package"]["family"], "app_id": "App"}

def inside_launch(plan_path, expected_sha, backend=None):
    backend = backend or WindowsBackend(); plan_path = Path(plan_path)
    validate_state_paths(defaults())
    require(sha(plan_path) == expected_sha, "launch plan checksum mismatch")
    plan = load_json(plan_path)
    require(plan["schema_version"] == 1 and plan["launcher_sha256"] == sha(__file__), "launcher changed after admission")
    run = plan_path.parent
    require(run.parent.resolve() == defaults()["launches"].resolve(), "plan is outside owned receipt directory")
    started = run / "started.json"
    with started.open("xb") as handle: handle.write(b"{}")
    _private(started)
    record = {"schema_version": 1, "status": "admitted", "plan_sha256": expected_sha, "replay_allowed": False}
    save(started, record)
    try:
        current = backend.current()
        record.update(helper=current, observed_at=time.time())
        save(started, record)
        require({key: current[key] for key in ("sid", "session")} == plan["validated"]["owner"], "helper changed user or desktop session")
        expected = baseline(Path(plan["request"]["baseline"]))
        require(current["package_full_name"] == expected["package_full_name"], "helper lacks the fixed DesktopPackage identity")
        with backend.launch_lock(current["sid"]), ExitStack() as diagnostic_guards:
            checked = validate_request(plan["request"], backend)
            require(checked["config_sha256"] == plan["validated"]["config_sha256"], "configuration changed after admission")
            require(checked["environment"] == plan["validated"]["environment"], "launch target changed after admission")
            frontend = Path(checked["environment"]["CODEX_ELECTRON_USER_DATA_PATH"])
            frontend.mkdir(parents=True, exist_ok=True); _private(frontend, directory=True)
            trace = plan["validated"].get("trace")
            require(checked.get("trace_root") == plan["validated"].get("trace_root"), "trace root changed after admission")
            if "trace_root" in checked:
                require(isinstance(trace, dict) and trace.get("requested_root") == checked["trace_root"]
                        and trace.get("owner_sid") == current["sid"]
                        and trace.get("environment_name") == TRACE_ROOT_ENV, "trace plan differs")
                trace_path = Path(checked["trace_root"]) / ("cadsg-" + run.name)
                require(trace.get("directory") == str(trace_path), "trace directory escaped its admitted container")
                diagnostic_guards.enter_context(backend.home_guard(trace_path, current["sid"]))
                stat = trace_path.stat()
                require(trace.get("directory_identity") == {"volume": stat.st_dev, "file_id": stat.st_ino},
                        "trace directory was replaced after admission")
                record["trace"] = trace
            else:
                require(trace is None, "unrequested trace plan")
            environment, removed = child_environment(dict(os.environ), checked["environment"],
                                                     trace_directory=trace["directory"] if trace else None)
            environment, git_dependency = git_child_environment(environment, checked["git_dependency"])
            executable = Path(checked["package"]["package"]["location"]) / expected["executable"]
            record.update(status="creating_process", owner=checked["owner"], expected_environment=checked["environment"],
                          removed_selector_names=removed, helper=current, app_executable=str(executable),
                          git_dependency=git_dependency)
            _save_receipt(run / "receipt.json", record)
            child = backend.spawn(executable, checked["app_args"], environment, executable.parent)
            record.update(created_pid=child.pid, created_at=time.time())
            _save_receipt(run / "receipt.json", record)
            identity = backend.identity(child.pid)
            require(Path(identity["exe"]).resolve() == executable.resolve(), "launched executable differs from the fixed App")
            require(identity["sid"] == current["sid"] and identity["session"] == current["session"], "App owner/session differs")
            record.update(status="observing_backend", app=identity, app_environment=backend.selected_environment(identity))
            require(record["app_environment"] == checked["environment"], "App did not inherit the controlled environment")
            if trace:
                record["trace_app_environment"] = backend.diagnostic_environment(identity)
                require(record["trace_app_environment"] == {TRACE_ROOT_ENV: trace["directory"]},
                        "App did not inherit the explicitly admitted official trace root")
            if git_dependency["status"] == "available":
                try:
                    git_dependency["observed_app_path_contains"] = backend.git_path_contains(
                        identity, git_dependency["directory"])
                except (psutil.Error, OSError, LaunchError):
                    git_dependency["observed_app_path_contains"] = None
                if git_dependency["observed_app_path_contains"] is not True:
                    git_dependency["diagnostic"] = "App Git PATH inheritance is unverified"
            record["app_profile_arguments"] = backend.profile_arguments(identity)
            require(record["app_profile_arguments"] == ["--user-data-dir=" + str(frontend)],
                    "App did not inherit the controlled browser profile argument")
            _save_receipt(run / "receipt.json", record)
            record.update(validation_scope=CONTROL_PLANE_SCOPE,
                          native_workload_scope=NATIVE_WORKLOAD_SCOPE)
            deadline = time.monotonic() + checked["timeout_seconds"]
            while time.monotonic() < deadline:
                try:
                    auxiliary_exits = []
                    control_options = {"executable": checked["bundle"]["files"]["codex.exe"]["path"],
                        "expected_sha256": checked["bundle"]["files"]["codex.exe"]["sha256"],
                        "environment": checked["environment"], "auxiliary_exits": auxiliary_exits}
                    if "runtime_package" in checked["bundle"]:
                        control_options["runtime_package"] = checked["bundle"]["runtime_package"]
                    observed = backend.native_control_plane(identity, **control_options)
                    record["excluded_auxiliary_processes"] = auxiliary_exits
                    natives = [item for item in observed if Path(item["exe"]).name.lower() == "codex.exe"]
                    for item in natives:
                        require(Path(item["exe"]).resolve() == Path(checked["bundle"]["files"]["codex.exe"]["path"]).resolve(), "App selected an unexpected native runtime")
                        item["native_role"] = backend.native_role(item)
                        if item["native_role"] != "app-server":
                            continue
                        actual_sha = sha(Path(item["exe"]))
                        require(actual_sha == checked["bundle"]["files"]["codex.exe"]["sha256"], "native runtime bytes changed after preflight")
                        item["observed_executable_sha256"] = actual_sha
                        observed = backend.selected_environment(item)
                        require(observed == checked["environment"], "native runtime selected a different home or controlled environment")
                        item["controlled_environment"] = observed
                        if trace:
                            item["diagnostic_environment"] = backend.diagnostic_environment(item)
                            require(item["diagnostic_environment"] == {TRACE_ROOT_ENV: trace["directory"]},
                                    "native backend did not inherit the explicitly admitted official trace root")
                        if "runtime_package" in checked["bundle"]:
                            from . import runtime_package as packages
                            activation = backend.runtime_process_activation(item)
                            packages.verify_activation(checked["bundle"]["runtime_package"], activation)
                            item["runtime_activation"] = activation
                    servers = [item for item in natives if item["native_role"] == "app-server"]
                    if servers:
                        record.update(status="started_expected_backend", native_processes=servers,
                                      frontend_path_source="official environment plus native user-data-dir switch; requires App I/O and GUI acceptance",
                                      app_runtime_acceptance=False, finished_at=time.time())
                        _save_receipt(run / "receipt.json", record); return record
                except psutil.NoSuchProcess:
                    # Only natural child disappearance retries under the same
                    # admitted App. Reuse/owner/unknown errors remain failures.
                    require(backend.live(identity), "original App exited during backend observation")
                time.sleep(0.2)
            raise LaunchError("expected backend not observed; inspect receipt, do not automatically retry")
    except Exception as error:
        record.update(status="failed_or_unverified", error_type=type(error).__name__, error=str(error), finished_at=time.time())
        _save_receipt(run / "receipt.json", record); raise

def prepare_runtime(manifest_path, manifest_sha, baseline_path, backend=None, *, resources_receipt=None):
    manifest_path = Path(manifest_path); expected = baseline(baseline_path); backend = backend or WindowsBackend()
    current = backend.current()
    with backend.launch_lock(current["sid"]):
        paths = defaults(); validate_state_paths(paths)
        require_disjoint_apps(backend, current, paths)
        package = verify_package(backend.package(expected), expected)
        require(isinstance(manifest_sha, str) and len(manifest_sha) == 64 and sha(manifest_path) == manifest_sha,
                "native manifest checksum mismatch")
        from . import runtime_package as packages
        packaged = load_json(manifest_path).get("kind") == packages.KIND
        if packaged:
            bundle = verify_launch_bundle(manifest_path, manifest_sha, expected, backend, current, paths["home"])
        else:
            bundle = verify_bundle(manifest_path, manifest_sha, expected, prepared=False)
        from . import native_app_tools as tools
        tools.preflight(paths["home"], for_setup=False)
        receipt = resources_receipt or paths["home"] / ".cadsg-native-tools/resources.json"
        with backend.home_guard(paths["home"], current["sid"], create=True):
            resources = tools.bind_resources(package["package"]["location"], paths["home"], receipt,
                owner_sid=current["sid"], package_full_name=expected["package_full_name"])
        if packaged:
            # The own signed package already owns its CodeModeHost and helpers.
            # Bind only original App plugin resources; retain the admitted descriptor.
            return {"manifest": bundle["manifest"], "manifest_sha256": bundle["manifest_sha256"],
                    "runtime_executed": False, "bundled_resources": resources,
                    "runtime_package": bundle["runtime_package"]}
        root = manifest_path.parent.resolve(); outputs = list(bundle["files"].values())
        path = root / "app-runtime-manifest.json"
        require(path != manifest_path.resolve(), "raw build manifest must have a separate filename")
        own_host = load_json(manifest_path).get("kind") == "cadsg-native-source-bundle"
        for name, relative in ({} if own_host else expected["required_private_sidecars"]).items():
            source = Path(package["package"]["location"]) / relative; destination = root / name
            wanted = expected["app_resources"][relative]["sha256"]
            require(not destination.is_symlink(), "private sidecar cannot be a symlink")
            if destination.exists(): require(sha(destination) == wanted, "private sidecar exists with different bytes")
            else:
                temporary = root / (name + "." + uuid.uuid4().hex + ".partial")
                try:
                    shutil.copy2(source, temporary)
                    require(sha(temporary) == wanted, "sidecar copy checksum mismatch")
                    temporary.replace(destination)
                finally:
                    if temporary.exists(): temporary.unlink()
            outputs.append({"name": name, "path": str(destination), "sha256": wanted, "size": destination.stat().st_size,
                            "source": {"kind": "official_app_package", "package": expected["package_full_name"], "relative_path": relative}})
        result = {"schema_version": 1, "status": "prepared-not-app-qualified", "source_sha256": bundle["source_sha256"],
                  "native_version": bundle["native_version"], "upstream_commit": bundle["upstream_commit"],
                  "native_build_manifest": {"file": manifest_path.name, "sha256": manifest_sha},
                  "app_baseline_sha256": BASELINE_SHA256, "app_package_full_name": expected["package_full_name"],
                  "outputs": outputs, "app_resource_sources": expected["app_resources"], "runtime_executed": False, "app_qualified": False}
        if own_host:
            raw = load_json(manifest_path)
            result.update(kind="cadsg-native-source-bundle", runtime_mode="legacy",
                          build_lock_sha256=raw["build_lock_sha256"], candidate_revision=raw["candidate_revision"])
            for row in result["outputs"]:
                row["producer_id"] = "O165"
            result["app_resource_sources"] = {name: value for name, value in expected["app_resources"].items()
                                               if name not in expected["required_private_sidecars"].values()}
        if path.exists(): require(load_json(path) == result, "prepared manifest already exists with different inputs")
        else: save(path, result)
        verify_bundle(path, sha(path), expected)
        return {"manifest": str(path), "manifest_sha256": sha(path), "runtime_executed": False,
                "bundled_resources": resources}

def current_backend_observation(run, plan, receipt, backend):
    """Observe one current generation under the same original App; never adopt a replacement."""
    result = {"status": "unknown", "observed_at": time.time(), "native_processes": [],
              "excluded_native_processes": [], "excluded_auxiliary_processes": [], "app_runtime_acceptance": False,
              "validation_scope": CONTROL_PLANE_SCOPE,
              "native_workload_scope": NATIVE_WORKLOAD_SCOPE}
    stage = "original_launch_unverified"
    try:
        require(receipt.get("status") == "started_expected_backend", "original launch unverified")
        receipt_path = Path(run) / "receipt.json"
        original_bytes = receipt_path.read_bytes()
        result["original_receipt_sha256"] = hashlib.sha256(original_bytes).hexdigest()
        stage = "launch_plan_unverified"
        require(sha(Path(run) / "plan.json") == receipt["plan_sha256"], "launch plan changed")
        validated = plan["validated"]
        expected = validated["environment"]
        require(set(expected) == CONTROLLED and expected["CODEX_APP_SERVER_FORCE_CLI"] == "1",
                "controlled launch environment changed")
        stage = "original_app_not_live"
        original = receipt["app"]
        require(backend.live(original), "original App no longer live")
        actual = backend.identity(original["pid"])
        require(all(actual[key] == original[key] for key in ("pid", "created", "sid", "session", "exe")),
                "original App identity changed")
        require({key: actual[key] for key in ("sid", "session")} == validated["owner"],
                "App owner/session differs from launch")
        require(actual["sid"] == backend.current()["sid"], "observer belongs to another user")
        require(Path(actual["exe"]).resolve() == Path(receipt["app_executable"]).resolve(),
                "App executable differs from launch")
        stage = "app_environment_unverified"
        require(backend.selected_environment(actual) == expected, "App controlled environment changed")
        stage = "native_manifest_unverified"
        manifest_path = Path(plan["request"]["native_manifest"])
        manifest_sha = plan["request"]["native_manifest_sha256"]
        require(sha(manifest_path) == manifest_sha == validated["bundle"]["manifest_sha256"],
                "native manifest changed")
        require(manifest_path.resolve() == Path(validated["bundle"]["manifest"]).resolve(),
                "native manifest path changed")
        native = validated["bundle"]["files"]["codex.exe"]
        manifest = load_json(manifest_path)
        rows = [item for item in manifest["outputs"] if item["name"] == "codex.exe"]
        require(len(rows) == 1 and rows[0]["sha256"] == native["sha256"]
                and Path(rows[0]["path"]).resolve() == Path(native["path"]).resolve(),
                "native image binding differs")
        runtime = validated["bundle"].get("runtime_package")
        if runtime is None:
            require(Path(expected["CODEX_CLI_PATH"]).resolve() == Path(native["path"]).resolve(),
                    "native image selector binding differs")
        else:
            from . import runtime_package as packages
            packages.validate_control_binding(runtime, native["path"], expected)
        require(re.fullmatch("[0-9a-f]{64}", native["sha256"]) is not None, "invalid native image hash")
        stage = "native_image_unverified"
        observed_hash = sha(native["path"])
        require(observed_hash == native["sha256"], "native image bytes changed")
        result["app"] = actual
        result["expected_native"] = {"path": native["path"], "sha256": native["sha256"],
                                     "manifest_sha256": manifest_sha}
        stage = "current_control_plane_unverified"
        control_options = {"executable": native["path"], "expected_sha256": native["sha256"],
                           "environment": expected, "auxiliary_exits": result["excluded_auxiliary_processes"]}
        if runtime is not None:
            control_options["runtime_package"] = runtime
        descendants = backend.native_control_plane(actual, **control_options)
        for candidate in descendants:
            if Path(candidate["exe"]).name.casefold() != "codex.exe":
                continue
            role = backend.native_role(candidate)
            if role == "other-native-command":
                result["excluded_native_processes"].append(
                    {"pid": candidate["pid"], "created": candidate["created"], "native_role": role})
                continue
            require(role == "app-server" and backend.live(candidate), "native role or liveness unverified")
            require(candidate["sid"] == actual["sid"] and candidate["session"] == actual["session"],
                    "native owner/session differs")
            require(candidate["created"] >= actual["created"], "native predates the original App")
            require(Path(candidate["exe"]).resolve() == Path(native["path"]).resolve(),
                    "native selected a different executable")
            environment = backend.selected_environment(candidate)
            require(environment == expected, "native controlled environment differs")
            chain = [candidate]
            cursor = candidate
            seen = set()
            for _ in range(32):
                require(cursor["pid"] not in seen, "native ancestor cycle")
                seen.add(cursor["pid"])
                if cursor["pid"] == actual["pid"]:
                    break
                parent = backend.identity(cursor["ppid"])
                require(backend.live(parent) and parent["sid"] == actual["sid"]
                        and parent["session"] == actual["session"]
                        and actual["created"] <= parent["created"] <= cursor["created"],
                        "native ancestor identity differs")
                chain.append(parent)
                cursor = parent
            require(chain[-1]["pid"] == actual["pid"] and chain[-1]["created"] == actual["created"],
                    "native does not descend from the original App")
            require(backend.identity(candidate["pid"]) == candidate and backend.live(candidate),
                    "native changed during observation")
            result["native_processes"].append({**candidate, "native_role": role,
                "observed_executable_sha256": observed_hash, "controlled_environment": environment,
                "ancestor_chain": chain})
        require(backend.identity(actual["pid"]) == actual and backend.live(actual), "App changed during observation")
        require(receipt_path.read_bytes() == original_bytes, "original receipt changed during observation")
        if result["native_processes"]:
            result["status"] = "verified_current_backends"
        else:
            result["status"] = "no_current_backend_verified"
        return result
    except (LaunchError, psutil.Error, OSError, ValueError, KeyError, TypeError, AttributeError) as error:
        result.update(reason=stage, error_type=type(error).__name__)
        if isinstance(error, psutil.NoSuchProcess) and type(error.pid) is int and error.pid > 0:
            result["error_pid"] = error.pid
        return result


def launch_status(run, backend=None):
    backend = backend or WindowsBackend(); run = Path(run).resolve()
    validate_state_paths(defaults())
    require(run.parent == defaults()["launches"].resolve(), "receipt is outside the owned launch directory")
    plan = load_json(run / "plan.json")
    current = backend.current()
    require(current["sid"] == plan["validated"]["owner"]["sid"], "receipt belongs to a different user")
    path = run / "receipt.json"
    receipt = load_json(path) if path.exists() else {"status": "unobserved"}
    observations = []
    for role, identity in [("app", receipt.get("app")), *[("native", item) for item in receipt.get("native_processes", [])]]:
        if identity is None: continue
        require(identity["sid"] == current["sid"], "recorded process belongs to a different user")
        try: live = backend.live(identity)
        except (psutil.Error, OSError, LaunchError): live = None
        observations.append({"role": role, "pid": identity["pid"], "created": identity["created"], "live": live})
    return {"status": "observation", "recorded_status": receipt["status"], "receipt": receipt,
            "observed_at": time.time(), "processes": observations, "replay_allowed": False,
            "app_runtime_acceptance": False,
            "current_backend_observation": current_backend_observation(run, plan, receipt, backend)}

def decode_request(text):
    require(len(text) <= 100_000, "launch request is too large")
    value = json.loads(base64.b64decode(text, validate=True))
    require(isinstance(value, dict), "launch request must be an object")
    return value

def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["check", "prepare", "inside", "status", "prepare-runtime"])
    parser.add_argument("--request-base64", required=True)
    args = parser.parse_args(argv); request = decode_request(args.request_base64)
    if args.mode == "check": result = {"status": "validated_not_launched", **validate_request(request)}
    elif args.mode == "prepare": result = prepare_launch(request)
    elif args.mode == "inside": result = inside_launch(Path(request["plan"]), request["plan_sha256"])
    elif args.mode == "prepare-runtime": result = prepare_runtime(Path(request["native_manifest"]), request["native_manifest_sha256"], Path(request["baseline"]))
    else: result = launch_status(request["run_dir"])
    print(json.dumps(result, indent=2)); return 0

if __name__ == "__main__": raise SystemExit(main())
