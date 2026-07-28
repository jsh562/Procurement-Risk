"""Conditional remaining draws, the canonical sort, the grid and its residual.

FR-010, FR-011, FR-029. AD-002 draws an open line's remainder by **inverse-CDF
conditioning** — `F* = F(e) + u·(1 − F(e))`, `T = F⁻¹(F*)`, stored as `T − e`.
Never rejection, whose acceptance rate `1 − F(e)` collapses on exactly the
longest-open lines the forecast exists for; never re-basing a total draw, which
puts a point mass of size `F(e)` at zero and still satisfies every delivered
constraint. Strict positivity of every returned draw is what separates the two.

The grid runs `k = 1..horizon_days` with a strict `>`, so there is no `S(0)`
element and a draw landing exactly on day `k` has delivered. `residual_tail_mass`
is counted by binary search over the order statistics rather than read off the
grid's tail, which is what makes DV-003 an agreement between two paths.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import NormalDist

import numpy as np
from numpy.typing import ArrayLike, NDArray

__all__ = [
    "PosteriorError",
    "SurvivalGrid",
    "conditional_remaining_draws",
    "survival_grid",
]


class PosteriorError(ValueError):
    """Raised when draws or a horizon cannot describe a remaining duration.

    A `ValueError`: every case is a value the caller handed over. Named as its
    own type so a refusal is distinguishable from a NumPy shape error, and a
    `ValueError` subclass so it is catchable either way.
    """


#: `math.erfc` and `NormalDist.inv_cdf` lifted over arrays. The stdlib scalars
#: are used rather than `scipy.special` for the reason `likelihood.py` gives:
#: SciPy is only a transitive dependency of PyMC here and this epic adds none.
#: The vectorization is a Python-level loop, which is affordable because this
#: module runs once per line over the draw vector, not inside the sampler.
_erfc = np.vectorize(math.erfc, otypes=[float])
_inv_cdf = np.vectorize(NormalDist().inv_cdf, otypes=[float])

_SQRT_TWO = math.sqrt(2.0)

#: The largest quantile `F⁻¹` has a finite answer for. `F*` is strictly below 1
#: in exact arithmetic, but `1 − S(e)(1 − u)` rounds to exactly 1 once `S(e)` is
#: small enough, and `inv_cdf(1.0)` raises from inside the stdlib rather than
#: naming the line that reached the far tail. Capping keeps the draw finite and
#: enormous, which is the honest answer for a line open that long.
_LARGEST_QUANTILE = math.nextafter(1.0, 0.0)


# `eq=False` because `survival` is an array and a generated `__eq__` would
# compare elementwise, yielding an array where a bool is expected.
@dataclass(frozen=True, slots=True, eq=False)
class SurvivalGrid:
    """The grid and the residual together, under the names the stores use.

    One result rather than two calls: DV-003 is an *agreement* between these two
    numbers, and a caller able to obtain one without the other would eventually
    store a residual taken over a different draw set than the grid beside it.
    """

    survival: NDArray[np.float64]
    residual_tail_mass: float


def _positive_scale(sigma: ArrayLike) -> NDArray[np.float64]:
    """The log-scale, proved positive before any arithmetic reads it.

    The same refusal `likelihood.py` makes in the same place: a σ of zero
    divides in the standardization and a negative one reverses the tail the
    conditioning is taken over.
    """
    scale = np.asarray(sigma, dtype=float)
    if not np.all(np.isfinite(scale)) or np.any(scale <= 0.0):
        raise PosteriorError(
            "the lognormal log-scale must be finite and strictly positive; a σ of zero "
            "divides by zero in the standardization and a negative one silently reverses "
            "the tail the conditional draw is taken from"
        )
    return scale


def _survival(
    elapsed: NDArray[np.float64], mu: NDArray[np.float64], sigma: NDArray[np.float64]
) -> NDArray[np.float64]:
    """`S(t) = ½·erfc(z/√2)`, never `1 − Φ(z)`.

    The subtraction loses every significant digit in the upper tail, which is
    exactly where a long-open line's `S(e)` lives — and `S(e)` multiplies the
    whole conditioning, so a digit lost here is a digit lost in every draw.
    `S(0) = 1` exactly: no mass has been consumed, and the substitute below keeps
    `log` out of its invalid domain without changing a value that is used.
    """
    open_before_anchor = elapsed > 0.0
    safe = np.where(open_before_anchor, elapsed, 1.0)
    survives = 0.5 * _erfc((np.log(safe) - mu) / (sigma * _SQRT_TWO))
    return np.asarray(np.where(open_before_anchor, survives, 1.0), dtype=float)


def conditional_remaining_draws(
    uniforms: ArrayLike, mu: ArrayLike, sigma: ArrayLike, elapsed_days: ArrayLike
) -> NDArray[np.float64]:
    """One remaining duration per uniform, ascending, conditioned on `elapsed_days`.

    AD-002 written out: `F* = F(e) + u·(1 − F(e))` in the `1 − S(e)(1 − u)` form
    that keeps the tail's precision, `T = F⁻¹(F*)`, returned as `T − e`. Every
    argument broadcasts, because each posterior draw carries its own `(μ, σ)` and
    the conditioning is per draw rather than per line.

    Ascending because `ck_line_posterior__draws_sorted` requires it of the stored
    array and `schema_constants.percentile_convention` reads `draws[ceil(p·n)]` —
    the canonical order is what makes a percentile a lookup rather than a scan.
    """
    drawn = np.atleast_1d(np.asarray(uniforms, dtype=float))
    location = np.asarray(mu, dtype=float)
    scale = _positive_scale(sigma)
    elapsed = np.asarray(elapsed_days, dtype=float)

    if drawn.size == 0:
        raise PosteriorError(
            "no uniforms were passed; a line with no draws has no posterior, and an empty "
            "array would reach `survival_grid` as a division by zero rather than as a "
            "named refusal"
        )
    if not np.all(np.isfinite(drawn)) or np.any(drawn <= 0.0) or np.any(drawn >= 1.0):
        raise PosteriorError(
            "every uniform must lie strictly inside `(0, 1)`; `F⁻¹(0)` is zero and `F⁻¹(1)` "
            "is infinite, and an infinite draw sorts last, passes the non-negativity check "
            "and turns the residual into 1 on a line nothing is wrong with"
        )
    if not np.all(np.isfinite(location)):
        raise PosteriorError(
            "the lognormal log-location must be finite; a NaN or an infinity here produces "
            "a whole draw vector of NaN that no stored constraint attributes to its cause"
        )
    if not np.all(np.isfinite(elapsed)):
        raise PosteriorError(
            "the elapsed time must be finite; NaN and infinity are not elapsed times, and "
            "both propagate silently through the conditioning"
        )
    if np.any(elapsed < 0.0):
        raise PosteriorError(
            "a negative elapsed time was passed; an as-of date before the line was ordered "
            "is a caller defect rather than a value, and conditioning on it would describe "
            "a line that does not exist yet"
        )

    drawn, location, scale, elapsed = np.broadcast_arrays(drawn, location, scale, elapsed)
    survives_elapsed = _survival(elapsed, location, scale)
    # `F(e) + u(1 − F(e))` rearranged: the tail probability is the small number
    # here, and forming it as `1 − F(e)` would round it away before it is used.
    conditioned = np.minimum(1.0 - survives_elapsed * (1.0 - drawn), _LARGEST_QUANTILE)
    total = np.exp(location + scale * _inv_cdf(conditioned))
    remaining = total - elapsed

    # The cap above is a precision guard, and past a certain elapsed time it stops
    # being one. Once `S(e)` underflows, `1 − S(e)(1 − u)` rounds to exactly 1 for
    # every `u`, the cap pins `F*` to one value, and `T` becomes a fixed ceiling
    # independent of `e` — so `T − e` goes *negative*, unboundedly. Measured at the
    # fit's own scale: every draw is negative from about 5,000 elapsed days, and
    # −15,610 at 20,000. An earlier revision of the comment above called the capped
    # result "finite and enormous, which is the honest answer for a line open that
    # long"; it is neither enormous nor honest, it is a wrong sign.
    #
    # Refusing rather than clamping follows `likelihood.py`, which raises on a
    # completed row at `t = 0` instead of returning `-inf`: a value that survives
    # into the artifact resurfaces far from its cause. Here it would surface as
    # `ck_line_posterior__draws_non_negative` rejecting a write, naming a
    # constraint rather than the elapsed time that broke it.
    if remaining.size and float(np.min(remaining)) <= 0.0:
        worst = float(np.min(elapsed[remaining <= 0.0])) if elapsed.size else float("nan")
        raise PosteriorError(
            "the conditional remaining duration is not representable at "
            f"elapsed_days={worst:g} for these parameters: the survival function "
            "has underflowed, so every draw collapses to one capped quantile and "
            "the remaining duration comes out non-positive. A draw must be "
            "strictly positive -- re-basing is what produces a point mass at "
            "zero, and this would produce worse."
        )

    return np.sort(remaining)


def survival_grid(draws: ArrayLike, horizon_days: int) -> SurvivalGrid:
    """`survival[k] = count(draws > k)/draw_count` for `k = 1..horizon_days`.

    Strictly `>`, so a draw landing exactly on day `k` has delivered by the end
    of that day. The array is indexed from day one and stores no `S(0)`: every
    draw exceeds zero, so a zeroth element would open at 1.0 on every line and
    invite the reading that the grid starts before the forecast does.

    `residual_tail_mass` is `count(draws > horizon_days)/draw_count` again, but
    counted by binary search over the order statistics rather than read off the
    grid's last element — DV-003 asks for agreement between two paths, and a
    residual copied from the tail would restate one expression instead.
    """
    values = np.asarray(draws, dtype=float)

    if values.ndim != 1:
        raise PosteriorError(
            f"the draw set is one vector per line, found {values.ndim} dimensions; a frame "
            f"of several lines would silently produce one grid pooled across all of them"
        )
    if values.size == 0:
        raise PosteriorError(
            "the survival grid was asked for over zero draws; `count/0` is not a "
            "probability, and NumPy would return a NaN array that fails "
            "`ck_line_posterior__survival_unit_interval` a whole sampling run later"
        )
    if not np.all(np.isfinite(values)):
        raise PosteriorError(
            "every draw must be finite; a NaN compares false against every threshold and "
            "would be counted as delivered on day one without any error being raised"
        )
    if isinstance(horizon_days, bool) or not isinstance(horizon_days, int | np.integer):
        raise PosteriorError(
            f"the horizon is a whole number of days, found {type(horizon_days).__name__}; a "
            f"fractional horizon has no `k` to index and no length to check against"
        )
    if horizon_days <= 0:
        raise PosteriorError(
            f"the horizon must be at least one day, found {horizon_days}; a zero-length grid "
            f"is not a shorter grid — it satisfies `ck_line_posterior__survival_length` and "
            f"expresses nothing at all"
        )

    total = values.size
    days = np.arange(1, int(horizon_days) + 1, dtype=float)
    survival = np.count_nonzero(values[:, None] > days, axis=0) / total

    # The second path. `searchsorted(..., "right")` lands after every value equal
    # to the horizon, so `total −` it is the same strict `>` the grid took, by an
    # algorithm that shares no expression with the comparison above.
    ordered = np.sort(values)
    beyond = total - int(np.searchsorted(ordered, float(horizon_days), side="right"))

    return SurvivalGrid(survival=survival, residual_tail_mass=beyond / total)
