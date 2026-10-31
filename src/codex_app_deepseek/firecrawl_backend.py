"""Explicit Firecrawl data-service adapter; no model, tools, or agent loop.

The default OpenRouter search route is unchanged. This optional route sends the
original query to the official key-optional API, and never changes backends on
failure. Cached retrieval uses the service's no-outbound lockdown contract.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import json
import re
import ssl
from urllib.parse import urlsplit

import aiohttp
from codex_deepseek.windows_asyncio import install_proactor_cleanup_backport

from .web_backend import (
    DomainPolicy, PublicDocumentFetcher, PublicResolver, RetrievedDocument,
    WebBackendError, _single_attempt, checked_url, fail, integer, strict_json,
    utc_now, domain_list, matches,
)

API_ORIGIN = "https://api.firecrawl.dev"
CONTEXT_RESULTS = {"low": 3, "medium": 5, "high": 10}


def web_mode(settings):
    value = settings.get("external_web_access", True)
    if value is True or value == "live":
        return "live"
    if value is False or value == "cached":
        return "cached"
    if value == "indexed":
        return "indexed"
    fail("invalid_web_mode", "external_web_access must be a native boolean or mode")


def location_bias(location):
    """Location is a bias, never a fence or an addition to the user's query."""
    if not location:
        return None
    if location.get("timezone"):
        fail("unsupported_timezone", "Firecrawl has no verified timezone search field", 422)
    country = location.get("country")
    if country and not re.fullmatch(r"[A-Za-z]{2}", country):
        fail("unsupported_country", "Country must be an explicit two-letter ISO country code", 422)
    parts = [location.get(key) for key in ("city", "region", "country")]
    parts = [part for part in parts if part]
    if not parts:
        return None
    if any("," in part or any(ord(c) < 32 for c in part) for part in parts):
        fail("ambiguous_location", "Location components must not contain separators or controls", 422)
    return ",".join(parts)


def recency_filter(days, now):
    """Map native day counts to the documented calendar-date range, explicitly."""
    if days is None:
        return None, None
    integer(days, "recency")
    if now.tzinfo is None:
        fail("clock_error", "Search clock must be timezone-aware", 500)
    end = now.astimezone(timezone.utc).date()
    try:
        start = end - timedelta(days=days)
    except (OverflowError, ValueError):
        fail("unsupported_recency", "Recency exceeds the backend's calendar range", 422)
    return (f"cdr:1,cd_min:{start:%m/%d/%Y},cd_max:{end:%m/%d/%Y}",
            {"kind": "UTC_calendar_date_range", "start_inclusive": start.isoformat(),
             "end_inclusive": end.isoformat(), "rolling_timestamp_filter": False})


def domain_filters(policy):
    """Reduce hostname subtrees only when native acceptance is unchanged.

    Firecrawl accepts includeDomains or excludeDomains, not both. A blocked
    ancestor removes an allowed subtree entirely; a disjoint block is already
    implied by the allowlist. A block inside a remaining allowed subtree still
    cannot be represented, so reject it rather than post-filter a capped page.
    None means the original policy admits no hostname and needs no request.
    """
    allowed = policy.upstream_allowed()
    blocked = policy.blocked
    if not allowed:
        return {"excludeDomains": list(blocked)} if blocked else {}
    remaining = [host for host in allowed
                 if not any(matches(host, parent) for parent in blocked)]
    if not remaining:
        return None
    if any(matches(child, parent) for parent in remaining for child in blocked):
        fail("unsupported_domain_combination",
             "Firecrawl cannot exclude a partial allowed domain subtree", 422)
    return {"includeDomains": remaining}


@contextmanager
def service_evidence(proof):
    """Keep the successful HTTP receipt on downstream semantic failures too."""
    try:
        yield
    except WebBackendError as error:
        existing = getattr(error, "evidence", None)
        error.evidence = {**proof, **(existing if isinstance(existing, dict) else {})}
        raise


