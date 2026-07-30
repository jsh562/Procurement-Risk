"""Recall, mean reciprocal rank, their intervals, and the unresolvable verdict.

Spec FR-030, FR-031, FR-032, FR-042. Principle II: every figure is published
with its interval, or with an explicit declaration that it has none and why.

**The two statistics take different interval methods, and that is not a style
choice.** Recall@5 is a proportion — each query either has a relevant chunk in
its top five or does not — so a Wilson interval is admissible: it inverts a
score test on a binomial trial count. Mean reciprocal rank averages continuous
per-query values and has no trial count to invert; a Wilson interval on it
would publish bounds belonging to a different quantity. `specs/prd.md` and
`specs/sad.md` both specified Wilson on MRR until `c422e24` corrected them, and
FR-031 is what keeps the correction from drifting back.

**Every emitted interval records the method that produced it.** A prohibition
with no observable is satisfied by every implementation that simply never calls
the forbidden function, and no test can fail — so the record is made over the
emitted artifact rather than over intent.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

import numpy as np

__all__ = [
    "BOOTSTRAP_BIT_GENERATOR",
    "BOOTSTRAP_RESAMPLES",
    "IntervalMethod",
    "IntervalRecord",
    "MetricsError",
    "mean_reciprocal_rank",
    "overlap_verdict",
    "percentile_bootstrap",
    "recall_at_k",
    "wilson_interval",
]

#: Fixed by `specs/sad.md`, not chosen here. Reading it from the registered
#: document rather than picking a round number is what makes the figure
#: reproducible by anyone holding the same record.
BOOTSTRAP_RESAMPLES: Final = 10_000

#: Pinned alongside the seed, and for a reason a seed alone does not cover: a
#: seed fixes a draw *within* a bit stream, not which bit stream. NumPy's
#: default generator has changed before, so an interval could move on a
#: dependency bump and read as reproducibility drift with no cause to find.
BOOTSTRAP_BIT_GENERATOR: Final = "PCG64"

#: The two-sided 95% normal quantile. Written out rather than imported so this
#: module needs no distribution library for one constant.
_Z_95: Final = 1.959963984540054


class MetricsError(ValueError):
    """A metric cannot be computed from what was supplied.

    One type for every failure, because the caller learns the same thing from
    each: no figure may be published for this run.
    """


class IntervalMethod(StrEnum):
    """How an interval was produced.

    A closed vocabulary carried *on the figure*, so FR-031's prohibition is
    checkable by reading emitted output rather than by auditing call sites.
    """

    WILSON = "wilson"
    PERCENTILE_BOOTSTRAP = "percentile_bootstrap"


@dataclass(frozen=True)
class IntervalRecord:
    """The provenance of one interval.

    `resamples`, `seed` and `bit_generator` are `None` for Wilson, which is
    analytic and draws nothing — the fields exist because the *bootstrap* needs
    them, and a record shaped differently per method would make two figures
    incomparable.

    The field is `method`, not `name`: `IntervalMethod` is a `StrEnum` and so
    already carries a `.name`, which made `record.name.name` resolve to the
    member identifier rather than the emitted value. That is a comparison that
    looks right and is not, which is the whole failure class this record exists
    to close.
    """

    method: IntervalMethod
    resamples: int | None = None
    seed: int | None = None
    bit_generator: str | None = None


def _require_non_empty(values: Sequence[object], what: str) -> None:
    if len(values) == 0:
        msg = (
            f"cannot compute {what} over an empty query set. A proportion over zero "
            f"queries is undefined, not zero — reporting 0.0 would publish a figure "
            f"for a run that measured nothing."
        )
        raise MetricsError(msg)


def recall_at_k(outcomes: Sequence[bool]) -> float:
    """The proportion of queries with a relevant chunk in the top *k*.

    `outcomes` is one boolean per query. The population is the query set, which
    is what makes this a proportion and Wilson admissible on it.
    """
    _require_non_empty(outcomes, "recall")
    return sum(1 for hit in outcomes if hit) / len(outcomes)


def wilson_interval(
    outcomes: Sequence[bool],
    *,
    with_method: bool = False,
) -> tuple[float, float] | tuple[float, float, IntervalRecord]:
    """The Wilson score interval for the proportion in `outcomes`.

    Wilson rather than the naive normal interval, and the difference is
    observable at the boundaries: the normal interval puts the lower bound of an
    all-miss set below zero and the upper bound of an all-hit set above one,
    publishing impossible values for a proportion. Wilson is bounded in [0, 1]
    by construction because it inverts the score test rather than adding a
    symmetric margin to the estimate.
    """
    _require_non_empty(outcomes, "a recall interval")
    n = len(outcomes)
    p = recall_at_k(outcomes)
    z = _Z_95
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    margin = (z / denominator) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    lower = max(0.0, centre - margin)
    upper = min(1.0, centre + margin)
    # Wilson provably contains the point estimate, and at p = 0 or p = 1 the
    # algebra gives exactly 0 or 1 while the arithmetic gives 0.9999999999999999.
    # Clamping to the estimate restores the property the interval has by
    # construction rather than papering over a wrong bound: the containment is
    # not an approximation, so a bound outside it is float error and nothing else.
    lower = min(lower, p)
    upper = max(upper, p)
    if with_method:
        return lower, upper, IntervalRecord(method=IntervalMethod.WILSON)
    return lower, upper


def mean_reciprocal_rank(reciprocals: Sequence[float]) -> float:
    """The mean of per-query reciprocal ranks.

    A zero is a real outcome — no relevant chunk was retrieved for that query —
    and not a missing value. Dropping zeros would compute the mean over the
    queries that succeeded, which is a different and flattering statistic.
    """
    _require_non_empty(reciprocals, "mean reciprocal rank")
    return sum(reciprocals) / len(reciprocals)


def percentile_bootstrap(
    reciprocals: Sequence[float],
    *,
    seed: int,
    resamples: int = BOOTSTRAP_RESAMPLES,
    with_method: bool = False,
) -> tuple[float, float] | tuple[float, float, IntervalRecord]:
    """A percentile bootstrap interval for the mean of `reciprocals`.

    Resamples queries with replacement and takes the 2.5th and 97.5th
    percentiles of the resampled means. No distributional assumption is made,
    which is the point: the sampling distribution of a mean of bounded,
    heavily-tied values is not normal at these set sizes, and a normal interval
    would be narrower than the data support.

    The generator is constructed explicitly from `BOOTSTRAP_BIT_GENERATOR` and
    the seed rather than from `numpy.random.default_rng`, so the bit stream is
    pinned rather than inherited from whatever NumPy currently defaults to.
    """
    _require_non_empty(reciprocals, "a mean reciprocal rank interval")
    if seed < 0:
        msg = f"the bootstrap seed must be non-negative, found {seed}"
        raise MetricsError(msg)
    values = np.asarray(reciprocals, dtype=np.float64)
    generator = np.random.Generator(np.random.PCG64(seed))
    draws = generator.integers(0, len(values), size=(resamples, len(values)))
    means = values[draws].mean(axis=1)
    lower = float(np.percentile(means, 2.5))
    upper = float(np.percentile(means, 97.5))
    if with_method:
        return (
            lower,
            upper,
            IntervalRecord(
                method=IntervalMethod.PERCENTILE_BOOTSTRAP,
                resamples=resamples,
                seed=seed,
                bit_generator=BOOTSTRAP_BIT_GENERATOR,
            ),
        )
    return lower, upper


def overlap_verdict(a: tuple[float, float], b: tuple[float, float]) -> bool:
    """Whether two intervals separate the arms they describe.

    Returns `True` when the comparison is **resolvable** — the intervals are
    disjoint — and `False` when it is not.

    Closed intervals, so intervals touching at a single point are *not*
    resolvable. Declaring a winner on one shared endpoint is the overclaim this
    verdict exists to prevent, and the conservative reading is the one FR-032
    fixes.
    """
    for name, interval in (("a", a), ("b", b)):
        lower, upper = interval
        if not (math.isfinite(lower) and math.isfinite(upper)):
            msg = (
                f"interval {name} is not finite ({interval}). NaN compares false "
                f"against everything, so an interval carrying one would be declared "
                f"separable from every other — a wrong verdict that looks confident."
            )
            raise MetricsError(msg)
        if lower > upper:
            msg = f"interval {name} has a lower bound above its upper bound: {interval}"
            raise MetricsError(msg)
    return a[1] < b[0] or b[1] < a[0]
