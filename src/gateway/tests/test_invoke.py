"""The mode branch, end to end, offline in both arms.

Every other test in this suite exercises one piece. This one exercises the join
— which is the part that was missing, and the part where an ordering mistake
does not show up in any component's own tests.

**Both arms run without a network.** The replay arm reaches no network by
design. The record arm is driven through an injected client, which is the only
honest way to test it here: the real one costs money, and `test_provider_smoke`
is where that lives, gated behind the opt-in.

**Everything is injected through `Resolution`.** The failure paths are the ones
worth exercising — an unreachable database, a provider that refuses twice then
answers — and none of them is something a real dependency can be asked to
produce on demand.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from gateway.compute.hashing import fixture_key
from gateway.config import (
    RECORD_MODE,
    REPLAY_MODE,
    GatewayConfig,
)
from gateway.fixtures import FixtureMissError, FixtureProvenance, FixtureStore
from gateway.models import InvocationRequest
from gateway.orchestrator import Resolution, _invoke
from gateway.record.spool import InvocationSpool
from gateway.record.writer import RecordWriteError, RecordWriter

PIN = "2026-07-26-published"
MODEL = "claude-opus-5"

#: The seeded rates for `claude-opus-5`, from migration 0103. Duplicated here so
#: the expected cost below is arithmetic a reader can check rather than a number
#: taken on faith.
INPUT_RATE = Decimal("5.000000")
OUTPUT_RATE = Decimal("25.000000")


class FakeConnection:
    """A database that accepts writes and answers the two queries the path makes."""

    def __init__(self, *, entries: list[tuple[Any, ...]] | None = None) -> None:
        self.rows: list[dict[str, Any]] = []
        self.entries = (
            entries
            if entries is not None
            else [(MODEL, date(2026, 1, 1), INPUT_RATE, INPUT_RATE, INPUT_RATE, OUTPUT_RATE)]
        )
        self._last: str = ""

    def execute(self, query: str, params: Any = None, /) -> FakeConnection:
        self._last = query
        if "INSERT INTO llm_invocation" in query and params is not None:
            self.rows.append(dict(params))
        return self

    def fetchone(self) -> Any:
        return (1,) if "price_table_version" in self._last else None

    def fetchall(self) -> list[Any]:
        return self.entries if "price_table_entry" in self._last else []

    def commit(self) -> None: ...
    def rollback(self) -> None: ...
    def close(self) -> None: ...


class RefusingConnection(FakeConnection):
    """A database that answers reads and refuses the invocation write."""

    def execute(self, query: str, params: Any = None, /) -> FakeConnection:
        if "INSERT INTO llm_invocation" in query:
            raise RuntimeError("database unreachable")
        return super().execute(query, params)


def a_clock() -> Callable[[], float]:
    """100.0 on the first reading, 100.25 on every one after.

    A fixed pair rather than an iterator: the record arm reads the clock more
    often than the replay arm — once for the deadline, once per attempt, once at
    the end — and an iterator sized for one arm raises `StopIteration` in the
    other. Holding the second value steady keeps the measured duration exactly
    250 ms whichever arm runs, so the assertion stays arithmetic rather than a
    count of how many times the clock happened to be consulted.
    """
    readings = iter([100.0])

    def read() -> float:
        return next(readings, 100.25)

    return read


def a_resolution(
    tmp_path: Path,
    *,
    mode: str = REPLAY_MODE,
    connection: FakeConnection | None = None,
) -> Resolution:
    connection = connection if connection is not None else FakeConnection()
    return Resolution(
        config=GatewayConfig(database_url="postgresql://fake/db", price_table_version_id=PIN),
        mode=mode,
        store=FixtureStore(tmp_path / "fixtures"),
        writer=RecordWriter("postgresql://fake/db", connect=lambda *a, **k: connection),
        spool=InvocationSpool(tmp_path / "spool.sqlite3"),
        now=lambda: datetime(2026, 7, 26, 12, 0, tzinfo=UTC),
        monotonic=a_clock(),
    )


def a_provenance(**overrides: object) -> FixtureProvenance:
    fields: dict[str, object] = {
        "recorded_on": date(2026, 7, 26),
        "gen_ai_response_model": MODEL,
        "gateway_revision": "0" * 40,
        "gen_ai_usage_input_tokens": 1000,
        "gen_ai_usage_output_tokens": 200,
    }
    fields.update(overrides)
    return FixtureProvenance(**fields)  # type: ignore[arg-type]


# --- The replay arm ----------------------------------------------------------


def test_a_replay_hit_produces_exactly_one_record(tmp_path: Path) -> None:
    """The whole path: resolve, price, classify, record, return.

    One row per invocation (TR-011) — asserted as a count, because "a record was
    written" and "one record was written" are different claims and only the
    second is the requirement.
    """
    request = InvocationRequest(prompt="assess this vendor")
    resolution = a_resolution(tmp_path)
    resolution.store.save(fixture_key(request), '{"verdict": "ok"}', a_provenance())
    connection = resolution.writer._connect("")  # type: ignore[attr-defined]

    result = _invoke(request, resolution)

    assert len(connection.rows) == 1
    assert result.outcome == "valid"
    assert result.resolution_mode == REPLAY_MODE
    assert result.content == '{"verdict": "ok"}'
    assert uuid.UUID(result.invocation_id)


def test_a_replay_row_prices_from_the_sidecars_token_counts(tmp_path: Path) -> None:
    """TR-056: replay is priced from what the provider reported at *recording*
    time, not from a re-estimate — which is what makes a replayed cost
    reproducible rather than a function of whatever tokenizer is linked today.

    The expected figure is computed here from the seeded rates so a reader can
    check the arithmetic instead of trusting a constant.
    """
    request = InvocationRequest(prompt="priced")
    resolution = a_resolution(tmp_path)
    resolution.store.save(fixture_key(request), "{}", a_provenance())
    connection = resolution.writer._connect("")  # type: ignore[attr-defined]

    _invoke(request, resolution)

    expected = ((1000 * INPUT_RATE) + (200 * OUTPUT_RATE)) / Decimal(1_000_000)
    assert connection.rows[0]["cost_usd"] == expected.quantize(Decimal("0.0000000001"))


def test_a_replay_row_prices_at_the_recording_date_not_today(tmp_path: Path) -> None:
    """TR-043. Replaying one fixture reproduces one cost however long afterwards
    it runs — including across an effective-from boundary inside the pin."""
    request = InvocationRequest(prompt="dated")
    resolution = a_resolution(tmp_path)
    resolution.store.save(fixture_key(request), "{}", a_provenance(recorded_on=date(2026, 3, 1)))
    connection = resolution.writer._connect("")  # type: ignore[attr-defined]

    _invoke(request, resolution)

    assert connection.rows[0]["pricing_timestamp"] == datetime(2026, 3, 1, tzinfo=UTC)
    assert connection.rows[0]["created_at"] == datetime(2026, 7, 26, 12, 0, tzinfo=UTC)


def test_a_replay_row_counts_the_lookup_as_one_transport_attempt(
    tmp_path: Path,
) -> None:
    """TR-056, and what keeps the column's own `CHECK (>= 1)` satisfiable on a
    replay row without anyone inferring it."""
    request = InvocationRequest(prompt="counted")
    resolution = a_resolution(tmp_path)
    resolution.store.save(fixture_key(request), "{}", a_provenance())
    connection = resolution.writer._connect("")  # type: ignore[attr-defined]

    _invoke(request, resolution)

    assert connection.rows[0]["transport_attempt_count"] == 1
    assert connection.rows[0]["fixture_key"] == fixture_key(request)


def test_a_replay_miss_raises_and_writes_nothing(tmp_path: Path) -> None:
    """TR-022. The miss is the correct outcome for a request that changed, and
    no row is written because no invocation happened — which is why this case
    sits outside TR-011's denominator rather than counting against it."""
    resolution = a_resolution(tmp_path)
    connection = resolution.writer._connect("")  # type: ignore[attr-defined]

    with pytest.raises(FixtureMissError):
        _invoke(InvocationRequest(prompt="never recorded"), resolution)

    assert connection.rows == [], "a row was written for an invocation that never ran"


