"""Native alpha/search service backend, not an agent or local tool scheduler.

Only server-tool source records are accepted; model-written prose is never web
content. Source references are immutable, durable and scoped to native sessions.
Unsupported native operations/policies fail before any outbound work.
"""
from __future__ import annotations
from .paths import _is_link_or_reparse, _sqlite_filename

import asyncio
import base64
from bisect import bisect_right
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from hashlib import sha256
import io
import html
import ipaddress
import json
import math
import os
from pathlib import Path
import re
import socket
import sqlite3
import ssl
import textwrap
import tempfile
from typing import Callable
from urllib.parse import quote, urljoin, urlsplit, urlunsplit
import uuid

import aiohttp
from codex_deepseek.windows_asyncio import install_proactor_cleanup_backport
from aiohttp.abc import AbstractResolver
from bs4 import BeautifulSoup

from codex_deepseek.protocol import MODEL
from .upstream_evidence import read_generation_evidence


class WebBackendError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def fail(code, message, status=400):
    raise WebBackendError(code, message, status)


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def encoded(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def strict_json(raw):
    def pairs(items):
        result = {}
        for name, value in items:
            if name in result:
                fail("invalid_provider_json", "Duplicate provider JSON fields", 502)
            result[name] = value
        return result
    def constant(value):
        fail("invalid_provider_json", "Non-finite provider JSON values", 502)
    def number(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            constant(value)
        return parsed
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant, parse_float=number)


def usage_evidence(value):
    if not isinstance(value, dict):
        return {}
    result = {}
    for name in ("input_tokens", "output_tokens", "total_tokens", "cost"):
        item = value.get(name)
        if type(item) in {int, float} and math.isfinite(item) and item >= 0:
            result[name] = item
    details = value.get("server_tool_use_details")
    if isinstance(details, dict):
        result["server_tool_use_details"] = {name: details[name] for name in
            ("web_search_requests", "tool_calls_requested", "tool_calls_executed")
            if type(details.get(name)) is int and details[name] >= 0}
    return result


def object_fields(value, allowed, name, required=()):
    if not isinstance(value, dict) or set(value) - set(allowed) or set(required) - set(value):
        fail("invalid_request", f"Invalid or unknown {name} fields")
    return value


def string(value, name, limit=8192):
    if not isinstance(value, str) or not value or len(value) > limit or "\x00" in value:
        fail("invalid_request", f"Invalid {name}")
    return value


def integer(value, name, minimum=0, maximum=1_000_000):
    if type(value) is not int or not minimum <= value <= maximum:
        fail("invalid_request", f"Invalid {name}")
    return value


def domain(value):
    string(value, "domain", 253)
    if any(x in value for x in (":", "/", "*", "@", "%", "\\")) or value.endswith("."):
        fail("invalid_domain", "Domain filters must contain literal DNS host names")
    try:
        name = value.encode("idna").decode("ascii").lower()
    except UnicodeError:
        fail("invalid_domain", "Invalid domain name")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", name) or ".." in name:
        fail("invalid_domain", "Invalid domain name")
    return name


def domain_list(value):
    if not isinstance(value, list) or len(value) > 100:
        fail("invalid_request", "Invalid domain list")
    return tuple(domain(x) for x in value)


def matches(host, parent):
    return host == parent or host.endswith("." + parent)


@dataclass(frozen=True)
class DomainPolicy:
    allowed_groups: tuple[tuple[str, ...], ...] = ()
    blocked: tuple[str, ...] = ()

    def accepts(self, host):
        return (all(any(matches(host, d) for d in group) for group in self.allowed_groups)
                and not any(matches(host, d) for d in self.blocked))

    def restrict(self, domains):
        return DomainPolicy(self.allowed_groups + ((domains,) if domains else ()), self.blocked)

    def upstream_allowed(self):
        if not self.allowed_groups:
            return []
        result = set(self.allowed_groups[0])
        for group in self.allowed_groups[1:]:
            result = {a if matches(a, b) else b for a in result for b in group
                      if matches(a, b) or matches(b, a)}
        if not result:
            fail("domain_intersection_empty", "Query domains and policy have no intersection", 403)
        return sorted(result)


def checked_url(value, policy=DomainPolicy()):
    string(value, "URL", 16384)
    if any(ord(c) < 33 for c in value) or "\\" in value:
        fail("unsafe_url", "URL contains prohibited characters", 403)
    try:
        p = urlsplit(value)
        host = p.hostname
        port = p.port
    except ValueError:
        fail("unsafe_url", "Malformed public URL", 403)
    if p.scheme not in {"http", "https"} or not host or p.username is not None or p.password is not None:
        fail("unsafe_url", "Only public HTTP(S) URLs without embedded credentials are accepted", 403)
    try:
        host = host.encode("idna").decode("ascii").lower().rstrip(".")
    except UnicodeError:
        fail("unsafe_url", "Invalid URL host", 403)
    if port not in {None, 80, 443}:
        fail("unsafe_url", "Nonstandard web ports are not enabled", 403)
    if host in {"localhost", "localhost.localdomain"} or host.endswith((".localhost", ".local", ".internal")):
        fail("private_target", "Local/private web targets are prohibited", 403)
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if "." not in host or not re.fullmatch(r"[a-z0-9.-]+", host):
            fail("unsafe_url", "Invalid public host", 403)
    else:
        if not address.is_global:
            fail("private_target", "Local/private web targets are prohibited", 403)
    if not policy.accepts(host):
        fail("domain_denied", "URL violates the native domain policy", 403)
    return urlunsplit((p.scheme, p.netloc, p.path or "/", p.query, ""))


class PublicResolver(AbstractResolver):
    """Check the actual connection addresses, including mixed public/private DNS."""
    def __init__(self):
        self.resolver = aiohttp.resolver.ThreadedResolver()

    async def resolve(self, host, port=0, family=socket.AF_INET):
        records = await self.resolver.resolve(host, port, family)
        if not records or any(not ipaddress.ip_address(record["host"]).is_global for record in records):
            fail("private_target", "DNS resolved to a prohibited address", 403)
        return records

    async def close(self):
        await self.resolver.close()


async def _single_attempt(request, handler):
    try:
        return await handler(request)
    except (aiohttp.ClientOSError, aiohttp.ServerDisconnectedError):
        fail("network_error", "Web connection failed; no automatic retry was attempted", 502)


@dataclass(frozen=True)
class RetrievedDocument:
    requested_url: str
    final_url: str
    content_type: str
    raw: bytes
    retrieved_at: str
    redirects: tuple[str, ...] = ()


class PublicDocumentFetcher:
    def __init__(self, *, ssl_context=None, max_bytes=8 * 1024 * 1024, timeout=35):
        self.ssl_context = ssl_context or ssl.create_default_context()
        self.max_bytes, self.timeout = max_bytes, timeout

    async def fetch(self, url, policy, *, index_check=None):
        initial = checked_url(url, policy)
        current, redirects = initial, []
        install_proactor_cleanup_backport()
        connector = aiohttp.TCPConnector(ssl=self.ssl_context, resolver=PublicResolver(), use_dns_cache=False)
        try:
            async with aiohttp.ClientSession(connector=connector, trust_env=False,
                    cookie_jar=aiohttp.DummyCookieJar(), middlewares=(_single_attempt,),
                    timeout=aiohttp.ClientTimeout(total=self.timeout)) as client:
                for _ in range(6):
                    if index_check is not None:
                        await index_check(current, policy)
                    async with client.get(current, allow_redirects=False,
                            headers={"User-Agent": "CodexAppDeepSeekGateway/0.1 (+public-document-fetch)",
                                     "Accept": "text/html,application/xhtml+xml,text/plain,application/pdf"}) as reply:
                        if reply.status in {301, 302, 303, 307, 308}:
                            target = reply.headers.get("Location")
                            if not target:
                                fail("invalid_redirect", "Redirect has no location", 502)
                            current = checked_url(urljoin(current, target), policy)
                            redirects.append(current)
                            continue
                        if reply.status != 200:
                            fail("document_http_error", f"Document service returned HTTP {reply.status}", 502)
                        kind = reply.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
                        if kind not in {"text/html", "application/xhtml+xml", "text/plain", "application/pdf"}:
                            fail("unsupported_document", "Document media type is not supported", 415)
                        pieces, size = [], 0
                        async for chunk in reply.content.iter_chunked(65536):
                            size += len(chunk)
                            if size > self.max_bytes:
                                fail("document_too_large", "Document exceeds the configured byte limit", 413)
                            pieces.append(chunk)
                        return RetrievedDocument(initial, current, kind, b"".join(pieces), utc_now(), tuple(redirects))
                fail("redirect_limit", "Document redirect limit exceeded", 502)
        except asyncio.CancelledError:
            raise
        except WebBackendError:
            raise
        except (aiohttp.ClientError, TimeoutError, OSError):
            fail("document_network_error", "Document retrieval failed; no automatic retry was attempted", 502)


def extract_document(document):
    links, pages = [], None
    if document.content_type == "application/pdf":
        fail("pdf_requires_bounded_worker", "PDF parsing must use the bounded document worker", 503)
    elif document.content_type == "text/plain":
        try:
            text = document.raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            fail("unsupported_text_encoding", "Plain text is not valid UTF-8", 415)
        title = document.final_url
    else:
        soup = BeautifulSoup(document.raw, "html.parser")
        title = soup.title.get_text(" ", strip=True) if soup.title else document.final_url
        base = document.final_url
        base_tag = soup.find("base", href=True)
        if base_tag:
            base = urljoin(base, base_tag["href"])
        for tag in soup.find_all(["script", "style", "noscript", "template", "head"]):
            tag.decompose()
        for anchor in soup.find_all("a", href=True):
            target = urljoin(base, anchor["href"])
            if urlsplit(target).scheme not in {"https", "http"}:
                continue
            label = anchor.get_text(" ", strip=True)
            link_id = len(links)
            links.append({"id": link_id, "url": target, "text": label})
            anchor.replace_with(f"{label or target} [{link_id}]")
        text = soup.get_text("\n", strip=True)
    lines = []
    for paragraph in text.splitlines():
        paragraph = " ".join(paragraph.split())
        if paragraph:
            lines.extend(textwrap.wrap(paragraph, width=140, break_long_words=True, break_on_hyphens=False))
    return {"url": document.final_url, "requested_url": document.requested_url, "title": title,
            "content_type": document.content_type, "retrieved_at": document.retrieved_at,
            "source_sha256": sha256(document.raw).hexdigest(), "lines": lines,
            "links": links, "excerpt": False, "page_count": pages,
            "provenance": {"backend": "public_http", "redirects": list(document.redirects)}}


class WebSourceStore:
    """Private bounded SQLite store; raw documents are never public diagnostics."""
    def __init__(self, path, *, max_sources=2000, max_bytes=128 * 1024 * 1024):
        self.path = Path(path)
        if any(_is_link_or_reparse(p)
               for p in (self.path, *self.path.parents)):
            raise ValueError("Web source store must not traverse a symlink or junction")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.max_sources, self.max_bytes = max_sources, max_bytes
        self.db = sqlite3.connect(_sqlite_filename(self.path))
        try:
            self.db.executescript("""
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS web_sources (
                sequence INTEGER PRIMARY KEY AUTOINCREMENT, session TEXT NOT NULL,
                ref TEXT UNIQUE, body TEXT NOT NULL, raw BLOB, bytes INTEGER NOT NULL);
              PRAGMA foreign_keys=ON;
              CREATE TABLE IF NOT EXISTS web_native_threads (
                thread TEXT PRIMARY KEY, session TEXT NOT NULL,
                fork_parent TEXT, fork_ordinal TEXT, inherited INTEGER NOT NULL);
              CREATE TABLE IF NOT EXISTS web_source_grants (
                session TEXT NOT NULL, sequence INTEGER NOT NULL,
                PRIMARY KEY(session, sequence),
                FOREIGN KEY(sequence) REFERENCES web_sources(sequence));
              CREATE TABLE IF NOT EXISTS web_receipts (
                id TEXT PRIMARY KEY, session TEXT NOT NULL, operation TEXT NOT NULL,
                request_sha256 TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT,
                outcome TEXT NOT NULL, evidence TEXT NOT NULL);
            """)
        except BaseException:
            self.db.close()
            raise

    def close(self):
        self.db.close()

    def put(self, session, source, raw=b"", *, kind="view"):
        body = encoded(source)
        size = len(body.encode()) + len(raw)
        with self.db:
            count, used = self.db.execute("SELECT count(*),coalesce(sum(bytes),0) FROM web_sources").fetchone()
            if count >= self.max_sources or used + size > self.max_bytes:
                fail("source_store_full", "Web source store capacity reached; retained references were not evicted", 507)
            cur = self.db.execute("INSERT INTO web_sources(session,body,raw,bytes) VALUES(?,?,?,?)",
                                  (session, body, raw, size))
            ref = f"turn{cur.lastrowid}{kind}0"
            self.db.execute("UPDATE web_sources SET ref=? WHERE sequence=?", (ref, cur.lastrowid))
        return {**source, "ref_id": ref}

    def bind_native_namespace(self, session, metadata):
        # This authenticated native header authorizes a document-cache namespace,
        # not model text or raw chat history. Fork history clipping stays native.
        if metadata is None:
            return
        if not isinstance(metadata, dict):
            fail("invalid_native_metadata", "Native turn metadata must be an object")
        def identity(name, required=False):
            value = metadata.get(name)
            if value is None and not required:
                return None
            if not isinstance(value, str) or len(value) != 36:
                fail("invalid_native_metadata", "Native identity must be a canonical UUID")
            try:
                valid = str(uuid.UUID(value)) == value
            except ValueError:
                valid = False
            if not valid:
                fail("invalid_native_metadata", "Native identity must be a canonical UUID")
            return value
        native_session = identity("session_id", True)
        thread = identity("thread_id", True)
        parent = identity("forked_from_thread_id")
        ordinal = metadata.get("forked_from_ordinal_exclusive")
        if native_session != session or parent == thread:
            fail("invalid_native_metadata", "Native session binding or fork identity is inconsistent")
        if ordinal is not None and (type(ordinal) is not int or not 0 <= ordinal < 2**64):
            fail("invalid_native_metadata", "Native fork boundary is invalid")
        ordinal = str(ordinal) if ordinal is not None else None
        with self.db:
            row = self.db.execute(
                "SELECT session,fork_parent,fork_ordinal,inherited FROM web_native_threads WHERE thread=?",
                (thread,)).fetchone()
            if ordinal is not None and parent is None and (row is None or row[1] is None):
                fail("invalid_native_metadata", "Native fork boundary has no known parent")
            if row is not None:
                if row[0] != session or (parent is not None and row[1] != parent) or (row[2] is not None and ordinal is not None and row[2] != ordinal):
                    fail("native_lineage_conflict", "Native thread namespace or fork lineage changed", 409)
                if row[2] is None and ordinal is not None:
                    self.db.execute("UPDATE web_native_threads SET fork_ordinal=? WHERE thread=?", (ordinal, thread))
                if row[3]:
                    return
            else:
                self.db.execute("INSERT INTO web_native_threads VALUES(?,?,?,?,0)", (thread, session, parent, ordinal))
            if parent is not None:
                owner = self.db.execute("SELECT session FROM web_native_threads WHERE thread=?", (parent,)).fetchone()
                # Older gateways did not record thread metadata. A native root's
                # session equals its thread ID, so existing root references remain usable.
                parent_session = owner[0] if owner else parent
                if parent_session != session:
                    # Freeze only refs already visible to the parent. This is a
                    # cache snapshot, never a claim about native history ordinal.
                    self.db.execute(
                        "INSERT OR IGNORE INTO web_source_grants(session,sequence) "
                        "SELECT ?,sequence FROM web_sources WHERE session=? OR sequence IN "
                        "(SELECT sequence FROM web_source_grants WHERE session=?)",
                        (session, parent_session, parent_session))
            self.db.execute("UPDATE web_native_threads SET inherited=1 WHERE thread=?", (thread,))

    def _source_row(self, session, ref, columns):
        return self.db.execute(
            "SELECT " + columns + " FROM web_sources WHERE ref=? AND "
            "(session=? OR sequence IN (SELECT sequence FROM web_source_grants WHERE session=?))",
            (ref, session, session)).fetchone()

    def get(self, session, ref):
        row = self._source_row(session, ref, "body")
        if row is None:
            fail("unknown_reference", "Reference is missing or belongs to a different native web session", 404)
        return {**json.loads(row[0]), "ref_id": ref}

    def raw(self, session, ref):
        row = self._source_row(session, ref, "body,raw")
        if row is None:
            fail("unknown_reference", "Reference is missing or belongs to a different native web session", 404)
        source = json.loads(row[0])
        raw = row[1]
        if not isinstance(raw, bytes) or sha256(raw).hexdigest() != source.get("source_sha256"):
            fail("source_integrity_error", "Retained source bytes do not match their original digest", 500)
        return raw

    def generated_png_path(self, session, ref):
        """Expose only already-retained PDFium pixels via the App's native file reader."""
        from .app_config import _private
        source = self.get(session, ref)
        raw = self.raw(session, ref)
        if (source.get("content_type") != "image/png"
                or source.get("provenance", {}).get("backend") != "pdfium"
                or not raw.startswith(b"\x89PNG\r\n\x1a\n")):
            fail("generated_media_invalid", "Only verified PDF renderer output can become a local display file", 500)
        directory = self.path.absolute().parent / "generated-media"
        def safe(path, *, file=False):
            if any(_is_link_or_reparse(part)
                   for part in (path, *path.parents)):
                fail("generated_media_path_unsafe", "Generated media cannot traverse links or junctions", 500)
            if path.exists() and (not path.is_file() or path.stat().st_nlink != 1) and file:
                fail("generated_media_path_unsafe", "Generated image must be a single-link regular file", 500)
        safe(directory)
        if directory.exists() and not directory.is_dir():
            fail("generated_media_path_unsafe", "Generated media directory is not a directory", 500)
        directory.mkdir(mode=0o700, exist_ok=True)
        _private(directory, directory=True)
        target = directory / (source["source_sha256"] + ".png")
        safe(target, file=True)
        if target.exists():
            if target.read_bytes() != raw:
                fail("generated_media_integrity_error", "Existing generated image differs from retained pixels", 500)
            return target
        # Derived files are bounded separately by the same retained-source limits.
        # Nothing is evicted: an existing reference must remain valid after resume.
        count, used = 0, 0
        for child in directory.iterdir():
            safe(child, file=True)
            if not re.fullmatch(r"[0-9a-f]{64}\.png", child.name):
                fail("generated_media_path_unsafe", "Generated media contains an unexpected entry", 500)
            count += 1
            used += child.stat().st_size
        if count >= self.max_sources or used + len(raw) > self.max_bytes:
            fail("generated_media_full", "Generated image cache capacity reached; no retained image was removed", 507)
        descriptor, name = tempfile.mkstemp(prefix=".generated-", suffix=".tmp", dir=directory)
        temporary = Path(name)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                _private(temporary)
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            # Use the existing policy publisher's atomic no-replace pattern.
            try:
                if os.name == "nt":
                    os.rename(temporary, target)
                else:
                    os.link(temporary, target)
            except FileExistsError:
                pass  # A winning immutable publication must still match below.
        finally:
            temporary.unlink(missing_ok=True)
        safe(target, file=True)
        if not target.is_file() or target.read_bytes() != raw:
            fail("generated_media_integrity_error", "Generated image publication did not retain the exact pixels", 500)
        return target

    def start(self, session, operation, request):
        key = uuid.uuid4().hex
        with self.db:
            self.db.execute("INSERT INTO web_receipts VALUES(?,?,?,?,?,?,?,?)",
                (key, session, operation, sha256(encoded(request).encode()).hexdigest(), utc_now(), None, "started_unknown", "{}"))
        return key

    def finish(self, key, outcome, evidence):
        with self.db:
            self.db.execute("UPDATE web_receipts SET finished_at=?,outcome=?,evidence=? WHERE id=?",
                            (utc_now(), outcome, encoded(evidence), key))


class OpenRouterSearch:
    """One bounded provider server-tool request, no application-owned agent loop."""
    def __init__(self, credential_provider: Callable[[], str], *, ssl_context=None, timeout=90, proxy_url=None):
        self.credential_provider = credential_provider
        self.proxy_url = proxy_url
        self.ssl_context = ssl_context or ssl.create_default_context()
        self.timeout = timeout

    @staticmethod
    def extract(data, query, policy):
        if not isinstance(data, dict) or data.get("status") != "completed" or data.get("model") != MODEL:
            fail("search_generation_invalid", "Search generation did not complete with the pinned model", 502)
        response_id = data.get("id")
        if not isinstance(response_id, str) or not re.fullmatch(r"gen-[A-Za-z0-9_-]{1,220}", response_id):
            fail("search_generation_invalid", "Search generation has no valid generation ID", 502)
        output = data.get("output")
        if not isinstance(output, list):
            fail("search_generation_invalid", "Search generation has no output records", 502)
        if any(not isinstance(x, dict) or x.get("type") not in {"openrouter:web_search", "message", "reasoning"} for x in output):
            fail("unexpected_search_output", "Unexpected output item in bounded search request", 502)
        usage = data.get("usage")
        if usage is not None and not isinstance(usage, dict):
            fail("search_generation_invalid", "Invalid provider usage envelope", 502)
        details = (usage or {}).get("server_tool_use_details")
        if details is not None and not isinstance(details, dict):
            fail("search_generation_invalid", "Invalid provider tool usage envelope", 502)
        executed = (details or {}).get("tool_calls_executed")
        if executed is not None and (type(executed) is not int or executed != 1):
            fail("search_tool_budget_violation", "Search service did not execute exactly one tool call", 502)
        calls = [x for x in output if isinstance(x, dict) and x.get("type") == "openrouter:web_search"]
        if len(calls) != 1 or calls[0].get("status") != "completed":
            fail("search_tool_not_executed", "Exactly one completed search service call was required", 502)
        action = calls[0].get("action", {})
        if not isinstance(action, dict):
            fail("search_generation_invalid", "Invalid provider search action", 502)
        if action.get("type") != "search" or action.get("query") != query:
            fail("search_query_changed", "Search service did not execute the exact requested query", 502)
        sources = action.get("sources")
        if not isinstance(sources, list) or len(sources) > 5:
            fail("search_sources_invalid", "Search service source records are invalid", 502)
        annotations = {}
        for item in output:
            if not isinstance(item, dict) or item.get("type") != "message":
                continue
            contents = item.get("content", [])
            if not isinstance(contents, list):
                fail("search_generation_invalid", "Invalid provider message content", 502)
            for content in contents:
                if not isinstance(content, dict):
                    fail("search_generation_invalid", "Invalid provider message block", 502)
                values = content.get("annotations", [])
                if not isinstance(values, list):
                    fail("search_generation_invalid", "Invalid provider citation list", 502)
                for annotation in values:
                    if not isinstance(annotation, dict) or annotation.get("type") != "url_citation":
                        continue
                    url = annotation.get("url")
                    if not isinstance(url, str):
                        fail("search_sources_invalid", "Invalid provider citation URL", 502)
                    fields = {k: annotation.get(k) for k in ("title", "content")}
                    if url in annotations and annotations[url] != fields:
                        fail("conflicting_search_source", "Conflicting search source metadata", 502)
                    if isinstance(url, str):
                        annotations[url] = fields
        documents = []
        for source in sources:
            if not isinstance(source, dict) or source.get("type") != "url":
                fail("search_sources_invalid", "Unrecognized search source record", 502)
            url = checked_url(source.get("url"), policy)
            annotation = annotations.get(source["url"], {})
            title, content = annotation.get("title"), annotation.get("content")
            if title is not None and not isinstance(title, str) or content is not None and not isinstance(content, str):
                fail("search_sources_invalid", "Search excerpt is not text", 502)
            if content is not None and len(content) > 100_000:
                fail("search_sources_invalid", "Search excerpt exceeds the configured limit", 502)
            # Empty source records remain URLs, not imaginary snippets or page text.
            excerpt = content or "[Search service returned this URL without a text excerpt]"
            documents.append({"url": url, "requested_url": url, "title": title or url,
                "content_type": "text/plain", "retrieved_at": utc_now(),
                "source_sha256": sha256(excerpt.encode()).hexdigest(), "lines": excerpt.splitlines(),
                "links": [], "excerpt": True, "page_count": None,
                "provenance": {"backend": "openrouter:web_search", "engine": "exa", "generation_id": response_id,
                               "server_tool_id": calls[0].get("id"), "content_origin": "source_matched_url_citation" if content else "url_only"}})
        return documents

    async def search(self, query, policy, context_size):
        key = self.credential_provider()
        if not isinstance(key, str) or not key or any(c.isspace() for c in key):
            fail("search_credential_unavailable", "Search service credential is unavailable", 503)
        parameters = {"engine": "exa", "max_uses": 1, "max_results": 5, "max_total_results": 5,
                      "search_context_size": context_size}
        allowed = policy.upstream_allowed()
        if allowed:
            parameters["allowed_domains"] = allowed
        if policy.blocked:
            parameters["excluded_domains"] = list(policy.blocked)
        payload = {"model": MODEL, "input": "Use the provided web search tool exactly once with the exact query in this JSON string: "
                   + json.dumps(query, ensure_ascii=False) + ". Do not rewrite it. Then answer only OK.",
                   "tools": [{"type": "openrouter:web_search", "parameters": parameters}],
                   "tool_choice": "auto", "provider": {"only": ["deepseek"], "allow_fallbacks": False, "require_parameters": True},
                   "reasoning": {"effort": "none"}, "max_tool_calls": 1, "max_output_tokens": 512,
                   "stream": False, "store": False}
        evidence = {"requested_model": MODEL, "requested_provider": "deepseek", "requested_engine": "exa",
                    "response_id": None, "observed_model": None, "observed_provider": None, "automatic_retries": 0}
        install_proactor_cleanup_backport()
        try:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=self.ssl_context),
                    trust_env=False, cookie_jar=aiohttp.DummyCookieJar(), middlewares=(_single_attempt,),
                    timeout=aiohttp.ClientTimeout(total=self.timeout)) as client:
                async with client.post("https://openrouter.ai/api/v1/responses", json=payload,
                        headers={"Authorization": "Bearer " + key}, allow_redirects=False, proxy=self.proxy_url) as reply:
                    evidence["http_status"] = reply.status
                    if reply.status != 200:
                        error = WebBackendError("search_http_error", f"Search provider returned HTTP {reply.status}; no retry was attempted", 502)
                        error.evidence = evidence
                        raise error
                    chunks, size = [], 0
                    async for chunk in reply.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > 2 * 1024 * 1024:
                            fail("search_response_too_large", "Search response exceeds the configured limit", 502)
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                    data = strict_json(raw)
                    if not isinstance(data, dict):
                        fail("invalid_provider_json", "Provider JSON envelope is not an object", 502)
                    evidence.update(response_id=data.get("id"), observed_model=data.get("model"),
                                    wire_response_sha256=sha256(raw).hexdigest(), usage=usage_evidence(data.get("usage")))
            documents = self.extract(data, query, policy)
            metadata = await read_generation_evidence({"id": "native-web-search", "response_id": evidence["response_id"],
                "requested_model": MODEL, "requested_upstream_model": MODEL, "requested_provider": "deepseek"},
                self.credential_provider, ssl_context=self.ssl_context, proxy_url=self.proxy_url, max_attempts=1, timeout=10)
            evidence["generation_evidence"] = metadata
            observed = metadata.get("observed") or {}
            evidence["observed_provider"] = observed.get("provider_name")
            if metadata.get("model_identity", {}).get("matches_requested") is False:
                fail("search_model_mismatch", "Generation metadata reports a different model", 502)
            if evidence["observed_provider"] not in {None, "DeepSeek"}:
                fail("search_provider_mismatch", "Generation metadata reports a different provider", 502)
            evidence["route_verified"] = (evidence["observed_provider"] == "DeepSeek"
                                           and metadata.get("model_identity", {}).get("matches_requested") is True)
            return documents, evidence
        except asyncio.CancelledError:
            raise
        except WebBackendError as error:
            error.evidence = evidence
            raise
        except (ValueError, aiohttp.ClientError, TimeoutError, OSError):
            error = WebBackendError("search_network_or_protocol_error", "Search did not return verified source records; no retry was attempted", 502)
            error.evidence = evidence
            raise error



