"""The censoring ablation's two published numbers: the floor, and the delta.

FR-033 and AD-008. SC-008 compares a censoring-ignoring fit's aggregate median
against a floor, and the whole force of the comparison is that the floor comes
from somewhere else: **a non-parametric survival estimate on the training split,
computed before and independently of the fit**. A floor back-solved from the
fitted model would compare a measurement against a derivation of the same
quantity, which is near-tautological and is exactly what FR-033 forbids.

So this module reaches neither the sampler, the graph, nor AD-001's lognormal
family — `likelihood.py` included, because that family *is* the fit's own
assumption and a floor derived from it would be the assumption returning as its
own bar. The prohibition is asserted over this file's imports rather than
promised here (`test_ablation_properties.py`).

**Both published quantities are relative shortenings of the censoring-aware
figure**, so they share a scale and a direction and SC-008's comparison is a
comparison: `(km_median − naive_mean) / km_median` for the floor, and
`(aware − ignoring) / aware` per seed for the delta. The one measured analogue
available reads the same way — 58.0 against a delivered-only 53.0 is a gap of
8.6% **of 58.0**.

Both carry an interval, for the reason Principle II gives and M-2 already closed
once for `vendor_shrinkage`: the Kaplan–Meier median is an estimate off a few
hundred lines, and the delta is one realization of a sampler, so a bare number
for either is a claim of exactness neither supports.

The seed loop that *produces* the per-seed medians is not here — it has to fit,
and fitting is the one thing this module may not reach. It lives in `fit.py`
(`censoring_ablation`), which hands the results back to `realized_delta`.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date
from statistics import NormalDist

from model.forecast.censoring import censoring_indicator, elapsed_days, terminal_event
from model.forecast.read import LifecycleEventRow, LineRow
from model.forecast.split import TRAIN, SplitResult

__all__ = [
    "FLOOR_INTERVAL_PROBABILITY",
    "MINIMUM_ABLATION_SEEDS",
    "AblationError",
    "KaplanMeierFloor",
    "RealizedDelta",
    "SeedResult",
    "kaplan_meier_floor",
    "realized_delta",
]


class AblationError(ValueError):
    """Raised when neither published quantity can be derived from what was passed.

    A `ValueError`: every case is something the caller handed over — a line with
    no split assignment, a training set in which nothing has completed, a
    survival curve that never reaches one half, or a seed set that is one seed
    wearing two labels. Named as its own type so a refusal is distinguishable
    from an arithmetic error, and a `ValueError` subclass so it is catchable
    either way.
    """


#: The mass the floor's interval carries. The same 0.94 `vendor_shrinkage`
#: publishes its HPDI at, stated here rather than imported from `manifest.py`:
#: the floor is computed **before and independently of the fit**, and reaching
#: into the module that assembles the fitted run's own record for a constant
#: would make that ordering a convention rather than a structural fact. Stated
#: rather than defaulted, because "wider" is undefined between two intervals of
#: different mass (SC-005's reason, applied here).
FLOOR_INTERVAL_PROBABILITY = 0.94

#: Two seeds is the smallest set with a spread to measure. FR-033 requires the
#: delta to carry an interval **over repeated seeds**, and one seed has no
#: disagreement to report — an interval taken off it would be a degenerate band
#: presented as evidence of stability.
MINIMUM_ABLATION_SEEDS = 2

#: The product-limit convention: the median is the first event time at which the
#: curve has fallen to one half or below. Named rather than inlined, because a
#: `<` here and a `<=` there are two different estimators that agree on most
#: inputs.
_HALF = 0.5


# ---------------------------------------------------------------------------
# What the estimator reads of a line
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Observation:
    """One training line reduced to the two facts the product-limit curve reads.

    `days` is the completed duration for a line delivered at the anchor and the
    censoring time for one still open — the two quantities `censoring.py` keeps
    distinct, consumed on the same axis, which is the whole of what makes a
    Kaplan–Meier estimate use a censored row rather than discard it.
    """

    days: int
    censored: bool


@dataclass(frozen=True, slots=True)
class _Step:
    """One step of the product-limit curve, with Greenwood's standard error.

    `standard_error` is `math.inf` where the risk set is exhausted by its own
    events — Greenwood's term divides by `n_i − d_i` — which is the honest
    reading rather than a number: past the last observation the curve's
    uncertainty is unbounded, and the interval below treats that time as
    admissible instead of pretending it is precise.
    """

    time: int
    survival: float
    standard_error: float


@dataclass(frozen=True, slots=True)
class KaplanMeierFloor:
    """The floor SC-008 is judged against, published with its own derivation.

    Both operands are on the record beside the floor, so a reader recomputes
    `(kaplan_meier_median − naive_completed_mean) / kaplan_meier_median` from
    the record alone — which is what stops the number from being an assertion.
    `training_line_count` publishes the "alone" in "the training split alone":
    a reader checking whether the floor came off 148 lines or 199 has no other
    way to tell, and the difference between those two numbers is the whole
    content of FR-007.
    """

    floor: float
    interval_low: float
    interval_high: float
    kaplan_meier_median: float
    naive_completed_mean: float
    training_line_count: int


@dataclass(frozen=True, slots=True)
class SeedResult:
    """One repeated seed's pair of aggregate medians.

    The censoring-aware fit's and the censoring-ignoring fit's, from the same
    seed — paired, because the delta is a within-seed difference and comparing
    an aware median from one seed against an ignoring median from another would
    measure sampler noise and call it censoring bias.
    """

    seed: int
    censoring_aware_median: float
    censoring_ignoring_median: float


@dataclass(frozen=True, slots=True)
class RealizedDelta:
    """The delta, its interval, and the seeds both were measured over.

    One object rather than three returns, for the reason `SplitResult` gives: a
    caller able to obtain the delta without the interval and the seed set would
    eventually publish it without them, and a single-seed pass wearing an
    interval is the specific failure FR-033 names.

    `seeds` and `per_seed_deltas` are aligned and both ascending by seed, so the
    reader who wants to check the summary can, rather than take it.
    """

    delta: float
    interval_low: float
    interval_high: float
    seeds: tuple[int, ...]
    per_seed_deltas: tuple[float, ...]


# ---------------------------------------------------------------------------
# The training split, and the observations it yields
# ---------------------------------------------------------------------------


def _occurred_on(event: LifecycleEventRow) -> date:
    """The calendar day an event happened on, in UTC — `censoring.py`'s rule.

    Written out rather than imported because it is private there, and taking
    `.date()` of an aware instant in some other zone would move the boundary by
    a day; `model.py` carries the same three lines for the same reason.
    """
    moment = event.occurred_at
    if moment.tzinfo is not None and moment.utcoffset() is not None:
        return moment.astimezone(UTC).date()
    return moment.date()


def _observation(line: LineRow, as_of_date: date) -> _Observation:
    """One line's contribution: a completed duration, or a censoring time.

    The censoring question is `censoring.py`'s dated one and never the loader's
    `is_closed` column, so a line whose terminal event has not happened at the
    anchor contributes its elapsed time rather than a duration from the future.
    Both readings are computable, which is what makes the wrong one silent: it
    yields a longer naive mean and a perfectly plausible floor.
    """
    if censoring_indicator(line, as_of_date):
        days = elapsed_days(line, as_of_date)
    else:
        terminal = terminal_event(line)
        if terminal is None:  # pragma: no cover - `censoring_indicator` excludes it
            raise AblationError(
                f"line {line.natural_key} reads as delivered at {as_of_date} and carries no "
                f"terminal event, so there is no date to measure its duration to"
            )
        days = (_occurred_on(terminal) - line.order_date).days
    if days < 0:
        raise AblationError(
            f"line {line.natural_key} yields an observation of {days} days at {as_of_date}; a "
            f"line ordered after the anchor, or one whose terminal event precedes its own "
            f"order date, has no duration for the survival curve to step at"
        )
    return _Observation(days=days, censored=censoring_indicator(line, as_of_date))


def _training_observations(
    lines: Iterable[LineRow], split: SplitResult, as_of_date: date
) -> tuple[tuple[_Observation, ...], int]:
    """The `train` side's observations, and how many lines produced them.

    The filter is the split, exactly as `training_frame` makes it — structural
    rather than conventional. That is why this function takes the **whole**
    cohort: handed pre-filtered rows it would be invariant to a held-out line by
    having never seen one, and "computed from the training split alone" would be
    a claim about its caller instead of about it.

    An unassigned line is refused rather than treated as training data, for the
    reason `training_frame` refuses one: FR-007 is a claim about every line, and
    a floor derived from a set nobody declared is not derived from the training
    split at all.
    """
    sides = {assignment.po_line_id: assignment.split_side for assignment in split.assignments}
    rows = tuple(lines)
    unassigned = [line.natural_key for line in rows if line.po_line_id not in sides]
    if unassigned:
        raise AblationError(
            f"{len(unassigned)} line(s) carry no split assignment — first {unassigned[0]}. The "
            f"floor is derived from the training split alone (FR-033), so a line with no side "
            f"cannot be admitted to it on the assumption that it is training data"
        )
    training = tuple(line for line in rows if sides[line.po_line_id] == TRAIN)
    if not training:
        raise AblationError(
            "the split assigned no line to the training side, so there is no cohort for the "
            "product-limit curve to be estimated over and no completed duration to average"
        )
    return tuple(_observation(line, as_of_date) for line in training), len(training)


# ---------------------------------------------------------------------------
# The product-limit estimator (AD-008)
# ---------------------------------------------------------------------------


def _kaplan_meier_steps(observations: Sequence[_Observation]) -> tuple[_Step, ...]:
    """`(t, S(t), se(t))` at every distinct completed duration, ascending.

    The textbook product limit: at each event time the risk set counts every
    observation still under study — **a row censored at an event time is at risk
    for that event**, the standard convention — and the curve is multiplied by
    `1 − d_i/n_i`. Nothing here assumes a family, which is the property AD-008
    chose Kaplan–Meier for: the floor is then the *input's* censoring bias
    rather than the model's.

    Greenwood's variance is accumulated alongside, because the median read off
    this curve is an estimate and the interval below is what says so.
    """
    survival = 1.0
    variance_sum = 0.0
    steps: list[_Step] = []
    for time in sorted({row.days for row in observations if not row.censored}):
        at_risk = sum(1 for row in observations if row.days >= time)
        events = sum(1 for row in observations if row.days == time and not row.censored)
        survival *= 1.0 - events / at_risk
        remaining = at_risk - events
        # The risk set exhausted by its own events leaves Greenwood's term
        # undefined; recorded as an unbounded standard error rather than as a
        # number, which is what it is.
        variance_sum = math.inf if remaining == 0 else variance_sum + events / (at_risk * remaining)
        steps.append(
            _Step(
                time=time,
                survival=survival,
                standard_error=(
                    math.inf if math.isinf(variance_sum) else survival * math.sqrt(variance_sum)
                ),
            )
        )
    return tuple(steps)


def _median_band(steps: Sequence[_Step], median: float) -> tuple[float, float]:
    """The event times consistent with a median, by the Brookmeyer–Crowley rule.

    The interval is the set of event times whose survival sits within a normal
    band of one half — `|S(t) − ½| ≤ z·se(t)` — read back onto the time axis.
    That is the standard construction for a Kaplan–Meier median's interval, and
    it is what makes the floor's interval the *estimate's* uncertainty rather
    than a nominal width nobody measured.

    The point estimate is included by construction. A band that excluded it
    would publish an interval the estimate beside it contradicts, and the
    inclusion also keeps both bounds strictly positive whenever the median is —
    which the floor's own ratio needs.
    """
    z = NormalDist().inv_cdf(0.5 + FLOOR_INTERVAL_PROBABILITY / 2.0)
    # A zero-day event time is excluded: the floor is a ratio *against* the
    # median, so a bound of zero days is not a bound the derivation is defined
    # at. A median of zero is refused outright by `kaplan_meier_floor`.
    admissible = [
        float(step.time)
        for step in steps
        if step.time > 0 and abs(step.survival - _HALF) <= z * step.standard_error
    ]
    return min([median, *admissible]), max([median, *admissible])


def kaplan_meier_floor(
    lines: Iterable[LineRow], split: SplitResult, as_of_date: date
) -> KaplanMeierFloor:
    """SC-008's floor: the Kaplan–Meier median against the naive completed mean.

    Two estimates of the same thing over the same training rows. One accounts
    for the lines still open at the anchor; the other averages the deliveries
    that have happened and discards the rest, which is what a reader gets by
    reading the completed durations off a report. **The gap between them is the
    input's own censoring bias**, expressed as a fraction of the censoring-aware
    figure, and that is the margin a fit which omits the censoring contribution
    should at least give up.

    There is no fitted quantity in the signature and none reachable from this
    module, so "computed before and independently of the fit" is structural
    rather than an ordering convention (AD-008).

    Refused rather than answered when the training set carries no completed
    line — neither operand exists — or when the curve never falls to one half,
    which is the general case the all-censored boundary is one instance of. A
    floor derived from no observation is not derived from anything, and
    Principle III records an unobtainable value as absent rather than storing it
    wrong.
    """
    observations, training_line_count = _training_observations(lines, split, as_of_date)

    completed = [row.days for row in observations if not row.censored]
    if not completed:
        raise AblationError(
            f"no line on the training side has completed at {as_of_date}, so there is no "
            f"completed-duration mean to take and no event time for the product-limit curve "
            f"to step at. The floor is refused rather than substituted: a floor derived from "
            f"no observation is not derived from anything"
        )
    naive_completed_mean = sum(completed) / len(completed)

    steps = _kaplan_meier_steps(observations)
    crossing = next((step for step in steps if step.survival <= _HALF), None)
    if crossing is None:
        raise AblationError(
            f"the training split's survival estimate never reaches one half — it ends at "
            f"{steps[-1].survival:.4f} after {len(observations)} line(s) — so no Kaplan–Meier "
            f"median exists. The naive mean is computable here and the floor still is not: "
            f"one operand in hand is not a derivation"
        )
    kaplan_meier_median = float(crossing.time)
    if kaplan_meier_median <= 0.0:
        raise AblationError(
            "the training split's Kaplan–Meier median is zero days, so the floor's ratio "
            "against it is undefined; a cohort whose majority delivers on its own order date "
            "carries no censoring bias to measure"
        )

    band_low, band_high = _median_band(steps, kaplan_meier_median)
    return KaplanMeierFloor(
        floor=(kaplan_meier_median - naive_completed_mean) / kaplan_meier_median,
        # The floor rises with the censoring-aware operand, so the lower bound of
        # the median maps to the lower bound of the floor.
        interval_low=(band_low - naive_completed_mean) / band_low,
        interval_high=(band_high - naive_completed_mean) / band_high,
        kaplan_meier_median=kaplan_meier_median,
        naive_completed_mean=naive_completed_mean,
        training_line_count=training_line_count,
    )


# ---------------------------------------------------------------------------
# The realized delta over repeated seeds (FR-033's second half)
# ---------------------------------------------------------------------------


def _checked_results(results: Sequence[SeedResult]) -> tuple[SeedResult, ...]:
    """The seed set, before a single delta is taken.

    Two refusals rather than one, because they close the same hole from two
    sides. Fewer than two seeds has no spread to measure. **Two results at one
    seed is the same thing wearing a second label** — the route by which a
    single-seed pass satisfies a count and publishes an interval that looks
    measured — so it is refused as loudly as the single seed it actually is.
    """
    ordered = tuple(sorted(results, key=lambda result: result.seed))
    if len(ordered) < MINIMUM_ABLATION_SEEDS:
        raise AblationError(
            f"the realized delta was given {len(ordered)} seed result(s); FR-033 requires an "
            f"interval over repeated seeds and at least {MINIMUM_ABLATION_SEEDS} are needed "
            f"for there to be any disagreement to report. A band taken off one seed is a "
            f"degenerate interval presented as evidence of stability"
        )
    seeds = [result.seed for result in ordered]
    repeated = sorted({seed for seed in seeds if seeds.count(seed) > 1})
    if repeated:
        raise AblationError(
            f"seed(s) {repeated} appear more than once. Two results at one seed are one "
            f"seed's outcome under two labels: the count is satisfied, the spread measures "
            f"nothing, and the interval looks measured. That is the single-seed pass FR-033 "
            f"names, reached by another route"
        )
    for result in ordered:
        if not math.isfinite(result.censoring_aware_median) or result.censoring_aware_median <= 0.0:
            raise AblationError(
                f"seed {result.seed} reports a censoring-aware median of "
                f"{result.censoring_aware_median!r}; the per-seed delta is expressed as a "
                f"fraction **of** that figure, so it is the one operand that has to be a "
                f"positive duration for the ratio to exist"
            )
        # The censoring-ignoring median is checked for finiteness and no further.
        # It is the numerator's other term, so the arithmetic is defined at any
        # value it takes, and a bar on it here would be this module deciding what
        # a comparator fit is allowed to have produced — which is the fit's own
        # refusal to make, on its own rows, where the cause is still visible.
        if not math.isfinite(result.censoring_ignoring_median):
            raise AblationError(
                f"seed {result.seed} reports a censoring-ignoring median of "
                f"{result.censoring_ignoring_median!r}; a non-finite median summarises no "
                f"draw set, and the delta taken from it would be non-finite in turn"
            )
    return ordered


def _nearest_rank_median(ordered: Sequence[float]) -> float:
    """`ordered[ceil(½·n)]`, one-based — `schema_constants.percentile_convention`.

    Nearest rank without interpolation, the convention this epic publishes every
    other percentile under, so the reported delta is a value some seed actually
    produced rather than the midpoint of two it did not.
    """
    return float(ordered[max(math.ceil(0.5 * len(ordered)), 1) - 1])


def realized_delta(results: Iterable[SeedResult]) -> RealizedDelta:
    """The delta over repeated seeds, with the range those seeds produced.

    Per seed, `(aware − ignoring) / aware`: **signed**, so a censoring-ignoring
    fit that came out *longer* reports as a negative delta rather than as a
    large positive one. SC-008's disposition when the delta falls below the
    floor is a published shortfall, not a defect, and that requires the number
    to be able to come out small, zero or negative.

    The interval is the **range the seeds actually produced**, not a normal
    approximation around them. At the handful of repetitions an ablation affords
    — each repetition is two fits — a t-interval reports a width the count does
    not support, and where the seeds agree exactly the honest interval is the
    degenerate one rather than a nominal band: the same shape `vendor_shrinkage`
    publishes for a vendor with no training line.

    Ordered by seed rather than by arrival, so `fit.py`'s loop order cannot move
    the published summary.
    """
    ordered = _checked_results(tuple(results))
    per_seed_deltas = tuple(
        (result.censoring_aware_median - result.censoring_ignoring_median)
        / result.censoring_aware_median
        for result in ordered
    )
    return RealizedDelta(
        delta=_nearest_rank_median(sorted(per_seed_deltas)),
        interval_low=min(per_seed_deltas),
        interval_high=max(per_seed_deltas),
        seeds=tuple(result.seed for result in ordered),
        per_seed_deltas=per_seed_deltas,
    )
