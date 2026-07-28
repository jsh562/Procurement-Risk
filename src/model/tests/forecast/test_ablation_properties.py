"""T049 / T118 (RED) — the censoring ablation, at the mandatory property tier.

`plan.md` § Mandated properties gives `ablation.py` one **Invariant** relation:
the Kaplan–Meier floor is computed from the training split alone and is
invariant to any held-out row. Domain: an all-censored and an all-delivered
training set. FR-033 fixes the derivation — a non-parametric survival estimate
against a naive completed-duration mean, never the fitted model — and requires
the realized delta to carry an interval over repeated seeds, which is T118's
half. The module does not exist yet; this is the red half of T049 and T118.
"""

from __future__ import annotations

import ast
import inspect
import math
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest
from hypothesis import given
from hypothesis import strategies as st
from model.forecast.ablation import (
    AblationError,
    KaplanMeierFloor,
    RealizedDelta,
    SeedResult,
    kaplan_meier_floor,
    realized_delta,
)

from model.forecast.censoring import censoring_indicator
from model.forecast.read import LifecycleEventRow, LineRow
from model.forecast.serialize import split_assignment_hash
from model.forecast.split import HELD_OUT, TRAIN, SplitAssignment, SplitResult
from model.procurement.censor import AS_OF_DATE

# ---------------------------------------------------------------------------
# Why the `ablation` import sits in the third-party block
# ---------------------------------------------------------------------------
#
# Ruff's isort decides first-party membership by looking for the module on
# disk. `model/forecast/ablation.py` does not exist yet — that is this file's
# whole point — so the import is classified third-party and `ruff check`
# demands it there. **When T050 lands the module, ruff will reclassify it and
# ask for it to be merged back** into the `model.*` block below; that is the
# expected diff, not a regression. Run `ruff check --no-cache`, because the
# cache keys on file content and will otherwise replay the classification made
# while the module was absent.
#
# ---------------------------------------------------------------------------
# The interface this file pins
# ---------------------------------------------------------------------------
#
# `kaplan_meier_floor(lines, split, as_of_date)` takes the **whole** cohort and
# the split, in the shape `model.training_frame(lines, split, …, as_of_date)`
# already establishes, and returns a `KaplanMeierFloor` publishing **`floor`**,
# **`interval_low`**, **`interval_high`**, **`kaplan_meier_median`**,
# **`naive_completed_mean`** and **`training_line_count`**.
#
# It takes the whole cohort rather than the training rows because that is the
# only shape in which "computed from the training split alone" is a claim about
# the function instead of about its caller: a function handed pre-filtered rows
# is invariant to a held-out row by having never seen one. The filter is the
# split, exactly as `training_frame` makes it structural rather than
# conventional.
#
# Both interval fields are present for the reason AD-008 gives: the
# Kaplan–Meier median is an estimate, and a bare number for it is the shape
# Principle II refuses — the same defect M-2 closed for `vendor_shrinkage`.
#
# `realized_delta(results)` takes an iterable of
# `SeedResult(seed, censoring_aware_median, censoring_ignoring_median)` — one
# per repeated seed, each carrying the two aggregate medians that seed's pair of
# fits produced — and returns a `RealizedDelta` publishing **`delta`**,
# **`interval_low`**, **`interval_high`**, **`seeds`** and
# **`per_seed_deltas`**. It is the pure half of FR-033: this tier never invokes
# the sampler, so the medians arrive as values and the seed loop producing them
# is `fit.py`'s (T051).
#
# **Both quantities are relative shortenings of the censoring-aware figure** —
# `(aware − ignoring) / aware` for the delta, `(km_median − naive_mean) /
# km_median` for the floor. That they share a scale and a direction is what
# makes SC-008's comparison a comparison at all; the one measured analogue
# available reads the same way, 58.0 against 53.0 being a gap of 8.6% **of
# 58.0**.
#
# ---------------------------------------------------------------------------
# What this file refuses to assert, and why
# ---------------------------------------------------------------------------
#
# **No percentage appears below as a bar.** An earlier revision of SC-008
# asserted a flat "at least 10% shorter" with nothing deriving it, and it sat
# above the only measured analogue available — so it would have failed a correct
# implementation. Every bar here is read off a value the module returned.
#
# **No sign is asserted for the floor.** `(km_median − naive_mean)` compares a
# median against a mean, and on a right-skewed sample with little censoring the
# mean is the larger of the two: eleven completed lines at 10…110 days give a
# Kaplan–Meier median of 60 against a naive mean of 60, and shifting the ladder
# either way moves the floor either way. A correct implementation reports what
# it finds. What is asserted instead is that the floor **moves with the input's
# own censoring level**, which is what a constant could not do.

#: The committed anchor every cohort below is fitted at, so the swept domain is
#: the dataset's own rather than a date chosen here.
AS_OF = AS_OF_DATE

#: The longest duration a fixture line carries. Every training set drawn below
#: ends with one completed line at exactly this duration — see `training_sets`.
LONGEST_DELIVERED = 400

#: Where a delivered line is ordered from, far enough back that its terminal
#: event lands before the anchor at any duration up to `LONGEST_DELIVERED`. A
#: terminal event *after* the anchor makes the line censored at it, which is a
#: case this file exercises deliberately and must not stumble into.
ORDER_ANCHOR = AS_OF - timedelta(days=LONGEST_DELIVERED + 30)

#: The forward path a line walks, terminal event last.
WALK = ("submitted", "under_review", "approved", "released_for_fabrication", "shipped", "delivered")

#: The shortest duration a fixture carries: five pre-terminal events at one-day
#: intervals have to fit before the terminal one.
SHORTEST = 6

