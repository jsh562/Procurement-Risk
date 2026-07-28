"""T025 (RED) — conditional remaining draws and the survival grid.

`plan.md` § Mandated properties gives `posterior.py` three relations.
**Invariant (algebraic identity)**: `survival[k] == count(draws > k)/draw_count`
for every `k`, the array non-increasing and inside `[0,1]`. **Alternate
implementation**: `residual_tail_mass` recomputed from the draws agrees with
`survival[horizon]` within `probability_sum_tolerance`, by a different code
path. **Metamorphic**: AD-002's inverse-CDF conditioning, which the re-based
alternative violates. The module under test does not exist yet — this file is
the RED half of T025/T026 and must fail at collection.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Mapping, Sequence
from statistics import NormalDist
from typing import Any

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from numpy.typing import NDArray

from model.forecast.posterior import conditional_remaining_draws, survival_grid

# ---------------------------------------------------------------------------
# The interface this file pins
# ---------------------------------------------------------------------------
#
# `conditional_remaining_draws(uniforms, mu, sigma, elapsed_days)` returns one
# remaining duration per uniform, in **ascending** order — the canonical order
# `ck_line_posterior__draws_sorted` and `schema_constants.percentile_convention`
# both rest on. Every parameter is `ArrayLike` and broadcasts, because each
# posterior draw carries its own `(μ, σ)`.
#
# `survival_grid(draws, horizon_days)` returns the grid and the residual
# together, published under the two names the stores use: **`survival`** and
# **`residual_tail_mass`**. Read here by name rather than by position, so the
# container `posterior.py` chooses is `write.py`'s concern and not a property of
# the arithmetic.

#: `schema_constants.probability_sum_tolerance`. A literal here because this
#: tier is pure and reads no database; auditing the DDL-side copies against the
#: published row is G-3's remediation, not this file's job.
PROBABILITY_SUM_TOLERANCE = 1e-9

#: The standard normal, from the stdlib. SciPy is only a transitive dependency
#: of PyMC here and this epic adds none — the same reason `likelihood.py`
#: reaches for `math.erfc` rather than `scipy.special`.
NORMAL = NormalDist()

#: Φ⁻¹(0.99), to the precision the P99 boundary case needs.
Z99 = 2.3263478740408408

#: The committed dataset's own scale: `exp(4.06) ≈ 58` days, the published
#: median, at the `σ = 0.527` AD-004 back-solves from that median and the P80.
MU_FIT = 4.06
SIGMA_FIT = 0.527


# ---------------------------------------------------------------------------
# References — each computed by a path the module under test does not use
# ---------------------------------------------------------------------------


def survival_by_counting(draws: Sequence[float], horizon_days: int) -> list[float]:
    """`count(draws > k)/n` for `k = 1..horizon_days`, as a Python loop.

    Deliberately not vectorized. DV-004 is an *agreement* test, and a NumPy
    comparison against a NumPy comparison would restate one expression rather
    than check it. The threshold is strict: a draw landing exactly on day `k`
    has delivered by the end of day `k` and does not survive it.
    """
    total = len(draws)
    return [sum(1 for value in draws if value > k) / total for k in range(1, horizon_days + 1)]


def residual_by_bisect(draws: Sequence[float], horizon_days: int) -> float:
    """The tail mass by binary search on the sorted values — a third path again.

    `bisect_right` returns the insertion point after every element equal to the
    horizon, so `n − bisect_right` is `count(draws > horizon_days)` with the
    same strict `>` the grid uses. DV-003's whole content is that this number
    and `survival[horizon_days]` are the same comparison.
    """
    ordered = sorted(float(value) for value in draws)
    return (len(ordered) - bisect.bisect_right(ordered, float(horizon_days))) / len(ordered)


def survival_at(t: float, mu: float, sigma: float) -> float:
    """`S(t) = ½·erfc(z/√2)` for a lognormal — never `1 − Φ(z)`.

    The subtraction loses every significant digit in the upper tail, which is
    exactly where a conditioned draw's reference value lives.
    """
    if t <= 0.0:
        return 1.0
    return 0.5 * math.erfc((math.log(t) - mu) / (sigma * math.sqrt(2.0)))


def total_at(u: float, mu: float, sigma: float) -> float:
    """`F⁻¹(u)` for a lognormal: the unconditional total duration at quantile `u`."""
    return math.exp(mu + sigma * NORMAL.inv_cdf(u))


def conditional_total(u: float, mu: float, sigma: float, elapsed: float) -> float:
    """AD-002 written out: `F* = F(e) + u·(1 − F(e))`, then `T = F⁻¹(F*)`.

    One evaluation per draw, exact, and a pure function of `(u, θ, e)` — which
    is what makes the whole module property-testable rather than only
    integration-testable.
    """
    survives_elapsed = survival_at(elapsed, mu, sigma)
    return total_at(1.0 - survives_elapsed * (1.0 - u), mu, sigma)


def re_based_remaining(
    uniforms: Sequence[float], mu: float, sigma: float, elapsed: float
) -> NDArray[np.float64]:
    """The alternative FR-029 exists to forbid: a total draw, minus elapsed, clipped.

    Kept in this file because a property that cannot separate the two
    implementations is a property that has not been tested. It satisfies
    `ck_line_posterior__draws_non_negative`, the sort check, and every array
    invariant below; only the conditioning relations catch it.
    """
    totals = np.array([total_at(u, mu, sigma) for u in uniforms], dtype=float)
    return np.sort(np.clip(totals - elapsed, 0.0, None))


def midpoint_uniforms(count: int) -> NDArray[np.float64]:
    """`u_i = (i + ½)/n`, ascending — a deterministic stand-in for the sampler's.

    Nothing here tests an RNG. A fixed grid makes every empirical quantile a
    function of `n` alone, so an agreement tolerance of `1/n` is an exact bound
    rather than a probabilistic one, and it keeps the pairing between a uniform
    and its draw intact under the module's ascending sort.
    """
    return (np.arange(count) + 0.5) / count


# ---------------------------------------------------------------------------
# Calling conventions
# ---------------------------------------------------------------------------


def published(result: Any, name: str) -> Any:
    """One field of `survival_grid`'s result, read by name."""
    if isinstance(result, Mapping):
        return result[name]
    assert hasattr(result, name), (
        f"`survival_grid` must publish {name!r}; the grid and the residual are one "
        f"result because DV-003 is an agreement between them, not two calls"
    )
    return getattr(result, name)


