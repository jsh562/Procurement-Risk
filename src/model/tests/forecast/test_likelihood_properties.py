"""T023 (RED) — the per-row log-contribution, at the mandatory property tier.

`plan.md` § Mandated properties gives `likelihood.py` one **Invariant** relation
for this pair: a censored row's contribution equals `log S(t)` and a completed
row's equals `log f(t)`, the two are never interchanged, and the censored
contribution is monotone decreasing in the censoring time while the density is
not. Domain: `t = 0`, `t` past the P99, and a censoring time equal to an
observed event time. AD-001 fixes the family — one lognormal per lifecycle
transition. The module under test does not exist yet; this is T023's red half.
"""

from __future__ import annotations

import math

import numpy as np
import pytest
from hypothesis import assume, given
from hypothesis import strategies as st
from model.forecast.likelihood import log_contribution

#: Φ⁻¹(0.99), to the precision the P99 boundary case needs.
Z99 = 2.3263478740408408

#: Relative tolerance for the agreement assertions. The reference below is the
#: textbook expression rather than a second implementation, so anything past
#: accumulated floating error is a different formula, not a rounding difference.
REL_TOL = 1e-9

#: How far into the upper tail the reference survival stays exact. `erfc(8/√2)`
#: is ~6e-16 and still a normal double; a few units further out it underflows to
#: zero and `log` of it is not a value this file could compare against. Every
#: duration below is drawn *as* a standardized position so the bound holds by
#: construction rather than by filtering, and the named boundaries — the median
#: at z = 0 and the P99 at z = 2.33 — sit well inside it.
Z_MAX = 6.0
Z_MIN = -6.0


def log_density(t: float, mu: float, sigma: float) -> float:
    """`log f(t)` for a lognormal, written out rather than composed."""
    z = (math.log(t) - mu) / sigma
    return -math.log(t) - math.log(sigma) - 0.5 * math.log(2.0 * math.pi) - 0.5 * z * z


def log_survival(t: float, mu: float, sigma: float) -> float:
    """`log S(t) = log P(T > t)` for a lognormal.

    `S(t) = ½·erfc(z/√2)` is the complementary form, taken directly rather than
    as `1 − Φ(z)`: the subtraction loses every significant digit in the upper
    tail, which is the only region where a censored contribution differs
    interestingly from zero.
    """
    if t <= 0.0:
        return 0.0
    z = (math.log(t) - mu) / sigma
    return math.log(0.5 * math.erfc(z / math.sqrt(2.0)))


def scalar(value: object) -> float:
    """One contribution as a Python float, whatever container it came back in."""
    array = np.asarray(value, dtype=float)
    assert array.size == 1, f"expected one contribution, got shape {array.shape}"
    return float(array.reshape(-1)[0])


def contribution(t: float, mu: float, sigma: float, *, censored: bool) -> float:
    return scalar(log_contribution(duration_days=t, mu=mu, sigma=sigma, is_censored=censored))


def frame(durations: np.ndarray, mu: float, sigma: float, censored: np.ndarray) -> np.ndarray:
    return np.asarray(
        log_contribution(duration_days=durations, mu=mu, sigma=sigma, is_censored=censored),
        dtype=float,
    )


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

#: AD-001's sojourn scale: `exp(μ)` between a week and a year, and a log spread
#: bracketing the 0.53 the dataset's published median and P80 back-solve to.
mus = st.floats(min_value=2.0, max_value=6.0, allow_nan=False, allow_infinity=False)
sigmas = st.floats(min_value=0.2, max_value=1.2, allow_nan=False, allow_infinity=False)
positions = st.floats(min_value=Z_MIN, max_value=Z_MAX, allow_nan=False, allow_infinity=False)


@st.composite
def cases(draw: st.DrawFn) -> tuple[float, float, float]:
    """`(t, μ, σ)` with the duration drawn as a standardized position.

    Drawing `t` directly and filtering would reject most of the upper half of
    the range and trip Hypothesis's own filtering health check; drawing the
    position instead covers the same durations — sub-day to several years — with
    nothing discarded.
    """
    mu = draw(mus)
    sigma = draw(sigmas)
    return (math.exp(mu + draw(positions) * sigma), mu, sigma)


@st.composite
def frames(draw: st.DrawFn) -> tuple[np.ndarray, np.ndarray, float, float]:
    """A frame of rows at one `(μ, σ)`, each with its own duration and indicator."""
    mu = draw(mus)
    sigma = draw(sigmas)
    rows = draw(st.lists(st.tuples(positions, st.booleans()), min_size=2, max_size=12))
    durations = np.array([math.exp(mu + z * sigma) for z, _ in rows], dtype=float)
    censored = np.array([flag for _, flag in rows], dtype=bool)
    return durations, censored, mu, sigma


# ---------------------------------------------------------------------------
# Invariant: which contribution each row takes
# ---------------------------------------------------------------------------