NS = uuid.uuid5(uuid.NAMESPACE_URL, "e007/tests/forecast/ablation-properties")

#: Relative tolerance for the agreement assertions. Every reference below is the
#: textbook expression rather than a second implementation, so anything past
#: accumulated floating error is a different formula.
REL_TOL = 1e-12

#: How close to one half a product-limit step may land before the crossing is a
#: floating-point coin toss rather than a decision. The convention is
#: `S(t) <= 0.5`, and it is pinned exactly where the arithmetic is exact; where
#: the running product sits within this of one half — which happens whenever the
#: risk set telescopes to an even count, so on a large share of drawn inputs —
#: the step before and the step after are both consistent with the convention
#: and `kaplan_meier_medians_of` admits either. Pinning one there would be this
#: file legislating an association order rather than asserting the estimator.
HALF_TOLERANCE = 1e-9

#: Modules `ablation.py` may not reach. FR-033's whole point: deriving the floor
#: from the fitted model compares a measurement against a derivation of the same
#: quantity. `likelihood.py` is on the list because AD-001's lognormal family
#: *is* the fitted model's family — a floor back-solved from it would be
#: parametric, and would be the fit's own assumption returning as its own bar.
FORBIDDEN_IMPORTS = (
    "pymc",
    "arviz",
    "model.forecast.model",
    "model.forecast.sample",
    "model.forecast.fit",
    "model.forecast.posterior",
    "model.forecast.likelihood",
    "model.forecast.compare",
    "model.forecast.reproduce",
)


@dataclass(frozen=True, slots=True)
class Observation:
    """One line reduced to what the ablation reads of it.

    `days` is the completed duration for a delivered line and the censoring time
    for an open one — the two quantities `censoring.py` keeps distinct and which
    the product-limit estimator consumes on the same axis.
    """

    days: int
    censored: bool


# ---------------------------------------------------------------------------
# Fixtures: cohorts and hand-built splits
# ---------------------------------------------------------------------------


def make_line(index: int, observation: Observation) -> LineRow:
    """One line realizing `observation` at `AS_OF`, by construction.

    A delivered line walks the full six-event path with its terminal event
    `observation.days` after its order date. An open one stops at `shipped` and
    is ordered `observation.days` before the anchor, so its elapsed time at
    `AS_OF` is the censoring time asked for. The stratum is therefore a property
    of the row rather than a flag passed in, which keeps the assertions about
    `ablation.py` rather than about their own fixtures.
    """
    po_number = f"PO-{index:04d}-0001"
    po_line_id = uuid.uuid5(NS, f"pol|{po_number}")
    if observation.censored:
        order_date = AS_OF - timedelta(days=observation.days)
        reached, offsets = WALK[:5], (1, 2, 3, 4, 5)
    else:
        order_date = ORDER_ANCHOR
        reached, offsets = WALK, (1, 2, 3, 4, 5, observation.days)
    return assemble(po_line_id, po_number, order_date, reached, offsets)


def line_open_but_flagged_closed(index: int, *, elapsed: int, overshoot: int) -> LineRow:
    """A line whose terminal event is dated `overshoot` days *after* the anchor.

    `is_closed` reads true and `closing_event_id` is set — the loader's snapshot
    answer to an undated question — while at `AS_OF` the delivery has not
    happened. FR-003 makes this line censored at `elapsed` days, and the floor
    has to see that rather than a duration from the future.
    """
    po_number = f"PO-{index:04d}-0001"
    po_line_id = uuid.uuid5(NS, f"pol|{po_number}")
    order_date = AS_OF - timedelta(days=elapsed)
    return assemble(po_line_id, po_number, order_date, WALK, (1, 2, 3, 4, 5, elapsed + overshoot))


def assemble(
    po_line_id: uuid.UUID,
    po_number: str,
    order_date,
    reached: Sequence[str],
    offsets: Sequence[int],
) -> LineRow:
    """The `LineRow` itself, given the walk and the day offsets it happened on."""
    events = tuple(
        LifecycleEventRow(
            event_id=uuid.uuid5(NS, f"evt|{po_line_id}|{position + 1}"),
            po_line_id=po_line_id,
            sequence_no=position + 1,
            from_state=reached[position - 1] if position else None,
            to_state=state,
            is_terminal=state == "delivered",
            occurred_at=datetime(order_date.year, order_date.month, order_date.day, tzinfo=UTC)
            + timedelta(days=offsets[position]),
            note=None,
        )
        for position, state in enumerate(reached)
    )
    delivered = events[-1].is_terminal
    return LineRow(
        po_line_id=po_line_id,
        project_id="PRJ-001",
        vendor_id="VND-001",
        po_number=po_number,
        line_number=1,
        material_category="WATER_CHILLER",
        description="Water Chiller (Tag 201-14)",
        manufacturer="Ironvane Thermal",
        part_number="IRV-236500-0001",
        quantity=Decimal("6.0"),
        unit_of_measure="EA",
        order_date=order_date,
        need_by_date=order_date + timedelta(days=120),
        criticality=3,
        lifecycle_state=reached[-1],
        is_closed=delivered,
        closing_event_id=events[-1].event_id if delivered else None,
        roster_hash="sha256:" + "0" * 64,
        events=events,
    )


