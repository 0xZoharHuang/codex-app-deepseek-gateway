"""Explicit image selection, native policy and metadata safety."""
import copy
import json
from datetime import datetime, timezone
import pytest
from codex_app_deepseek import firecrawl_backend as fc
from codex_app_deepseek import web_backend as wb
from codex_app_deepseek.gateway_policy import load_service_config, ServiceConfigError

NOW = datetime(2026, 11, 1, tzinfo=timezone.utc)
LOCATION = {"type": "approximate", "country": "US", "region": "California", "city": "San Francisco"}

def image_record():
    return {"url": "https://www.example.com/image-page", "imageUrl": "https://images.example.net/image(1).webp",
            "title": "] (<https://evil.example/>)", "imageWidth": 640, "imageHeight": 480}

class NoFetch:
    calls = 0
    async def fetch(self, *args, **kwargs):
        self.calls += 1
        pytest.fail("Index metadata must not fetch pages or pixels")

@pytest.fixture
def image_backend(tmp_path):
    store = wb.WebSourceStore(tmp_path / "sources.sqlite")
    fetcher = NoFetch()
    service = fc.FirecrawlBackend(fetcher=fetcher, clock=lambda: NOW)
    calls, record = [], image_record()
    async def post(endpoint, payload):
        calls.append((endpoint, copy.deepcopy(payload)))
        data = ({"web": [{"url": "https://www.example.com/doc", "title": "Text", "description": "Excerpt"}]}
                if payload["sources"] == ["web"] else {"images": [copy.deepcopy(record)]})
        return {"success": True, "data": data}, {"backend": "firecrawl", "http_status": 200, "service_request_id": "fixture"}
    service._post = post
    images = fc.FirecrawlImageSearch(service)
    backend = wb.WebBackend(store, None, fetcher, policy_backend=service, image_search=images)
    yield backend, store, images, calls, record, fetcher
    store.close()

def request(commands, mode=True, **extra):
    return {"id": "owned-image-test", "model": wb.MODEL, "commands": commands,
            "settings": {"external_web_access": mode, "user_location": LOCATION,
                         "filters": {"allowed_domains": ["example.com"]}, **extra}}

def test_selector_is_explicit_and_preserves_default(tmp_path):
    assert load_service_config(tmp_path).image_search_backend is None
    path = tmp_path / "gateway.json"
    path.write_text(json.dumps({"image_search_backend": "firecrawl"}))
    with pytest.raises(ServiceConfigError, match="explicitly configured Firecrawl"):
        load_service_config(tmp_path)
    path.write_text(json.dumps({"web_search_backend": "firecrawl", "image_search_backend": "firecrawl"}))
    assert load_service_config(tmp_path).image_search_backend == "firecrawl"
    path.write_text(json.dumps({"image_search_backend": "wikimedia_commons"}))
    assert load_service_config(tmp_path).image_search_backend == "wikimedia_commons"
    path.write_text(json.dumps({"image_search_backend": "auto"}))
    with pytest.raises(ServiceConfigError): load_service_config(tmp_path)

@pytest.mark.parametrize("mode", [True, False, "indexed"], ids=["live", "cached", "indexed"])
async def test_native_batch_real_fields_and_web_only_tbs(image_backend, mode):
    backend, store, _, calls, _, fetcher = image_backend
    result = await backend.handle(request({"search_query": [{"q": "original text", "recency": 7}],
                                          "image_query": [{"q": "original image", "domains": ["www.example.com"]}]}, mode))
    assert len(calls) == 2
    web, image = calls[0][1], calls[1][1]
    assert web["query"] == "original text" and web["sources"] == ["web"] and "tbs" in web
    assert image == {"query": "original image", "limit": 4, "sources": ["images"], "highlights": False,
                     "includeDomains": ["www.example.com"], "location": "San Francisco,California,US", "country": "US"}
    assert "tbs" not in image and "scrapeOptions" not in image
    assert web["location"] == image["location"] and web["country"] == image["country"]
    assert fetcher.calls == 0
    item = result["results"][1]
    assert item["type"] == "image_v2" and item["image_result"]["width"] == 640
    assert "Wikimedia Commons" not in result["output"]
    assert "Native Markdown display reference" in result["output"]
    assert "![Web image](<https://images.example.net/image%281%29.webp>)" in result["output"]
    assert "license" not in item["image_result"] and "not supplied" in item["image_result"]["attribution"]
    source = store.get("owned-image-test", item["ref_id"])
    assert source["provenance"]["pixel_content_retrieved"] is False
    assert source["provenance"]["target_page_fetch_requested"] is False
    assert source["provenance"]["external_web_access"] == fc.web_mode({"external_web_access": mode})