@given(case=cases())
def test_a_censored_row_contributes_the_log_survival(case: tuple[float, float, float]) -> None:
    """FR-003: an open line contributes the probability that its duration exceeds `t`."""
    t, mu, sigma = case

    assert contribution(t, mu, sigma, censored=True) == pytest.approx(
        log_survival(t, mu, sigma), rel=REL_TOL, abs=1e-12
    )


@given(case=cases())
def test_a_completed_row_contributes_the_log_density(case: tuple[float, float, float]) -> None:
    """An observed delivery contributes the density at the duration it took."""
    t, mu, sigma = case

    assert contribution(t, mu, sigma, censored=False) == pytest.approx(
        log_density(t, mu, sigma), rel=REL_TOL, abs=1e-12
    )


@given(case=cases())
def test_the_two_contributions_are_never_interchanged(
    case: tuple[float, float, float],
) -> None:
    """The defect this whole module exists for, asserted in both directions.

    The spec says it in US2's *Why P1*: a censored line written as a density
    builds cleanly and yields a plausible posterior, so nothing downstream
    reports it. Wherever the two quantities are separated at all, the censored
    contribution must not be the density and the completed one must not be the
    survival — a swap, a dropped indicator, or an `is_censored` read as an index
    all land here.
    """
    t, mu, sigma = case
    log_s = log_survival(t, mu, sigma)
    log_f = log_density(t, mu, sigma)
    assume(abs(log_s - log_f) > 1e-6)

    censored = contribution(t, mu, sigma, censored=True)
    completed = contribution(t, mu, sigma, censored=False)

    assert censored != completed
    assert censored != pytest.approx(log_f, rel=REL_TOL)
    assert completed != pytest.approx(log_s, rel=REL_TOL)


@given(case=cases())
def test_a_censoring_time_equal_to_an_observed_event_time_is_still_censored(
    case: tuple[float, float, float],
) -> None:
    """The domain's third case, and the one an outcome-keyed implementation fails.

    Two rows at the identical duration, one open and one delivered, differ only
    in their indicator. An implementation that keyed on the *duration* — or that
    inferred completion from the row landing inside the observed window — cannot
    tell them apart and returns one value for both.
    """
    t, mu, sigma = case
    assume(abs(log_survival(t, mu, sigma) - log_density(t, mu, sigma)) > 1e-6)

    assert contribution(t, mu, sigma, censored=True) != contribution(t, mu, sigma, censored=False)


# ---------------------------------------------------------------------------
# Invariant: monotone in the censoring time, and the density that is not
# ---------------------------------------------------------------------------


@given(case=cases(), step=st.floats(min_value=0.0, max_value=4.0))
def test_the_censored_contribution_never_rises_with_the_censoring_time(
    case: tuple[float, float, float], step: float
) -> None:
    """`log S` is non-increasing: surviving longer is never more probable.

    The later time is taken a bounded number of standard deviations further out,
    so the pair stays inside the region the reference is exact in while still
    sweeping from just-after to far-after.
    """
    t, mu, sigma = case
    later = t * math.exp(step * sigma)

    assert (
        contribution(later, mu, sigma, censored=True)
        <= contribution(t, mu, sigma, censored=True) + 1e-12
    )


@pytest.mark.parametrize(("mu", "sigma"), [(4.0, 0.53), (3.0, 0.9), (5.0, 0.3)])
def test_the_censored_contribution_strictly_decreases_across_the_quantiles(
    mu: float, sigma: float
) -> None:
    """Strict, at separations a double can express: five quantiles of the fit.

    The non-strict form above holds for a constant function too, and in the deep
    lower tail `S` really is 1.0 to the last bit — so the positions are taken as
    quantiles rather than as day counts. σ = 0.53 is the dataset's own back-solve
    from the published median 58.0 and P80 90.4 (AD-004); the others bracket it.
    """
    values = [
        contribution(math.exp(mu + z * sigma), mu, sigma, censored=True)
        for z in (-2.0, -1.0, 0.0, 1.0, 2.0)
    ]

    assert all(later < earlier for earlier, later in zip(values, values[1:], strict=False))
    assert values[0] <= 0.0, "a survival probability cannot exceed 1"


@pytest.mark.parametrize(("mu", "sigma"), [(4.0, 0.53), (3.0, 0.9), (5.0, 0.3)])
def test_the_density_contribution_is_not_monotone(mu: float, sigma: float) -> None:
    """The other half of the invariant, and the reason the first half discriminates.

    `f` rises to its mode at `exp(μ − σ²)` and falls after it, so a completed
    row's contribution *increases* over part of the domain. An implementation
    that took the survival for both row kinds would be monotone everywhere and
    would satisfy every assertion above; it fails here.
    """
    mode = math.exp(mu - sigma * sigma)
    rising = contribution(mode * 0.4, mu, sigma, censored=False)
    at_mode = contribution(mode, mu, sigma, censored=False)
    falling = contribution(mode * 4.0, mu, sigma, censored=False)

    assert rising < at_mode, "the density must rise towards its mode"
    assert falling < at_mode, "and fall away from it"