def make_split(lines: Sequence[LineRow], held_out: frozenset[uuid.UUID]) -> SplitResult:
    """A split assigning exactly the named lines to the held-out side.

    Hand-built rather than produced by `assign_split`, because the invariance
    property needs the sides held fixed while a held-out row moves. `assign_split`
    keys every side on `input_data_hash`, so perturbing one held-out line would
    reshuffle the whole cohort and the comparison would be between two different
    training sets — which is the failure this property exists to detect, arriving
    from the fixture instead of from the code.
    """
    assignments = tuple(
        SplitAssignment(
            po_line_id=line.po_line_id,
            project_id=line.project_id,
            po_number=line.po_number,
            line_number=line.line_number,
            split_side=HELD_OUT if line.po_line_id in held_out else TRAIN,
            is_censored=censoring_indicator(line, AS_OF),
            canonical_ordinal=ordinal,
        )
        for ordinal, line in enumerate(sorted(lines, key=lambda row: row.natural_key), start=1)
    )
    return SplitResult(
        assignments=assignments, split_assignment_hash=split_assignment_hash(assignments)
    )


def cohort(
    training: Sequence[Observation], held_out: Sequence[Observation] = ()
) -> tuple[tuple[LineRow, ...], SplitResult]:
    """A cohort whose first `len(training)` lines are the training side.

    Positional, so the same training observations produce identical `LineRow`s
    whatever is appended after them. That is what lets the invariance assertions
    compare two whole records for equality rather than for approximate
    agreement.
    """
    lines = tuple(
        make_line(index, observation) for index, observation in enumerate([*training, *held_out])
    )
    return lines, make_split(lines, frozenset(line.po_line_id for line in lines[len(training) :]))


def floor_of(
    training: Sequence[Observation], held_out: Sequence[Observation] = ()
) -> KaplanMeierFloor:
    lines, split = cohort(training, held_out)
    return kaplan_meier_floor(lines, split, AS_OF)


# ---------------------------------------------------------------------------
# The reference estimator, written out rather than composed
# ---------------------------------------------------------------------------


def kaplan_meier_steps(rows: Sequence[Observation]) -> tuple[tuple[int, float], ...]:
    """`(t, S(t))` at every distinct event time, by the product-limit formula.

    The risk set at `t` counts every observation with `days >= t`, so a row
    censored at an event time is at risk for that event — the standard
    convention, and the one a correct implementation has to share for the
    agreement assertions below to mean anything. It is stated here rather than
    left for the green half to infer from a failing example.
    """
    survival = 1.0
    steps: list[tuple[int, float]] = []
    for time in sorted({row.days for row in rows if not row.censored}):
        at_risk = sum(1 for row in rows if row.days >= time)
        events = sum(1 for row in rows if row.days == time and not row.censored)
        survival *= 1.0 - events / at_risk
        steps.append((time, survival))
    return tuple(steps)


def kaplan_meier_medians_of(rows: Sequence[Observation]) -> tuple[int, ...]:
    """Every event time consistent with `S(t) <= 0.5` under floating noise.

    One value wherever the curve crosses cleanly, two where a step lands on one
    half — see `HALF_TOLERANCE`. Returned as the admissible set rather than as a
    single answer so that a correct implementation is not failed for rounding a
    telescoping product the other way.
    """
    steps = kaplan_meier_steps(rows)
    crossings = (
        next((time for time, survival in steps if survival <= bound), None)
        for bound in (0.5 + HALF_TOLERANCE, 0.5 - HALF_TOLERANCE)
    )
    return tuple(dict.fromkeys(time for time in crossings if time is not None))


def naive_completed_mean_of(rows: Sequence[Observation]) -> float:
    """The arithmetic mean over the completed durations, censored rows discarded.

    The naive comparator AD-008 names: exactly what a reader gets by averaging
    the deliveries that have happened, and its downward bias is the censoring
    bias the floor is derived from.
    """
    completed = [row.days for row in rows if not row.censored]
    return sum(completed) / len(completed)


def earliest_delivered_only_median_of(rows: Sequence[Observation]) -> int:
    """The same estimator with the censored rows dropped rather than accounted for.

    The *earliest* admissible crossing, so the inequality it is compared under
    is the weakest one the convention supports and no tie can flip it.
    """
    completed = [row for row in rows if not row.censored]
    steps = kaplan_meier_steps(completed)
    return next(time for time, survival in steps if survival <= 0.5 + HALF_TOLERANCE)


def module_imports() -> set[str]:
    """Every module `ablation.py` imports, read from its own source.

    Parsed rather than inspected through the module's globals, because an import
    made inside a function body is still an import and would not appear there
    until the branch holding it ran. The source file is resolved from the
    exported function rather than from a second import of the module, so there
    is one route into `ablation.py` from this file and it is the public one.
    """
    source = Path(inspect.getsourcefile(kaplan_meier_floor) or "").read_text(encoding="utf-8")
    names: set[str] = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            names.add(node.module)
    return names


# ---------------------------------------------------------------------------
# Strategies
# ---------------------------------------------------------------------------

#: Durations short of `LONGEST_DELIVERED`, so the completed line appended by
#: `training_sets` is strictly the largest observation in the set.
durations = st.integers(min_value=SHORTEST, max_value=LONGEST_DELIVERED - 1)

observations = st.builds(Observation, days=durations, censored=st.booleans())


@st.composite
def training_sets(draw: st.DrawFn) -> tuple[Observation, ...]:
    """A training set whose Kaplan–Meier median is always defined.

    Every draw ends with one completed line at the strictly largest duration in
    the set, so the product-limit curve reaches zero and a median exists.
    Without it Hypothesis would spend most of its budget on heavily censored
    sets whose curve never crosses one half — a real input, and the one the
    refusal boundary below is written for, but not one the invariance relation
    can be stated over.
    """
    body = draw(st.lists(observations, min_size=0, max_size=14))
    return (*body, Observation(days=LONGEST_DELIVERED, censored=False))


