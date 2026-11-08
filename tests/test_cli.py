from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import ssl
import subprocess
from types import SimpleNamespace

import pytest

from codex_app_deepseek import cli
from codex_deepseek.protocol import MODEL, PROVIDER, SERIAL_PROVIDER, STRUCTURED_PROVIDER


@pytest.fixture(autouse=True)
def isolated_cli_loop_policy():
    """Model a fresh CLI process without clearing pytest's restored old loop.

    These are synchronous CLI entrypoint tests. asyncio.run correctly clears its
    process/thread policy loop on exit; it must not discard pytest-asyncio's
    separate restored loop in this in-process unit harness.
    """
    previous = asyncio.get_event_loop_policy()
    created = []
    class CLIPolicy(asyncio.DefaultEventLoopPolicy):
        def new_event_loop(self):
            loop = super().new_event_loop()
            created.append(loop)
            return loop
    asyncio.set_event_loop_policy(CLIPolicy())
    try:
        yield
    finally:
        pending = [loop for loop in created if not loop.is_closed()]
        try:
            assert not pending, "A CLI-created loop was not closed by the tested operation"
        finally:
            for loop in pending:
                assert not loop.is_running()
                loop.close()
            asyncio.set_event_loop_policy(previous)


def write_config(home, config):
    home.mkdir(parents=True, exist_ok=True)
    (home / "gateway.json").write_text(json.dumps(config), encoding="utf-8")


@pytest.fixture
def no_service_side_effects(monkeypatch):
    from aiohttp import web
    from codex_app_deepseek import bridge, protocol_state
    import truststore
    calls = []
    def forbid(name):
        def fail(*args, **kwargs):
            calls.append(name)
            pytest.fail(f"Invalid config must not reach {name}")
        return fail
    monkeypatch.setattr(cli, "read", forbid("credential read"))
    monkeypatch.setattr(truststore, "SSLContext", forbid("TLS construction"))
    monkeypatch.setattr(protocol_state, "ProtocolState", forbid("database construction"))
    monkeypatch.setattr(bridge, "create_app", forbid("application construction"))
    monkeypatch.setattr(web, "run_app", forbid("network service"))
    return calls


def test_missing_config_has_official_only_default_and_no_writes(tmp_path):
    home = tmp_path / "missing gateway home"
    config = cli.load_service_config(home)
    assert config.accepted_models == (MODEL,)
    assert config.allowed_providers == (PROVIDER,)
    assert config.upstream_proxy is None
    assert config.configured_baseline == {}
    assert not home.exists()


def test_all_supported_fields_round_trip_and_proxy_repr_redacted(tmp_path):
    home = tmp_path / "gateway"
    write_config(home, {
        "accepted_models": [MODEL, "explicit-agent-alias"],
        "allowed_providers": [PROVIDER, STRUCTURED_PROVIDER, SERIAL_PROVIDER],
        "upstream_proxy": "https://user:PRIVATE_PROXY_PASSWORD@proxy.invalid:8443",
        "app_version": "26.1016.8120.0",
        "native_version": "codex-cli 0.163.0-alpha.11",
        "native_sha256": "A" * 64,
    })
    config = cli.load_service_config(home)
    assert config.accepted_models == (MODEL, "explicit-agent-alias")
    assert config.allowed_providers == (PROVIDER, STRUCTURED_PROVIDER, SERIAL_PROVIDER)
    assert config.upstream_proxy == "https://user:PRIVATE_PROXY_PASSWORD@proxy.invalid:8443"
    assert config.configured_baseline == {"app_version": "26.1016.8120.0", "native_version": "codex-cli 0.163.0-alpha.11", "native_sha256": "a" * 64}
    assert "PRIVATE_PROXY_PASSWORD" not in repr(config)
    assert "proxy.invalid" not in repr(config)


