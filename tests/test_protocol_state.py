from contextlib import closing
import json

import pytest

from codex_deepseek.protocol import Events, MODEL, ProtocolError, native_history_fingerprint, prepare
from codex_app_deepseek.protocol_state import ProtocolState


def start(state):
    return state.start(requested_model="guardian", requested_upstream_model=MODEL, requested_provider="deepseek",
                       route_reason="official_default", context_key="thread-not-a-secret")


def complete_events(state):
    _, registry = prepare({"model": MODEL, "tools": [
        {"type": "function", "name": "sample", "parameters": {"type": "object"}}]}, state)
    events = Events(registry)
    item = {"id": "fc_one", "call_id": "call_one", "type": "function_call",
            "name": "sample", "arguments": "{}", "status": "completed"}
    events.transform({"type": "response.completed", "response": {
        "id": "resp_one", "status": "completed", "output": [item], "usage": {"input_tokens": 12}}})
    return events, item


def test_search_evidence_survives_reopen_and_is_immutable(tmp_path):
    path = tmp_path / "protocol.sqlite"
    item = {"id": "s1", "type": "openrouter:web_search", "status": "completed", "action": {"query": "test"}}
    with ProtocolState(path) as state:
        state.put("ws_s1", item)
        assert state.get("ws_s1") == item
        with pytest.raises(ProtocolError, match="different evidence"):
            state.put("ws_s1", {**item, "action": {"query": "other"}})
    with ProtocolState(path) as state:
        assert state.get("ws_s1") == item


def test_completed_receipt_attests_real_item_atomically(tmp_path):
    with ProtocolState(tmp_path / "protocol.sqlite") as state:
        request_id = start(state)
        events, item = complete_events(state)
        fingerprint = native_history_fingerprint(item)
        assert not state.has_completed_native_item(fingerprint, item_id=item["id"])
        state.progress(request_id, events)
        assert state.finish(request_id, "completed", 200, events)
        assert state.has_completed_native_item(fingerprint, item_id=item["id"])
        assert not state.finish(request_id, "cancelled", 200, events)
        receipt = state.receipt(request_id)
        assert receipt["status"] == "completed"
        assert receipt["response_id"] == "resp_one"
        assert receipt["requested_model"] == "guardian"
        assert receipt["requested_upstream_model"] == MODEL
        assert receipt["usage"]["input_tokens"] == 12


def test_cancellation_never_creates_attestation_or_overwrites_outcome(tmp_path):
    with ProtocolState(tmp_path / "protocol.sqlite") as state:
        request_id = start(state)
        events, item = complete_events(state)
        assert state.finish(request_id, "cancelled", 200, events)
        assert not state.finish(request_id, "completed", 200, events)
        assert not state.has_completed_native_item(native_history_fingerprint(item), item_id=item["id"])
        assert state.receipt(request_id)["status"] == "cancelled"


def test_unverified_terminal_receipt_rejected(tmp_path):
    with ProtocolState(tmp_path / "protocol.sqlite") as state:
        request_id = start(state)
        with pytest.raises(ValueError, match="matching validated"):
            state.finish(request_id, "completed", 200)
        assert state.receipt(request_id)["status"] == "running"


def test_exclusive_owner_does_not_touch_other_owner_receipt(tmp_path):
    path = tmp_path / "protocol.sqlite"
    with ProtocolState(path) as first:
        key = start(first)
        with pytest.raises(RuntimeError, match="Another gateway"):
            ProtocolState(path)
        assert first.receipt(key)["status"] == "running"


def test_shutdown_and_lost_owner_are_honest_without_replay(tmp_path):
    path = tmp_path / "protocol.sqlite"
    state = ProtocolState(path)
    orderly = start(state)
    state.close()
    with ProtocolState(path) as next_state:
        assert next_state.receipt(orderly)["status"] == "owner_closed"
        lost = start(next_state)
        # Simulate OS cleanup after a crash; no native PID or session is involved.
        next_state.db.close()
        next_state._release_lock()
        next_state._closed = True
    with ProtocolState(path) as restored:
        assert restored.receipt(lost)["status"] == "owner_lost"
        assert "unknown" in restored.receipt(lost)["detail"]
        assert restored.list_receipts()[0]["id"] == lost


