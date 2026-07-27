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
from pydantic import BaseModel, ConfigDict, Field

from gateway.compute.hashing import fixture_key, repair_fixture_key
from gateway.config import (
    RECORD_MODE,
    REPLAY_MODE,
    GatewayConfig,
)
from gateway.errors import GatewayValidationError
from gateway.fixtures import FixtureMissError, FixtureProvenance, FixtureStore
from gateway.models import InvocationRequest
from gateway.orchestrator import Resolution, _invoke
from gateway.record.spool import InvocationSpool
from gateway.record.writer import RecordWriteError, RecordWriter
from gateway.validation import ValidationFailure, repair_instruction

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


# --- TR-005 to TR-008: validation through the composed path ------------------
#
# The unit tests in test_validation_repair.py prove `validate_or_repair` works.
# These prove `invoke()` *uses* it — a different claim, and one that was false
# until this change: the entry point hardcoded zero repairs and returned
# whatever came back, so TR-006 held in units and was violated on every call.


class Assessment(BaseModel):
    """A caller's schema. `score` has bounds the native mode cannot express, so
    enforcing them is the post-decode step's job (TR-005)."""

    model_config = ConfigDict(extra="forbid")

    label: str = Field(min_length=3)
    score: int = Field(ge=1, le=10)


def _failing_instruction() -> str:
    """The repair instruction the two failures below actually produce.

    Built from `repair_instruction` rather than typed as a literal: the repair
    fixture is keyed on this text, so a literal that drifted from what
    `validate_or_repair` generates would make every repair miss — and the test
    would be asserting against a key nothing writes.
    """
    return repair_instruction(
        [
            ValidationFailure("label", "String should have at least 3 characters"),
            ValidationFailure("score", "Input should be less than or equal to 10"),
        ]
    )


def test_a_valid_response_is_validated_and_returned(tmp_path: Path) -> None:
    """TR-006's happy path, through the entry point rather than the unit."""
    request = InvocationRequest(prompt="assess", output_schema=Assessment)
    resolution = a_resolution(tmp_path)
    resolution.store.save(
        fixture_key(request, schema=Assessment),
        '{"label":"good","score":7}',
        a_provenance(),
    )
    connection = resolution.writer._connect("")  # type: ignore[attr-defined]

    result = _invoke(request, resolution)

    assert result.outcome == "valid"
    assert connection.rows[0]["repair_attempt_count"] == 0
    assert connection.rows[0]["error_type"] is None
    assert Assessment.model_validate_json(result.content).score == 7


def test_a_repaired_invocation_records_one_repair(tmp_path: Path) -> None:
    """TR-007 through the composed path, and the reason replay repairs from a
    *second* fixture: a recorded repair must replay as a repair, or `repaired`
    is unreachable in the only mode continuous integration runs."""
    request = InvocationRequest(prompt="assess", output_schema=Assessment)
    resolution = a_resolution(tmp_path)
    resolution.store.save(
        fixture_key(request, schema=Assessment),
        '{"label":"x","score":99}',
        a_provenance(),
    )
    resolution.store.save(
        repair_fixture_key(request, _failing_instruction(), schema=Assessment),
        '{"label":"fixed","score":4}',
        a_provenance(),
    )
    connection = resolution.writer._connect("")  # type: ignore[attr-defined]

    result = _invoke(request, resolution)

    assert result.outcome == "repaired"
    assert connection.rows[0]["repair_attempt_count"] == 1
    assert connection.rows[0]["error_type"] is None
    assert Assessment.model_validate_json(result.content).label == "fixed"


def test_a_second_failure_writes_the_row_then_raises(tmp_path: Path) -> None:
    """TR-008, and the ordering is the requirement.

    The row must exist *before* the error reaches the caller, so someone
    catching it can rely on the trace being there — a paid call is never left
    with no record of itself.
    """
    request = InvocationRequest(prompt="assess", output_schema=Assessment)
    resolution = a_resolution(tmp_path)
    bad = '{"label":"x","score":99}'
    resolution.store.save(fixture_key(request, schema=Assessment), bad, a_provenance())
    resolution.store.save(
        repair_fixture_key(request, _failing_instruction(), schema=Assessment),
        bad,
        a_provenance(),
    )
    connection = resolution.writer._connect("")  # type: ignore[attr-defined]

    with pytest.raises(GatewayValidationError):
        _invoke(request, resolution)

    assert len(connection.rows) == 1, "the row was not written before the raise (TR-008)"
    assert connection.rows[0]["outcome"] == "failed"
    assert connection.rows[0]["error_type"] == "validation_failed"