PDF_RENDERER_VERSION = "5.14.0"
_PDF_MAX_BYTES = 8 * 1024 * 1024
_PNG_MAX_BYTES = 8 * 1024 * 1024
_PDF_MAX_PIXELS = 16_000_000


def _render_pdf_bytes(raw, pageno):
    """Runs in a dedicated renderer process; never executes document JavaScript."""
    import importlib.metadata
    import pypdfium2 as pdfium
    if importlib.metadata.version("pypdfium2") != PDF_RENDERER_VERSION:
        fail("pdf_renderer_version_changed", "PDF renderer version requires compatibility validation", 503)
    if len(raw) > _PDF_MAX_BYTES:
        fail("document_too_large", "PDF exceeds renderer input limit", 413)
    try:
        with pdfium.PdfDocument(raw) as document:
            pages = len(document)
            if not 0 <= pageno < pages:
                fail("pdf_page_out_of_range", "Requested zero-based PDF page is outside the document", 400)
            if pages > 250:
                fail("pdf_page_limit", "PDF exceeds the configured page limit", 413)
            # PDFium's supported form appearance renderer, without document/page
            # action dispatch, preserves filled fields. Do not invoke JS actions.
            if document.get_formtype() not in {0, 1}:
                fail("unsupported_pdf_forms", "This pinned renderer does not support XFA forms", 422)
            document.init_forms()
            page = document[pageno]
            try:
                width, height = page.get_size()
                scale = 2.0
                if not math.isfinite(width) or not math.isfinite(height) or min(width, height) <= 0:
                    fail("invalid_pdf_page", "PDF page dimensions are invalid", 422)
                pixels = math.ceil(width * scale) * math.ceil(height * scale)
                if pixels > _PDF_MAX_PIXELS:
                    fail("pdf_pixel_limit", "PDF page exceeds the raster pixel budget; no silent resizing applied", 413)
                bitmap = page.render(scale=scale, draw_annots=True, may_draw_forms=True, limit_image_cache=True)
                try:
                    image = bitmap.to_pil()
                    try:
                        output = io.BytesIO()
                        image.save(output, format="PNG")
                        png = output.getvalue()
                        dimensions = image.size
                    finally:
                        image.close()
                finally:
                    bitmap.close()
            finally:
                page.close()
    except WebBackendError:
        raise
    except Exception:
        fail("pdf_render_failed", "PDF could not be rendered", 422)
    if len(png) > _PNG_MAX_BYTES:
        fail("pdf_image_size_limit", "Rendered PDF image exceeds its byte budget", 413)
    return png, {"width": dimensions[0], "height": dimensions[1], "page_count": pages,
                 "pageno": pageno, "scale": scale, "mime_type": "image/png",
                 "renderer": "pypdfium2", "renderer_version": PDF_RENDERER_VERSION,
                 "pdfium_version": str(pdfium.PDFIUM_INFO), "sha256": sha256(png).hexdigest()}


