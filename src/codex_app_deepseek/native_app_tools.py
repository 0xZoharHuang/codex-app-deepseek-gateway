"""Bind original App resources and use the native plugin manager. No tool runtime."""
from __future__ import annotations
from contextlib import contextmanager
import copy
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import time
import tomllib
from uuid import uuid4
import tomlkit
from . import app_config as ac
from .paths import _is_link_or_reparse

INVENTORY_NAME = "app-bundled-resources-26.1023.6621.0.json"
INVENTORY_SHA256 = "505c30885686a194c82b6c9875673244a023da4ed61d15841b4bad6bf81e22c2"
MARKETPLACE = "openai-bundled"
PLUGIN = "codex-app-tools"
PLUGIN_ID = PLUGIN + "@" + MARKETPLACE
SERVER = "codex_app"
POLICY_KEY = "plugins." + PLUGIN_ID + ".mcp_servers.codex_app.enabled"
MANIFEST = ".agents/plugins/marketplace.json"
PLUGIN_PREFIX = "plugins/codex-app-tools/"
PROJECT = "codex-app-deepseek-gateway"

class NativeToolsError(ValueError):
    pass

class NativeToolsConflict(NativeToolsError):
    pass

def _require(value, message):
    if not value:
        raise NativeToolsConflict(message)

