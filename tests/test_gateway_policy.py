from __future__ import annotations
import hashlib
import json
from pathlib import Path
import pytest
from codex_app_deepseek import gateway_policy as gp
from codex_deepseek import protocol


def test_versioned_app_default_covers_actual_pinned_prepare_routes(tmp_path):
    assert gp.PINNED_MODEL == protocol.MODEL
    assert set(gp.DEFAULT_PROVIDERS) == {protocol.PROVIDER, protocol.STRUCTURED_PROVIDER,
                                        protocol.SERIAL_PROVIDER, protocol.PEER_HOSTED_PROVIDER}
    tool = {"type": "function", "name": "fixture", "parameters": {"type": "object", "properties": {}}}
    ordinary, _ = protocol.prepare({"model": protocol.MODEL, "input": "fixture"})
    serial, _ = protocol.prepare({"model": protocol.MODEL, "input": "fixture", "tools": [tool],
                                 "parallel_tool_calls": False})
    schema, _ = protocol.prepare({"model": protocol.MODEL, "input": "fixture",
                                 "text": {"format": {"type": "json_schema", "schema": {"type": "object"}}}})
    assert {ordinary["provider"]["only"][0], serial["provider"]["only"][0],
            schema["provider"]["only"][0]} == set(gp.DEFAULT_PROVIDERS)
    config, receipt = gp.ensure_app_policy(tmp_path / "new")
    assert receipt["status"] == "created_fixed_app_policy"
    assert config.policy_origin["provider_reasons"]["primeintellect"] == "explicit_parallel_tool_calls_false"
    lock = Path(__file__).resolve().parents[1] / "release/dependencies.windows-py313.json"
    pinned_commit = json.loads(lock.read_text(encoding="utf-8"))["protocol_boundary"]["source_commit"]
    assert config.policy_origin["protocol_commit"] == pinned_commit


def test_config_snapshot_parses_and_hashes_same_single_read(tmp_path, monkeypatch):
    home = tmp_path / "gateway"; home.mkdir()
    path = home / "gateway.json"
    old = b'{"accepted_models":["old"],"allowed_providers":["deepseek"]}\n'
    new = b'{"accepted_models":["new"],"allowed_providers":["deepseek"]}\n'
    path.write_bytes(old)
    original = Path.read_bytes
    reads = []
    def changed_after_read(self):
        raw = original(self)
        if self == path:
            reads.append(self); self.write_bytes(new)
        return raw
    monkeypatch.setattr(Path, "read_bytes", changed_after_read)
    config = gp.load_service_config(home)
    assert config.accepted_models == ("old",)
    assert config.config_sha256 == hashlib.sha256(old).hexdigest()
    assert reads == [path] and original(path) == new


def test_existing_custom_policy_is_byte_preserved_with_extra_explicit_alias(tmp_path):
    home = tmp_path / "gateway"; home.mkdir()
    raw = json.dumps({"upstream_proxy": "http://127.0.0.1:3128",
                      "accepted_models": ["explicit-helper", *reversed(gp.REQUEST_MODEL_IDS)],
                      "allowed_providers": list(reversed(gp.DEFAULT_PROVIDERS))}, indent=4).encode()
    path = home / "gateway.json"; path.write_bytes(raw)
    config, info = gp.ensure_app_policy(home)
    gp.require_app_policy(config, ["explicit-helper", *gp.REQUEST_MODEL_IDS])
    assert path.read_bytes() == raw and info["status"] == "existing_policy_preserved"
    assert config.policy_origin is None


@pytest.mark.parametrize("field", ["accepted_models", "allowed_providers"])
def test_existing_narrowed_policy_is_not_silently_expanded(tmp_path, field):
    home = tmp_path / "gateway"; home.mkdir()
    value = gp.default_app_policy(); value[field].pop()
    raw = json.dumps(value).encode(); path = home / "gateway.json"; path.write_bytes(raw)
    with pytest.raises(gp.ServiceConfigError):
        gp.ensure_app_policy(home)
    assert path.read_bytes() == raw