def _extract_pdf_bytes(raw):
    """Selectable PDF text extraction inside the same bounded worker as rendering."""
    import importlib.metadata
    import pypdfium2 as pdfium
    if importlib.metadata.version("pypdfium2") != PDF_RENDERER_VERSION:
        fail("pdf_renderer_version_changed", "PDF renderer version requires compatibility validation", 503)
    if len(raw) > _PDF_MAX_BYTES:
        fail("document_too_large", "PDF exceeds extractor input limit", 413)
    try:
        with pdfium.PdfDocument(raw) as document:
            pages = len(document)
            if pages > 250:
                fail("pdf_page_limit", "PDF exceeds the configured page limit", 413)
            title = document.get_metadata_dict().get("Title", "")
            if not isinstance(title, str) or len(title) > 8192:
                fail("pdf_metadata_limit", "PDF title metadata exceeds its budget", 413)
            lines, total_bytes, total_characters = [], 0, 0
            for index in range(pages):
                page = document[index]
                try:
                    textpage = page.get_textpage()
                    try:
                        characters = textpage.count_chars()
                        total_characters += characters
                        if total_characters > 2_000_000:
                            fail("pdf_text_limit", "PDF selectable text exceeds its character budget", 413)
                        text = textpage.get_text_range(index=0, count=characters) if characters else ""
                    finally:
                        textpage.close()
                finally:
                    page.close()
                total_bytes += len(text.encode("utf-8"))
                if total_bytes > 2 * 1024 * 1024:
                    fail("pdf_text_limit", "PDF selectable text exceeds its byte budget", 413)
                lines.append(f"[PDF page {index}]")
                if not text.strip():
                    lines.append("[No extractable text on this page]")
                for paragraph in text.splitlines():
                    paragraph = " ".join(paragraph.split())
                    if paragraph:
                        lines.extend(textwrap.wrap(paragraph, width=140, break_long_words=True, break_on_hyphens=False))
            return {"title": title, "lines": lines, "page_count": pages,
                    "renderer_version": PDF_RENDERER_VERSION, "pdfium_version": str(pdfium.PDFIUM_INFO)}
    except WebBackendError:
        raise
    except Exception:
        fail("pdf_text_extraction_failed", "PDF text extraction failed", 422)


