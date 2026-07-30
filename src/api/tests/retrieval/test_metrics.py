"""Recall, MRR, their intervals, and the unresolvable verdict.

Spec FR-030, FR-031, FR-032, FR-042. These are the pure scoring functions the
quality policy names, and unlike fusion ranking they have no SQL obstacle — so
they carry the mandatory test-first cycle directly. Each pair here was written
and observed to fail before its implementation existed.

**Two statistics, two interval methods, and the distinction is the point.**
Recall@5 is a proportion — each query either has a relevant chunk in its top
five or does not — so a Wilson interval is admissible: it inverts a score test
on a binomial trial count. Mean reciprocal rank is a mean of continuous
per-query values with no trial count to invert, so Wilson has nothing to work
with and would publish bounds belonging to a different quantity. MRR takes a
percentile bootstrap over queries.

`specs/sad.md` fixes the bootstrap's parameters and this module reads them
rather than choosing: **B = 10,000 resamples**, and the bit generator pinned to
**PCG64** alongside the seed. A seed alone does not fix a bit stream across
library versions, so an interval could move on a dependency bump and read as
reproducibility drift.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from api.retrieval.metrics import (
    IntervalMethod,
    MetricsError,
    mean_reciprocal_rank,
    overlap_verdict,
    percentile_bootstrap,
    recall_at_k,
    wilson_interval,
)

#: Query-set sizes from one upward (FR-042). One is included deliberately: a
#: single-query set is where an interval degenerates, and it is the size a
#: careless implementation divides by zero on.
_SET_SIZES = st.integers(min_value=1, max_value=60)

#: Hit-or-miss outcomes, the population a proportion is computed over.
_OUTCOMES = st.lists(st.booleans(), min_size=1, max_size=60)

#: Per-query reciprocal ranks. Zero means "no relevant chunk was retrieved",
#: which is a real outcome and not a missing value.
_RECIPROCALS = st.lists(st.sampled_from([0.0, 1.0, 0.5, 1 / 3, 0.25, 0.2]), min_size=1, max_size=60)


# ---------------------------------------------------------------------------
# Recall and its Wilson interval (T041 -> T042)
# ---------------------------------------------------------------------------


@given(outcomes=_OUTCOMES)
def test_recall_is_the_proportion_of_hits(outcomes: list[bool]) -> None:
    assert recall_at_k(outcomes) == pytest.approx(sum(outcomes) / len(outcomes))


@given(outcomes=_OUTCOMES)
def test_recall_is_bounded(outcomes: list[bool]) -> None:
    assert 0.0 <= recall_at_k(outcomes) <= 1.0


def test_recall_refuses_an_empty_set() -> None:
    """A proportion over zero queries is undefined, not zero.

    FR-042 requires the empty set be refused rather than reported. Returning
    0.0 would publish a recall figure for a run that measured nothing, which
    reads as a bad result rather than as no result.
    """
    with pytest.raises(MetricsError, match="empty"):
        recall_at_k([])


@given(outcomes=_OUTCOMES)
def test_the_wilson_interval_contains_the_point_estimate(outcomes: list[bool]) -> None:
    """The estimate lies inside its own interval.

    True of Wilson by construction and false of the naive normal interval at
    the boundaries — which is why Wilson is the admissible one here.
    """
    point = recall_at_k(outcomes)
    lower, upper = wilson_interval(outcomes)
    assert lower <= point <= upper


@given(outcomes=_OUTCOMES)
def test_the_wilson_interval_stays_within_zero_and_one(outcomes: list[bool]) -> None:
    """A proportion's interval cannot leave [0, 1].

    The naive normal interval does exactly that near 0 and 1, publishing a
    lower bound below zero for a proportion. Wilson does not, and this is the
    property that makes the difference observable.
    """
    lower, upper = wilson_interval(outcomes)
    assert 0.0 <= lower <= upper <= 1.0


def test_an_all_hit_set_has_an_upper_bound_of_one() -> None:
    """All-hit and all-miss are the boundaries FR-042 names."""
    _, upper = wilson_interval([True] * 20)
    assert upper == pytest.approx(1.0)


def test_an_all_miss_set_has_a_lower_bound_of_zero() -> None:
    lower, _ = wilson_interval([False] * 20)
    assert lower == pytest.approx(0.0)


@given(size=_SET_SIZES)
def test_a_wider_set_gives_a_narrower_interval(size: int) -> None:
    """More queries, less uncertainty — at the same observed proportion.

    Stated because it is the property an interval exists to express, and an
    implementation that ignored the denominator would pass every bound check
    above.
    """
    small = wilson_interval([True, False] * 2)
    large = wilson_interval([True, False] * (size + 10))
    assert (large[1] - large[0]) <= (small[1] - small[0])


# ---------------------------------------------------------------------------
# MRR and its percentile bootstrap (T043 -> T044)
# ---------------------------------------------------------------------------


@given(reciprocals=_RECIPROCALS)
def test_mrr_is_the_mean_of_the_reciprocal_ranks(reciprocals: list[float]) -> None:
    assert mean_reciprocal_rank(reciprocals) == pytest.approx(sum(reciprocals) / len(reciprocals))


def test_mrr_refuses_an_empty_set() -> None:
    with pytest.raises(MetricsError, match="empty"):
        mean_reciprocal_rank([])


@settings(max_examples=15, deadline=None)
@given(reciprocals=_RECIPROCALS)
def test_the_bootstrap_interval_brackets_the_point_estimate(reciprocals: list[float]) -> None:
    point = mean_reciprocal_rank(reciprocals)
    lower, upper = percentile_bootstrap(reciprocals, seed=7)
    assert lower <= point <= upper


@settings(max_examples=10, deadline=None)
@given(reciprocals=_RECIPROCALS)
def test_the_bootstrap_is_reproducible_from_its_seed(reciprocals: list[float]) -> None:
    """Same seed, same bounds — exactly, not approximately.

    The reproducibility gate compares interval *bounds* for equality rather
    than within a tolerance, so anything less than exact here would surface
    there as drift with no cause.
    """
    assert percentile_bootstrap(reciprocals, seed=11) == percentile_bootstrap(reciprocals, seed=11)


def test_a_different_seed_generally_moves_the_bounds() -> None:
    """Otherwise the seed is decorative and the recorded value means nothing."""
    values = [0.0, 1.0, 0.5, 0.25, 0.2, 1 / 3] * 4
    assert percentile_bootstrap(values, seed=1) != percentile_bootstrap(values, seed=2)


def test_the_bootstrap_of_a_constant_set_is_that_constant() -> None:
    """Every resample of identical values is identical, so the interval collapses.

    The degenerate case, asserted because an implementation that added jitter
    or assumed variance would produce a spurious width here.
    """
    lower, upper = percentile_bootstrap([0.5] * 30, seed=3)
    assert lower == pytest.approx(0.5)
    assert upper == pytest.approx(0.5)


def test_a_single_query_set_is_handled_rather_than_dividing_by_zero() -> None:
    """FR-042's smallest domain. One query is a legal, if uninformative, run."""
    assert mean_reciprocal_rank([0.5]) == pytest.approx(0.5)
    lower, upper = percentile_bootstrap([0.5], seed=5)
    assert lower == pytest.approx(0.5)
    assert upper == pytest.approx(0.5)


