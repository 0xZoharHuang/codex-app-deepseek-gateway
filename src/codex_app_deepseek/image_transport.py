"""App image-protocol facade over the pinned CDS pure cache implementation.

Codex App retains its own lifecycle, protocol receipts and HTTP client. Only the
lossless image cache and its metadata/lease types are reused; no CDS provider,
launcher, store, supervisor or native HTTP executor is imported here.
"""
from __future__ import annotations
from pathlib import Path
from io import BytesIO
from typing import Any
from urllib.parse import urlsplit
import asyncio
import json
import os
import re
import sqlite3
import psutil
from codex_deepseek import image_transport as _protocol
from .paths import _is_link_or_reparse

# Keep App imports and exception identity compatible with the shared cache.
ImageTransportError = _protocol.ImageTransportError
UploadOutcomeUnknown = _protocol.UploadOutcomeUnknown
CleanupPending = _protocol.CleanupPending
PayloadBudgetExceeded = _protocol.PayloadBudgetExceeded
FileReferenceLimitExceeded = _protocol.FileReferenceLimitExceeded
ImageSelectionUnresolved = _protocol.ImageSelectionUnresolved
FileOperationError = _protocol.FileOperationError
FileMetadata = _protocol.FileMetadata
FilePage = _protocol.FilePage
FilesBackend = _protocol.FilesBackend
TransportPolicy = _protocol.TransportPolicy
PreparedImages = _protocol.PreparedImages
MAX_UPLOAD_BYTES = _protocol.MAX_UPLOAD_BYTES
MAX_REFERENCED_FILE_BYTES = _protocol.MAX_REFERENCED_FILE_BYTES
FILE_ID = _protocol.FILE_ID
DATA_URL = _protocol.DATA_URL
TERMINALS = _protocol.TERMINALS
V3_SALT_FENCE = _protocol.V3_SALT_FENCE
ImageBudgetPlan = _protocol.ImageBudgetPlan
encode_body = _protocol.encode_body
image_plan = _protocol.image_plan
count_file_references = _protocol.count_file_references
select_image_references = _protocol.select_image_references
plan_image_budget = _protocol.plan_image_budget
_FileResponse = _protocol._FileResponse
_Blob = _protocol._Blob
_CallLease = _protocol._CallLease
_strict_json = _protocol._strict_json
_safe_error_detail = _protocol._safe_error_detail
_safe_budget_diagnostics = _protocol._safe_budget_diagnostics
_private_directory = _protocol._private_directory
_file_lock = _protocol._file_lock
_ledger_transaction = _protocol._ledger_transaction
_owner_alive = _protocol._owner_alive
_validate_reference = _protocol._validate_reference
_validate_terminal = _protocol._validate_terminal

class ImageTransport(_protocol.ImageTransport):
    """Retain the App's pre-constructor path guard; cache mechanisms stay shared."""
    def __init__(self, cache_root, credentials, backend, policy, **options):
        root = Path(cache_root)
        if any(_is_link_or_reparse(item) for item in (root, *root.parents)):
            raise ImageTransportError("Image transport directory cannot be a link or junction")
        super().__init__(root, credentials, backend, policy, **options)

# The App gateway supplies its existing aiohttp session and owns its lifetime.
# Preserve its streamed FormData/BytesIO backend rather than importing CDS HTTP IPC.

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
