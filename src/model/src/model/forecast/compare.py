"""The reproduction oracle: a nearest-rank lookup and an absolute day tolerance.

FR-022, SC-018, DV-018. Extracted out of `reproduce.py` — which is I/O-bound and
so unreachable by the property tier — because these two functions are what the
epic's entire reproducibility claim rests on and an off-by-one in either yields a
plausible day value no stored constraint rejects (`plan.md` § What qualifies).

The lookup implements the **delivered** convention rather than a second one:
`schema_constants.percentile_convention` is pinned to
`nearest_rank_one_based_no_interpolation` by `ck_schema_constants__percentile_
convention`, so the rank is `ceil(p·n)` read as **1-indexed** and the answer is
always a draw the sampler produced, never the midpoint of two it did not. The
tolerance is absolute and inclusive, and the claim it forms is quantified over
every per-line delta: an aggregate can sit inside the bar while individual lines
move in compensating directions, which is the reading FR-022 exists to exclude.
"""

from __future__ import annotations

import math

import numpy as np
from numpy.typing import ArrayLike

__all__ = [
    "MEDIAN_PROBABILITY",
    "P80_PROBABILITY",
    "PERCENTILE_CONVENTION",
    "CompareError",
    "nearest_rank_percentile",
    "within_tolerance",
]


class CompareError(ValueError):
    """Raised when a comparison cannot be formed from the values given.

    A `ValueError`, following `PosteriorError` and `LikelihoodError`: every case
    is an argument — an empty draw set, a probability outside `(0, 1]`, a
    tolerance that is not a published bar, a claim quantified over nothing.
    A *breach* is never an error here; it is a `False`, which is a measurement.
    """


#: The label `schema_constants.percentile_convention` carries, restated here
#: because this module is the implementation of it. Two properties in one name:
#: the rank is 1-indexed, and no value between two order statistics is ever
#: returned. A module implementing a lookup under another name would be a second
#: convention beside the one `/src/api` serves readers from.
PERCENTILE_CONVENTION = "nearest_rank_one_based_no_interpolation"

#: The two quantities FR-022 compares per line. Named rather than passed at each
#: call site, so "median and 80th percentile" is one fact in this package and a
#: caller cannot compare a P75 while reporting a P80.
MEDIAN_PROBABILITY = 0.5
P80_PROBABILITY = 0.8


def _finite_draws(draws: ArrayLike) -> np.ndarray:
    """One line's draw vector, proved to be one before a rank indexes it.

    One dimension, non-empty, every value finite. A frame of several lines would
    silently pool them into one percentile belonging to no row, and a NaN sorts
    unpredictably — so it would decide which order statistic came back without
    any error being raised.
    """
    values = np.asarray(draws, dtype=float)
    if values.ndim != 1:
        raise CompareError(
            f"a percentile is taken over one line's draw vector, found {values.ndim} "
            f"dimensions; a frame of several lines would pool them into one figure that "
            f"belongs to no stored row"
        )
    if values.size == 0:
        raise CompareError(
            "the percentile was asked for over zero draws; there is no order statistic to "
            "return and no rank that names one"
        )
    if not np.all(np.isfinite(values)):
        raise CompareError(
            "every draw must be finite before a percentile is taken over it; a NaN sorts "
            "unpredictably and would decide which order statistic is returned without any "
            "error being raised"
        )
    return values


def nearest_rank_percentile(draws: ArrayLike, probability: float) -> float:
    """`ordered[ceil(p·n) − 1]` — the rank is 1-indexed (`PERCENTILE_CONVENTION`).

    Sorted here rather than assumed of the caller. Both stores hold ascending
    arrays and both are checked to, but a lookup that indexed whatever order it
    was handed would be a convention enforced by its callers, and a row read back
    in another order would move a published percentile.

    The subtraction is the whole of the delicacy. `ceil(p·n)` is a *rank*, so a
    0-based subscript is one element too far — and at `p·n` exactly integral, the
    case both reported quantiles hit at the committed 4,000 draws, it is exactly
    one order statistic out and otherwise indistinguishable from a correct day.
    """
    if not isinstance(probability, float | int) or isinstance(probability, bool):
        raise CompareError(
            f"a probability is a real number, found {type(probability).__name__}; the rank "
            f"is `ceil(p·n)` and a non-numeric `p` has no rank"
        )
    if not math.isfinite(probability) or not 0.0 < probability <= 1.0:
        raise CompareError(
            f"the probability must lie in `(0, 1]`, found {probability!r}; `ceil(0·n)` is a "
            f"rank of zero, which a 1-indexed convention has no element for, and a `p` above "
            f"one names a rank past the last draw"
        )
    ordered = np.sort(_finite_draws(draws))
    # `max(..., 1)` covers only the case a probability below `1/n` produces —
    # a rank of zero is already refused above, and rounding cannot reach it.
    rank = max(math.ceil(float(probability) * ordered.size), 1)
    return float(ordered[rank - 1])


def within_tolerance(delta_days: ArrayLike, tolerance_days: float) -> bool:
    """`True` exactly when **every** delta sits within the absolute day tolerance.

    One verdict about the whole population rather than an array, so a caller
    writing `if within_tolerance(...)` gets an answer instead of NumPy's
    ambiguous-truth error. Quantified over every element because FR-022's claim
    is per line: an aggregate delta can sit inside the bar while individual lines
    move in compensating directions.

    Inclusive at the bar, since the requirement publishes agreement *within* a
    tolerance. A non-finite delta is never within one — a comparison that did not
    produce a number did not produce a pass — and it is returned as a `False`
    rather than raised, because a breach is a measurement.
    """
    if isinstance(tolerance_days, bool) or not isinstance(tolerance_days, float | int):
        raise CompareError(
            f"the tolerance is a published number of days, found "
            f"{type(tolerance_days).__name__}"
        )
    if not math.isfinite(tolerance_days) or tolerance_days < 0.0:
        raise CompareError(
            f"the tolerance must be finite and non-negative, found {tolerance_days!r}; a "
            f"negative bar admits nothing and an infinite one admits everything, and "
            f"neither is a bar a reproduction claim could be judged against"
        )
    values = np.atleast_1d(np.asarray(delta_days, dtype=float)).ravel()
    if values.size == 0:
        raise CompareError(
            "the tolerance claim was quantified over zero comparisons; FR-022's population "
            "is every stored line in both stores, and a harness that paired none of them "
            "would otherwise publish agreement having compared nothing"
        )
    # `np.abs` of a NaN is a NaN and every comparison against one is false, so
    # the non-measurable case falls out of the arithmetic rather than needing a
    # branch — the negated form (`> tolerance` inverted) would turn it into a pass.
    return bool(np.all(np.abs(values) <= float(tolerance_days)))