def test_no_process_or_rollout_dependencies():
    import inspect
    import codex_app_deepseek.protocol_state as module
    source = inspect.getsource(module)
    assert "from .processes" not in source
    assert "codex_deepseek.state" not in source
    assert "subprocess" not in source


def test_version_one_receipt_migrates_without_losing_previous_outcome(tmp_path):
    import sqlite3
    path = tmp_path / "protocol.sqlite"
    with ProtocolState(path) as state:
        key = start(state)
        state.finish(key, "cancelled")
    with closing(sqlite3.connect(path)) as database, database:
        database.execute("ALTER TABLE receipts DROP COLUMN adaptations")
        database.execute("ALTER TABLE receipts RENAME COLUMN requested_upstream_model TO actual_model")
        database.execute("ALTER TABLE receipts RENAME COLUMN requested_provider TO provider")
        database.execute("ALTER TABLE receipts DROP COLUMN observed_model")
        database.execute("ALTER TABLE receipts DROP COLUMN observed_provider")
        database.execute("DROP TABLE completed_native_evidence")
        database.execute("PRAGMA user_version=1")
    with ProtocolState(path) as state:
        receipt = state.receipt(key)
        assert receipt["status"] == "cancelled"
        assert receipt["requested_model"] == "guardian"
        assert receipt["adaptations"] == {}
        assert state.db.execute("PRAGMA user_version").fetchone()[0] == 3


def test_request_scoped_evidence_requires_every_original_identity(tmp_path):
    import copy
    reasoning = {"type": "reasoning", "id": "rs_original",
                 "content": [{"type": "reasoning_text", "text": "same semantic text"}]}
    with ProtocolState(tmp_path / "protocol.sqlite") as state:
        key = start(state)
        _, registry = prepare({"model": MODEL}, state.evidence_view([]))
        events = Events(registry)
        events.transform({"type": "response.completed", "response": {
            "id": "resp_source", "status": "completed", "output": [reasoning]}})
        state.finish(key, "completed", 200, events)
        fingerprint = native_history_fingerprint(reasoning)
        assert not state.has_completed_native_item(fingerprint)
        assert state.has_completed_native_item(fingerprint, item_id=reasoning["id"])
        row = state.db.execute("SELECT * FROM completed_native_evidence").fetchone()
        assert row["receipt_id"] == key and row["response_id"] == "resp_source"

        def route(items, evidence_items=None):
            history = [*items,
                {"type": "message", "role": "assistant", "content": "answer"},
                {"type": "message", "role": "user", "content": "Continue"}]
            body = {"model": MODEL, "input": history, "reasoning": {"effort": "high"},
                    "tools": [{"type": "web_search", "external_web_access": True}]}
            view = state.evidence_view(history if evidence_items is None else evidence_items)
            payload, _ = prepare(body, view)
            return payload["provider"]["only"][0]

        # Full-history fork retains original item identities and remains attested.
        assert route(copy.deepcopy([reasoning])) == "deepseek"
        other = {**reasoning, "id": "rs_other_thread"}
        assert route([other]) == "morph/fp8"
        assert route([reasoning, other]) == "morph/fp8"
        missing = {name: value for name, value in reasoning.items() if name != "id"}
        assert route([missing]) == "morph/fp8"
        # A globally known fingerprint cannot attest an unrelated request view.
        assert route([reasoning], evidence_items=[]) == "morph/fp8"


def test_legacy_semantic_evidence_never_attests_an_item_identity(tmp_path):
    with ProtocolState(tmp_path / "protocol.sqlite") as state:
        item = {"type": "reasoning", "id": "legacy-rs",
                "content": [{"type": "reasoning_text", "text": "legacy same words"}]}
        fingerprint = native_history_fingerprint(item)
        with state.db:
            state.db.execute("INSERT INTO completed_native_items VALUES (?,?)",
                             (fingerprint, "legacy-receipt"))
        assert not state.evidence_view([item]).has_completed_native_item(fingerprint)
        assert not state.has_completed_native_item(fingerprint, item_id="legacy-rs")
