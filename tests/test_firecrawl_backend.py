"""Policy and effects tests for the explicitly selected data-service backend."""
from datetime import datetime, timezone
import json
from pathlib import Path

import pytest

from codex_app_deepseek import firecrawl_backend as fc
from codex_app_deepseek import web_backend as wb
from codex_app_deepseek.gateway_policy import load_service_config, ServiceConfigError

NOW = datetime(2026, 10, 27, 14, 30, tzinfo=timezone.utc)
HTML = "<html><head><title>Cached source</title></head><body>Real needle<a href='/next'>Follow actual link</a></body></html>"


def query_result(url="https://example.com/doc"):
    return {"success": True, "data": {"web": [
        {"url": url, "title": "Index title", "description": "Actual index excerpt"}]}, "id": "public-receipt"}


def native(commands, settings=None, session="source-owner"):
    return {"id": session, "model": wb.MODEL, "commands": commands, "settings": settings or {}}


class RecordingFetcher:
    def __init__(self):
        self.calls = []

    async def fetch(self, url, policy, *, index_check=None):
        if index_check:
            await index_check(url, policy)
        self.calls.append(url)
        return wb.RetrievedDocument(url, url, "text/html", HTML.encode(), "fixture-time")


@pytest.fixture
def setup_backend(tmp_path, monkeypatch):
    store = wb.WebSourceStore(tmp_path / "sources.sqlite")
    fetcher = RecordingFetcher()
    adapter = fc.FirecrawlBackend(fetcher=fetcher, clock=lambda: NOW)
    calls = []
    async def post(endpoint, body):
        calls.append((endpoint, body))
        if endpoint == "/v2/search":
            return query_result(), {"backend": "firecrawl", "http_status": 200}
        return {"success": True, "data": {"html": HTML, "metadata": {
            "statusCode": 200, "sourceURL": body["url"], "url": body["url"]}}}, {"backend": "firecrawl", "http_status": 200}
    monkeypatch.setattr(adapter, "_post", post)
    backend = wb.WebBackend(store, None, fetcher, policy_backend=adapter)
    yield backend, adapter, calls, fetcher, store
    store.close()


def test_data_backend_is_explicit_and_does_not_change_default_policy(tmp_path):
    home = tmp_path / "home"; home.mkdir()
    assert load_service_config(home).web_search_backend == "openrouter_exa"
    path = home / "gateway.json"
    path.write_text('{"web_search_backend":"firecrawl"}')
    assert load_service_config(home).web_search_backend == "firecrawl"
    for invalid in (None, [], True, "auto", "firecrawl_or_exa"):
        path.write_text(json.dumps({"web_search_backend": invalid}))
        with pytest.raises(ServiceConfigError):
            load_service_config(home)


async def test_exact_query_date_and_city_are_real_service_fields(setup_backend):
    backend, _, calls, _, store = setup_backend
    out = await backend.handle(native({"search_query": [{"q": "unchanged near me", "recency": 7,
        "domains": ["example.com"]}]}, {"user_location": {
        "type": "approximate", "country": "US", "region": "California", "city": "San Francisco"},
        "external_web_access": "cached"}))
    endpoint, body = calls[0]
    assert endpoint == "/v2/search"
    assert body == {"query": "unchanged near me", "limit": 5, "sources": ["web"],
        "highlights": False, "includeDomains": ["example.com"],
        "location": "San Francisco,California,US", "country": "US",
        "tbs": "cdr:1,cd_min:10/20/2026,cd_max:10/27/2026"}
    assert "scrapeOptions" not in body
    source = store.get("source-owner", out["results"][0]["ref_id"])
    assert source["provenance"]["date_filter"]["rolling_timestamp_filter"] is False
    assert source["provenance"]["content_origin"] == "search_index_excerpt"
    assert "requested route was DeepSeek" not in out["output"]


async def test_cached_native_open_find_click_retains_real_sources_without_live_fetch(setup_backend):
    backend, _, calls, fetcher, store = setup_backend
    settings = {"external_web_access": False}
    opened = await backend.handle(native({"open": [{"ref_id": "https://example.com/doc"}]}, settings))
    ref = opened["results"][0]["ref_id"]
    assert "Follow actual link [0]" in opened["output"]
    found = await backend.handle(native({"find": [{"ref_id": ref, "pattern": "needle"}]}, settings))
    assert "matching_lines=1" in found["output"]
    assert len(calls) == 1
    followed = await backend.handle(native({"click": [{"ref_id": ref, "id": 0}]}, settings))
    assert followed["results"][0]["url"] == "https://example.com/next"
    assert fetcher.calls == []
    assert all(endpoint == "/v2/scrape" and body["lockdown"] is True for endpoint, body in calls)
    assert all(body["skipTlsVerification"] is False for _, body in calls)
    assert store.db.execute("SELECT count(*) FROM web_receipts WHERE outcome='completed'").fetchone()[0] == 3


