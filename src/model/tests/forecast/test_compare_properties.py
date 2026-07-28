"""T094 (RED) — the reproduction oracle, at the mandatory property tier.

`plan.md` § Mandated properties gives `compare.py` two relations for this pair.
The first is **alternate implementation**: the nearest-rank percentile of an
array of length `n` is the element at `ceil(p·n)`, **1-indexed**, and equals the
value a direct sort-and-index computes — over the domain `p = 0.5` and `0.8` at
`n = 4,000`, an odd `n`, every draw equal, and `p·n` exactly integral, which is
the off-by-one boundary. The second is an **invariant**: the comparison passes
exactly when every per-line delta is within the tolerance, and a single line
outside it fails the whole claim.

Why this module is property-tested at all (`plan.md` § What qualifies): an
off-by-one here yields a plausible day value that no constraint rejects, and it
is the oracle the epic's entire reproducibility claim rests on. The module under
test does not exist yet; this is T094's red half.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from model.forecast.compare import (
    MEDIAN_PROBABILITY,
    P80_PROBABILITY,
    PERCENTILE_CONVENTION,
    CompareError,
    nearest_rank_percentile,
    within_tolerance,
)

#: The delivered convention label, re-typed from `0002`'s
#: `ck_schema_constants__percentile_convention`. The module under test must name
#: the same string: a percentile lookup that disagrees with the label the
#: database publishes is a second convention, and FR-022 makes the *delivered*
#: one the basis of the reproduction claim.
DELIVERED_CONVENTION = "nearest_rank_one_based_no_interpolation"

#: The committed shape's draw count. AD-004 derives the 5.0-day tolerance at
#: this `n`, and it is where both named probabilities land on an exact integer.
COMMITTED_DRAW_COUNT = 4000

#: An odd length, so `ceil(p·n)` is never integral at `p = 0.5` and the boundary
#: below exercises the rounding rather than the exact case.
ODD_DRAW_COUNT = 4001

#: AD-004's published tolerance, restated rather than imported: this file is the
#: oracle's test, and importing the number the implementation uses would let a
#: changed constant pass unnoticed on both sides.
TOLERANCE_DAYS = 5.0

#: The comparison population AD-004 sizes the tolerance over — ≈68 lines at two
#: quantities each. Used where a property needs "one outlier among many
#: conforming values" to be the realistic shape rather than a pair.
COMPARISON_COUNT = 136


# ---------------------------------------------------------------------------
# The reference implementation
# ---------------------------------------------------------------------------


def sort_and_index(draws: list[float] | np.ndarray, probability: float) -> float:
    """`ordered[ceil(p·n) − 1]` written out, with nothing shared with the module.

    The alternate implementation the relation is stated against: a plain Python
    sort, a plain `math.ceil`, and a 1-indexed rank converted to a 0-based
    subscript by subtracting one. Any agreement between this and the module is
    agreement about the convention rather than about a shared expression.
    """
    ordered = sorted(float(value) for value in np.asarray(draws, dtype=float).tolist())
    rank = math.ceil(probability * len(ordered))
    return ordered[max(rank, 1) - 1]


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

#: Durations on the dataset's own scale — sub-day to several years — never
#: subnormal and never so large that a difference of two of them loses days.
days = st.floats(min_value=0.001, max_value=5000.0, allow_nan=False, allow_infinity=False)

#: The open interval `(0, 1]`. Zero is excluded because `ceil(0·n)` is a rank of
#: zero, which 1-indexing has no element for; one is included because `p = 1` is
#: the maximum and is a legal nearest-rank request.
probabilities = st.floats(
    min_value=0.001, max_value=1.0, allow_nan=False, allow_infinity=False
)

deltas = st.floats(min_value=-500.0, max_value=500.0, allow_nan=False, allow_infinity=False)


@st.composite
def draw_sets(draw: st.DrawFn) -> list[float]:
    """A non-empty draw vector, unsorted, with repeats permitted.

    Unsorted on purpose: the stores hold ascending arrays, but a lookup that
    depended on being handed one already sorted would be a convention enforced
    by the caller rather than by the function, and a re-ordered read would move
    a published percentile.
    """
    return draw(st.lists(days, min_size=1, max_size=200))


# ---------------------------------------------------------------------------
# Alternate implementation: the rank is `ceil(p·n)`, 1-indexed
# ---------------------------------------------------------------------------


@given(draws=draw_sets(), probability=probabilities)
def test_the_percentile_equals_what_a_direct_sort_and_index_computes(
    draws: list[float], probability: float
) -> None:
    """The relation itself, over the whole domain rather than at its boundaries."""
    assert nearest_rank_percentile(draws, probability) == sort_and_index(draws, probability)


@given(draws=draw_sets(), probability=probabilities)
def test_the_percentile_is_always_one_of_the_draws_and_never_between_two(
    draws: list[float], probability: float
) -> None:
    """No interpolation, which is the half of the label a rank alone does not fix.

    `nearest_rank_one_based_no_interpolation` names two properties, and an
    implementation that averaged the two neighbouring order statistics would
    satisfy every monotonicity assertion in this file while publishing a figure
    the sampler never produced.
    """
    value = nearest_rank_percentile(draws, probability)

    assert value in set(draws)


@given(draws=draw_sets(), probability=probabilities, seed=st.integers(0, 2**32 - 1))
def test_the_lookup_does_not_depend_on_the_order_it_was_handed(
    draws: list[float], probability: float, seed: int
) -> None:
    """A shuffled read reproduces the same percentile.

    The stored arrays are ascending, so a function that indexed without sorting
    would agree with this file everywhere the caller behaved — and would move a
    published median the first time a row came back in another order.
    """
    shuffled = np.asarray(draws, dtype=float)
    np.random.default_rng(seed).shuffle(shuffled)

    assert nearest_rank_percentile(shuffled, probability) == nearest_rank_percentile(
        draws, probability
    )


@given(draws=draw_sets(), low=probabilities, high=probabilities)
def test_the_percentile_never_decreases_as_the_probability_rises(
    draws: list[float], low: float, high: float
) -> None:
    """Monotone in `p`, so the median can never exceed the 80th percentile."""
    assume(low <= high)

    assert nearest_rank_percentile(draws, low) <= nearest_rank_percentile(draws, high)


@given(draws=draw_sets())
def test_the_extremes_are_the_smallest_and_the_largest_draw(draws: list[float]) -> None:
    """`p = 1` is the maximum, and the smallest legal rank is the minimum."""
    assert nearest_rank_percentile(draws, 1.0) == max(draws)
    assert nearest_rank_percentile(draws, 1.0 / len(draws)) == min(draws)


# ---------------------------------------------------------------------------
# The named boundaries
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("probability", [MEDIAN_PROBABILITY, P80_PROBABILITY])
def test_the_two_reported_quantiles_at_the_committed_draw_count(probability: float) -> None:
    """`p = 0.5` and `0.8` at `n = 4,000` — FR-022's two quantities, at the shape.

    Both products are exactly integral at this `n`, so both land on the boundary
    the next test isolates. Asserted against an independently computed 0-based
    subscript rather than against the reference above, so the two checks do not
    rest on one expression.
    """
    ordered = np.arange(1.0, COMMITTED_DRAW_COUNT + 1.0)
    rank = math.ceil(probability * COMMITTED_DRAW_COUNT)

    assert nearest_rank_percentile(ordered, probability) == float(rank)
    assert nearest_rank_percentile(ordered, probability) == ordered[rank - 1]


@pytest.mark.parametrize("probability", [MEDIAN_PROBABILITY, P80_PROBABILITY])
def test_an_exactly_integral_product_takes_the_rank_itself_and_not_the_next_one(
    probability: float,
) -> None:
    """**The off-by-one boundary.** `p·n` integral is where the two forms differ.

    `draws[ceil(p·n)]` read as a 0-based subscript is one element too far, and
    at `p·n` integral it is *exactly* one element too far — everywhere else the
    ceiling absorbs the error and the two forms agree. A day value one order
    statistic along is plausible, passes every stored constraint, and moves a
    published percentile, which is why this case is named in the domain.
    """
    ordered = np.arange(1.0, COMMITTED_DRAW_COUNT + 1.0)
    rank = probability * COMMITTED_DRAW_COUNT
    value = nearest_rank_percentile(ordered, probability)

    assert rank == int(rank), "this boundary needs an exactly integral product"
    assert value == ordered[int(rank) - 1]
    assert value != ordered[int(rank)], (
        "the lookup returned the element *after* the nearest rank; `ceil(p·n)` is a "
        "1-indexed rank and reading it as a 0-based subscript is the off-by-one this "
        "boundary exists to catch"
    )


@pytest.mark.parametrize("probability", [MEDIAN_PROBABILITY, P80_PROBABILITY])
def test_an_odd_draw_count_rounds_the_rank_up(probability: float) -> None:
    """`n` odd — the domain's third case, where the product is not integral.

    At `p = 0.5` and an odd `n` the nearest rank is the upper of the two middle
    order statistics, never their average: the convention names no interpolation
    and the stored draws are the values the sampler produced.
    """
    ordered = np.arange(1.0, ODD_DRAW_COUNT + 1.0)
    exact = probability * ODD_DRAW_COUNT
    value = nearest_rank_percentile(ordered, probability)

    assert exact != int(exact), "this boundary needs a non-integral product"
    assert value == ordered[math.ceil(exact) - 1]
    assert value == float(math.ceil(exact))


@given(
    value=days,
    count=st.integers(min_value=1, max_value=500),
    probability=probabilities,
)
def test_every_draw_equal_gives_that_value_at_every_probability(
    value: float, count: int, probability: float
) -> None:
    """The domain's fourth case: a degenerate line still reports its own value.

    A line whose draws all coincide has one percentile for every `p`, and an
    implementation that computed a rank outside the array would raise here
    rather than return it — which is what makes this a boundary rather than a
    triviality.
    """
    draws = [value] * count

    assert nearest_rank_percentile(draws, probability) == value


# ---------------------------------------------------------------------------
# What the lookup refuses
# ---------------------------------------------------------------------------


def test_an_empty_draw_set_is_refused() -> None:
    """There is no order statistic to return, and no rank that names one."""
    with pytest.raises(CompareError):
        nearest_rank_percentile([], MEDIAN_PROBABILITY)


@pytest.mark.parametrize("probability", [0.0, -0.1, 1.5, float("nan")])
def test_a_probability_outside_the_half_open_unit_interval_is_refused(
    probability: float,
) -> None:
    """`p = 0` has a rank of zero, which 1-indexing has no element for."""
    with pytest.raises(CompareError):
        nearest_rank_percentile([1.0, 2.0, 3.0], probability)


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
def test_a_non_finite_draw_is_refused(bad: float) -> None:
    """A NaN sorts unpredictably and would silently decide which rank is returned."""
    with pytest.raises(CompareError):
        nearest_rank_percentile([1.0, bad, 3.0], MEDIAN_PROBABILITY)


def test_a_frame_of_several_lines_is_refused() -> None:
    """One vector per line: a two-dimensional array would pool them all."""
    with pytest.raises(CompareError):
        nearest_rank_percentile(np.ones((3, 4)), MEDIAN_PROBABILITY)


# ---------------------------------------------------------------------------
# Invariant: the comparison passes exactly when every delta is within tolerance
# ---------------------------------------------------------------------------


@given(values=st.lists(deltas, min_size=1, max_size=200))
def test_the_claim_holds_exactly_when_every_single_delta_holds(
    values: list[float],
) -> None:
    """The invariant, in both directions and over the whole domain.

    Stated as an equality against the elementwise conjunction rather than as an
    implication, so a comparison that quietly passed on an aggregate — the mean
    delta, or the median of them — fails here: an aggregate can sit inside the
    tolerance while individual lines move in compensating directions, which is
    the reading FR-022 rejects per-line comparison in order to exclude.
    """
    together = within_tolerance(values, TOLERANCE_DAYS)
    apart = all(within_tolerance(value, TOLERANCE_DAYS) for value in values)

    assert together is apart


@given(
    conforming=st.lists(
        st.floats(min_value=-TOLERANCE_DAYS, max_value=TOLERANCE_DAYS),
        min_size=COMPARISON_COUNT - 1,
        max_size=COMPARISON_COUNT - 1,
    ),
    outlier=st.floats(min_value=TOLERANCE_DAYS + 0.01, max_value=500.0),
    position=st.integers(min_value=0, max_value=COMPARISON_COUNT - 1),
)
def test_one_line_outside_the_tolerance_fails_the_whole_claim(
    conforming: list[float], outlier: float, position: int
) -> None:
    """The domain's second case: one outlier among 135 conforming comparisons.

    Placed at an arbitrary position, because a comparison that stopped at the
    first breach and one that stopped at the last would both pass a test that
    only ever put the outlier at the end.
    """
    values = list(conforming)
    values.insert(position, outlier)

    assert within_tolerance(conforming, TOLERANCE_DAYS)
    assert not within_tolerance(values, TOLERANCE_DAYS)
    assert not within_tolerance(-outlier, TOLERANCE_DAYS), (
        "a delta of the same magnitude in the other direction was accepted; the "
        "tolerance is absolute, so a re-run that came out five days *short* on a line "
        "breaches exactly as one that came out five days long"
    )


@given(tolerance=st.floats(min_value=0.0, max_value=100.0))
def test_a_delta_exactly_at_the_tolerance_is_within_it(tolerance: float) -> None:
    """The domain's first case, on both signs.

    Inclusive, because FR-022 publishes agreement *within* an absolute day
    tolerance and a strict comparison would make the published number one the
    claim never actually reaches.
    """
    assert within_tolerance(tolerance, tolerance)
    assert within_tolerance(-tolerance, tolerance)
    assert not within_tolerance(math.nextafter(tolerance, math.inf), tolerance)


@given(values=st.lists(deltas, min_size=1, max_size=50))
def test_the_claim_is_a_bool_and_never_an_array(values: list[float]) -> None:
    """The verdict is one answer about the whole population.

    NumPy's elementwise comparison returns an array, and an array is truthy in
    ways that raise on more than one element — a caller writing `if
    within_tolerance(...)` must get a verdict rather than an exception or, worse,
    a first element.
    """
    assert isinstance(within_tolerance(values, TOLERANCE_DAYS), bool)


@pytest.mark.parametrize("bad", [float("nan"), float("inf")])
def test_a_non_measurable_delta_is_never_within_the_tolerance(bad: float) -> None:
    """A comparison that did not produce a number did not produce a pass.

    `nan <= 5.0` is false and `inf <= 5.0` is false, so the arithmetic already
    answers correctly — asserted anyway, because the alternative implementation
    a reader would reach for (`abs(delta) > tolerance` negated) inverts a NaN
    into a pass.
    """
    assert not within_tolerance(bad, TOLERANCE_DAYS)
    assert not within_tolerance([0.0, bad], TOLERANCE_DAYS)


@pytest.mark.parametrize("tolerance", [-1.0, float("nan"), float("inf")])
def test_a_tolerance_that_is_not_a_published_bar_is_refused(tolerance: float) -> None:
    """A negative bar admits nothing and an infinite one admits everything."""
    with pytest.raises(CompareError):
        within_tolerance(0.0, tolerance)


def test_an_empty_comparison_is_refused() -> None:
    """A claim quantified over nothing is vacuously true, and would report a pass.

    FR-022's population is every stored line in both stores; a harness that
    paired none of them would otherwise publish "agrees" having compared
    nothing, which is the failure this refusal makes visible.
    """
    with pytest.raises(CompareError):
        within_tolerance([], TOLERANCE_DAYS)


# ---------------------------------------------------------------------------
# The convention the lookup implements
# ---------------------------------------------------------------------------


def test_the_module_names_the_delivered_percentile_convention() -> None:
    """FR-022's "under the delivered percentile convention", as a value.

    `schema_constants.percentile_convention` is checked to exactly this string
    by `ck_schema_constants__percentile_convention`, so a module implementing a
    lookup under another name would be publishing a second convention beside the
    one the serving tier reads.
    """
    assert PERCENTILE_CONVENTION == DELIVERED_CONVENTION
    assert (MEDIAN_PROBABILITY, P80_PROBABILITY) == (0.5, 0.8)