def grid_of(draws: Any, horizon_days: int) -> tuple[NDArray[np.float64], float]:
    """The survival array and the residual tail mass, as two plain values."""
    result = survival_grid(draws=draws, horizon_days=horizon_days)
    survival = np.asarray(published(result, "survival"), dtype=float)
    return survival, float(published(result, "residual_tail_mass"))


def remaining_of(uniforms: Any, mu: Any, sigma: Any, elapsed: Any) -> NDArray[np.float64]:
    return np.asarray(
        conditional_remaining_draws(uniforms=uniforms, mu=mu, sigma=sigma, elapsed_days=elapsed),
        dtype=float,
    )


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

#: AD-001's sojourn scale, matching `test_likelihood_properties.py`: `exp(μ)`
#: between a week and a year, and a log spread bracketing the fit's 0.527.
mus = st.floats(min_value=2.0, max_value=6.0, allow_nan=False, allow_infinity=False)
sigmas = st.floats(min_value=0.2, max_value=1.2, allow_nan=False, allow_infinity=False)

#: The open unit interval. `u = 0` and `u = 1` map to `∓∞` through `F⁻¹` and are
#: refused rather than drawn; the refusal is asserted separately below.
uniforms = st.floats(min_value=1e-6, max_value=1.0 - 1e-6, allow_nan=False, allow_infinity=False)

