from __future__ import annotations
import asyncio
import importlib.util
import json
from pathlib import Path
import sqlite3
import sys
import pytest
from codex_app_deepseek.web_backend import WebSourceStore


def fault_module(name="fault_server"):
    path = Path(__file__).parent / "fixtures" / "app_faults" / (name + ".py")
    module_name = "cdag_resource_test_" + name
    spec = importlib.util.spec_from_file_location(module_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


def assert_closed(db):
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        db.execute("SELECT 1")


def test_web_store_constructor_failure_closes_actual_sqlite_connection(tmp_path, monkeypatch):
    path = tmp_path / "malformed.sqlite"; path.write_bytes(b"not a sqlite database")
    opened = []
    original = sqlite3.connect
    def connect(*args, **kwargs):
        db = original(*args, **kwargs); opened.append(db); return db
    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises(sqlite3.DatabaseError):
        WebSourceStore(path)
    assert len(opened) == 1
    assert_closed(opened[0])


def test_counter_constructor_permission_failure_closes_connection(tmp_path, monkeypatch):
    module = fault_module()
    home = tmp_path / "counter"; home.mkdir()
    opened = []; original = sqlite3.connect
    def connect(*args, **kwargs):
        db = original(*args, **kwargs); opened.append(db); return db
    def denied(*args, **kwargs):
        raise PermissionError("synthetic fixture permission failure")
    monkeypatch.setattr(sqlite3, "connect", connect)
    monkeypatch.setattr(module, "_private", denied)
    with pytest.raises(PermissionError):
        module.CounterLedger(home)
    assert len(opened) == 1
    assert_closed(opened[0])


@pytest.mark.parametrize("saved", ["not json", "[]", '{"events":[1],"nonce_sha256":"bad"}'])
def test_fault_constructor_invalid_history_closes_both_databases_and_lock(tmp_path, monkeypatch, saved):
    module = fault_module()
    home = tmp_path / "fault"; module.initialize(home)
    (home / "events.json").write_text(saved)
    opened = []; original = sqlite3.connect
    def connect(*args, **kwargs):
        db = original(*args, **kwargs); opened.append(db); return db
    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises((ValueError, AttributeError)):
        module.FaultHarness(home)
    assert len(opened) == 2
    for db in opened: assert_closed(db)
    state = module.ProtocolState(home / "protocol.sqlite")
    state.close()  # Its exact ownership lock must have been released.


def test_fault_constructor_second_database_failure_closes_first(tmp_path, monkeypatch):
    module = fault_module()
    home = tmp_path / "fault"; module.initialize(home)
    opened = []; original = sqlite3.connect
    def connect(*args, **kwargs):
        db = original(*args, **kwargs); opened.append(db); return db
    def denied(*args, **kwargs): raise RuntimeError("synthetic second database failure")
    monkeypatch.setattr(sqlite3, "connect", connect)
    monkeypatch.setattr(module, "ProtocolState", denied)
    with pytest.raises(RuntimeError, match="second database"):
        module.FaultHarness(home)
    assert len(opened) == 1
    assert_closed(opened[0])


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_fault_close_attempts_every_resource_after_failure_or_cancel(tmp_path, failure):
    module = fault_module()
    home = tmp_path / "fault"; module.initialize(home)
    harness = module.FaultHarness(home)
    closed = []
    class Runner:
        def __init__(self, name, fails=False): self.name, self.fails = name, fails
        async def cleanup(self):
            closed.append(self.name)
            if self.fails: raise failure("synthetic close failure")
    class Client:
        async def close(self): closed.append("client")
    harness.runners = [Runner("first"), Runner("last", True)]
    harness.client = Client()
    with pytest.raises(failure):
        await harness.close()
    assert closed == ["last", "first", "client"]
    assert_closed(harness.state.db)
    assert_closed(harness.ledger.db)
    state = module.ProtocolState(home / "protocol.sqlite"); state.close()


def test_guardian_second_state_read_failure_closes_parent_resources(tmp_path, monkeypatch):
    module = fault_module("guardian_negative")
    module.check_core()
    home = tmp_path / "guardian"; module.initialize(home)
    opened = []; original_connect = sqlite3.connect
    def connect(*args, **kwargs):
        db = original_connect(*args, **kwargs); opened.append(db); return db
    original_load = module.load; calls = []
    def load(*args):
        calls.append(True)
        if len(calls) > 1: raise ValueError("synthetic changed Guardian file")
        return original_load(*args)
    monkeypatch.setattr(sqlite3, "connect", connect)
    monkeypatch.setattr(module, "load", load)
    with pytest.raises(ValueError, match="changed Guardian"):
        module.GuardianHarness(home)
    assert len(opened) == 2
    for db in opened: assert_closed(db)
    state = module.core.ProtocolState(home / "protocol.sqlite"); state.close()
