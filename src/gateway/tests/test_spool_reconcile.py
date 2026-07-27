"""TR-041/052/053/054 (VR-018, VR-019): the spool, the drain, and the bad row.

The spool exists for one case — a call that was **billed** and whose record
could not be written — so these tests are almost entirely about failure paths.
That shapes how they are built: the database failures are injected through the
writer's `connect` seam rather than produced by breaking a real server, because
"make PostgreSQL refuse this one write and then accept the next" is not
something a live database does on request, and a test that cannot produce the
failure cannot assert what happens after it.

The spool itself is always real. It is a SQLite file, `tmp_path` gives a
disposable one, and faking it would leave the durability claims — append-only,
delete-after-commit, tolerant of a concurrent drain — asserted against a
dictionary.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from gateway.models import InvocationRecord
from gateway.record.reconcile import drain_spool
from gateway.record.spool import PAYLOAD_SCHEMA_VERSION, InvocationSpool
from gateway.record.writer import RecordWriteError, RecordWriter


def a_record(**overrides: object) -> InvocationRecord:
    now = datetime.now(UTC)
    defaults: dict[str, object] = {
        "invocation_id": str(uuid.uuid4()),
        "trace_id": uuid.uuid4().hex,
        "gen_ai_provider_name": "test-provider",
        "gen_ai_operation_name": "chat",
        "gen_ai_request_model": "claude-opus-5",
        "gen_ai_response_model": "claude-opus-5",
        "resolution_mode": "record",
        "fixture_key": None,
        "gen_ai_usage_input_tokens": 10,
        "gen_ai_usage_output_tokens": 5,
        "cache_write_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "duration_ms": 12,
        "transport_attempt_count": 1,
        "repair_attempt_count": 0,
        "cost_usd": Decimal("0.0001000000"),
        "cost_absent_reason": None,
        "price_table_version_id": "2026-07-26-published",
        "pricing_timestamp": now,
        "outcome": "valid",
        "error_type": None,
        "created_at": now,
    }
    defaults.update(overrides)
    return InvocationRecord(**defaults)  # type: ignore[arg-type]


class RecordingConnection:
    """A connection that accepts writes and remembers them."""

    def __init__(self) -> None:
        self.written: list[dict[str, Any]] = []
        self.committed = 0

    def execute(self, query: str, params: Any = None, /) -> Any:
        if params is not None and "INSERT INTO llm_invocation" in query:
            self.written.append(dict(params))
        return self

    def fetchone(self) -> Any:
        return None

    def fetchall(self) -> list[Any]:
        return []

    def commit(self) -> None:
        self.committed += 1

    def rollback(self) -> None: ...
    def close(self) -> None: ...


class RefusingConnection(RecordingConnection):
    """A connection whose invocation writes always fail a named constraint."""

    def __init__(self, constraint: str = "fk_llm_invocation__price_table_version") -> None:
        super().__init__()
        self.constraint = constraint

    def execute(self, query: str, params: Any = None, /) -> Any:
        if "INSERT INTO llm_invocation" in query:
            raise _DriverError(self.constraint)
        return super().execute(query, params)


class _DriverError(Exception):
    """Shaped like psycopg's error: carries a `diag.constraint_name`."""

    def __init__(self, constraint: str) -> None:
        super().__init__(f"violates constraint {constraint}")
        self.diag = type("Diag", (), {"constraint_name": constraint})()


def writer_over(connection: RecordingConnection) -> RecordWriter:
    return RecordWriter("postgresql://ignored/db", connect=lambda *a, **k: connection)


@pytest.fixture
def spool(tmp_path: Path) -> Iterator[InvocationSpool]:
    yield InvocationSpool(tmp_path / "spool.sqlite3")


# --- TR-041 / VR-018: a failed write spools rather than vanishing ------------


def test_a_spooled_record_round_trips_unchanged(spool: InvocationSpool) -> None:
    """The record must come back *identical*. A spool that loses a field, or
    widens a Decimal into a float, would reconcile a row that differs from the
    one the invocation produced — and nothing downstream would ever know."""
    record = a_record()
    spool.append(record, write_error_type="OperationalError")

    [waiting] = list(spool.pending())
    assert waiting.to_record() == record