#: Day counts drawn *including exact integers*, because the strict `>` boundary
#: only exists at whole days and continuous floats land on one about never.
day_values = st.one_of(
    st.floats(min_value=0.0, max_value=60.0, allow_nan=False, allow_infinity=False),
    st.integers(min_value=0, max_value=60).map(float),
)


@st.composite
def draw_sets(draw: st.DrawFn) -> tuple[NDArray[np.float64], int]:
    """A sorted draw array and a horizon, both small enough to count by hand.

    The real run is 4,000 draws over 365 days; the identity being asserted does
    not depend on either number, and a 40-day horizon keeps 200 Hypothesis
    examples cheap. The realized shape is pinned by DV-014 over emitted runs,
    which is AD-009's job and not this tier's.
    """
    horizon = draw(st.integers(min_value=1, max_value=40))
    values = draw(st.lists(day_values, min_size=1, max_size=30))
    return np.sort(np.asarray(values, dtype=float)), horizon


# ---------------------------------------------------------------------------
# Invariant (algebraic identity): the grid is the strict threshold count
# ---------------------------------------------------------------------------


@given(built=draw_sets())
def test_the_grid_is_the_strict_threshold_count_at_every_k(
    built: tuple[NDArray[np.float64], int],
) -> None:
    """DV-004 over the in-memory grid, at every `k` rather than at a sample of them.

    The grid is a pure function of the draws, so this is an identity and not an
    approximation. A wrong grid that is non-increasing, inside `[0,1]` and whose
    tail matches the stored residual satisfies every delivered constraint — the
    plan's own reason this module is property-tested rather than trusted.
    """
    draws, horizon = built
    survival, _ = grid_of(draws, horizon)

    assert survival == pytest.approx(
        survival_by_counting(draws, horizon), abs=PROBABILITY_SUM_TOLERANCE
    )


@given(built=draw_sets())
def test_the_grid_is_non_increasing_and_inside_the_unit_interval(
    built: tuple[NDArray[np.float64], int],
) -> None:
    """`fn_is_non_increasing` and `fn_all_within_unit_interval`, before the write.

    The database enforces both on the stored array. Asserting them here is what
    makes a violation a named property failure over the value `posterior.py`
    produced, rather than a constraint error at the end of a sampling run.
    """
    draws, horizon = built
    survival, _ = grid_of(draws, horizon)

    assert np.all(survival >= 0.0)
    assert np.all(survival <= 1.0)
    assert np.all(np.diff(survival) <= 0.0)


@given(built=draw_sets())
def test_the_named_boundaries_are_k_equals_one_and_k_equals_the_horizon(
    built: tuple[NDArray[np.float64], int],
) -> None:
    """The two `k` the plan's domain column names, stated separately from the sweep.

    The first and last elements are where an off-by-one lives: everything
    between them is bracketed by its neighbours and an interior shift shows up
    in the monotonicity check, while a shift at either end does not.
    """
    draws, horizon = built
    survival, _ = grid_of(draws, horizon)
    total = len(draws)

    assert survival[0] == pytest.approx(
        sum(1 for value in draws if value > 1) / total, abs=PROBABILITY_SUM_TOLERANCE
    )
    assert survival[-1] == pytest.approx(
        sum(1 for value in draws if value > horizon) / total, abs=PROBABILITY_SUM_TOLERANCE
    )


def test_the_array_is_indexed_from_day_one_and_stores_no_s_of_zero() -> None:
    """`survival[1]` is `P(remaining > 1 day)`, and there is no `S(0)` element.

    Four separate times in this spec's history a criterion was written against
    an `S(0)` the schema does not store, which is why the index convention is
    asserted on its own with hand-countable numbers. Ten draws, a three-day
    horizon: six exceed day 1, three exceed day 2, one exceeds day 3. An
    implementation that emitted `S(0)` first — or that counted `draws > k − 1` —
    would open with `1.0`, because every draw exceeds zero.
    """
    draws = np.array([0.6, 0.7, 0.8, 0.9, 1.5, 1.6, 1.7, 2.4, 2.5, 3.5])
    survival, _ = grid_of(draws, 3)

    assert len(survival) == 3
    assert survival == pytest.approx([0.6, 0.3, 0.1], abs=PROBABILITY_SUM_TOLERANCE)


