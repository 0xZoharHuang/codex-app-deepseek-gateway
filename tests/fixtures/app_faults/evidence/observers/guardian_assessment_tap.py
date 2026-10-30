"""Private, bounded observer of original Guardian response events.
No auth, provider selection, verdict generation, tool execution or response engine.
"""
from contextlib import contextmanager
import contextvars
import hashlib
import json
import re
from pathlib import Path
import time
from codex_app_deepseek.app_config import _private
from codex_app_deepseek.atomic_io import atomic_write

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "properties": {
        "risk_level": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
        "user_authorization": {"type": "string", "enum": ["unknown", "low", "medium", "high"]},
        "outcome": {"type": "string", "enum": ["allow", "deny"]},
        "rationale": {"type": "string"},
    },
    "required": ["outcome"],
}
MAX_RECORDS = 8
MAX_TEXT_BYTES = 16384

def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()

def digest(value):
    return hashlib.sha256(value).hexdigest()

def parsed_assessment(text):
    """Record literal JSON fields; never supply the native parser's defaults."""
    raw = text.encode("utf-8")
    result = {"text_sha256": digest(raw), "text_bytes": len(raw), "parse_candidate_valid": False}
    if len(raw) > MAX_TEXT_BYTES:
        result["observation_error"] = "message_text_bound"
        return result
    wrapped = False
    try:
        value = json.loads(text)
    except ValueError:
        first, last = text.find("{"), text.rfind("}")
        if first < 0 or first >= last:
            return result
        try:
            value = json.loads(text[first:last + 1])
        except ValueError:
            return result
        wrapped = True
    if not isinstance(value, dict):
        return result
    outcome = value.get("outcome")
    risk = value.get("risk_level")
    authorization = value.get("user_authorization")
    rationale = value.get("rationale")
    valid = (outcome in ("allow", "deny")
             and risk in (None, "low", "medium", "high", "critical")
             and authorization in (None, "unknown", "low", "medium", "high")
             and (rationale is None or isinstance(rationale, str)))
    result.update(
        parse_candidate_valid=valid,
        literal_outcome=outcome if outcome in ("allow", "deny") else None,
        literal_risk_level=risk if risk in (None, "low", "medium", "high", "critical") else None,
        literal_user_authorization=authorization if authorization in (None, "unknown", "low", "medium", "high") else None,
        field_presence={key: key in value for key in SCHEMA["properties"]},
        rationale_sha256=digest(rationale.encode()) if isinstance(rationale, str) else None,
        rationale_bytes=len(rationale.encode()) if isinstance(rationale, str) else None,
        native_prose_wrapper_recovery=wrapped,
        unknown_field_count=len(set(value) - set(SCHEMA["properties"])),
    )
    return result

TRANSCRIPT_ENDS = (">>> TRANSCRIPT END\n", ">>> TRANSCRIPT DELTA END\n")
PARENT = re.compile(r"Reviewed Codex session id: ([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\n")
ACTION_START = ">>> APPROVAL REQUEST START\n"
ACTION_JSON = "Planned action JSON:\n"
ACTION_END = ">>> APPROVAL REQUEST END\n"