@pytest.mark.parametrize("config", [
    [], None, "not-an-object", 12,
    {"allowed_provider": [PROVIDER]}, {"unknown": True},
    {"accepted_models": MODEL}, {"accepted_models": []}, {"accepted_models": None},
    {"accepted_models": [MODEL, MODEL]}, {"accepted_models": [1]},
    {"accepted_models": [" "]}, {"accepted_models": ["model\n"]},
    {"allowed_providers": PROVIDER}, {"allowed_providers": []}, {"allowed_providers": None},
    {"allowed_providers": [PROVIDER, PROVIDER]}, {"allowed_providers": ["unapproved"]},
    {"allowed_providers": [False]},
    {"upstream_proxy": 9}, {"upstream_proxy": ""}, {"upstream_proxy": "ftp://proxy.invalid"},
    {"upstream_proxy": "http://"}, {"upstream_proxy": "http://proxy.invalid:wrong"},
    {"upstream_proxy": "http://proxy.invalid:0"}, {"upstream_proxy": "http://proxy.invalid:65536"},
    {"upstream_proxy": "http://proxy.invalid/path"}, {"upstream_proxy": "http://proxy.invalid?key=PRIVATE_TEST_VALUE"},
    {"upstream_proxy": "http://proxy.invalid/#fragment"}, {"upstream_proxy": "http://proxy.invalid\n"},
    {"upstream_proxy": "http://proxy\\invalid"},
    {"app_version": None}, {"app_version": ""}, {"app_version": 123},
    {"native_version": " 0.163.0"}, {"native_version": "0.163.0\n"},
    {"native_sha256": "z" * 64}, {"native_sha256": "a" * 63}, {"native_sha256": 1},
])
def test_invalid_or_unknown_config_fails_before_all_service_side_effects(tmp_path, no_service_side_effects, config):
    home = tmp_path / "gateway"
    write_config(home, config)
    before = (home / "gateway.json").read_bytes()
    with pytest.raises(cli.ServiceConfigError) as error:
        cli.serve(home, 42817)
    assert "PRIVATE_TEST_VALUE" not in str(error.value)
    assert no_service_side_effects == []
    assert (home / "gateway.json").read_bytes() == before
    assert sorted(p.name for p in home.iterdir()) == ["gateway.json"]


@pytest.mark.parametrize("raw", [
    '{"allowed_providers":["deepseek"],"allowed_providers":["morph/fp8"]}',
    '{"allowed_providers":["deepseek"],}',
    '{"upstream_proxy": NaN}',
    '{"app_version": Infinity}',
])
def test_invalid_json_or_duplicate_fields_are_not_silently_coerced(tmp_path, no_service_side_effects, raw):
    home = tmp_path / "gateway"
    home.mkdir()
    (home / "gateway.json").write_text(raw, encoding="utf-8")
    with pytest.raises(cli.ServiceConfigError):
        cli.serve(home, 42817)
    assert no_service_side_effects == []
    assert not (home / "protocol.sqlite").exists()


@pytest.mark.parametrize("proxy", [None, "http://127.0.0.1:8080", "https://proxy.invalid:8443/", "http://[::1]:3128"])
def test_proxy_endpoint_forms_are_supported(tmp_path, proxy):
    home = tmp_path / "gateway"
    write_config(home, {"upstream_proxy": proxy})
    assert cli.load_service_config(home).upstream_proxy == proxy


@pytest.mark.parametrize("port", [0, 80, 65536, True, "42817"])
def test_invalid_port_fails_before_credentials_or_home_creation(tmp_path, no_service_side_effects, port):
    home = tmp_path / "never-created"
    with pytest.raises(cli.ServiceConfigError):
        cli.serve(home, port)
    assert no_service_side_effects == []
    assert not home.exists()


