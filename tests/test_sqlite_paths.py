"""Real Windows SQLite sidecars and read-only URIs at long logical paths."""
from contextlib import closing
import hashlib
import os
from pathlib import Path
import sqlite3
from urllib.parse import unquote

import pytest

from codex_app_deepseek.image_call_reconciliation import read_project_receipt
from codex_app_deepseek.image_transport import ImageTransport, TransportPolicy
from codex_app_deepseek.paths import _sqlite_filename
from codex_app_deepseek.protocol_state import ProtocolState
from codex_app_deepseek.web_backend import WebSourceStore

pytestmark = pytest.mark.skipif(os.name != "nt", reason="Real Windows SQLite VFS and sidecar limits")


def directory_with_length(base, length, *, extended=False):
    path = Path(base)
    # Keep each component ordinary while retaining the requested full length.
    assert length > len(str(path)) + 1
    while length - len(str(path)) - 1 > 150:
        path /= "d" * 100
    path /= "d" * (length - len(str(path)) - 1)
    assert len(str(path)) == length
    if extended:
        path = Path(_sqlite_filename(path))
    path.mkdir(parents=True)
    return path


def database_path(tmp_path, length, extended):
    name = "資料 #100% & test.sqlite"
    parent = directory_with_length(tmp_path, length - len(name) - 1, extended=extended)
    path = parent / name
    assert len(str(path)) == length + (4 if extended else 0)
    return path


def assert_readonly_write_refused(path):
    before = hashlib.sha256(path.read_bytes()).hexdigest()
    uri = _sqlite_filename(path, read_only=True)
    assert uri.count("?") == 1 and uri.endswith("?mode=ro")
    assert unquote(uri[5:-8]) == _sqlite_filename(path)
    with closing(sqlite3.connect(uri, uri=True)) as db:
        assert db.execute("PRAGMA user_version").fetchone() is not None
        with pytest.raises(sqlite3.OperationalError) as caught:
            db.execute("CREATE TABLE must_remain_readonly(value)")
        assert caught.value.sqlite_errorcode == sqlite3.SQLITE_READONLY
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before


@pytest.mark.parametrize("length,extended", [(253, False), (259, False), (273, True)],
                         ids=["ordinary-db253", "ordinary-db259", "extended-db273"])
def test_protocol_long_path_reopen_and_readonly_receipt(tmp_path, length, extended):
    path = database_path(tmp_path, length, extended)
    assert len(str(path) + "-journal") > 260
    with ProtocolState(path) as state:
        assert state.path == path
        with pytest.raises(RuntimeError, match="Another gateway owns this protocol database"):
            ProtocolState(path)
        key = state.start(requested_model="fixture", requested_upstream_model="fixture",
                          requested_provider="fixture", route_reason="local_test",
                          adaptations={"longpath_control": True})
        assert state.finish(key, "adapter_error", 500)
    with ProtocolState(path) as reopened:
        assert reopened.path == path
        assert reopened.receipt(key)["status"] == "adapter_error"
    receipt = read_project_receipt(path, key)
    assert receipt["id"] == key and receipt["status"] == "adapter_error"
    assert receipt["adaptations"] == {"longpath_control": True}
    assert_readonly_write_refused(path)


@pytest.mark.parametrize("length,extended", [(253, False), (259, False), (273, True)],
                         ids=["ordinary-db253", "ordinary-db259", "extended-db273"])
def test_web_store_long_path_wal_commit_and_reopen(tmp_path, length, extended):
    path = database_path(tmp_path, length, extended)
    raw = "Unicode source資料".encode()
    source = {"url": "https://example.invalid/owned-fixture", "title": "資料",
              "source_sha256": hashlib.sha256(raw).hexdigest(), "lines": ["fixture only"]}
    store = WebSourceStore(path)
    try:
        assert store.path == path
        saved = store.put("longpath-session", source, raw)
    finally:
        store.close()
    reopened = WebSourceStore(path)
    try:
        assert reopened.path == path
        assert reopened.get("longpath-session", saved["ref_id"])["title"] == "資料"
        assert reopened.raw("longpath-session", saved["ref_id"]) == raw
    finally:
        reopened.close()
    assert_readonly_write_refused(path)


@pytest.mark.asyncio
@pytest.mark.parametrize("length,extended", [(253, False), (259, False), (273, True)],
                         ids=["ordinary-db253", "ordinary-db259", "extended-db273"])
async def test_image_cache_long_path_transaction_and_foreign_readonly_scan(tmp_path, length, extended):
    root = directory_with_length(tmp_path, length - 1 - 64 - 1 - len("files.sqlite"), extended=extended)
    class NoFilesBackend:
        def __getattr__(self, name):
            raise AssertionError("No external file operation is part of this SQLite test: " + name)
    transport = ImageTransport(root, lambda: "synthetic-only", NoFilesBackend(),
                               TransportPolicy(activate_above_bytes=1))
    scope, directory = await transport._scope("synthetic-only")
    path = directory / "files.sqlite"
    assert transport.root == root and len(str(path)) == length + (4 if extended else 0)
    async with transport._db(directory) as db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("INSERT INTO assets(digest,mime,nbytes,filename,state,created,last_used,verified) "
                   "VALUES(?,?,?,?,?,?,?,?)", ("a" * 64, "image/png", 1, "fixture.png", "ready", 0, 0, 0))
        db.execute("COMMIT")
    other_scope, _ = await transport._scope("second-synthetic-only")
    assert transport._foreign_pending_scopes(other_scope) == [scope]
    async with transport._db(directory) as db:
        assert db.execute("SELECT state FROM assets").fetchone()[0] == "ready"
        db.execute("BEGIN IMMEDIATE")
        db.execute("UPDATE assets SET state='upload_rejected'")
        db.execute("COMMIT")
    # The old RO connection failure conservatively reported this scope pending.
    # A clean empty result therefore proves the long-path RO query succeeded.
    assert transport._foreign_pending_scopes(other_scope) == []
    assert_readonly_write_refused(path)


def test_sqlite_windows_formatting_preserves_extended_and_unc_names(tmp_path, monkeypatch):
    extended = "\\\\?\\" + str(tmp_path / "already #100%.sqlite")
    assert _sqlite_filename(extended) == extended
    assert _sqlite_filename(r"\\server\share\資料 #100%.sqlite") == r"\\?\UNC\server\share\資料 #100%.sqlite"
    monkeypatch.chdir(tmp_path)
    relative = Path("unused") / ".." / "relative.sqlite"
    assert _sqlite_filename(relative) == "\\\\?\\" + str(tmp_path / "relative.sqlite")
