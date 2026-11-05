"""Lossless private image transport. Never controls native threads or runs tools.

The body returned by prepare() must be the actual HTTP body sent by its caller. File IDs and
cache metadata are private. No image bytes or upstream credentials are persisted.
"""
from __future__ import annotations
from .paths import _is_link_or_reparse, _sqlite_filename

import asyncio
import base64
from contextlib import asynccontextmanager, closing
from dataclasses import dataclass, field
import errno
import hashlib
import hmac
from io import BytesIO
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from typing import Any, Callable, Protocol
from urllib.parse import urlsplit

import psutil

FILE_ID = re.compile(r"or_file_[A-Za-z0-9_-]{8,128}\Z")
DATA_URL = re.compile(r"data:(image/(?:png|jpeg|gif|webp));base64,([A-Za-z0-9+/]*={0,2})\Z")
MAX_UPLOAD_BYTES = 100 * 1024 * 1024


class ImageTransportError(RuntimeError):
    """A safe error message; neither key, raw body nor file ID is included."""


class UploadOutcomeUnknown(ImageTransportError):
    pass


class CleanupPending(ImageTransportError):
    pass


class PayloadBudgetExceeded(ImageTransportError):
    pass


class FileReferenceLimitExceeded(ImageTransportError):
    pass


class FileOperationError(ImageTransportError):
    def __init__(self, operation: str, status: int | None, *, definitely_not_created: bool = False,
                 safe_error_code: str | int | None = None, safe_headers: dict | None = None):
        super().__init__(f"Files API {operation} failed (status {status}); inspect private transport receipts")
        self.status = status
        self.definitely_not_created = definitely_not_created
        self.safe_error_code = safe_error_code
        self.safe_headers = safe_headers or {}


def _safe_error_detail(error: BaseException) -> str:
    details = {'type': type(error).__name__}
    if isinstance(error, FileOperationError):
        details.update({'http_status': error.status, 'code': error.safe_error_code, 'headers': error.safe_headers})
    return json.dumps(details, separators=(',', ':'))


@dataclass(frozen=True)
class _FileResponse:
    status: int
    value: Any = field(repr=False)
    safe_error_code: str | int | None = None
    safe_headers: dict = field(default_factory=dict)


def _strict_json(raw: bytes) -> Any:
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('Duplicate metadata JSON field')
            result[key] = value
        return result
    def constant(value):
        raise ValueError('Nonfinite metadata JSON constant')
    def number(value):
        parsed = float(value)
        if not math.isfinite(parsed):
            raise ValueError('Nonfinite metadata JSON number')
        return parsed
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=constant, parse_float=number)


@dataclass(frozen=True)
class FileMetadata:
    file_id: str = field(repr=False)
    filename: str
    mime: str
    size_bytes: int

    @classmethod
    def parse(cls, value: Any) -> FileMetadata:
        if not isinstance(value, dict):
            raise ImageTransportError("Files API metadata is not an object")
        fid, name, mime, size = (value.get(k) for k in ("id", "filename", "mime_type", "size_bytes"))
        if not isinstance(fid, str) or not FILE_ID.fullmatch(fid):
            raise ImageTransportError("Files API returned an invalid private file identifier")
        if not isinstance(name, str) or not isinstance(mime, str) or type(size) is not int or size <= 0:
            raise ImageTransportError("Files API returned incomplete file metadata")
        return cls(fid, name, mime, size)


@dataclass(frozen=True)
class FilePage:
    entries: tuple[dict, ...] = field(repr=False)
    has_more: bool
    cursor: str | None = field(default=None, repr=False)


class FilesBackend(Protocol):
    async def upload(self, key: str, data: bytes, mime: str, filename: str) -> FileMetadata: ...
    async def get(self, key: str, file_id: str) -> FileMetadata | None: ...
    async def delete(self, key: str, file_id: str) -> None: ...
    async def list_page(self, key: str, *, limit: int, cursor: str | None) -> FilePage: ...