def test_an_unvalidated_value_never_reaches_the_caller(tmp_path: Path) -> None:
    """TR-006's prohibition, stated as something a caller can observe.

    The failure path raises rather than returning a result carrying an error, so
    there is no shape in which the rejected value comes back — including through
    the exception, which would be the same value arriving by a quieter route.
    """
    request = InvocationRequest(prompt="assess", output_schema=Assessment)
    resolution = a_resolution(tmp_path)
    bad = '{"label":"x","score":99}'
    resolution.store.save(fixture_key(request, schema=Assessment), bad, a_provenance())
    resolution.store.save(
        repair_fixture_key(request, _failing_instruction(), schema=Assessment),
        bad,
        a_provenance(),
    )

    with pytest.raises(GatewayValidationError) as raised:
        _invoke(request, resolution)

    assert "99" not in str(raised.value), "the rejected value came back through the error"


def test_no_schema_skips_validation_and_claims_nothing(tmp_path: Path) -> None:
    """A caller wanting raw text is legitimate. What the gateway must not do is
    pretend it checked — so the raw content comes back unchanged, and the row's
    `outcome` carries no claim about a schema nobody supplied."""
    request = InvocationRequest(prompt="unchecked")
    resolution = a_resolution(tmp_path)
    resolution.store.save(fixture_key(request), "not json at all", a_provenance())
    connection = resolution.writer._connect("")  # type: ignore[attr-defined]

    result = _invoke(request, resolution)

    assert result.content == "not json at all"
    assert connection.rows[0]["repair_attempt_count"] == 0
    assert len(connection.rows) == 1


def test_the_schema_reaches_the_fixture_key(tmp_path: Path) -> None:
    """TR-038: a schema change must change the key, or a validator edit leaves
    every earlier fixture matching.

    The field is *excluded* from the request's JSON dump — a class is not JSON —
    so this is the assertion that it still reaches the hash, by digest.
    """

    class Wider(BaseModel):
        model_config = ConfigDict(extra="forbid")

        label: str = Field(min_length=3)
        score: int = Field(ge=1, le=100)

    request = InvocationRequest(prompt="same prompt", output_schema=Assessment)
    widened = InvocationRequest(prompt="same prompt", output_schema=Wider)

    assert fixture_key(request, schema=Assessment) != fixture_key(widened, schema=Wider)


def test_a_repair_key_never_collides_with_its_original() -> None:
    """They are different calls and must not answer each other."""
    request = InvocationRequest(prompt="assess", output_schema=Assessment)
    assert repair_fixture_key(request, "fix it", schema=Assessment) != fixture_key(
        request, schema=Assessment
    )


def test_two_different_failures_do_not_share_one_repair() -> None:
    """The reason the instruction is in the key rather than a constant marker.
    Two invocations that failed differently need different repairs."""
    request = InvocationRequest(prompt="assess", output_schema=Assessment)
    first = repair_fixture_key(request, "fix the score", schema=Assessment)
    second = repair_fixture_key(request, "fix the label", schema=Assessment)
    assert first != second


def test_a_missing_repair_fixture_is_a_miss(tmp_path: Path) -> None:
    """The actionable failure: the original was recorded before the repair path
    existed, or the schema changed. Either way the fix is to regenerate."""
    request = InvocationRequest(prompt="assess", output_schema=Assessment)
    resolution = a_resolution(tmp_path)
    resolution.store.save(
        fixture_key(request, schema=Assessment),
        '{"label":"x","score":99}',
        a_provenance(),
    )

    with pytest.raises(FixtureMissError):
        _invoke(request, resolution)


def test_the_record_arm_repairs_by_asking_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """TR-007 on the live arm: a repair is a *second* provider call carrying the
    failing field path, and it is recorded as its own fixture."""
    monkeypatch.setenv("GATEWAY_ALLOW_PROVIDER_CALLS", "1")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-value-not-key-shaped")

    replies = iter(['{"label":"x","score":99}', '{"label":"good","score":5}'])
    prompts: list[str] = []

    class TwoShotClient:
        def __init__(self, **_: object) -> None:
            self.messages = self

        def create(self, **kwargs: Any) -> Any:
            prompts.append(kwargs["messages"][0]["content"])
            reply = next(replies)
            usage = type(
                "U",
                (),
                {
                    "input_tokens": 100,
                    "output_tokens": 20,
                    "cache_creation_input_tokens": 0,
                    "cache_read_input_tokens": 0,
                },
            )()
            return type(
                "R",
                (),
                {
                    "model": MODEL,
                    "content": [type("B", (), {"text": reply})()],
                    "usage": usage,
                },
            )()

    monkeypatch.setattr("gateway.provider.load_client_class", lambda: TwoShotClient)

    request = InvocationRequest(prompt="assess", output_schema=Assessment)
    resolution = a_resolution(tmp_path, mode=RECORD_MODE)
    connection = resolution.writer._connect("")  # type: ignore[attr-defined]

    result = _invoke(request, resolution)

    assert result.outcome == "repaired"
    assert connection.rows[0]["repair_attempt_count"] == 1
    assert len(prompts) == 2, "the repair did not issue a second request"
    assert "score" in prompts[1], "the repair carried no failing field path"
    assert resolution.store.has(fixture_key(request, schema=Assessment))
