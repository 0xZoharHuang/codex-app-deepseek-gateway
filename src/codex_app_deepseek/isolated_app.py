"""One-shot, process-scoped launch admission for the fixed Windows Codex App.
No runtime loop, setup, App replacement, global environment edits, restart or fallback.
The DesktopPackage transport requires separate actual App acceptance.
"""
from __future__ import annotations
import argparse, base64, ctypes, hashlib, json, ntpath, os, re, shutil, subprocess, sys, time, tomllib, uuid
from contextlib import contextmanager
from ctypes import wintypes
from pathlib import Path
import psutil
from .app_config import _atomic_write, _private

BASELINE_SHA256 = "a67ef1480c1a526f56b4cd84e8a2e87c8fa26b3868aa4baf5a3fbf8586c53325"
CONTROLLED = {"CODEX_HOME", "CODEX_CLI_PATH", "CODEX_ELECTRON_USER_DATA_PATH", "CODEX_APP_SERVER_FORCE_CLI"}
REMOVED = {
    "OPENAI_API_KEY", "CODEX_API_KEY", "CODEX_ACCESS_TOKEN", "OPENROUTER_API_KEY",
    "OPENAI_BASE_URL", "OPENAI_API_BASE", "OPENAI_ORGANIZATION", "OPENAI_PROJECT",
    "OPENAI_ORG_ID", "OPENAI_PROJECT_ID", "CODEX_AUTHAPI_BASE_URL",
    "CODEX_REFRESH_TOKEN_URL_OVERRIDE", "CODEX_REVOKE_TOKEN_URL_OVERRIDE",
    "CODEX_APP_SERVER_LOGIN_ISSUER", "CODEX_APP_SERVER_LOGIN_CLIENT_ID",
    "CODEX_SQLITE_HOME", "CODEX_APP_SERVER_WS_URL", "CODEX_APP_SERVER_USE_LOCAL_DAEMON",
    "CODEX_WINDOWS_REGISTERED_CORE", "CODEX_WINDOWS_SANDBOX_PACKAGE_FAMILY",
}
SAFE_APP_ARGS = {"--force-renderer-accessibility"}

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
        require(not path.is_symlink() and not (hasattr(path, "is_junction") and path.is_junction()),
                "dedicated App state path is a link: " + name)
        require(not path.exists() or path.is_dir(), "dedicated App state path is not a directory: " + name)
    require(paths["frontend"].resolve().is_relative_to(paths["home"].resolve()),
            "Electron state escapes the dedicated native home")


def child_environment(base, values):
    require(set(values) == CONTROLLED and values["CODEX_APP_SERVER_FORCE_CLI"] == "1", "invalid controlled launch environment")
    names = sorted(key for key in base if key.upper() in CONTROLLED | REMOVED or key.upper().startswith("CODEX_DEEPSEEK_"))
    result = {key: value for key, value in base.items() if key not in names}
    result.update(values)
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