class OpenRouterFilesBackend:
    """Official global endpoint, no retries, redirects, provider fallback or list-all."""
    def __init__(self, client: Any, *, proxy_url: str | None = None):
        if proxy_url is not None:
            if not isinstance(proxy_url, str):
                raise ImageTransportError('Invalid explicit Files API proxy configuration')
            try:
                value = urlsplit(proxy_url)
                if value.scheme not in {'http', 'https'} or not value.hostname or value.fragment or value.query:
                    raise ValueError('Invalid proxy')
                _ = value.port
            except (ValueError, TypeError) as error:
                raise ImageTransportError('Invalid explicit Files API proxy configuration') from None
        self.client, self.proxy_url = client, proxy_url

    async def _call(self, method: str, suffix: str, key: str, **kwargs: Any) -> _FileResponse:
        async with self.client.request(method, 'https://openrouter.ai/api/v1/files' + suffix,
                headers={'Authorization': 'Bearer ' + key}, allow_redirects=False,
                proxy=self.proxy_url, **kwargs) as response:
            chunks, length = [], 0
            while True:
                chunk = await response.content.read(65536)
                if not chunk:
                    break
                length += len(chunk)
                if length > 1024 * 1024:
                    raise ImageTransportError('Files API response exceeded metadata limit')
                chunks.append(chunk)
            raw = b''.join(chunks)
            try:
                value = _strict_json(raw)
            except ValueError as error:
                if response.status == 200:
                    raise ImageTransportError('Files API metadata JSON is ambiguous or invalid') from error
                value = None
            safe_headers = {}
            for name, value_header in getattr(response, 'headers', {}).items():
                lower = name.lower()
                if lower not in {'retry-after', 'x-ratelimit-limit', 'x-ratelimit-remaining', 'x-ratelimit-reset'}:
                    continue
                if isinstance(value_header, str) and re.fullmatch(r'[A-Za-z0-9 ,:.-]{1,128}', value_header) and key not in value_header:
                    safe_headers[lower] = value_header
            code = value.get('error', {}).get('code') if isinstance(value, dict) and isinstance(value.get('error'), dict) else None
            if not (type(code) is int and 0 <= code <= 999999) and not (isinstance(code, str) and re.fullmatch(r'[a-z][a-z0-9_]{0,63}', code)):
                code = None
            return _FileResponse(response.status, value, code, safe_headers)

    @staticmethod
    def _error(operation: str, response: _FileResponse, *, definitely_not_created: bool = False) -> FileOperationError:
        return FileOperationError(operation, response.status, definitely_not_created=definitely_not_created,
                                  safe_error_code=response.safe_error_code, safe_headers=response.safe_headers)

    async def upload(self, key: str, data: bytes, mime: str, filename: str) -> FileMetadata:
        import aiohttp
        # FormData otherwise creates a large BytesPayload. The caller owns
        # this stream through response parsing, including rejection/cancellation.
        with BytesIO(data) as stream:
            form = aiohttp.FormData()
            form.add_field('file', stream, filename=filename, content_type=mime)
            response = await self._call('POST', '', key, data=form)
        if response.status != 200:
            raise self._error('upload', response, definitely_not_created=response.status in {400,401,403,413,415,422,429})
        return FileMetadata.parse(response.value)

    async def get(self, key: str, file_id: str) -> FileMetadata | None:
        self._validate_id(file_id)
        response = await self._call('GET', '/' + file_id, key)
        if response.status == 404:
            return None
        if response.status != 200:
            raise self._error('get', response)
        return FileMetadata.parse(response.value)

    async def delete(self, key: str, file_id: str) -> None:
        self._validate_id(file_id)
        response = await self._call('DELETE', '/' + file_id, key)
        if response.status == 404:
            return
        if response.status != 200 or not isinstance(response.value, dict) or response.value.get('id') != file_id or response.value.get('type') != 'file_deleted':
            raise self._error('delete', response)

    async def list_page(self, key: str, *, limit: int, cursor: str | None) -> FilePage:
        if type(limit) is not int or not 1 <= limit <= 1000:
            raise ImageTransportError('Invalid bounded Files metadata page size')
        if cursor is not None and (not isinstance(cursor, str) or not cursor or len(cursor) > 4096):
            raise ImageTransportError('Invalid Files metadata cursor')
        params = {'limit': str(limit), 'order': 'asc'}
        if cursor is not None:
            params['cursor'] = cursor
        response = await self._call('GET', '', key, params=params)
        if response.status != 200:
            raise self._error('list', response)
        value = response.value
        if not isinstance(value, dict) or not isinstance(value.get('data'), list) or type(value.get('has_more')) is not bool:
            raise ImageTransportError('Files metadata page has an invalid envelope')
        if len(value['data']) > limit:
            raise ImageTransportError('Files metadata page exceeded its requested bound')
        next_cursor = value.get('cursor')
        if next_cursor is not None and (not isinstance(next_cursor, str) or len(next_cursor) > 4096):
            raise ImageTransportError('Files metadata page has an invalid cursor')
        if value['has_more'] and not next_cursor:
            raise ImageTransportError('Files metadata page is incomplete without a cursor')
        entries = []
        for entry in value['data']:
            if not isinstance(entry, dict) or not isinstance(entry.get('filename'), str):
                raise ImageTransportError('Files metadata page contains an invalid entry')
            # Discard extra fields immediately. No file contents endpoint is used.
            entries.append({name: entry.get(name) for name in ('id','filename','mime_type','size_bytes')})
        return FilePage(tuple(entries), value['has_more'], next_cursor)

    @staticmethod
    def _validate_id(file_id: str) -> None:
        if not FILE_ID.fullmatch(file_id):
            raise ImageTransportError("Invalid private file identifier")


@dataclass(frozen=True)
class TransportPolicy:
    # An explicit operator policy, NOT a measured upstream request-size limit.
    activate_above_bytes: int
    idle_ttl_seconds: float = 3600
    verify_after_seconds: float = 60
    lock_timeout_seconds: float = 120
    max_file_references: int = 16
    wire_budget_bytes: int | None = None

    def __post_init__(self) -> None:
        if type(self.activate_above_bytes) is not int or self.activate_above_bytes <= 0:
            raise ValueError("activate_above_bytes must be a positive byte count")
        if type(self.max_file_references) is not int or not 0 <= self.max_file_references <= 16:
            raise ValueError('File references must respect the observed 16-reference request limit')
        if self.wire_budget_bytes is not None and (type(self.wire_budget_bytes) is not int or self.wire_budget_bytes < self.activate_above_bytes):
            raise ValueError('Wire budget must be a positive byte count at least as large as the activation threshold')
        values = (self.idle_ttl_seconds, self.verify_after_seconds, self.lock_timeout_seconds)
        if any(type(value) not in {int, float} or not math.isfinite(value) for value in values):
            raise ValueError("Transport durations must be finite numbers")
        if self.idle_ttl_seconds < 0 or self.verify_after_seconds < 0 or self.lock_timeout_seconds <= 0:
            raise ValueError("Transport durations are invalid")


@dataclass(frozen=True)
class _Blob:
    digest: str
    mime: str
    data: bytes = field(repr=False)


@dataclass(frozen=True)
class PreparedImages:
    payload: dict = field(repr=False)
    body: bytes = field(repr=False)
    before_bytes: int
    image_occurrences: int
    uploads: int
    reused: int
    scope: str | None
    references: tuple[dict, ...] = field(repr=False)
    _lease: Any = field(default=None, repr=False)
    converted_occurrences: int = 0
    existing_file_references: int = 0
    wire_budget_bytes: int | None = None

    async def mark_upstream_started(self, request_ref: str) -> None:
        if self._lease is not None:
            await self._lease.start(request_ref)

    async def mark_upstream_settled(self, response_id: str, terminal: str) -> None:
        if self._lease is not None:
            await self._lease.settle(response_id, terminal)

    async def mark_upstream_rejected(self, status: int) -> None:
        if self._lease is not None:
            await self._lease.rejected(status)

    def receipt(self, *, private: bool = False) -> dict:
        refs = [dict(item) for item in self.references]
        if not private:
            for item in refs:
                item.pop("file_id", None)
        return {"before_bytes": self.before_bytes, "after_bytes": len(self.body),
                "image_occurrences": self.image_occurrences, "uploads": self.uploads,
                "reused": self.reused, "credential_scope": self.scope,
                "policy_kind": "operator_prevention_threshold", "references": refs,
                "native_history_modified": False, "raw_images_persisted": False, "converted_image_occurrences": self.converted_occurrences,
                "remaining_inline_images": self.image_occurrences - self.converted_occurrences,
                "existing_file_references": self.existing_file_references,
                "wire_budget_bytes": self.wire_budget_bytes,
                "within_wire_budget": None if self.wire_budget_bytes is None else len(self.body) <= self.wire_budget_bytes}


