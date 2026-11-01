"""Exercise project-local selection through config, HTTP forwarding and receipt truth."""
import copy
import hashlib
import json

import pytest
from aiohttp import web
from codex_deepseek import protocol
from codex_app_deepseek import cli, gateway_policy as gp, lifecycle
from test_bridge import harness, HEADERS, TOKEN, terminal, message
from test_cli import fake_service, no_service_side_effects, write_config


def test_absent_selection_preserves_official_default_without_ambient_override(tmp_path, monkeypatch):
    for name in ("CDS_ORDINARY_DEFAULT_PROVIDER", "CODEX_APP_ORDINARY_DEFAULT_PROVIDER", "OPENROUTER_PROVIDER"):
        monkeypatch.setenv(name, "morph/fp8")
    config = gp.load_service_config(tmp_path / "absent")
    assert config.ordinary_default_provider == protocol.PROVIDER == "deepseek"
    assert gp.policy_info(config)["ordinary_default_provider"] == "deepseek"
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize("value", [None, True, "", "Morph", "primeintellect", "unknown", [], {}])
def test_invalid_selection_fails_before_credentials_database_tls_or_network(tmp_path, no_service_side_effects, value):
    home = tmp_path / "gateway"
    write_config(home, {"ordinary_default_provider": value, "allowed_providers": list(gp.DEFAULT_PROVIDERS)})
    before = (home / "gateway.json").read_bytes()
    with pytest.raises(gp.ServiceConfigError, match="ordinary_default_provider"):
        cli.serve(home, 42817)
    assert no_service_side_effects == []
    assert (home / "gateway.json").read_bytes() == before
    assert sorted(p.name for p in home.iterdir()) == ["gateway.json"]


def test_explicit_selection_requires_existing_allowlist_instead_of_expanding_it(tmp_path, no_service_side_effects):
    write_config(tmp_path, {"ordinary_default_provider": "morph/fp8", "allowed_providers": ["deepseek"]})
    with pytest.raises(gp.ServiceConfigError, match="explicitly allowed"):
        cli.serve(tmp_path, 42817)
    assert no_service_side_effects == []
    # A historical special-only allowlist can still start and reject ordinary requests.
    write_config(tmp_path, {"allowed_providers": ["morph/fp8"]})
    assert gp.load_service_config(tmp_path).ordinary_default_provider == "deepseek"


@pytest.mark.parametrize("provider", ["deepseek", "morph/fp8"])
def test_cli_uses_existing_snapshot_and_records_selected_identity(tmp_path, fake_service, provider):
    value = {**gp.default_app_policy(), "ordinary_default_provider": provider}
    write_config(tmp_path, value)
    raw = (tmp_path / "gateway.json").read_bytes()
    cli.serve(tmp_path, 42817)
    assert fake_service["settings"].ordinary_default_provider == provider
    assert fake_service["settings"].config_sha256 == hashlib.sha256(raw).hexdigest()
    assert fake_service["service_record"]["ordinary_default_provider"] == provider
    assert fake_service["service_record"]["config_sha256"] == hashlib.sha256(raw).hexdigest()
    assert fake_service["credential_calls"] == [cli.LOCAL_TARGET]


TOOL = {"type": "function", "name": "fixture", "parameters": {"type": "object", "properties": {}}}
SCHEMA = {"type": "json_schema", "name": "answer", "strict": True,
          "schema": {"type": "object", "properties": {"count": {"type": "integer"}},
                     "required": ["count"], "additionalProperties": False}}
CASES = [
    ("ordinary", {"input": "fixture"}, None),
    ("native_tools", {"input": "fixture", "tools": [TOOL]}, None),
    ("guardian", {"model": "guardian", "input": "fixture", "text": {"format": SCHEMA}, "tools": [TOOL]}, None),
    ("serial", {"input": "fixture", "tools": [TOOL], "parallel_tool_calls": False}, "primeintellect"),
    ("strict", {"input": "fixture", "text": {"format": SCHEMA}}, "morph/fp8"),
    ("unattested_history", {"input": [{"type": "reasoning", "id": "rs_peer", "content": [
        {"type": "reasoning_text", "text": "fixture prior reasoning"}]},
        {"role": "user", "content": "Continue"}], "reasoning": {"effort": "high"},
        "tools": [{"type": "web_search", "external_web_access": True}]}, "morph/fp8"),
]