@pytest.mark.parametrize("query,location,code", [
    ({"q": "original", "recency": 7}, LOCATION, "unsupported_image_recency"),
    ({"q": "original", "recency": 0}, LOCATION, "unsupported_image_recency"),
    ({"q": "original"}, {**LOCATION, "timezone": "America/Los_Angeles"}, "unsupported_timezone")])
async def test_unsupported_policy_preflight_not_fake_filter(image_backend, query, location, code):
    backend, store, _, calls, _, _ = image_backend
    with pytest.raises(wb.WebBackendError) as failure:
        await backend.handle(request({"search_query": [{"q": "would search"}], "image_query": [query]}, user_location=location))
    assert failure.value.code == code and failure.value.status == 422
    assert calls == [] and store.db.execute("SELECT count(*) FROM web_receipts").fetchone()[0] == 0

@pytest.mark.parametrize("mutation", ["private_asset", "credential_asset", "scheme_asset", "blocked_asset",
                                     "outside_landing", "title", "dimension"])
async def test_security_errors_store_no_partial_results(image_backend, mutation):
    backend, store, _, calls, record, _ = image_backend
    if mutation == "private_asset": record["imageUrl"] = "http://127.0.0.1/x.png"
    elif mutation == "credential_asset": record["imageUrl"] = "https://user:pass@example.net/x.png"
    elif mutation == "scheme_asset": record["imageUrl"] = "javascript:alert(1)"
    elif mutation == "blocked_asset": record["imageUrl"] = "https://blocked.net/x.png"
    elif mutation == "outside_landing": record["url"] = "https://other.net/page"
    elif mutation == "title": record["title"] = None
    else: record["imageWidth"] = "640"
    with pytest.raises(wb.WebBackendError):
        await backend.handle(request({"image_query": [{"q": "original"}]},
            filters={"allowed_domains": ["example.com"], "blocked_domains": ["blocked.net"]}))
    assert len(calls) == 1 and store.db.execute("SELECT count(*) FROM web_sources").fetchone()[0] == 0
    outcome, proof = store.db.execute("SELECT outcome,evidence FROM web_receipts").fetchone()
    assert outcome == "failed" and json.loads(proof)["upstream"]["http_status"] == 200

async def test_unknown_dimensions_not_fabricated(image_backend):
    backend, _, _, _, record, _ = image_backend
    record.pop("imageWidth"); record.pop("imageHeight")
    result = await backend.handle(request({"image_query": [{"q": "original"}]}))
    assert not {"width", "height", "content_size", "license"} & result["results"][0]["image_result"].keys()

async def test_zero_limit_and_empty_domain_policy_no_request(image_backend):
    backend, _, images, calls, _, _ = image_backend
    result = await backend.handle(request({"image_query": [{"q": "original"}]}, image_settings={"max_results": 0}))
    assert result["results"] == [] and calls == []
    documents, proof = await images.search_native({"q": "original"}, wb.DomainPolicy((("example.com",),), ("example.com",)),
                                                   {"external_web_access": False}, max_results=3)
    assert documents == [] and proof["request_performed"] is False and calls == []

@pytest.mark.parametrize("data", [{"web": []}, {"images": {}}, {"images": [], "web": []}])
async def test_wrong_result_group_is_not_success(image_backend, data):
    _, _, images, _, _, _ = image_backend
    async def post(*args): return {"success": True, "data": data}, {"http_status": 200}
    images.service._post = post
    with pytest.raises(wb.WebBackendError) as failure:
        await images.search_native({"q": "original"}, wb.DomainPolicy(), {})
    assert failure.value.code == "image_search_protocol_error"

async def test_service_failure_does_not_switch_provider(image_backend):
    _, _, images, _, _, _ = image_backend
    error, calls = wb.WebBackendError("search_http_error", "service rejected", 429), []
    async def post(*args):
        calls.append(args); raise error
    images.service._post = post
    with pytest.raises(wb.WebBackendError) as failure:
        await images.search_native({"q": "original"}, wb.DomainPolicy(), {})
    assert failure.value is error and len(calls) == 1

async def test_image_partial_domain_policy_preflights_entire_native_batch(image_backend):
    backend, store, _, calls, _, fetcher = image_backend
    payload = request({"search_query": [{"q": "valid text", "domains": ["safe.example.com"]}],
                       "image_query": [{"q": "unrepresentable image policy"}]},
                      filters={"allowed_domains": ["example.com"], "blocked_domains": ["blocked.example.com"]})
    with pytest.raises(wb.WebBackendError) as failure:
        await backend.handle(payload)
    assert failure.value.code == "unsupported_domain_combination" and failure.value.status == 422
    assert calls == [] and fetcher.calls == 0
    assert store.db.execute("SELECT count(*) FROM web_receipts").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM web_sources").fetchone()[0] == 0