async def test_cached_reference_survives_reopen_and_remains_owned(setup_backend):
    backend, _, calls, fetcher, store = setup_backend
    opened = await backend.handle(native({"open": [{"ref_id": "https://example.com/doc"}]},
                                        {"external_web_access": "cached"}))
    ref = opened["results"][0]["ref_id"]
    store.db.close()
    path = store.path
    with_store = wb.WebSourceStore(path)
    try:
        resumed = wb.WebBackend(with_store, None, fetcher, policy_backend=backend.policy_backend)
        out = await resumed.handle(native({"find": [{"ref_id": ref, "pattern": "needle"}]},
                                          {"external_web_access": "cached"}))
        assert "matching_lines=1" in out["output"] and len(calls) == 1
        with pytest.raises(wb.WebBackendError) as err:
            await resumed.handle(native({"open": [{"ref_id": ref}]},
                                        {"external_web_access": False}, session="other-owner"))
        assert err.value.code == "unknown_reference"
        assert "different native web session" in str(err.value)
    finally:
        with_store.close()


@pytest.mark.parametrize("settings,commands,code", [
    ({"user_location": {"type": "approximate", "timezone": "Asia/Shanghai"}},
     {"search_query": [{"q": "x"}]}, "unsupported_timezone"),
    ({"external_web_access": "anything"}, {"open": [{"ref_id": "https://example.com"}]}, "invalid_web_mode"),
    ({"filters": {"allowed_domains": ["example.com"], "blocked_domains": ["blocked.example.com"]}},
     {"search_query": [{"q": "x"}]}, "unsupported_domain_combination"),
    ({"user_location": {"type": "approximate", "city": "bad,separator"}},
     {"search_query": [{"q": "x"}]}, "ambiguous_location"),
])
async def test_unsupported_semantics_reject_whole_batch_before_any_request(setup_backend, settings, commands, code):
    backend, _, calls, fetcher, store = setup_backend
    batch = {"open": [{"ref_id": "https://example.com/doc"}], **commands}
    with pytest.raises(wb.WebBackendError) as err:
        await backend.handle(native(batch, settings))
    assert err.value.code == code
    assert not calls and not fetcher.calls
    assert store.db.execute("SELECT count(*) FROM web_receipts").fetchone()[0] == 0


async def test_missing_index_membership_never_calls_target(setup_backend):
    _, adapter, calls, fetcher, _ = setup_backend
    with pytest.raises(wb.WebBackendError) as err:
        await adapter.fetch_native("https://example.com/not-returned", wb.DomainPolicy(), "indexed")
    assert err.value.code == "index_membership_unverified"
    assert fetcher.calls == [] and len(calls) == 1


@pytest.mark.parametrize("days,expected", [
    (0, "cdr:1,cd_min:10/27/2026,cd_max:10/27/2026"),
    (1, "cdr:1,cd_min:10/26/2026,cd_max:10/27/2026"),
    (365, "cdr:1,cd_min:10/27/2025,cd_max:10/27/2026"),
])
def test_calendar_range_boundaries_are_explicit(days, expected):
    value, proof = fc.recency_filter(days, NOW)
    assert value == expected and proof["kind"] == "UTC_calendar_date_range"


def test_calendar_range_overflow_is_not_a_generic_internal_error():
    with pytest.raises(wb.WebBackendError) as err:
        fc.recency_filter(1_000_000, NOW)
    assert err.value.code == "unsupported_recency"


class Content:
    def __init__(self, body):
        self.body = body

    async def read(self, _limit):
        return self.body

    async def iter_chunked(self, _size):
        yield self.body


class Reply:
    def __init__(self, status=200, data=None, body=None, headers=None):
        self.status = status
        self.content = Content(body if body is not None else json.dumps(data).encode())
        self.headers = headers or {"Content-Type": "text/html"}

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False