def test_a_line_delivering_tomorrow_has_a_low_first_element_rather_than_a_wrong_one() -> None:
    """The plan's own example: `survival[1]` near 0.3 is legitimate, not a defect.

    Seven of ten draws land inside the first day for a line about to deliver, so
    the honest `P(remaining > 1 day)` is 0.3. Read as `S(0)` it would look like a
    line 70% delivered before the grid starts, which is the misreading the
    absent zeroth element exists to prevent.
    """
    draws = np.array([0.1, 0.2, 0.3, 0.4, 0.5, 0.6, 0.9, 1.4, 2.2, 5.0])
    survival, _ = grid_of(draws, 3)

    assert survival[0] == pytest.approx(0.3, abs=PROBABILITY_SUM_TOLERANCE)


def test_a_draw_landing_exactly_on_day_k_counts_as_delivered() -> None:
    """The strict `>` the normative table publishes, at the only place it shows.

    Eight draws all at exactly 5.0 days over a ten-day horizon. Under `>` the
    line has delivered by the end of day 5, so `survival[5]` is 0; under `>=` it
    would be 1 and the whole curve would sit one day late. No continuous draw
    ever lands on an integer, so this case is unreachable by sampling and
    reachable only by construction.
    """
    draws = np.full(8, 5.0)
    survival, residual = grid_of(draws, 10)

    assert list(survival[:4]) == [1.0, 1.0, 1.0, 1.0]
    assert survival[4] == 0.0
    assert np.all(survival[4:] == 0.0)
    assert residual == 0.0


@given(built=draw_sets())
def test_every_draw_beyond_the_horizon_gives_a_grid_of_ones(
    built: tuple[NDArray[np.float64], int],
) -> None:
    """The domain's first extreme, and the reachable one (`data-model.md`, L-2).

    A line whose every draw runs past the grid is not expressible by the grid at
    all; the array is flat at 1 and the whole statement moves into the residual.
    """
    draws, horizon = built
    beyond = draws + horizon + 1.0
    survival, residual = grid_of(beyond, horizon)

    assert np.all(survival == 1.0)
    assert residual == 1.0


@given(horizon=st.integers(min_value=1, max_value=40), count=st.integers(min_value=1, max_value=30))
def test_every_draw_at_zero_gives_a_grid_of_zeros(horizon: int, count: int) -> None:
    """The domain's other extreme: nothing survives day one, so nothing survives day `k`.

    `residual_tail_mass` is exactly 0 here, which is one of the two boundary
    values the alternate-implementation row names.
    """
    survival, residual = grid_of(np.zeros(count), horizon)

    assert np.all(survival == 0.0)
    assert residual == 0.0


# ---------------------------------------------------------------------------
# Alternate implementation: the residual against the grid tail
# ---------------------------------------------------------------------------


@given(built=draw_sets())
def test_the_residual_agrees_with_the_grid_tail_within_the_published_tolerance(
    built: tuple[NDArray[np.float64], int],
) -> None:
    """DV-003 over the in-memory pair, in the `abs(a − b) <= 1e-9` form the DDL uses.

    Never `=`: E003 records exact equality as a form it explicitly refuses, and
    `ck_line_posterior__residual_matches_grid_tail` is the constraint this
    assertion is the pre-image of.
    """
    draws, horizon = built
    survival, residual = grid_of(draws, horizon)

    assert abs(residual - survival[-1]) <= PROBABILITY_SUM_TOLERANCE