@st.composite
def delivered_training_sets(draw: st.DrawFn) -> tuple[Observation, ...]:
    """The all-delivered boundary the plan's domain column names."""
    body = draw(st.lists(durations, min_size=0, max_size=14))
    return (
        *(Observation(days=day, censored=False) for day in body),
        Observation(days=LONGEST_DELIVERED, censored=False),
    )


#: Per-seed deltas as fractions. Negative values are inside the domain on
#: purpose: the censoring-ignoring fit coming out *longer* on some seed is an
#: outcome, and a delta that could not represent it would report a shortfall as
#: a pass.
seed_deltas = st.floats(min_value=-0.5, max_value=0.9, allow_nan=False, allow_infinity=False)


def seed_results(
    deltas: Sequence[float], *, aware: float = 100.0, first_seed: int = 1
) -> tuple[SeedResult, ...]:
    """One `SeedResult` per requested delta, back-solved from a fixed aware median.

    Back-solved rather than drawn, so a test wanting a known spread of per-seed
    deltas gets exactly that spread instead of whatever two drawn medians happen
    to imply.
    """
    return tuple(
        SeedResult(
            seed=first_seed + position,
            censoring_aware_median=aware,
            censoring_ignoring_median=aware * (1.0 - delta),
        )
        for position, delta in enumerate(deltas)
    )


def by_seed(results: Sequence[SeedResult]) -> dict[int, SeedResult]:
    return {result.seed: result for result in results}


# ---------------------------------------------------------------------------
# The cohorts the constant-free assertions are read off
# ---------------------------------------------------------------------------
#
# Eleven completed lines at 10…110 days, with 0, 2 and 4 lines still open at 95
# beside them. The completed side never moves, so the naive mean is 60.0 in all
# three and every difference between their floors is the input's own censoring
# level talking. The counts are chosen odd on purpose: an even risk set
# telescopes to exactly one half at its midpoint, which is the floating tie
# `HALF_TOLERANCE` exists for, and a fixed cohort should not need it.

COMPLETED_LADDER = tuple(Observation(days=day, censored=False) for day in range(10, 111, 10))

#: Where the still-open lines sit — inside the completed ladder's range, so they
#: carry the risk set past the point the delivered-only curve has already
#: crossed. Censoring beyond the largest completed duration would move nothing.
OPEN_AT = 95

CENSORING_LEVELS = (0, 2, 4)


def ladder_with(censored_count: int) -> tuple[Observation, ...]:
    return (
        *COMPLETED_LADDER,
        *(Observation(days=OPEN_AT, censored=True) for _ in range(censored_count)),
    )


# ---------------------------------------------------------------------------
# T049 — Invariant: the floor is computed from the training split alone
# ---------------------------------------------------------------------------


@given(training=training_sets(), held_out=st.lists(observations, min_size=0, max_size=8))
def test_the_floor_over_the_whole_cohort_equals_the_floor_over_its_training_rows_alone(
    training: tuple[Observation, ...], held_out: list[Observation]
) -> None:
    """`plan.md` § Mandated properties, in its strongest form.

    Not "close to" and not "within a tolerance": the same rows in the same order
    are the same arithmetic, so the whole record — the point estimate and both
    interval bounds — must be equal. A held-out row reaching the floor by any
    route at all fails here, including one that reaches only the interval, which
    is the half a comparison on the point estimate alone would miss.
    """
    assert floor_of(training, held_out) == floor_of(training)


@given(training=training_sets(), extra=observations)
def test_adding_a_held_out_line_never_moves_the_floor(
    training: tuple[Observation, ...], extra: Observation
) -> None:
    """FR-033: the floor is the input's censoring bias measured where the fit is
    allowed to look. A held-out line is evidence the fit has not been shown, and
    a bar that moved when one arrived would be a bar the held-out set could be
    tuned against."""
    assert floor_of(training, (extra,)) == floor_of(training)


@given(training=training_sets(), first=observations, second=observations)
def test_removing_a_held_out_line_never_moves_the_floor(
    training: tuple[Observation, ...], first: Observation, second: Observation
) -> None:
    """The other direction, stated separately because it fails differently.

    An implementation pooling the whole cohort would move in both directions;
    one pooling only *censored* held-out rows would move on the removal of a
    censored line and not on the addition of a delivered one.
    """
    assert floor_of(training, (first, second)) == floor_of(training, (first,))


@given(training=training_sets(), before=observations, after=observations)
def test_perturbing_a_held_out_line_never_moves_the_floor(
    training: tuple[Observation, ...], before: Observation, after: Observation
) -> None:
    """The perturbation covers both axes at once — the duration and the stratum.

    `Observation` carries exactly the two things the estimator reads, so two
    independent draws of it differ in the censoring time, in whether the line
    delivered, or in both.
    """
    assert floor_of(training, (before,)) == floor_of(training, (after,))


def test_moving_one_line_from_the_training_side_to_the_held_out_side_does_move_the_floor() -> None:
    """The positive control the four invariance assertions above need.

    Every one of them is satisfied by a function returning a constant, and a
    constant is exactly the failure SC-008's history records. Moving the longest
    completed line across the split changes the training set the floor is
    derived from, so the record must move: this is the assertion that makes
    "invariant to any held-out row" a claim about the split rather than about a
    function ignoring its arguments.
    """
    ladder = ladder_with(4)
    longest_completed = len(COMPLETED_LADDER) - 1
    remaining = (*ladder[:longest_completed], *ladder[longest_completed + 1 :])

    assert floor_of(ladder) != floor_of(remaining, (ladder[longest_completed],))


