"""Read-only OpenRouter generation metadata; never retrieves prompts or starts inference."""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import hashlib
import json
import math
import re
import ssl
from typing import Callable, Mapping, Any
from urllib.parse import urlsplit

import aiohttp
from codex_deepseek.protocol import MODEL

OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"
_MAX_BYTES = 1024 * 1024
_FIELDS = frozenset({
    "id", "upstream_id", "request_id", "model", "provider_name", "api_type", "created_at",
    "cancelled", "finish_reason", "native_finish_reason", "streamed", "latency",
    "generation_time", "tokens_prompt", "tokens_completion", "native_tokens_prompt",
    "native_tokens_completion", "native_tokens_cached", "native_tokens_reasoning",
    "num_search_results", "total_cost",
})


class _MetadataTransportError(RuntimeError):
    pass


async def _single_transport_attempt(request, handler):
    # Public aiohttp client middleware prevents its implicit idempotent GET retry.
    # Only the explicit generation-404 loop below may repeat a metadata lookup.
    try:
        return await handler(request)
    except (aiohttp.ClientOSError, aiohttp.ServerDisconnectedError) as error:
        raise _MetadataTransportError(type(error).__name__) from error


def _json(raw):
    def reject(_):
        raise ValueError("Non-finite JSON number")
    def finite(value):
        number = float(value)
        if not math.isfinite(number):
            reject(value)
        return number
    def pairs(items):
        result = {}
        for name, value in items:
            if name in result:
                raise ValueError('Duplicate metadata JSON field')
            result[name] = value
        return result
    return json.loads(raw, parse_constant=reject, parse_float=finite, object_pairs_hook=pairs)


def _redact(value, secret):
    if isinstance(value, str):
        return value.replace(secret, "[REDACTED]") if secret else value
    if isinstance(value, dict):
        return {name: _redact(item, secret) for name, item in value.items()}
    if isinstance(value, list):
        return [_redact(item, secret) for item in value]
    return value


def _generation_fields(data):
    result = {"model": None, "provider_name": None}
    for name in _FIELDS:
        if name not in data:
            continue
        value = data[name]
        if value is not None and not isinstance(value, (str, int, float, bool)):
            raise ValueError("Generation metadata field is not scalar")
        if isinstance(value, str) and len(value) > 1024:
            raise ValueError("Generation metadata scalar exceeds its budget")
        result[name] = value
    for name in ("model", "provider_name"):
        if result[name] is not None and (not isinstance(result[name], str) or not result[name]):
            raise ValueError("Generation identity is not a nonempty string")
    return result


