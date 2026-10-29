"""Durable hash-only diagnostics must not alter native completion authority."""
import copy
import json
from types import SimpleNamespace

import pytest

from codex_app_deepseek.protocol_state import ProtocolState


def record():
    return {"reason": "producer_append_constraint_violation",
            "item_sha256": "1" * 64, "call_sha256": "2" * 64,
            "preview_arguments_sha256": "3" * 64, "candidate_arguments_sha256": "4" * 64,
            "hash_encoding": "utf8-surrogatepass"}


def summary(count=1):
    return {"total_count": count, "truncated": count > 16,
            "records": [record() for _ in range(min(count, 16))]}


def start(state):
    return state.start(requested_model="main", requested_upstream_model="deepseek",
                       requested_provider="deepseek", route_reason="official_default",
                       adaptations={"existing_control": {"retained": True}})


def events(value):
    return SimpleNamespace(terminal=True, status="completed", done={}, response_id="resp_diag",
                           usage={}, diagnostic_summary=lambda: copy.deepcopy(value))


@pytest.mark.parametrize("status", ["completed", "cancelled", "adapter_error"])
@pytest.mark.parametrize("count", [0, 1, 16, 17, 101])
def test_summary_durable_and_existing_adaptations_preserved(tmp_path, status, count):
    path = tmp_path / "state.sqlite"
    with ProtocolState(path) as state:
        key = start(state)
        state.image_transport(key, consumption_status="preparing")
        value = summary(count)
        assert state.finish(key, status, 200, events(value))
        assert state.db.execute("PRAGMA user_version").fetchone()[0] == 3
        receipt = state.receipt(key)
        assert receipt["status"] == status
        expected_adaptations = {"existing_control": {"retained": True},
                                "image_transport": {"consumption_status": "preparing"}}
        if count:
            expected_adaptations["tool_argument_diagnostics"] = value
        assert receipt["adaptations"] == expected_adaptations
        assert state.db.execute("SELECT COUNT(*) FROM completed_native_evidence").fetchone()[0] == 0
        value["records"].clear()
        stored = copy.deepcopy(receipt["adaptations"])
        assert not state.finish(key, "cancelled", 200, events(summary(2)))
        assert state.receipt(key)["adaptations"] == stored
    with ProtocolState(path) as reopened:
        assert reopened.receipt(key)["adaptations"] == stored


def test_older_events_and_missing_events_do_not_fabricate_observations(tmp_path):
    with ProtocolState(tmp_path / "state.sqlite") as state:
        for event in [None, SimpleNamespace(response_id=None, usage={})]:
            key = start(state)
            assert state.finish(key, "upstream_error", 429, event)
            assert "tool_argument_diagnostics" not in state.receipt(key)["adaptations"]


@pytest.mark.parametrize("change", [
    lambda x: x.update(total_count=True),
    lambda x: x.update(total_count=-1),
    lambda x: x.update(total_count=17, truncated=False),
    lambda x: x.update(records=[]),
    lambda x: x.update(truncated="true"),
    lambda x: x.update(unknown="DO_NOT_PERSIST_RAW"),
    lambda x: x["records"][0].update(input="DO_NOT_PERSIST_RAW"),
    lambda x: x["records"][0].update(reason="DO_NOT_PERSIST_RAW"),
    lambda x: x["records"][0].update(item_sha256="raw item id"),
    lambda x: x["records"][0].update(call_sha256="A" * 64),
    lambda x: x["records"][0].update(hash_encoding="utf8"),
])
def test_invalid_diagnostic_contract_never_persists_raw_or_changes_outcome(tmp_path, change):
    with ProtocolState(tmp_path / "state.sqlite") as state:
        key = start(state)
        value = summary()
        change(value)
        with pytest.raises(ValueError, match="Invalid tool-argument diagnostic"):
            state.finish(key, "completed", 200, events(value))
        receipt = state.receipt(key)
        assert receipt["status"] == "running"
        assert "DO_NOT_PERSIST_RAW" not in json.dumps(receipt)
        assert state.db.execute("SELECT COUNT(*) FROM completed_native_evidence").fetchone()[0] == 0
        assert state.finish(key, "adapter_error", 502)