def test_the_reported_training_line_count_is_the_training_side_of_the_split() -> None:
    """The "alone" in "the training split alone", made directly observable.

    Published rather than inferred, because a reader checking whether the floor
    was derived from 148 lines or from 199 has no other way to tell — and the
    difference between those two numbers is the whole content of FR-007.
    """
    training = ladder_with(4)
    held_out = (Observation(days=40, censored=False), Observation(days=200, censored=True))

    assert floor_of(training, held_out).training_line_count == len(training)


def test_a_line_carrying_no_split_assignment_is_refused() -> None:
    """The route by which a held-out row could reach the floor without being one.

    `training_frame` already refuses an unassigned line for this reason — FR-007
    is a claim about every line, so one carrying no side cannot be admitted on
    the assumption that it is training data. A floor that silently skipped it
    would be derived from a set nobody declared.
    """
    training = ladder_with(2)
    lines, split = cohort(training)
    smuggled = make_line(len(training), Observation(days=30, censored=False))

    with pytest.raises(AblationError):
        kaplan_meier_floor((*lines, smuggled), split, AS_OF)


# ---------------------------------------------------------------------------
# T049 — the derivation: non-parametric survival against a naive mean
# ---------------------------------------------------------------------------


@given(training=training_sets())
def test_the_two_operands_are_the_kaplan_meier_median_and_the_naive_completed_mean(
    training: tuple[Observation, ...],
) -> None:
    """AD-008's derivation, against the product-limit formula written out here.

    The reference in `kaplan_meier_steps` is the textbook expression rather than
    a second implementation, tie convention included: a row censored at an event
    time is at risk for that event.
    """
    record = floor_of(training)

    assert record.kaplan_meier_median in kaplan_meier_medians_of(training)
    assert record.naive_completed_mean == pytest.approx(naive_completed_mean_of(training))


@given(training=training_sets())
def test_the_floor_is_the_published_relation_between_those_two_operands(
    training: tuple[Observation, ...],
) -> None:
    """Published with its derivation, as an identity rather than as prose.

    The floor is the gap between the two operands as a fraction **of the
    censoring-aware one** — the way the one measured analogue available reads
    it, 58.0 against 53.0 being 8.6% of 58.0. Both operands are on the record,
    so a reader can recompute the derivation from the record alone, which is
    what stops the number from being an assertion.
    """
    record = floor_of(training)
    expected = (
        record.kaplan_meier_median - record.naive_completed_mean
    ) / record.kaplan_meier_median

    assert math.isclose(record.floor, expected, rel_tol=REL_TOL, abs_tol=REL_TOL)


@given(training=training_sets())
def test_the_survival_estimate_is_non_parametric(training: tuple[Observation, ...]) -> None:
    """The median lands on an observed completed duration, never between two.

    A product-limit curve steps only at event times, so its median is one of
    them. A floor back-solved from a fitted lognormal — the near-tautological
    route FR-033 forbids — would land wherever the family put it, and almost
    never on the grid.
    """
    record = floor_of(training)
    observed = {row.days for row in training if not row.censored}

    assert record.kaplan_meier_median in observed


@given(training=training_sets())
def test_the_estimate_uses_the_censored_rows_rather_than_discarding_them(
    training: tuple[Observation, ...],
) -> None:
    """Accounting for censoring never shortens the estimate.

    `S_KM(t) >= S_delivered_only(t)` everywhere, because each product-limit
    factor divides by a risk set counting the open lines too. So the
    Kaplan–Meier median is at or above the delivered-only median for **every**
    input — and an implementation that dropped the censored rows would satisfy
    this with equality throughout, which is why the strict case below is
    asserted separately.
    """
    record = floor_of(training)

    assert record.kaplan_meier_median >= earliest_delivered_only_median_of(training)


def test_censoring_pushes_the_estimate_strictly_above_the_delivered_only_median() -> None:
    """The strict case, so the inequality above is not satisfied by equality.

    Four lines still open at 95 days sit inside the completed ladder's range, so
    they carry the risk set past the point where the delivered-only curve has
    already crossed one half.
    """
    ladder = ladder_with(4)

    assert floor_of(ladder).kaplan_meier_median > earliest_delivered_only_median_of(ladder)


def test_a_line_delivered_after_the_as_of_date_contributes_as_censored() -> None:
    """The floor reads censoring at the anchor, not the loader's closure flag.

    A line whose terminal event has not happened at the as-of date is open at
    that date however `is_closed` reads, and the floor must take its elapsed
    time rather than a duration from the future. Both readings are computable,
    which is what makes this silent: the wrong one yields a longer naive mean
    and a plausible floor.
    """
    training = ladder_with(1)
    lines = tuple(make_line(index, row) for index, row in enumerate(training))
    late = line_open_but_flagged_closed(len(training), elapsed=OPEN_AT, overshoot=40)
    rows = (*lines, late)
    record = kaplan_meier_floor(rows, make_split(rows, frozenset()), AS_OF)

    as_censored = (*training, Observation(days=OPEN_AT, censored=True))
    as_delivered = (*training, Observation(days=OPEN_AT + 40, censored=False))

    assert record.naive_completed_mean == pytest.approx(naive_completed_mean_of(as_censored))
    assert record.kaplan_meier_median in kaplan_meier_medians_of(as_censored)
    assert record.naive_completed_mean != pytest.approx(naive_completed_mean_of(as_delivered))


def test_the_floor_takes_no_fitted_quantity() -> None:
    """FR-033 read off the signature: there is nowhere for a fit to enter.

    The floor is computed **before and independently of** the fit (AD-008), and
    a parameter able to carry a trace, a posterior or an idata would make that
    an ordering convention rather than a structural fact.
    """
    parameters = tuple(inspect.signature(kaplan_meier_floor).parameters)

    assert parameters == ("lines", "split", "as_of_date")


