"""TR-028 / TR-040 / TR-056: duration and attempt aggregation, as arithmetic.

Written before `compute/timing.py`, per the test-first rule for deterministic
computation.

**The point of putting this behind the computation boundary at all.** Duration
looks like the least arithmetic-shaped thing in the epic — it is "how long did
that take" — and that is exactly why it is worth isolating. A duration measured
inline in the module that made the call would be measured from whatever clock
was handy, over whatever interval the code happened to span, and neither choice
would be visible to a test. TR-056 fixes both: an integer count of milliseconds
from a **monotonic** clock, over an interval that starts at the first attempt
and stops at the terminal outcome.

**Monotonic, and the test says why.** A wall-clock duration goes negative when
NTP steps the clock backwards mid-invocation, and the row's `CHECK
(duration_ms >= 0)` would then reject a perfectly ordinary invocation — after
the provider had been paid. The property below is stated over arbitrary clock
readings rather than over a real elapsed interval, so it holds for the case
nobody can reproduce on demand.
"""

from __future__ import annotations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from gateway.compute.timing import (
    AttemptUsage,
    aggregate_usage,
    elapsed_ms,
)

#: `time.monotonic()` returns seconds as a float from an undefined origin. The
#: range here spans a machine that has been up for years, because the origin
#: being undefined is precisely what a naive implementation gets wrong.
instants = st.floats(min_value=0.0, max_value=3.0e8, allow_nan=False, allow_infinity=False)

counts = st.integers(min_value=0, max_value=10_000_000)


@st.composite
def attempts(draw: st.DrawFn) -> AttemptUsage:
    return AttemptUsage(
        input_tokens=draw(counts),
        cache_write_input_tokens=draw(counts),
        cache_read_input_tokens=draw(counts),
        output_tokens=draw(counts),
    )


# --- TR-056: the duration ----------------------------------------------------


@given(instants, st.floats(min_value=0.0, max_value=1.0e6, allow_nan=False))
@settings(max_examples=300)
def test_duration_is_never_negative(started: float, span: float) -> None:
    """The row's `CHECK (duration_ms >= 0)` must never be what discovers this.

    A constraint violation on the write path fails an invocation the provider
    was already paid for, which is the billed-but-untraced case the whole epic
    is arranged to prevent.
    """
    assert elapsed_ms(started, started + span) >= 0


@given(instants, instants)
@settings(max_examples=300)
def test_duration_is_an_integer_count_of_milliseconds(a: float, b: float) -> None:
    """TR-056 names the unit and the type. A float would make the column's
    equality assertions platform-sensitive, and the property tests behind
    SC-006 compare exact values."""
    value = elapsed_ms(min(a, b), max(a, b))
    assert isinstance(value, int)
    assert not isinstance(value, bool)


@given(instants, instants)
@settings(max_examples=300)
def test_a_clock_that_went_backwards_does_not_produce_a_negative_duration(
    a: float, b: float
) -> None:
    """The case a monotonic clock is supposed to make impossible, asserted
    anyway.

    `time.monotonic()` guarantees non-decreasing readings within a process, so
    on a correct call site the `end < start` branch is unreachable. It is
    covered because the guarantee belongs to the clock, not to this function —
    and this function is what the record write depends on. Clamping to zero is
    the defined behaviour rather than raising: an invocation that happened
    should be recorded, and a duration of zero is a defensible reading of a
    negative interval where a raise would lose the whole row.
    """
    assert elapsed_ms(max(a, b), min(a, b)) >= 0


def test_a_zero_length_interval_is_zero_not_one() -> None:
    """Rounding a sub-millisecond replay up to 1 ms would make every replayed
    invocation look like it took time it did not take — and replay resolution
    is meant to be fast enough that this is the common case."""
    assert elapsed_ms(100.0, 100.0) == 0


def test_sub_millisecond_intervals_round_rather_than_truncate() -> None:
    """0.6 ms is nearer 1 than 0. Truncation would report a systematic
    under-count across every fast invocation, which is invisible per row."""
    assert elapsed_ms(0.0, 0.0006) == 1
    assert elapsed_ms(0.0, 0.0004) == 0


def test_the_duration_is_measured_from_two_readings_not_from_a_clock() -> None:
    """TR-056 puts the record write *outside* the interval, which is only
    possible if the caller decides when the interval stops.

    A function that read the clock itself would stop it wherever it happened to
    be called, and the write would be inside the measurement — inflating every
    duration by the cost of the thing being measured.
    """
    import inspect

    parameters = list(inspect.signature(elapsed_ms).parameters)
    assert parameters == ["started_at", "ended_at"], (
        f"elapsed_ms takes {parameters}; reading a clock internally would put "
        f"the record write inside the measured interval"
    )


# --- TR-040 / TR-056: the aggregation ---------------------------------------


@given(st.lists(attempts(), min_size=1, max_size=6))
@settings(max_examples=300)
def test_usage_is_the_sum_across_every_attempt(sequence: list[AttemptUsage]) -> None:
    """TR-040. The stored cost is the invocation's total spend, not its final
    attempt's — an invocation that burned two retries and then succeeded was
    billed for all three."""
    total = aggregate_usage(sequence)
    assert total.input_tokens == sum(a.input_tokens for a in sequence)
    assert total.output_tokens == sum(a.output_tokens for a in sequence)
    assert total.cache_write_input_tokens == sum(a.cache_write_input_tokens for a in sequence)
    assert total.cache_read_input_tokens == sum(a.cache_read_input_tokens for a in sequence)


def test_an_attempt_reporting_no_usage_contributes_zero() -> None:
    """TR-056, stated as its own requirement because the alternative is not
    "leave it out" but "leave the term undefined".

    A transport failure returns no response body and therefore no usage. Summing
    over only the reporting attempts would give the same answer here — but it
    would also give an answer when *no* attempt reported, where the honest
    result is zero rather than an empty sum nobody defined.
    """
    reported = AttemptUsage(10, 2, 3, 5)
    silent = AttemptUsage(0, 0, 0, 0)
    assert aggregate_usage([reported, silent]) == aggregate_usage([reported])


def test_aggregating_no_attempts_is_refused() -> None:
    """A row exists only if at least one provider request or fixture lookup
    happened — `transport_attempt_count >= 1` is a `CHECK` on the table. An
    empty aggregate would be a row describing an invocation that never
    occurred, so it is refused here rather than written and rejected there."""
    with pytest.raises(ValueError, match="at least one attempt"):
        aggregate_usage([])


@given(st.lists(attempts(), min_size=1, max_size=6))
@settings(max_examples=200)
def test_aggregation_does_not_depend_on_attempt_order(
    sequence: list[AttemptUsage],
) -> None:
    """Addition is commutative, so this can only fail if the implementation
    stopped adding — took the last attempt, or the maximum, both of which are
    plausible-looking mistakes that satisfy the sum property for a
    single-attempt invocation."""
    assert aggregate_usage(sequence) == aggregate_usage(list(reversed(sequence)))


def test_the_last_attempt_alone_is_not_the_answer() -> None:
    """The specific mistake TR-040 exists to exclude, pinned by example.

    Recording only the successful attempt's usage is the natural
    implementation — it is the response you have in hand — and it understates
    every repaired invocation by exactly the cost of the attempt that failed.
    """
    first = AttemptUsage(100, 0, 0, 50)
    second = AttemptUsage(120, 0, 0, 60)
    total = aggregate_usage([first, second])
    assert total.input_tokens == 220, "usage was taken from one attempt, not summed"
    assert total.output_tokens == 110