@pytest.mark.parametrize("status,data,body,code", [
    (404, {"success": False, "code": "SCRAPE_LOCKDOWN_CACHE_MISS"}, None, "cache_miss"),
    (429, {"success": False}, None, "search_http_error"),
    (401, {"success": False}, None, "search_http_error"),
    (401, None, b"<html>login required</html>", "search_http_error"),
    (302, {"success": False}, None, "search_http_error"),
    (200, None, b'{"success":', "search_protocol_error"),
    (200, {"success": False}, None, "search_protocol_error"),
    (200, {"success": True, "warnings": ["field ignored"]}, None, "search_policy_warning"),
])
async def test_single_attempt_errors_never_change_backend_or_allow_live(monkeypatch, status, data, body, code):
    calls = []
    class Session:
        def __init__(self, **kwargs):
            assert kwargs["trust_env"] is False
            self.connector = kwargs["connector"]
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            await self.connector.close()
        def post(self, url, **kwargs):
            calls.append((url, kwargs))
            assert kwargs["allow_redirects"] is False
            return Reply(status, data=data, body=body)
    monkeypatch.setattr(fc.aiohttp, "ClientSession", Session)
    adapter = fc.FirecrawlBackend()
    with pytest.raises(wb.WebBackendError) as err:
        await adapter._post("/v2/scrape", {"url": "https://example.com/new", "lockdown": True})
    assert err.value.code == code
    assert len(calls) == 1
    assert calls[0][0] == fc.API_ORIGIN + "/v2/scrape"
    assert calls[0][1]["json"]["lockdown"] is True
    assert err.value.evidence["automatic_retries"] == 0


@pytest.mark.parametrize("second_indexed", [False, True])
async def test_real_fetch_redirect_loop_checks_every_target_before_get(monkeypatch, second_indexed):
    order = []
    class Session:
        def __init__(self, **kwargs):
            self.connector = kwargs["connector"]
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            await self.connector.close()
        def get(self, url, **kwargs):
            order.append(("GET", url))
            assert kwargs["allow_redirects"] is False
            if url.endswith("/doc"):
                return Reply(302, body=b"", headers={"Location": "/next"})
            return Reply(200, body=HTML.encode())
    monkeypatch.setattr(wb.aiohttp, "ClientSession", Session)
    adapter = fc.FirecrawlBackend(clock=lambda: NOW)
    async def post(endpoint, body):
        assert endpoint == "/v2/search"
        order.append(("INDEX", body["query"]))
        target = body["query"]
        return query_result(target if second_indexed or target.endswith("/doc") else "https://example.com/doc"), {}
    monkeypatch.setattr(adapter, "_post", post)
    if second_indexed:
        document, proof = await adapter.fetch_native("https://example.com/doc", wb.DomainPolicy(), "indexed")
        assert document.final_url == "https://example.com/next"
        assert len(proof["index_membership_checks"]) == 2
        assert order[-1] == ("GET", "https://example.com/next")
    else:
        with pytest.raises(wb.WebBackendError) as err:
            await adapter.fetch_native("https://example.com/doc", wb.DomainPolicy(), "indexed")
        assert err.value.code == "index_membership_unverified"
        assert ("GET", "https://example.com/next") not in order
    assert order[:3] == [("INDEX", "https://example.com/doc"), ("GET", "https://example.com/doc"),
                         ("INDEX", "https://example.com/next")]