def test_the_floor_module_does_not_reach_the_fitted_model() -> None:
    """The same claim over the module, closing the route the signature cannot.

    A function taking no fitted argument can still import the sampler, the graph
    or the lognormal family the graph is written in, and derive its bar from one
    of them. That is the near-tautological construction FR-033 exists to forbid:
    a measurement compared against a derivation of the same quantity.
    """
    reached = sorted(
        name
        for name in module_imports()
        for forbidden in FORBIDDEN_IMPORTS
        if name == forbidden or name.startswith(f"{forbidden}.")
    )

    assert not reached, (
        f"`ablation.py` imports {reached}. AD-008 derives the floor from a non-parametric "
        f"survival estimate on the training split, computed before and independently of the "
        f"fit; a route into the fitted model — or into AD-001's lognormal family, which is "
        f"the fit's own assumption — makes SC-008 compare a measurement against a "
        f"derivation of the same quantity (FR-033)."
    )


# ---------------------------------------------------------------------------
# T049 — boundary: the all-delivered and all-censored training sets
# ---------------------------------------------------------------------------


@given(training=delivered_training_sets())
def test_an_all_delivered_training_set_reduces_to_the_empirical_survival(
    training: tuple[Observation, ...],
) -> None:
    """The boundary the plan's domain column names first.

    With nothing censored every risk set is the plain count of survivors, so the
    product-limit estimator collapses to the empirical survival function and its
    median is the ordinary order statistic under the `S(t) <= 0.5` convention.
    This is the case that fails loudly on a mis-updated risk set, because there
    is no censoring for the error to hide behind — the reference here counts
    survivors directly rather than accumulating a product.
    """
    record = floor_of(training)
    days = sorted(row.days for row in training)
    survivors = {day: sum(1 for other in days if other > day) / len(days) for day in days}
    empirical = tuple(
        dict.fromkeys(
            next(day for day in days if survivors[day] <= bound)
            for bound in (0.5 + HALF_TOLERANCE, 0.5 - HALF_TOLERANCE)
        )
    )

    assert record.kaplan_meier_median in empirical
    assert record.naive_completed_mean == pytest.approx(sum(days) / len(days))


def test_an_all_censored_training_set_is_refused() -> None:
    """The other named boundary: neither operand exists.

    No line has completed, so there is no completed-duration mean to take and no
    event time for the curve to step at. Refused rather than answered with a
    substituted number — a floor derived from no observation is not derived from
    anything, and Principle III records an unobtainable value as absent rather
    than storing it wrong.
    """
    training = tuple(Observation(days=day, censored=True) for day in (30, 60, 90, 120))

    with pytest.raises(AblationError):
        floor_of(training)


def test_a_training_set_whose_survival_never_reaches_one_half_is_refused() -> None:
    """The general case the all-censored boundary is one instance of.

    One delivery at ten days against nine lines still open at two hundred leaves
    the curve at 0.9 and it never falls further, so no median exists. The naive
    mean is perfectly computable here, which is what makes the case worth its
    own test: an implementation taking the mean first has one operand in hand
    and must still refuse.
    """
    training = (
        Observation(days=10, censored=False),
        *(Observation(days=200, censored=True) for _ in range(9)),
    )

    with pytest.raises(AblationError):
        floor_of(training)


# ---------------------------------------------------------------------------
# T049 — the floor is derived, never a constant
# ---------------------------------------------------------------------------


def test_the_floor_moves_with_the_inputs_own_censoring_level() -> None:
    """SC-008's history, as a test rather than as a paragraph.

    Three cohorts sharing one completed ladder and differing only in how many
    lines are still open produce three different floors. A flat percentage — the
    retired "at least 10% shorter", which sat above the only measured analogue
    available and would therefore have failed a correct implementation — returns
    one value here and fails.
    """
    floors = [floor_of(ladder_with(level)).floor for level in CENSORING_LEVELS]

    assert len(set(floors)) == len(CENSORING_LEVELS), (
        f"the floor read {floors} across censoring levels {CENSORING_LEVELS} on a fixed "
        f"completed ladder. It is derived from the input's own censoring bias (AD-008), so "
        f"it has to move when that bias does; a value that does not is a constant wearing a "
        f"derivation's name."
    )


def test_the_floor_rises_as_the_input_carries_more_censoring() -> None:
    """The direction, on the ladder where the comparison is well posed.

    The completed side is held fixed, so the naive mean is the same in all three
    cohorts and only the Kaplan–Meier median moves. More open lines carried in
    the risk set push it up and the floor with it, which is the sense in which
    the floor is the *input's* censoring bias rather than the estimator's.
    """
    records = [floor_of(ladder_with(level)) for level in CENSORING_LEVELS]

    assert [record.floor for record in records] == sorted(record.floor for record in records)
    for record in records:
        assert record.naive_completed_mean == pytest.approx(
            naive_completed_mean_of(COMPLETED_LADDER)
        )


# ---------------------------------------------------------------------------
# T049 — the floor is published with its own interval (AD-008, Principle II)
# ---------------------------------------------------------------------------


@given(training=training_sets())
def test_the_floor_is_published_with_an_interval_around_it(
    training: tuple[Observation, ...],
) -> None:
    """AD-008: the Kaplan–Meier median is an estimate, so the floor is uncertain.

    Ordered and finite, asserted over the whole swept domain. The width is
    checked separately, because ordering alone is satisfied by a bare number
    restated three times — the shape M-2 closed for `vendor_shrinkage` and the
    one Principle II refuses.
    """
    record = floor_of(training)

    assert math.isfinite(record.interval_low)
    assert math.isfinite(record.interval_high)
    assert record.interval_low <= record.floor <= record.interval_high


