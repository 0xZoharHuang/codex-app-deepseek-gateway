import asyncio
from contextlib import asynccontextmanager
import json

from aiohttp import web
from aiohttp.test_utils import TestServer
import pytest

from codex_deepseek.protocol import MODEL
from codex_app_deepseek.upstream_evidence import read_generation_evidence

KEY = "synthetic-readback-credential-never-real"
GENERATION_ID = "gen-example123"
CANONICAL = "deepseek/deepseek-v4.1-flash-20261017"
PROMPT = "PRIVATE_PROMPT_MUST_NOT_BE_PERSISTED"


def receipt():
    return {"id": "receipt-one", "response_id": GENERATION_ID, "requested_model": "guardian",
            "requested_upstream_model": MODEL, "requested_provider": "deepseek"}


def generation(model=CANONICAL):
    return {"data": {"id": GENERATION_ID, "model": model, "provider_name": "Actual Provider",
                     "api_type": "responses", "cancelled": False, "upstream_id": "native-upstream-1",
                     "tokens_prompt": 100, "tokens_completion": 12, "total_cost": .0001,
                     "prompt": PROMPT, "completion": PROMPT,
                     "external_user": "private-user", "provider_responses": [{"prompt": PROMPT}]}}


@asynccontextmanager
async def server(generation_handler, model_handler=None):
    app = web.Application()
    app.router.add_get("/api/v1/generation", generation_handler)
    async def default_model(request):
        assert "Authorization" not in request.headers
        return web.json_response({"data": {"id": MODEL, "canonical_slug": CANONICAL, "created": 1792225624}})
    app.router.add_get("/api/v1/model/" + MODEL, model_handler or default_model)
    async with TestServer(app) as test_server:
        yield str(test_server.make_url("/api/v1"))


@pytest.mark.asyncio
async def test_canonical_alias_requires_official_model_metadata_and_preserves_observed_provider():
    calls = []
    async def handler(request):
        assert request.method == "GET" and request.query["id"] == GENERATION_ID
        assert request.headers["Authorization"] == "Bearer " + KEY
        calls.append(request.path)
        return web.json_response(generation())
    async with server(handler) as api:
        result = await read_generation_evidence(receipt(), lambda: KEY, api_base=api, test_upstream=True)
    assert result["lookup_status"] == "found"
    assert result["requested"]["provider"] == "deepseek"
    assert result["observed"]["provider_name"] == "Actual Provider"
    assert result["observed"]["model"] == CANONICAL
    assert result["model_identity"]["status"] == "verified_canonical_alias"
    assert result["model_identity"]["matches_requested"] is True
    proof = result["model_identity"]["proof"]
    assert proof["metadata"] == {"id": MODEL, "canonical_slug": CANONICAL, "created": 1792225624}
    assert len(proof["raw_sha256"]) == 64
    assert calls == ["/api/v1/generation"] and result["inference_requests"] == 0
    serialized = json.dumps(result)
    assert PROMPT not in serialized and KEY not in serialized and "private-user" not in serialized
    assert "provider_responses" not in serialized


@pytest.mark.asyncio
async def test_404_polling_is_bounded_and_only_repeats_read_only_metadata():
    calls = []
    async def handler(request):
        calls.append((request.method, request.query["id"]))
        if len(calls) < 3:
            return web.json_response({"error": {"code": "NOT_FOUND", "message": PROMPT}}, status=404)
        return web.json_response(generation(MODEL))
    async with server(handler) as api:
        result = await read_generation_evidence(receipt(), lambda: KEY, api_base=api,
                                                test_upstream=True, poll_delay=0, max_attempts=4)
    assert calls == [("GET", GENERATION_ID)] * 3
    assert [a["http_status"] for a in result["generation_lookup"]["attempts"]] == [404, 404, 200]
    assert result["model_identity"]["status"] == "exact_id"
    assert PROMPT not in json.dumps(result)


@pytest.mark.asyncio
async def test_exhausted_404_remains_unknown():
    count = 0
    async def handler(request):
        nonlocal count
        count += 1
        return web.json_response({"error": {"code": 404}}, status=404)
    async with server(handler) as api:
        result = await read_generation_evidence(receipt(), lambda: KEY, api_base=api,
                                                test_upstream=True, poll_delay=0, max_attempts=2)
    assert count == 2 and result["lookup_status"] == "not_found"
    assert result["observed"] is None and result["model_identity"]["matches_requested"] is None


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [401, 402, 429, 500, 502, 302])
async def test_non_404_error_is_preserved_without_prompt_or_token_or_retry(status):
    count = 0
    async def handler(request):
        nonlocal count
        count += 1
        return web.json_response({"error": {"code": "UPSTREAM_FAILURE", "message": KEY + PROMPT},
                                  "prompt": PROMPT}, status=status,
                                 headers={"Location": "/never-follow"})
    async with server(handler) as api:
        result = await read_generation_evidence(receipt(), lambda: KEY, api_base=api, test_upstream=True)
    assert count == 1
    attempt = result["generation_lookup"]["attempts"][0]
    assert attempt["http_status"] == status and attempt["error"]["upstream_code"] == "UPSTREAM_FAILURE"
    assert result["lookup_status"] == "http_error"
    assert KEY not in json.dumps(result) and PROMPT not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("case", ["different_date", "wrong_metadata_id", "metadata_unavailable"])
