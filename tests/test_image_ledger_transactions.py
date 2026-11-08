"""Atomic image-ledger transitions; no network, credential or native runtime."""
from contextlib import asynccontextmanager, closing
import asyncio
import gc
import sqlite3
import time

import pytest

from codex_app_deepseek import image_transport as image


@pytest.fixture(autouse=True)
def collect_released_connections_after_each_transition_test():
    # Surface leaks in the allocating test, rather than when an unrelated
    # later test happens to trigger cyclic GC. CI keeps warnings as errors.
    yield
    gc.collect()


class TracedDatabase:
    def __init__(self, db, delay=0):
        self.db = db
        self.delay = delay
        self.commits = 0
        self.autocommit_writes = 0
        self.raise_after_commit = False

    def __getattr__(self, name):
        return getattr(self.db, name)

    def execute(self, sql, parameters=()):
        inside = self.db.in_transaction
        cursor = self.db.execute(sql, parameters)
        command = sql.split(None, 1)[0].upper()
        if command == "COMMIT":
            self.commits += 1
            if self.delay:
                time.sleep(self.delay)
            if self.raise_after_commit:
                self.raise_after_commit = False
                raise sqlite3.OperationalError("injected commit acknowledgement loss")
        elif command in {"INSERT", "UPDATE", "DELETE"} and not inside and cursor.rowcount:
            self.autocommit_writes += 1
            if self.delay:
                time.sleep(self.delay)
        return cursor


