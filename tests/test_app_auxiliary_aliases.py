"""Fixed desktop helper aliases: exact routing, metadata, and explicit upgrades."""
from copy import deepcopy
import json

from aiohttp import web
import pytest

from codex_app_deepseek import catalog, gateway_policy as gp
from test_catalog import bundled
from test_bridge import harness, HEADERS, message, terminal


def test_fixed_desktop_aliases_use_actual_deepseek_runtime_metadata_without_wildcards():
    source = bundled()
    misleading = deepcopy(source["models"][1])
    misleading.update(slug="gpt-6-luna", multi_agent_version="v1", supports_search_tool=False)
    source["models"].append(misleading)
    before = deepcopy(source)
    result = catalog.build_catalog(source, native_version=catalog.NATIVE_VERSION)["models"]
    assert [item["slug"] for item in result] == [
        catalog.PINNED_MODEL, "gpt-5.6-luna", "gpt-6-luna", "gpt-6.1-sol", "gpt-6.1-terra"]
    assert source == before
    by_id = {item["slug"]: item for item in result}
    for alias in ("gpt-6-luna", "gpt-6.1-sol", "gpt-6.1-terra"):
        model = by_id[alias]
        assert model["visibility"] == "hide" and model["supported_in_api"] is True
        assert "not an OpenAI model" in model["description"]
        for key in ("multi_agent_version", "model_messages", "shell_type",
                    "node_repl_auto_review_required", "supports_search_tool"):
            assert model[key] == by_id[catalog.PINNED_MODEL][key]
    assert by_id["gpt-5.6-luna"]["multi_agent_version"] == "v1"  # existing native alias unchanged
    with pytest.raises(catalog.CatalogCompatibilityError):
        catalog.build_catalog(bundled(), native_version=catalog.NATIVE_VERSION,
                              auxiliary_aliases=("gpt-6-luna-unqualified",))


def test_old_two_model_policy_is_refused_and_never_silently_rewritten(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    legacy = gp.default_app_policy()
    legacy["accepted_models"] = [catalog.PINNED_MODEL, "gpt-5.6-luna"]
    raw = (json.dumps(legacy, indent=4) + "\n").encode()
    path = home / "gateway.json"; path.write_bytes(raw)
    config = gp.load_service_config(home)
    assert config.accepted_models == (catalog.PINNED_MODEL, "gpt-5.6-luna")
    with pytest.raises(gp.ServiceConfigError) as err:
        gp.ensure_app_policy(home)
    text = str(err.value)
    assert "gpt-6-luna" in text and "gpt-6.1-sol" in text
    assert "explicitly update accepted_models" in text
    assert path.read_bytes() == raw


def test_previous_full_policy_requires_explicit_terra_alias(tmp_path):
    policy = gp.default_app_policy()
    policy["policy_origin"] = gp.LEGACY_POLICY_ORIGIN_E83
    policy["accepted_models"].remove("gpt-6.1-terra")
    path = tmp_path / "gateway.json"
    old = json.dumps(policy, indent=2).encode()
    path.write_bytes(old)
    assert gp.load_service_config(tmp_path).policy_origin == gp.LEGACY_POLICY_ORIGIN_E83
    with pytest.raises(gp.ServiceConfigError, match="gpt-6.1-terra"):
        gp.ensure_app_policy(tmp_path)
    assert path.read_bytes() == old

    policy["accepted_models"].append("gpt-6.1-terra")
    edited = json.dumps(policy, indent=2).encode()
    path.write_bytes(edited)
    _, receipt = gp.ensure_app_policy(tmp_path)
    assert receipt["status"] == "existing_policy_preserved"
    assert path.read_bytes() == edited


def test_new_explicit_policy_and_catalog_must_have_the_same_exact_ids(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    raw = (json.dumps(gp.default_app_policy()) + "\n").encode()
    (home / "gateway.json").write_bytes(raw)
    config = gp.load_service_config(home)
    catalog_ids = [m["slug"] for m in catalog.build_catalog(
        bundled(), native_version=catalog.NATIVE_VERSION)["models"]]
    gp.require_app_policy(config, catalog_ids)
    assert list(config.accepted_models) == catalog_ids
    with pytest.raises(gp.ServiceConfigError, match="immutable App catalog"):
        gp.require_app_policy(config, [catalog.PINNED_MODEL, "gpt-5.6-luna"])
    assert (home / "gateway.json").read_bytes() == raw


@pytest.mark.asyncio
@pytest.mark.parametrize("alias", ["gpt-6-luna", "gpt-6.1-sol", "gpt-6.1-terra"])
async def test_helper_structured_request_maps_only_explicit_alias_to_fixed_deepseek(tmp_path, alias):
    requests = []
    async def handler(request):
        requests.append(await request.json())
        return web.json_response(terminal([message(json.dumps(
            {"description": "Structured helper result from the selected model."}))])["response"])
    body = {
        "model": alias, "input": "Generate a short description.", "reasoning": {"effort": "low"},
        "text": {"format": {"type": "json_schema", "name": "thread_description",
            "strict": True, "schema": {"type": "object", "properties": {
                "description": {"type": "string", "minLength": 1}},
                "required": ["description"], "additionalProperties": False}}},
    }
    # Reproduce the old exact failure before testing the explicit new policy.
    async with harness(tmp_path, handler, settings={
        "accepted_models": (catalog.PINNED_MODEL, "gpt-5.6-luna")}) as (client, state):
        response = await client.post("/v1/responses", json=body, headers=HEADERS)
        error = await response.json()
        assert response.status == 400
        assert error["error"]["code"] == "provider_compatibility"
        assert "explicitly configured alias" in error["error"]["message"]
        assert requests == [] and state.list_receipts() == []
    async with harness(tmp_path, handler, settings={
        "accepted_models": gp.REQUEST_MODEL_IDS,
        "allowed_providers": gp.DEFAULT_PROVIDERS}) as (client, state):
        response = await client.post("/v1/responses", json=body, headers=HEADERS)
        result = await response.json()
        assert response.status == 200 and result["status"] == "completed"
        assert len(requests) == 1 and requests[0]["model"] == catalog.PINNED_MODEL
        assert requests[0]["provider"] == {
            "only": ["morph/fp8"], "allow_fallbacks": False, "require_parameters": True}
        receipt = state.receipt(response.headers["X-Gateway-Request-Id"])
        assert receipt["requested_model"] == alias
        assert receipt["requested_upstream_model"] == catalog.PINNED_MODEL
        assert receipt["requested_provider"] == "morph/fp8" and receipt["status"] == "completed"
        unknown = await client.post("/v1/responses", json={**body, "model": alias + "-unqualified"},
                                    headers=HEADERS)
        assert unknown.status == 400
        assert len(requests) == 1 and len(state.list_receipts()) == 1
