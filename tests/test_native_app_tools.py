"""Filesystem/native-frontdoor unit contracts; real CLI/App gates are separate."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tomllib
import tomlkit
import pytest
from codex_app_deepseek import native_app_tools as tools
from codex_app_deepseek import app_config as ac


def stock_fixture(package, package_name="fixed-package"):
    root = package / "bundled"
    plugin = root / "plugins/codex-app-tools"
    plugin.mkdir(parents=True)
    files = {
        ".codex-plugin/plugin.json": json.dumps({"name": tools.PLUGIN, "version": "1"}).encode(),
        ".mcp.json": json.dumps({"mcpServers": {tools.SERVER: {
            "command": "cmd.exe", "args": ["/c", "official.cmd"], "cwd": ".", "enabled": False,
            "env_vars": ["CODEX_APP_TOOLS_PIPE_PATH"], "default_tools_approval_mode": "approve",
            "tools": {"create_thread": {"approval_mode": "prompt"}}}}}).encode(),
        "server.mjs": b"original fixture server; never executed",
        "scripts/launch.cmd": b"original fixture launcher; never executed",
        "scripts/launch": b"original fixture launcher; never executed",
    }
    for name, raw in files.items():
        path = plugin / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    other = root / "plugins/other/data.txt"
    other.parent.mkdir()
    other.write_bytes(b"another original plugin")
    manifest = {"name": tools.MARKETPLACE, "interface": {"displayName": "Original"}, "plugins": [
        {"name": tools.PLUGIN, "source": {"source": "local", "path": "./plugins/codex-app-tools"},
         "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"}},
        {"name": "other", "source": {"source": "local", "path": "./plugins/other"}},
    ]}
    target = root / tools.MANIFEST
    target.parent.mkdir(parents=True)
    target.write_bytes((json.dumps(manifest) + "\n").encode())
    inventory = {path.relative_to(root).as_posix(): {"sha256": tools._sha(path), "size": path.stat().st_size}
                 for path in root.rglob("*") if path.is_file()}
    fingerprint = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"schema_version": 1, "package_full_name": package_name, "source_relative": "bundled",
            "marketplace_name": tools.MARKETPLACE, "plugin_id": tools.PLUGIN_ID, "plugin_version": "1",
            "files": inventory, "files_count": len(inventory),
            "total_bytes": sum(row["size"] for row in inventory.values()), "fingerprint": fingerprint}


@pytest.fixture
def fixture(tmp_path, monkeypatch):
    monkeypatch.setattr(ac, "_private", lambda *args, **kwargs: None)
    package = tmp_path / "package"
    inventory = stock_fixture(package)
    home = tmp_path / "home"
    home.mkdir()
    data = tmp_path / "data"
    data.mkdir()
    catalog = tmp_path / "catalog.json"
    catalog.write_text(json.dumps({"models": [{"slug": ac.PINNED_MODEL}]}))
    native = tmp_path / "native/codex.exe"
    native.parent.mkdir()
    native.write_bytes(b"trusted unit fixture; never executed")
    return package, home, data, catalog, native, inventory


def bind(f):
    package, home, data, _, _, inventory = f
    return tools.bind_resources(package, home, data / "resources.json", owner_sid="sid",
                                package_full_name="fixed-package", inventory=inventory)


class FakeNativeCLI:
    """Mock only the CLI frontdoor; no model/tool implementation is supplied."""
    events = []
    fail = None
    def __init__(self, executable, checksum, home, receipt, receipt_path):
        self.home = Path(home)
        self.receipt = receipt
        self.receipt_path = receipt_path
    def document(self):
        path = self.home / "config.toml"
        return tomlkit.parse(path.read_text()) if path.exists() else tomlkit.document()
    def write(self, document):
        (self.home / "config.toml").write_bytes(tomlkit.dumps(document).encode())
    def call(self, args):
        type(self).events.append(list(args))
        if type(self).fail is not None and args[:len(type(self).fail)] == type(self).fail:
            raise tools.NativeToolsError("synthetic native failure")
        document = self.document()
        target = self.home / ".tmp/bundled-marketplaces/openai-bundled"
        cache = self.home / "plugins/cache/openai-bundled/codex-app-tools/1"
        if args[:3] == ["plugin", "marketplace", "add"]:
            document.setdefault("marketplaces", {})[tools.MARKETPLACE] = {"source_type": "local", "source": str(target)}
            self.write(document)
            return {"marketplaceName": tools.MARKETPLACE, "alreadyAdded": False}
        if args[:2] == ["plugin", "list"]:
            return {"installed": [{"pluginId": tools.PLUGIN_ID}] if cache.exists() else [], "available": []}
        if args[:2] == ["plugin", "add"]:
            cache.parent.mkdir(parents=True, exist_ok=True)
            shutil.copytree(target / "plugins/codex-app-tools", cache)
            document.setdefault("plugins", {})[tools.PLUGIN_ID] = {"enabled": True}
            self.write(document)
            return {"pluginId": tools.PLUGIN_ID, "version": "1", "installedPath": str(cache)}
        if args[:2] == ["mcp", "get"]:
            decl = json.loads((cache / ".mcp.json").read_bytes())["mcpServers"][tools.SERVER]
            enabled = document["plugins"][tools.PLUGIN_ID].get("mcp_servers", {}).get(tools.SERVER, {}).get("enabled", False)
            return {"name": tools.SERVER, "enabled": enabled, "transport": {"type": "stdio",
                "command": decl["command"], "args": decl["args"], "env": None,
                "env_vars": decl["env_vars"], "cwd": str(cache)}}
        if args[:2] == ["plugin", "remove"]:
            shutil.rmtree(cache)
            document["plugins"].pop(tools.PLUGIN_ID)
            if not document["plugins"]:
                document.pop("plugins")
            self.write(document)
            return {"pluginId": tools.PLUGIN_ID}
        if args[:3] == ["plugin", "marketplace", "remove"]:
            document["marketplaces"].pop(tools.MARKETPLACE)
            if not document["marketplaces"]:
                document.pop("marketplaces")
            self.write(document)
            return {"marketplaceName": tools.MARKETPLACE}
        raise AssertionError(args)


def setup(f):
    package, home, data, catalog, native, inventory = f
    bind(f)
    FakeNativeCLI.events = []
    FakeNativeCLI.fail = None
    return tools.bootstrap(home, native, tools._sha(native), inventory, data / "installation.json",
        owner_sid="sid", catalog_path=catalog, catalog_sha256=tools._sha(catalog),
        backup_dir=data / "policy", source_root=package / "bundled", runner_factory=FakeNativeCLI)


def test_packaged_inventory_is_hash_only_exact_qualified_source():
    root = Path(__file__).parents[1]
    asset = Path(tools.__file__).parent / "assets" / tools.INVENTORY_NAME
    assert asset.read_bytes() == (root / "native" / tools.INVENTORY_NAME).read_bytes()
    inventory = tools.load_inventory()
    assert inventory["files_count"] == 860 and inventory["total_bytes"] == 35191141
    assert not any(key in inventory for key in ["source", "credentials", "contents"])


def test_bind_copies_original_bytes_and_preserves_existing_ownership(fixture):
    first = bind(fixture)
    assert first["created_now"] and first["claimed_ownership"]
    second = bind(fixture)
    assert not second["created_now"] and second["claimed_ownership"]
    package, home, _, _, _, inventory = fixture
    assert tools._tree(package / "bundled", inventory) == tools._tree(Path(first["target"]), inventory)


def test_identical_existing_tree_is_reused_without_claiming(fixture):
    package, home, _, _, _, _ = fixture
    target = home / ".tmp/bundled-marketplaces/openai-bundled"
    target.parent.mkdir(parents=True)
    shutil.copytree(package / "bundled", target)
    result = bind(fixture)
    assert not result["created_now"] and not result["claimed_ownership"]


def test_source_filtered_subset_is_adopted_only_with_complete_stock_plugin(fixture):
    package, home, _, _, _, _ = fixture
    target = home / ".tmp/bundled-marketplaces/openai-bundled"
    target.parent.mkdir(parents=True)
    shutil.copytree(package / "bundled", target)
    shutil.rmtree(target / "plugins/other")
    path = target / tools.MANIFEST
    value = json.loads(path.read_bytes()); value["plugins"] = value["plugins"][:1]
    path.write_text(json.dumps(value))
    result = bind(fixture)
    assert result["observation"]["kind"] == "app_filtered_original_subset"
    assert not result["claimed_ownership"]


@pytest.mark.parametrize("corruption", ["file", "extra", "policy", "missing_selected"])
def test_unknown_existing_resources_are_never_overwritten(fixture, corruption):
    package, home, data, _, _, _ = fixture
    target = home / ".tmp/bundled-marketplaces/openai-bundled"
    target.parent.mkdir(parents=True)
    shutil.copytree(package / "bundled", target)
    if corruption == "file":
        (target / "plugins/codex-app-tools/server.mjs").write_bytes(b"user change")
    elif corruption == "extra":
        (target / "user.txt").write_bytes(b"keep")
    elif corruption == "missing_selected":
        (target / "plugins/codex-app-tools/server.mjs").unlink()
    else:
        path = target / tools.MANIFEST; value = json.loads(path.read_bytes())
        value["plugins"][0]["policy"]["installation"] = "NOT_AVAILABLE"
        path.write_text(json.dumps(value))
    before = {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    with pytest.raises(tools.NativeToolsConflict):
        bind(fixture)
    assert before == {p.relative_to(target).as_posix(): p.read_bytes() for p in target.rglob("*") if p.is_file()}
    assert not (data / "resources.json").exists()


def test_real_directory_alias_is_rejected_before_copy(fixture):
    package, home, data, _, _, _ = fixture
    target = home / ".tmp/bundled-marketplaces/openai-bundled"
    target.parent.mkdir(parents=True)
    if os.name == "nt":
        result = subprocess.run([str(Path(os.environ["SystemRoot"]) / "System32/cmd.exe"), "/d", "/c",
                                 "mklink", "/J", str(target), str(package / "bundled")], capture_output=True)
        assert result.returncode == 0
    else:
        target.symlink_to(package / "bundled", target_is_directory=True)
    with pytest.raises(tools.NativeToolsConflict, match="reparse"):
        bind(fixture)
    assert not (data / "resources.json").exists()


def test_legacy_conflict_precedes_resource_or_checkpoint_mutation(fixture):
    _, home, data, _, _, _ = fixture
    raw = b'[mcp_servers.codex_app]\ncommand="do-not-print-secret"\n'
    (home / "config.toml").write_bytes(raw)
    with pytest.raises(tools.NativeToolsConflict, match="Legacy") as error:
        bind(fixture)
    assert "do-not-print-secret" not in str(error.value)
    assert (home / "config.toml").read_bytes() == raw and not list(data.iterdir())


def test_bootstrap_and_native_rollback_preserve_other_configuration(fixture):
    _, home, data, _, _, inventory = fixture
    original = b'# user heading\ncustom = "keep"\n'
    (home / "config.toml").write_bytes(original)
    value = setup(fixture)
    assert value["state"] == "configured" and value["created_plugin"]
    assert value["policy_checkpoint"]
    parsed = tomllib.loads((home / "config.toml").read_text())
    assert parsed["plugins"][tools.PLUGIN_ID]["mcp_servers"][tools.SERVER]["enabled"]
    policy = json.loads(Path(value["policy_checkpoint"]).read_bytes())
    assert policy["changed_settings"] == [tools.POLICY_KEY]
    result = tools.rollback(home, inventory, data / "installation.json", owner_sid="sid", runner_factory=FakeNativeCLI)
    assert result["status"] == "removed"
    assert tomllib.loads((home / "config.toml").read_text()) == {"custom": "keep"}
    assert b"# user heading" in (home / "config.toml").read_bytes()
    assert ["plugin", "remove", tools.PLUGIN_ID, "--json"] in FakeNativeCLI.events


@pytest.mark.parametrize("adoption", ["policy", "cache", "reference", "comment"])
def test_user_adopted_plugins_and_references_are_retained(fixture, adoption):
    _, home, data, _, _, inventory = fixture
    setup(fixture)
    path = home / "config.toml"
    if adoption == "cache":
        (tools._cache(home, inventory) / "server.mjs").write_bytes(b"user adopted version")
    else:
        document = tomlkit.parse(path.read_text())
        if adoption == "policy":
            document["plugins"][tools.PLUGIN_ID]["mcp_servers"][tools.SERVER]["disabled_tools"] = ["user_choice"]
        elif adoption == "reference":
            document["mcp_servers"] = {"user_reference": {"command": "cmd.exe", "cwd": str(tools._cache(home, inventory))}}
        else:
            document["plugins"][tools.PLUGIN_ID].add(tomlkit.comment("user adopted this plugin"))
        path.write_text(tomlkit.dumps(document))
    before = path.read_bytes()
    FakeNativeCLI.events = []
    result = tools.rollback(home, inventory, data / "installation.json", owner_sid="sid", runner_factory=FakeNativeCLI)
    assert result["status"] == "retained_user_adopted" and not FakeNativeCLI.events
    assert path.read_bytes() == before and tools._cache(home, inventory).exists()


def test_failure_is_recorded_and_never_promoted_to_ready(fixture):
    _, home, data, catalog, native, inventory = fixture
    package = fixture[0]; bind(fixture)
    FakeNativeCLI.events = []; FakeNativeCLI.fail = ["plugin", "add"]
    with pytest.raises(tools.NativeToolsError):
        tools.bootstrap(home, native, tools._sha(native), inventory, data / "installation.json",
            owner_sid="sid", catalog_path=catalog, catalog_sha256=tools._sha(catalog),
            backup_dir=data / "policy", source_root=package / "bundled", runner_factory=FakeNativeCLI)
    assert json.loads((data / "installation.json").read_bytes())["state"] == "failed"
    FakeNativeCLI.fail = None


def test_native_file_tamper_is_rejected_before_child_execution(fixture, monkeypatch):
    _, home, data, _, native, _ = fixture
    checksum = tools._sha(native)
    cli = tools.NativeCLI(native, checksum, home, {}, None)
    native.write_bytes(b"tampered")
    monkeypatch.setattr(tools.subprocess, "Popen", lambda *a, **k: pytest.fail("No child may run"))
    with pytest.raises(tools.NativeToolsConflict, match="changed"):
        cli.call(["mcp", "get", tools.SERVER, "--json"])


def test_native_management_child_has_file_only_auth_and_no_secret_environment(fixture, monkeypatch):
    from types import SimpleNamespace
    _, home, _, _, native, _ = fixture
    monkeypatch.setenv("OPENROUTER_API_KEY", "must-not-reach-child")
    monkeypatch.setenv("MY_PRIVATE_SECRET", "must-not-reach-child")
    seen = {}
    class Process:
        pid = 12345
        returncode = 0
        def poll(self): return None
        def communicate(self, timeout=None): return b"{}", b""
    def spawn(argv, **kwargs):
        seen.update(argv=argv, environment=kwargs["env"])
        return Process()
    monkeypatch.setattr(tools.subprocess, "Popen", spawn)
    monkeypatch.setattr(__import__("psutil"), "Process", lambda pid: SimpleNamespace(create_time=lambda: 1.0))
    cli = tools.NativeCLI(native, tools._sha(native), home, {}, None)
    assert cli.call(["plugin", "list", "--json"]) == {}
    assert not any("SECRET" in key or key.startswith(("OPENAI", "OPENROUTER")) for key in seen["environment"])
    assert seen["environment"]["CODEX_HOME"] == str(home)
    assert 'cli_auth_credentials_store="file"' in seen["argv"]
    assert 'mcp_oauth_credentials_store="file"' in seen["argv"]
    assert os.environ["OPENROUTER_API_KEY"] == "must-not-reach-child"


def test_preexisting_explicit_disable_is_not_overridden(fixture):
    _, home, data, _, _, _ = fixture
    raw = ('[plugins."' + tools.PLUGIN_ID + '"]\nenabled=false\n').encode()
    (home / "config.toml").write_bytes(raw)
    # Resource binding does not enable plugins; setup preflight respects opt-out.
    bind(fixture)
    with pytest.raises(tools.NativeToolsConflict, match="explicitly disabled"):
        tools.preflight(home)
    assert (home / "config.toml").read_bytes() == raw


@pytest.mark.parametrize("user_adopted", [False, True])
def test_native_rollback_precedes_provider_chain_and_preserves_adopted_plugin(fixture, monkeypatch, user_adopted):
    from codex_app_deepseek import lifecycle as lc
    package, home, data, catalog, native, inventory = fixture
    root = data / "installation-root"; root.mkdir()
    monkeypatch.setattr(lc, "_active", lambda unused: (root / "release", {"data_home": str(data)}, None))
    first = ac.apply(ac.prepare(home / "config.toml", catalog_path=catalog, auth_command=native, port=42817),
                     backup_dir=data / "config-backups")
    lc.register_checkpoint(root, first.manifest_path)
    value = setup(fixture)
    if user_adopted:
        path = home / "config.toml"
        document = tomlkit.parse(path.read_text())
        document["plugins"][tools.PLUGIN_ID].add(tomlkit.comment("user adopted"))
        path.write_bytes(tomlkit.dumps(document).encode())
    events = []
    original_restore = ac.restore
    def restore(path):
        if Path(path) == first.manifest_path:
            events.append("provider")
            state = json.loads((data / "installation.json").read_bytes())["state"]
            assert state == ("retained_user_adopted" if user_adopted else "removed")
        return original_restore(path)
    monkeypatch.setattr(ac, "restore", restore)
    def rollback():
        events.append("native")
        return tools.rollback(home, inventory, data / "installation.json", owner_sid="sid", runner_factory=FakeNativeCLI)
    result = lc._rollback_registered(root, rollback)
    assert result["restored_checkpoints"] == [str(first.manifest_path)] and events == ["native", "provider"]
    path = home / "config.toml"
    config = tomllib.loads(path.read_text()) if path.exists() else {}
    assert "model_provider" not in config
    assert (tools.PLUGIN_ID in config.get("plugins", {})) is user_adopted
    assert tools.verify_resources(home / ".tmp/bundled-marketplaces/openai-bundled", inventory)["kind"] == "original"


def test_existing_user_plugin_is_not_claimed_by_native_installation(fixture):
    package, home, data, catalog, native, inventory = fixture
    setup(fixture)
    # A distinct new installation may reuse the existing native plugin but owns
    # neither its software nor the already-enabled user policy.
    receipt = data / "other-installation.json"
    value = tools.bootstrap(home, native, tools._sha(native), inventory, receipt,
        owner_sid="sid", catalog_path=catalog, catalog_sha256=tools._sha(catalog),
        backup_dir=data / "other-policy", source_root=package / "bundled", runner_factory=FakeNativeCLI)
    assert not value["created_plugin"] and not value["created_marketplace"] and value["policy_checkpoint"] is None
    FakeNativeCLI.events = []
    result = tools.rollback(home, inventory, receipt, owner_sid="sid", runner_factory=FakeNativeCLI)
    assert result["status"] == "removed" and not FakeNativeCLI.events
    assert tools._cache(home, inventory).exists()


def test_native_removal_whitespace_is_not_user_adoption_but_comments_are(fixture):
    _, home, _, _, _, _ = fixture
    path = home / "config.toml"
    prefix = ('[marketplaces.openai-bundled]\nsource_type="local"\nsource="same"\n')
    path.write_bytes((prefix + '\n[plugins.other]\nenabled=true\n').encode())
    initial = tools._entry_syntax(path, "marketplaces", tools.MARKETPLACE)
    path.write_bytes(prefix.encode())
    assert tools._entry_syntax(path, "marketplaces", tools.MARKETPLACE) == initial
    path.write_bytes((prefix + '# user adopted this reference\n').encode())
    assert tools._entry_syntax(path, "marketplaces", tools.MARKETPLACE) != initial


def test_explicit_rollback_after_native_remove_before_receipt_commit_is_idempotent(fixture):
    _, home, data, _, native, inventory = fixture
    setup(fixture)
    # Model a crash after the original native commands committed their removals,
    # before this project's receipt could record the terminal state.
    cli = FakeNativeCLI(native, tools._sha(native), home, {}, None)
    cli.call(["plugin", "remove", tools.PLUGIN_ID, "--json"])
    cli.call(["plugin", "marketplace", "remove", tools.MARKETPLACE, "--json"])
    FakeNativeCLI.events = []
    result = tools.rollback(home, inventory, data / "installation.json", owner_sid="sid", runner_factory=FakeNativeCLI)
    assert result["status"] == "removed" and not FakeNativeCLI.events
    assert json.loads((data / "installation.json").read_bytes())["state"] == "removed"