@given(built=draw_sets())
def test_the_residual_recomputed_by_binary_search_agrees_with_the_stored_one(
    built: tuple[NDArray[np.float64], int],
) -> None:
    """The **different code path** DV-003 requires, rather than a restatement.

    `bisect_right` over the sorted values touches neither the loop that built
    the grid nor the comparison that wrote the residual, so an agreement here is
    evidence about the number rather than about one expression being itself.
    """
    draws, horizon = built
    _, residual = grid_of(draws, horizon)

    assert residual == pytest.approx(
        residual_by_bisect(draws, horizon), abs=PROBABILITY_SUM_TOLERANCE
    )


@pytest.mark.parametrize("horizon", [1, 30, 365])
def test_a_residual_of_exactly_zero_and_exactly_one(horizon: int) -> None:
    """Both named boundaries in one place, including the real 365-day horizon.

    Exactly 0 and exactly 1 are the two values a tolerance cannot rescue: a
    residual computed as `1 − count(draws <= h)/n` would round the second to
    `0.9999999999999999` on some inputs, which `ck_..._residual_range` accepts
    and `abs(survival[h] − residual) <= 1e-9` then still passes — so the test is
    for the exact value, not for a neighbourhood of it.
    """
    inside = np.linspace(0.0, float(horizon), 16)
    outside = np.linspace(float(horizon) + 1.0, float(horizon) + 100.0, 16)

    assert grid_of(inside, horizon)[1] == 0.0
    assert grid_of(outside, horizon)[1] == 1.0


# ---------------------------------------------------------------------------
# Refusals on the grid
# ---------------------------------------------------------------------------


def test_an_empty_draw_set_is_refused() -> None:
    """`count/0` is not a probability, and NumPy would return `nan` with a warning.

    `ck_line_posterior__survival_unit_interval` rejects a NaN array, but only
    after a whole sampling run. Refusing here names the caller that passed no
    draws (Principle III).
    """
    with pytest.raises(ValueError):
        survival_grid(draws=np.array([]), horizon_days=30)


@pytest.mark.parametrize("horizon", [0, -1])
def test_a_non_positive_horizon_is_refused(horizon: int) -> None:
    """A zero-length grid is not a shorter grid; it is an array the store rejects.

    `ck_line_posterior__survival_length` compares against `horizon_days`, so a
    zero horizon would produce an empty array that satisfies the length check
    and expresses nothing at all.
    """
    with pytest.raises(ValueError):
        survival_grid(draws=np.array([1.0, 2.0]), horizon_days=horizon)


# ---------------------------------------------------------------------------
# The conditioning itself — AD-002
# ---------------------------------------------------------------------------


@given(mu=mus, sigma=sigmas)
def test_at_zero_elapsed_the_conditional_draw_is_the_unconditional_total(
    mu: float, sigma: float
) -> None:
    """The domain's first boundary: `F(0) = 0`, so `F* = u` and `T = F⁻¹(u)`.

    A line ordered on the as-of date has consumed no mass, and its remaining
    duration is its total duration. This is also the one elapsed time at which
    the re-based alternative is *correct*, which is why the tests that
    discriminate against it all move away from zero.
    """
    grid = midpoint_uniforms(64)
    expected = np.sort(np.array([total_at(u, mu, sigma) for u in grid]))

    assert remaining_of(grid, mu, sigma, 0.0) == pytest.approx(expected, rel=1e-9)


@given(mu=mus, sigma=sigmas, u=uniforms, position=st.floats(min_value=-3.0, max_value=3.0))
def test_the_conditional_draw_is_the_inverse_cdf_construction(
    mu: float, sigma: float, u: float, position: float
) -> None:
    """AD-002 as an identity, asserted on the implied delivery date.

    Stated as `remaining + elapsed == F⁻¹(F(e) + u(1 − F(e)))` rather than on the
    remaining duration alone: when `u` is small and `e` is large the difference
    `T − e` is a cancellation of two nearly equal doubles, and a relative
    tolerance on it would measure floating-point subtraction rather than the
    construction. The elapsed time is drawn as a standardized position so it
    sweeps the whole distribution, the P99 included, with nothing filtered.
    """
    elapsed = math.exp(mu + position * sigma)
    drawn = float(remaining_of([u], mu, sigma, elapsed)[0])

    assert drawn + elapsed == pytest.approx(conditional_total(u, mu, sigma, elapsed), rel=1e-9)