def test_the_cost_survives_as_an_exact_decimal(spool: InvocationSpool) -> None:
    """CD-3 reaches the spool too. A cost that round-tripped through a binary
    float would reconcile a figure that no longer reproduces on recomputation,
    which is the one property SC-006 asserts."""
    record = a_record(cost_usd=Decimal("0.1234567891"))
    spool.append(record, write_error_type="OperationalError")

    [waiting] = list(spool.pending())
    assert waiting.to_record().cost_usd == Decimal("0.1234567891")


def test_the_spool_records_why_the_write_failed(spool: InvocationSpool) -> None:
    """A class name, never the driver exception's arguments — those can carry
    the connection string, and this file is on disk."""
    spool.append(a_record(), write_error_type="OperationalError")
    [waiting] = list(spool.pending())
    assert waiting.write_error_type == "OperationalError"
    assert "://" not in waiting.write_error_type


def test_appending_the_same_invocation_twice_leaves_one_row(
    spool: InvocationSpool,
) -> None:
    """TR-045/TR-052: the identifier is minted once per invocation, so a second
    append under it is a retry of one failure rather than a second record.
    Absorbing it is what keeps the spool append-only — inserted once, removed
    once, never updated in place."""
    record = a_record()
    spool.append(record, write_error_type="OperationalError")
    spool.append(record, write_error_type="OperationalError")
    assert spool.depth() == 1


def test_the_spool_starts_empty_and_returns_to_empty(
    spool: InvocationSpool, caplog: pytest.LogCaptureFixture
) -> None:
    """ADR-0015's third test — steady state is empty — is what makes this a
    buffer rather than a second datastore of record."""
    assert spool.depth() == 0
    record = a_record()
    spool.append(record, write_error_type="OperationalError")
    assert spool.depth() == 1
    spool.discard(record.invocation_id)
    assert spool.depth() == 0


def test_the_spool_write_logs_depth_and_the_invocation_id(
    spool: InvocationSpool, caplog: pytest.LogCaptureFixture
) -> None:
    """TR-053 and TR-077 event 3. Depth on every spool write is what makes "the
    outage is still going and nothing has drained" visible rather than silent,
    and the identifier is what resolves the line to a row."""
    record = a_record()
    with caplog.at_level(logging.WARNING, logger="gateway.record"):
        spool.append(record, write_error_type="OperationalError")

    [line] = [r for r in caplog.records if "spooled" in r.message]
    assert line.invocation_id == record.invocation_id  # type: ignore[attr-defined]
    assert line.spool_depth == 1  # type: ignore[attr-defined]


def test_a_spooled_record_survives_the_process(tmp_path: Path) -> None:
    """Durability, asserted across two `InvocationSpool` objects on one file.

    `synchronous=FULL` is what makes this true of a crash rather than only of a
    clean exit, and the whole point of the spool is the crash case.
    """
    record = a_record()
    InvocationSpool(tmp_path / "s.sqlite3").append(record, write_error_type="X")
    assert InvocationSpool(tmp_path / "s.sqlite3").depth() == 1


# --- TR-052 / VR-019: the drain, and double-draining ------------------------


def test_a_drain_reconciles_and_empties(spool: InvocationSpool) -> None:
    record = a_record()
    spool.append(record, write_error_type="OperationalError")

    connection = RecordingConnection()
    result = drain_spool(spool, writer_over(connection))

    assert result.reconciled == 1
    assert result.retained == 0
    assert spool.depth() == 0, "the spool row outlived its committed record"
    assert connection.written[0]["invocation_id"] == record.invocation_id


def test_the_spool_row_is_deleted_only_after_the_commit(spool: InvocationSpool) -> None:
    """TR-052's ordering, and the reason it is that way round.

    A connection that accepts the insert and then fails to commit must leave the
    spool row in place. Deleting first would lose the record on exactly this
    failure — which is the failure the spool exists to survive.
    """

    class CommitFails(RecordingConnection):
        def commit(self) -> None:
            raise _DriverError("commit-refused")

    spool.append(a_record(), write_error_type="OperationalError")
    result = drain_spool(spool, writer_over(CommitFails()))

    assert result.reconciled == 0
    assert spool.depth() == 1, (
        "the spool row was deleted although the invocation-table transaction "
        "never committed (TR-052)"
    )


