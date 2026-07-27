"""DV-007 and DV-009 — the walk's shape, and the rework allocation as equality.

DV-009 is an **equality**, not a recording: FR-006 declares the allocation, so a
realized histogram merely "near" (42, 13, 5) is a defect, not a variation.
"""

from __future__ import annotations

from collections import Counter
from datetime import date, timedelta

import pytest

from model.procurement.allocate import (
    DECLARED_TOTAL,
    REWORK_LINE_SHARE,
    REWORK_MAX_LOOPS,
    rework_loop_allocation,
)
from model.procurement.lifecycle import (
    INITIAL_STATE,
    LEGAL_TRANSITIONS,
    NON_TERMINAL_STATES,
    STATES,
    TERMINAL_STATE,
    LifecycleError,
    WalkedEvent,
    state_sequence,
    validate_walk,
    walk,
)

ORDER = date(2025, 9, 1)


def _dates(count: int) -> list[date]:
    return [ORDER + timedelta(days=7 * i) for i in range(count)]


class TestStateMachine:
    def test_seven_states_and_seven_legal_edges(self) -> None:
        assert len(STATES) == 7
        assert len(LEGAL_TRANSITIONS) == 7

    def test_the_edges_match_the_delivered_function(self) -> None:
        """The same seven pairs `fn_is_legal_lifecycle_transition` enforces. A
        generator that disagreed would emit an artifact the loader rejects."""
        assert (
            frozenset(
                {
                    ("submitted", "under_review"),
                    ("under_review", "approved"),
                    ("under_review", "revise_and_resubmit"),
                    ("revise_and_resubmit", "submitted"),
                    ("approved", "released_for_fabrication"),
                    ("released_for_fabrication", "shipped"),
                    ("shipped", "delivered"),
                }
            )
            == LEGAL_TRANSITIONS
        )

    def test_only_delivered_is_terminal(self) -> None:
        assert TERMINAL_STATE == "delivered"
        assert set(NON_TERMINAL_STATES) == set(STATES) - {TERMINAL_STATE}
        assert not any(edge[0] == TERMINAL_STATE for edge in LEGAL_TRANSITIONS)

    def test_every_non_terminal_state_has_an_outgoing_edge(self) -> None:
        sources = {edge[0] for edge in LEGAL_TRANSITIONS}
        assert sources == set(NON_TERMINAL_STATES)


class TestStateSequence:
    @pytest.mark.parametrize("loops", [0, 1, 2, 3])
    def test_the_event_count_is_six_plus_three_l(self, loops: int) -> None:
        assert len(state_sequence(loops)) == 6 + 3 * loops

    @pytest.mark.parametrize("loops", [0, 1, 2, 3])
    def test_every_adjacent_pair_is_legal(self, loops: int) -> None:
        states = state_sequence(loops)
        for pair in zip(states, states[1:], strict=False):
            assert pair in LEGAL_TRANSITIONS

    @pytest.mark.parametrize("loops", [0, 1, 2, 3])
    def test_it_opens_at_submitted_and_ends_delivered(self, loops: int) -> None:
        states = state_sequence(loops)
        assert states[0] == INITIAL_STATE
        assert states[-1] == TERMINAL_STATE

    def test_each_loop_repeats_three_states(self) -> None:
        assert len(state_sequence(1)) - len(state_sequence(0)) == 3

    def test_negative_loops_are_refused(self) -> None:
        with pytest.raises(LifecycleError, match="negative"):
            state_sequence(-1)