@pytest.mark.skipif(gp.os.name != "nt", reason="Windows no-replace atomic rename")
def test_concurrent_custom_policy_wins_initialization_without_overwrite(tmp_path, monkeypatch):
    home = tmp_path / "gateway"; home.mkdir()
    winner = json.dumps({"accepted_models": list(reversed(gp.REQUEST_MODEL_IDS)),
                         "allowed_providers": list(reversed(gp.DEFAULT_PROVIDERS))}).encode()
    original = gp.os.rename
    def competing_write(source, target):
        Path(target).write_bytes(winner)
        return original(source, target)
    monkeypatch.setattr(gp.os, "rename", competing_write)
    config, receipt = gp.ensure_app_policy(home)
    assert (home / "gateway.json").read_bytes() == winner
    assert receipt["status"] == "existing_policy_preserved" and config.policy_origin is None
    assert sorted(x.name for x in home.iterdir()) == ["gateway.json"]


@pytest.mark.parametrize("origin", [{"policy_id": "unknown"}, {}, False, "fixed"])
def test_unknown_policy_origin_is_explicitly_rejected(tmp_path, origin):
    home = tmp_path / "gateway"; home.mkdir()
    value = gp.default_app_policy(); value["policy_origin"] = origin
    (home / "gateway.json").write_text(json.dumps(value))
    with pytest.raises(gp.ServiceConfigError, match="unrecognized policy_origin"):
        gp.load_service_config(home)


@pytest.mark.asyncio
async def test_real_health_retains_startup_policy_fingerprint(tmp_path):
    from aiohttp import web
    from test_bridge import harness, HEADERS
    calls = []
    async def handler(request):
        calls.append("unexpected upstream")
        return web.json_response({})
    raw = json.dumps(gp.default_app_policy()).encode()
    policy = tmp_path / "gateway.json"; policy.write_bytes(raw)
    config = gp.load_service_config(tmp_path)
    async with harness(tmp_path, handler, settings={"config_sha256": config.config_sha256,
                         "accepted_models": gp.REQUEST_MODEL_IDS}) as (client, state):
        policy.write_bytes(raw + b" ")
        response = await client.get("/healthz", headers=HEADERS)
        health = await response.json()
        assert health["config_sha256"] == hashlib.sha256(raw).hexdigest()
        assert health["accepted_models"] == list(gp.REQUEST_MODEL_IDS)
        assert set(health["allowed_providers"]) == set(gp.DEFAULT_PROVIDERS)
        assert calls == [] and state.list_receipts() == []

@pytest.mark.parametrize("old_commit", [
    "4f24af36f62c538ba35817ad48aa49d4308220b5",
    "c68e065935c7a11f92be05c2e43e8716f739540f",
], ids=["published-historical", "private-development"])
def test_old_pinned_origin_requires_explicit_operator_transition_without_rewrite(tmp_path, old_commit):
    home = tmp_path / "gateway"; home.mkdir()
    value = gp.default_app_policy()
    value["policy_origin"]["protocol_commit"] = old_commit
    value["upstream_proxy"] = "http://127.0.0.1:3128"
    value["web_search_backend"] = "firecrawl"
    raw = (json.dumps(value, indent=4) + "\n").encode()
    path = home / "gateway.json"; path.write_bytes(raw)
    state = home / "state-preserved.bin"; state.write_bytes(b"unchanged fixture state")
    with pytest.raises(gp.ServiceConfigError, match="unrecognized policy_origin"):
        gp.ensure_app_policy(home)
    assert path.read_bytes() == raw
    assert state.read_bytes() == b"unchanged fixture state"
    assert sorted(item.name for item in home.iterdir()) == ["gateway.json", "state-preserved.bin"]