class Ledger(image.ImageTransport):
    """Use real SQLite transactions while isolating unrelated cache/file setup."""
    def __init__(self, delay=0):
        self.connection = sqlite3.connect(":memory:", isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.executescript("""
            CREATE TABLE pins(lease TEXT,digest TEXT,pid INTEGER,birth TEXT,
              phase TEXT,request_ref TEXT,PRIMARY KEY(lease,digest));
            CREATE TABLE receipts(sequence INTEGER PRIMARY KEY,at REAL,
              digest TEXT,event TEXT,detail TEXT);
        """)
        self.db = TracedDatabase(self.connection, delay)
        self.guard = asyncio.Lock()
        self.clock = time.time
        self.credentials = lambda: "synthetic-no-network"
        self.policy = image.TransportPolicy(1)
        self.failure_event = None
        self.failure_type = RuntimeError
        self.failure_calls = 0
        self.record_states = []

    @asynccontextmanager
    async def _db(self, directory):
        async with self.guard:
            yield self.db

    async def _scope(self, key):
        return "test-scope", "test-directory"

    def _record(self, db, digest, event, detail=None):
        if event == self.failure_event:
            self.failure_calls += 1
            if self.failure_calls == 3:
                raise self.failure_type("injected third receipt failure")
        self.record_states.append(db.in_transaction)
        return super()._record(db, digest, event, detail)

    def seed(self, *, phase="prepared", leases=("current",), historical=572):
        self.connection.execute("BEGIN")
        self.connection.executemany(
            "INSERT INTO pins VALUES(?,?,?,?,?,?)",
            [("historical", f"old-{i}", 424242, "linux:old-boot:1",
              "upstream_unknown", "old-request") for i in range(historical)]
            + [(lease, f"image-{i}", 123, "linux:current-boot:1", phase,
                "current-request" if phase == "upstream_unknown" else None)
               for lease in leases for i in range(16)],
        )
        self.connection.execute("COMMIT")

    def projection(self, where="1"):
        return [tuple(row) for row in self.connection.execute(
            "SELECT lease,digest,pid,birth,phase,request_ref FROM pins WHERE "
            + where + " ORDER BY lease,digest")]

    def lease(self, name="current"):
        return image._CallLease(self, "test-directory", name)


@pytest.fixture
def ledger():
    value = Ledger()
    try:
        yield value
    finally:
        value.connection.close()


@pytest.mark.asyncio
async def test_sixteen_images_commit_once_per_transition(ledger):
    ledger.seed()
    old = ledger.projection("lease='historical'")
    await ledger.lease().start("current-request")
    assert {row[0] for row in ledger.connection.execute(
        "SELECT phase FROM pins WHERE lease='current'")} == {"upstream_unknown"}
    await ledger.lease().settle("generation-fixed", "completed")
    assert {row[0] for row in ledger.connection.execute(
        "SELECT phase FROM pins WHERE lease='current'")} == {"settled"}
    assert ledger.db.commits == 2
    assert ledger.db.autocommit_writes == 0
    assert len(ledger.record_states) == 32 and all(ledger.record_states)
    assert ledger.projection("lease='historical'") == old
    assert ledger.policy.lock_timeout_seconds == 120


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,event,phase", [
    ("start", "upstream_started", "prepared"),
    ("finish", "upstream_settled", "upstream_unknown"),
    ("recover", "upstream_settled_recovery", "upstream_unknown"),
])
@pytest.mark.parametrize("failure", [RuntimeError, asyncio.CancelledError])
async def test_third_receipt_failure_rolls_back_whole_lease(
    ledger, operation, event, phase, failure
):
    ledger.seed(phase=phase)
    before = ledger.projection()
    ledger.failure_event = event
    ledger.failure_type = failure
    with pytest.raises(failure, match="third receipt"):
        if operation == "start":
            await ledger.lease().start("current-request")
        elif operation == "finish":
            await ledger.lease().settle("generation-fixed", "completed")
        else:
            await ledger.settle_unknown("current-request", "generation-fixed", "completed")
    assert ledger.projection() == before
    assert ledger.connection.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 0
    assert not ledger.connection.in_transaction


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,phase", [
    ("start", "prepared"),
    ("finish", "upstream_unknown"),
    ("recover", "upstream_unknown"),
])
async def test_real_sqlite_commit_rejection_preserves_previous_phase(
    ledger, operation, phase
):
    ledger.seed(phase=phase)
    before = ledger.projection()
    ledger.connection.set_authorizer(
        lambda action, one, two, database, trigger:
        sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_TRANSACTION
        and one == "COMMIT" else sqlite3.SQLITE_OK
    )
    try:
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            if operation == "start":
                await ledger.lease().start("current-request")
            elif operation == "finish":
                await ledger.lease().settle("generation-fixed", "completed")
            else:
                await ledger.settle_unknown("current-request", "generation-fixed", "completed")
    finally:
        ledger.connection.set_authorizer(None)
    assert ledger.projection() == before
    assert ledger.connection.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 0
    assert not ledger.connection.in_transaction


@pytest.mark.asyncio
async def test_verified_recovery_only_releases_matching_current_request(ledger):
    ledger.seed(phase="upstream_unknown")
    old = ledger.projection("lease='historical'")
    await ledger.settle_unknown("current-request", "generation-fixed", "completed")
    assert ledger.projection("lease='current'") == []
    assert ledger.projection("lease='historical'") == old
    assert ledger.db.commits == 1 and ledger.db.autocommit_writes == 0
    assert len(ledger.record_states) == 16 and all(ledger.record_states)


def test_nested_transaction_rejected_without_committing_outer_changes(ledger):
    with pytest.raises(image.ImageTransportError, match="already active"):
        with image._ledger_transaction(ledger.db):
            ledger.db.execute("INSERT INTO receipts VALUES(NULL,0,'a','outer',NULL)")
            with image._ledger_transaction(ledger.db):
                raise AssertionError("Nested body must not run")
    assert not ledger.connection.in_transaction
    assert ledger.connection.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 0


