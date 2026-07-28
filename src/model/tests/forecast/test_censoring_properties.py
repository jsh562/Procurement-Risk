"""T019 (RED) — the censoring indicator and elapsed time, at the property tier.

`plan.md` § Mandated properties gives `censoring.py` two relations. **Invariant**:
a line is censored exactly when its terminal event is absent at the as-of date,
and elapsed time equals `as_of_date − order_date`. **Metamorphic (monotone)**: a
later as-of date never moves a line from delivered to censored. The domain is
as-of dates before the order window, on a terminal event's own date, and far
after the window. The module under test does not exist yet — this file is the
RED half of T019/T020 and must fail at collection.
"""

from __future__ import annotations

import uuid
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from model.forecast.censoring import censoring_indicator, elapsed_days

from model.forecast.read import LifecycleEventRow, LineRow
from model.procurement.censor import AS_OF_DATE, ORDER_DATE_WINDOW

NS = uuid.uuid5(uuid.NAMESPACE_URL, "e007/tests/forecast/censoring-properties")

#: The realized order window's midpoint, used wherever one concrete line is
#: enough. Both endpoints and the committed as-of date come from
#: `model.procurement.censor`, so the swept domain is the dataset's own rather
#: than a set of dates chosen here.
ORDER_DATE = ORDER_DATE_WINDOW.first + timedelta(days=120)

#: The forward path a delivered line walks, terminal event last.
WALK = ("submitted", "under_review", "approved", "released_for_fabrication", "shipped", "delivered")


def make_event(
    po_line_id: uuid.UUID,
    sequence_no: int,
    to_state: str,
    when: date,
    *,
    from_state: str | None = None,
) -> LifecycleEventRow:
    """One event at midnight UTC on `when` — the instant E005's loader stores."""
    return LifecycleEventRow(
        event_id=uuid.uuid5(NS, f"evt|{po_line_id}|{sequence_no}"),
        po_line_id=po_line_id,
        sequence_no=sequence_no,
        from_state=from_state,
        to_state=to_state,
        is_terminal=to_state == "delivered",
        occurred_at=datetime(when.year, when.month, when.day, tzinfo=UTC),
        note=None,
    )


def make_line(
    *,
    order_date: date = ORDER_DATE,
    events: tuple[LifecycleEventRow, ...] = (),
    line_number: int = 1,
    **overrides: Any,
) -> LineRow:
    po_line_id = uuid.uuid5(NS, f"pol|{order_date}|{line_number}")
    base = LineRow(
        po_line_id=po_line_id,
        project_id="PRJ-001",
        vendor_id="VND-001",
        po_number="PO-001-0001",
        line_number=line_number,
        material_category="WATER_CHILLER",
        description="Water Chiller (Tag 201-14)",
        manufacturer="Ironvane Thermal",
        part_number="IRV-236500-0001",
        quantity=Decimal("6.0"),
        unit_of_measure="EA",
        order_date=order_date,
        need_by_date=order_date + timedelta(days=120),
        criticality=3,
        lifecycle_state=events[-1].to_state if events else "submitted",
        is_closed=bool(events) and events[-1].is_terminal,
        closing_event_id=events[-1].event_id if events and events[-1].is_terminal else None,
        roster_hash="sha256:" + "0" * 64,
        events=events,
    )
    return replace(base, **overrides)


def walked_line(order_date: date, legs: tuple[int, ...], *, line_number: int = 1) -> LineRow:
    """A line that walked `len(legs)` forward transitions, one per leg in days.

    Six legs reach `delivered` and anything short of that leaves the line open,
    which is the only distinction censoring turns on. Event dates accumulate, so
    the terminal event lands `sum(legs)` days after the order date.
    """
    po_line_id = uuid.uuid5(NS, f"pol|{order_date}|{line_number}")
    events: list[LifecycleEventRow] = []
    when = order_date
    for index, leg in enumerate(legs):
        when = when + timedelta(days=leg)
        events.append(
            make_event(
                po_line_id,
                index + 1,
                WALK[index],
                when,
                from_state=WALK[index - 1] if index else None,
            )
        )
    return make_line(order_date=order_date, events=tuple(events), line_number=line_number)


def terminal_date(line: LineRow) -> date | None:
    """The calendar date the line's terminal event occurred on, or `None`."""
    return next((event.occurred_at.date() for event in line.events if event.is_terminal), None)