def test_mrr_is_never_given_a_wilson_interval() -> None:
    """FR-031's prohibition, asserted on the emitted method rather than on intent.

    A prohibition with no observable is satisfied by every implementation that
    simply never calls the function, and no test can fail. The interval carries
    the method that produced it, so this fails when the prohibition is breached
    rather than when someone remembers to look.
    """
    _, _, record = percentile_bootstrap([0.5, 0.25], seed=1, with_method=True)
    assert record.method is IntervalMethod.PERCENTILE_BOOTSTRAP
    assert record.method is not IntervalMethod.WILSON
    # The emitted value, not the member identifier — the record is read back
    # from published output where only the string survives.
    assert str(record.method) == "percentile_bootstrap"


def test_the_bootstrap_records_its_resample_count_seed_and_bit_generator() -> None:
    """Three values, not two.

    `specs/sad.md` pins the bit generator alongside the seed because a seed
    alone does not fix a bit stream across library versions — an interval could
    move on a dependency bump and read as reproducibility drift.
    """
    _, _, record = percentile_bootstrap([0.5, 0.25], seed=1, with_method=True)
    assert record.resamples == 10_000
    assert record.seed == 1
    assert record.bit_generator == "PCG64"


# ---------------------------------------------------------------------------
# The unresolvable verdict (T045 -> T046)
# ---------------------------------------------------------------------------


@given(
    a=st.tuples(st.floats(0, 1), st.floats(0, 1)).map(sorted).map(tuple),
    b=st.tuples(st.floats(0, 1), st.floats(0, 1)).map(sorted).map(tuple),
)
def test_the_verdict_is_symmetric(a: tuple[float, float], b: tuple[float, float]) -> None:
    """Exchanging the two arms cannot change whether they are separable."""
    assert overlap_verdict(a, b) == overlap_verdict(b, a)


@given(a=st.tuples(st.floats(0, 1), st.floats(0, 1)).map(sorted).map(tuple))
def test_an_interval_compared_with_itself_is_unresolvable(a: tuple[float, float]) -> None:
    """Reflexivity. An arm cannot be shown better than itself."""
    assert overlap_verdict(a, a) is False


def test_touching_endpoints_are_unresolvable() -> None:
    """The closed-interval rule, at the boundary FR-032 names.

    `[0.1, 0.2]` and `[0.2, 0.3]` share exactly one point. Under a closed
    reading they overlap and the comparison is unresolvable; under an open one
    they separate. The closed reading is the conservative one and is what
    FR-032 fixes, because declaring a winner on a single shared point is the
    overclaim the verdict exists to prevent.
    """
    assert overlap_verdict((0.1, 0.2), (0.2, 0.3)) is False


def test_disjoint_intervals_are_resolvable() -> None:
    assert overlap_verdict((0.1, 0.2), (0.3, 0.4)) is True


def test_a_contained_interval_is_unresolvable() -> None:
    assert overlap_verdict((0.1, 0.9), (0.4, 0.5)) is False


def test_the_verdict_refuses_an_inverted_interval() -> None:
    """A lower bound above its upper is a caller error, not a narrow interval."""
    with pytest.raises(MetricsError, match="lower"):
        overlap_verdict((0.9, 0.1), (0.2, 0.3))


def test_intervals_are_finite() -> None:
    """NaN and infinity are refused rather than compared.

    `nan` compares false against everything, so an interval carrying one would
    be silently declared separable from every other — a wrong verdict that
    looks like a confident one.
    """
    with pytest.raises(MetricsError, match="finite"):
        overlap_verdict((float("nan"), 0.5), (0.2, 0.3))
    with pytest.raises(MetricsError, match="finite"):
        overlap_verdict((0.1, math.inf), (0.2, 0.3))
