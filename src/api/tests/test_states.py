"""FR-018a's precedence, over the pairs FR-033 says are jointly constructible.

A precedence test is only meaningful for combinations that can actually happen.
Asserting that roster mismatch beats already-late on a line where both hold is
evidence; asserting it on a line where only one can hold is a test of nothing,
and there is no way to tell the two apart by reading the test.

So FR-033 enumerates which pairs are constructible, and this module walks that
enumeration. The exclusions are as load-bearing as the inclusions: the three
date-derived states are pairwise exclusive by their own arithmetic —

    already late     `need_by <= as_of`
    calendar passed  `as_of < need_by <= today`
    beyond horizon   `need_by > as_of + horizon_days`

— with exactly one exception, beyond horizon and calendar passed, which
co-occur when `today > as_of + horizon_days`. That is `age_days > horizon_days`,
a run more than fifty times the staleness threshold's age at a 365-day horizon.
FR-038's injected `today` is what makes it constructible in a test at all,
rather than something that could only be observed by waiting a year.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any
from uuid import UUID, uuid4

import pytest

from api.risk_read.query import Conventions, ForecastRunRef, OpenLine, WorklistInputs
from api.risk_read.states import PageState, RowState, resolve_states

AS_OF = date(2026, 6, 1)
HORIZON = 365
RUN_ROSTER = "sha256:" + "a" * 64
OTHER_ROSTER = "sha256:" + "b" * 64


def run(**overrides: Any) -> ForecastRunRef:
    defaults: dict[str, Any] = {
        "run_id": UUID(int=99),
        "as_of_date": AS_OF,
        "horizon_days": HORIZON,
        "draw_count": 10,
        "roster_hash": RUN_ROSTER,
        "model_version": "lognormal-hierarchical-v3",
        "artifact_schema_version": 1,
    }
    return ForecastRunRef(**{**defaults, **overrides})


def line(
    *,
    need_by: date,
    covered: bool = True,
    roster_hash: str = RUN_ROSTER,
) -> OpenLine:
    return OpenLine(
        po_line_id=uuid4(),
        project_id="PRJ-001",
        vendor_id="VND-001",
        po_number="PO-4471",
        line_number=1,
        description="Air handling unit",
        quantity=1.0,
        unit_of_measure="EA",
        need_by_date=need_by,
        criticality=3,
        lifecycle_state="submitted",
        roster_hash=roster_hash,
        draws=(1.0, 2.0) if covered else None,
        survival=(0.5,) * HORIZON if covered else None,
        residual_tail_mass=0.5 if covered else None,
    )


def state_of(item: OpenLine, *, today: date, active: ForecastRunRef | None = run()) -> RowState:
    inputs = WorklistInputs(
        run=active,
        lines=(item,),
        today=today,
        conventions=Conventions(
            draw_count=10, percentile_convention="x", anchor_date_convention="y"
        ),
        available_projects=(),
    )
    resolved, _ = resolve_states(inputs)
    return resolved[0].state


#: Dates realising each date-derived state, against `AS_OF` and a horizon of 365.
BEFORE_ANCHOR = AS_OF - timedelta(days=10)
ON_ANCHOR = AS_OF
INSIDE_PASSED = AS_OF + timedelta(days=1)
INSIDE_FUTURE = AS_OF + timedelta(days=60)
LAST_IN_GRID = AS_OF + timedelta(days=HORIZON)
PAST_HORIZON = AS_OF + timedelta(days=HORIZON + 1)

#: Two days after the anchor: `INSIDE_PASSED` has been passed, nothing else has.
TODAY_FRESH = AS_OF + timedelta(days=2)

#: Past the whole horizon. FR-033's one exception needs this, and nothing less
#: reaches it — a run merely past the staleness threshold does not.
TODAY_PAST_HORIZON = AS_OF + timedelta(days=HORIZON + 30)


class TestEachStateAlone:
    """Every state is reachable on its own, or the pair tests below prove nothing."""

    def test_no_active_run(self) -> None:
        assert (
            state_of(line(need_by=INSIDE_FUTURE), today=TODAY_FRESH, active=None)
            is RowState.NO_ACTIVE_RUN
        )

    def test_roster_mismatch(self) -> None:
        assert (
            state_of(line(need_by=INSIDE_FUTURE, roster_hash=OTHER_ROSTER), today=TODAY_FRESH)
            is RowState.ROSTER_MISMATCH
        )

    def test_not_covered(self) -> None:
        assert (
            state_of(line(need_by=INSIDE_FUTURE, covered=False), today=TODAY_FRESH)
            is RowState.NOT_COVERED
        )

    def test_beyond_horizon(self) -> None:
        assert state_of(line(need_by=PAST_HORIZON), today=TODAY_FRESH) is RowState.BEYOND_HORIZON

    def test_the_last_in_grid_day_is_not_beyond_the_horizon(self) -> None:
        """The boundary itself. `need_by == as_of + horizon_days` has a grid
        entry to read, and the day after does not — one day apart, and the
        difference between a figure and a bound."""
        assert state_of(line(need_by=LAST_IN_GRID), today=TODAY_FRESH) is RowState.NOMINAL

    def test_already_late_before_the_anchor(self) -> None:
        assert state_of(line(need_by=BEFORE_ANCHOR), today=TODAY_FRESH) is RowState.ALREADY_LATE

    def test_already_late_on_the_anchor(self) -> None:
        """FR-030's equality case. The survival grid is one-based over
        `k = 1..horizon_days` and stores no `k = 0`, so at equality there is no
        offset to read — which is why the rule is `need_by <= as_of` and not
        "earlier than", and why this case belongs here rather than to the
        nominal row where no requirement would own it."""
        assert state_of(line(need_by=ON_ANCHOR), today=TODAY_FRESH) is RowState.ALREADY_LATE

    def test_calendar_passed(self) -> None:
        assert state_of(line(need_by=INSIDE_PASSED), today=TODAY_FRESH) is RowState.CALENDAR_PASSED

    def test_nominal(self) -> None:
        assert state_of(line(need_by=INSIDE_FUTURE), today=TODAY_FRESH) is RowState.NOMINAL


class TestConstructiblePairs:
    """FR-033's enumeration, and FR-018a's winner for each pair."""

    @pytest.mark.parametrize(
        "need_by", [BEFORE_ANCHOR, ON_ANCHOR, INSIDE_PASSED, INSIDE_FUTURE, PAST_HORIZON]
    )
    def test_roster_mismatch_beats_every_date_derived_state(self, need_by: date) -> None:
        """Roster mismatch is independent of every date-derived state, so it
        combines with all of them — and wins all of them.

        It wins because it makes the figure *untrustworthy* rather than merely
        annotating one: the run was fitted against a different population, so
        any figure would be about some other line. Every state that undermines
        trust in a figure outranks every state that merely annotates one.
        """
        assert (
            state_of(line(need_by=need_by, roster_hash=OTHER_ROSTER), today=TODAY_FRESH)
            is RowState.ROSTER_MISMATCH
        )

    @pytest.mark.parametrize(
        "need_by", [BEFORE_ANCHOR, ON_ANCHOR, INSIDE_PASSED, INSIDE_FUTURE, PAST_HORIZON]
    )
    def test_not_covered_beats_every_date_derived_state(self, need_by: date) -> None:
        """Not covered combines with all of them too — including beyond horizon,
        which is a comparison of dates and needs no posterior to hold. The
        posterior is only what the resulting *figure* would need, and precedence
        gives the row to not covered before any figure is read.
        """
        assert (
            state_of(line(need_by=need_by, covered=False), today=TODAY_FRESH)
            is RowState.NOT_COVERED
        )

    def test_roster_mismatch_beats_not_covered(self) -> None:
        """Both refuse to show a figure, and only roster mismatch names why.

        Within a class, the more specific cause wins, because the label is what
        tells a coordinator what to do — "the vendor records changed" is
        actionable and "there is no forecast for this line" is less so.
        """
        assert (
            state_of(
                line(need_by=INSIDE_FUTURE, covered=False, roster_hash=OTHER_ROSTER),
                today=TODAY_FRESH,
            )
            is RowState.ROSTER_MISMATCH
        )

    def test_beyond_horizon_beats_calendar_passed(self) -> None:
        """FR-033's one date-derived co-occurrence, and it needs a run older
        than its own whole horizon to construct.

        Beyond horizon wins because it makes the figure only *partially*
        available — a bound rather than a point — while calendar passed leaves
        every figure sound and merely says the date has gone by. Partially
        available outranks annotating.
        """
        assert state_of(line(need_by=PAST_HORIZON), today=TODAY_PAST_HORIZON) is (
            RowState.BEYOND_HORIZON
        )

    def test_already_late_beats_calendar_passed_by_construction(self) -> None:
        """Not a precedence decision — an arithmetic one. `need_by <= as_of`
        and `as_of < need_by` cannot both hold, so the first test to match wins
        because the second can never be reached, not because it was ranked."""
        assert state_of(line(need_by=BEFORE_ANCHOR), today=TODAY_PAST_HORIZON) is (
            RowState.ALREADY_LATE
        )


class TestPageStates:
    """The three page-scope states, which compose with a row label."""

    def test_no_active_run_governs_every_row_alone(self) -> None:
        """FR-033. With no run there is no as-of date, no horizon and no roster
        hash, so no row state is defined — which is why this one page state does
        not compose with any row state and is echoed onto every row instead."""
        inputs = WorklistInputs(
            run=None,
            lines=(line(need_by=PAST_HORIZON, covered=False, roster_hash=OTHER_ROSTER),),
            today=TODAY_FRESH,
            conventions=Conventions(
                draw_count=10, percentile_convention="x", anchor_date_convention="y"
            ),
            available_projects=(),
        )
        resolved, page = resolve_states(inputs)

        assert page == (PageState.NO_ACTIVE_RUN,)
        assert resolved[0].state is RowState.NO_ACTIVE_RUN

    def test_stale_run_composes_with_a_row_state(self) -> None:
        """A stale run does not stop a row from being beyond its horizon."""
        inputs = WorklistInputs(
            run=run(),
            lines=(line(need_by=PAST_HORIZON),),
            today=TODAY_PAST_HORIZON,
            conventions=Conventions(
                draw_count=10, percentile_convention="x", anchor_date_convention="y"
            ),
            available_projects=(),
        )
        resolved, page = resolve_states(inputs)

        assert PageState.STALE_RUN in page
        assert resolved[0].state is RowState.BEYOND_HORIZON

    def test_stale_and_no_active_run_are_never_both_reported(self) -> None:
        """FR-033. The pair is unsatisfiable — one requires no run and the other
        requires one — so admitting it would describe a state the data cannot
        produce."""
        for active, today in ((None, TODAY_PAST_HORIZON), (run(), TODAY_PAST_HORIZON)):
            inputs = WorklistInputs(
                run=active,
                lines=(),
                today=today,
                conventions=Conventions(
                    draw_count=10, percentile_convention="x", anchor_date_convention="y"
                ),
                available_projects=(),
            )
            _, page = resolve_states(inputs)
            assert not {PageState.NO_ACTIVE_RUN, PageState.STALE_RUN} <= set(page)

    def test_a_run_exactly_at_the_threshold_is_not_yet_stale(self) -> None:
        """FR-029. Seven days is the threshold and the comparison is strict, so
        the boundary day is fresh and the day after it is not — a fixture that
        only proved the far side would not distinguish `>` from `>=`."""
        boundary = run()
        assert boundary.age_days(AS_OF + timedelta(days=7)) == 7
        assert not boundary.is_stale(AS_OF + timedelta(days=7))
        assert boundary.is_stale(AS_OF + timedelta(days=8))