def pdf_source(document, extracted):
    return {"url": document.final_url, "requested_url": document.requested_url,
            "title": extracted["title"] or document.final_url, "content_type": "application/pdf",
            "retrieved_at": document.retrieved_at, "source_sha256": sha256(document.raw).hexdigest(),
            "lines": extracted["lines"], "links": [], "excerpt": False, "page_count": extracted["page_count"],
            "provenance": {"backend": "public_http", "redirects": list(document.redirects),
                           "text_extractor": "PDFium selectable text", "extractor_version": extracted["renderer_version"],
                           "resource_limits": extracted.get("resource_limits")}}


_PDF_MEMORY_BYTES = 512 * 1024 * 1024


def _renderer_environment():
    if os.name == "nt":
        system_root = os.environ.get("SystemRoot", r"C:\Windows")
        return {"SystemRoot": system_root, "WINDIR": system_root,
                "PATH": str(Path(system_root) / "System32")}
    return {"PATH": "/usr/bin:/bin", "LANG": "C.UTF-8"}


class _WindowsPdfJob:
    """An unnamed OS resource limit for this exact owned renderer handle."""
    def __init__(self, process):
        import ctypes
        from ctypes import wintypes
        class Basic(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_int64), ("job_time", ctypes.c_int64),
                        ("flags", wintypes.DWORD), ("min_working", ctypes.c_size_t),
                        ("max_working", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]
        class IO(ctypes.Structure):
            _fields_ = [(name, ctypes.c_uint64) for name in ("read_ops", "write_ops", "other_ops", "read_bytes", "write_bytes", "other_bytes")]
        class Limits(ctypes.Structure):
            _fields_ = [("basic", Basic), ("io", IO), ("process_memory", ctypes.c_size_t),
                        ("job_memory", ctypes.c_size_t), ("peak_process_memory", ctypes.c_size_t),
                        ("peak_job_memory", ctypes.c_size_t)]
        self.api = ctypes.WinDLL("kernel32", use_last_error=True)
        self.api.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        self.api.CreateJobObjectW.restype = wintypes.HANDLE
        self.api.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        self.api.SetInformationJobObject.restype = wintypes.BOOL
        self.api.QueryInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p]
        self.api.QueryInformationJobObject.restype = wintypes.BOOL
        self.api.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        self.api.AssignProcessToJobObject.restype = wintypes.BOOL
        self.api.CloseHandle.argtypes = [wintypes.HANDLE]
        self.api.CloseHandle.restype = wintypes.BOOL
        self.handle = self.api.CreateJobObjectW(None, None)
        try:
            if not self.handle:
                fail("pdf_resource_limit_failed", "Cannot create the PDF renderer resource limit", 503)
            limits = Limits()
            # Windows venv uses a redirector plus the real interpreter. The whole
            # job is capped; create suspended so neither escapes before assignment.
            limits.basic.flags = 0x0002 | 0x0008 | 0x0100 | 0x0200 | 0x2000 | 0x0400
            limits.basic.active_processes = 2
            limits.basic.process_time = 25 * 10_000_000
            limits.process_memory = _PDF_MEMORY_BYTES
            limits.job_memory = _PDF_MEMORY_BYTES
            if not self.api.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                fail("pdf_resource_limit_failed", "Cannot apply the PDF renderer resource limit", 503)
            actual = Limits()
            if (not self.api.QueryInformationJobObject(self.handle, 9, ctypes.byref(actual), ctypes.sizeof(actual), None)
                    or actual.process_memory != _PDF_MEMORY_BYTES or actual.job_memory != _PDF_MEMORY_BYTES
                    or actual.basic.active_processes != 2 or actual.basic.process_time != 25 * 10_000_000
                    or actual.basic.flags & limits.basic.flags != limits.basic.flags):
                fail("pdf_resource_limit_failed", "PDF renderer resource limits did not read back correctly", 503)
            # The transport owns the exact Popen process handle. Never reopen a
            # global PID or find/kill a process by name. Fail if that handle is unavailable.
            popen = process._transport.get_extra_info("subprocess")
            native_handle = getattr(popen, "_handle", None)
            if native_handle is None or not self.api.AssignProcessToJobObject(self.handle, int(native_handle)):
                fail("pdf_resource_limit_failed", "Cannot bind resource limits to the owned PDF renderer", 503)
            # Resume only the single primary thread of this CREATE_SUSPENDED child.
            # Verify both the owning process handle and the opened thread identity.
            import psutil
            self.api.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
            self.api.GetExitCodeProcess.restype = wintypes.BOOL
            self.api.OpenThread.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
            self.api.OpenThread.restype = wintypes.HANDLE
            self.api.GetProcessIdOfThread.argtypes = [wintypes.HANDLE]
            self.api.GetProcessIdOfThread.restype = wintypes.DWORD
            self.api.ResumeThread.argtypes = [wintypes.HANDLE]
            self.api.ResumeThread.restype = wintypes.DWORD
            exit_code = wintypes.DWORD()
            if not self.api.GetExitCodeProcess(int(native_handle), ctypes.byref(exit_code)) or exit_code.value != 259:
                fail("pdf_resource_limit_failed", "Owned PDF renderer exited before resource admission", 503)
            threads = psutil.Process(process.pid).threads()
            if len(threads) != 1:
                fail("pdf_resource_limit_failed", "Suspended PDF renderer did not have exactly one primary thread", 503)
            thread = self.api.OpenThread(0x0042, False, threads[0].id)
            if not thread:
                fail("pdf_resource_limit_failed", "Cannot open the owned renderer primary thread", 503)
            try:
                if self.api.GetProcessIdOfThread(thread) != process.pid or self.api.ResumeThread(thread) != 1:
                    fail("pdf_resource_limit_failed", "Cannot resume the verified renderer primary thread", 503)
            finally:
                self.api.CloseHandle(thread)
        except BaseException:
            self.close()
            raise

    def close(self):
        if self.handle:
            self.api.CloseHandle(self.handle)
            self.handle = None