@pytest.fixture
def fake_service(monkeypatch):
    from aiohttp import web
    from codex_app_deepseek import bridge, protocol_state
    import truststore
    captured = {"credential_calls": [], "db_paths": [], "tls_protocols": [], "closed": False}
    tls_context = object()
    def ssl_context(protocol):
        captured["tls_protocols"].append(protocol)
        return tls_context
    def credentials(target):
        captured["credential_calls"].append(target)
        assert target == cli.LOCAL_TARGET, "No upstream credential should be read while constructing the service"
        return "test-local-token-" + "x" * 32
    class State:
        def __init__(self, path):
            captured["db_paths"].append(path)
        def close(self):
            captured["closed"] = True
    app = SimpleNamespace(on_startup=[], on_cleanup=[])
    def create_app(settings, upstream_token, state, *, web_backend=None, image_transport_factory=None):
        captured.update(settings=settings, upstream_token_provider=upstream_token, state=state, web_backend=web_backend, image_transport_factory=image_transport_factory)
        return app
    def run_app(actual_app, **kwargs):
        assert actual_app is app
        captured["run_app_kwargs"] = kwargs
        async def lifecycle():
            for callback in app.on_startup:
                await callback(app)
            path = captured["db_paths"][0].parent / "service.json"
            captured["service_record"] = json.loads(path.read_text(encoding="utf-8"))
            for callback in app.on_cleanup:
                await callback(app)
            captured["service_record_removed"] = not path.exists()
        asyncio.run(lifecycle())
    monkeypatch.setattr(truststore, "SSLContext", ssl_context)
    monkeypatch.setattr(cli, "read", credentials)
    monkeypatch.setattr(protocol_state, "ProtocolState", State)
    monkeypatch.setattr(bridge, "create_app", create_app)
    monkeypatch.setattr(web, "run_app", run_app)
    captured["tls_context"] = tls_context
    return captured


def test_official_only_allowlist_is_passed_to_real_bridge_settings(tmp_path, fake_service):
    home = tmp_path / "gateway"
    write_config(home, {"allowed_providers": [PROVIDER], "accepted_models": [MODEL, "explicit-alias"],
                        "upstream_proxy": "http://127.0.0.1:8080", "app_version": "26.1016.8120.0",
                        "native_version": "0.163.0-alpha.11", "native_sha256": "a" * 64})
    cli.serve(home, 42817)
    from codex_app_deepseek.bridge import BridgeSettings
    actual = fake_service["settings"]
    assert isinstance(actual, BridgeSettings)
    assert actual.allowed_providers == (PROVIDER,)
    assert actual.accepted_models == (MODEL, "explicit-alias")
    assert actual.proxy_url == "http://127.0.0.1:8080"
    assert actual.ssl_context is fake_service["tls_context"]
    assert fake_service["tls_protocols"] == [ssl.PROTOCOL_TLS_CLIENT]
    assert fake_service["credential_calls"] == [cli.LOCAL_TARGET]
    assert fake_service["db_paths"] == [home.resolve() / "protocol.sqlite"]
    assert fake_service["run_app_kwargs"] == {"host": "127.0.0.1", "port": 42817, "handle_signals": True,
                                               "handler_cancellation": True, "access_log": None, "print": None}
    record = fake_service["service_record"]
    assert record["pid"] == os.getpid() and record["created"] > 0 and record["executable"]
    assert record["host"] == "127.0.0.1" and record["port"] == 42817
    assert record["configured_baseline"] == {"app_version": "26.1016.8120.0", "native_version": "0.163.0-alpha.11", "native_sha256": "a" * 64}
    assert fake_service["closed"] and fake_service["service_record_removed"]


def test_explicit_compatible_routes_keep_caller_order(tmp_path, fake_service):
    home = tmp_path / "gateway"
    providers = [PROVIDER, STRUCTURED_PROVIDER, SERIAL_PROVIDER]
    write_config(home, {"allowed_providers": providers})
    cli.serve(home, 42817)
    assert fake_service["settings"].allowed_providers == tuple(providers)


def test_missing_allowlist_does_not_use_bridge_three_provider_default(tmp_path, fake_service):
    cli.serve(tmp_path / "new-gateway", 42817)
    assert fake_service["settings"].allowed_providers == (PROVIDER,)


def test_main_returns_clear_config_error_without_credentials(tmp_path, no_service_side_effects, capsys):
    home = tmp_path / "gateway"
    write_config(home, {"allowed_providers": "deepseek"})
    assert cli.main(["serve", "--home", str(home)]) == 2
    output = capsys.readouterr()
    assert "allowed_providers" in output.err
    assert output.out == ""
    assert no_service_side_effects == []


@pytest.fixture
def protected_homes(tmp_path, monkeypatch):
    user = tmp_path / "test user"
    local = user / "AppData" / "Local"
    app = user / ".codex"
    cds = local / "codex-deepseek"
    app.mkdir(parents=True)
    cds.mkdir(parents=True)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: user))
    monkeypatch.setenv("LOCALAPPDATA", str(local))
    return user, local, app, cds


