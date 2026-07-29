"""Properties of the displayed probability.

FR-039's second half. Written before `api.compute.probability` existed and
watched failing.

Three rules interact here and each exists to close a specific way a figure lies:

- **Whole percent** (FR-008) — several thousand draws support one percent and no
  more; a finer figure asserts resolution the artifact does not have.
- **Bounded forms** — `<1%` and `>99%` rather than `0%` and `100%`, *including*
  for a stored probability of exactly zero or one. Several thousand draws cannot
  evidence a certainty, and an exact zero in the array is itself an estimate at
  the resolution the draw count supports.
- **The complement is subtracted, not re-rounded** — so a mandated FR-006 pair
  always sums to one hundred. Rounding both directions independently produces
  pairs summing to 99 or 101, which reads as an arithmetic bug and undermines
  the pair the dual framing exists to provide.

The third rule holds *only* where both directions render as integers. At a
bounded form there is no integer to subtract from, so the pair does not sum to
one hundred and asserting that it does would be asserting something false.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from api.compute.probability import PercentFigure, complement, percent_figure
from api.risk_read.query import Conventions, OpenLine
from api.risk_read.rows import MEASURE_UPPER_BOUND, RowInputs, miss_probability
from api.risk_read.states import ResolvedLine, RowState

#: Stored probabilities: the domain `ck_line_posterior__survival_unit_interval`
#: admits. Both endpoints included, because both are reachable and both take the
#: bounded form.
stored_probability = st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False)

#: The run anchor every generated posterior is positioned against.
_AS_OF = date(2026, 6, 1)


@pytest.mark.parametrize(
    ("stored", "expected"),
    [
        (0.125, "13%"),
        (0.875, "88%"),
        (0.005, "1%"),
        (0.015, "2%"),
        (0.345, "35%"),
    ],
)
def test_rounding_is_half_up_at_the_percent_scale(stored: float, expected: str) -> None:
    """FR-008 states the rule and its worked example: a stored `0.125` renders
    `13%` and not the `12%` a half-to-even rule produces.

    Named explicitly because Python's own `round` is half-to-even, so the
    default behaviour is the wrong one and would pass unnoticed at every value
    that is not exactly on a half.
    """
    assert percent_figure(stored).display == expected


@given(stored=stored_probability)
def test_a_figure_is_whole_percent_or_a_bound_and_never_anything_else(stored: float) -> None:
    """FR-008. There is no third form — no decimals, no `0%`, no `100%`."""
    figure = percent_figure(stored)
    if figure.bounded:
        assert figure.display in {"<1%", ">99%"}
    else:
        assert figure.display.endswith("%")
        assert figure.percent is not None
        assert 1 <= figure.percent <= 99
        assert figure.display == f"{figure.percent}%"


@pytest.mark.parametrize("stored", [0.0, 1.0, 0.0004, 0.9996])
def test_the_extremes_take_the_bounded_form_including_exact_zero_and_one(stored: float) -> None:
    """FR-008. "There is no endpoint at which the bounded form is skipped."

    A stored exact zero is the case a reader expects to be exempt, and it is
    the one that most needs the rule: `0%` on a screen is a promise, and the
    posterior is not in a position to make one.
    """
    figure = percent_figure(stored)
    assert figure.bounded
    assert figure.percent is None
    assert figure.display in {"<1%", ">99%"}


@given(stored=stored_probability)
def test_a_pair_of_unbounded_point_figures_sums_to_one_hundred(stored: float) -> None:
    """FR-008, FR-006. The complement is one hundred minus the *displayed*
    integer, never a second independent rounding.

    Scoped to unbounded point figures on purpose. At a bounded form there is no
    integer to subtract from — `<1%` pairs with `>99%` — so the sum is not
    defined there and claiming it is would be asserting a falsehood about the
    very forms the bound exists to introduce.
    """
    figure = percent_figure(stored)
    other = complement(figure)

    if figure.bounded:
        assert other.bounded, (
            "a bounded value paired with a flat certainty reintroduces through the "
            "complement exactly what the bound removes"
        )
        return

    assert not other.bounded
    assert figure.percent is not None and other.percent is not None
    assert figure.percent + other.percent == 100


@given(stored=stored_probability)
def test_the_complement_of_the_complement_is_the_original(stored: float) -> None:
    """A dual framing a coordinator can read in either direction has to agree
    with itself; otherwise which of the two is 'the' figure starts to matter."""
    figure = percent_figure(stored)
    assert complement(complement(figure)) == figure


@given(
    first=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
    second=st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
)
def test_a_larger_stored_probability_never_displays_as_a_smaller_one(
    first: float, second: float
) -> None:
    """FR-013's comparison rule, as a property of the display itself.

    "`<1%` ranks below every integer and `>99%` above every integer" is what
    keeps FR-013 decidable at the bounded forms rather than undefined there, so
    the ordering over displayed forms is asserted here where it is defined.
    """
    if first > second:
        first, second = second, first
    assert _rank(percent_figure(first)) <= _rank(percent_figure(second))


def _rank(figure: PercentFigure) -> Decimal:
    """Order over displayed forms: `<1%` below every integer, `>99%` above."""
    if figure.bounded:
        return Decimal("0.5") if figure.display == "<1%" else Decimal("99.5")
    assert figure.percent is not None
    return Decimal(figure.percent)


def test_a_figure_states_its_own_reference_class() -> None:
    """FR-003, FR-005. A percentage with no stated reference class is read as a
    confidence rather than as a frequency, which is the failure the research
    named — so the class travels with the figure rather than being applied by
    whichever renderer happens to remember."""
    figure = percent_figure(0.35)
    assert figure.reference_class
    assert "100" in figure.reference_class or "hundred" in figure.reference_class


@pytest.mark.parametrize("stored", [-0.001, 1.001])
def test_a_probability_outside_the_unit_interval_is_refused(stored: float) -> None:
    """`ck_line_posterior__survival_unit_interval` makes this unreachable from
    storage, so reaching it means the caller computed it — and silently clamping
    would turn a computation defect into a plausible-looking figure."""
    with pytest.raises(ValueError, match="unit interval"):
        percent_figure(stored)


# --- FR-013's monotonicity, over the domain FR-039 names ---------------------
#
# T060, corrected by T067. Two things were wrong with the first attempt and are
# worth stating rather than quietly replacing.
#
# It asserted `percent_figure(survival[-1]) == percent_figure(survival[-1])` and
# called that the residual-tail property — a tautology that could not fail.
#
# And all three properties read through a helper defined in this file rather
# than through `api.risk_read.rows.miss_probability`, which is the function that
# actually performs the survival lookup. A complement or an off-by-one
# introduced in production would have left them green, which is precisely the
# gap the properties were written to close.


@st.composite
def posteriors(draw: st.DrawFn, *, horizon: int = 60) -> tuple[tuple[float, ...], float]:
    """A `(survival, residual_tail_mass)` pair the storage layer would accept.

    Generated together because E003 ties them: `ck_line_posterior__survival_monotone`
    (non-increasing), `ck_line_posterior__survival_unit_interval` ([0,1]),
    `ck_line_posterior__survival_length` (exactly `horizon_days`), and
    `ck_line_posterior__residual_matches_grid_tail` (the residual equals the last
    grid entry within tolerance). Generating the array alone and inventing a
    residual would model a database state that cannot exist.

    Sorted descending rather than filtered for monotonicity: a filter would
    spend its budget rejecting candidates and shrink to uninformative examples.

    `horizon` is 60 rather than the production 365 because every shape property
    holds identically at either width, and a 365-element array per example makes
    the run slow without making the property stronger. The frozen fixture
    carries the production width.
    """
    values = draw(
        st.lists(
            st.floats(min_value=0.0, max_value=1.0, allow_nan=False, allow_infinity=False),
            min_size=horizon,
            max_size=horizon,
        )
    )
    survival = tuple(sorted(values, reverse=True))
    return survival, survival[-1]


def _row_inputs(survival: tuple[float, ...], residual: float, *, need_by_offset: int) -> RowInputs:
    """A `RowInputs` positioned at `need_by_offset`, for the production reader.

    Built here so the properties below drive `miss_probability` rather than a
    reimplementation of it. Everything not under test is held at a value that
    resolves the row to `NOMINAL`, so the figure is read from the grid rather
    than suppressed by a state.
    """
    line = OpenLine(
        po_line_id=UUID(int=1),
        project_id="PRJ-001",
        vendor_id="VND-001",
        po_number="PO-1",
        line_number=1,
        description="line",
        quantity=1.0,
        unit_of_measure="EA",
        need_by_date=_AS_OF + timedelta(days=need_by_offset),
        criticality=3,
        lifecycle_state="submitted",
        roster_hash="sha256:" + "a" * 64,
        draws=(1.0, 2.0),
        survival=survival,
        residual_tail_mass=residual,
    )
    return RowInputs(
        resolved=ResolvedLine(line=line, state=RowState.NOMINAL),
        as_of_date=_AS_OF,
        horizon_days=len(survival),
        conventions=Conventions(
            draw_count=4000,
            percentile_convention="nearest_rank_one_based_no_interpolation",
            anchor_date_convention="run_as_of_date",
        ),
        today=_AS_OF,
        run_draw_count=4000,
    )


def _displayed(survival: tuple[float, ...], residual: float, offset: int) -> PercentFigure:
    """The miss figure production code emits for a need-by at `offset`."""
    figure = miss_probability(_row_inputs(survival, residual, need_by_offset=offset))
    assert figure is not None, "a nominal row inside the grid must carry a figure"
    percent = figure["miss"]["percent"]
    return PercentFigure(
        display=figure["miss"]["display"], bounded=percent is None, percent=percent
    )


@given(posterior=posteriors(), data=st.data())
def test_pulling_a_need_by_date_in_never_lowers_the_displayed_probability(
    posterior: tuple[tuple[float, ...], float], data: st.DataObject
) -> None:
    """FR-013, over the interval it names, through the production reader.

    "While a need-by date remains within the interval from the active run's
    as-of date to the end of its horizon, moving it earlier MUST NOT decrease
    the displayed probability of missing it."

    The interval is `as_of < d <= as_of + horizon_days` — exactly the offsets
    `1..horizon_days` the grid stores. Open at the lower end because
    `d == as_of` resolves to already-late under FR-030 and displays no
    probability, so the property would have no subject at its own endpoint.

    Compared over **displayed forms**, because that is what the requirement says
    and because the two can disagree: two stored probabilities a hair apart can
    round to the same integer, and `<1%` is not an integer at all.
    """
    survival, residual = posterior
    later = data.draw(st.integers(min_value=1, max_value=len(survival)))
    earlier = data.draw(st.integers(min_value=1, max_value=later))

    assert _rank(_displayed(survival, residual, earlier)) >= _rank(
        _displayed(survival, residual, later)
    ), (
        f"moving the need-by date from offset {later} to {earlier} lowered the displayed "
        "probability of missing it — which inverts the direction a coordinator relies on"
    )


@settings(max_examples=25)
@given(posterior=posteriors(horizon=20))
def test_every_offset_in_the_grid_displays_an_admissible_form(
    posterior: tuple[tuple[float, ...], float],
) -> None:
    """FR-008 over the whole grid rather than at sampled points.

    A rounding rule correct at most offsets and wrong at one is a rule that is
    wrong on the day a line's need-by date lands there.
    """
    survival, residual = posterior
    for offset in range(1, len(survival) + 1):
        figure = _displayed(survival, residual, offset)
        if figure.bounded:
            assert figure.percent is None
            assert figure.display in {"<1%", ">99%"}
        else:
            assert figure.percent is not None
            assert 1 <= figure.percent <= 99
            assert complement(figure).percent == 100 - figure.percent


@given(posterior=posteriors())
def test_the_horizon_day_figure_and_the_residual_bound_agree(
    posterior: tuple[tuple[float, ...], float],
) -> None:
    """`ck_line_posterior__residual_matches_grid_tail` ties the residual to the
    last grid entry, which is why FR-013's point figure and FR-017's bound meet
    at the horizon instead of jumping.

    Not a tautology — that is what the first version of this test was. The left
    side is the **point** figure production code reads from the grid at the last
    in-grid day; the right side is the **bound** it derives from
    `residual_tail_mass` for a line one day further out. Two different branches
    of `miss_probability` — `MEASURE_POINT` and `MEASURE_UPPER_BOUND` — reaching
    the same displayed value.
    """
    survival, residual = posterior
    horizon = len(survival)

    point = _displayed(survival, residual, horizon)

    beyond = _row_inputs(survival, residual, need_by_offset=horizon + 1)
    beyond = replace(
        beyond,
        resolved=ResolvedLine(line=beyond.resolved.line, state=RowState.BEYOND_HORIZON),
    )
    bound = miss_probability(beyond)

    assert bound is not None
    assert bound["measure"] == MEASURE_UPPER_BOUND
    assert bound["miss"]["display"] == point.display