def _sha(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()

def _json(data):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            _require(key not in value, "Duplicate resource/receipt JSON field")
            value[key] = item
        return value
    return json.loads(data, object_pairs_hook=unique)

def _plain(path):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        _require(not _is_link_or_reparse(part), "App tools path traverses a reparse point")
    return path

def _same_path(left, right):
    def normalized(value):
        value = str(Path(value).resolve())
        if os.name == "nt" and value.startswith("\\\\?\\"):
            value = value[4:]
        return os.path.normcase(value)
    return normalized(left) == normalized(right)

def _save(path, value):
    path = _plain(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ac._private(path.parent, directory=True)
    ac._atomic_write(path, (json.dumps(value, sort_keys=True, indent=2) + "\n").encode())

def receipt_paths(data):
    root = _plain(Path(data) / "native-app-tools")
    return root / "resources.json", root / "installation.json"

def load_inventory():
    path = _plain(Path(__file__).parent / "assets" / INVENTORY_NAME)
    _require(_sha(path) == INVENTORY_SHA256, "Bundled resource inventory changed")
    value = _json(path.read_bytes())
    _require(value["schema_version"] == 1 and value["marketplace_name"] == MARKETPLACE
             and value["plugin_id"] == PLUGIN_ID, "Unsupported bundled resource inventory")
    files = value["files"]
    for name, row in files.items():
        relative = PurePosixPath(name)
        _require(not relative.is_absolute() and ".." not in relative.parts
                 and str(relative) == name and "\\" not in name and ":" not in name,
                 "Unsafe bundled resource member")
        _require(type(row["size"]) is int and row["size"] >= 0
                 and len(row["sha256"]) == 64, "Invalid bundled resource identity")
    digest = hashlib.sha256(json.dumps(files, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    _require(digest == value["fingerprint"] and len(files) == value["files_count"]
             and sum(row["size"] for row in files.values()) == value["total_bytes"],
             "Bundled resource inventory is inconsistent")
    return value

def _config(path):
    path = _plain(path)
    if not path.exists():
        return {}, None
    _require(path.is_file(), "Private App config is not a regular file")
    raw = path.read_bytes()
    try:
        return tomllib.loads(raw.decode("utf-8-sig")), raw
    except (ValueError, UnicodeError):
        raise NativeToolsConflict("Private App config cannot be parsed") from None

def preflight(home, *, for_setup=True):
    """Reject legacy/foreign control entries before copying or changing anything."""
    home = _plain(home)
    config, _ = _config(home / "config.toml")
    servers = config.get("mcp_servers", {})
    _require(isinstance(servers, dict) and SERVER not in servers,
             "Legacy top-level codex_app MCP entry conflicts; checkpoint and migrate it explicitly")
    _require(not for_setup or config.get("features", {}).get("plugins") is not False,
             "Native plugins were explicitly disabled")
    plugins = config.get("plugins", {})
    _require(isinstance(plugins, dict), "plugins must be a TOML table")
    plugin = plugins.get(PLUGIN_ID)
    _require(plugin is None or isinstance(plugin, dict), "App tools plugin config is invalid")
    if plugin is not None and for_setup:
        _require(plugin.get("enabled") is not False, "App tools plugin was explicitly disabled")
        policies = plugin.get("mcp_servers", {})
        _require(isinstance(policies, dict), "App tools MCP policy is invalid")
        policy = policies.get(SERVER, {})
        _require(isinstance(policy, dict) and policy.get("enabled") is not False
                 and "ema_auth" not in policy, "App tools MCP policy conflicts or was explicitly disabled")
    marketplaces = config.get("marketplaces", {})
    _require(isinstance(marketplaces, dict), "marketplaces must be a TOML table")
    entry = marketplaces.get(MARKETPLACE)
    if entry is not None:
        target = home / ".tmp/bundled-marketplaces" / MARKETPLACE
        _require(isinstance(entry, dict) and entry.get("source_type") == "local"
                 and isinstance(entry.get("source"), str) and _same_path(entry["source"], target),
                 "Existing bundled marketplace points outside this private App home")
    return config

def _tree(root, inventory):
    root = _plain(root)
    _require(root.is_dir(), "Bundled resource directory is missing")
    files = {}
    for path in root.rglob("*"):
        _plain(path)
        name = path.relative_to(root).as_posix()
        if path.is_dir():
            _require(any(key.startswith(name + "/") for key in inventory["files"]),
                     "Unknown directory in bundled resource tree")
            continue
        _require(path.is_file() and name in inventory["files"], "Unknown bundled resource file")
        files[name] = {"sha256": _sha(path), "size": path.stat().st_size}
    return files

def verify_resources(root, inventory, *, source=None, allow_filtered=False):
    actual = _tree(root, inventory)
    expected = inventory["files"]
    if actual == expected:
        return {"kind": "original", "files": len(actual), "fingerprint": inventory["fingerprint"]}
    _require(allow_filtered and source is not None and MANIFEST in actual,
             "Bundled resources differ from the fixed original tree")
    for name, row in actual.items():
        _require(name == MANIFEST or row == expected[name], "An existing bundled resource file differs")
    original = _json((Path(source) / MANIFEST).read_bytes())
    filtered = _json((Path(root) / MANIFEST).read_bytes())
    _require(isinstance(filtered, dict) and set(filtered) == set(original)
             and {key: value for key, value in filtered.items() if key != "plugins"}
             == {key: value for key, value in original.items() if key != "plugins"},
             "Existing marketplace metadata differs beyond the App plugin filter")
    chosen = filtered["plugins"]
    _require(isinstance(chosen, list) and len(chosen) < len(original["plugins"]),
             "Existing marketplace is not a strict original plugin subset")
    iterator = iter(original["plugins"])
    for entry in chosen:
        _require(any(candidate == entry for candidate in iterator), "Existing marketplace plugin entry was modified")
        prefix = "plugins/" + entry["name"] + "/"
        _require(all(name in actual for name in expected if name.startswith(prefix)),
                 "A listed bundled plugin has missing original files")
    _require(any(entry.get("name") == PLUGIN for entry in chosen)
             and all(name in actual for name in expected if name.startswith(PLUGIN_PREFIX)),
             "The App-filtered resource tree lacks the complete local App tools plugin")
    return {"kind": "app_filtered_original_subset", "files": len(actual), "fingerprint": None}

def bind_resources(package_root, home, receipt_path, *, owner_sid, package_full_name=None, inventory=None):
    """Caller holds the existing signed-package, idle-profile and home guards."""
    inventory = inventory or load_inventory()
    _require(package_full_name is None or package_full_name == inventory["package_full_name"],
             "App resource inventory belongs to another fixed package")
    home = _plain(home)
    preflight(home, for_setup=False)
    source = _plain(Path(package_root) / inventory["source_relative"])
    verify_resources(source, inventory)
    target = _plain(home / ".tmp/bundled-marketplaces" / MARKETPLACE)
    receipt_path = _plain(receipt_path)
    previous = _json(receipt_path.read_bytes()) if receipt_path.exists() else None
    if previous is not None:
        _require(previous.get("project") == PROJECT and previous.get("schema_version") == 1
                 and _same_path(previous["home"], home) and previous["owner_sid"] == owner_sid
                 and previous.get("source_package") == inventory["package_full_name"]
                 and previous.get("source_fingerprint") == inventory["fingerprint"]
                 and _same_path(previous["target"], target),
                 "Unknown bundled resource ownership receipt")
    created = False
    if target.exists():
        observed = verify_resources(target, inventory, source=source, allow_filtered=True)
        owned = bool(previous and previous.get("created") and observed["kind"] == "original")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        stage = target.parent / (".cadsg-original-" + uuid4().hex)
        stage.mkdir()
        ac._private(stage, directory=True)
        try:
            for name, wanted in sorted(inventory["files"].items()):
                src, dst = _plain(source / name), stage / name
                _plain(dst.parent).mkdir(parents=True, exist_ok=True)
                before = src.stat()
                with src.open("rb") as incoming, dst.open("xb") as outgoing:
                    shutil.copyfileobj(incoming, outgoing)
                    outgoing.flush()
                    os.fsync(outgoing.fileno())
                after = src.stat()
                _require((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                         == (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                         and _sha(dst) == wanted["sha256"], "App resource changed during private copying")
            verify_resources(source, inventory)
            verify_resources(stage, inventory)
            _require(not target.exists() and not _is_link_or_reparse(target), "Bundled target appeared during copying")
            stage.rename(target)
        finally:
            if stage.exists():
                _tree(stage, inventory)  # Refuse cleanup through an injected reparse point.
                shutil.rmtree(stage)
        observed = verify_resources(target, inventory)
        created = owned = True
    value = {"schema_version": 1, "project": PROJECT, "owner_sid": owner_sid, "home": str(home),
             "source_package": inventory["package_full_name"], "source": str(source), "target": str(target),
             "source_fingerprint": inventory["fingerprint"], "created": owned, "observation": observed,
             "observed_at": time.time(), "closed_resources_redistributed": False}
    _save(receipt_path, value)
    return {"receipt": str(receipt_path), "created_now": created, "claimed_ownership": owned,
            "target": str(target), "observation": observed, "source_bytes": inventory["total_bytes"]}

def _entry_syntax(config_path, group, key):
    _, raw = _config(config_path)
    if raw is None:
        return None
    document = tomlkit.parse(raw.decode("utf-8-sig"))
    parent = document.get(group, {})
    if key not in parent:
        return None
    # Native PluginManager removal can move a surviving table to EOF and change
    # only its trailing whitespace. Preserve real user semantics and comments;
    # layout alone is not evidence that a user adopted the native object.
    semantic = tomllib.loads(raw.decode("utf-8-sig"))[group][key]
    identity = {"settings": semantic, "comments": list(ac._item_comments(parent[key]))}
    return hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

class NativeCLI:
    """One-shot original native commands; never starts a model or an App."""
    def __init__(self, executable, checksum, home, receipt, receipt_path):
        self.executable, self.home = _plain(executable), _plain(home)
        _require(_sha(self.executable) == checksum, "Bound native CLI changed")
        self.checksum, self.receipt, self.receipt_path = checksum, receipt, receipt_path

    def _persist(self):
        if self.receipt_path is not None:
            _save(self.receipt_path, self.receipt)

    def call(self, args):
        import psutil
        _require(_sha(self.executable) == self.checksum, "Bound native CLI changed before invocation")
        allowed = {"SYSTEMROOT", "WINDIR", "COMSPEC", "PATHEXT", "PROGRAMFILES", "PROGRAMFILES(X86)",
                   "PROGRAMW6432", "SYSTEMDRIVE", "USERNAME", "USERDOMAIN", "USERPROFILE", "HOMEDRIVE",
                   "HOMEPATH", "LOCALAPPDATA", "APPDATA", "ALLUSERSPROFILE", "NUMBER_OF_PROCESSORS",
                   "PROCESSOR_ARCHITECTURE", "PATH", "TEMP", "TMP"}
        environment = {key: value for key, value in os.environ.items() if key.upper() in allowed}
        environment["CODEX_HOME"] = str(self.home)
        environment["GIT_TERMINAL_PROMPT"] = "0"
        command = [str(self.executable), "-c", 'cli_auth_credentials_store="file"',
                   "-c", 'mcp_oauth_credentials_store="file"', *args]
        row = {"args": args, "started": time.time(), "completion": "starting"}
        self.receipt.setdefault("commands", []).append(row)
        self._persist()
        process = subprocess.Popen(command, cwd=self.home, env=environment, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        created = None
        if process.poll() is None:
            try:
                observed = psutil.Process(process.pid).create_time()
                if process.poll() is None:
                    created = observed
            except psutil.NoSuchProcess:
                _require(process.poll() is not None, "Native child identity became uncertain")
        row.update(pid=process.pid, created=created, completion="running")
        self._persist()
        try:
            stdout, stderr = process.communicate(timeout=30)
        except subprocess.TimeoutExpired:
            process.kill()  # Only this exact one-shot child, never a name/PID search.
            process.communicate()
            row.update(completion="unknown_after_timeout", finished=time.time())
            self._persist()
            raise NativeToolsError("Native plugin command timed out; inspect its owned receipt, no automatic replay") from None
        row.update(completion="exited", exit_code=process.returncode, finished=time.time(),
                   stderr_present=bool(stderr), stdout_sha256=hashlib.sha256(stdout).hexdigest())
        self._persist()
        _require(process.returncode == 0, "Native plugin command failed; inspect its phase/exit code in the owned receipt")
        _require(len(stdout) <= 2 * 1024 * 1024, "Native plugin metadata output exceeds its bound")
        try:
            return _json(stdout)
        except ValueError:
            raise NativeToolsError("Native plugin command returned invalid metadata") from None

def _without_native_entries(config):
    value = copy.deepcopy(config)
    for group, key in (("plugins", PLUGIN_ID), ("marketplaces", MARKETPLACE)):
        if isinstance(value.get(group), dict):
            value[group].pop(key, None)
            if not value[group]:
                value.pop(group)
    return value



def _cache(home, inventory):
    return Path(home) / "plugins/cache" / MARKETPLACE / PLUGIN / inventory["plugin_version"]

def _verify_cache(path, inventory):
    expected = {name[len(PLUGIN_PREFIX):]: row for name, row in inventory["files"].items()
                if name.startswith(PLUGIN_PREFIX)}
    local = {**inventory, "files": expected}
    _require(_tree(path, local) == expected, "Installed App tools cache differs from the original signed plugin")

def _verify_mcp(value, cache, inventory):
    declared = _json((Path(cache) / ".mcp.json").read_bytes())["mcpServers"][SERVER]
    transport = value.get("transport", {})
    _require(value.get("name") == SERVER and value.get("enabled") is True
             and transport.get("type") == "stdio" and transport.get("command") == declared["command"]
             and transport.get("args") == declared["args"] and transport.get("env") == declared.get("env")
             and transport.get("env_vars") == declared.get("env_vars")
             and isinstance(transport.get("cwd"), str) and _same_path(transport["cwd"], cache),
             "Configured codex_app does not select the verified native plugin cache")
    _verify_cache(cache, inventory)

def _policy_plan(home, catalog_path, catalog_sha256):
    config_path = Path(home) / "config.toml"
    config, original = _config(config_path)
    preflight(home)
    _require(original is not None and config.get("plugins", {}).get(PLUGIN_ID, {}).get("enabled") is True,
             "Native plugin installation is not enabled")
    document = tomlkit.parse(original.decode("utf-8-sig"))
    plugin = document["plugins"][PLUGIN_ID]
    if "mcp_servers" not in plugin:
        plugin["mcp_servers"] = tomlkit.table()
    if SERVER not in plugin["mcp_servers"]:
        plugin["mcp_servers"][SERVER] = tomlkit.table()
    plugin["mcp_servers"][SERVER]["enabled"] = True
    rendered = (original.decode("utf-8") if config["plugins"][PLUGIN_ID].get("mcp_servers", {}).get(SERVER, {}).get("enabled") is True
                else tomlkit.dumps(document))
    if original.startswith(b"\xef\xbb\xbf") and rendered.startswith("\ufeff"):
        rendered = rendered[1:]
    if b"\r\n" in original:
        rendered = rendered.replace("\r\n", "\n").replace("\n", "\r\n")
    if original.startswith(b"\xef\xbb\xbf"):
        rendered = "\ufeff" + rendered
    _require(_sha(catalog_path) == catalog_sha256, "Model catalog changed before native MCP policy publication")
    before, after = hashlib.sha256(original).hexdigest(), hashlib.sha256(rendered.encode()).hexdigest()
    return ac.PreparedConfig(config_path, Path(catalog_path), catalog_sha256, before, after, True,
                             (POLICY_KEY,) if before != after else (), rendered, original)

def bootstrap(home, native, native_sha256, inventory, receipt_path, *, owner_sid,
              catalog_path, catalog_sha256, backup_dir, source_root,
              native_manifest=None, native_manifest_sha256=None, runner_factory=NativeCLI):
    """Caller owns idle/home locks; provider checkpoint is already registered."""
    home, receipt_path = _plain(home), _plain(receipt_path)
    before = preflight(home)
    target = home / ".tmp/bundled-marketplaces" / MARKETPLACE
    resources = verify_resources(target, inventory, source=source_root, allow_filtered=True)
    if receipt_path.exists():
        prior_bytes = receipt_path.read_bytes()
        prior = _json(prior_bytes)
        _require(prior.get("project") == PROJECT and prior.get("schema_version") == 1
                 and prior.get("owner_sid") == owner_sid and _same_path(prior["home"], home),
                 "Native App tools receipt belongs to another profile")
        if prior.get("project") == PROJECT and prior.get("state") in {"removed", "retained_user_adopted"}:
            history = receipt_path.parent / "history" / (hashlib.sha256(prior_bytes).hexdigest() + ".json")
            if history.exists():
                _require(history.read_bytes() == prior_bytes, "Native receipt history changed")
            else:
                _save(history, prior)
            receipt_path.unlink()
    if receipt_path.exists():
        prior = _json(receipt_path.read_bytes())
        _require(prior.get("project") == PROJECT and prior.get("owner_sid") == owner_sid
                 and _same_path(prior["home"], home) and prior.get("state") == "configured"
                 and prior.get("source_package") == inventory["package_full_name"]
                 and prior.get("resource_fingerprint") == inventory["fingerprint"],
                 "Native App tools setup has an unresolved prior receipt")
        cli = runner_factory(native, native_sha256, home, prior, receipt_path)
        _verify_mcp(cli.call(["mcp", "get", SERVER, "--json"]), _cache(home, inventory), inventory)
        prior.update(native=str(native), native_sha256=native_sha256,
                     native_manifest=str(native_manifest) if native_manifest else None,
                     native_manifest_sha256=native_manifest_sha256)
        _save(receipt_path, prior)
        return prior
    value = {"schema_version": 1, "project": PROJECT, "owner_sid": owner_sid, "home": str(home),
             "native": str(native), "native_sha256": native_sha256,
             "native_manifest": str(native_manifest) if native_manifest else None,
             "native_manifest_sha256": native_manifest_sha256, "source_package": inventory["package_full_name"],
             "resource_fingerprint": inventory["fingerprint"], "state": "installing", "commands": [],
             "created_marketplace": False, "created_plugin": False, "policy_checkpoint": None}
    _save(receipt_path, value)
    cli = runner_factory(native, native_sha256, home, value, receipt_path)
    try:
        if MARKETPLACE not in before.get("marketplaces", {}):
            result = cli.call(["plugin", "marketplace", "add", str(target), "--json"])
            _require(result.get("marketplaceName") == MARKETPLACE, "Native marketplace identity differs")
            value["created_marketplace"] = not result.get("alreadyAdded", False)
            value["marketplace_syntax"] = _entry_syntax(home / "config.toml", "marketplaces", MARKETPLACE)
            _save(receipt_path, value)
        listing = cli.call(["plugin", "list", "--marketplace", MARKETPLACE, "--available", "--json"])
        installed = [row for row in listing["installed"] if row.get("pluginId") == PLUGIN_ID]
        if not installed:
            result = cli.call(["plugin", "add", PLUGIN_ID, "--json"])
            _require(result.get("pluginId") == PLUGIN_ID and result.get("version") == inventory["plugin_version"]
                     and _same_path(result["installedPath"], _cache(home, inventory)), "Native plugin identity differs")
            value["created_plugin"] = PLUGIN_ID not in before.get("plugins", {})
            _save(receipt_path, value)
        _verify_cache(_cache(home, inventory), inventory)
        after_native, _ = _config(home / "config.toml")
        _require(_without_native_entries(after_native) == _without_native_entries(before),
                 "Native plugin command changed unrelated configuration; no MCP policy was applied")
        value["plugin_syntax"] = _entry_syntax(home / "config.toml", "plugins", PLUGIN_ID)
        _save(receipt_path, value)
        policy = _policy_plan(home, catalog_path, catalog_sha256)
        if policy.changed:
            checkpoint = ac.apply(policy, backup_dir=backup_dir)
            value["policy_checkpoint"] = str(checkpoint.manifest_path)
            value["plugin_syntax"] = _entry_syntax(home / "config.toml", "plugins", PLUGIN_ID)
            _save(receipt_path, value)
        _verify_mcp(cli.call(["mcp", "get", SERVER, "--json"]), _cache(home, inventory), inventory)
        value.update(state="configured", plugin_syntax=_entry_syntax(home / "config.toml", "plugins", PLUGIN_ID),
                     marketplace_syntax=_entry_syntax(home / "config.toml", "marketplaces", MARKETPLACE),
                     configured_at=time.time(), resource_observation=resources)
        _save(receipt_path, value)
        return value
    except Exception:
        value["state"] = "failed"
        _save(receipt_path, value)
        raise

def rollback(home, inventory, receipt_path, *, owner_sid, runner_factory=NativeCLI):
    """Only the caller's native receipt owns the MCP leaf and software objects."""
    receipt_path = _plain(receipt_path)
    if not receipt_path.exists():
        return {"status": "not_configured"}
    value = _json(receipt_path.read_bytes())
    _require(value.get("project") == PROJECT and value.get("owner_sid") == owner_sid
             and _same_path(value["home"], home), "Native App tools receipt belongs to another profile")
    if value.get("state") in {"removed", "retained_user_adopted"}:
        return {"status": value["state"]}
    config_path = Path(home) / "config.toml"
    current, _ = _config(config_path)
    if PLUGIN_ID in current.get("plugins", {}):
        _require(value.get("plugin_syntax") is not None, "Native setup completion is unconfirmed; no plugin was removed")
    changed = (PLUGIN_ID in current.get("plugins", {})
               and _entry_syntax(config_path, "plugins", PLUGIN_ID) != value.get("plugin_syntax"))
    if MARKETPLACE in current.get("marketplaces", {}) and value.get("marketplace_syntax") is not None:
        changed |= _entry_syntax(config_path, "marketplaces", MARKETPLACE) != value["marketplace_syntax"]
    def references(value):
        if isinstance(value, str):
            return "codex-app-tools" in value.casefold()
        if isinstance(value, dict):
            return any(references(item) for item in value.values())
        if isinstance(value, list):
            return any(references(item) for item in value)
        return False
    changed |= SERVER in current.get("mcp_servers", {}) or references(current.get("mcp_servers", {}))
    if not changed and value.get("created_plugin") and _cache(home, inventory).exists():
        try:
            _verify_cache(_cache(home, inventory), inventory)
        except NativeToolsConflict:
            changed = True
    if changed:
        value["state"] = "retained_user_adopted"
        _save(receipt_path, value)
        return {"status": value["state"], "resources_retained": True}
    cli = runner_factory(value["native"], value["native_sha256"], home, value, receipt_path)
    if value.get("created_plugin") and PLUGIN_ID in current.get("plugins", {}):
        _verify_cache(_cache(home, inventory), inventory)
        cli.call(["plugin", "remove", PLUGIN_ID, "--json"])
    if value.get("policy_checkpoint"):
        policy_path = _plain(value["policy_checkpoint"])
        policy = _json(policy_path.read_bytes())
        _require(policy.get("changed_settings") == [POLICY_KEY]
                 and _same_path(policy.get("config_path", ""), config_path),
                 "Native MCP policy checkpoint has a different owner/scope")
        ac.restore(policy_path)
    current, _ = _config(config_path)
    if value.get("created_marketplace") and MARKETPLACE in current.get("marketplaces", {}):
        listing = cli.call(["plugin", "list", "--marketplace", MARKETPLACE, "--json"])
        current, _ = _config(config_path)
        if not listing["installed"] and MARKETPLACE in current.get("marketplaces", {}):
            _require(_entry_syntax(config_path, "marketplaces", MARKETPLACE) == value["marketplace_syntax"],
                     "User changed the bundled marketplace reference; it was retained")
            cli.call(["plugin", "marketplace", "remove", MARKETPLACE, "--json"])
    value["state"] = "removed"
    _save(receipt_path, value)
    return {"status": "removed", "original_resources_retained": True}


@contextmanager
def rollback_admission(data, *, backend=None):
    """Reuse App owner/home/idle locks while the ordinary checkpoint chain runs."""
    _, receipt_path = receipt_paths(data)
    if not receipt_path.exists():
        yield lambda: {"status": "not_configured"}
        return
    from . import isolated_app as app
    value = _json(receipt_path.read_bytes())
    backend = backend or app.WindowsBackend()
    owner = backend.current()
    paths = app.defaults()
    app.validate_state_paths(paths)
    _require(value.get("owner_sid") == owner["sid"] and _same_path(value["home"], paths["home"]),
             "Native App tools belong to another Windows user/home")
    expected = app.baseline(Path(__file__).parent / "assets/app-launch-baseline.json")
    with backend.launch_lock(owner["sid"]), backend.home_guard(paths["home"], owner["sid"]):
        app.require_disjoint_apps(backend, owner, paths)
        admitted = app.verify_bundle(value["native_manifest"], value["native_manifest_sha256"],
                                     expected, cleanup_only=True)
        _require(_same_path(value["native"], admitted["files"]["codex.exe"]["path"])
                 and value["native_sha256"] == admitted["files"]["codex.exe"]["sha256"],
                 "Native App tools cleanup does not match its verified CLI")
        yield lambda: rollback(paths["home"], load_inventory(), receipt_path, owner_sid=owner["sid"])


def configured(home, native, checksum, inventory, receipt_path, *, owner_sid, runner_factory=NativeCLI):
    preflight(home)
    receipt_path = _plain(receipt_path)
    _require(receipt_path.is_file(), "Native App tools are not configured; run app-configure")
    value = _json(receipt_path.read_bytes())
    _require(value.get("state") == "configured" and value.get("owner_sid") == owner_sid
             and _same_path(value["home"], home), "Native App tools configuration receipt is not ready")
    cli = runner_factory(native, checksum, home, {}, None)
    _verify_mcp(cli.call(["mcp", "get", SERVER, "--json"]), _cache(home, inventory), inventory)
    return {"status": "native_plugin_configuration_verified", "plugin_id": PLUGIN_ID,
            "App_tool_call_qualified": False}