def is_censored_by_definition(line: LineRow, as_of: date) -> bool:
    """FR-003 restated: no terminal event has happened on or before the as-of date."""
    terminal = terminal_date(line)
    return terminal is None or terminal > as_of


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

#: Leg lengths in days. A first leg of 0 is excluded because `occurred_at` must
#: strictly increase with `sequence_no` (E005's lifecycle rule).
legs = st.lists(st.integers(min_value=1, max_value=60), min_size=0, max_size=6).map(tuple)

order_dates = st.dates(min_value=ORDER_DATE_WINDOW.first, max_value=ORDER_DATE_WINDOW.last)

#: Swept across and beyond the window, which is the domain the metamorphic row
#: names: a year before the first order and three years after the last.
as_of_dates = st.dates(
    min_value=ORDER_DATE_WINDOW.first - timedelta(days=365),
    max_value=ORDER_DATE_WINDOW.last + timedelta(days=1095),
)


@st.composite
def lines(draw: st.DrawFn) -> LineRow:
    return walked_line(draw(order_dates), draw(legs))


# ---------------------------------------------------------------------------
# Invariant: censored exactly when the terminal event is absent
# ---------------------------------------------------------------------------


@given(line=lines(), as_of=as_of_dates)
def test_a_line_is_censored_exactly_when_its_terminal_event_is_absent(
    line: LineRow, as_of: date
) -> None:
    """FR-003, stated as an equality rather than as an implication.

    Both directions matter and they fail differently: an open line recorded as
    delivered contributes a density where it owes a survival term, and a
    delivered line recorded as open throws away the observation the fit is for.
    """
    assert censoring_indicator(line, as_of) == is_censored_by_definition(line, as_of)


@given(line=lines(), as_of=as_of_dates)
def test_the_indicator_returns_a_boolean(line: LineRow, as_of: date) -> None:
    """`forecast_split_assignment.is_censored` is `boolean` NOT NULL — not a truthy int."""
    assert isinstance(censoring_indicator(line, as_of), bool)


@given(line=lines(), as_of=as_of_dates)
def test_the_stored_closure_flag_is_not_the_authority(line: LineRow, as_of: date) -> None:
    """`is_closed` is the load snapshot's answer, and the run asks a dated question.

    A line closed by an event that has not happened yet at the run's as-of date
    is **censored**, however the loader flagged it. This is the case that
    separates reading the events from reading the column, and taking the column
    would be wrong on every as-of date earlier than the load.
    """
    terminal = terminal_date(line)
    if line.is_closed and terminal is not None and terminal > as_of:
        assert censoring_indicator(line, as_of) is True


def test_a_line_with_no_event_at_all_is_censored() -> None:
    """The degenerate input: nothing has happened, so nothing terminal has."""
    assert censoring_indicator(make_line(events=()), AS_OF_DATE) is True


# ---------------------------------------------------------------------------
# Boundary: the terminal event's own date
# ---------------------------------------------------------------------------


def test_a_line_is_delivered_on_its_terminal_events_own_date() -> None:
    """The boundary the plan names: `<=`, not `<`.

    An event that occurred on the as-of date has occurred. Reading the
    comparison strictly would censor every line delivered on the snapshot day
    and hand the fit a survival term for an observed delivery.
    """
    line = walked_line(ORDER_DATE, (10, 8, 6, 20, 14, 9))
    delivered_on = terminal_date(line)
    assert delivered_on is not None

    assert censoring_indicator(line, delivered_on) is False
    assert censoring_indicator(line, delivered_on - timedelta(days=1)) is True
    assert censoring_indicator(line, delivered_on + timedelta(days=1)) is False


def test_an_as_of_before_the_order_window_censors_every_line() -> None:
    """Nothing can have terminated before its own line was ordered."""
    before = ORDER_DATE_WINDOW.first - timedelta(days=1)
    for legs_walked in ((), (5,), (10, 8, 6, 20, 14, 9)):
        assert censoring_indicator(walked_line(ORDER_DATE, legs_walked), before) is True