@pytest.mark.parametrize("provider", ["deepseek", "morph/fp8"])
@pytest.mark.parametrize("name,extra,special_route", CASES, ids=[x[0] for x in CASES])
async def test_actual_forwarded_payload_equals_pinned_protocol_and_preserves_special_priority(tmp_path, provider, name, extra, special_route):
    body = {"model": protocol.MODEL, "stream": False, **copy.deepcopy(extra)}
    captured = []
    async def upstream(request):
        captured.append(await request.json())
        event = terminal([message('{"count":1}' if "text" in body else "ok")])
        return web.json_response(event["response"])
    async with harness(tmp_path, upstream, settings={"ordinary_default_provider": provider}) as (client, state):
        translated = {**body, "model": protocol.MODEL}
        expected, registry = protocol.prepare(translated, state.evidence_view(body.get("input")),
                                              ordinary_default_provider=provider)
        response = await client.post("/v1/responses", headers=HEADERS, json=body)
        assert response.status == 200, await response.text()
        await response.read()
        assert captured == [expected]
        route = special_route or provider
        assert expected["provider"] == {"only": [route], "allow_fallbacks": False, "require_parameters": True}
        receipt, = state.list_receipts()
        assert receipt["requested_model"] == body["model"]
        assert receipt["requested_upstream_model"] == protocol.MODEL
        assert receipt["requested_provider"] == route
        assert receipt["route_reason"] == registry.route_reason
        assert receipt["status"] == "completed"


async def test_loaded_selection_and_health_are_immutable_after_project_file_edit(tmp_path):
    write_config(tmp_path, {**gp.default_app_policy(), "ordinary_default_provider": "morph/fp8"})
    raw = (tmp_path / "gateway.json").read_bytes()
    config = gp.load_service_config(tmp_path)
    captured = []
    async def upstream(request):
        captured.append(await request.json())
        return web.json_response(terminal()["response"])
    async with harness(tmp_path, upstream, settings={"ordinary_default_provider": config.ordinary_default_provider,
                       "config_sha256": config.config_sha256}) as (client, state):
        write_config(tmp_path, {**gp.default_app_policy(), "ordinary_default_provider": "deepseek"})
        health = await (await client.get("/healthz", headers=HEADERS)).json()
        assert health["ordinary_default_provider"] == "morph/fp8"
        assert health["config_sha256"] == hashlib.sha256(raw).hexdigest()
        response = await client.post("/v1/responses", headers=HEADERS,
                                     json={"model": protocol.MODEL, "input": "fixture", "stream": False})
        assert response.status == 200
        await response.read()
        assert captured[0]["provider"]["only"] == ["morph/fp8"]
        assert state.list_receipts()[0]["requested_provider"] == "morph/fp8"


async def test_upstream_failure_is_one_attempt_and_never_changes_selected_provider(tmp_path):
    captured = []
    async def upstream(request):
        captured.append(await request.json())
        return web.json_response({"error": {"message": "fixture upstream unavailable"}}, status=503)
    async with harness(tmp_path, upstream, settings={"ordinary_default_provider": "morph/fp8"}) as (client, state):
        response = await client.post("/v1/responses", headers=HEADERS, json={"model": protocol.MODEL, "stream": False})
        assert response.status == 503
        await response.read()
        assert len(captured) == 1 and captured[0]["provider"]["only"] == ["morph/fp8"]
        assert captured[0]["provider"]["allow_fallbacks"] is False
        receipt, = state.list_receipts()
        assert receipt["requested_provider"] == "morph/fp8" and receipt["status"] == "upstream_error"


@pytest.mark.parametrize("extra", [{"model": "unknown-model"}, {"ordinary_default_provider": "deepseek"},
                                     {"provider": {"only": ["deepseek"]}}, {"unknown_control": True}])
async def test_request_cannot_override_project_selection_or_bypass_control_admission(tmp_path, extra):
    calls = []
    async def upstream(request):
        calls.append(True)
        raise AssertionError("inadmissible request reached upstream")
    async with harness(tmp_path, upstream, settings={"ordinary_default_provider": "morph/fp8"}) as (client, state):
        response = await client.post("/v1/responses", headers=HEADERS, json={"model": protocol.MODEL, **extra})
        assert response.status == 400
        assert calls == [] and state.list_receipts() == []


@pytest.mark.parametrize("selection", [None, "unknown", "primeintellect", True, {}])
def test_lifecycle_health_rejects_missing_or_invalid_selection(monkeypatch, selection):
    value = {"status": "ready", "model": protocol.MODEL, "accepted_models": [protocol.MODEL],
             "allowed_providers": list(gp.DEFAULT_PROVIDERS), "config_sha256": None}
    if selection is not None:
        value["ordinary_default_provider"] = selection
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size): return json.dumps(value).encode()
    class Opener:
        def open(self, *args, **kwargs): return Response()
    monkeypatch.setattr(lifecycle.urllib.request, "build_opener", lambda *args: Opener())
    with pytest.raises(lifecycle.LifecycleError, match="policy identity"):
        lifecycle.health(42817, token_provider=lambda: TOKEN)
