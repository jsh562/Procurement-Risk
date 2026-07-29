"""The per-row log-contribution: a density if completed, a survival if censored.

FR-003, and the epic's risk arithmetic in the Testing Policy's own sense. A
censored line written as a density at its censoring time **builds cleanly and
yields a plausible posterior** — the spec says so in US2's *Why P1* — so this is
extracted as a pure NumPy function and property-tested directly, with the PyMC
graph's `logp` asserted to agree with it.

The survival is the closed form `½·erfc(z/√2)`, never `1 − Φ(z)`: the
subtraction loses every significant digit in the upper tail, which is the only
region a censored contribution differs interestingly from zero. A duration of
zero is refused for a completed row rather than returned as `-inf`, because
`-inf` sums into the log-likelihood and resurfaces as an initialisation failure
the sampler reports far from its cause.
"""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import ArrayLike, NDArray

__all__ = ["LikelihoodError", "log_contribution"]


class LikelihoodError(ValueError):
    """Raised when a row's inputs place it outside the family's support.

    A `ValueError`: the values were handed in. Named as its own type so a
    caller can distinguish "this row is not fittable" from a NumPy shape error,
    and a `ValueError` subclass so the refusal is catchable either way.
    """


#: `math.erfc` lifted over arrays. The stdlib scalar is used rather than
#: `scipy.special.erfc` on purpose: SciPy is only a transitive dependency of
#: PyMC here, and this epic adds none. The vectorization is a Python-level loop
#: and so is not free, but this module is the *reference* implementation — the
#: sampler runs on PyTensor's own erfc — and the frames it sees are one per
#: line, not one per draw.
_erfc = np.vectorize(math.erfc, otypes=[float])

_LOG_TWO_PI = math.log(2.0 * math.pi)
_SQRT_TWO = math.sqrt(2.0)


def _positive_scale(sigma: ArrayLike) -> NDArray[np.float64]:
    """The log-scale, proved positive. A σ of zero divides; a negative one lies."""
    scale = np.asarray(sigma, dtype=float)
    if not np.all(np.isfinite(scale)) or np.any(scale <= 0.0):
        raise LikelihoodError(
            "the lognormal log-scale must be finite and strictly positive; a σ of zero "
            "divides by zero in the standardization and a negative one silently reverses "
            "the tail the survival is taken over"
        )
    return scale


def log_contribution(
    duration_days: ArrayLike, mu: ArrayLike, sigma: ArrayLike, is_censored: ArrayLike
) -> NDArray[np.float64]:
    """`log S(t)` for a censored row and `log f(t)` for a completed one.

    Vectorized over the frame, with the indicator broadcast against the
    durations and read row by row — a mask of the wrong shape is a defect
    `np.where` would otherwise absorb by recycling it. `S(0) = 1` exactly, so a
    line censored on the day it was ordered contributes zero rather than a small
    negative number that would penalise it for existing; `f(0)` has no value at
    all and is refused.
    """
    durations = np.asarray(duration_days, dtype=float)
    censored = np.asarray(is_censored, dtype=bool)
    location = np.asarray(mu, dtype=float)
    scale = _positive_scale(sigma)

    if not np.all(np.isfinite(durations)):
        raise LikelihoodError(
            "a duration must be finite; NaN and infinity are not elapsed times, and both "
            "would propagate silently through the log-likelihood as a single NaN"
        )
    if np.any(durations < 0.0):
        raise LikelihoodError(
            "a negative duration was passed; elapsed time before the line was ordered is a "
            "caller defect rather than a value, and the lognormal has no support there"
        )
    if np.any((durations == 0.0) & ~censored):
        raise LikelihoodError(
            "a completed row was passed a duration of zero. A lognormal has no support at "
            "zero, so the honest answer is `-inf` — which sums into the log-likelihood and "
            "resurfaces as an initialisation failure far from its cause. Refused instead, "
            "so the row that caused it is named (Principle III)"
        )

    # Only censored rows may sit at zero, and their contribution is fixed below,
    # so the substitute keeps `log` and the division out of the invalid domain
    # without changing any value that is actually used.
    safe = np.where(durations > 0.0, durations, 1.0)
    log_t = np.log(safe)
    z = (log_t - location) / scale

    log_density = -log_t - np.log(scale) - 0.5 * _LOG_TWO_PI - 0.5 * z * z
    log_survival = np.log(0.5 * _erfc(z / _SQRT_TWO))
    # `S(0) = 1` exactly: no mass has been consumed, so `log S(0)` is zero and
    # not the value the standardization above would give at the substitute.
    log_survival = np.where(durations > 0.0, log_survival, 0.0)

    return np.asarray(np.where(censored, log_survival, log_density), dtype=float)