@pytest.mark.parametrize("selection", ["app", "cds", "app-child", "cds-child", "app-parent", "cds-parent"])
@pytest.mark.parametrize("extended", [
    False, pytest.param(True, marks=pytest.mark.skipif(os.name != "nt", reason="Win32 extended path")),
])
def test_protected_home_overlap_rejected_before_config_read(
    protected_homes, no_service_side_effects, monkeypatch, selection, extended,
):
    user, local, app, cds = protected_homes
    candidate = {"app": app, "cds": cds, "app-child": app / "gateway", "cds-child": cds / "gateway",
                 "app-parent": user, "cds-parent": local}[selection]
    if extended:
        candidate = Path("\\\\?\\" + str(candidate))
    monkeypatch.setattr(Path, "read_text", lambda *args, **kwargs: pytest.fail("Must reject protected home before reading any files"))
    with pytest.raises(cli.ServiceConfigError, match="overlap"):
        cli.serve(candidate, 42817)
    assert no_service_side_effects == []


def test_gateway_sibling_of_cds_is_allowed_without_creating_files(protected_homes):
    _, local, _, _ = protected_homes
    sibling = local / "codex-app-deepseek-gateway"
    assert cli.resolve_service_home(sibling) == sibling.resolve()
    assert cli.load_service_config(sibling).allowed_providers == (PROVIDER,)
    assert not sibling.exists()


def test_home_cannot_be_a_file(tmp_path, no_service_side_effects):
    file = tmp_path / "not-directory"
    file.write_bytes(b"fixture")
    with pytest.raises(cli.ServiceConfigError, match="directory"):
        cli.serve(file, 42817)
    assert no_service_side_effects == []


def test_resolved_junction_cannot_bypass_protected_home(protected_homes, tmp_path, no_service_side_effects):
    _, _, app, _ = protected_homes
    alias = tmp_path / "innocent gateway path"
    if os.name == "nt":
        command = str(Path(os.environ.get("SystemRoot", "C:/Windows")) / "System32" / "cmd.exe")
        result = subprocess.run([command, "/d", "/c", "mklink", "/J", str(alias), str(app)],
                                capture_output=True, timeout=10)
        assert result.returncode == 0, "Fixture junction must be created to test the Windows boundary"
    else:
        alias.symlink_to(app, target_is_directory=True)
    try:
        with pytest.raises(cli.ServiceConfigError, match="overlap"):
            cli.serve(alias, 42817)
        assert no_service_side_effects == []
    finally:
        if os.name == "nt":
            alias.rmdir()
        else:
            alias.unlink()


@pytest.mark.parametrize("config", [
    {"image_search_backend": "auto"}, {"image_search_backend": False},
    {"image_search_backend": []}, {"image_transport": {}},
    {"image_transport": []}, {"image_transport": True},
    {"image_transport": {"activate_above_bytes": True}},
    {"image_transport": {"activate_above_bytes": 10, "wire_budget_bytes": 9}},
    {"image_transport": {"activate_above_bytes": 10, "max_file_references": 17}},
    {"image_transport": {"activate_above_bytes": 10, "unknown": 1}},
    {"image_transport": {"activate_above_bytes": 10, "idle_ttl_seconds": -1}},
])
def test_invalid_media_config_rejected_before_effects(tmp_path, no_service_side_effects, config):
    home = tmp_path / "invalid-media"
    write_config(home, config)
    with pytest.raises(cli.ServiceConfigError):
        cli.serve(home, 42817)
    assert no_service_side_effects == []
    assert sorted(p.name for p in home.iterdir()) == ["gateway.json"]