@pytest.mark.asyncio
async def test_four_concurrent_sixteen_image_leases_have_eight_durable_commits():
    value = Ledger(delay=0.005)
    try:
        value.seed(leases=tuple(f"current-{i}" for i in range(4)))
        old = value.projection("lease='historical'")
        async def request(index):
            lease = value.lease(f"current-{index}")
            await lease.start(f"request-{index}")
            await lease.settle(f"generation-{index}", "completed")
        await asyncio.gather(*(request(i) for i in range(4)))
        assert value.db.commits == 8
        assert value.db.autocommit_writes == 0
        assert len(value.record_states) == 128 and all(value.record_states)
        assert value.projection("lease='historical'") == old
        assert value.policy.lock_timeout_seconds == 120
    finally:
        value.connection.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("operation,phase,expected", [
    ("start", "prepared", "upstream_unknown"),
    ("finish", "upstream_unknown", "settled"),
    ("recover", "upstream_unknown", None),
])
async def test_commit_acknowledgement_loss_preserves_committed_truth(
    ledger, operation, phase, expected
):
    ledger.seed(phase=phase)
    old = ledger.projection("lease='historical'")
    ledger.db.raise_after_commit = True
    with pytest.raises(sqlite3.OperationalError, match="acknowledgement loss"):
        if operation == "start":
            await ledger.lease().start("current-request")
        elif operation == "finish":
            await ledger.lease().settle("generation-fixed", "completed")
        else:
            await ledger.settle_unknown("current-request", "generation-fixed", "completed")
    current = ledger.projection("lease='current'")
    if expected is None:
        assert current == []
    else:
        assert len(current) == 16 and {row[4] for row in current} == {expected}
    assert ledger.projection("lease='historical'") == old
    assert ledger.connection.execute("SELECT COUNT(*) FROM receipts").fetchone()[0] == 16
    assert not ledger.connection.in_transaction and ledger.db.commits == 1


@pytest.mark.parametrize("committed", [False, True])
def test_process_death_preserves_whole_atomic_transition(ledger, tmp_path, committed):
    import os
    from pathlib import Path
    import subprocess
    import sys
    ledger.seed()
    before = ledger.projection()
    path = tmp_path / "crash.sqlite"
    with closing(sqlite3.connect(path)) as destination:
        ledger.connection.backup(destination)
    program = (
        "import sys,os,sqlite3;"
        "sys.path.insert(0,sys.argv[4]);sys.path.insert(0,sys.argv[1]);"
        "from codex_app_deepseek.image_transport import _ledger_transaction;"
        "db=sqlite3.connect(sys.argv[2],isolation_level=None);"
        "db.execute('PRAGMA synchronous=FULL');"
        "db.execute('PRAGMA cache_size=1');"
        "\nwith _ledger_transaction(db):\n"
        " db.execute(\"UPDATE pins SET phase='upstream_unknown',request_ref='current-request' WHERE lease='current'\")\n"
        " for i in range(16):db.execute(\"INSERT INTO receipts VALUES(NULL,0,?,'upstream_started','current-request')\",(f'image-{i}',))\n"
        " if sys.argv[3]=='before':os._exit(73)\n"
        "os._exit(73)\n"
    )
    child = subprocess.run(
        [sys.executable, "-I", "-B", "-c", program,
         str(Path(image.__file__).resolve().parents[1]), str(path),
         "after" if committed else "before",
         str(Path(image._protocol.__file__).resolve().parents[1])],
        capture_output=True, timeout=15,
    )
    assert child.returncode == 73, child.stderr
    with closing(sqlite3.connect(path)) as recovered:
        current = recovered.execute(
            "SELECT lease,digest,pid,birth,phase,request_ref FROM pins ORDER BY lease,digest"
        ).fetchall()
        count = recovered.execute("SELECT COUNT(*) FROM receipts").fetchone()[0]
    if committed:
        assert count == 16
        assert [row for row in current if row[0] == "historical"] == [
            row for row in before if row[0] == "historical"]
        assert {row[4] for row in current if row[0] == "current"} == {"upstream_unknown"}
    else:
        assert count == 0 and current == before