# ---------------------------------------------------------------------------
# Boundaries: t = 0, and t past the P99
# ---------------------------------------------------------------------------


@given(mu=mus, sigma=sigmas)
def test_a_censored_row_at_zero_elapsed_contributes_nothing(mu: float, sigma: float) -> None:
    """`S(0) = 1`, so `log S(0) = 0` exactly — a line ordered on the as-of date.

    Zero rather than a small negative number: the whole mass survives, and a
    contribution that drifted below zero here would penalise a line for existing.
    """
    assert contribution(0.0, mu, sigma, censored=True) == 0.0


@given(mu=mus, sigma=sigmas)
def test_a_completed_row_at_zero_elapsed_is_refused(mu: float, sigma: float) -> None:
    """A lognormal has no support at zero, and `-inf` is the silent failure.

    Summed into a log-likelihood, `-inf` surfaces as an initialisation failure
    the sampler reports far from its cause, and NumPy raises only a warning on
    the way. Refusing names the row instead — Principle III, where a mistake
    would otherwise be invisible.
    """
    with pytest.raises(ValueError):
        log_contribution(duration_days=0.0, mu=mu, sigma=sigma, is_censored=False)


@given(
    t=st.floats(min_value=-500.0, max_value=-0.5, allow_nan=False),
    mu=mus,
    sigma=sigmas,
    censored=st.booleans(),
)
def test_a_negative_duration_is_refused_on_both_sides(
    t: float, mu: float, sigma: float, censored: bool
) -> None:
    """An elapsed time before the line was ordered is a caller defect, not a value."""
    with pytest.raises(ValueError):
        log_contribution(duration_days=t, mu=mu, sigma=sigma, is_censored=censored)


@given(mu=mus, sigma=sigmas)
def test_a_censored_row_at_the_p99_carries_the_remaining_one_percent(
    mu: float, sigma: float
) -> None:
    """`t` past the P99, the domain's second case, with a value known in closed form.

    At the 99th percentile exactly one percent of the mass is left, so the
    contribution is `log 0.01` whatever μ and σ are. An off-by-a-tail
    implementation — the CDF where the survival belongs — gives `log 0.99` here
    and is otherwise plausible everywhere.
    """
    p99 = math.exp(mu + Z99 * sigma)

    assert contribution(p99, mu, sigma, censored=True) == pytest.approx(math.log(0.01), rel=1e-6)
    assert contribution(p99 * 2.0, mu, sigma, censored=True) < math.log(0.01)


@given(mu=mus, sigma=sigmas)
def test_the_median_leaves_half_the_mass(mu: float, sigma: float) -> None:
    """The same closed-form check at the centre, where a tail swap is invisible."""
    assert contribution(math.exp(mu), mu, sigma, censored=True) == pytest.approx(
        math.log(0.5), rel=1e-9
    )


# ---------------------------------------------------------------------------
# The vectorized surface
# ---------------------------------------------------------------------------


@given(built=frames())
def test_a_mixed_frame_gives_each_row_the_contribution_it_would_get_alone(
    built: tuple[np.ndarray, np.ndarray, float, float],
) -> None:
    """One call over a frame of both kinds equals one call per row.

    This is where a mis-broadcast mask shows up. `np.where` over an indicator of
    the wrong shape silently recycles it, so a frame of 199 rows can take the
    survival for the first row's status and the density for nobody — and every
    scalar assertion in this file still passes.
    """
    durations, censored, mu, sigma = built

    together = frame(durations, mu, sigma, censored)
    apart = np.array(
        [
            contribution(float(t), mu, sigma, censored=bool(flag))
            for t, flag in zip(durations, censored, strict=True)
        ],
        dtype=float,
    )

    assert together.shape == durations.shape
    assert together == pytest.approx(apart, rel=REL_TOL, abs=1e-12)


@given(built=frames())
def test_the_frame_result_tracks_the_indicator_row_by_row(
    built: tuple[np.ndarray, np.ndarray, float, float],
) -> None:
    """Flipping one row's indicator moves that row's contribution and no other's."""
    durations, censored, mu, sigma = built
    assume(
        all(
            abs(log_survival(float(t), mu, sigma) - log_density(float(t), mu, sigma)) > 1e-6
            for t in durations
        )
    )

    before = frame(durations, mu, sigma, censored)
    flipped = censored.copy()
    flipped[0] = not flipped[0]
    after = frame(durations, mu, sigma, flipped)

    assert before[0] != after[0]
    assert np.array_equal(before[1:], after[1:])