@pytest.mark.parametrize("kind", ["fragmented", "truncated", "overflow", "malformed_complete"])
async def test_actual_http_fragmentation_eof_and_byte_budget(monkeypatch, kind):
    import asyncio
    import aiohttp
    from aiohttp import web
    calls = []
    valid = json.dumps({"success": True, "data": {"web": []}, "note": "合法分块"},
                       ensure_ascii=False).encode()
    async def handler(request):
        calls.append(await request.json())
        reply = web.StreamResponse(headers={"Content-Type": "application/json"})
        await reply.prepare(request)
        raw = valid if kind == "fragmented" else (
            b'{"success":' if kind in {"truncated", "malformed_complete"} else
            json.dumps({"success": True, "body": "x" * 1000}).encode())
        try:
            for i in range(0, len(raw), 5):
                await reply.write(raw[i:i+5])
                await asyncio.sleep(0.001)
            if kind == "truncated":
                request.transport.close()
            else:
                await reply.write_eof()
        except ConnectionResetError:
            pass  # expected when the bounded client rejects an oversized body
        return reply
    app = web.Application(); app.router.add_post("/v2/search", handler)
    runner = web.AppRunner(app); await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0); await site.start()
    port = site._server.sockets[0].getsockname()[1]
    monkeypatch.setattr(fc, "API_ORIGIN", f"http://127.0.0.1:{port}")
    monkeypatch.setattr(fc, "PublicResolver", aiohttp.resolver.ThreadedResolver)
    adapter = fc.FirecrawlBackend(max_bytes=80 if kind == "overflow" else 4096)
    try:
        if kind == "fragmented":
            data, proof = await adapter._post("/v2/search", {"query": "unchanged public fixture"})
            assert data["note"] == "合法分块"
            assert proof["response_complete"] is True and proof["response_bytes"] == len(valid)
            assert proof["response_sha256"] == __import__("hashlib").sha256(valid).hexdigest()
        else:
            with pytest.raises(wb.WebBackendError) as err:
                await adapter._post("/v2/search", {"query": "unchanged public fixture"})
            expected = {"truncated": "search_network_error", "overflow": "search_response_too_large",
                        "malformed_complete": "search_protocol_error"}[kind]
            assert err.value.code == expected
            assert err.value.evidence["response_complete"] is (kind == "malformed_complete")
            if kind == "overflow":
                assert err.value.evidence["response_bytes"] > adapter.max_bytes
        assert calls == [{"query": "unchanged public fixture"}]  # no read-only replay either
    finally:
        await runner.cleanup()


async def test_successful_http_proof_is_preserved_on_untrusted_source_errors(monkeypatch):
    adapter = fc.FirecrawlBackend()
    async def post(endpoint, payload):
        return query_result("https://127.0.0.1/"), {
            "backend": "firecrawl", "http_status": 200, "service_request_id": "one-receipt",
            "response_sha256": "0" * 64}
    monkeypatch.setattr(adapter, "_post", post)
    with pytest.raises(wb.WebBackendError) as err:
        await adapter.search_native("x", wb.DomainPolicy(), "medium")
    assert err.value.code == "private_target"
    assert err.value.evidence["service_request_id"] == "one-receipt"


@pytest.mark.parametrize("metadata,code", [
    ({"statusCode": 200, "sourceURL": "https://example.com/doc"}, "cached_document_identity_unverified"),
    ({"statusCode": 200, "sourceURL": "https://example.com/other", "url": "https://example.com/doc"},
     "cached_document_identity_unverified"),
    ({"statusCode": 200, "sourceURL": "https://example.com/doc", "url": "https://forbidden.invalid/doc"},
     "domain_denied"),
])
async def test_cached_final_identity_is_not_invented_or_allowed_past_policy(monkeypatch, metadata, code):
    adapter = fc.FirecrawlBackend()
    async def post(endpoint, payload):
        return {"success": True, "data": {"html": HTML, "metadata": metadata}}, {
            "backend": "firecrawl", "http_status": 200, "service_request_id": "cached-receipt"}
    monkeypatch.setattr(adapter, "_post", post)
    with pytest.raises(wb.WebBackendError) as err:
        await adapter.fetch_native("https://example.com/doc", wb.DomainPolicy((("example.com",),)), "cached")
    assert err.value.code == code
    assert err.value.evidence["service_request_id"] == "cached-receipt"


@pytest.mark.parametrize("context,limit", [("low", 3), ("medium", 5), ("high", 10)])
async def test_context_size_is_an_explicit_finite_service_budget(setup_backend, context, limit):
    _, adapter, calls, _, _ = setup_backend
    _, evidence = await adapter.search_native("same query", wb.DomainPolicy(), context,
        location={"type": "approximate", "city": "Tokyo", "country": "jp"})
    assert calls[0][1]["limit"] == limit and calls[0][1]["country"] == "JP"
    assert calls[0][1]["query"] == "same query"
    assert evidence["requested_context_size"] == context and evidence["result_limit"] == limit


async def test_country_is_not_inferred_from_timezone_or_invalid_country_names(setup_backend):
    backend, _, calls, _, _ = setup_backend
    with pytest.raises(wb.WebBackendError) as err:
        await backend.handle(native({"search_query": [{"q": "near me"}]},
            {"user_location": {"type": "approximate", "country": "United States"}}))
    assert err.value.code == "unsupported_country" and calls == []