def verify_bundle(path, expected_sha, expected, *, prepared=True):
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
    root, files = path.parent.resolve(), {}
    for row in rows:
        name = row.get("name", row.get("file"))
        require(isinstance(name, str) and Path(name).name == name and name not in files, "invalid or duplicate native binary name")
        file = Path(row["path"]) if "path" in row else root / name
        require(file.resolve().parent == root and file.resolve().name == name and not file.is_symlink(), "native file escapes its owned bundle")
        require(file.is_file() and file.stat().st_size == row["size"] and sha(file) == row["sha256"], "native binary checksum mismatch: " + name)
        files[name] = {"name": name, "path": str(file.resolve()), "size": row["size"], "sha256": row["sha256"]}
    required = set(expected["native_binaries"])
    if prepared: required.update(expected["required_private_sidecars"])
    require(required == files.keys(), "native bundle has missing or unexpected binaries/Code Mode Host")
    for name, resource in expected["required_private_sidecars"].items():
        if prepared: require(files[name]["sha256"] == expected["app_resources"][resource]["sha256"], "Code Mode Host differs from the fixed App")
    return {"manifest": str(path.resolve()), "manifest_sha256": expected_sha, "source_sha256": source_sha,
            "native_version": manifest["native_version"], "upstream_commit": manifest["upstream_commit"], "files": files}

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
        self.kernel.LocalFree.argtypes = [ctypes.c_void_p]

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
            require(process.create_time() == created, "process identity changed during inspection")
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
        output = subprocess.run(command, capture_output=True, timeout=90, creationflags=subprocess.CREATE_NO_WINDOW)
        require(output.returncode == 0, "cannot verify installed App package/signature/resources")
        return json.loads(output.stdout.decode("utf-8-sig"))

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
        require(self.live(identity), "owned process exited or PID was reused")
        values = psutil.Process(identity["pid"]).environ()
        found = path_contains_directory(values, directory)
        require(self.live(identity), "owned process changed during Git environment observation")
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

    def selected_environment(self, identity):
        require(self.live(identity), "owned process exited or PID was reused")
        values = psutil.Process(identity["pid"]).environ()
        result = {key: values.get(key) for key in CONTROLLED}
        require(self.live(identity), "owned process changed during environment observation")
        return result

    def profile_arguments(self, identity):
        require(self.live(identity), "owned App exited or PID was reused")
        args = psutil.Process(identity["pid"]).cmdline()[1:]
        require(self.live(identity), "owned App changed during argument observation")
        return [arg for arg in args if arg == "--user-data-dir" or arg.startswith("--user-data-dir=")]

    def native_role(self, identity):
        try:
            process = psutil.Process(identity["pid"])
            require(process.create_time() == identity["created"], "owned native PID was reused")
            args = process.cmdline()
            require(process.create_time() == identity["created"], "owned native identity changed")
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
        require(self.live(root), "owned App exited; refusing to adopt a replacement")
        found = [root]
        for process in psutil.Process(root["pid"]).children(recursive=True):
            try: identity = self.identity(process.pid)
            except psutil.NoSuchProcess: continue
            require(identity["sid"] == root["sid"] and identity["session"] == root["session"], "unexpected owner/session in App process tree")
            require(identity["created"] >= root["created"], "child predates the owned App")
            found.append(identity)
        require(self.live(root), "owned App exited during observation")
        return found

def validate_request(request, backend=None):
    require(set(request) <= {"baseline", "native_manifest", "native_manifest_sha256", "app_args", "timeout_seconds", "expected_config_sha256"}, "unknown launch request field")
    backend = backend or WindowsBackend()
    owner = backend.current()
    require(owner["session"] > 0 and not owner["elevated"], "use the current non-elevated interactive desktop session")
    expected = baseline(Path(request["baseline"]).resolve())
    package = verify_package(backend.package(expected), expected)
    bundle = verify_bundle(Path(request["native_manifest"]).resolve(), request["native_manifest_sha256"], expected)
    paths = defaults()
    validate_state_paths(paths)
    require(paths["home"].is_dir() and (paths["home"] / "config.toml").is_file(), "dedicated App home must be configured before launch")
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
    require(not backend.existing_apps(owner), "this user already has a Codex App instance; quit it explicitly first")
    arguments = request.get("app_args", [])
    require(isinstance(arguments, list) and all(isinstance(arg, str) and arg in SAFE_APP_ARGS for arg in arguments) and len(arguments) == len(set(arguments)), "unsupported or duplicate App argument")
    timeout = request.get("timeout_seconds", 60)
    require(type(timeout) is int and 5 <= timeout <= 120, "invalid launch observation timeout")
    environment = {"CODEX_HOME": str(paths["home"]), "CODEX_CLI_PATH": bundle["files"]["codex.exe"]["path"],
                   "CODEX_ELECTRON_USER_DATA_PATH": str(paths["frontend"]), "CODEX_APP_SERVER_FORCE_CLI": "1"}
    return {"owner": {key: owner[key] for key in ("sid", "session")}, "package": package, "bundle": bundle,
            "environment": environment, "app_args": ["--user-data-dir=" + str(paths["frontend"]), *arguments], "timeout_seconds": timeout,
            "config_sha256": config_sha256, "native_log_directory": str(paths["native_logs"]),
            "git_dependency": backend.trusted_git(),
            "scope": "validated inputs only; actual App/frontend behavior requires acceptance"}