async def test_no_string_guessing_for_date_aliases(case):
    async def handler(request):
        model = "deepseek/deepseek-v4.1-flash-20261018" if case == "different_date" else CANONICAL
        return web.json_response(generation(model))
    async def model_handler(request):
        if case == "metadata_unavailable":
            return web.json_response({"error": {"code": 404}}, status=404)
        return web.json_response({"data": {"id": "other/model" if case == "wrong_metadata_id" else MODEL,
                                          "canonical_slug": CANONICAL}})
    async with server(handler, model_handler) as api:
        result = await read_generation_evidence(receipt(), lambda: KEY, api_base=api, test_upstream=True)
    identity = result["model_identity"]
    if case == "different_date":
        assert identity["status"] == "model_mismatch" and identity["matches_requested"] is False
    else:
        assert identity["matches_requested"] is None
        assert identity["status"] in {"metadata_unavailable", "invalid_alias_metadata"}


@pytest.mark.asyncio
async def test_wrong_generation_id_is_not_accepted_as_this_receipt():
    async def handler(request):
        value = generation()
        value["data"]["id"] = "gen-another"
        return web.json_response(value)
    async with server(handler) as api:
        result = await read_generation_evidence(receipt(), lambda: KEY, api_base=api, test_upstream=True)
    assert result["lookup_status"] == "generation_id_mismatch" and result["observed"] is None


@pytest.mark.asyncio
async def test_missing_observed_identity_stays_unknown():
    async def handler(request):
        return web.json_response({"data": {"id": GENERATION_ID, "model": None, "provider_name": None}})
    async with server(handler) as api:
        result = await read_generation_evidence(receipt(), lambda: KEY, api_base=api, test_upstream=True)
    assert result["observed"]["model"] is None and result["observed"]["provider_name"] is None
    assert result["model_identity"]["status"] == "observed_model_missing"


@pytest.mark.asyncio
async def test_missing_response_id_never_reads_credential_or_network():
    def credential():
        raise AssertionError("credential must not be read")
    value = receipt()
    value["response_id"] = None
    result = await read_generation_evidence(value, credential)
    assert result["lookup_status"] == "missing_or_invalid_response_id"
    assert result["generation_lookup"]["attempts"] == []


@pytest.mark.asyncio
async def test_credential_failure_does_not_return_exception_message():
    def credential():
        raise RuntimeError(KEY + PROMPT)
    result = await read_generation_evidence(receipt(), credential)
    assert result["lookup_status"] == "credential_error"
    assert result["error"]["exception_type"] == "RuntimeError"
    assert KEY not in json.dumps(result) and PROMPT not in json.dumps(result)


@pytest.mark.asyncio
async def test_arbitrary_origin_is_never_used_for_readback_credentials():
    with pytest.raises(ValueError, match="Production metadata"):
        await read_generation_evidence(receipt(), lambda: KEY, api_base="https://untrusted.invalid/api/v1")
    with pytest.raises(ValueError, match="Production metadata"):
        await read_generation_evidence(receipt(), lambda: KEY,
                                       api_base="https://untrusted.invalid/api/v1", test_upstream=True)


@pytest.mark.asyncio
async def test_readback_timeout_preserves_error_and_never_posts_inference():
    methods = []
    async def handler(request):
        methods.append(request.method)
        await asyncio.sleep(.2)
        return web.json_response(generation())
    async with server(handler) as api:
        result = await read_generation_evidence(receipt(), lambda: KEY, api_base=api,
                                                test_upstream=True, timeout=.02)
    assert methods == ["GET"]
    assert result["lookup_status"] == "transport_error"
    assert result["inference_requests"] == 0


@pytest.mark.asyncio
async def test_transport_disconnect_is_not_silently_retried_by_http_library():
    calls = 0
    async def handler(request):
        nonlocal calls
        calls += 1
        request.transport.close()
        return web.Response()
    async with server(handler) as api:
        result = await read_generation_evidence(receipt(), lambda: KEY, api_base=api, test_upstream=True)
    assert calls == 1
    assert result["lookup_status"] == "transport_error"
    assert len(result["generation_lookup"]["attempts"]) == 1


@pytest.mark.asyncio
async def test_malformed_public_alias_metadata_is_not_copied_as_prompt_data():
    async def handler(request):
        return web.json_response(generation())
    async def metadata(request):
        return web.json_response({"data": {"id": MODEL, "canonical_slug": {"prompt": PROMPT}}})
    async with server(handler, metadata) as api:
        result = await read_generation_evidence(receipt(), lambda: KEY, api_base=api, test_upstream=True)
    assert result["model_identity"]["matches_requested"] is None
    assert result["model_identity"]["proof"]["error"]["kind"] == "invalid_model_metadata"
    assert PROMPT not in json.dumps(result)


@pytest.mark.asyncio
async def test_duplicate_generation_json_field_cannot_prove_identity():
    async def handler(request):
        return web.Response(text='{"data":{"id":"'+GENERATION_ID+'","id":"gen-other","model":"'+MODEL+'"}}',content_type='application/json')
    async with server(handler) as api:
        result=await read_generation_evidence(receipt(),lambda:KEY,api_base=api,test_upstream=True)
    assert result['lookup_status']=='invalid_metadata_envelope' and result['observed'] is None


@pytest.mark.asyncio
@pytest.mark.parametrize('advice,expected',[('300','300'),('Fri, 23 Oct 2026 12:00:00 GMT','Fri, 23 Oct 2026 12:00:00 GMT'),(KEY,None)])
async def test_safe_retry_after_is_preserved_without_credential(advice,expected):
    async def handler(request):
        return web.json_response({'error':{'code':'rate_limited','message':KEY}},status=429,headers={'Retry-After':advice})
    async with server(handler) as api:
        result=await read_generation_evidence(receipt(),lambda:KEY,api_base=api,test_upstream=True,max_attempts=1)
    assert result['generation_lookup']['attempts'][0]['error']['headers'].get('retry-after')==expected
    assert KEY not in json.dumps(result)