class TestWalk:
    @pytest.mark.parametrize("loops", [0, 1, 2, 3])
    def test_a_full_walk_validates(self, loops: int) -> None:
        events = walk(ORDER, _dates(6 + 3 * loops), loops)
        validate_walk(events)
        assert events[-1].to_state == TERMINAL_STATE

    @pytest.mark.parametrize("survived", [1, 2, 4, 5])
    def test_a_truncated_walk_is_a_legal_prefix(self, survived: int) -> None:
        """Censoring truncates the path; it never re-routes it. A censored line
        is partway along the legal walk, not on a different one."""
        events = walk(ORDER, _dates(survived), 1)
        validate_walk(events)
        assert len(events) == survived
        assert events[-1].to_state != TERMINAL_STATE

    def test_event_one_must_carry_the_order_date(self) -> None:
        with pytest.raises(LifecycleError, match="clock start"):
            walk(ORDER, [ORDER + timedelta(days=1)], 0)

    def test_more_dates_than_states_is_refused(self) -> None:
        with pytest.raises(LifecycleError, match="only"):
            walk(ORDER, _dates(20), 0)

    def test_an_empty_walk_is_refused(self) -> None:
        with pytest.raises(LifecycleError, match="at least one event"):
            validate_walk([])


class TestValidateWalkCatchesEachDV007Clause:
    def test_a_gap_in_the_sequence_fails(self) -> None:
        events = list(walk(ORDER, _dates(4), 0))
        broken = events[:2] + [WalkedEvent(4, events[2].to_state, events[2].occurred_at)]
        with pytest.raises(LifecycleError, match="contiguous"):
            validate_walk(broken)

    def test_a_chain_not_opening_at_submitted_fails(self) -> None:
        with pytest.raises(LifecycleError, match="opens at"):
            validate_walk([WalkedEvent(1, "under_review", ORDER)])

    def test_an_illegal_pair_fails(self) -> None:
        with pytest.raises(LifecycleError, match="not one of"):
            validate_walk(
                [
                    WalkedEvent(1, INITIAL_STATE, ORDER),
                    WalkedEvent(2, "shipped", ORDER + timedelta(days=1)),
                ]
            )

    def test_a_non_increasing_date_fails(self) -> None:
        """The 1-day floor exists to make this impossible; assert it anyway,
        because the delivered chain constraint rejects it only at load."""
        with pytest.raises(LifecycleError, match="strictly increase"):
            validate_walk(
                [
                    WalkedEvent(1, INITIAL_STATE, ORDER),
                    WalkedEvent(2, "under_review", ORDER),
                ]
            )


class TestDV009:
    """Equality against the declared allocation, not a recorded approximation."""

    def test_the_realized_histogram_equals_the_declared_one(self) -> None:
        counts = Counter(rework_loop_allocation(199))
        assert [counts[1], counts[2], counts[3]] == [42, 13, 5]

    def test_the_looped_line_count_equals_the_declared_formula(self) -> None:
        for n in (190, 199, 200, 210):
            allocation = rework_loop_allocation(n)
            assert sum(1 for x in allocation if x) == round(REWORK_LINE_SHARE * n)

    def test_no_line_exceeds_three_loops(self) -> None:
        assert max(rework_loop_allocation(DECLARED_TOTAL)) <= REWORK_MAX_LOOPS == 3

    def test_the_three_loop_stratum_is_never_empty(self) -> None:
        """A rework depth that never occurs is a path the dataset claims to
        exercise and does not."""
        for n in (190, 199, 210):
            assert Counter(rework_loop_allocation(n))[3] >= 5

    def test_the_allocation_is_the_declared_length(self) -> None:
        for n in (190, 199, 210):
            assert len(rework_loop_allocation(n)) == n

    def test_the_allocation_is_deterministic(self) -> None:
        assert rework_loop_allocation(199) == rework_loop_allocation(199)


class TestEventTotals:
    def test_the_uncensored_event_total_follows_from_the_allocation(self) -> None:
        """`6 + 3L` per line, summed over the declared allocation."""
        allocation = rework_loop_allocation(DECLARED_TOTAL)
        expected = sum(6 + 3 * loops for loops in allocation)
        actual = sum(len(state_sequence(loops)) for loops in allocation)
        assert actual == expected
        assert expected == 199 * 6 + 3 * sum(allocation)