async def read_generation_evidence(
    receipt: Mapping[str, Any],
    credential_provider: Callable[[], str],
    *,
    proxy_url: str | None = None,
    ssl_context: ssl.SSLContext | None = None,
    max_attempts: int = 4,
    poll_delay: float = 0.5,
    timeout: float = 10,
    api_base: str = OPENROUTER_API_BASE,
    test_upstream: bool = False,
) -> dict:
    """GET metadata only. A found record is not itself a successful model/provider verdict.

    Only generation 404s are polled, at most max_attempts. Other errors are returned
    without retry. provider_name is observed metadata, never inferred from provider.only.
    Canonical aliases require an exact id/canonical_slug pair from the model endpoint.
    Returned dictionaries are safe to persist: no raw responses, prompts, tokens,
    provider_responses, external-user data, or credential-bearing URLs are retained.
    """
    if not isinstance(max_attempts, int) or not 1 <= max_attempts <= 5:
        raise ValueError("max_attempts must be between 1 and 5")
    if not 0 <= poll_delay <= 5 or not 0 < timeout <= 30:
        raise ValueError("Polling delay or request timeout is out of bounds")
    api_base = api_base.rstrip("/")
    if api_base != OPENROUTER_API_BASE:
        parsed = urlsplit(api_base)
        if (not test_upstream or parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "::1"}
                or parsed.path != "/api/v1" or parsed.query or parsed.fragment
                or parsed.username or parsed.password):
            raise ValueError("Production metadata endpoint must be OpenRouter")
    result = {
        "schema_version": 1,
        "read_at": datetime.now(timezone.utc).isoformat(),
        "receipt_id": receipt.get("id"),
        "response_id": receipt.get("response_id"),
        "requested": {
            "model_alias": receipt.get("requested_model"),
            "upstream_model": receipt.get("requested_upstream_model"),
            "provider": receipt.get("requested_provider"),
        },
        "lookup_status": "not_started",
        "generation_lookup": {"endpoint": api_base + "/generation", "attempts": []},
        "observed": None,
        "model_identity": {"status": "not_checked", "matches_requested": None},
        "inference_requests": 0,
    }
    generation_id = receipt.get("response_id")
    if not isinstance(generation_id, str) or not re.fullmatch(r"(?:gen|resp)[-_][A-Za-z0-9_-]{1,220}", generation_id):
        result["lookup_status"] = "missing_or_invalid_response_id"
        result["response_id"] = None
        return result
    if receipt.get("requested_upstream_model") != MODEL:
        result["lookup_status"] = "unsupported_requested_model"
        return result
    try:
        key = credential_provider()
        if not isinstance(key, str) or not key or any(char.isspace() for char in key):
            raise ValueError("Unusable credential")
    except Exception as error:
        result["lookup_status"] = "credential_error"
        result["error"] = {"kind": "credential_error", "exception_type": type(error).__name__}
        return result

    async def fetch(client, path, *, authenticated, params=None):
        try:
            async with client.get(
                api_base + path, params=params, proxy=proxy_url, allow_redirects=False,
                headers={"Authorization": "Bearer " + key} if authenticated else {},
            ) as response:
                chunks, size = [], 0
                async for chunk in response.content.iter_chunked(65536):
                    size += len(chunk)
                    if size > _MAX_BYTES:
                        return response.status, None, {"kind": "response_too_large"}, None
                    chunks.append(chunk)
                raw = b"".join(chunks)
                try:
                    document = _json(raw)
                except (ValueError, UnicodeError):
                    document = None
                if response.status != 200:
                    code = None
                    if isinstance(document, dict) and isinstance(document.get("error"), dict):
                        candidate = document["error"].get("code")
                        if isinstance(candidate, (str, int)) and re.fullmatch(r"[A-Za-z0-9_.-]{1,128}", str(candidate)):
                            code = str(candidate)
                    # Error text may include upstream prompt data. Preserve status
                    # and machine code, never copy the raw body or message.
                    safe_headers = {}
                    advice = response.headers.get('Retry-After')
                    if isinstance(advice,str) and len(advice)<=128 and re.fullmatch(r'[A-Za-z0-9 ,:.-]+',advice) and key not in advice:
                        safe_headers['retry-after'] = advice
                    return response.status, None, {"kind": "http_error", "upstream_code": code, "headers": safe_headers}, None
                if not isinstance(document, dict) or not isinstance(document.get("data"), dict):
                    return response.status, None, {"kind": "invalid_metadata_envelope"}, None
                # This digest is only used for public model metadata. Authenticated
                # generation payloads are filtered rather than stored or hashed.
                digest = hashlib.sha256(raw).hexdigest() if not authenticated else None
                return response.status, document["data"], None, digest
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # Exception strings can contain proxy credentials; retain only the type.
            return None, None, {"kind": "transport_error", "exception_type": type(error).__name__}, None

    connector = aiohttp.TCPConnector(ssl=ssl_context or ssl.create_default_context())
    async with aiohttp.ClientSession(
        connector=connector, timeout=aiohttp.ClientTimeout(total=timeout),
        cookie_jar=aiohttp.DummyCookieJar(), trust_env=False, middlewares=(_single_transport_attempt,),
    ) as client:
        generation = None
        for attempt in range(1, max_attempts + 1):
            status, generation, error, _ = await fetch(
                client, "/generation", authenticated=True, params={"id": generation_id})
            record = {"attempt": attempt, "http_status": status}
            if error:
                record["error"] = error
            result["generation_lookup"]["attempts"].append(record)
            if status == 404 and attempt < max_attempts:
                await asyncio.sleep(poll_delay)
                continue
            break
        if generation is None:
            result["lookup_status"] = "not_found" if status == 404 else (error or {}).get("kind", "metadata_error")
            return _redact(result, key)
        if generation.get("id") != generation_id:
            result["lookup_status"] = "generation_id_mismatch"
            return _redact(result, key)
        try:
            result["observed"] = _generation_fields(generation)
        except ValueError:
            result["lookup_status"] = "invalid_generation_metadata"
            return _redact(result, key)
        result["lookup_status"] = "found"
        observed_model = result["observed"]["model"]
        if observed_model is None:
            result["model_identity"] = {"status": "observed_model_missing", "matches_requested": None}
            return _redact(result, key)

        metadata_path = "/model/" + MODEL
        status, metadata, error, digest = await fetch(client, metadata_path, authenticated=False)
        proof = {"endpoint": api_base + metadata_path, "http_status": status}
        if error:
            proof["error"] = error
        if metadata is not None and (
            any(metadata.get(name) is not None and (
                not isinstance(metadata.get(name), str) or len(metadata[name]) > 512
            ) for name in ("id", "canonical_slug"))
            or (metadata.get("created") is not None and not isinstance(metadata["created"], int))
        ):
            proof["error"] = {"kind": "invalid_model_metadata"}
            metadata = None
        if metadata is not None:
            proof.update(raw_sha256=digest, metadata={
                name: metadata.get(name) for name in ("id", "canonical_slug", "created")})
        identity = {"status": "metadata_unavailable", "matches_requested": None, "proof": proof}
        if observed_model == MODEL:
            identity.update(status="exact_id", matches_requested=True)
        elif metadata is not None:
            canonical = metadata.get("canonical_slug")
            if metadata.get("id") != MODEL or not isinstance(canonical, str) or not canonical:
                identity.update(status="invalid_alias_metadata")
            elif observed_model == canonical:
                identity.update(status="verified_canonical_alias", matches_requested=True)
            else:
                identity.update(status="model_mismatch", matches_requested=False)
        result["model_identity"] = identity
        return _redact(result, key)
