"""Property tests for the calendar and as-of truncation (FR-009, FR-010).

A wrong truncation still emits a valid short line, which is why this is a
mandatory property module rather than a unit check.
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest

from model.procurement.censor import (
    AS_OF_DATE,
    ORDER_DATE_WINDOW,
    ShapeFloorError,
    check_shape_floors,
    delivered_share_window,
    event_dates,
    is_delivered_by,
)

#: A line's legs, forward only, in emission order.
LEGS = [7, 12, 5, 28, 9]


class TestCommittedConstants:
    def test_the_window_and_as_of_are_committed_literals(self) -> None:
        """FR-009: never defaulted to the run date, or the committed hash stops
        reproducing the day after generation while the seed still looks honoured."""
        assert ORDER_DATE_WINDOW.first == date(2025, 6, 16)
        assert ORDER_DATE_WINDOW.last == date(2026, 2, 16)
        assert date(2026, 4, 1) == AS_OF_DATE

    def test_the_window_closes_before_the_snapshot(self) -> None:
        assert ORDER_DATE_WINDOW.first <= ORDER_DATE_WINDOW.last < AS_OF_DATE

    def test_the_declared_offsets_hold(self) -> None:
        """289 days before the snapshot, and 44 — `data-model.md` states both."""
        assert (AS_OF_DATE - ORDER_DATE_WINDOW.first).days == 289
        assert (AS_OF_DATE - ORDER_DATE_WINDOW.last).days == 44


class TestEventDates:
    """Invariant: event 1 equals `order_date`, nothing exceeds the as-of date."""

    def test_first_event_equals_the_order_date(self) -> None:
        order = date(2025, 9, 1)
        assert event_dates(order, LEGS, AS_OF_DATE)[0] == order

    def test_dates_are_strictly_increasing(self) -> None:
        """The 1-day floor exists to make this true; assert it rather than assume."""
        emitted = event_dates(date(2025, 9, 1), LEGS, AS_OF_DATE)
        assert all(b > a for a, b in zip(emitted, emitted[1:], strict=False))

    def test_no_instant_exceeds_the_as_of_date(self) -> None:
        emitted = event_dates(ORDER_DATE_WINDOW.last, [400] * 5, AS_OF_DATE)
        assert all(d <= AS_OF_DATE for d in emitted)

    @pytest.mark.parametrize(
        "as_of",
        [
            date(2025, 1, 1),  # before the window opens
            date(2025, 6, 16),  # equal to `first`
            date(2026, 2, 16),  # equal to `last`
            date(2030, 1, 1),  # far after `last`
        ],
    )
    def test_as_of_dates_outside_the_window_are_handled(self, as_of: date) -> None:
        """The domain the plan names by name. An as-of before the order date
        truncates the chain to nothing rather than emitting a negative span."""
        emitted = event_dates(date(2025, 9, 1), LEGS, as_of)
        assert all(d <= as_of for d in emitted)
        assert emitted == sorted(emitted)
        assert len(set(emitted)) == len(emitted)

    def test_an_as_of_before_the_order_date_emits_nothing(self) -> None:
        assert event_dates(date(2025, 9, 1), LEGS, date(2025, 8, 31)) == []

    def test_an_as_of_equal_to_the_order_date_emits_only_the_first_event(self) -> None:
        order = date(2025, 9, 1)
        assert event_dates(order, LEGS, order) == [order]

    def test_the_full_chain_survives_a_far_future_as_of(self) -> None:
        emitted = event_dates(date(2025, 9, 1), LEGS, date(2030, 1, 1))
        assert len(emitted) == len(LEGS) + 1


class TestMonotonicity:
    """Metamorphic: a later as-of date never removes a line from the delivered set."""

    @pytest.mark.parametrize("order", [date(2025, 6, 16), date(2025, 11, 3), date(2026, 2, 16)])
    def test_delivery_is_monotone_in_the_as_of_date(self, order: date) -> None:
        seen_delivered = False
        for offset in range(0, 400, 7):
            as_of = ORDER_DATE_WINDOW.first + timedelta(days=offset)
            delivered = is_delivered_by(order, LEGS, as_of)
            if seen_delivered:
                assert delivered, f"line un-delivered at {as_of}"
            seen_delivered = seen_delivered or delivered

    def test_the_event_count_never_decreases_as_the_as_of_advances(self) -> None:
        counts = [
            len(event_dates(date(2025, 9, 1), LEGS, ORDER_DATE_WINDOW.first + timedelta(days=d)))
            for d in range(0, 400, 11)
        ]
        assert counts == sorted(counts)


class TestShapeFloors:
    """DV-010's three bounds, asserted jointly as one admissible window."""

    def test_the_window_is_the_greater_of_the_two_floors(self) -> None:
        """`[max(0.80, 160/N), 0.90]` — the N=200 crossover of FR-010's floors."""
        assert delivered_share_window(199) == pytest.approx((160 / 199, 0.90))
        assert delivered_share_window(200) == pytest.approx((0.80, 0.90))
        assert delivered_share_window(250) == pytest.approx((0.80, 0.90))

    def test_both_binding_regimes_are_exercised(self) -> None:
        """Below N=200 the absolute floor of 160 binds; at or above it, the 80%
        share does. DV-010 requires both regimes be reached, not just one."""
        assert delivered_share_window(190)[0] > 0.80
        assert delivered_share_window(210)[0] == pytest.approx(0.80)

    def test_the_intended_shape_passes(self) -> None:
        """≈85% delivered at 199 lines, with ~10 events of margin over 160."""
        check_shape_floors(
            line_count=199,
            delivered=170,
            non_terminal_occupancy={
                s: 1
                for s in (
                    "submitted",
                    "under_review",
                    "approved",
                    "revise_and_resubmit",
                    "released_for_fabrication",
                    "shipped",
                )
            },
        )

    def test_too_few_delivered_fails(self) -> None:
        with pytest.raises(ShapeFloorError, match="delivered"):
            check_shape_floors(199, 150, {s: 1 for s in ("submitted", "under_review")})

    def test_too_few_censored_fails(self) -> None:
        """≥10% censored. 199 delivered of 199 breaches the ceiling, not the floor."""
        with pytest.raises(ShapeFloorError, match="censored"):
            check_shape_floors(199, 199, {s: 1 for s in ("submitted", "under_review")})

    def test_an_empty_non_terminal_state_fails(self) -> None:
        """FR-010 makes this a hard failure, not a warning. `approved` and
        `revise_and_resubmit` are the thin ones at the declared leg shares."""
        occupancy = {
            "submitted": 4,
            "under_review": 6,
            "approved": 0,
            "revise_and_resubmit": 2,
            "released_for_fabrication": 8,
            "shipped": 9,
        }
        with pytest.raises(ShapeFloorError, match="approved"):
            check_shape_floors(199, 170, occupancy)

    def test_the_three_bounds_are_asserted_jointly(self) -> None:
        """A breach of any one fails even when the other two hold."""
        healthy = {
            s: 3
            for s in (
                "submitted",
                "under_review",
                "approved",
                "revise_and_resubmit",
                "released_for_fabrication",
                "shipped",
            )
        }
        check_shape_floors(199, 170, healthy)
        for delivered in (150, 199):
            with pytest.raises(ShapeFloorError):
                check_shape_floors(199, delivered, healthy)