def test_the_floors_interval_is_not_the_point_estimate_restated() -> None:
    """The width, on a cohort big enough for one to be meaningful.

    Fifteen lines, four of them still open: an interval of zero width there
    would be a claim that the floor is known exactly, which no estimate off
    fifteen observations is.
    """
    record = floor_of(ladder_with(4))

    assert record.interval_high > record.interval_low


# ---------------------------------------------------------------------------
# T118 — the realized delta carries an interval over repeated seeds
# ---------------------------------------------------------------------------


@given(deltas=st.lists(seed_deltas, min_size=2, max_size=8))
def test_the_realized_delta_is_reported_with_an_interval(deltas: list[float]) -> None:
    """FR-033 and Principle II: never a point estimate on its own.

    The interval brackets the reported figure and both bounds are real numbers,
    over every spread of per-seed outcomes the domain admits.
    """
    reported = realized_delta(seed_results(deltas))

    assert math.isfinite(reported.interval_low)
    assert math.isfinite(reported.interval_high)
    assert reported.interval_low <= reported.delta <= reported.interval_high


@given(deltas=st.lists(seed_deltas, min_size=2, max_size=8))
def test_the_interval_is_computed_over_more_than_one_seed(deltas: list[float]) -> None:
    """Repetition over seeds made observable rather than asserted.

    The seeds the interval was computed over are on the record, they are
    distinct, there are at least two of them, and there is one per-seed delta for
    each. A reader can therefore check the claim rather than take it — which is
    the whole difference between this and a single-seed pass.
    """
    reported = realized_delta(seed_results(deltas))

    assert len(reported.seeds) == len(deltas)
    assert len(set(reported.seeds)) == len(reported.seeds)
    assert len(reported.seeds) > 1
    assert len(reported.per_seed_deltas) == len(reported.seeds)


@given(delta=seed_deltas)
def test_a_single_seed_result_cannot_satisfy_the_interval(delta: float) -> None:
    """The failing direction FR-033 names: a single-seed pass is refused.

    One seed has no spread to measure, so any interval reported off it would be
    a degenerate one presented as evidence of stability. Refused rather than
    published wide or published narrow — either would be a number nobody
    measured.
    """
    with pytest.raises(AblationError):
        realized_delta(seed_results([delta]))


def test_no_seeds_at_all_is_refused() -> None:
    """The degenerate input, refused for the same reason as the single one."""
    with pytest.raises(AblationError):
        realized_delta(())


@given(delta=seed_deltas, other=seed_deltas)
def test_the_same_seed_twice_is_not_two_seeds(delta: float, other: float) -> None:
    """Two results at one seed is a single-seed pass with a second label on it.

    This is the route by which the refusal above is satisfiable without the
    property holding: re-reporting one seed's outcome under a repeated
    identifier produces two results, a spread that measures nothing, and an
    interval that looks measured.
    """
    repeated = (*seed_results([delta], first_seed=7), *seed_results([other], first_seed=7))

    with pytest.raises(AblationError):
        realized_delta(repeated)


# ---------------------------------------------------------------------------
# T118 — what the interval and the delta are made of
# ---------------------------------------------------------------------------


@given(deltas=st.lists(seed_deltas, min_size=2, max_size=8))
def test_each_per_seed_delta_is_the_relative_shortening_of_the_censoring_ignoring_fit(
    deltas: list[float],
) -> None:
    """SC-008's relation, per seed, on the same scale as the derived floor.

    `(aware - ignoring) / aware`: positive when the fit omitting the censoring
    contribution is the shorter one, which is the direction SC-008 claims. A
    delta expressed in days, or taken against the other denominator, would still
    be a plausible number and would make the comparison against the floor
    meaningless rather than false. Paired through `seeds` rather than by
    position, so the alignment of the two published sequences is asserted too.
    """
    results = seed_results(deltas)
    reported = realized_delta(results)
    lookup = by_seed(results)

    for seed, per_seed in zip(reported.seeds, reported.per_seed_deltas, strict=True):
        result = lookup[seed]
        expected = (
            result.censoring_aware_median - result.censoring_ignoring_median
        ) / result.censoring_aware_median
        assert math.isclose(per_seed, expected, rel_tol=REL_TOL, abs_tol=REL_TOL)


@given(deltas=st.lists(seed_deltas, min_size=2, max_size=8))
def test_the_delta_is_signed_by_which_fit_came_out_shorter(deltas: list[float]) -> None:
    """The sign carries the claim, so a shortfall reports as one.

    SC-008's disposition when the delta falls below the floor is a published
    shortfall, not a defect — which requires the delta to be able to come out
    small, zero or negative. An absolute magnitude would report a
    censoring-ignoring fit that came out *longer* as a large positive delta, and
    turn the epic's own failing direction into a pass.
    """
    results = seed_results(deltas)
    reported = realized_delta(results)
    lookup = by_seed(results)

    for seed, per_seed in zip(reported.seeds, reported.per_seed_deltas, strict=True):
        result = lookup[seed]
        shorter = result.censoring_ignoring_median < result.censoring_aware_median
        assert (per_seed > 0.0) is shorter