def encode_body(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")


def _image_parts(payload: dict):
    # Walk only typed Responses content. JSON tool arguments, schemas, developer
    # text and arbitrary metadata can contain lookalike objects and are not images.
    items = payload.get("input", [])
    if not isinstance(items, list):
        return
    for item_index, item in enumerate(items):
        if not isinstance(item, dict):
            continue
        if item.get("type", "message") == "message":
            key = "content"
        elif item.get("type") in {"function_call_output", "custom_tool_call_output"}:
            key = "output"
        else:
            continue
        parts = item.get(key)
        if not isinstance(parts, list):
            continue
        for content_index, part in enumerate(parts):
            if isinstance(part, dict) and part.get("type") == "input_image":
                yield (item_index, key, content_index), part


def image_plan(payload: dict) -> tuple[list[tuple[tuple, str]], dict[str, _Blob]]:
    positions, blobs = [], {}
    for position, part in _image_parts(payload):
        url = part.get("image_url")
        if not isinstance(url, str) or not url.startswith("data:"):
            continue
        if part.get("file_id") is not None:
            raise ImageTransportError("Input image cannot contain both inline data and a private file ID")
        match = DATA_URL.fullmatch(url)
        if not match:
            raise ImageTransportError("Inline image is not a supported canonical base64 data URL")
        mime, encoded = match.groups()
        try:
            data = base64.b64decode(encoded, validate=True)
        except ValueError as error:
            raise ImageTransportError("Inline image has invalid base64 bytes") from error
        if not data or len(data) > MAX_UPLOAD_BYTES:
            raise ImageTransportError("Inline image exceeds the official individual Files upload range")
        # Identity is raw bytes, with MIME checked separately; never resize/recode.
        digest = hashlib.sha256(data).hexdigest()
        if digest in blobs and blobs[digest].mime != mime:
            raise ImageTransportError("Identical image bytes carry conflicting MIME types")
        blobs[digest] = _Blob(digest, mime, data)
        positions.append((position, digest))
    return positions, blobs


def count_file_references(value: Any) -> int:
    # Schemas and business metadata describe JSON data, not uploaded resources.
    # Keep the existing conservative occurrence count everywhere else, including
    # hosted-tool parameters/environment and unknown future file-bearing fields.
    def visit(node, context="request"):
        if isinstance(node, list):
            child_context = "tool" if context == "tools" else context
            return sum(visit(item, child_context) for item in node)
        if not isinstance(node, dict):
            return 0
        kind = node.get("type")
        opaque = {"metadata", "client_metadata"}
        if context == "tool" and kind == "function":
            opaque.update({"parameters", "output_schema"})
        if context == "schema_holder" or (context == "format" and kind == "json_schema"):
            opaque.add("schema")
        count = int(isinstance(node.get("file_id"), str) and bool(node["file_id"]))
        if isinstance(node.get("file_ids"), list):
            count += sum(isinstance(item, str) and bool(item) for item in node["file_ids"])
        for key, item in node.items():
            if key in opaque or key in {"file_id", "file_ids"}:
                continue
            child_context = "other"
            if key == "tools" and (context == "request" or (context == "tool" and kind == "namespace")):
                child_context = "tools"
            elif context == "request" and key == "text":
                child_context = "text"
            elif (context == "request" and key == "response_format") or (context == "text" and key == "format"):
                child_context = "format"
            elif context == "format" and kind == "json_schema" and key == "json_schema":
                child_context = "schema_holder"
            count += visit(item, child_context)
        return count
    return visit(value)


def select_image_references(payload: dict, positions: list, slots: int) -> list:
    candidates = []
    for encounter, (position, digest) in enumerate(positions):
        i, key, j = position
        part = payload['input'][i][key][j]
        # Files references ignore detail. Explicit low must retain its native
        # inline precision even when another occurrence shares the same bytes.
        if part.get('detail') == 'low':
            continue
        reference = dict(part)
        del reference['image_url']
        # Current official ID shape is 32 characters. The actual body is checked
        # again after upload; an unexpectedly longer ID cannot bypass the budget.
        reference['file_id'] = 'or_file_' + 'x' * 24
        savings = len(encode_body(part)) - len(encode_body(reference))
        if savings > 0:
            candidates.append((savings, encounter, position, digest))
    picked = sorted(candidates, key=lambda item: (-item[0], item[1]))[:slots]
    return [(position, digest) for _, _, position, digest in sorted(picked, key=lambda item: item[1])]


def _private_directory(path: Path) -> None:
    """Protect storage before any salt, DB or provider file ID is written."""
    if not path.is_absolute():
        raise ImageTransportError("Image cache must have an explicit absolute directory")
    for ancestor in (path, *path.parents):
        if _is_link_or_reparse(ancestor):
            raise ImageTransportError("Image cache cannot traverse a link or junction")
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        path.chmod(0o700)
        return
    import ctypes
    from ctypes import wintypes as w
    adv = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.GetCurrentProcess.restype = w.HANDLE
    adv.OpenProcessToken.argtypes = [w.HANDLE, w.DWORD, ctypes.POINTER(w.HANDLE)]
    adv.GetTokenInformation.argtypes = [w.HANDLE, ctypes.c_int, ctypes.c_void_p, w.DWORD, ctypes.POINTER(w.DWORD)]
    adv.ConvertSidToStringSidW.argtypes = [ctypes.c_void_p, ctypes.POINTER(w.LPWSTR)]
    adv.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [w.LPCWSTR, w.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
    adv.GetSecurityDescriptorDacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(w.BOOL), ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(w.BOOL)]
    adv.SetNamedSecurityInfoW.argtypes = [w.LPWSTR, ctypes.c_int, w.DWORD, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    adv.SetNamedSecurityInfoW.restype = w.DWORD
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    token, sid_text, descriptor = w.HANDLE(), w.LPWSTR(), ctypes.c_void_p()
    try:
        if not adv.OpenProcessToken(kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
            raise OSError("Token access failed")
        size = w.DWORD()
        adv.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
        buffer = ctypes.create_string_buffer(size.value)
        if not adv.GetTokenInformation(token, 1, buffer, size, ctypes.byref(size)):
            raise OSError("Token identity failed")
        sid = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_void_p))[0]
        if not adv.ConvertSidToStringSidW(sid, ctypes.byref(sid_text)):
            raise OSError("SID conversion failed")
        sddl = f"D:P(A;OICI;FA;;;{sid_text.value})(A;OICI;FA;;;SY)"
        if not adv.ConvertStringSecurityDescriptorToSecurityDescriptorW(sddl, 1, ctypes.byref(descriptor), None):
            raise OSError("DACL conversion failed")
        present, defaulted, dacl = w.BOOL(), w.BOOL(), ctypes.c_void_p()
        if not adv.GetSecurityDescriptorDacl(descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(defaulted)) or not present:
            raise OSError("DACL inspection failed")
        if adv.SetNamedSecurityInfoW(str(path), 1, 0x80000004, None, None, dacl, None) != 0:
            raise OSError("DACL installation failed")
    except OSError as error:
        raise ImageTransportError("Cannot protect the private image cache") from error
    finally:
        if token:
            kernel.CloseHandle(token)
        if sid_text:
            kernel.LocalFree(sid_text)
        if descriptor:
            kernel.LocalFree(descriptor)


@asynccontextmanager
async def _file_lock(path: Path, timeout: float):
    if _is_link_or_reparse(path):
        raise ImageTransportError("Cache lock cannot be a link")
    handle = path.open("a+b")
    locked = False
    start = time.monotonic()
    try:
        # The locked byte need not exist on Unix. Windows needs one initialized byte.
        handle.seek(0, os.SEEK_END)
        if handle.tell() == 0:
            handle.write(b"\0"); handle.flush()
        while True:
            handle.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                locked = True
                break
            except OSError as error:
                if error.errno not in {errno.EACCES, errno.EAGAIN, errno.EDEADLK}:
                    raise
                if time.monotonic() - start >= timeout:
                    raise ImageTransportError("Image cache lock wait expired; no upload replayed") from error
                await asyncio.sleep(0.025)
        yield
    finally:
        if locked:
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


def _owner_alive(pid: int, birth: float) -> bool | None:
    try:
        return psutil.Process(pid).create_time() == birth
    except psutil.NoSuchProcess:
        return False
    except (psutil.AccessDenied, OSError):
        return None  # Do not reap on an observation failure.


class ImageTransport:
    def __init__(self, cache_root: Path, credentials: Callable[[], str], backend: FilesBackend,
                 policy: TransportPolicy, *, clock: Callable[[], float] = time.time,
                 owner_alive: Callable[[int, float], bool | None] = _owner_alive):
        self.root, self.credentials, self.backend, self.policy = Path(cache_root), credentials, backend, policy
        self.clock, self.owner_alive = clock, owner_alive
        self.pid, self.birth = os.getpid(), psutil.Process().create_time()
        _private_directory(self.root)

    async def _scope(self, key: str) -> tuple[str, Path]:
        if not isinstance(key, str) or not key:
            raise ImageTransportError("Missing credential for private image transport")
        async with _file_lock(self.root / "initialize.lock", self.policy.lock_timeout_seconds):
            salt_path = self.root / "scope.salt"
            if not salt_path.exists():
                fd = os.open(salt_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
                with os.fdopen(fd, "wb") as output:
                    output.write(secrets.token_bytes(32)); output.flush(); os.fsync(output.fileno())
            if salt_path.is_symlink():
                raise ImageTransportError("Credential scope salt cannot be a link")
            salt = salt_path.read_bytes()
            if len(salt) != 32:
                raise ImageTransportError("Credential scope salt is invalid; existing uploads need reconciliation")
            scope = hmac.new(salt, key.encode("utf-8"), hashlib.sha256).hexdigest()
            directory = self.root / scope
            _private_directory(directory)
        return scope, directory

    @asynccontextmanager
    async def _db(self, directory: Path):
        async with _file_lock(directory / "cache.lock", self.policy.lock_timeout_seconds):
            path = directory / "files.sqlite"
            if path.is_symlink():
                raise ImageTransportError("Image cache database cannot be a link")
            db = sqlite3.connect(_sqlite_filename(path), isolation_level=None)
            try:
                db.row_factory = sqlite3.Row
                db.executescript("""
                    PRAGMA foreign_keys=ON;
                    PRAGMA synchronous=FULL;
                    CREATE TABLE IF NOT EXISTS assets (
                      digest TEXT PRIMARY KEY,mime TEXT NOT NULL,nbytes INTEGER NOT NULL,
                      filename TEXT NOT NULL,state TEXT NOT NULL,file_id TEXT,
                      created REAL NOT NULL,last_used REAL NOT NULL,verified REAL NOT NULL,
                      operation_owner TEXT,reason TEXT);
                    CREATE TABLE IF NOT EXISTS pins (
                      lease TEXT NOT NULL,digest TEXT NOT NULL,pid INTEGER NOT NULL,birth REAL NOT NULL,
                      phase TEXT NOT NULL DEFAULT 'prepared',request_ref TEXT,
                      PRIMARY KEY(lease,digest));
                    CREATE TABLE IF NOT EXISTS receipts (
                      sequence INTEGER PRIMARY KEY,at REAL NOT NULL,digest TEXT NOT NULL,
                      event TEXT NOT NULL,detail TEXT);
                """)
                columns = {row['name'] for row in db.execute('PRAGMA table_info(pins)')}
                if 'phase' not in columns:
                    db.execute("ALTER TABLE pins ADD COLUMN phase TEXT NOT NULL DEFAULT 'prepared'")
                if 'request_ref' not in columns:
                    db.execute('ALTER TABLE pins ADD COLUMN request_ref TEXT')
                # Pending operations cannot still own this OS lock. Never replay an
                # upload whose response may have been lost, even if its PID still exists.
                db.execute("UPDATE assets SET state='upload_unknown',reason='interrupted_upload' WHERE state='upload_pending'")
                db.execute("UPDATE assets SET state='delete_unknown',reason='interrupted_delete' WHERE state='delete_pending'")
                # A verified provider terminal/rejection has already ended image
                # consumption. Recover a failed final local release even while its
                # owner lives; prepared and upstream_unknown leases remain protected.
                db.execute("DELETE FROM pins WHERE phase='settled'")
                for pin in db.execute("SELECT DISTINCT pid,birth FROM pins WHERE phase!='upstream_unknown'").fetchall():
                    if self.owner_alive(pin['pid'], pin['birth']) is False:
                        db.execute("DELETE FROM pins WHERE pid=? AND birth=? AND phase!='upstream_unknown'", (pin['pid'], pin['birth']))
                yield db
            finally:
                db.close()

    def _record(self, db, digest: str, event: str, detail: str | None = None) -> None:
        db.execute("INSERT INTO receipts(at,digest,event,detail) VALUES(?,?,?,?)", (self.clock(), digest, event, detail))

    @staticmethod
    def _matches(row, meta: FileMetadata) -> bool:
        return meta.filename == row['filename'] and meta.mime == row['mime'] and meta.size_bytes == row['nbytes'] and (row['file_id'] is None or meta.file_id == row['file_id'])

    async def _acquire(self, directory: Path, key: str, blob: _Blob, lease: str) -> tuple[str, bool]:
        async with self._db(directory) as db:
            row = db.execute("SELECT * FROM assets WHERE digest=?", (blob.digest,)).fetchone()
            if row and (row['mime'] != blob.mime or row['nbytes'] != len(blob.data)):
                raise ImageTransportError("Cached image metadata conflicts with original bytes")
            if row and row['state'] == 'ready':
                if self.clock() - row['verified'] >= self.policy.verify_after_seconds:
                    meta = await self.backend.get(key, row['file_id'])
                    if meta is None:
                        db.execute("UPDATE assets SET state='deleted',file_id=NULL WHERE digest=?", (blob.digest,))
                        self._record(db, blob.digest, 'externally_invalidated')
                        row = None
                    elif not self._matches(row, meta):
                        raise ImageTransportError("Uploaded image metadata changed; refusing to reuse the file")
                    else:
                        db.execute("UPDATE assets SET verified=? WHERE digest=?", (self.clock(), blob.digest))
                if row is not None:
                    db.execute("UPDATE assets SET last_used=? WHERE digest=?", (self.clock(), blob.digest))
                    db.execute("INSERT OR IGNORE INTO pins(lease,digest,pid,birth) VALUES(?,?,?,?)", (lease, blob.digest, self.pid, self.birth))
                    return row['file_id'], False
            if row and row['state'] in {'upload_pending','upload_unknown'}:
                raise UploadOutcomeUnknown("A previous image upload has unknown outcome; inspect its private receipt before retrying")
            if row and row['state'] in {'delete_pending','delete_unknown'}:
                raise CleanupPending("A previous image deletion needs reconciliation before reuse")
            filename = 'cds-image-' + secrets.token_hex(20) + '.' + blob.mime.split('/')[1]
            now = self.clock()
            db.execute("INSERT OR REPLACE INTO assets VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (blob.digest, blob.mime, len(blob.data), filename, 'upload_pending', None, now, now, 0, lease, None))
            self._record(db, blob.digest, 'upload_started')
            try:
                meta = await self.backend.upload(key, blob.data, blob.mime, filename)
            except BaseException as error:
                known = isinstance(error, FileOperationError) and error.definitely_not_created
                db.execute("UPDATE assets SET state=?,reason=? WHERE digest=?", ('upload_rejected' if known else 'upload_unknown', type(error).__name__, blob.digest))
                self._record(db, blob.digest, 'upload_rejected' if known else 'upload_unknown', _safe_error_detail(error))
                raise
            # Persist the returned ID even on a metadata mismatch so cleanup is
            # possible; such a resource is never passed to the model.
            row = db.execute("SELECT * FROM assets WHERE digest=?", (blob.digest,)).fetchone()
            valid = self._matches(row, meta)
            db.execute("UPDATE assets SET state=?,file_id=?,verified=? WHERE digest=?",
                ('ready' if valid else 'delete_unknown', meta.file_id, self.clock(), blob.digest))
            self._record(db, blob.digest, 'upload_confirmed' if valid else 'upload_metadata_mismatch')
            if not valid:
                raise CleanupPending("Upload metadata did not match the original image; cleanup is required")
            db.execute("INSERT INTO pins(lease,digest,pid,birth) VALUES(?,?,?,?)", (lease, blob.digest, self.pid, self.birth))
            return meta.file_id, True

    @asynccontextmanager
    async def prepare(self, payload: dict, *, credential: str | None = None):
        original = encode_body(payload)
        # JSON roundtrip copies all JSON values without changing the caller's native history.
        copied = json.loads(original)
        if len(original) <= self.policy.activate_above_bytes:
            yield PreparedImages(copied, original, len(original), 0, 0, 0, None, (), wire_budget_bytes=self.policy.wire_budget_bytes)
            return
        positions, blobs = image_plan(copied)
        existing = count_file_references(copied)
        if existing > self.policy.max_file_references:
            raise FileReferenceLimitExceeded('Request already exceeds the observed private-file reference limit')
        selected = select_image_references(copied, positions, self.policy.max_file_references - existing)
        selected_hashes = {digest for _, digest in selected}
        # Preserve original encounter order for uploads; only selection ranking is
        # by net body savings. Repeated occurrences consume separate reference slots.
        blobs = {digest: blob for digest, blob in blobs.items() if digest in selected_hashes}
        estimate = json.loads(original)
        for (item_index, field_name, content_index), _ in selected:
            part = estimate['input'][item_index][field_name][content_index]
            del part['image_url']; part['file_id'] = 'or_file_' + 'x' * 24
        if self.policy.wire_budget_bytes is not None and len(encode_body(estimate)) > self.policy.wire_budget_bytes:
            raise PayloadBudgetExceeded('Lossless mixed image encoding cannot satisfy the configured wire budget; native compaction is required')
        if not selected:
            yield PreparedImages(copied, original, len(original), len(positions), 0, 0, None, (),
                                 existing_file_references=existing, wire_budget_bytes=self.policy.wire_budget_bytes)
            return
        key = self.credentials() if credential is None else credential
        scope, directory = await self._scope(key)
        lease, mappings, refs = secrets.token_hex(16), {}, []
        uploads = reused = 0
        try:
            for digest, blob in blobs.items():
                fid, uploaded = await self._acquire(directory, key, blob, lease)
                mappings[digest] = fid
                uploads += int(uploaded); reused += int(not uploaded)
                refs.append({'sha256': digest, 'bytes': len(blob.data), 'mime': blob.mime, 'file_id': fid})
            for (item_index, field_name, content_index), digest in selected:
                part = copied['input'][item_index][field_name][content_index]
                del part['image_url']
                part['file_id'] = mappings[digest]
            body = encode_body(copied)
            if self.policy.wire_budget_bytes is not None and len(body) > self.policy.wire_budget_bytes:
                raise PayloadBudgetExceeded('Actual private file IDs leave the body above the configured wire budget; no inference was sent')
            yield PreparedImages(copied, body, len(original), len(positions), uploads, reused, scope, tuple(refs),
                                 _CallLease(self, directory, lease), converted_occurrences=len(selected),
                                 existing_file_references=existing, wire_budget_bytes=self.policy.wire_budget_bytes)
        finally:
            # Cancellation must release only this transport call's pins. It never
            # cancels another request or changes a native thread/tool lifecycle.
            async def release():
                async with self._db(directory) as db:
                    db.execute("DELETE FROM pins WHERE lease=? AND phase!='upstream_unknown'", (lease,))
            task = asyncio.create_task(release())
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
            key = ''

    async def cleanup(self, *, all_unpinned: bool = False, _only_digests: set[str] | None = None, _expected_scope: str | None = None, max_assets: int | None = None) -> dict:
        """Delete only own acknowledged IDs. Caller schedules maintenance/shutdown.

        Workspace files do not expire. TTL is enforced here, not by OpenRouter.
        Other credential scopes cannot be cleaned with the current key; they are
        reported explicitly so rotation cannot silently orphan remote files.
        """
        if max_assets is not None and (type(max_assets) is not int or max_assets < 1):
            raise ValueError('max_assets must be positive')
        key = self.credentials()
        scope, directory = await self._scope(key)
        if _expected_scope is not None and scope != _expected_scope:
            raise ImageTransportError('Credential scope changed before cleanup; original ownership journal remains intact')
        result = {'scope': scope, 'deleted': 0, 'pinned': 0, 'pending': 0,
                  'foreign_scopes': self._foreign_pending_scopes(scope),
                  'unknown_uploads': 0, 'unknown_upstream_calls': 0, 'retained': 0, 'deferred_assets': 0}
        attempted = 0
        async with self._db(directory) as db:
            result['unknown_upstream_calls'] = db.execute("SELECT COUNT(DISTINCT lease) FROM pins WHERE phase='upstream_unknown'").fetchone()[0]
            for row in db.execute("SELECT * FROM assets WHERE state NOT IN ('deleted','upload_rejected')").fetchall():
                if _only_digests is not None and row['digest'] not in _only_digests:
                    continue
                if db.execute("SELECT 1 FROM pins WHERE digest=?", (row['digest'],)).fetchone():
                    result['pinned'] += 1
                    continue
                if row['state'] in {'upload_pending','upload_unknown'} or not row['file_id']:
                    result['pending'] += 1; result['unknown_uploads'] += 1
                    continue
                if not all_unpinned and row['state'] == 'ready' and self.clock() - row['last_used'] < self.policy.idle_ttl_seconds:
                    result['retained'] += 1
                    continue
                if max_assets is not None and attempted >= max_assets:
                    result['deferred_assets'] += 1
                    continue
                attempted += 1
                db.execute("UPDATE assets SET state='delete_pending' WHERE digest=?", (row['digest'],))
                try:
                    # Resolve an ambiguous DELETE by GET first; do not blindly repeat.
                    meta = await self.backend.get(key, row['file_id'])
                    if meta is not None:
                        if not self._matches(row, meta):
                            raise ImageTransportError("Refusing to delete a file whose metadata differs from the owned upload")
                        await self.backend.delete(key, row['file_id'])
                        if await self.backend.get(key, row['file_id']) is not None:
                            raise CleanupPending("Files API delete did not become absent")
                    db.execute("UPDATE assets SET state='deleted' WHERE digest=?", (row['digest'],))
                    self._record(db, row['digest'], 'deleted_confirmed')
                    result['deleted'] += 1
                except BaseException as error:
                    db.execute("UPDATE assets SET state='delete_unknown',reason=? WHERE digest=?", (type(error).__name__, row['digest']))
                    self._record(db, row['digest'], 'delete_unknown', _safe_error_detail(error))
                    result['pending'] += 1
                    if isinstance(error, (asyncio.CancelledError, KeyboardInterrupt, SystemExit)):
                        raise
                    if isinstance(error, FileOperationError):
                        result['halted_error'] = json.loads(_safe_error_detail(error))
                        break  # Stop on rate/auth/server rejection; do not exhaust a shared key.
            result['remaining_assets'] = db.execute("SELECT COUNT(*) FROM assets WHERE state NOT IN ('deleted','upload_rejected')").fetchone()[0]
        result['complete'] = not result['pending'] and not result['foreign_scopes'] and not result['pinned'] and not result['deferred_assets']
        result['all_owned_files_deleted'] = result['complete'] and result['remaining_assets'] == 0
        return result

    async def reconcile_unknown_uploads(self, *, page_size: int = 50, max_pages: int = 4) -> dict:
        # Explicit maintenance only. Never invoked by prepare/normal inference.
        # Files REST exposes no exact filename filter or documented idempotency.
        if type(page_size) is not int or type(max_pages) is not int or not 1 <= page_size <= 1000 or not 1 <= max_pages <= 100 or page_size * max_pages > 1000:
            raise ValueError('Reconciliation must be bounded to at most 1000 metadata entries')
        result = {'scope': None, 'target_count': None, 'pages_read': 0, 'metadata_entries_examined': 0,
                  'nonmatching_entries_discarded': 0, 'enumeration_complete': False,
                  'cleanup_only': True, 'file_contents_read': False, 'foreign_metadata_recorded': False,
                  'inference_requests': 0, 'uploads': 0, 'targets': [], 'cleanup': None, 'complete': False}
        key = self.credentials()
        try:
            scope, directory = await self._scope(key)
            result['scope'] = scope
            async with self._db(directory) as db:
                targets = [dict(row) for row in db.execute("SELECT * FROM assets WHERE state='upload_unknown' AND file_id IS NULL")]
                recoverable = {row['digest'] for row in db.execute("SELECT digest FROM assets WHERE state='delete_unknown' AND file_id IS NOT NULL")}
        except (OSError, sqlite3.Error, ImageTransportError) as error:
            result.update({'state': 'local_state_unavailable', 'error_type': type(error).__name__})
            return result
        result['target_count'] = len(targets)
        result['known_cleanup_count'] = len(recoverable)
        if not targets:
            if not recoverable:
                result.update({'state': 'nothing_to_reconcile', 'complete': True})
                return result
            try:
                result['cleanup'] = await self.cleanup(all_unpinned=True, _only_digests=recoverable, _expected_scope=scope)
                async with self._db(directory) as db:
                    resolved = sum(db.execute('SELECT state FROM assets WHERE digest=?', (digest,)).fetchone()[0] == 'deleted' for digest in recoverable)
                result.update({'state': 'known_cleanup_recovered', 'resolved_count': resolved, 'complete': resolved == len(recoverable)})
            except (OSError, sqlite3.Error, ImageTransportError) as error:
                result.update({'state': 'cleanup_state_failure', 'error': json.loads(_safe_error_detail(error))})
            return result
        names = {}
        for row in targets:
            expected_suffix = row['mime'].split('/')[-1] if isinstance(row['mime'], str) else ''
            if not re.fullmatch(r'cds-image-[0-9a-f]{40}\.(png|jpeg|gif|webp)', row['filename']) or not row['filename'].endswith('.' + expected_suffix) or not re.fullmatch(r'[0-9a-f]{64}', row['digest']) or not 0 < row['nbytes'] <= MAX_UPLOAD_BYTES or not re.fullmatch(r'[0-9a-f]{32}', row['operation_owner'] or '') or row['filename'] in names:
                result.update({'state': 'invalid_owned_upload_journal'})
                return result
            names[row['filename']] = row
        candidates = {row['digest']: {} for row in targets}
        conflicts = set()
        cursor, seen_cursors = None, set()
        try:
            for _ in range(max_pages):
                page = await self.backend.list_page(key, limit=page_size, cursor=cursor)
                if not isinstance(page, FilePage) or len(page.entries) > page_size or type(page.has_more) is not bool:
                    raise ImageTransportError('Files metadata page violated the explicit read bound')
                result['pages_read'] += 1
                for entry in page.entries:
                    result['metadata_entries_examined'] += 1
                    if not isinstance(entry, dict) or not isinstance(entry.get('filename'), str):
                        raise ImageTransportError('Files metadata scan received an invalid entry')
                    row = names.get(entry.get('filename'))
                    if row is None:
                        result['nonmatching_entries_discarded'] += 1
                        continue
                    try:
                        meta = FileMetadata.parse(entry)
                    except ImageTransportError:
                        conflicts.add(row['digest']); continue
                    if not self._matches(row, meta):
                        conflicts.add(row['digest']); continue
                    prior = candidates[row['digest']].get(meta.file_id)
                    if prior is not None and prior != meta:
                        conflicts.add(row['digest'])
                    candidates[row['digest']][meta.file_id] = meta
                if not page.has_more:
                    result['enumeration_complete'] = True
                    break
                if not page.cursor or page.cursor in seen_cursors:
                    raise ImageTransportError('Files metadata cursor did not advance')
                seen_cursors.add(page.cursor)
                cursor = page.cursor
        except (ImageTransportError, OSError, asyncio.TimeoutError) as error:
            result.update({'state': 'metadata_scan_failed', 'error': json.loads(_safe_error_detail(error))})
            return result
        if not result['enumeration_complete']:
            result['state'] = 'metadata_scan_incomplete'
            return result  # One match in a partial scan is not uniqueness proof.
        recognized = set(recoverable)
        for row in targets:
            digest = row['digest']
            matches = list(candidates[digest].values())
            item = {'sha256': digest, 'matches': len(matches), 'state': 'unknown'}
            result['targets'].append(item)
            if digest in conflicts:
                item['state'] = 'metadata_conflict'; continue
            if len(matches) != 1:
                item['state'] = 'not_found' if not matches else 'ambiguous'; continue
            meta = matches[0]
            try:
                current_meta = await self.backend.get(key, meta.file_id)
                if current_meta is not None and current_meta != meta:
                    item['state'] = 'get_metadata_conflict'; continue
                async with self._db(directory) as db:
                    current = db.execute('SELECT * FROM assets WHERE digest=?', (digest,)).fetchone()
                    if current is None or any(current[name] != row[name] for name in ('filename','mime','nbytes','operation_owner','created')) or current['state'] != 'upload_unknown' or current['file_id'] is not None:
                        item['state'] = 'local_journal_changed'; continue
                    # Persist the discovered ID before any destructive request. It
                    # is only eligible for cleanup, never inferred to be content-SHA verified.
                    state = 'deleted' if current_meta is None else 'delete_unknown'
                    db.execute('UPDATE assets SET state=?,file_id=?,reason=? WHERE digest=?',
                               (state, meta.file_id, 'metadata_reconciled_for_cleanup_only', digest))
                    self._record(db, digest, 'unknown_upload_reconciled_for_cleanup', json.dumps({'file_id': meta.file_id, 'metadata_only': True, 'get_absent': current_meta is None}))
                recognized.add(digest)
                item['state'] = 'already_absent' if current_meta is None else 'owned_cleanup_candidate'
            except (ImageTransportError, OSError, sqlite3.Error, asyncio.TimeoutError) as error:
                item.update({'state': 'reconciliation_failed', 'error': json.loads(_safe_error_detail(error))})
                result['state'] = 'reconciliation_stopped'
                return result  # Rate/auth/state errors never fan out across the remaining list.
        if recognized:
            try:
                result['cleanup'] = await self.cleanup(all_unpinned=True, _only_digests=recognized, _expected_scope=scope)
            except (OSError, sqlite3.Error, ImageTransportError) as error:
                result.update({'state': 'cleanup_state_failure', 'error': json.loads(_safe_error_detail(error))})
                return result  # Acknowledged IDs remain journalled for the next GET-first pass.
        try:
            async with self._db(directory) as db:
                states = {digest: db.execute('SELECT state FROM assets WHERE digest=?', (digest,)).fetchone()[0] for digest in ({row['digest'] for row in targets} | recoverable)}
            result['resolved_count'] = sum(state == 'deleted' for state in states.values())
            result['complete'] = result['resolved_count'] == len(states)
            result['state'] = 'resolved' if result['complete'] else 'requires_reconciliation'
        except (OSError, sqlite3.Error, ImageTransportError) as error:
            result.update({'state': 'final_state_unavailable', 'error': json.loads(_safe_error_detail(error))})
        return result

    async def settle_unknown(self, request_ref: str, response_id: str, terminal: str, *, credential: str | None = None, evidence_sha256: str | None = None) -> None:
        # Caller must first verify a provider terminal/generation receipt. Local
        # cancellation, a dead PID or elapsed time are insufficient evidence.
        _validate_reference(request_ref)
        _validate_terminal(response_id, terminal)
        if evidence_sha256 is not None and (not isinstance(evidence_sha256,str) or not re.fullmatch(r"[0-9a-f]{64}",evidence_sha256)):
            raise ImageTransportError("Invalid image reconciliation evidence hash")
        key = credential if credential is not None else self.credentials()
        _, directory = await self._scope(key)
        async with self._db(directory) as db:
            rows = db.execute("SELECT DISTINCT lease,digest FROM pins WHERE request_ref=? AND phase='upstream_unknown'", (request_ref,)).fetchall()
            if not rows:
                raise ImageTransportError("No unknown upstream image lease matches this request")
            for row in rows:
                self._record(db, row['digest'], 'upstream_settled_recovery', json.dumps({'response_id': response_id, 'terminal': terminal, 'evidence_sha256': evidence_sha256}))
            db.execute("DELETE FROM pins WHERE request_ref=? AND phase='upstream_unknown'", (request_ref,))

    def _foreign_pending_scopes(self, current_scope: str) -> list[str]:
        pending = []
        for directory in self.root.iterdir():
            if not directory.is_dir() or directory.name == current_scope:
                continue
            if not re.fullmatch(r"[0-9a-f]{64}", directory.name):
                continue
            database = directory / "files.sqlite"
            if not database.exists():
                continue
            try:
                with closing(sqlite3.connect(_sqlite_filename(database, read_only=True), uri=True)) as db:
                    count = db.execute("SELECT COUNT(*) FROM assets WHERE state NOT IN ('deleted','upload_rejected')").fetchone()[0]
                if count:
                    pending.append(directory.name)
            except sqlite3.Error:
                pending.append(directory.name)  # unreadable is not proof of cleanup
        return sorted(pending)

    async def diagnostics(self, *, private: bool = False, credential: str | None = None) -> dict:
        key = credential if credential is not None else self.credentials()
        scope, directory = await self._scope(key)
        async with self._db(directory) as db:
            rows = [dict(row) for row in db.execute('SELECT * FROM assets')]
            pins = db.execute('SELECT COUNT(*) FROM pins').fetchone()[0]
            unknown = [dict(row) for row in db.execute("SELECT DISTINCT lease,request_ref,pid,birth FROM pins WHERE phase='upstream_unknown'")]
            if not private:
                unknown = [{'phase': 'upstream_unknown'} for _ in unknown]
        if not private:
            for row in rows:
                row.pop('file_id', None); row.pop('filename', None)
        return {'scope': scope, 'assets': rows, 'active_transport_pins': pins, 'unknown_upstream_calls': unknown,
                'foreign_scopes': self._foreign_pending_scopes(scope),
                'raw_images_persisted': False, 'credential_values_persisted': False}


TERMINALS = {'completed', 'failed', 'incomplete', 'cancelled_confirmed'}


def _validate_reference(value: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 256 or any(ord(c) < 32 for c in value):
        raise ImageTransportError("Invalid upstream receipt reference")


def _validate_terminal(response_id: str, terminal: str) -> None:
    _validate_reference(response_id)
    if terminal not in TERMINALS:
        raise ImageTransportError("Only a verified provider terminal can settle an image lease")


class _CallLease:
    def __init__(self, transport: ImageTransport, directory: Path, lease: str):
        self.transport, self.directory, self.lease = transport, directory, lease

    async def start(self, request_ref: str) -> None:
        _validate_reference(request_ref)
        async with self.transport._db(self.directory) as db:
            rows = db.execute('SELECT * FROM pins WHERE lease=?', (self.lease,)).fetchall()
            if not rows or any(row['phase'] != 'prepared' for row in rows):
                raise ImageTransportError("Image upstream request was already started or released")
            if db.execute('SELECT 1 FROM pins WHERE request_ref=?', (request_ref,)).fetchone():
                raise ImageTransportError("Upstream receipt reference is already in use")
            db.execute("UPDATE pins SET phase='upstream_unknown',request_ref=? WHERE lease=?", (request_ref, self.lease))
            for row in rows:
                self.transport._record(db, row['digest'], 'upstream_started', request_ref)

    async def settle(self, response_id: str, terminal: str) -> None:
        _validate_terminal(response_id, terminal)
        await self._finish(json.dumps({'response_id': response_id, 'terminal': terminal}))

    async def rejected(self, status: int) -> None:
        if type(status) is not int or status not in {400,401,403,404,413,415,422,429}:
            raise ImageTransportError("This HTTP status does not prove that image consumption was rejected")
        await self._finish(json.dumps({'http_status': status, 'terminal': 'http_rejected'}))

    async def _finish(self, evidence: str) -> None:
        async with self.transport._db(self.directory) as db:
            rows = db.execute('SELECT * FROM pins WHERE lease=?', (self.lease,)).fetchall()
            if not rows or any(row['phase'] != 'upstream_unknown' for row in rows):
                raise ImageTransportError("Image lease has no unsettled upstream request")
            db.execute("UPDATE pins SET phase='settled' WHERE lease=?", (self.lease,))
            for row in rows:
                self.transport._record(db, row['digest'], 'upstream_settled', evidence)