def parent_action_binding(payload):
    """Read only source-generated independent blocks in the current user message."""
    items = payload.get("input")
    if not isinstance(items, list):
        return None
    users = [item for item in items if isinstance(item, dict) and item.get("role") == "user"]
    if not users or not isinstance(users[-1].get("content"), list):
        return None
    blocks = users[-1]["content"]
    def text(index):
        value = blocks[index]
        if isinstance(value, dict) and value.get("type") == "input_text" and isinstance(value.get("text"), str):
            return value["text"]
        return None
    parents = []
    for index in range(1, len(blocks)):
        value = text(index)
        match = PARENT.fullmatch(value) if isinstance(value, str) else None
        if match is not None and text(index - 1) in TRANSCRIPT_ENDS:
            parents.append((index, match.group(1), value))
    if len(parents) != 1:
        return None
    parent_index, parent_id, parent_block = parents[0]
    starts = [index for index in range(parent_index + 1, len(blocks)) if text(index) == ACTION_START]
    if len(starts) != 1 or text(len(blocks) - 1) != ACTION_END:
        return None
    start = starts[0]
    labels = [index for index in range(start + 1, len(blocks)) if text(index) == ACTION_JSON]
    if len(labels) != 1 or labels[0] + 2 != len(blocks) - 1:
        return None
    action_text = text(labels[0] + 1)
    if not isinstance(action_text, str) or len(action_text.encode()) > MAX_TEXT_BYTES:
        return None
    try:
        action = json.loads(action_text)
    except ValueError:
        return None
    if not isinstance(action, dict) or action.get("tool") != "exec_command":
        return None
    command = action.get("command")
    if (not isinstance(command, list) or not command
            or not all(isinstance(part, str) for part in command)
            or not isinstance(action.get("cwd"), str)):
        return None
    return {
        "parent_session_id": parent_id,
        "source_parent_marker_sha256": digest(parent_block.encode()),
        "source_transcript_end": text(parent_index - 1),
        "current_action_json_sha256": digest(canonical(action)),
        "current_action_text_sha256": digest(action_text.encode()),
        "current_action_tool": action["tool"],
        "current_command_vector_sha256": digest(canonical(command)),
        "current_command_last_segment_sha256": digest(command[-1].encode()),
        "current_cwd_sha256": digest(action["cwd"].encode()),
        "current_sandbox_permissions": action.get("sandbox_permissions"),
        "current_tty": action.get("tty"),
        "wire_contains_native_review_or_target_ids": any(key in action for key in ("id", "review_id", "approval_id", "target_item_id")),
        "native_environment_id_present": "environment_id" in action,
        "native_environment_id_sha256": digest(canonical(action["environment_id"])) if "environment_id" in action else None,
    }