def test_draining_twice_writes_one_row_and_is_not_an_error(
    spool: InvocationSpool,
) -> None:
    """VR-019. Exactly-once is a property of the *effect*: a second drain finds
    nothing to do, and a spool row re-inserted after a crash inside the
    reconcile window is the designed recovery path rather than a violation."""
    spool.append(a_record(), write_error_type="OperationalError")
    connection = RecordingConnection()
    writer = writer_over(connection)

    first = drain_spool(spool, writer)
    second = drain_spool(spool, writer)

    assert first.reconciled == 1
    assert second.reconciled == 0
    assert len(connection.written) == 1


def test_a_drain_of_an_empty_spool_still_reports(
    spool: InvocationSpool, caplog: pytest.LogCaptureFixture
) -> None:
    """TR-077 event 4 is emitted unconditionally. A drain that logged only when
    it moved something would make "the spool is empty" and "the drain never
    ran" the same observation — and those need different responses."""
    with caplog.at_level(logging.INFO, logger="gateway.record"):
        result = drain_spool(spool, writer_over(RecordingConnection()))

    assert result.depth_before == 0
    assert any("drained" in record.message for record in caplog.records)


def test_the_drain_uses_the_conflict_ignoring_insert(spool: InvocationSpool) -> None:
    """The mechanism behind the idempotency, asserted rather than inferred.

    Both drains above would also pass if the drain simply never re-inserted. The
    property that matters is that the insert it *does* issue tolerates a
    primary-key conflict, because the crash-inside-the-window case reaches this
    path with the row already committed.
    """
    statements: list[str] = []

    class Watching(RecordingConnection):
        def execute(self, query: str, params: Any = None, /) -> Any:
            statements.append(query)
            return super().execute(query, params)

    spool.append(a_record(), write_error_type="OperationalError")
    drain_spool(spool, writer_over(Watching()))

    inserts = [s for s in statements if "INSERT INTO llm_invocation" in s]
    assert inserts, "the drain issued no insert"
    assert all("ON CONFLICT (invocation_id) DO NOTHING" in s for s in inserts), (
        "the drain's insert does not tolerate a primary-key conflict (TR-052)"
    )


# --- TR-054: one bad row must not become an outage --------------------------


def test_a_row_failing_a_constraint_is_retained_and_named(
    spool: InvocationSpool, caplog: pytest.LogCaptureFixture
) -> None:
    """TR-054. Retained, and the log line names the failing constraint and the
    invocation identifier — which is the payoff for naming every constraint in
    the migrations. "Something referential failed" is not actionable."""
    record = a_record()
    spool.append(record, write_error_type="OperationalError")

    with caplog.at_level(logging.ERROR, logger="gateway.record"):
        result = drain_spool(spool, writer_over(RefusingConnection()))

    assert result.reconciled == 0
    assert result.retained == 1
    assert spool.depth() == 1, "an unreconcilable row was dropped rather than retained"

    [line] = [r for r in caplog.records if r.levelno >= logging.ERROR]
    assert line.invocation_id == record.invocation_id  # type: ignore[attr-defined]
    assert line.constraint == "fk_llm_invocation__price_table_version"  # type: ignore[attr-defined]


def test_a_poisoned_row_does_not_stop_the_drain(spool: InvocationSpool) -> None:
    """TR-054's other half: the drain continues with the remaining rows.

    A recovery mechanism that stops at the first unrecoverable record converts
    one bad row into an unbounded backlog — every later record blocked behind
    the one that will never succeed.
    """
    poison = a_record(price_table_version_id="no-such-version")
    good_one = a_record()
    good_two = a_record()
    for record in (poison, good_one, good_two):
        spool.append(record, write_error_type="OperationalError")

    class RefusesOne(RecordingConnection):
        def execute(self, query: str, params: Any = None, /) -> Any:
            poisoned = (
                "INSERT INTO llm_invocation" in query
                and params is not None
                and params["invocation_id"] == poison.invocation_id
            )
            if poisoned:
                raise _DriverError("fk_llm_invocation__price_table_version")
            return super().execute(query, params)

    result = drain_spool(spool, writer_over(RefusesOne()))

    assert result.reconciled == 2, "the drain stopped at the poisoned row"
    assert result.retained == 1
    assert spool.depth() == 1