def test_explicit_media_options_are_wired_without_upload_or_model_calls(tmp_path, fake_service):
    from codex_app_deepseek.web_backend import WikimediaImageSearch
    home = tmp_path / "media-gateway"
    policy = {"activate_above_bytes": 24 * 1024 * 1024, "wire_budget_bytes": 30 * 1024 * 1024}
    write_config(home, {"image_search_backend": "wikimedia_commons", "image_transport": policy,
                        "upstream_proxy": "http://127.0.0.1:8080"})
    cli.serve(home, 42817)
    backend = fake_service["web_backend"]
    assert isinstance(backend.image_search, WikimediaImageSearch)
    factory = fake_service["image_transport_factory"]
    assert factory is not None
    client = object()
    transport = factory(client, lambda: "bound-request-credential")
    assert transport.credentials() == "bound-request-credential"
    assert transport.backend.client is client
    assert transport.backend.proxy_url == "http://127.0.0.1:8080"
    assert transport.policy.wire_budget_bytes == 30 * 1024 * 1024
    assert transport.root == home.resolve() / "image-transport"
    assert fake_service["credential_calls"] == [cli.LOCAL_TARGET]


def test_default_media_options_do_not_invent_search_provider_or_upload_policy(tmp_path, fake_service):
    home = tmp_path / "default-media"
    cli.serve(home, 42817)
    assert fake_service["web_backend"].image_search is None
    assert fake_service["image_transport_factory"] is None
    assert not (home / "image-transport").exists()


def test_image_cache_maintenance_requires_explicit_configuration(tmp_path, monkeypatch):
    monkeypatch.setattr(cli, "read", lambda _: pytest.fail("Disabled transport must not read credentials"))
    with pytest.raises(cli.ServiceConfigError, match="explicitly"):
        asyncio.run(cli.image_cache_operation(tmp_path / "no-policy", "image-cache-status"))


@pytest.mark.parametrize("operation", ["image-cache-status", "image-cache-cleanup", "image-cache-reconcile-uploads", "image-cache-reconcile-calls"])
def test_media_maintenance_cli_dispatches_exact_operation(tmp_path, monkeypatch, capsys, operation):
    seen = []
    async def operation_fn(home, action, *, all_unpinned):
        seen.append((home, action, all_unpinned))
        return {"status": "test-receipt", "complete": True}
    monkeypatch.setattr(cli, "image_cache_operation", operation_fn)
    args = [operation, "--home", str(tmp_path)]
    if operation == "image-cache-cleanup": args.append("--all-unpinned")
    assert cli.main(args) == 0
    assert seen == [(tmp_path, operation, operation == "image-cache-cleanup")]
    assert json.loads(capsys.readouterr().out) == {"status": "test-receipt", "complete": True}


def test_empty_reconcile_calls_uses_readonly_receipts_without_model_or_files_http(tmp_path, monkeypatch):
    import hashlib
    import aiohttp
    from codex_app_deepseek.protocol_state import ProtocolState
    home = tmp_path / "maintenance-gateway"
    write_config(home, {"image_transport": {"activate_above_bytes": 1024}})
    source = home / "protocol.sqlite"
    state = ProtocolState(source)
    state.close()
    before = hashlib.sha256(source.read_bytes()).hexdigest()
    reads = []
    def credential(target):
        reads.append(target)
        assert target == cli.UPSTREAM_TARGET
        return "fixture-upstream-credential"
    monkeypatch.setattr(cli, "read", credential)
    monkeypatch.setattr(ProtocolState, "__init__", lambda *a, **k: pytest.fail("Maintenance must not construct writable ProtocolState"))
    async def no_http(*args, **kwargs):
        pytest.fail("An empty reconciliation must perform no network requests")
    monkeypatch.setattr(aiohttp.ClientSession, "_request", no_http)
    result = asyncio.run(cli.image_cache_operation(home, "image-cache-reconcile-calls"))
    assert result["complete"] is True
    assert reads == [cli.UPSTREAM_TARGET]
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("status", ["pending", "cooldown", "service_error", "error", "timed_out"])
def test_call_reconciliation_cli_never_reports_nonterminal_status_as_success(tmp_path, monkeypatch, capsys, status):
    from codex_app_deepseek import image_call_reconciliation
    home = tmp_path / "reconcile-gateway"
    write_config(home, {"image_transport": {"activate_above_bytes": 1024}})
    monkeypatch.setattr(cli, "read", lambda _: "fixture-upstream-key")
    async def reconcile(*args, **kwargs):
        assert kwargs["max_calls"] == 2 and kwargs["timeout_seconds"] == 10
        return {"status": status, "remaining_unknown": 1}
    monkeypatch.setattr(image_call_reconciliation, "reconcile_image_calls", reconcile)
    assert cli.main(["image-cache-reconcile-calls", "--home", str(home)]) == 3
    result = json.loads(capsys.readouterr().out)
    assert result == {"status": status, "remaining_unknown": 1, "complete": False}