def test_an_as_of_far_after_the_window_leaves_only_the_never_delivered_censored() -> None:
    """Three years past the last order date: the open line stays open forever."""
    far_after = ORDER_DATE_WINDOW.last + timedelta(days=1095)

    assert censoring_indicator(walked_line(ORDER_DATE, (10, 8, 6, 20, 14, 9)), far_after) is False
    assert censoring_indicator(walked_line(ORDER_DATE, (10, 8, 6)), far_after) is True


# ---------------------------------------------------------------------------
# Metamorphic (monotone): a later as-of never re-censors
# ---------------------------------------------------------------------------


@given(line=lines(), first=as_of_dates, second=as_of_dates)
def test_a_later_as_of_date_never_moves_a_line_from_delivered_to_censored(
    line: LineRow, first: date, second: date
) -> None:
    """Monotone in the as-of date, over the whole swept domain.

    Delivery is absorbing: once a terminal event is in the past it stays in the
    past. An implementation that compared against a window, or that re-derived
    the state from the *last* event rather than from the terminal one, would
    break here and nowhere else in this file.
    """
    earlier, later = sorted((first, second))
    if not censoring_indicator(line, earlier):
        assert not censoring_indicator(line, later)


@given(line=lines())
def test_the_indicator_falls_at_most_once_across_the_whole_sweep(line: LineRow) -> None:
    """The monotone claim as one sequence rather than as a sampled pair.

    Swept day by day from before the order window to well past it, the indicator
    is `True` for a prefix and `False` for the rest — at most one transition.
    Two transitions would mean a line un-delivered itself, which the pairwise
    form above can miss when Hypothesis never draws the straddling pair.
    """
    start = ORDER_DATE_WINDOW.first - timedelta(days=30)
    sweep = [censoring_indicator(line, start + timedelta(days=step * 7)) for step in range(0, 120)]
    transitions = sum(1 for a, b in zip(sweep, sweep[1:], strict=False) if a != b)

    assert transitions <= 1, "the censoring indicator changed direction across the sweep"
    assert sweep == sorted(sweep, reverse=True), "censored must precede delivered, never follow it"


# ---------------------------------------------------------------------------
# Invariant: elapsed time
# ---------------------------------------------------------------------------


@given(line=lines(), as_of=as_of_dates)
def test_elapsed_is_the_as_of_date_minus_the_order_date(line: LineRow, as_of: date) -> None:
    """The identity as § Mandated properties writes it, over the whole sweep.

    Anchored on the line's own `order_date` — the same anchor
    `fk_held_out_prediction__line_anchor` proves structurally — and on the run's
    as-of date, never on the terminal event. A delivered line's elapsed time is
    the same quantity as an open one's; what differs is which contribution the
    likelihood takes, and that is `censoring.py`'s other function.
    """
    assert elapsed_days(line, as_of) == (as_of - line.order_date).days


@given(line=lines(), as_of=as_of_dates)
def test_elapsed_is_a_plain_integer_count_of_days(line: LineRow, as_of: date) -> None:
    assert isinstance(elapsed_days(line, as_of), int)
    assert not isinstance(elapsed_days(line, as_of), bool)


@pytest.mark.parametrize(
    ("as_of", "expected"),
    [
        (ORDER_DATE - timedelta(days=30), -30),  # before the order window
        (ORDER_DATE, 0),  # the order date itself
        (ORDER_DATE + timedelta(days=1), 1),
        (ORDER_DATE + timedelta(days=365), 365),  # far after the window
    ],
)
def test_elapsed_at_the_named_boundaries(as_of: date, expected: int) -> None:
    """Including the negative case, which the domain column names first.

    An as-of date before the order window is a real configuration — the fit is
    run at a dated anchor and a line ordered after it has not been ordered yet.
    The signed difference is the honest answer; clamping it to zero would report
    a not-yet-ordered line as having survived zero days, which is exactly the
    line a fit must not train on and would then be unable to distinguish.
    """
    assert elapsed_days(walked_line(ORDER_DATE, (10, 8)), as_of) == expected


def test_elapsed_is_monotone_in_the_as_of_date() -> None:
    """One day later is one day more, with no dependence on the events walked."""
    line = walked_line(ORDER_DATE, (10, 8, 6, 20, 14, 9))
    for step in range(0, 200, 7):
        as_of = ORDER_DATE + timedelta(days=step)
        assert elapsed_days(line, as_of + timedelta(days=1)) == elapsed_days(line, as_of) + 1
