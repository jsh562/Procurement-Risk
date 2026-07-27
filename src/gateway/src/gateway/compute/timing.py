"""Duration arithmetic and attempt aggregation — pure, and clock-free.

TR-028, TR-040, TR-056. Behind the computation boundary with `pricing.py`, and
for the same reason: `gateway.provider` may not import this, so the retry loop
cannot compute a duration and the orchestration module above both composes them
instead.

**Nothing here reads a clock.** `elapsed_ms` takes two readings rather than
taking one and calling `time.monotonic()` for the other. That is what lets
TR-056 place the record write *outside* the measured interval — a function that
stopped the clock itself would stop it wherever it was called, and the write
would land inside the measurement it is supposed to follow. It also makes every
property below testable without sleeping, which is the difference between a
suite that asserts the arithmetic and one that asserts the machine was not busy.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

__all__ = ["AttemptUsage", "aggregate_usage", "elapsed_ms"]

_MILLISECONDS_PER_SECOND = 1000


@dataclass(frozen=True, slots=True)
class AttemptUsage:
    """What one attempt reported, by billing class.

    An attempt that returned no response body reports zeros rather than being
    omitted (TR-056) — so the sum is defined over *every* attempt, including
    the case where none of them reported anything.
    """

    input_tokens: int
    cache_write_input_tokens: int
    cache_read_input_tokens: int
    output_tokens: int


def elapsed_ms(started_at: float, ended_at: float) -> int:
    """Milliseconds between two monotonic-clock readings.

    Args:
        started_at: `time.monotonic()` when the gateway began the first attempt.
        ended_at: `time.monotonic()` when it reached the terminal outcome —
            after validation and any repair, before the record write (TR-056).

    Returns:
        A non-negative integer count of milliseconds.

    **Rounded, not truncated.** Truncation would under-report every invocation
    by up to a millisecond, which is invisible in one row and systematic across
    all of them. Sub-millisecond replays are the common case, so the bias would
    land hardest on exactly the mode designed to be fast.

    **Clamped at zero rather than raising.** `time.monotonic()` is
    non-decreasing within a process, so a negative interval is unreachable from
    a correct call site. It is handled anyway because the guarantee belongs to
    the clock and this function is what the record write depends on: an
    invocation that happened should be recorded, and the row's
    `CHECK (duration_ms >= 0)` rejecting it would fail-close an invocation the
    provider was already paid for. Zero is a defensible reading of a negative
    interval; losing the row is not.
    """
    delta = ended_at - started_at
    if delta <= 0:
        return 0
    return round(delta * _MILLISECONDS_PER_SECOND)


def aggregate_usage(attempts: Sequence[AttemptUsage]) -> AttemptUsage:
    """Sum token counts across every attempt of one invocation (TR-040).

    The stored cost is the invocation's **total spend**, not its final
    attempt's. An invocation that consumed two transport retries and then
    repaired successfully was billed for every one of those calls, and recording
    only the successful attempt's usage — the natural implementation, since that
    response is the one in hand — understates it by exactly the cost of the
    attempts that failed.

    Raises:
        ValueError: No attempts. A row exists only if at least one provider
            request or fixture lookup happened, which the table states as
            `transport_attempt_count >= 1`. An empty aggregate would describe an
            invocation that never occurred, so it is refused here rather than
            written and rejected at the constraint.
    """
    if not attempts:
        raise ValueError(
            "an invocation record requires at least one attempt; a row exists "
            "only if a provider request was issued or a fixture was resolved"
        )
    return AttemptUsage(
        input_tokens=sum(attempt.input_tokens for attempt in attempts),
        cache_write_input_tokens=sum(attempt.cache_write_input_tokens for attempt in attempts),
        cache_read_input_tokens=sum(attempt.cache_read_input_tokens for attempt in attempts),
        output_tokens=sum(attempt.output_tokens for attempt in attempts),
    )