def test_missing_maintenance_outcome_is_an_error_not_success(tmp_path, monkeypatch, capsys):
    async def malformed(*args, **kwargs): return {"unexpected": "not a result"}
    monkeypatch.setattr(cli, "image_cache_operation", malformed)
    assert cli.main(["image-cache-cleanup", "--home", str(tmp_path)]) == 2
    result = capsys.readouterr()
    assert not result.out and "verified completion" in result.err


def _native_agent_catalog(path, version):
    from codex_app_deepseek.catalog import PINNED_MODEL
    raw = json.dumps({"models": [
        {"slug": PINNED_MODEL, "visibility": "list", "multi_agent_version": version},
        {"slug": "gpt-5.6-luna", "visibility": "hide", "multi_agent_version": "v1"},
    ]}).encode()
    path.write_bytes(raw)
    import hashlib
    return hashlib.sha256(raw).hexdigest()


@pytest.mark.parametrize("entrypoint", ["configure-app", "app-configure"])
@pytest.mark.parametrize("version", ["v1", "v2"])
def test_configure_native_agent_mode_is_explicit_and_preserves_catalog(tmp_path, monkeypatch, capsys, entrypoint, version):
    from dataclasses import dataclass
    from codex_app_deepseek import app_config, app_delivery
    path = tmp_path / "catalog.json"
    digest = _native_agent_catalog(path, version)
    original = path.read_bytes()
    calls = []
    @dataclass
    class Receipt:
        status: str = "prepared-fixture"
    prepared = SimpleNamespace(catalog_sha256=digest)
    monkeypatch.setattr(app_config, "prepare", lambda *a, **k: calls.append(("prepare", k["catalog_path"])) or prepared)
    monkeypatch.setattr(app_config, "apply", lambda p, **k: calls.append(("apply", p)) or Receipt())
    monkeypatch.setattr(app_delivery, "configure", lambda *a, **k: calls.append(("managed", a[1], a[2])) or {"status": "prepared-fixture"})
    monkeypatch.setattr(cli, "read", lambda *a: pytest.fail("Configuration selection must not read credentials"))
    args = [entrypoint, "--catalog", str(path)]
    if entrypoint == "app-configure":
        args += ["--catalog-sha256", digest]
    if version == "v1":
        args += ["--multi-agent-version", "v1"]
    assert cli.main(args) == 0
    assert path.read_bytes() == original
    assert calls == ([("managed", path, digest)] if entrypoint == "app-configure"
                     else [("prepare", path), ("apply", prepared)])
    assert json.loads(capsys.readouterr().out)["status"] == "prepared-fixture"


@pytest.mark.parametrize("entrypoint", ["configure-app", "app-configure"])
@pytest.mark.parametrize("catalog_version, selection", [("v1", []), ("v2", ["--multi-agent-version", "v1"])])
def test_configure_rejects_agent_mode_mismatch_before_operations(tmp_path, monkeypatch, capsys, entrypoint, catalog_version, selection):
    from codex_app_deepseek import app_config, app_delivery
    path = tmp_path / "catalog.json"
    digest = _native_agent_catalog(path, catalog_version)
    original = path.read_bytes()
    def forbidden(*args, **kwargs):
        pytest.fail("Invalid agent selection must not prepare, configure, apply or read credentials")
    monkeypatch.setattr(app_config, "prepare", forbidden)
    monkeypatch.setattr(app_config, "apply", forbidden)
    monkeypatch.setattr(app_delivery, "configure", forbidden)
    monkeypatch.setattr(cli, "read", forbidden)
    args = [entrypoint, "--catalog", str(path), *selection]
    if entrypoint == "app-configure":
        args += ["--catalog-sha256", digest]
    assert cli.main(args) == 2
    output = capsys.readouterr()
    assert not output.out and "must match --multi-agent-version" in output.err
    assert path.read_bytes() == original