@pytest.mark.parametrize("allowed,blocked,remaining", [
    (["example.com"], ["reddit.com"], ["example.com"]),
    (["Example.COM"], ["REDDIT.com"], ["example.com"]),
    (["example.com"], ["notexample.com"], ["example.com"]),
    (["example.com"], ["example.com.evil.com"], ["example.com"]),
    (["example.com", "allowed.org"], ["example.com"], ["allowed.org"]),
    (["child.example.com", "allowed.org"], ["example.com"], ["allowed.org"]),
])
async def test_domain_reduction_only_removes_provably_redundant_constraints(
        setup_backend, monkeypatch, allowed, blocked, remaining):
    backend, adapter, calls, _, store = setup_backend
    async def post(endpoint, body):
        calls.append((endpoint, body))
        return query_result("https://" + remaining[0] + "/doc"), {
            "backend": "firecrawl", "http_status": 200}
    monkeypatch.setattr(adapter, "_post", post)
    out = await backend.handle(native({"search_query": [{"q": "original query"}]},
        {"filters": {"allowed_domains": allowed, "blocked_domains": blocked}}))
    assert len(calls) == 1
    assert calls[0][1] == {"query": "original query", "limit": 5, "sources": ["web"],
                         "highlights": False, "includeDomains": remaining}
    assert out["results"][0]["url"] == "https://" + remaining[0] + "/doc"
    evidence = json.loads(store.db.execute(
        "SELECT evidence FROM web_receipts").fetchone()[0])
    assert evidence["upstream"]["http_status"] == 200
    assert "local_policy_empty" not in evidence["upstream"]


@pytest.mark.parametrize("allowed,blocked", [
    (["example.com"], ["example.com"]),
    (["child.example.com"], ["example.com"]),
    (["example.com", "child.allowed.org"], ["EXAMPLE.com", "allowed.org"]),
])
async def test_domain_reduction_known_empty_is_no_request_not_provider_success(
        setup_backend, allowed, blocked):
    backend, _, calls, fetcher, store = setup_backend
    out = await backend.handle(native({"search_query": [{"q": "original query"}]},
        {"filters": {"allowed_domains": allowed, "blocked_domains": blocked}}))
    assert out["results"] == []
    assert calls == [] and fetcher.calls == []
    outcome, body = store.db.execute(
        "SELECT outcome,evidence FROM web_receipts").fetchone()
    evidence = json.loads(body)["upstream"]
    assert outcome == "completed"
    assert evidence["request_performed"] is False
    assert evidence["local_policy_empty"] is True
    assert evidence["route_verified"] is False
    assert not {"http_status", "response_sha256", "service_request_id"} & set(evidence)
    assert store.db.execute("SELECT count(*) FROM web_sources").fetchone()[0] == 0


@pytest.mark.parametrize("allowed,blocked", [
    (["example.com"], ["blocked.example.com"]),
    (["example.com", "blocked.example.com"], ["blocked.example.com"]),
    (["example.com", "allowed.org"], ["example.com", "nested.allowed.org"]),
])
async def test_domain_reduction_partial_subtree_remains_whole_batch_rejection(
        setup_backend, allowed, blocked):
    backend, _, calls, fetcher, store = setup_backend
    with pytest.raises(wb.WebBackendError) as err:
        await backend.handle(native({"time": [{"utc_offset": "+00:00"}],
                                    "search_query": [{"q": "original query"}]},
            {"filters": {"allowed_domains": allowed, "blocked_domains": blocked}}))
    assert err.value.code == "unsupported_domain_combination"
    assert calls == [] and fetcher.calls == []
    assert store.db.execute("SELECT count(*) FROM web_receipts").fetchone()[0] == 0


@pytest.mark.parametrize("invalid", [".example.com", "example.com.", "*.example.com"])
async def test_domain_reduction_preserves_existing_hostname_grammar(setup_backend, invalid):
    backend, _, calls, _, store = setup_backend
    with pytest.raises(wb.WebBackendError) as err:
        await backend.handle(native({"search_query": [{"q": "original query"}]},
            {"filters": {"allowed_domains": [invalid], "blocked_domains": ["reddit.com"]}}))
    assert err.value.code == "invalid_domain"
    assert calls == []
    assert store.db.execute("SELECT count(*) FROM web_receipts").fetchone()[0] == 0