@given(mu=mus, sigma=sigmas, position=st.floats(min_value=-2.0, max_value=3.0))
def test_every_conditional_draw_is_strictly_positive(
    mu: float, sigma: float, position: float
) -> None:
    """A line that survived `e` has not delivered, so no draw of its remainder is zero.

    `F*` sits strictly above `F(e)` for every `u` in `(0,1)`, so `T > e` and the
    remainder is strictly positive. Re-basing instead puts a **point mass of
    size `F(e)`** at exactly zero — at the P99 that is 99% of the draws — and
    `ck_line_posterior__draws_non_negative` accepts every one of them.
    """
    elapsed = math.exp(mu + position * sigma)

    assert np.all(remaining_of(midpoint_uniforms(256), mu, sigma, elapsed) > 0.0)


@given(mu=mus, sigma=sigmas, position=st.floats(min_value=-2.0, max_value=3.0))
def test_the_draws_come_back_ascending_one_per_uniform(
    mu: float, sigma: float, position: float
) -> None:
    """The canonical order, which is what makes the percentile convention a lookup.

    `ck_line_posterior__draws_sorted` requires it of the stored array, and
    `schema_constants.percentile_convention` reads `draws[ceil(p·n)]` — an
    unsorted array satisfies neither and fails only after a sampling run.
    """
    grid = midpoint_uniforms(37)
    drawn = remaining_of(grid, mu, sigma, math.exp(mu + position * sigma))

    assert drawn.shape == (37,)
    assert np.all(np.diff(drawn) >= 0.0)


@given(mu=mus, sigma=sigmas)
def test_the_parameters_may_vary_draw_by_draw(mu: float, sigma: float) -> None:
    """Each posterior draw carries its own `(μ, σ)`; the conditioning is per draw.

    The real call site hands in 4,000 sampled pairs, not one. A module that
    broadcast a scalar correctly and silently recycled the first element of a
    vector would produce a plausible curve from one posterior draw repeated
    4,000 times, which no downstream constraint distinguishes.
    """
    grid = midpoint_uniforms(16)
    per_draw_mu = mu + 0.01 * np.arange(16)
    per_draw_sigma = sigma + 0.005 * np.arange(16)
    elapsed = math.exp(mu)
    expected = np.sort(
        np.array(
            [
                conditional_total(u, float(m), float(s), elapsed) - elapsed
                for u, m, s in zip(grid, per_draw_mu, per_draw_sigma, strict=True)
            ]
        )
    )

    assert remaining_of(grid, per_draw_mu, per_draw_sigma, elapsed) == pytest.approx(
        expected, rel=1e-9, abs=1e-9 * (1.0 + elapsed)
    )


# ---------------------------------------------------------------------------
# Metamorphic: conditioning is monotone in the elapsed time
# ---------------------------------------------------------------------------


@given(
    mu=mus,
    sigma=sigmas,
    first=st.floats(min_value=-3.0, max_value=3.0),
    second=st.floats(min_value=-3.0, max_value=3.0),
)
def test_the_drawn_delivery_date_is_monotone_in_the_elapsed_time(
    mu: float, sigma: float, first: float, second: float
) -> None:
    """Metamorphic, per draw: conditioning on more elapsed time never moves a
    line's implied delivery date earlier.

    This is AD-002's monotonicity in the form that holds over the whole domain.
    `F* = F(e) + u(1 − F(e))` is increasing in `e` for every fixed `u`, so
    `T(e)` is too, and the uniforms are handed in ascending so the module's own
    ascending sort leaves the pairing between the two calls intact.
    """
    earlier, later = sorted((math.exp(mu + first * sigma), math.exp(mu + second * sigma)))
    grid = midpoint_uniforms(64)

    before = remaining_of(grid, mu, sigma, earlier) + earlier
    after = remaining_of(grid, mu, sigma, later) + later

    assert np.all(after >= before - 1e-9 * (1.0 + later))