def test_the_poisoned_row_does_not_fail_the_triggering_invocation(
    spool: InvocationSpool,
) -> None:
    """TR-054, stated as the thing a caller experiences.

    The drain runs at the start of an unrelated invocation. If it raised, that
    invocation would fail because of a *previous* one's unreconcilable record —
    turning a recovery mechanism into an outage.
    """
    spool.append(a_record(), write_error_type="OperationalError")
    drain_spool(spool, writer_over(RefusingConnection()))  # must not raise


def test_an_unreadable_payload_version_is_retained_not_reinterpreted(
    spool: InvocationSpool, caplog: pytest.LogCaptureFixture
) -> None:
    """TR-054. A payload from a newer gateway is not corrupt — it is simply not
    this process's to interpret, so it is retained and surfaced rather than read
    under a shape it was not written in."""
    record = a_record()
    spool.append(record, write_error_type="OperationalError")
    with sqlite3.connect(spool.path) as raw:
        raw.execute(
            "UPDATE invocation_spool SET payload_schema_version = ?",
            (PAYLOAD_SCHEMA_VERSION + 1,),
        )

    connection = RecordingConnection()
    with caplog.at_level(logging.ERROR, logger="gateway.record"):
        result = drain_spool(spool, writer_over(connection))

    assert result.retained == 1
    assert spool.depth() == 1
    assert connection.written == [], (
        "a payload of an unknown version was written anyway, under a shape it "
        "was not stored in (TR-054)"
    )
    assert any("schema version" in r.message for r in caplog.records)


def test_an_undecodable_payload_is_retained(spool: InvocationSpool) -> None:
    """The same rule for a payload that is the right version and the wrong
    shape. Discarding it would destroy the only copy of a billed call's
    record."""
    spool.append(a_record(), write_error_type="OperationalError")
    with sqlite3.connect(spool.path) as raw:
        raw.execute("UPDATE invocation_spool SET payload = ?", ('{"nope": true}',))

    result = drain_spool(spool, writer_over(RecordingConnection()))
    assert result.retained == 1
    assert spool.depth() == 1


def test_the_write_error_reaching_the_drain_carries_no_driver_exception() -> None:
    """The seam the constraint name travels through, checked end to end.

    `RecordWriteError` extracts the name where the driver exception is in reach
    and then drops it. If that ever regressed to carrying the exception, the
    drain's log line would be one `repr` away from a connection string.
    """
    with pytest.raises(RecordWriteError) as raised:
        writer_over(RefusingConnection()).write(a_record())

    assert raised.value.constraint_name == "fk_llm_invocation__price_table_version"
    assert raised.value.__context__ is None
    assert not isinstance(raised.value, _DriverError)


# --- AD-008: the spool's depth soft cap --------------------------------------

#: 10 MB, from AD-008. The spool's steady state is empty (ADR-0015), so any
#: sustained size at all is a signal — this cap is where "the outage is long"
#: becomes "the outage is long enough to act on".
SPOOL_SOFT_CAP_BYTES = 10 * 1024 * 1024


def test_a_spool_under_the_cap_reports_its_size(tmp_path: Path) -> None:
    """The measurement itself, on a spool with something in it. A cap check
    that only ever ran against an empty file would pass on any threshold."""
    spool = InvocationSpool(tmp_path / "spool.sqlite3")
    spool.append(a_record(), write_error_type="OperationalError")

    size = spool.path.stat().st_size
    assert 0 < size <= SPOOL_SOFT_CAP_BYTES, (
        f"spool is {size} bytes against a {SPOOL_SOFT_CAP_BYTES} byte soft cap"
    )


def test_an_empty_spool_is_the_steady_state(tmp_path: Path) -> None:
    """ADR-0015's third test, restated as the thing an operator watches. Depth
    rather than bytes, because the file keeps its pages after a drain — size
    stays up while depth returns to zero, and depth is the honest signal."""
    spool = InvocationSpool(tmp_path / "spool.sqlite3")
    record = a_record()
    spool.append(record, write_error_type="OperationalError")
    spool.discard(record.invocation_id)

    assert spool.depth() == 0
    assert spool.path.stat().st_size <= SPOOL_SOFT_CAP_BYTES


def test_the_cap_is_a_soft_one() -> None:
    """Recorded as a decision rather than left to inference: exceeding this
    fails one test and blocks nothing. A hard cap would convert a long outage —
    the case the spool exists for — into a second failure on top of the first.
    """
    assert SPOOL_SOFT_CAP_BYTES == 10 * 1024 * 1024