class Tap:
    def __init__(self, core, output, deadline):
        if getattr(core, "_private_guardian_assessment_tap", None) is not None:
            raise ValueError("One private assessment observer per fixture process")
        self.core = core
        self.original = core.sse_events
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=False)
        _private(self.output, directory=True)
        self.deadline = deadline
        self.context = contextvars.ContextVar("private-guardian-response-tap", default=None)
        self.coverage = {"schema": "original-Guardian-model-response-observation/v1", "records": 0,
                         "status": "complete_so_far", "failures": [],
                         "model_verdicts_generated": False, "original_events_mutated": False,
                         "original_deadline": deadline, "max_records": MAX_RECORDS,
                         "max_message_text_bytes": MAX_TEXT_BYTES}
        original = self.original
        async def observed(content, maximum):
            async for event in original(content, maximum):
                record = self.context.get()
                if record is not None:
                    try:
                        self.observe(record, event)
                    except Exception as error:
                        self.fail(type(error).__name__)
                yield event
        self.observed = observed
        self.save("coverage.json", self.coverage)
        core.sse_events = observed
        core._private_guardian_assessment_tap = self

    def save(self, name, value):
        atomic_write(self.output / name, json.dumps(value, indent=2).encode() + b"\n", prepare_temp=_private)

    def fail(self, reason):
        self.coverage["status"] = "FAILED_INCONCLUSIVE"
        if len(self.coverage["failures"]) < 16:
            self.coverage["failures"].append(reason)
        try:
            self.save("coverage.json", self.coverage)
        except Exception:
            pass

    def candidate(self, request, payload, row, state):
        expected = state.get("thread_id")
        fmt = payload.get("text", {}).get("format", {}) if isinstance(payload.get("text", {}), dict) else {}
        if not expected or state.get("status") not in ("armed", "consumed") or fmt.get("schema") != SCHEMA:
            return None
        if fmt.get("type") != "json_schema":
            return None
        binding = parent_action_binding(payload)
        if binding is None or binding["parent_session_id"] != expected:
            return None
        actual_native_thread = request.headers.get("X-CDAG-QA-Thread-ID")
        if not isinstance(actual_native_thread, str) or not actual_native_thread:
            return None
        if not isinstance(row, dict) or row.get("body_observation") != "complete":
            self.fail("missing_complete_native_request_observation")
            return None
        if (row.get("thread_sha256") != digest(actual_native_thread.encode())
                or type(row.get("physical_request_index")) is not int
                or row["physical_request_index"] < 1 or not row.get("raw_body_sha256")):
            self.fail("native_root_or_physical_request_unproved")
            return None
        if self.coverage["records"] >= MAX_RECORDS or time.time() >= self.deadline:
            self.fail("original_observation_budget")
            return None
        self.coverage["records"] += 1
        return {"index": self.coverage["records"], "started": time.time(),
                "declared_outer_root_sha256": digest(expected.encode()),
                "actual_reviewer_native_thread_sha256": digest(actual_native_thread.encode()),
                "parent_action_source_binding": {key: value for key, value in binding.items() if key != "parent_session_id"},
                "native_parent_session_sha256": digest(binding["parent_session_id"].encode()),
                "native_review_id_or_target_item_id_not_present_in_wire": not binding["wire_contains_native_review_or_target_ids"],
                "physical_request_index": row["physical_request_index"],
                "native_raw_body_sha256": row["raw_body_sha256"],
                "native_outer_QA_request_id": row.get("outerQARequestID"),
                "upstream_trace_ids_at_start": list(row.get("upstreamRoleRequestIDs", [])),
                "native_observer_epoch": row.get("epoch"),
                "actual_guardian_schema_sha256": digest(canonical(fmt["schema"])),
                "case_id_sha256": digest(state["case_id"].encode()),
                "fixture_injected_outer_call_id_sha256": digest(state["call_id"].encode()) if isinstance(state.get("call_id"), str) else None,
                "fixture_phase_at_request_start": state["status"],
                "generation_id": None, "messages": [], "upstream_terminal_completed": False,
                "upstream_failed": False, "native_decision_observed_by_this_tap": False}

    @contextmanager
    def scope(self, request, payload, row, state):
        try:
            record = self.candidate(request, payload, row, state)
        except Exception as error:
            self.fail("candidate_" + type(error).__name__)
            record = None
        token = self.context.set(record)
        try:
            yield
        finally:
            self.context.reset(token)
            if record is not None:
                record["finished"] = time.time()
                record["finished_within_original_deadline"] = record["finished"] < self.deadline
                record["scope"] = "Literal real-upstream model payload; native parsed decision must still be independently joined."
                try:
                    self.save("response-%03d.json" % record["index"], record)
                    self.save("coverage.json", self.coverage)
                except Exception as error:
                    self.fail("write_" + type(error).__name__)

    def message(self, record, item):
        if not isinstance(item, dict) or item.get("type") != "message" or item.get("role") != "assistant":
            return
        blocks = item.get("content")
        if not isinstance(blocks, list):
            return
        texts = [block["text"] for block in blocks if isinstance(block, dict)
                 and block.get("type") == "output_text" and isinstance(block.get("text"), str)]
        if not texts:
            return
        fingerprint = digest(canonical(item))
        if any(message["item_sha256"] == fingerprint for message in record["messages"]):
            return
        if len(record["messages"]) >= 4:
            self.fail("message_count_bound")
            return
        parsed = parsed_assessment("".join(texts))
        if parsed.get("observation_error"):
            self.fail(parsed["observation_error"])
        record["messages"].append({"item_id": item.get("id"), "item_sha256": fingerprint,
                                   **parsed})

    def observe(self, record, event):
        if not isinstance(event, dict):
            return
        kind = event.get("type")
        if kind == "response.created":
            record["generation_id"] = event.get("response", {}).get("id")
        elif kind == "response.output_item.done":
            self.message(record, event.get("item"))
        elif kind == "response.completed":
            response = event.get("response", {})
            record["upstream_terminal_completed"] = response.get("status") == "completed"
            if isinstance(response.get("id"), str):
                record["generation_id"] = response["id"]
            for item in response.get("output", []):
                self.message(record, item)
        elif kind in ("response.failed", "error"):
            record["upstream_failed"] = True

    def close(self):
        if self.core.sse_events is not self.observed or getattr(self.core, "_private_guardian_assessment_tap", None) is not self:
            self.fail("original_parser_restore_conflict")
            return
        self.core.sse_events = self.original
        del self.core._private_guardian_assessment_tap