@pytest.mark.parametrize(("mu", "sigma"), [(MU_FIT, SIGMA_FIT), (3.0, 0.9), (5.0, 0.3)])
def test_the_median_remaining_never_collapses_at_the_named_elapsed_boundaries(
    mu: float, sigma: float
) -> None:
    """The three elapsed times the plan's domain column names: 0, the median, past the P99.

    At zero the median remainder is the distribution's own median. At the median
    and past the P99 it is smaller but strictly positive, because a conditioned
    draw is a draw from the tail rather than a leftover of a total. The re-based
    alternative reports **exactly zero** past the P99 — the "already delivered"
    curve AD-002 names — and the negative control below shows it.
    """
    grid = midpoint_uniforms(2000)
    at_zero = float(np.median(remaining_of(grid, mu, sigma, 0.0)))
    at_median = float(np.median(remaining_of(grid, mu, sigma, math.exp(mu))))
    past_p99 = float(np.median(remaining_of(grid, mu, sigma, 2.0 * math.exp(mu + Z99 * sigma))))

    assert at_zero == pytest.approx(math.exp(mu), rel=0.01)
    assert at_median > 0.0
    assert past_p99 > 0.0


@pytest.mark.parametrize(("mu", "sigma"), [(MU_FIT, SIGMA_FIT), (3.0, 0.9), (5.0, 0.7)])
def test_increasing_elapsed_never_decreases_the_median_in_the_long_open_regime(
    mu: float, sigma: float
) -> None:
    """The plan's metamorphic row, asserted with its basis condition published.

    A lognormal's hazard rises to a peak and falls after it, so its median
    residual life **falls** while the hazard is rising and rises afterwards: at
    `σ = 0.527` the turn is at about `1.5 × exp(μ)`, well inside the body of the
    distribution. Sweeping elapsed across the whole domain and asserting a
    non-decreasing median would therefore assert something false of a correct
    inverse-CDF implementation.

    The basis condition, stated here rather than discovered later: for `σ ≥ 0.4`
    the turn sits at or below the P99, so the claim holds from the P99 outward —
    which is the regime the forecast exists for, the longest-open lines. At
    `σ = 0.3` it does not, which is why this is a parametrized comparison at the
    fit's own scale and not a Hypothesis sweep over σ.
    """
    grid = midpoint_uniforms(2000)
    p99 = math.exp(mu + Z99 * sigma)
    medians = [
        float(np.median(remaining_of(grid, mu, sigma, p99 * multiple)))
        for multiple in (1.0, 1.5, 2.0, 3.0)
    ]

    assert all(later >= earlier for earlier, later in zip(medians, medians[1:], strict=False)), (
        f"the median remainder fell as the line stayed open longer: {medians}"
    )


@pytest.mark.parametrize("position", [-1.0, 0.0, 1.0, Z99])
def test_the_conditional_law_is_the_law_truncated_at_the_elapsed_time(position: float) -> None:
    """Alternate implementation: `count(R > k)/n == S(e + k)/S(e)`.

    The defining property of conditioning, stated over the same strict threshold
    count the grid uses, so the two halves of this module are checked against
    one closed form. Re-basing gives `S(e + k)/S(0)` instead — smaller by the
    whole factor `S(e)`, which at the P99 is a hundredfold understatement of
    every remaining probability.

    The uniforms are the deterministic midpoint grid, so the agreement bound is
    `1/n` exactly rather than a sampling statement.
    """
    count = 2000
    elapsed = math.exp(MU_FIT + position * SIGMA_FIT)
    drawn = remaining_of(midpoint_uniforms(count), MU_FIT, SIGMA_FIT, elapsed)
    survives_elapsed = survival_at(elapsed, MU_FIT, SIGMA_FIT)

    for k in (1, 5, 20, 60):
        expected = survival_at(elapsed + k, MU_FIT, SIGMA_FIT) / survives_elapsed
        assert np.count_nonzero(drawn > k) / count == pytest.approx(expected, abs=2.0 / count)


