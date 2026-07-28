"""Censoring status and elapsed time at the run's as-of date (FR-003, FR-004).

**`is_closed` is not the authority here, and that is the whole design.** The
loader's flag is a snapshot answer to an undated question; a run asks a dated
one. A line closed by an event that has not yet happened at the as-of date is
**censored**, however the column reads, and every as-of date earlier than the
load would otherwise be answered wrong. So the indicator is derived from the
line's own terminal event, which is the only fact that carries a date.

Elapsed time is anchored on `order_date` and the as-of date, never on the
terminal event: a delivered line and an open one measure the same quantity, and
what differs is which contribution `likelihood.py` takes for it. The difference
is signed — a line ordered after the anchor has not been ordered yet, and
clamping that to zero would report it as having survived no time at all.
"""

from __future__ import annotations

from datetime import UTC, date, datetime

from model.forecast.read import LifecycleEventRow, LineRow

__all__ = [
    "CensoringError",
    "censoring_indicator",
    "elapsed_days",
    "terminal_event",
]


class CensoringError(ValueError):
    """Raised when a line or an as-of date cannot be asked the dated question.

    A `ValueError`: the caller passed something that is not a line at an as-of
    date. Nothing about the database or the environment is at fault.
    """


def _checked(line: LineRow, as_of_date: date) -> None:
    """Both arguments, before either is read.

    A `datetime` is refused rather than truncated. It is a subclass of `date`,
    so an accidental one would pass the type test and then decide a boundary
    case — a line delivered on the as-of date — by an hour nobody supplied.
    """
    if not isinstance(line, LineRow):
        raise CensoringError(
            f"the censoring question is asked of a `LineRow`, found {type(line).__name__}; "
            f"the answer is derived from the line's own event sequence, which a looser "
            f"record would not carry"
        )
    if isinstance(as_of_date, datetime) or not isinstance(as_of_date, date):
        raise CensoringError(
            f"an as-of date is a `datetime.date`, found {type(as_of_date).__name__}; the "
            f"run's anchor is a calendar day, and an instant would decide the boundary "
            f"case by an hour no caller stated"
        )


def _occurred_on(event: LifecycleEventRow) -> date:
    """The calendar day an event happened on, in UTC.

    `read.py` normalizes every instant it returns, so the conversion is a
    no-op there; it is written anyway because taking `.date()` of an aware
    instant in some other zone would shift the boundary by a day, and that is
    the failure the boundary test exists to catch.
    """
    moment = event.occurred_at
    if moment.tzinfo is not None and moment.utcoffset() is not None:
        return moment.astimezone(UTC).date()
    return moment.date()


def terminal_event(line: LineRow) -> LifecycleEventRow | None:
    """The line's terminal event, or `None` if it has not reached one.

    The *terminal* event rather than the last one recorded. Re-deriving the
    state from whichever event has the highest `sequence_no` would answer
    correctly on a forward walk and wrongly on any line whose history was read
    mid-rework, and the monotone property is where that shows up.
    """
    return next((event for event in line.events if event.is_terminal), None)


def censoring_indicator(line: LineRow, as_of_date: date) -> bool:
    """`True` when no terminal event has occurred on or before the as-of date.

    FR-003, as an equality rather than an implication — both directions fail
    differently. An open line recorded as delivered contributes a density where
    it owes a survival term; a delivered line recorded as open discards the
    observation the fit exists for. The comparison is `<=`: an event that
    occurred on the as-of date has occurred.
    """
    _checked(line, as_of_date)
    terminal = terminal_event(line)
    return terminal is None or _occurred_on(terminal) > as_of_date


def elapsed_days(line: LineRow, as_of_date: date) -> int:
    """`as_of_date − order_date`, in whole days and signed.

    The identity `plan.md` § Mandated properties writes, and one quantity for
    both kinds of line: how long the line has been open at the anchor. It is a
    censored row's censoring time directly; a completed row's observed duration
    is measured to its terminal event instead, which is that caller's join to
    make and not a second meaning for this number.
    """
    _checked(line, as_of_date)
    return (as_of_date - line.order_date).days