def prepare_launch(request, backend=None):
    checked = validate_request(request, backend)
    root = defaults()["launches"]
    root.mkdir(parents=True, exist_ok=True); _private(root, directory=True)
    run = root / uuid.uuid4().hex
    run.mkdir(); _private(run, directory=True)
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
        with backend.launch_lock(current["sid"]):
            checked = validate_request(plan["request"], backend)
            require(checked["config_sha256"] == plan["validated"]["config_sha256"], "configuration changed after admission")
            require(checked["environment"] == plan["validated"]["environment"], "launch target changed after admission")
            frontend = Path(checked["environment"]["CODEX_ELECTRON_USER_DATA_PATH"])
            frontend.mkdir(parents=True, exist_ok=True); _private(frontend, directory=True)
            environment, removed = child_environment(dict(os.environ), checked["environment"])
            environment, git_dependency = git_child_environment(environment, checked["git_dependency"])
            executable = Path(checked["package"]["package"]["location"]) / expected["executable"]
            record.update(status="creating_process", owner=checked["owner"], expected_environment=checked["environment"],
                          removed_selector_names=removed, helper=current, app_executable=str(executable),
                          git_dependency=git_dependency)
            save(run / "receipt.json", record)
            child = backend.spawn(executable, checked["app_args"], environment, executable.parent)
            record.update(created_pid=child.pid, created_at=time.time())
            save(run / "receipt.json", record)
            identity = backend.identity(child.pid)
            require(Path(identity["exe"]).resolve() == executable.resolve(), "launched executable differs from the fixed App")
            require(identity["sid"] == current["sid"] and identity["session"] == current["session"], "App owner/session differs")
            record.update(status="observing_backend", app=identity, app_environment=backend.selected_environment(identity))
            require(record["app_environment"] == checked["environment"], "App did not inherit the controlled environment")
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
            save(run / "receipt.json", record)
            deadline = time.monotonic() + checked["timeout_seconds"]
            while time.monotonic() < deadline:
                natives = [item for item in backend.descendants(identity) if Path(item["exe"]).name.lower() == "codex.exe"]
                for item in natives:
                    require(Path(item["exe"]).resolve() == Path(checked["environment"]["CODEX_CLI_PATH"]).resolve(), "App selected an unexpected native runtime")
                    item["native_role"] = backend.native_role(item)
                    if item["native_role"] != "app-server":
                        continue
                    actual_sha = sha(Path(item["exe"]))
                    require(actual_sha == checked["bundle"]["files"]["codex.exe"]["sha256"], "native runtime bytes changed after preflight")
                    item["observed_executable_sha256"] = actual_sha
                    observed = backend.selected_environment(item)
                    require(observed == checked["environment"], "native runtime selected a different home or controlled environment")
                    item["controlled_environment"] = observed
                servers = [item for item in natives if item["native_role"] == "app-server"]
                if servers:
                    record.update(status="started_expected_backend", native_processes=servers,
                                  frontend_path_source="official environment plus native user-data-dir switch; requires App I/O and GUI acceptance",
                                  app_runtime_acceptance=False, finished_at=time.time())
                    save(run / "receipt.json", record); return record
                time.sleep(0.2)
            raise LaunchError("expected backend not observed; inspect receipt, do not automatically retry")
    except Exception as error:
        record.update(status="failed_or_unverified", error_type=type(error).__name__, error=str(error), finished_at=time.time())
        save(run / "receipt.json", record); raise

def prepare_runtime(manifest_path, manifest_sha, baseline_path, backend=None):
    manifest_path = Path(manifest_path); expected = baseline(baseline_path); backend = backend or WindowsBackend()
    current = backend.current()
    with backend.launch_lock(current["sid"]):
        require(not backend.existing_apps(current), "this user already has a Codex App instance; no runtime preparation")
        package = verify_package(backend.package(expected), expected)
        bundle = verify_bundle(manifest_path, manifest_sha, expected, prepared=False)
        root = manifest_path.parent.resolve(); outputs = list(bundle["files"].values())
        path = root / "app-runtime-manifest.json"
        require(path != manifest_path.resolve(), "raw build manifest must have a separate filename")
        for name, relative in expected["required_private_sidecars"].items():
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
        if path.exists(): require(load_json(path) == result, "prepared manifest already exists with different inputs")
        else: save(path, result)
        verify_bundle(path, sha(path), expected)
        return {"manifest": str(path), "manifest_sha256": sha(path), "runtime_executed": False}

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
            "app_runtime_acceptance": False}

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
