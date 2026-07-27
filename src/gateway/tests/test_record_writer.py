"""TR-035 / TR-044 / TR-055 (VR-016, VR-021): the record survives its caller.

Two families here, and they need different things.

**Caller-rollback survival** needs a real database and two real connections.
The claim is that the gateway's transaction is independent of a caller's, and
independence is only observable when both exist — a fake would prove that the
code calls `commit()`, which was never in doubt.

**Constraint pairing** also needs the real server: `CHECK` enforcement is
server-side behaviour with no faithful mock, and the point is *which named
constraint* rejected a row. Matching on the constraint name rather than the
message is what makes these assertions stable across locales and server
versions, and is why every constraint in this epic's migrations is named.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import psycopg
import pytest

from gateway.errors import GatewayConfigError
from gateway.models import InvocationRecord
from gateway.record.writer import _INSERT_SQL, COLUMNS, RecordWriteError, RecordWriter

SEEDED_VERSION = "2026-07-26-published"


def _database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        pytest.skip("DATABASE_URL is not set; this tier needs a live PostgreSQL")
    return url


@pytest.fixture
def writer() -> Iterator[RecordWriter]:
    with RecordWriter(_database_url()) as record_writer:
        yield record_writer


@pytest.fixture
def cleanup() -> Iterator[list[str]]:
    """Invocation identifiers to remove after the test.

    These tests **commit** — that is the property under test, so the usual
    rollback-everything harness would defeat them. Cleaning up by identifier
    keeps them re-runnable without a `TRUNCATE` that would delete rows another
    test is using.
    """
    written: list[str] = []
    yield written
    if written:
        with psycopg.connect(_database_url()) as connection:
            connection.execute(
                "DELETE FROM llm_invocation WHERE invocation_id = ANY(%s)",
                ([uuid.UUID(i) for i in written],),
            )
            connection.commit()


def a_record(**overrides: object) -> InvocationRecord:
    """A valid row, with the awkward fields already correct.

    A helper rather than a fixture so a test can vary one field and have the
    rest stay coherent — most of these tests are about a single field being
    wrong, and rebuilding twenty-two of them per case would bury which one.
    """
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
        "gen_ai_usage_input_tokens": 100,
        "gen_ai_usage_output_tokens": 50,
        "cache_write_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "duration_ms": 1234,
        "transport_attempt_count": 1,
        "repair_attempt_count": 0,
        "cost_usd": Decimal("0.0017500000"),
        "cost_absent_reason": None,
        "price_table_version_id": SEEDED_VERSION,
        "pricing_timestamp": now,
        "outcome": "valid",
        "error_type": None,
        "created_at": now,
    }
    defaults.update(overrides)
    return InvocationRecord(**defaults)  # type: ignore[arg-type]


# --- The statement and the field list cannot drift -------------------------


def test_the_insert_names_exactly_the_closed_column_list() -> None:
    """The duplication `_INSERT_SQL` accepts, paid for here.

    The statement is written out rather than joined from `COLUMNS` so a reviewer
    can read it as SQL. That buys readability at the cost of two statements of
    one fact — so the two are compared, and a column added to one and not the
    other fails here rather than at a write.
    """
    named = {
        column
        for column in COLUMNS
        if f"%({column})s" in _INSERT_SQL and column in _INSERT_SQL
    }
    assert named == set(COLUMNS), (
        f"the INSERT and COLUMNS disagree: {sorted(set(COLUMNS) - named)} are in the "
        f"list but not both halves of the statement"
    )
    assert _INSERT_SQL.count("%(") == len(COLUMNS), (
        f"the INSERT binds {_INSERT_SQL.count('%(')} parameters for "
        f"{len(COLUMNS)} columns"
    )


def test_the_column_list_matches_the_record_type() -> None:
    """TR-068's closure, as far as two Python objects can carry it.

    The comparison against the *migrated schema* is `test_read_contract.py`'s;
    this one catches the cheaper mistake of a model field the writer never
    sends, which would be written as a silent `NULL`.
    """
    assert set(COLUMNS) == set(InvocationRecord.model_fields), (
        f"model-only: {sorted(set(InvocationRecord.model_fields) - set(COLUMNS))}; "
        f"writer-only: {sorted(set(COLUMNS) - set(InvocationRecord.model_fields))}"
    )


# --- TR-035 / VR-016: the record survives a caller's rollback ---------------


def test_the_record_survives_a_caller_rolling_back(
    writer: RecordWriter, cleanup: list[str]
) -> None:
    """VR-016, and the reason TR-035 says "its own connection".

    The caller opens a transaction, the gateway writes its record, the caller
    rolls back. The row must still be there — a provider call that was billed
    is not undone by the caller changing its mind about its own unit of work.
    """
    record = a_record()
    cleanup.append(record.invocation_id)

    with psycopg.connect(_database_url()) as caller:
        caller.execute("SELECT 1")  # open a real transaction on the caller's side
        writer.write(record)
        caller.rollback()

    with psycopg.connect(_database_url()) as probe:
        found = probe.execute(
            "SELECT outcome FROM llm_invocation WHERE invocation_id = %s",
            (uuid.UUID(record.invocation_id),),
        ).fetchone()

    assert found is not None, (
        "the invocation record vanished when the caller rolled back; the gateway "
        "is writing on the caller's transaction rather than its own (TR-035)"
    )
    assert found[0] == "valid"


def test_the_writer_uses_a_connection_the_caller_never_supplied() -> None:
    """The same property, stated structurally rather than observed.

    `RecordWriter` takes a URL, not a connection. There is no parameter through
    which a caller could hand it one — which is a stronger guarantee than any
    test of behaviour, because it makes the wrong arrangement unexpressible.
    """
    import inspect

    parameters = set(inspect.signature(RecordWriter.__init__).parameters) - {"self"}
    assert parameters == {"database_url", "connect"}, (
        f"RecordWriter accepts {sorted(parameters)}; a connection or session "
        f"parameter would let a caller's transaction own the record (TR-035)"
    )


def test_a_missing_database_url_is_a_configuration_error() -> None:
    """Refused rather than run untraced. Every invocation must produce a record,
    so a gateway with nowhere to write is a configuration error and not a
    gateway that quietly records nothing."""
    with pytest.raises(GatewayConfigError, match="DATABASE_URL"):
        RecordWriter(None).connection()


def test_the_configuration_error_names_the_key_and_not_the_value() -> None:
    """TR-065. A connection URL carries a password, so the key's name is
    permitted and the value is not — including any part of it."""
    with pytest.raises(GatewayConfigError) as raised:
        RecordWriter(None).connection()
    assert "DATABASE_URL" in str(raised.value)
    assert "://" not in str(raised.value), "the message echoes a connection URL"


# --- TR-044 / VR-021: the named constraints reject what they exist to reject -


@pytest.mark.parametrize(
    ("overrides", "constraint"),
    [
        (
            {"cost_usd": None, "cost_absent_reason": None},
            "ck_llm_invocation__cost_xor_absent_reason",
        ),
        (
            {"cost_usd": Decimal("1.0000000000"), "cost_absent_reason": "model_unresolved"},
            "ck_llm_invocation__cost_xor_absent_reason",
        ),
        ({"transport_attempt_count": 4}, "ck_llm_invocation__transport_attempts_in_budget"),
        ({"repair_attempt_count": 2}, "ck_llm_invocation__repair_attempts_in_budget"),
        ({"outcome": "unknown"}, "ck_llm_invocation__outcome_domain"),
        ({"resolution_mode": "sometimes"}, "ck_llm_invocation__resolution_mode_domain"),
        ({"trace_id": "0" * 32}, "ck_llm_invocation__trace_id_not_all_zero"),
        ({"trace_id": "Z" * 32}, "ck_llm_invocation__trace_id_format"),
        (
            {"resolution_mode": "replay", "fixture_key": None},
            "ck_llm_invocation__fixture_key_when_replaying",
        ),
        (
            {"resolution_mode": "replay", "fixture_key": "sha256:nothex"},
            "ck_llm_invocation__fixture_key_shape",
        ),
        ({"price_table_version_id": "no-such-version"}, "fk_llm_invocation__price_table_version"),
    ],
    ids=lambda value: value if isinstance(value, str) else "",
)
def test_a_malformed_row_is_rejected_by_the_named_constraint(
    writer: RecordWriter, overrides: dict[str, object], constraint: str
) -> None:
    """VR-021. Asserted on the constraint *name*, never on message text.

    Message text is locale- and version-dependent; the name is the schema's own
    identifier and is stable. This is the payoff for naming every constraint in
    the migrations, and it is what makes the drain's TR-054 log line useful.

    Some of these rows the record type refuses to build at all, which is the
    better outcome — a malformed pair caught before the write costs nothing.
    Those are covered in `test_read_contract.py` against the type; here the row
    is built past the model with `model_construct` so the *database* rule is
    what is under test.
    """
    record = InvocationRecord.model_construct(**{**a_record().model_dump(), **overrides})

    with pytest.raises(RecordWriteError) as raised:
        writer.write(record)

    assert raised.value.constraint_name == constraint, (
        f"expected {constraint!r} to reject the row, got "
        f"{raised.value.constraint_name!r}"
    )


def test_the_write_failure_carries_no_driver_exception(writer: RecordWriter) -> None:
    """TR-064's discipline, applied to the storage boundary.

    A driver exception's arguments can carry the connection string, and this
    error is logged and spooled. The constraint name is extracted and the
    exception is dropped — including as `__context__`, which `raise ... from
    None` inside the handler would have left populated.
    """
    record = InvocationRecord.model_construct(
        **{**a_record().model_dump(), "outcome": "unknown"}
    )
    with pytest.raises(RecordWriteError) as raised:
        writer.write(record)

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None, (
        "the driver exception survives as __context__ on the gateway error"
    )
    assert not isinstance(raised.value, psycopg.Error)


def test_a_second_write_of_one_invocation_is_refused_by_default(
    writer: RecordWriter, cleanup: list[str]
) -> None:
    """TR-011 / TR-045: exactly one row per invocation.

    Off the drain's path a primary-key conflict means an identifier was reused,
    which TR-045 says cannot happen — so it surfaces rather than being absorbed.
    The drain passes `idempotent=True` precisely because there the conflict is
    the designed recovery path, and the difference between the two is the whole
    reason the flag exists.
    """
    record = a_record()
    cleanup.append(record.invocation_id)
    writer.write(record)

    with pytest.raises(RecordWriteError):
        writer.write(record)


def test_the_drain_path_absorbs_the_same_conflict(
    writer: RecordWriter, cleanup: list[str]
) -> None:
    """TR-052: exactly-once is a property of the effect, not of delivery."""
    record = a_record()
    cleanup.append(record.invocation_id)
    writer.write(record)

    writer.write(record, idempotent=True)  # must not raise

    with psycopg.connect(_database_url()) as probe:
        count = probe.execute(
            "SELECT count(*) FROM llm_invocation WHERE invocation_id = %s",
            (uuid.UUID(record.invocation_id),),
        ).fetchone()
    assert count is not None and count[0] == 1, "a second row was written"


# --- TR-048: the pin resolves before anything is requested -------------------


def test_the_seeded_pin_resolves(writer: RecordWriter) -> None:
    assert writer.pin_resolves(SEEDED_VERSION)


def test_an_unknown_pin_does_not_resolve(writer: RecordWriter) -> None:
    """TR-048's whole point: this is answerable *before* a request is built, so
    an unresolvable pin is a configuration error on an invocation that never
    billed rather than a foreign-key failure after one did."""
    assert not writer.pin_resolves("no-such-version")


def test_price_entries_come_back_scoped_to_the_pin(writer: RecordWriter) -> None:
    """TR-039. The query owns which rows are visible; `resolve_price_entry`
    owns which of them wins."""
    entries = writer.price_entries(SEEDED_VERSION, "claude-sonnet-5")
    assert len(entries) == 2, (
        f"expected the two seeded effective-from rows, got {len(entries)}"
    )
    assert {entry.model_id for entry in entries} == {"claude-sonnet-5"}
    assert entries[0].rates.input_usd_per_mtok > 0


def test_price_entries_for_an_unknown_model_are_empty(writer: RecordWriter) -> None:
    """Absent, never nearest (TR-016). The empty list becomes
    `cost_absent_reason='no_covering_price_entry'` on the row."""
    assert writer.price_entries(SEEDED_VERSION, "claude-opus-5-turbo") == []