@pytest.mark.parametrize("entrypoint", ["configure-app", "app-configure"])
def test_configure_agent_version_help_and_invalid_input(tmp_path, capsys, entrypoint):
    with pytest.raises(SystemExit) as result:
        cli.main([entrypoint, "--help"])
    assert result.value.code == 0
    help_text = " ".join(capsys.readouterr().out.split())
    assert "--multi-agent-version {v1,v2}" in help_text
    assert "fresh chat" in help_text and "existing chats retain their recorded version" in help_text
    args = [entrypoint, "--catalog", str(tmp_path / "unread.json"), "--multi-agent-version", "v3"]
    if entrypoint == "app-configure":
        args += ["--catalog-sha256", "a" * 64]
    with pytest.raises(SystemExit) as result:
        cli.main(args)
    assert result.value.code == 2


@pytest.mark.parametrize("mutation", ["duplicate", "hidden-main", "another-visible", "no-version", "nondict"])
def test_agent_selection_requires_unique_visible_pinned_main(tmp_path, mutation):
    path = tmp_path / "catalog.json"
    _native_agent_catalog(path, "v2")
    data = json.loads(path.read_bytes())
    if mutation == "duplicate":
        data["models"].append(dict(data["models"][0]))
    elif mutation == "hidden-main":
        data["models"][0]["visibility"] = "hide"
    elif mutation == "another-visible":
        data["models"][1]["visibility"] = "list"
    elif mutation == "no-version":
        del data["models"][0]["multi_agent_version"]
    else:
        data["models"].append(None)
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(cli.ServiceConfigError):
        cli._validate_catalog_agent_version(path, "v2")


def test_configure_binds_agent_validation_to_prepared_catalog_bytes(tmp_path, monkeypatch, capsys):
    from codex_app_deepseek import app_config
    path = tmp_path / "catalog.json"
    _native_agent_catalog(path, "v1")
    monkeypatch.setattr(app_config, "prepare", lambda *a, **k: SimpleNamespace(catalog_sha256="0" * 64))
    monkeypatch.setattr(app_config, "apply", lambda *a, **k: pytest.fail("Changed catalog must not be applied"))
    assert cli.main(["configure-app", "--catalog", str(path), "--multi-agent-version", "v1"]) == 2
    assert "nothing applied" in capsys.readouterr().err


def test_managed_agent_selection_keeps_supplied_trusted_sha(tmp_path, monkeypatch, capsys):
    from codex_app_deepseek import app_delivery
    path = tmp_path / "catalog.json"
    _native_agent_catalog(path, "v1")
    monkeypatch.setattr(app_delivery, "configure", lambda *a, **k: pytest.fail("Mismatched trusted SHA must not configure"))
    assert cli.main(["app-configure", "--catalog", str(path), "--catalog-sha256", "0" * 64,
                     "--multi-agent-version", "v1"]) == 2
    assert "SHA256 mismatch" in capsys.readouterr().err


def test_gateway_upgrade_accepts_original_0610_policy_without_rewriting(tmp_path):
    from codex_app_deepseek import gateway_policy
    home = tmp_path / "prior-installation-data"
    payload = gateway_policy.default_app_policy()
    payload["policy_origin"].update(
        policy_id="codex-app-680c4-full-runtime-v1",
        native_version="0.165.0-alpha.1.2",
        protocol_commit="6d6ccd32c92d06fc52ecd72712140f24496c30a2",
    )
    payload["accepted_models"].remove("gpt-6.1-terra")
    write_config(home, payload)
    path = home / "gateway.json"
    raw = path.read_bytes()
    assert gateway_policy.load_service_config(home).policy_origin == payload["policy_origin"]
    with pytest.raises(gateway_policy.ServiceConfigError, match="gpt-6.1-terra"):
        gateway_policy.ensure_app_policy(home)
    assert path.read_bytes() == raw

    payload["accepted_models"].append("gpt-6.1-terra")
    write_config(home, payload)
    edited = path.read_bytes()
    config, outcome = gateway_policy.ensure_app_policy(home)
    assert config.policy_origin == payload["policy_origin"]
    assert outcome["status"] == "existing_policy_preserved"
    assert path.read_bytes() == edited
