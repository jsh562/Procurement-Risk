"""Properties of the harm score and the ordering.

FR-039 makes test-first development an obligation rather than a plan hint for
this feature's deterministic computation, and names the two properties: FR-013a
(monotonic in criticality) and FR-010 (a total tiebreak). These were written
before `api.compute.ranking` existed and were watched failing.

The generated inputs come from the domain the storage layer can actually
produce — draws ascending and non-negative, criticality an integer from 1 to 5 —
because a property reported as falsified by an input no stored artifact could
hold is a false alarm that trains its reader to ignore the next one.

`draw_count` here is small. That is not a weakened domain: `forecast_run`
constrains it only to be positive, and the shape properties the ranking depends
on — sorted, non-negative, length equal to `draw_count` — hold identically at
50 draws and at 4000. The frozen fixture carries the production width.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from api.compute.ranking import RankableLine, expected_harm, order_lines

#: E003's `criticality smallint` with `ck_pol__criticality_range`: 1 to 5, 5
#: most critical.
criticality = st.integers(min_value=1, max_value=5)


@st.composite
def draw_arrays(draw: st.DrawFn, *, size: int = 50) -> tuple[float, ...]:
    """Ascending, non-negative draws — `ck_line_posterior__draws_sorted` and
    `ck_line_posterior__draws_non_negative` made into a strategy."""
    values = draw(
        st.lists(
            st.floats(min_value=0.0, max_value=900.0, allow_nan=False, allow_infinity=False),
            min_size=size,
            max_size=size,
        )
    )
    return tuple(sorted(values))


@st.composite
def rankable_lines(draw: st.DrawFn) -> RankableLine:
    """One line the ranking can order."""
    return RankableLine(
        po_line_id=draw(st.uuids()),
        draws=draw(draw_arrays()),
        need_by_offset=draw(st.integers(min_value=-30, max_value=400)),
        criticality=draw(criticality),
    )


@given(line=rankable_lines(), raised=criticality)
def test_raising_criticality_never_moves_a_line_further_from_the_top(
    line: RankableLine, raised: int
) -> None:
    """FR-013a, against the harm-ordered sequence under the default key.

    Weak rather than strict, and deliberately so: across the zero-harm block —
    every line whose draws all land on or before its need-by date — harm is
    exactly zero at every criticality, so raising it changes no score. The order
    inside that block is then decided by FR-010's second key, criticality
    descending, which moves the line toward the top or leaves it, never away.
    """
    assume(raised >= line.criticality)
    others = [
        RankableLine(
            po_line_id=UUID(int=index + 1),
            draws=line.draws,
            need_by_offset=line.need_by_offset + index,
            criticality=3,
        )
        for index in range(4)
    ]

    before = order_lines([line, *others]).index(line.po_line_id)
    raised_line = RankableLine(
        po_line_id=line.po_line_id,
        draws=line.draws,
        need_by_offset=line.need_by_offset,
        criticality=raised,
    )
    after = order_lines([raised_line, *others]).index(line.po_line_id)

    assert after <= before, (
        f"raising criticality from {line.criticality} to {raised} moved the line from "
        f"position {before} to {after} — further from the top, which inverts the whole "
        "point of weighting by criticality"
    )


@given(lines=st.lists(rankable_lines(), min_size=2, max_size=8, unique_by=lambda x: x.po_line_id))
def test_the_ordering_is_total_and_reproducible(lines: list[RankableLine]) -> None:
    """FR-010. The identifier terminates the order, so no two lines are ever
    left in an undefined relative position.

    Reproducibility is checked by ordering a shuffled copy: an order that
    depended on input sequence would pass a single-call test and disagree with
    itself across two reloads of the same page.
    """
    forward = order_lines(lines)
    backward = order_lines(list(reversed(lines)))

    assert forward == backward, "the order depends on the input sequence, so it is not total"
    assert len(set(forward)) == len(lines), "every line appears exactly once"


@given(lines=st.lists(rankable_lines(), min_size=2, max_size=6, unique_by=lambda x: x.po_line_id))
def test_ties_are_broken_before_the_identifier_is_reached(lines: list[RankableLine]) -> None:
    """FR-010's sequence, checked in order rather than only at its end.

    A tiebreak that jumped straight to the identifier would still be total and
    would still be reproducible — and would order a critical line due tomorrow
    below a trivial one due next year. Totality is necessary and nowhere near
    sufficient.
    """
    ordered = order_lines(lines)
    by_id = {line.po_line_id: line for line in lines}

    for first, second in zip(ordered, ordered[1:], strict=False):
        left, right = by_id[first], by_id[second]
        left_harm = expected_harm(left)
        right_harm = expected_harm(right)
        if left_harm != right_harm:
            assert left_harm > right_harm, "harm must order descending — worst first"
            continue
        if left.need_by_offset != right.need_by_offset:
            assert left.need_by_offset < right.need_by_offset, "then need-by ascending"
            continue
        if left.criticality != right.criticality:
            assert left.criticality > right.criticality, "then criticality descending"
            continue
        assert str(left.po_line_id) < str(right.po_line_id), "then the identifier, ascending"


@given(line=rankable_lines())
def test_harm_is_never_negative(line: RankableLine) -> None:
    """FR-001. Overrun counts zero where a draw delivers on time, so a line that
    is comfortably early has a harm of zero and never a negative score that
    would sort it below lines with no risk at all."""
    assert expected_harm(line) >= 0.0


@given(draws=draw_arrays(), offset=st.integers(min_value=0, max_value=400))
def test_harm_scales_with_criticality(draws: tuple[float, ...], offset: int) -> None:
    """FR-001. Criticality multiplies; it does not offset or cap.

    Checked as a ratio rather than by recomputing the formula, so the test does
    not simply restate the implementation in a second place.
    """
    base = RankableLine(po_line_id=UUID(int=1), draws=draws, need_by_offset=offset, criticality=1)
    scaled = RankableLine(po_line_id=UUID(int=2), draws=draws, need_by_offset=offset, criticality=4)

    base_harm = expected_harm(base)
    assume(base_harm > 0.0)
    assert abs(expected_harm(scaled) - 4 * base_harm) < 1e-9


@settings(max_examples=50)
@given(draws=draw_arrays(), offset=st.integers(min_value=1, max_value=300))
def test_pulling_a_need_by_date_in_never_lowers_the_harm(
    draws: tuple[float, ...], offset: int
) -> None:
    """A date the coordinator needs sooner cannot be less harmful to miss.

    Not named as a requirement, but it is the arithmetic FR-013 assumes on the
    probability side, and a ranking that violated it would disagree with the
    miss probability on the same row — which is the kind of internal
    contradiction a coordinator would notice and could not resolve.
    """
    later = RankableLine(po_line_id=UUID(int=1), draws=draws, need_by_offset=offset, criticality=3)
    sooner = RankableLine(
        po_line_id=UUID(int=2), draws=draws, need_by_offset=offset - 1, criticality=3
    )
    assert expected_harm(sooner) >= expected_harm(later) - 1e-9


@given(lines=st.lists(rankable_lines(), min_size=2, max_size=6, unique_by=lambda x: x.po_line_id))
def test_every_offered_key_produces_a_total_order(lines: list[RankableLine]) -> None:
    """FR-010, FR-026. The tiebreak follows every key, not only the default.

    Under `need_by_date`, a whole day's lines tie on the key alone. Without the
    tiebreak their order would be whatever the query returned — the same defect
    FR-010 names for the zero-harm block, appearing somewhere else.
    """
    for key in ("expected_harm", "need_by_date", "criticality", "calendar_margin"):
        forward = order_lines(lines, sort_key=key)
        backward = order_lines(list(reversed(lines)), sort_key=key)
        assert forward == backward, f"{key} depends on the input sequence"
        assert sorted(forward) == sorted(line.po_line_id for line in lines)


@given(lines=st.lists(rankable_lines(), min_size=2, max_size=6, unique_by=lambda x: x.po_line_id))
def test_need_by_date_orders_soonest_first(lines: list[RankableLine]) -> None:
    """FR-026's fixed direction for this key. Ascending: the date due soonest
    leads, because a worklist ordered latest-first is a list of what to ignore."""
    by_id = {line.po_line_id: line for line in lines}
    offsets = [by_id[item].need_by_offset for item in order_lines(lines, sort_key="need_by_date")]
    assert offsets == sorted(offsets)


@given(lines=st.lists(rankable_lines(), min_size=2, max_size=6, unique_by=lambda x: x.po_line_id))
def test_criticality_orders_most_critical_first(lines: list[RankableLine]) -> None:
    """FR-026's fixed direction. Descending, 5 first — the direction is fixed
    per key rather than chosen by the caller, so "most critical first" cannot be
    inverted into "least critical first" by a query parameter."""
    by_id = {line.po_line_id: line for line in lines}
    values = [by_id[item].criticality for item in order_lines(lines, sort_key="criticality")]
    assert values == sorted(values, reverse=True)


def test_an_unoffered_key_is_refused_rather_than_defaulted() -> None:
    """FR-026. Falling back to the default would order the list by something
    other than what the screen says it is ordered by — and the screen is what a
    coordinator would believe."""
    with pytest.raises(ValueError, match="four keys"):
        order_lines([], sort_key="p50")