@pytest.mark.parametrize("known_origin", [gp.LEGACY_POLICY_ORIGIN_067,gp.LEGACY_POLICY_ORIGIN_068,gp.LEGACY_POLICY_ORIGIN_068_PUBLISHED], ids=["legacy067","legacy068","published068"])
def test_exact_previous_initialization_origin_is_preserved_on_protocol_only_upgrade(tmp_path, known_origin):
    home = tmp_path / "gateway"; home.mkdir()
    value = gp.default_app_policy()
    value["policy_origin"] = json.loads(json.dumps(known_origin))
    value["upstream_proxy"] = "http://127.0.0.1:3128"
    value["web_search_backend"] = "firecrawl"
    raw = (json.dumps(value, indent=4) + "\n").encode()
    path = home / "gateway.json"; path.write_bytes(raw)
    state = home / "unrelated-state.bin"; state.write_bytes(b"preserved")
    config, receipt = gp.ensure_app_policy(home)
    assert config.policy_origin == known_origin
    assert config.policy_origin["protocol_commit"] == known_origin["protocol_commit"]
    assert config.config_sha256 == hashlib.sha256(raw).hexdigest()
    assert receipt["status"] == "existing_policy_preserved"
    assert receipt["origin"] == value["policy_origin"]
    assert path.read_bytes() == raw and state.read_bytes() == b"preserved"
    assert config.allowed_providers == tuple(gp.DEFAULT_PROVIDERS)
    assert config.accepted_models == tuple(gp.REQUEST_MODEL_IDS)


@pytest.mark.parametrize("change", ["native_version", "policy_id", "protocol_commit", "reason", "extra"])
@pytest.mark.parametrize("known_origin", [gp.LEGACY_POLICY_ORIGIN_067,gp.LEGACY_POLICY_ORIGIN_068,gp.LEGACY_POLICY_ORIGIN_068_PUBLISHED], ids=["legacy067","legacy068","published068"])
def test_previous_origin_compatibility_rejects_any_unrecognized_metadata(tmp_path, known_origin, change):
    home = tmp_path / "gateway"; home.mkdir()
    value = gp.default_app_policy()
    origin = json.loads(json.dumps(known_origin))
    if change == "reason":
        origin["provider_reasons"]["deepseek"] = "different"
    elif change == "extra":
        origin["future_behavior"] = True
    else:
        origin[change] = "different"
    value["policy_origin"] = origin
    raw = json.dumps(value).encode(); path = home / "gateway.json"; path.write_bytes(raw)
    with pytest.raises(gp.ServiceConfigError, match="unrecognized policy_origin"):
        gp.ensure_app_policy(home)
    assert path.read_bytes() == raw


@pytest.mark.parametrize("field", ["allowed_providers", "accepted_models"])
@pytest.mark.parametrize("known_origin", [gp.LEGACY_POLICY_ORIGIN_067,gp.LEGACY_POLICY_ORIGIN_068,gp.LEGACY_POLICY_ORIGIN_068_PUBLISHED], ids=["legacy067","legacy068","published068"])
def test_previous_origin_does_not_expand_a_narrowed_policy(tmp_path, known_origin, field):
    home = tmp_path / "gateway"; home.mkdir()
    value = gp.default_app_policy()
    value["policy_origin"] = json.loads(json.dumps(known_origin))
    value[field].pop()
    raw = json.dumps(value).encode(); path = home / "gateway.json"; path.write_bytes(raw)
    with pytest.raises(gp.ServiceConfigError, match="policy lacks"):
        gp.ensure_app_policy(home)
    assert path.read_bytes() == raw


def test_previous_published_069_policy_keeps_original_bytes(tmp_path):
    home = tmp_path / "existing-069"; home.mkdir()
    value = gp.default_app_policy(); value["policy_origin"] = gp.LEGACY_POLICY_ORIGIN_069
    raw = json.dumps(value, indent=4).encode()
    path = home / "gateway.json"; path.write_bytes(raw)
    config, info = gp.ensure_app_policy(home)
    assert path.read_bytes() == raw
    assert config.policy_origin == gp.LEGACY_POLICY_ORIGIN_069
    assert info["status"] == "existing_policy_preserved"