@given(deltas=st.lists(seed_deltas, min_size=2, max_size=8))
def test_the_reported_figure_lies_inside_the_range_the_seeds_produced(
    deltas: list[float],
) -> None:
    """The point estimate summarises the seeds rather than replacing them.

    Deliberately a containment rather than an equality against a mean or a
    median: which central summary T051 takes is its choice, and pinning one here
    would be this file legislating an implementation detail instead of asserting
    the property.

    The bounds carry a slack, because they have to. A summary of `n` identical
    values is not required to reproduce them bit for bit — `fmean` of five
    copies of one float lands an ulp below it — and a containment asserted
    exactly would fail a correct implementation on the degenerate case, which is
    the defect class SC-008's own history records.
    """
    reported = realized_delta(seed_results(deltas))
    low, high = min(reported.per_seed_deltas), max(reported.per_seed_deltas)
    slack = REL_TOL * (1.0 + max(abs(low), abs(high)))

    assert low - slack <= reported.delta <= high + slack


@given(delta=seed_deltas, count=st.integers(min_value=2, max_value=8))
def test_seeds_that_agree_exactly_yield_a_degenerate_interval(delta: float, count: int) -> None:
    """Zero measured spread is reported as zero width, not as a default one.

    The same shape `vendor_shrinkage` publishes for a vendor with no training
    line: where the data supports no spread, the honest interval is the
    degenerate one rather than a nominal band nobody measured.
    """
    reported = realized_delta(seed_results([delta] * count))

    assert reported.interval_low == pytest.approx(reported.delta)
    assert reported.interval_high == pytest.approx(reported.delta)


def test_seeds_that_disagree_yield_an_interval_of_positive_width() -> None:
    """The complement, so the degenerate case is not the only case reachable."""
    reported = realized_delta(seed_results([0.02, 0.09, 0.14, 0.21]))

    assert reported.interval_high > reported.interval_low


@given(
    deltas=st.lists(seed_deltas, min_size=2, max_size=8),
    stretch=st.floats(min_value=1.0, max_value=4.0, allow_nan=False, allow_infinity=False),
)
def test_the_interval_never_narrows_as_the_seeds_disagree_more(
    deltas: list[float], stretch: float
) -> None:
    """Metamorphic: spreading the per-seed outcomes cannot tighten the claim.

    The deltas are stretched about their own centre, so their summary is
    unchanged and only the disagreement grows. Any interval that is a scale
    estimate of the seeds — a range, a percentile pair, a normal approximation —
    widens by the same factor; one that does not is not reporting the seeds'
    disagreement at all.
    """
    centre = sum(deltas) / len(deltas)
    stretched = [centre + (delta - centre) * stretch for delta in deltas]

    narrow = realized_delta(seed_results(deltas))
    wide = realized_delta(seed_results(stretched))

    narrow_width = narrow.interval_high - narrow.interval_low
    wide_width = wide.interval_high - wide.interval_low

    assert wide_width >= narrow_width - REL_TOL


@given(deltas=st.lists(seed_deltas, min_size=2, max_size=8), rotation=st.integers(0, 7))
def test_the_interval_does_not_depend_on_the_order_the_seeds_arrive_in(
    deltas: list[float], rotation: int
) -> None:
    """The seeds are a set of repetitions, not a sequence.

    `fit.py` will produce them in whatever order its loop runs, and a summary
    depending on that order would be a different number each time the loop was
    reorganised — reproducible in neither the manifest's sense nor SC-018's.
    """
    results = seed_results(deltas)
    cut = rotation % len(results)
    rotated = (*results[cut:], *results[:cut])

    reported = realized_delta(results)
    reordered = realized_delta(rotated)

    assert reordered.delta == pytest.approx(reported.delta)
    assert reordered.interval_low == pytest.approx(reported.interval_low)
    assert reordered.interval_high == pytest.approx(reported.interval_high)
    assert set(reordered.seeds) == set(reported.seeds)


# ---------------------------------------------------------------------------
# T118 — the delta and the floor are the same kind of number
# ---------------------------------------------------------------------------


def test_the_delta_and_the_derived_floor_are_measured_on_one_scale() -> None:
    """The join SC-008 rests on, with the bar taken from the module.

    Seeds are constructed to straddle the floor this cohort derives, and the
    reported delta must land on it with the floor inside the interval. This
    catches the two halves drifting apart — a delta in days against a floor as a
    fraction, or the two dividing by different denominators — which would leave
    SC-008 comparing numbers that are not comparable while both look plausible.
    **The bar is read off `kaplan_meier_floor`, never written here**; no
    percentage appears anywhere in this file as a threshold.

    Straddling rather than sitting exactly on it, so the containment is not
    decided by the last bit of a value that made a round trip through
    `seed_results`' back-solve.
    """
    derived = floor_of(ladder_with(4))
    spread = abs(derived.floor) / 10.0 + 0.01
    reported = realized_delta(
        seed_results([derived.floor - spread, derived.floor, derived.floor + spread])
    )

    assert reported.delta == pytest.approx(derived.floor)
    assert reported.interval_low <= derived.floor <= reported.interval_high


def test_the_reported_unit_carries_the_delta_the_interval_and_the_seeds_together() -> None:
    """FR-038's unit, at the boundary this module owns.

    `report.py` renders the verdict against the floor (T052); what `ablation.py`
    has to guarantee is that the delta cannot be obtained without the interval
    and the seed set that produced it. One object rather than three returns, for
    the reason `SplitResult` gives: a caller able to obtain one without the
    others would eventually publish one without the others.
    """
    reported = realized_delta(seed_results([0.05, 0.11, 0.17]))

    assert isinstance(reported, RealizedDelta)
    for field in ("delta", "interval_low", "interval_high", "seeds", "per_seed_deltas"):
        assert hasattr(reported, field), f"the reported unit drops {field!r}"
