"""The committed calendar, as-of truncation, and DV-010's shape floors.

The as-of date and order window are **committed literals, never defaulted to
the run date** (FR-009) — a run-date default makes the content hash
unreproducible the day after generation while the recorded seed still appears
honoured.

DV-010's three bounds are asserted **jointly** as one admissible window, not
each alone: a run can satisfy any two and still be wrong.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, timedelta

__all__ = [
    "AS_OF_DATE",
    "CENSORED_SHARE_FLOOR",
    "DELIVERED_EVENT_FLOOR",
    "DELIVERED_SHARE_FLOOR",
    "ORDER_DATE_WINDOW",
    "OrderDateWindow",
    "ShapeFloorError",
    "check_shape_floors",
    "delivered_share_window",
    "event_dates",
    "is_delivered_by",
]


@dataclass(frozen=True, slots=True)
class OrderDateWindow:
    first: date
    last: date

    def contains(self, value: date) -> bool:
        return self.first <= value <= self.last


ORDER_DATE_WINDOW = OrderDateWindow(date(2025, 6, 16), date(2026, 2, 16))
AS_OF_DATE = date(2026, 4, 1)

#: FR-010's two floors on uncensored delivery events and its censored ceiling.
#: The event floor is `max(0.80 × N, 160)`; the two branches cross at N = 200.
DELIVERED_SHARE_FLOOR = 0.80
DELIVERED_EVENT_FLOOR = 160
CENSORED_SHARE_FLOOR = 0.10


class ShapeFloorError(ValueError):
    """Raised when the realized dataset shape breaches DV-010. Fails the run
    before any artifact is written — the remedy is a new seed or a widened
    window, never emitting the dataset anyway."""


def event_dates(order_date: date, leg_days: Sequence[int], as_of: date) -> list[date]:
    """Event dates for one line, truncated at `as_of`.

    Event 1 is the order date itself; each leg advances the clock. Truncation
    drops whole events rather than clamping them to `as_of`, because a clamped
    date would make `occurred_at` non-increasing and be rejected far downstream
    by the delivered chain constraint.
    """
    if order_date > as_of:
        return []
    emitted = [order_date]
    current = order_date
    for days in leg_days:
        if days < 1:
            raise ValueError(f"a leg must be at least one day, found {days}")
        current = current + timedelta(days=days)
        if current > as_of:
            break
        emitted.append(current)
    return emitted


def is_delivered_by(order_date: date, leg_days: Sequence[int], as_of: date) -> bool:
    """Whether the full chain — every leg — completes on or before `as_of`."""
    return len(event_dates(order_date, leg_days, as_of)) == len(leg_days) + 1


def delivered_share_window(line_count: int) -> tuple[float, float]:
    """The single admissible delivered share, `[max(0.80, 160/N), 0.90]`.

    Returned as a window rather than as three separate bounds so callers cannot
    check one and forget the others. The lower bound takes whichever of FR-010's
    two floors binds: below N = 200 the absolute 160 does, at or above it the
    80% share does.
    """
    if line_count < 1:
        raise ShapeFloorError(f"no admissible window at {line_count} lines")
    return (
        max(DELIVERED_SHARE_FLOOR, DELIVERED_EVENT_FLOOR / line_count),
        1.0 - CENSORED_SHARE_FLOOR,
    )


def check_shape_floors(
    line_count: int, delivered: int, non_terminal_occupancy: Mapping[str, int]
) -> None:
    """Enforce DV-010's three bounds jointly, before any artifact is written."""
    low, high = delivered_share_window(line_count)
    share = delivered / line_count

    if share < low:
        raise ShapeFloorError(
            f"{delivered} delivered of {line_count} is a share of {share:.4f}, below the "
            f"floor of {low:.4f} — FR-010 requires the greater of 80% and {DELIVERED_EVENT_FLOOR} "
            f"uncensored delivery events, measured before any split"
        )
    if share > high:
        raise ShapeFloorError(
            f"{delivered} delivered of {line_count} leaves a censored share of "
            f"{1 - share:.4f}, below the {CENSORED_SHARE_FLOOR:.2f} floor — the dataset has "
            f"too little right-censoring to demonstrate what it exists to demonstrate"
        )

    empty = sorted(state for state, count in non_terminal_occupancy.items() if count < 1)
    if empty:
        raise ShapeFloorError(
            f"non-terminal state(s) {', '.join(empty)} hold no line at the as-of date. "
            f"FR-010 makes this a hard failure rather than a warning; the remedy is a new "
            f"seed or a widened window, never emitting the dataset anyway"
        )
