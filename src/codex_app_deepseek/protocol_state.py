"""Only protocol evidence and request receipts; never owns an App or native process."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sqlite3
import time
import uuid

from codex_deepseek.protocol import ProtocolError, native_history_fingerprint


class ProtocolState:
    """Single gateway owner per database, with durable, immutable completion evidence."""

    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.instance_id = str(uuid.uuid4())
        self._closed = False
        self._lock = self.path.with_suffix(self.path.suffix + ".lock").open("a+b")
        self._lock.seek(0, os.SEEK_END)
        if self._lock.tell() == 0:
            self._lock.write(b"0")
            self._lock.flush()
        self._lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._lock.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            self._lock.close()
            raise RuntimeError("Another gateway owns this protocol database") from exc
        try:
            self.db = sqlite3.connect(self.path, timeout=30)
            self.db.row_factory = sqlite3.Row
            if self.db.execute("PRAGMA user_version").fetchone()[0] > 3:
                raise RuntimeError("Protocol database requires a newer gateway")
            self.db.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;
                CREATE TABLE IF NOT EXISTS searches(id TEXT PRIMARY KEY, item TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS completed_native_items(
                    fingerprint TEXT PRIMARY KEY, receipt_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS receipts(
                    id TEXT PRIMARY KEY, instance_id TEXT NOT NULL, started REAL NOT NULL,
                    finished REAL, status TEXT NOT NULL, requested_model TEXT NOT NULL,
                    requested_upstream_model TEXT NOT NULL, requested_provider TEXT NOT NULL, route_reason TEXT NOT NULL,
                    context_key TEXT, upstream_status INTEGER, response_id TEXT,
                    usage TEXT NOT NULL DEFAULT '{}', detail TEXT,
                    delivery_status TEXT NOT NULL DEFAULT 'pending');
            """)
            columns = {row[1] for row in self.db.execute("PRAGMA table_info(receipts)")}
            if "adaptations" not in columns:
                self.db.execute("ALTER TABLE receipts ADD COLUMN adaptations TEXT NOT NULL DEFAULT '{}'")
            for old, new in (("actual_model", "requested_upstream_model"), ("provider", "requested_provider")):
                if old in columns:
                    self.db.execute(f"ALTER TABLE receipts RENAME COLUMN {old} TO {new}")
            for name in ("observed_model", "observed_provider"):
                if name not in columns:
                    self.db.execute(f"ALTER TABLE receipts ADD COLUMN {name} TEXT")
            self.db.execute("""CREATE TABLE IF NOT EXISTS completed_native_evidence(
                item_id TEXT NOT NULL, fingerprint TEXT NOT NULL, receipt_id TEXT NOT NULL,
                response_id TEXT NOT NULL, PRIMARY KEY(item_id,fingerprint,receipt_id))""")
            self.db.execute("PRAGMA user_version=3")
            self.db.commit()
            # The exclusive lock proves no previous owner remains. Its external
            # request outcome is unknown; neither requests nor tools are replayed.
            with self.db:
                self.db.execute(
                    "UPDATE receipts SET status='owner_lost', finished=?, detail=? WHERE status='running'",
                    (time.time(), "Gateway exited without a verified terminal response; upstream outcome unknown"),
                )
        except BaseException:
            if hasattr(self, "db"):
                self.db.close()
            self._release_lock()
            raise

    def _release_lock(self):
        if self._lock.closed:
            return
        self._lock.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self._lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lock.fileno(), fcntl.LOCK_UN)
        finally:
            self._lock.close()

    def get(self, key):
        row = self.db.execute("SELECT item FROM searches WHERE id=?", (key,)).fetchone()
        return json.loads(row["item"]) if row else None

    def put(self, key, value):
        if not isinstance(key, str) or not key or not isinstance(value, dict):
            raise ProtocolError("Invalid hosted search evidence")
        previous = self.get(key)
        if previous and previous.get("status") == "completed" and previous != value:
            raise ProtocolError("Provider reused a completed search ID with different evidence")
        with self.db:
            self.db.execute("INSERT OR REPLACE INTO searches VALUES (?,?)",
                            (key, json.dumps(value, ensure_ascii=False)))

    def has_completed_native_item(self, fingerprint, *, item_id=None):
        # Legacy semantic-only rows cannot prove the identity of a replayed item.
        if not isinstance(item_id, str) or not item_id:
            return False
        return self.db.execute(
            "SELECT 1 FROM completed_native_evidence WHERE item_id=? AND fingerprint=?",
            (item_id, fingerprint),
        ).fetchone() is not None

    def evidence_view(self, items):
        return RequestEvidence(self, items)

    def start(self, *, requested_model, requested_upstream_model, requested_provider, route_reason, context_key=None, adaptations=None):
        key = str(uuid.uuid4())
        with self.db:
            self.db.execute(
                """INSERT INTO receipts(id,instance_id,started,status,requested_model,
                   requested_upstream_model,requested_provider,route_reason,context_key,adaptations) VALUES (?,?,?,'running',?,?,?,?,?,?)""",
                (key, self.instance_id, time.time(), requested_model, requested_upstream_model,
                 requested_provider, route_reason, context_key, json.dumps(adaptations or {})),
            )
        return key

    def progress(self, key, events):
        with self.db:
            self.db.execute("UPDATE receipts SET response_id=? WHERE id=? AND instance_id=? AND status='running'",
                            (events.response_id, key, self.instance_id))

    def finish(self, key, status, upstream=None, events=None, detail=None):
        allowed = {"completed", "incomplete", "failed", "upstream_error", "cancelled", "adapter_error"}
        if status not in allowed:
            raise ValueError("Invalid request terminal status")
        if status in {"completed", "incomplete", "failed"}:
            if not events or not events.terminal or events.status != status:
                raise ValueError("A model terminal receipt requires matching validated terminal events")
        fingerprints = []
        if status == "completed":
            for item in events.done.values():
                if item.get("type") in {"reasoning", "function_call"}:
                    normalized = events.registry.output(item, final=True)
                    fingerprint = native_history_fingerprint(normalized)
                    if fingerprint:
                        fingerprints.append((normalized["id"], fingerprint, key, events.response_id))
        with self.db:
            changed = self.db.execute(
                """UPDATE receipts SET finished=?,status=?,upstream_status=?,response_id=?,usage=?,detail=?
                   WHERE id=? AND instance_id=? AND status='running'""",
                (time.time(), status, upstream, events.response_id if events else None,
                 json.dumps(events.usage if events else {}), detail, key, self.instance_id),
            ).rowcount
            if changed:
                self.db.executemany("INSERT OR IGNORE INTO completed_native_evidence VALUES (?,?,?,?)", fingerprints)
        return bool(changed)

    def image_transport(self, key, **updates):
        # Additive structured receipt inside schema3 adaptations. This owns no
        # model history or resource lifecycle and contains no image/file IDs.
        allowed = {'preparation', 'consumption_status', 'provider_terminal',
                   'provider_response_id', 'upstream_http_status', 'release_error_type'}
        if not set(updates) <= allowed:
            raise ValueError('Unsupported image transport receipt field')
        with self.db:
            row = self.db.execute('SELECT adaptations FROM receipts WHERE id=? AND instance_id=?',
                                  (key, self.instance_id)).fetchone()
            if row is None:
                raise ValueError('Image transport receipt has no owned request')
            adaptations = json.loads(row['adaptations'])
            current = dict(adaptations.get('image_transport', {}))
            # A downstream error must not erase provider-terminal evidence.
            if current.get('provider_terminal') and updates.get('consumption_status') in {'unknown','not_sent'}:
                updates = {name: value for name, value in updates.items() if name != 'consumption_status'}
            current.update(updates)
            adaptations['image_transport'] = current
            self.db.execute('UPDATE receipts SET adaptations=? WHERE id=? AND instance_id=?',
                            (json.dumps(adaptations, ensure_ascii=False, allow_nan=False), key, self.instance_id))

    def observe(self, key, *, model=None, provider=None):
        with self.db:
            self.db.execute(
                """UPDATE receipts SET observed_model=COALESCE(?,observed_model),
                   observed_provider=COALESCE(?,observed_provider)
                   WHERE id=? AND instance_id=? AND status='running'""",
                (model, provider, key, self.instance_id),
            )

    def delivery(self, key, status):
        if status not in {"streaming", "delivered", "disconnected", "failed"}:
            raise ValueError("Invalid downstream delivery status")
        with self.db:
            self.db.execute("UPDATE receipts SET delivery_status=? WHERE id=? AND instance_id=?",
                            (status, key, self.instance_id))

    def receipt(self, key):
        row = self.db.execute("SELECT * FROM receipts WHERE id=?", (key,)).fetchone()
        if not row:
            return None
        value = dict(row)
        value["usage"] = json.loads(value["usage"])
        value["adaptations"] = json.loads(value["adaptations"])
        return value

    def list_receipts(self, limit=100):
        if not isinstance(limit, int) or not 1 <= limit <= 1000:
            raise ValueError("Receipt limit must be between 1 and 1000")
        ids = self.db.execute("SELECT id FROM receipts ORDER BY started DESC LIMIT ?", (limit,)).fetchall()
        return [self.receipt(row["id"]) for row in ids]

    def close(self):
        if self._closed:
            return
        try:
            with self.db:
                self.db.execute(
                    """UPDATE receipts SET status='owner_closed',finished=?,detail=?
                       WHERE instance_id=? AND status='running'""",
                    (time.time(), "Gateway stopped before a verified terminal response", self.instance_id),
                )
        finally:
            self.db.close()
            self._release_lock()
            self._closed = True

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()


class RequestEvidence:
    """Only identities explicitly present in this request may attest a semantic fingerprint."""

    def __init__(self, state, items):
        self.state = state
        self.identities = {}
        if isinstance(items, list):
            for item in items:
                if not isinstance(item, dict) or item.get("type") not in {
                    "reasoning", "function_call", "custom_tool_call"
                }:
                    continue
                fingerprint = native_history_fingerprint(item)
                if fingerprint:
                    self.identities.setdefault(fingerprint, []).append(item.get("id"))

    def has_completed_native_item(self, fingerprint):
        identities = self.identities.get(fingerprint, [])
        return bool(identities) and all(
            self.state.has_completed_native_item(fingerprint, item_id=item_id)
            for item_id in identities
        )

    def get(self, key):
        return self.state.get(key)

    def put(self, key, value):
        self.state.put(key, value)