def test_the_re_based_alternative_satisfies_the_array_checks_and_fails_the_conditioning() -> None:
    """The negative control the plan asks the property set to carry (HINT-004).

    Three statements about the forbidden implementation, in one place because
    the point is the contrast: it is non-negative and sorted, so
    `ck_line_posterior__draws_non_negative` and `ck_..._draws_sorted` accept it;
    its grid is non-increasing and inside `[0,1]`, so every array invariant in
    this file passes over it; and past the P99 its median remainder is **exactly
    zero** and its tail probabilities are the unconditional ones — the two
    relations above are the only things between it and a shipped run.
    """
    grid = midpoint_uniforms(2000)
    elapsed = 2.0 * math.exp(MU_FIT + Z99 * SIGMA_FIT)
    forbidden = re_based_remaining(grid, MU_FIT, SIGMA_FIT, elapsed)
    conditioned = remaining_of(grid, MU_FIT, SIGMA_FIT, elapsed)

    assert np.all(forbidden >= 0.0)
    assert np.all(np.diff(forbidden) >= 0.0)
    survival, residual = grid_of(forbidden, 365)
    assert np.all(np.diff(survival) <= 0.0)
    assert np.all((survival >= 0.0) & (survival <= 1.0))
    assert abs(residual - survival[-1]) <= PROBABILITY_SUM_TOLERANCE

    assert float(np.median(forbidden)) == 0.0
    assert float(np.median(conditioned)) > 0.0
    assert np.count_nonzero(forbidden == 0.0) > 0
    assert np.count_nonzero(conditioned == 0.0) == 0


# ---------------------------------------------------------------------------
# Refusals on the conditioning
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("elapsed", [-1.0, -0.5, -400.0])
def test_a_negative_elapsed_time_is_refused(elapsed: float) -> None:
    """An as-of date before the line was ordered is a caller defect, not a value.

    `censoring.elapsed_days` returns the signed difference deliberately, so the
    negative case is reachable and has to be refused where it would otherwise
    condition on a line that does not exist yet.
    """
    with pytest.raises(ValueError):
        conditional_remaining_draws(
            uniforms=[0.5], mu=MU_FIT, sigma=SIGMA_FIT, elapsed_days=elapsed
        )


@pytest.mark.parametrize("sigma", [0.0, -0.5])
def test_a_non_positive_scale_is_refused(sigma: float) -> None:
    """A σ of zero divides in the standardization and a negative one reverses the tail.

    The same refusal `likelihood.py` makes, for the same reason and in the same
    place: before any arithmetic, so the message names the parameter.
    """
    with pytest.raises(ValueError):
        conditional_remaining_draws(uniforms=[0.5], mu=MU_FIT, sigma=sigma, elapsed_days=10.0)


@pytest.mark.parametrize("u", [0.0, 1.0, -0.1, 1.5])
def test_a_uniform_outside_the_open_unit_interval_is_refused(u: float) -> None:
    """`F⁻¹(0)` and `F⁻¹(1)` are `0` and `∞`, and neither is a duration.

    An infinite draw sorts last, passes the non-negativity check, and turns the
    residual into 1 on a line nothing is wrong with. Refusing at the input is
    the only place the caller is still identifiable.
    """
    with pytest.raises(ValueError):
        conditional_remaining_draws(uniforms=[u], mu=MU_FIT, sigma=SIGMA_FIT, elapsed_days=10.0)