@pytest.mark.parametrize("field", ["allowed_domains", "blocked_domains"])
async def test_domain_reduction_preserves_native_domain_count_limit(setup_backend, field):
    backend, _, calls, _, store = setup_backend
    with pytest.raises(wb.WebBackendError) as err:
        await backend.handle(native({"search_query": [{"q": "original query"}]},
            {"filters": {field: [f"domain{i}.example.com" for i in range(101)]}}))
    assert err.value.code == "invalid_request"
    assert calls == []
    assert store.db.execute("SELECT count(*) FROM web_receipts").fetchone()[0] == 0


async def test_domain_reduction_query_domains_use_same_normalization_in_preflight_and_request(
        setup_backend, monkeypatch):
    backend, adapter, calls, _, _ = setup_backend
    async def post(endpoint, body):
        calls.append((endpoint, body))
        return query_result("https://www.example.com/doc"), {"backend": "firecrawl"}
    monkeypatch.setattr(adapter, "_post", post)
    out = await backend.handle(native(
        {"search_query": [{"q": "original query", "domains": ["WWW.Example.COM"]}]},
        {"filters": {"allowed_domains": ["example.com"], "blocked_domains": ["reddit.com"]}}))
    assert calls[0][1]["includeDomains"] == ["www.example.com"]
    assert calls[0][1]["query"] == "original query"
    assert out["results"][0]["url"] == "https://www.example.com/doc"


@pytest.mark.parametrize("field", ["allowed_domains", "blocked_domains"])
async def test_domain_reduction_accepts_existing_hundred_domain_boundary(
        setup_backend, monkeypatch, field):
    backend, adapter, calls, _, _ = setup_backend
    domains = [f"domain{i}.example.com" for i in range(100)]
    filters = {"allowed_domains": ["example.com"], "blocked_domains": ["reddit.com"]}
    filters[field] = domains
    # With a blocklist nested inside example.com this would be the unsupported
    # partial case. Use disjoint domains so the boundary itself is exercised.
    if field == "blocked_domains":
        filters["allowed_domains"] = ["allowed.org"]
        result_host = "allowed.org"
    else:
        result_host = domains[0]
    async def post(endpoint, body):
        calls.append((endpoint, body))
        return query_result(f"https://{result_host}/doc"), {"backend": "firecrawl"}
    monkeypatch.setattr(adapter, "_post", post)
    await backend.handle(native({"search_query": [{"q": "original query"}]}, {"filters": filters}))
    assert len(calls) == 1
    assert calls[0][1]["includeDomains"] == (
        sorted(domains) if field == "allowed_domains" else ["allowed.org"])


async def test_domain_reduction_known_empty_with_explicit_nullable_location(setup_backend):
    backend, _, calls, _, store = setup_backend
    out = await backend.handle(native({"search_query": [{"q": "original query"}]}, {
        "user_location": {"type": "approximate", "country": None},
        "filters": {"allowed_domains": ["example.com"], "blocked_domains": ["example.com"]}}))
    assert out["results"] == [] and calls == []
    proof = json.loads(store.db.execute("SELECT evidence FROM web_receipts").fetchone()[0])["upstream"]
    assert proof["country_bias"] is None and proof["location_bias"] is None
    assert proof["local_policy_empty"] and not proof["request_performed"]


def test_domain_reduction_matches_native_acceptance_for_boundary_witnesses():
    from itertools import combinations
    names = ["example.com", "child.example.com", "other.org"]
    blocks = names + ["notexample.com", "example.com.evil.com"]
    witnesses = names + blocks + ["deep.child.example.com", "new.example.com",
        "child.other.org", "deep.notexample.com", "example.com.evil.com", "outside.net"]
    sets = lambda values: [()] + [item for size in (1, 2) for item in combinations(values, size)]
    for allowed in sets(names):
        for blocked in sets(blocks):
            original = wb.DomainPolicy((allowed,) if allowed else (), blocked)
            try:
                filters = fc.domain_filters(original)
            except wb.WebBackendError as error:
                assert error.code == "unsupported_domain_combination"
                continue  # representability refusal is tested separately with no effects
            if filters is None:
                assert not any(original.accepts(host) for host in witnesses)
            else:
                include = tuple(filters.get("includeDomains", ()))
                reduced = wb.DomainPolicy((include,) if include else (),
                                          tuple(filters.get("excludeDomains", ())))
                assert [(host, original.accepts(host)) for host in witnesses] == [
                    (host, reduced.accepts(host)) for host in witnesses]