class PdfiumRenderer:
    """Bounded renderer subprocess, unrelated to Codex threads or their tools."""
    def __init__(self, *, timeout=25):
        if type(timeout) not in {int, float} or not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise ValueError("PDF renderer timeout must be finite and between 0 and 60 seconds")
        self.timeout = timeout
        self._slots = asyncio.Semaphore(1)

    def ensure_available(self):
        import importlib.metadata
        try:
            version = importlib.metadata.version("pypdfium2")
            importlib.metadata.version("Pillow")
        except importlib.metadata.PackageNotFoundError:
            fail("pdf_renderer_not_installed", "Install the pinned PDF renderer dependencies before screenshot use", 503)
        if version != PDF_RENDERER_VERSION:
            fail("pdf_renderer_version_changed", "PDF renderer version requires compatibility validation", 503)

    async def extract(self, raw):
        return await self.render(raw, 0, _extract=True)

    async def render(self, raw, pageno, *, _extract=False):
        import sys
        self.ensure_available()
        if len(raw) > _PDF_MAX_BYTES:
            fail("document_too_large", "PDF exceeds renderer input limit", 413)
        async with self._slots:
            process = await asyncio.create_subprocess_exec(
                sys.executable, "-I", "-X", "utf8", "-m", "codex_app_deepseek.web_backend",
                "--pdf-extract-worker" if _extract else "--pdf-render-worker", str(pageno), stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
                env=_renderer_environment(), cwd=str(Path(sys.executable).parent),
                creationflags=0x00000004 if os.name == "nt" else 0)
            job = None
            async def exchange():
                async def write():
                    try:
                        process.stdin.write(raw)
                        await process.stdin.drain()
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    finally:
                        process.stdin.close()
                async def read():
                    chunks, size = [], 0
                    while chunk := await process.stdout.read(65536):
                        size += len(chunk)
                        if size > (_PNG_MAX_BYTES * 4 // 3 + 65536):
                            fail("pdf_worker_output_limit", "PDF renderer exceeded its output budget", 502)
                        chunks.append(chunk)
                    return b"".join(chunks)
                _, response, _ = await asyncio.gather(write(), read(), process.wait())
                return response
            try:
                if os.name == "nt":
                    job = _WindowsPdfJob(process)
                response = await asyncio.wait_for(exchange(), timeout=self.timeout)
                if process.returncode != 0:
                    fail("pdf_renderer_process_failed", "PDF renderer exited before producing an image (resource limit or parser failure)", 422)
                try:
                    data = strict_json(response)
                except ValueError:
                    fail("pdf_render_protocol_error", "PDF renderer did not return valid JSON", 502)
                if not isinstance(data, dict):
                    fail("pdf_render_protocol_error", "Invalid renderer response", 502)
                if data.get("error") is not None:
                    error = data["error"]
                    fail(error["code"], error["message"], error["status"])
                limits = {"memory_bytes": _PDF_MEMORY_BYTES, "timeout_seconds": self.timeout,
                          "credential_environment_inherited": False, "input_interface": "stdin_bytes",
                          "platform": "windows_job" if os.name == "nt" else "posix_rlimit",
                          "active_process_limit": 2 if os.name == "nt" else None, "cpu_seconds": 25}
                if _extract:
                    document = data.get("document")
                    if (set(data) != {"document"} or not isinstance(document, dict)
                            or not isinstance(document.get("lines"), list)
                            or any(not isinstance(line, str) for line in document["lines"])
                            or type(document.get("page_count")) is not int or not 0 <= document["page_count"] <= 250
                            or not isinstance(document.get("title"), str)):
                        fail("pdf_render_protocol_error", "Invalid PDF text worker response", 502)
                    document["resource_limits"] = limits
                    return document
                if set(data) != {"png_base64", "metadata"}:
                    fail("pdf_render_protocol_error", "PDF renderer did not complete", 502)
                png = base64.b64decode(data["png_base64"], validate=True)
                if len(png) > _PNG_MAX_BYTES or not png.startswith(b"\x89PNG\r\n\x1a\n") or sha256(png).hexdigest() != data["metadata"].get("sha256"):
                    fail("pdf_render_protocol_error", "PDF renderer image integrity check failed", 502)
                data["metadata"]["resource_limits"] = limits
                return png, data["metadata"]
            except asyncio.TimeoutError:
                fail("pdf_render_timeout", "PDF rendering exceeded its time budget", 504)
            finally:
                if process.returncode is None:
                    try:
                        process.kill()
                    except ProcessLookupError:
                        pass
                    await process.wait()
                if job is not None:
                    job.close()


def _pdf_worker_main():
    import sys
    try:
        if os.name != "nt":
            import resource
            resource.setrlimit(resource.RLIMIT_AS, (_PDF_MEMORY_BYTES, _PDF_MEMORY_BYTES))
            resource.setrlimit(resource.RLIMIT_CPU, (25, 25))
        raw = sys.stdin.buffer.read(_PDF_MAX_BYTES + 1)
        if sys.argv[1] == "--pdf-extract-worker":
            value = {"document": _extract_pdf_bytes(raw)}
        else:
            png, metadata = _render_pdf_bytes(raw, int(sys.argv[2]))
            value = {"png_base64": base64.b64encode(png).decode("ascii"), "metadata": metadata}
    except WebBackendError as error:
        value = {"error": {"code": error.code, "message": error.message, "status": error.status}}
    except Exception:
        value = {"error": {"code": "pdf_render_failed", "message": "PDF renderer failed", "status": 422}}
    sys.stdout.write(encoded(value))


def native_image_markdown(target, *, page=None):
    """A fixed caption and literal target; never interpret an untrusted source title."""
    caption = "Web image" if page is None else f"PDF page {page}"
    # Angle destinations preserve spaces in native absolute paths. Reject control
    # characters and encode delimiters so URL/title data cannot inject Markdown.
    if not isinstance(target, str) or any(ord(c) < 32 or ord(c) == 127 for c in target):
        fail("invalid_image_display_target", "Image display target contains invalid characters", 500)
    if target.startswith(("http:", "https:")):
        target = quote(checked_url(target), safe=":/?&=#%+,-._~")
    elif not Path(target).is_absolute() or any(c in target for c in "<>"):
        fail("invalid_image_display_target", "Generated image needs an absolute native file path", 500)
    return f"![{caption}](<{target}>)"


def image_result(source, content_url=None):
    """Fields observed in the pinned App's generic image_v2 result renderer.

    An actual Codex App rendering check is still required; generic DTO parsing
    does not establish that every native web result is shown as a rich card.
    """
    item = dict(source["image"])
    if content_url is not None:
        item.update(content_url=content_url, original_content_url=content_url, thumbnail_url=content_url)
    return {"type": "image_v2", "ref_id": source["ref_id"], "url": source["url"],
            "title": source["title"], "image_result": item}


class WikimediaImageSearch:
    """Explicit Commons file-index backend, not an OpenAI/general-web image index."""
    NAME = "wikimedia_commons"
    ENDPOINT = "https://commons.wikimedia.org/w/api.php"
    USER_AGENT = "CodexAppDeepSeekGateway/0.1 (https://github.com/0xZoharHuang; interoperability)"

    def __init__(self, *, ssl_context=None, timeout=25):
        self.ssl_context = ssl_context or ssl.create_default_context()
        self.timeout = timeout

    @staticmethod
    def extract(data, policy, *, max_results, caption):
        if not isinstance(data, dict) or "error" in data or "warnings" in data:
            fail("image_search_protocol_error", "Commons rejected or did not preserve the query parameters", 502)
        query = data.get("query", {})
        if not isinstance(query, dict):
            fail("image_search_protocol_error", "Invalid Commons query response", 502)
        pages = query.get("pages", [])
        if not isinstance(pages, list) or len(pages) > max_results:
            fail("image_search_protocol_error", "Invalid Commons result count", 502)
        output = []
        for page in sorted(pages, key=lambda x: x.get("index", 0) if isinstance(x, dict) else 0):
            if not isinstance(page, dict) or page.get("ns") != 6:
                fail("image_search_protocol_error", "Commons returned a non-file page", 502)
            infos = page.get("imageinfo", [])
            if not isinstance(infos, list) or len(infos) != 1 or not isinstance(infos[0], dict):
                fail("image_search_protocol_error", "Commons returned ambiguous image metadata", 502)
            info = infos[0]
            mime = info.get("mime")
            if not isinstance(mime, str) or not mime.startswith("image/"):
                continue
            landing = checked_url(info.get("descriptionurl"))
            if urlsplit(landing).hostname != "commons.wikimedia.org":
                fail("image_search_protocol_error", "Image metadata has an unexpected source site", 502)
            if not policy.accepts("commons.wikimedia.org"):
                continue
            original = checked_url(info.get("url"))
            thumbnail = checked_url(info.get("thumburl") or info.get("url"))
            allowed_assets = {"upload.wikimedia.org", "thumb.wikimedia.org", "commons.wikimedia.org"}
            for asset in (original, thumbnail):
                host = urlsplit(asset).hostname
                if host not in allowed_assets:
                    fail("image_search_protocol_error", "Image metadata points outside Wikimedia's documented asset hosts", 502)
                if any(matches(host, blocked) for blocked in policy.blocked):
                    fail("domain_denied", "Image asset violates the requested blocked-domain policy", 403)
            width, height = info.get("width"), info.get("height")
            if type(width) is not int or type(height) is not int or min(width, height) < 1:
                fail("image_search_protocol_error", "Image dimensions are missing", 502)
            metadata = info.get("extmetadata", {})
            if not isinstance(metadata, dict):
                fail("image_search_protocol_error", "Invalid Commons attribution metadata", 502)
            def field(name):
                entry = metadata.get(name, {})
                value = entry.get("value") if isinstance(entry, dict) else None
                if value is None:
                    return ""
                if not isinstance(value, str) or len(value) > 20000:
                    fail("image_search_protocol_error", "Commons metadata field is invalid", 502)
                if "<" not in value:
                    return html.unescape(value)
                soup = BeautifulSoup(value, "html.parser")
                for tag in soup.find_all(["script", "style"]):
                    tag.decompose()
                return soup.get_text(" ", strip=True)
            title = field("ObjectName") or string(page.get("title"), "Commons file title")
            description = field("ImageDescription") if caption else ""
            artist, license_name, license_url = field("Artist"), field("LicenseShortName"), field("LicenseUrl")
            if license_url:
                checked_url(license_url)
            lines = ["Image search index: Wikimedia Commons public file repository.", title]
            if description:
                lines.append("Source-provided caption: " + description)
            lines += [f"Original dimensions: {width} x {height}; MIME: {mime}",
                      "Artist: " + (artist or "Not supplied"), "License: " + (license_name or "See source page"),
                      "License URL: " + (license_url or landing)]
            image = {"url": landing, "content_url": thumbnail, "original_content_url": original,
                     "thumbnail_url": thumbnail, "title": title, "width": width, "height": height,
                     "content_size": {"width": width, "height": height}, "attribution": artist or "Wikimedia Commons",
                     "license": license_name, "license_url": license_url or landing, "show_url": True}
            if description:
                image["caption"] = description
            output.append({"url": landing, "requested_url": landing, "title": title,
                "content_type": "application/vnd.wikimedia.image-metadata+json", "retrieved_at": utc_now(),
                "source_sha256": sha256(encoded(page).encode()).hexdigest(), "lines": lines,
                "links": [{"id": 0, "url": landing, "text": title}], "excerpt": True, "page_count": None,
                "image": image, "provenance": {"backend": "wikimedia_commons", "page_id": page.get("pageid"),
                "file_timestamp": info.get("timestamp"), "dimensions_source": "imageinfo metadata", "pixel_content_retrieved": False}})
        return output

    async def search(self, query, policy, *, max_results=4, caption=True):
        if max_results == 0 or not policy.accepts("commons.wikimedia.org"):
            return [], {"backend": self.NAME, "request_performed": False, "scope": "Commons file index"}
        params = {"action": "query", "format": "json", "formatversion": 2, "generator": "search",
                  "gsrsearch": query, "gsrnamespace": 6, "gsrlimit": max_results,
                  "prop": "imageinfo", "iiprop": "url|size|mime|timestamp|extmetadata", "iiurlwidth": 960,
                  "maxlag": 5}
        install_proactor_cleanup_backport()
        try:
            async with aiohttp.ClientSession(connector=aiohttp.TCPConnector(ssl=self.ssl_context),
                    trust_env=False, cookie_jar=aiohttp.DummyCookieJar(), middlewares=(_single_attempt,),
                    timeout=aiohttp.ClientTimeout(total=self.timeout)) as client:
                async with client.get(self.ENDPOINT, params=params, allow_redirects=False,
                        headers={"User-Agent": self.USER_AGENT, "Accept": "application/json"}) as reply:
                    if reply.status != 200:
                        fail("image_search_http_error", f"Commons returned HTTP {reply.status}; no retry attempted", 502)
                    chunks, size = [], 0
                    async for chunk in reply.content.iter_chunked(65536):
                        size += len(chunk)
                        if size > 2 * 1024 * 1024:
                            fail("image_search_response_limit", "Image metadata response exceeds its byte limit", 502)
                        chunks.append(chunk)
                    raw = b"".join(chunks)
                    data = strict_json(raw)
            sources = self.extract(data, policy, max_results=max_results, caption=caption)
            return sources, {"backend": self.NAME, "request_performed": True, "endpoint": self.ENDPOINT,
                             "scope": "Commons file index", "wire_response_sha256": sha256(raw).hexdigest(),
                             "has_more_results": "continue" in data, "automatic_retries": 0}
        except asyncio.CancelledError:
            raise
        except WebBackendError:
            raise
        except (ValueError, aiohttp.ClientError, TimeoutError, OSError):
            fail("image_search_network_error", "Commons image metadata retrieval failed; no retry attempted", 502)


_NATIVE_COMMANDS = {"search_query", "image_query", "open", "find", "click", "screenshot", "finance", "weather", "sports", "time"}
_SUPPORTED = {"search_query", "open", "find", "click", "time", "screenshot", "weather"}


def validate_request(payload, accepted_models, *, supported_operations=None, policy_backend=False):
    supported = _SUPPORTED if supported_operations is None else supported_operations
    object_fields(payload, {"id", "model", "reasoning", "input", "commands", "settings", "max_output_tokens"},
                  "search request", {"id", "model", "commands"})
    session = string(payload["id"], "native web session", 256)
    if payload["model"] not in accepted_models:
        fail("unknown_model", "Native web request model is not explicitly configured")
    if payload.get("reasoning") is not None:
        fail("unsupported_reasoning", "Standalone web reasoning controls have no backend mapping", 422)
    # This native conversation tail is not search instructions and is never sent upstream.
    if payload.get("input") is not None and not isinstance(payload["input"], (str, list)):
        fail("invalid_request", "Invalid search input")
    commands = object_fields(payload["commands"], _NATIVE_COMMANDS | {"response_length"}, "search commands")
    length = commands.get("response_length", "short")
    if length not in {"short", "medium", "long"}:
        fail("invalid_request", "Invalid response_length")
    operations = []
    for name, values in commands.items():
        if name == "response_length":
            continue
        if not isinstance(values, list) or len(values) > 20:
            fail("invalid_request", "Command values must be bounded arrays")
        if values and name not in supported:
            fail("backend_not_configured", f"Native {name} needs a verified backend; this acceptance gate remains open", 501)
        for value in values:
            fields = {"search_query": {"q", "recency", "domains"}, "image_query": {"q", "recency", "domains"}, "screenshot": {"ref_id", "pageno"}, "open": {"ref_id", "lineno"},
                      "find": {"ref_id", "pattern"}, "click": {"ref_id", "id"}, "time": {"utc_offset"}, "weather": {"location", "start", "duration"}}[name]
            required = {"search_query": {"q"}, "image_query": {"q"}, "screenshot": {"ref_id", "pageno"}, "open": {"ref_id"}, "find": {"ref_id", "pattern"},
                        "click": {"ref_id", "id"}, "time": {"utc_offset"}, "weather": {"location"}}[name]
            object_fields(value, fields, name, required)
            for field in required - {"id", "pageno", "q"}:
                string(value[field], field)
            if "q" in value and (not isinstance(value["q"], str) or len(value["q"]) > 8192 or "\x00" in value["q"]):
                fail("invalid_request", "Invalid search query")
            if "pageno" in value:
                integer(value["pageno"], "PDF page number")
            if name == "find" and not value["pattern"].strip():
                fail("invalid_pattern", "Find pattern must contain non-whitespace text")
            if "id" in value:
                integer(value["id"], "link id")
            if "lineno" in value:
                integer(value["lineno"], "line number")
            if "recency" in value:
                integer(value["recency"], "recency")
                if not policy_backend:
                    fail("unsupported_recency", "Selected search backend has no verified native recency mapping", 422)
            if "domains" in value:
                domain_list(value["domains"])
            if name == "weather":
                from .weather_backend import validate_weather
                validate_weather(value)
            if name == "time":
                parse_offset(value["utc_offset"])
            operations.append((name, value))
    if len(operations) > 20:
        fail("invalid_request", "Too many operations in one native web call")
    settings = object_fields({} if payload.get("settings") is None else payload["settings"], {"user_location", "search_context_size", "filters",
        "image_settings", "allowed_callers", "external_web_access"}, "search settings")
    query_operations = any(name in {"search_query", "image_query"} for name, _ in operations)
    if settings.get("user_location") is not None:
        location = object_fields(settings["user_location"], {"type", "country", "region", "city", "timezone"}, "user_location", {"type"})
        if location["type"] != "approximate":
            fail("invalid_request", "Invalid user_location type")
        for name in {"country", "region", "city", "timezone"} & set(location):
            if location[name] is not None:
                string(location[name], "user_location." + name, 256)
        if not policy_backend and query_operations and any(location.get(name) for name in ("country", "region", "city", "timezone")):
            fail("unsupported_location", "Selected search backend cannot preserve location-biased results", 422)
        # Search bias has no meaning for literal document operations or time.
    if settings.get("image_settings") is not None:
        options = object_fields(settings["image_settings"], {"max_results", "caption"}, "image settings")
        if "max_results" in options:
            integer(options["max_results"], "image max_results", 0, 20)
        if "caption" in options and type(options["caption"]) is not bool:
            fail("invalid_request", "image caption must be boolean")
        # Only image_query consumes image settings; other commands are unaffected.
    if settings.get("allowed_callers", ["direct"]) != ["direct"]:
        fail("unsupported_callers", "This endpoint accepts native direct web callers only", 422)
    external = settings.get("external_web_access", True)
    if not policy_backend and external is not True and external != "live":
        fail("unsupported_web_mode", "Cached or indexed mode has no equivalent backend mapping", 422)
    context = settings.get("search_context_size", "medium")
    if context not in {"low", "medium", "high"}:
        fail("invalid_request", "Invalid search_context_size")
    filters = object_fields({} if settings.get("filters") is None else settings["filters"], {"allowed_domains", "blocked_domains"}, "domain filters")
    allow = domain_list(filters.get("allowed_domains", []))
    blocked = domain_list(filters.get("blocked_domains", []))
    policy = DomainPolicy((allow,) if allow else (), blocked)
    for name, value in operations:
        if name in {"search_query", "image_query"}:
            policy.restrict(domain_list(value.get("domains", []))).upstream_allowed()
        elif "ref_id" in value and value["ref_id"].startswith(("http:", "https:")):
            checked_url(value["ref_id"], policy)
    budget = payload.get("max_output_tokens", {"short": 4000, "medium": 8000, "long": 16000}[length])
    integer(budget, "max_output_tokens", 128, 128000)
    return session, operations, policy, context, budget, length


def parse_offset(value):
    if not isinstance(value, str) or not re.fullmatch(r"[+-](?:0[0-9]|1[0-4]):[0-5][0-9]", value):
        fail("invalid_offset", "UTC offset must use +/-HH:MM within 14 hours")
    hours, minutes = int(value[1:3]), int(value[4:6])
    if hours == 14 and minutes:
        fail("invalid_offset", "UTC offset exceeds 14 hours")
    return timedelta(minutes=(hours * 60 + minutes) * (1 if value[0] == "+" else -1))


def source_result(source):
    return {"type": "text_result", "ref_id": source["ref_id"], "url": source["url"], "title": source["title"]}


def present(source, *, lineno=0, matches_at=None, window=45):
    lines = source["lines"]
    if lineno and lineno >= len(lines):
        fail("line_out_of_range", "Requested line is outside the retained document")
    description = "SEARCH EXCERPTS; open this reference to fetch the document" if source["excerpt"] else "FETCHED DOCUMENT"
    header = (f"{source['title']} ({source['url']})\n[{source['ref_id']}] {description}; "
              f"total_lines={len(lines)}; retrieved_at={source['retrieved_at']}; source_sha256={source['source_sha256']}")
    if matches_at is None:
        indices = list(range(lineno, min(len(lines), lineno + window)))
    else:
        header += f"; matching_lines={len(matches_at)}"
        indices = sorted({i for match in matches_at for i in range(max(0, match-2), min(len(lines), match+3))})[:window]
    body = "\n".join(f"L{i}: {lines[i]}" for i in indices)
    if matches_at == []:
        body = "Literal pattern was not found in this retained document."
    if len(indices) < len(lines):
        body += "\n[Partial display. The complete retrieved source is retained; use open with lineno.]"
    return header + "\n" + body


def find_lines(lines, pattern):
    # Normalized whitespace allows phrases that cross presentation line wraps.
    folded = [line.casefold() for line in lines]
    starts, cursor = [], 0
    for line in folded:
        starts.append(cursor)
        cursor += len(line) + 1
    haystack = " ".join(folded)
    needle = " ".join(pattern.casefold().split())
    if not needle:
        fail("invalid_pattern", "Find pattern must contain non-whitespace text")
    matches_at, position = set(), 0
    while True:
        found = haystack.find(needle, position)
        if found < 0:
            break
        matches_at.add(max(0, bisect_right(starts, found)-1))
        position = found + max(1, len(needle))
    return sorted(matches_at)


def bound_output(text, budget):
    # One UTF-8 byte per allowed token is conservative across byte tokenizers.
    raw = text.encode("utf-8")
    if len(raw) <= budget:
        return text
    marker = b"\n[Output budget reached; source references remain retained. Use open/lineno for continuation.]"
    return raw[:max(0, budget-len(marker))].decode("utf-8", errors="ignore") + marker.decode()


class WebBackend:
    def __init__(self, store, search, fetcher, *, accepted_models=(MODEL,), clock=None, image_search=None, pdf_renderer=None, policy_backend=None, weather_backend=None):
        self.store, self.search, self.fetcher = store, search, fetcher
        self.policy_backend = policy_backend
        from .weather_backend import OpenMeteoWeather
        self.weather_backend = weather_backend or OpenMeteoWeather(clock=clock, ssl_context=getattr(fetcher, "ssl_context", None))
        self.image_search = image_search
        self.pdf_renderer = pdf_renderer or PdfiumRenderer()
        self.accepted_models = tuple(accepted_models)
        self.clock = clock or (lambda: datetime.now(timezone.utc))

    async def _document(self, session, reference, policy, settings=None):
        if reference.startswith(("http:", "https:")):
            url = checked_url(reference, policy)
        else:
            source = self.store.get(session, reference)
            checked_url(source["url"], policy)
            if not source["excerpt"]:
                return source
            url = source["url"]
        retrieval_proof = None
        if self.policy_backend is not None:
            from .firecrawl_backend import web_mode
            document, retrieval_proof = await self.policy_backend.fetch_native(url, policy, web_mode(settings or {}))
        else:
            document = await self.fetcher.fetch(url, policy)
        if document.content_type == "application/pdf":
            extracted = await self.pdf_renderer.extract(document.raw)
            source = pdf_source(document, extracted)
        else:
            source = extract_document(document)
        if retrieval_proof is not None:
            source["provenance"]["policy_service"] = retrieval_proof
            if retrieval_proof.get("document_backend"):
                source["provenance"]["backend"] = retrieval_proof["document_backend"]
        return self.store.put(session, source, document.raw)

    async def handle(self, payload, *, native_metadata=None):
        supported = _SUPPORTED | ({"image_query"} if self.image_search is not None else set())
        session, operations, policy, context, budget, length = validate_request(
            payload, self.accepted_models, supported_operations=supported,
            policy_backend=self.policy_backend is not None)
        native_settings = payload.get("settings") or {}
        if self.policy_backend is not None:
            self.policy_backend.validate(operations, native_settings, policy)
        for name, value in operations:
            if name == "weather":
                self.weather_backend.validate(value, native_settings, policy)
        self.store.bind_native_namespace(session, native_metadata)
        if any(name == "screenshot" for name, _ in operations):
            self.pdf_renderer.ensure_available()
        # Resolve reference ownership and known index errors before any network work.
        for name, value in operations:
            if "ref_id" in value and not value["ref_id"].startswith(("http:", "https:")):
                source = self.store.get(session, value["ref_id"])
                checked_url(source["url"], policy)
                if name == "screenshot" and not source["excerpt"]:
                    if source["content_type"] != "application/pdf":
                        fail("screenshot_requires_pdf", "Screenshots require a PDF source", 415)
                    if value["pageno"] >= source["page_count"]:
                        fail("pdf_page_out_of_range", "Requested PDF page is outside the retained document")
                if name == "click":
                    if source["excerpt"] or value["id"] >= len(source["links"]):
                        fail("unknown_link", "Numbered link does not exist in an opened document")
                    checked_url(source["links"][value["id"]]["url"], policy)
                if name == "open" and not source["excerpt"] and value.get("lineno", 0) >= max(1, len(source["lines"])):
                    fail("line_out_of_range", "Requested line is outside the retained document")
            elif name == "click":
                fail("click_requires_reference", "Click requires the reference of a previously opened document")
        pieces, results, image_bytes = [], [], 0
        for name, value in operations:
            receipt = self.store.start(session, name, value)
            proof = {"source_refs": []}
            try:
                if name in {"search_query", "image_query"} and not value["q"].strip():
                    pieces.append("Empty query: no search request was performed.")
                    proof["request_performed"] = False
                elif name == "time":
                    offset = parse_offset(value["utc_offset"])
                    now = self.clock()
                    if now.tzinfo is None:
                        fail("clock_error", "Backend clock must be timezone-aware", 500)
                    stamp = now.astimezone(timezone(offset)).isoformat()
                    pieces.append(f"Current time at UTC{value['utc_offset']}: {stamp}")
                    proof["observed_time"] = stamp
                elif name == "weather":
                    document, raw, upstream = await self.weather_backend.forecast(value, policy, native_settings)
                    source = self.store.put(session, document, raw, kind="weather")
                    pieces.append(present(source, window=20))
                    results.append(source_result(source))
                    proof.update(source_refs=[source["ref_id"]], upstream=upstream,
                                 source_sha256=source["source_sha256"])
                elif name == "search_query":
                    search_policy = policy.restrict(domain_list(value.get("domains", [])))
                    if self.policy_backend is not None:
                        from .firecrawl_backend import web_mode
                        documents, upstream = await self.policy_backend.search_native(
                            value["q"], search_policy, context, recency=value.get("recency"),
                            location=native_settings.get("user_location"), mode=web_mode(native_settings))
                    else:
                        documents, upstream = await self.search.search(value["q"], search_policy, context)
                    proof["upstream"] = upstream
                    if not upstream.get("route_verified"):
                        pieces.append("[Search generation provider metadata is not yet verified; requested route was DeepSeek with fallbacks disabled.]")
                    if not documents:
                        pieces.append("Search service returned no source URLs.")
                    for document in documents:
                        source = self.store.put(session, document, kind="search")
                        pieces.append(present(source, window={"short": 15, "medium": 30, "long": 60}[length]))
                        results.append(source_result(source)); proof["source_refs"].append(source["ref_id"])
                elif name == "image_query":
                    search_policy = policy.restrict(domain_list(value.get("domains", [])))
                    options = (payload.get("settings") or {}).get("image_settings") or {}
                    documents, upstream = await self.image_search.search(value["q"], search_policy,
                        max_results=options.get("max_results", 4), caption=options.get("caption", True))
                    proof["upstream"] = upstream
                    pieces.append("Image search index: Wikimedia Commons public file repository (explicitly selected backend).")
                    if not documents:
                        pieces.append("No image records returned for this query and its filters in the bounded index result page.")
                    for document in documents:
                        source = self.store.put(session, document, kind="image")
                        pieces.append(present(source, window=20))
                        # Landing-page allowlists select the image index results;
                        # Wikimedia assets use separate known CDN hosts and blocked domains.
                        display_url = checked_url(source["image"]["thumbnail_url"],
                                                  DomainPolicy((), search_policy.blocked))
                        if urlsplit(display_url).hostname not in {
                                "upload.wikimedia.org", "thumb.wikimedia.org", "commons.wikimedia.org"}:
                            fail("image_search_protocol_error", "Image display URL is outside the known asset hosts", 502)
                        pieces.append("Native Markdown display reference (remote image safety policy still applies):\n"
                                      + native_image_markdown(display_url))
                        results.append(image_result(source)); proof["source_refs"].append(source["ref_id"])
                    if upstream.get("has_more_results"):
                        pieces.append("[The image index has further result pages; this response is limited to the configured result count.]")
                elif name == "screenshot":
                    source = await self._document(session, value["ref_id"], policy, native_settings)
                    if source["content_type"] != "application/pdf":
                        fail("screenshot_requires_pdf", "Screenshots require a PDF source", 415)
                    png, metadata = await self.pdf_renderer.render(self.store.raw(session, source["ref_id"]), value["pageno"])
                    image_bytes += len(png)
                    if image_bytes > 16 * 1024 * 1024:
                        fail("screenshot_batch_limit", "Screenshot batch exceeds the image byte budget", 413)
                    title = f"{source['title']} — page {value['pageno']}"
                    rendered = self.store.put(session, {
                        "url": source["url"], "requested_url": source["url"], "title": title,
                        "content_type": "image/png", "retrieved_at": utc_now(), "source_sha256": metadata["sha256"],
                        "lines": [f"Rendered PDF page {value['pageno']} of {metadata['page_count']}; {metadata['width']} x {metadata['height']} pixels.",
                                  "This is image metadata, not extracted page text."],
                        "links": [], "excerpt": False, "page_count": None,
                        "image": {"url": source["url"], "title": title, "width": metadata["width"], "height": metadata["height"],
                                  "content_size": {"width": metadata["width"], "height": metadata["height"]},
                                  "attribution": "PDF page screenshot", "show_url": True},
                        "provenance": {"backend": "pdfium", "parent_ref": source["ref_id"],
                                       "parent_sha256": source["source_sha256"], **metadata}}, png, kind="image")
                    pieces.append(f"[{rendered['ref_id']}] {title} ({source['url']})\nRendered {metadata['width']} x {metadata['height']} PNG; sha256={metadata['sha256']}; source PDF [{source['ref_id']}].")
                    display_path = self.store.generated_png_path(session, rendered["ref_id"])
                    pieces.append("Native Markdown display reference for these exact retained pixels:\n"
                                  + native_image_markdown(display_path.as_posix(), page=value["pageno"]))
                    results.append(image_result(rendered, "data:image/png;base64," + base64.b64encode(png).decode("ascii")))
                    proof.update(source_refs=[source["ref_id"], rendered["ref_id"]], source_sha256=source["source_sha256"],
                                 image=metadata, generated_display={"native_path": str(display_path), "sha256": metadata["sha256"]})
                else:
                    if name == "click":
                        origin = self.store.get(session, value["ref_id"])
                        reference = origin["links"][value["id"]]["url"]
                    else:
                        reference = value["ref_id"]
                    source = await self._document(session, reference, policy, native_settings)
                    at = find_lines(source["lines"], value["pattern"]) if name == "find" else None
                    pieces.append(present(source, lineno=value.get("lineno", 0), matches_at=at,
                                          window={"short": 45, "medium": 90, "long": 180}[length]))
                    results.append(source_result(source)); proof["source_refs"].append(source["ref_id"])
                    proof["source_sha256"] = source["source_sha256"]
                self.store.finish(receipt, "completed", proof)
            except asyncio.CancelledError:
                self.store.finish(receipt, "cancelled_unknown_outcome", proof)
                raise
            except WebBackendError as error:
                proof.update(error_code=error.code, upstream=getattr(error, "evidence", None))
                self.store.finish(receipt, "failed", proof)
                raise
            except Exception:
                self.store.finish(receipt, "failed", {**proof, "error_code": "internal_backend_error"})
                fail("internal_backend_error", "Native web backend encountered an internal error", 500)
        return {"encrypted_output": None, "output": bound_output("\n\n".join(pieces) or "No web operations requested.", budget),
                "results": list({x["ref_id"]: x for x in results}.values())}


if __name__ == "__main__":
    import sys
    if len(sys.argv) != 3 or sys.argv[1] not in {"--pdf-render-worker", "--pdf-extract-worker"}:
        raise SystemExit(2)
    _pdf_worker_main()