class FirecrawlBackend:
    """One explicit keyless API route, plus existing guarded local retrieval.

    Search uses no scrapeOptions and no highlights. Indexed live reads must first
    prove each exact URL in the search index, including every redirect target.
    Unknown membership is a visible error, never permission to fetch.
    """
    backend_name = "firecrawl"

    def __init__(self, *, ssl_context=None, fetcher=None, clock=None,
                 timeout=60, max_bytes=8 * 1024 * 1024):
        self.ssl_context = ssl_context or ssl.create_default_context()
        self.fetcher = fetcher or PublicDocumentFetcher(ssl_context=self.ssl_context)
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.timeout, self.max_bytes = timeout, max_bytes

    def validate(self, operations, settings, policy):
        mode = web_mode(settings)
        queries = [(name, value) for name, value in operations
                   if name in {"search_query", "image_query"}]
        if queries:
            location_bias(settings.get("user_location"))
        for name, value in queries:
            if name == "image_query":
                if value.get("recency") is not None or mode != "live" or settings.get("user_location"):
                    fail("unsupported_image_policy", "The configured image index cannot express this search policy", 422)
                continue
            recency_filter(value.get("recency"), self.clock())
            selected = policy.restrict(domain_list(value.get("domains", [])))
            domain_filters(selected)
        # Cached PDF bytes/pixels are only available if the native source store
        # already retains them; the scrape cache is not an original-media store.
        return mode

    async def _post(self, endpoint, payload):
        if endpoint not in {"/v2/search", "/v2/scrape"}:
            raise ValueError("Unrecognized fixed Firecrawl endpoint")
        install_proactor_cleanup_backport()
        connector = aiohttp.TCPConnector(
            ssl=self.ssl_context, resolver=PublicResolver(), use_dns_cache=False)
        request_hash = sha256(json.dumps(payload, sort_keys=True, separators=(",", ":"),
                                        ensure_ascii=False).encode()).hexdigest()
        proof = {"backend": "firecrawl", "endpoint": endpoint,
                 "request_sha256": request_hash, "started_at": utc_now(),
                 "model_request": False, "automatic_retries": 0}
        try:
            async with aiohttp.ClientSession(
                    connector=connector, trust_env=False,
                    cookie_jar=aiohttp.DummyCookieJar(),
                    middlewares=(_single_attempt,),
                    timeout=aiohttp.ClientTimeout(total=self.timeout)) as client:
                async with client.post(
                        API_ORIGIN + endpoint, json=payload, allow_redirects=False,
                        headers={"Accept": "application/json",
                                 "User-Agent": "CodexAppDeepSeekGateway/0.1 (+explicit-firecrawl)"}) as reply:
                    proof.update(http_status=reply.status, response_complete=False, response_bytes=0)
                    chunks, size, digest = [], 0, sha256()
                    async for chunk in reply.content.iter_chunked(65536):
                        size += len(chunk)
                        digest.update(chunk)
                        proof.update(response_bytes=size, received_prefix_sha256=digest.hexdigest())
                        if size > self.max_bytes:
                            fail("search_response_too_large", "Search service response exceeds its byte budget", 413)
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                    proof.update(response_complete=True, response_sha256=digest.hexdigest(),
                                 finished_at=utc_now())
                    if reply.status != 200:
                        if reply.status == 404:
                            try:
                                rejected = strict_json(raw)
                            except (ValueError, WebBackendError):
                                rejected = None
                            if isinstance(rejected, dict) and rejected.get("code") == "SCRAPE_LOCKDOWN_CACHE_MISS":
                                fail("cache_miss", "No cached document exists; no live fallback was attempted", 404)
                        fail("search_http_error", f"Firecrawl returned HTTP {reply.status}; no fallback was attempted",
                             reply.status if reply.status in {401, 403, 429} else 502)
                    data = strict_json(raw)
                    if not isinstance(data, dict) or data.get("success") is not True:
                        fail("search_protocol_error", "Firecrawl did not return a successful data response", 502)
                    if data.get("warning") or data.get("warnings"):
                        fail("search_policy_warning", "Search service reported warnings; semantic loss was not accepted", 502)
                    request_id = data.get("id")
                    if request_id is not None and (
                            not isinstance(request_id, str) or len(request_id) > 256
                            or any(ord(c) < 32 for c in request_id)):
                        fail("search_protocol_error", "Search service request identity is invalid", 502)
                    proof["service_request_id"] = request_id
                    return data, proof
        except asyncio.CancelledError:
            raise
        except WebBackendError as error:
            proof["finished_at"] = utc_now()
            error.evidence = proof
            raise
        except (ValueError, aiohttp.ClientError, TimeoutError, OSError) as cause:
            code = "search_protocol_error" if isinstance(cause, ValueError) else "search_network_error"
            error = WebBackendError(code, "Search data connection or JSON protocol failed; no fallback was attempted", 502)
            proof["finished_at"] = utc_now()
            error.evidence = proof
            raise error from None

    async def search_native(self, query, policy, context_size, *, recency=None,
                            location=None, mode="live"):
        bias = location_bias(location)
        if context_size not in CONTEXT_RESULTS:
            fail("invalid_request", "Invalid search_context_size")
        if mode not in {"live", "cached", "indexed"}:
            fail("invalid_web_mode", "Invalid explicitly selected web mode")
        limit = CONTEXT_RESULTS[context_size]
        tbs, date_proof = recency_filter(recency, self.clock())
        payload = {"query": query, "limit": limit, "sources": ["web"],
                   "highlights": False}
        filters = domain_filters(policy)
        if filters is None:
            return [], {"backend": self.backend_name, "request_performed": False,
                        "local_policy_empty": True, "route_verified": False,
                        "route_policy": "explicit_firecrawl_only", "automatic_retries": 0,
                        "date_filter": date_proof, "location_bias": bias,
                        "country_bias": location["country"].upper() if location and location.get("country") else None,
                        "requested_context_size": context_size, "result_limit": limit,
                        "external_web_access": mode, "target_page_fetch_requested": False}
        payload.update(filters)
        if bias:
            payload["location"] = bias
        if location and location.get("country"):
            payload["country"] = location["country"].upper()
        if tbs:
            payload["tbs"] = tbs
        data, proof = await self._post("/v2/search", payload)
        with service_evidence(proof):
            group = data.get("data")
            records = group.get("web") if isinstance(group, dict) else None
            if (not isinstance(records, list) or len(records) > limit
                    or set(group) - {"web"}):
                fail("search_protocol_error", "Search service returned an unexpected result group", 502)
            documents = []
            for record in records:
                if not isinstance(record, dict):
                    fail("search_protocol_error", "Search service returned a malformed source", 502)
                url = checked_url(record.get("url"), policy)
                title, excerpt = record.get("title"), record.get("description")
                if not isinstance(title, str) or not isinstance(excerpt, str) or len(excerpt) > 100_000:
                    fail("search_protocol_error", "Search source metadata is invalid", 502)
                documents.append({
                    "url": url, "requested_url": url, "title": title,
                    "content_type": "text/plain", "retrieved_at": utc_now(),
                    "source_sha256": sha256(excerpt.encode()).hexdigest(),
                    "lines": excerpt.splitlines(), "links": [], "excerpt": True, "page_count": None,
                    "provenance": {**proof, "content_origin": "search_index_excerpt",
                                   "external_web_access": mode, "date_filter": date_proof,
                                   "location_bias": bias}})
            proof.update(route_verified=True, data_service_identity="firecrawl", route_policy="explicit_firecrawl_only",
                         date_filter=date_proof, location_bias=bias,
                         country_bias=payload.get("country"), requested_context_size=context_size,
                         result_limit=limit, external_web_access=mode,
                         target_page_fetch_requested=False)
            return documents, proof

    async def index_check(self, url, policy):
        exact = checked_url(url, policy)
        narrowed = policy.restrict((urlsplit(exact).hostname,))
        documents, proof = await self.search_native(exact, narrowed, "low", mode="cached")
        if not any(item["url"] == exact for item in documents):
            error = WebBackendError("index_membership_unverified", "Exact URL membership was not established; no target request was made", 404)
            error.evidence = proof
            raise error
        return proof

    async def fetch_native(self, url, policy, mode):
        initial = checked_url(url, policy)
        if mode == "cached":
            data, proof = await self._post("/v2/scrape", {
                "url": initial, "formats": ["html"], "lockdown": True,
                "skipTlsVerification": False})
            with service_evidence(proof):
                body = data.get("data")
                if not isinstance(body, dict) or not isinstance(body.get("html"), str):
                    fail("cached_document_unavailable", "Cache did not return the requested HTML representation", 502)
                metadata = body.get("metadata")
                if not isinstance(metadata, dict) or metadata.get("statusCode") != 200:
                    fail("cached_document_invalid", "Cached source metadata has no successful document status", 502)
                if body.get("warning") or metadata.get("error"):
                    fail("cached_document_invalid", "Cached source reported a warning or error", 502)
                if metadata.get("sourceURL") is not None and checked_url(metadata["sourceURL"], policy) != initial:
                    fail("cached_document_identity_unverified", "Cache metadata names a different requested URL", 502)
                if not metadata.get("url"):
                    fail("cached_document_identity_unverified", "Cache metadata has no reported final URL", 502)
                final = checked_url(metadata["url"], policy)
                raw = body["html"].encode()
                if len(raw) > self.max_bytes:
                    fail("document_too_large", "Cached document exceeds its byte budget", 413)
                proof.update(external_web_access="cached", lockdown=True, target_page_fetch_requested=False,
                             document_backend="firecrawl_cache", data_service_identity="firecrawl",
                             reported_final_url=final, redirect_chain_verified=False,
                             request_and_reported_final_domain_checked=True,
                             cache_state=metadata.get("cacheState"), cached_at=metadata.get("cachedAt"))
                return RetrievedDocument(initial, final, "text/html", raw, utc_now()), proof

        if mode == "indexed":
            checks = []
            async def check_each(target, target_policy):
                proof = await self.index_check(target, target_policy)
                checks.append({"url": target, **proof})
            document = await self.fetcher.fetch(initial, policy, index_check=check_each)
            return document, {"backend": "public_http", "external_web_access": "indexed",
                              "index_membership_checks": checks}
        if mode != "live":
            fail("invalid_web_mode", "Invalid explicitly selected web mode")
        return await self.fetcher.fetch(initial, policy), {"backend": "public_http", "external_web_access": "live"}