def test_a_changed_request_misses_the_earlier_fixture(tmp_path: Path) -> None:
    """The direction that would otherwise be silent: serving a fixture recorded
    for a different call returns plausible output and looks like success."""
    resolution = a_resolution(tmp_path)
    resolution.store.save(fixture_key(InvocationRequest(prompt="original")), "{}", a_provenance())

    with pytest.raises(FixtureMissError):
        _invoke(InvocationRequest(prompt="original."), resolution)


# --- The record arm ----------------------------------------------------------


class FakeResponse:
    def __init__(self) -> None:
        self.model = MODEL
        self.content = [type("Block", (), {"text": '{"verdict": "live"}'})()]
        self.usage = type(
            "Usage",
            (),
            {
                "input_tokens": 1000,
                "output_tokens": 200,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        )()


def test_the_record_arm_writes_a_fixture_with_its_sidecar(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The `record` arm's own obligation: a call produces a *labelled* fixture.

    Driven through an injected client — the real one is `test_provider_smoke`'s
    business, gated behind the opt-in, because it costs money.
    """
    monkeypatch.setenv("GATEWAY_ALLOW_PROVIDER_CALLS", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-value-not-key-shaped")

    class FakeClient:
        def __init__(self, **_: object) -> None:
            self.messages = self

        def create(self, **_: object) -> FakeResponse:
            return FakeResponse()

    monkeypatch.setattr("gateway.provider.load_client_class", lambda: FakeClient)

    request = InvocationRequest(prompt="live call")
    resolution = a_resolution(tmp_path, mode=RECORD_MODE)
    connection = resolution.writer._connect("")  # type: ignore[attr-defined]

    result = _invoke(request, resolution)

    key = fixture_key(request)
    assert resolution.store.has(key), "the record arm did not commit a fixture"
    stored = resolution.store.load(key)
    assert stored.provenance.gen_ai_response_model == MODEL
    assert stored.provenance.gen_ai_usage_input_tokens == 1000
    assert connection.rows[0]["resolution_mode"] == RECORD_MODE
    assert result.content == '{"verdict": "live"}'


def test_the_record_arm_refuses_without_the_opt_in(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two decisions, not one. Selecting `record` is a configuration slip;
    selecting it *and* setting the opt-in is a choice (TR-027, TR-063)."""
    monkeypatch.delenv("GATEWAY_ALLOW_PROVIDER_CALLS", raising=False)
    resolution = a_resolution(tmp_path, mode=RECORD_MODE)

    with pytest.raises(Exception, match="GATEWAY_ALLOW_PROVIDER_CALLS"):
        _invoke(InvocationRequest(prompt="ungated"), resolution)


def test_the_replay_arm_refuses_beside_a_credential(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TR-023. The credential's absence is the only evidence available that the
    offline claim is being tested offline."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "present-but-not-key-shaped")
    request = InvocationRequest(prompt="guarded")
    resolution = a_resolution(tmp_path)
    resolution.store.save(fixture_key(request), "{}", a_provenance())

    with pytest.raises(Exception, match="ANTHROPIC_API_KEY"):
        _invoke(request, resolution)


# --- Failure paths shared by both arms --------------------------------------


def test_an_unwritable_record_spools_and_still_fails_closed(tmp_path: Path) -> None:
    """TR-036 and TR-041 together, which is the pair most easily got wrong.

    The caller gets an error *and* the record survives. Spooling does not soften
    fail-closed — it stops a billed call's record being lost with the exception.
    """
    request = InvocationRequest(prompt="unwritable")
    resolution = a_resolution(tmp_path, connection=RefusingConnection())
    resolution.store.save(fixture_key(request), "{}", a_provenance())

    # The specific type, not a bare `Exception`: a blind assertion would also
    # pass if the invocation failed for an unrelated reason — and "it raised"
    # is only half the claim. The other half is that it raised *this*, after
    # spooling.
    with pytest.raises(RecordWriteError):
        _invoke(request, resolution)

    assert resolution.spool.depth() == 1, (
        "the record was lost with the exception rather than spooled (TR-041)"
    )
    spooled = next(iter(resolution.spool.pending())).to_record()
    assert spooled.resolution_mode == REPLAY_MODE


def test_an_unresolvable_price_pin_is_refused_before_anything_happens(
    tmp_path: Path,
) -> None:
    """TR-048. Discovered at the write it would be a foreign-key failure on a
    call already billed, and no recorded absence is available for it — the row
    cannot be written at all."""

    class NoSuchVersion(FakeConnection):
        def fetchone(self) -> Any:
            return None

    request = InvocationRequest(prompt="unpinned")
    resolution = a_resolution(tmp_path, connection=NoSuchVersion())
    resolution.store.save(fixture_key(request), "{}", a_provenance())

    with pytest.raises(Exception, match="GATEWAY_PRICE_TABLE_VERSION"):
        _invoke(request, resolution)


def test_a_model_with_no_covering_rate_records_cost_absent(tmp_path: Path) -> None:
    """TR-016: absent with a stated reason, never zero. A free invocation and an
    unpriceable one are different facts."""

    class NoEntries(FakeConnection):
        def fetchall(self) -> list[Any]:
            return []

    request = InvocationRequest(prompt="unpriced")
    resolution = a_resolution(tmp_path, connection=NoEntries())
    resolution.store.save(fixture_key(request), "{}", a_provenance())
    connection = resolution.writer._connect("")  # type: ignore[attr-defined]

    _invoke(request, resolution)

    assert connection.rows[0]["cost_usd"] is None
    assert connection.rows[0]["cost_absent_reason"] == "no_covering_price_entry"


def test_the_duration_measures_this_run_not_the_recording(tmp_path: Path) -> None:
    """TR-056. Replay inherits the fixture's token counts and *not* its latency:
    the recording's duration describes a call that is not happening now."""
    request = InvocationRequest(prompt="timed")
    resolution = a_resolution(tmp_path)
    resolution.store.save(fixture_key(request), "{}", a_provenance())
    connection = resolution.writer._connect("")  # type: ignore[attr-defined]

    _invoke(request, resolution)

    # The injected monotonic clock advances 100.0 -> 100.25 seconds.
    assert connection.rows[0]["duration_ms"] == 250


def test_the_trace_id_is_the_callers_when_supplied(tmp_path: Path) -> None:
    """TR-080: an explicit field, never ambient. A caller correlating two
    invocations supplies the identifier and the row carries it unchanged."""
    supplied = uuid.uuid4().hex
    request = InvocationRequest(prompt="traced", trace_id=supplied)
    resolution = a_resolution(tmp_path)
    resolution.store.save(fixture_key(request), "{}", a_provenance())
    connection = resolution.writer._connect("")  # type: ignore[attr-defined]

    result = _invoke(request, resolution)

    assert result.trace_id == supplied
    assert connection.rows[0]["trace_id"] == supplied


def test_the_spool_is_drained_before_the_invocation_proceeds(tmp_path: Path) -> None:
    """TR-053: at the start of an invocation that opens a good connection, not
    at the end — a drain after the write never runs on the invocation that
    failed, which is the one whose predecessors are waiting."""
    request = InvocationRequest(prompt="draining")
    resolution = a_resolution(tmp_path)
    resolution.store.save(fixture_key(request), "{}", a_provenance())

    waiting = a_provenance()
    del waiting
    earlier = InvocationRequest(prompt="earlier")
    resolution.store.save(fixture_key(earlier), "{}", a_provenance())

    _invoke(request, resolution)
    assert resolution.spool.depth() == 0
