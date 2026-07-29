"""T053 — NC-6: the two failing directions SC-008's ablation could hide behind.

`plan.md` § Planted failing directions gives NC-6 two cases, and they close
different holes.

**The censoring-ignoring fit's aggregate median is demonstrated shorter.**
SC-008 claims a direction, and a delta reported without ever having been
observed to point that way is a number nobody has watched move. Here the
direction is produced by two real fits differing in exactly one term, on an
input whose censoring is heavy enough that the term has something to say.

**A constructed input with no censoring produces a delta at zero rather than a
passing number.** This is the one that makes the first mean something. Remove
the censoring contribution from a training frame that carries no censored row
and *nothing changes*: the two graphs are the same graph, so the delta must be
exactly zero. An implementation whose "ablation" perturbed something else — the
seed, the shape, the frame's row order — would report a plausible non-zero
number here and pass SC-008 on a difference that has nothing to do with
censoring.

**Constructed cohorts rather than the committed dataset**, for both. The
committed input carries 18 censored sojourns in 909, which is a real censoring
level and a small one; NC-6 is about whether the *mechanism* is wired up, and
that is asserted where the effect is large and where the no-censoring case is
constructible at all. DV-020's end-to-end reading of the delivered dataset is
`test_ablation.py`'s.

**The fits are deliberately tiny** — two chains of fifty draws. Neither
assertion here is shape-dependent: the first is a direction and the second is an
exact equality between two runs of the same graph, and a longer chain would
sharpen a margin that is already unambiguous while multiplying the cost of six
fits by twenty.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from model.forecast.ablation import realized_delta
from model.forecast.censoring import censoring_indicator
from model.forecast.fit import (
    ABLATION_CHAINS,
    ABLATION_DRAWS_PER_CHAIN,
    ABLATION_TUNING_DRAWS,
    censoring_ablation,
    censoring_ignoring_frame,
)
from model.forecast.model import training_frame
from model.forecast.read import LifecycleEventRow, LineRow
from model.forecast.serialize import split_assignment_hash
from model.forecast.split import HELD_OUT, TRAIN, SplitAssignment, SplitResult

#: The anchor these cohorts are built around. A committed date and never a clock
#: read, for the reason `fit.py` refuses to default one.
AS_OF = date(2026, 4, 1)

#: The forward path a line walks, terminal event last.
WALK = ("submitted", "under_review", "approved", "released_for_fabrication", "shipped", "delivered")

#: Two of each, which is the minimum `build_model` admits: partial pooling over
#: one group pools over nothing and τ would be identified by its prior alone.
VENDORS = ("VND-A01", "VND-B02")
CATEGORIES = ("AIR_HANDLER", "WATER_CHILLER")

#: The completed `shipped → delivered` leg every delivered line carries, and the
#: elapsed time every open line sits at. The open lines are censored **far past**
#: the completed legs on the same transition, which is where the two likelihoods
#: disagree most: a survival term reads "at least 115 days" and a density term
#: reads "exactly 115", and only the first can push the fitted location past its
#: own observation.
DELIVERED_TOTAL_DAYS = 30
OPEN_ELAPSED_DAYS = 120

#: Where the *held-out* open lines of the no-censoring cohort sit. Well inside
#: the completed durations that cohort's training side carries, unlike
#: `OPEN_ELAPSED_DAYS` — deliberately, and the reason is a defect in delivered
#: code rather than a preference. `conditional_remaining_draws` returns
#: `F⁻¹(F*) − e`, and where `S(e)` underflows to zero the conditioned quantile
#: saturates at `_LARGEST_QUANTILE`; the total it inverts to is then finite and
#: can fall *below* `e`, so the "remaining" duration comes back negative even
#: though `posterior.py` documents every returned draw as strictly positive. A
#: training set of 30-day deliveries asked to forecast a line open 120 days
#: reaches that regime. NC-6's second case is about the ablation, not about the
#: far tail of the conditioning, so the cohort stays where the arithmetic is
#: sound and the defect is reported rather than asserted around here.
HELD_OUT_OPEN_ELAPSED_DAYS = 15

#: The horizon the conditional draws are gridded over. E003's published 365, but
#: read as an argument rather than from the database: nothing here needs a
#: connection, and the aggregate median is taken over the draws rather than off
#: the grid.
HORIZON_DAYS = 365

#: Two seeds, which is the smallest set FR-033's interval is defined over. Each
#: is a pair of fits, so the count is the cost.
SEEDS = (11, 13)

#: How far below one the no-censoring delta must sit to be "at zero". The two
#: arms are the same graph at the same seed, so the expectation is *exact*
#: equality and this bound exists only to name what a failure would look like.
ZERO_DELTA_TOLERANCE = 1e-12

NS = uuid.uuid5(uuid.NAMESPACE_URL, "e007/tests/forecast/ablation-controls")


# ---------------------------------------------------------------------------
# Constructed cohorts
# ---------------------------------------------------------------------------


def make_line(index: int, *, censored: bool, elapsed: int = OPEN_ELAPSED_DAYS) -> LineRow:
    """One line, delivered `DELIVERED_TOTAL_DAYS` after its order or still open.

    A delivered line walks the whole path and closes before the anchor. An open
    one stops at `shipped`, ordered `elapsed` days back, so every event it has is
    observed at the anchor and the leg out of `shipped` is the censored sojourn
    the ablation is about. Vendor and category alternate, so both hierarchies
    have something to pool over whichever subset a case uses.
    """
    po_number = f"PO-{index:04d}-0001"
    po_line_id = uuid.uuid5(NS, f"pol|{po_number}")
    if censored:
        order_date = AS_OF - timedelta(days=elapsed)
        reached, offsets = WALK[:5], (1, 2, 3, 4, 5)
    else:
        order_date = AS_OF - timedelta(days=DELIVERED_TOTAL_DAYS + 10)
        reached, offsets = WALK, (1, 2, 3, 4, 5, DELIVERED_TOTAL_DAYS)
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
    return LineRow(
        po_line_id=po_line_id,
        project_id="PRJ-001",
        vendor_id=VENDORS[index % len(VENDORS)],
        po_number=po_number,
        line_number=1,
        material_category=CATEGORIES[index % len(CATEGORIES)],
        description="Constructed line",
        manufacturer="Ironvane Thermal",
        part_number=f"IRV-{index:06d}",
        quantity=Decimal("1.0"),
        unit_of_measure="EA",
        order_date=order_date,
        need_by_date=order_date + timedelta(days=120),
        criticality=3,
        lifecycle_state=reached[-1],
        is_closed=not censored,
        closing_event_id=events[-1].event_id if not censored else None,
        roster_hash="sha256:" + "0" * 64,
        events=events,
    )


def make_split(lines: Sequence[LineRow], held_out: frozenset[uuid.UUID]) -> SplitResult:
    """A split assigning exactly the named lines to the held-out side.

    Hand-built rather than drawn by `assign_split`, because both cases need the
    sides *chosen*: the no-censoring case is precisely "every training line has
    delivered", and a hash-keyed draw would put open lines on the training side
    and quietly turn it into the other case.
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


def heavily_censored_cohort() -> tuple[tuple[LineRow, ...], SplitResult]:
    """Twelve delivered lines and twelve still open, all on the training side.

    Half the `shipped → delivered` sojourns the fit sees are censored, and every
    one of them is censored well past the completed ones. That is a censoring
    level the term can be measured at; the committed dataset's is far lower,
    which is why NC-6's direction is demonstrated here and DV-020's realized
    delta is read off the delivered input elsewhere.
    """
    lines = tuple(make_line(index, censored=index % 2 == 1) for index in range(24))
    return lines, make_split(lines, frozenset())


def uncensored_training_cohort() -> tuple[tuple[LineRow, ...], SplitResult]:
    """Every training line delivered; the open lines are all held out.

    "No censoring" has to mean *no censored row in the fit*, and it cannot mean
    no open line anywhere: SC-008's measured quantity is an aggregate median
    forecast **over open lines**, so a cohort with none has nothing to compare.
    Putting the open lines on the held-out side gives the forecast a population
    while leaving the training frame with no censored sojourn at all — which is
    the input NC-6 asks for.
    """
    delivered = tuple(make_line(index, censored=False) for index in range(12))
    open_lines = tuple(
        make_line(100 + index, censored=True, elapsed=HELD_OUT_OPEN_ELAPSED_DAYS)
        for index in range(4)
    )
    lines = (*delivered, *open_lines)
    return lines, make_split(lines, frozenset(line.po_line_id for line in open_lines))


def ablate(lines: Sequence[LineRow], split: SplitResult):
    """Both arms at every seed, at the tiny shape this file declares."""
    return censoring_ablation(
        lines,
        split,
        VENDORS,
        CATEGORIES,
        AS_OF,
        horizon_days=HORIZON_DAYS,
        seeds=SEEDS,
        chains=ABLATION_CHAINS,
        draws=ABLATION_DRAWS_PER_CHAIN,
        tune=ABLATION_TUNING_DRAWS,
        cores=1,
    )


@pytest.fixture(scope="module")
def censored_results():
    """The heavily censored cohort's paired medians, fitted once for this module."""
    lines, split = heavily_censored_cohort()
    return ablate(lines, split)


@pytest.fixture(scope="module")
def uncensored_results():
    """The no-censoring cohort's paired medians, fitted once for this module."""
    lines, split = uncensored_training_cohort()
    return ablate(lines, split)


# ---------------------------------------------------------------------------
# NC-6, first case: the comparator's aggregate median is shorter
# ---------------------------------------------------------------------------


def test_the_constructed_cohort_actually_carries_the_censoring_it_claims_to() -> None:
    """The precondition, asserted rather than assumed.

    Every claim below is about what the censoring term does, so a cohort that
    turned out to carry no censored sojourn would satisfy the direction test by
    accident on either side of zero. The count is read off the frame the fit is
    actually built from, not off the fixture's intent.
    """
    lines, split = heavily_censored_cohort()
    frame = training_frame(lines, split, VENDORS, CATEGORIES, AS_OF)

    assert int(frame.is_censored.sum()) > 0
    assert int(frame.is_censored.sum()) < frame.row_count


def test_the_censoring_ignoring_fit_produces_a_shorter_aggregate_median(
    censored_results,
) -> None:
    """NC-6's first case, on every seed rather than on the summary.

    SC-008's direction, produced rather than asserted: the comparator drops the
    survival term, so a line still open at 120 days enters the fit as though it
    had delivered then, and the fitted `shipped → delivered` location has no
    reason to reach past its own longest observation. Checked per seed, because
    a summary can point the right way while an individual repetition points the
    other — and the interval FR-033 requires exists precisely because that can
    happen.
    """
    for result in censored_results:
        assert result.censoring_ignoring_median < result.censoring_aware_median, (
            f"seed {result.seed} produced a censoring-ignoring median of "
            f"{result.censoring_ignoring_median:.2f} days against a censoring-aware "
            f"{result.censoring_aware_median:.2f}. SC-008 claims the comparator comes out "
            f"shorter; on a cohort where half the sojourns are censored far past the "
            f"completed ones, a comparator that did not is not omitting the censoring term"
        )


def test_the_realized_delta_over_that_cohort_is_positive_and_carries_its_interval(
    censored_results,
) -> None:
    """The same direction as the reported unit, with the interval beside it.

    A positive delta *and* an interval that stays on the positive side: a
    reported figure above zero whose interval straddled it would be a direction
    nobody had measured, which is the single-seed pass FR-033 names wearing a
    band.
    """
    reported = realized_delta(censored_results)

    assert reported.delta > 0.0
    assert reported.interval_low > 0.0
    assert reported.interval_low <= reported.delta <= reported.interval_high
    assert len(reported.seeds) == len(SEEDS)


# ---------------------------------------------------------------------------
# NC-6, second case: no censoring, no delta
# ---------------------------------------------------------------------------


def test_a_training_frame_with_no_censored_row_is_unchanged_by_the_ablation() -> None:
    """The mechanism, before the fits that exercise it.

    `censoring_ignoring_frame` clears the censoring flag and, on the rows that
    carried it, the decision flag. On a frame with no censored row there is
    nothing to clear, so the comparator's frame is the aware frame value for
    value — which is *why* the delta below has to be zero, stated where it can be
    checked without sampling anything.
    """
    lines, split = uncensored_training_cohort()
    frame = training_frame(lines, split, VENDORS, CATEGORIES, AS_OF)
    ablated = censoring_ignoring_frame(frame)

    assert int(frame.is_censored.sum()) == 0
    assert (ablated.is_censored == frame.is_censored).all()
    assert (ablated.is_decision == frame.is_decision).all()
    assert (ablated.duration_days == frame.duration_days).all()
    assert (ablated.transition_index == frame.transition_index).all()


def test_the_cohort_still_has_open_lines_to_forecast_over() -> None:
    """The other precondition: the no-censoring case is not the empty case.

    "No censoring in the fit" and "no open line at all" are different inputs, and
    only the first is NC-6's. A cohort with nothing open would make the delta
    zero by having no forecast to take a median of, which would pass this file's
    second case while asserting nothing about the ablation.
    """
    lines, split = uncensored_training_cohort()
    open_lines = [line for line in lines if censoring_indicator(line, AS_OF)]
    held_out = {
        assignment.po_line_id
        for assignment in split.assignments
        if assignment.split_side == HELD_OUT
    }

    assert open_lines
    assert {line.po_line_id for line in open_lines} == held_out


def test_an_input_with_no_censoring_produces_a_delta_at_zero(uncensored_results) -> None:
    """NC-6's second case, and the one that makes the first case evidence.

    The two arms are the same graph at the same seed, so both arms sample the
    same posterior and condition the same open lines with the same uniforms:
    every per-seed delta is zero, and so is the reported one. A non-zero number
    here means the ablation is perturbing something other than the censoring
    contribution, and every passing SC-008 elsewhere would then be measuring
    that something instead.
    """
    reported = realized_delta(uncensored_results)

    for result, per_seed in zip(uncensored_results, reported.per_seed_deltas, strict=True):
        assert abs(per_seed) <= ZERO_DELTA_TOLERANCE, (
            f"seed {result.seed} reports a delta of {per_seed!r} on an input whose training "
            f"frame carries no censored row: {result.censoring_aware_median!r} against "
            f"{result.censoring_ignoring_median!r}. Removing a term that contributes nothing "
            f"cannot change the fit, so this is the ablation perturbing something else"
        )
    assert abs(reported.delta) <= ZERO_DELTA_TOLERANCE
    assert reported.interval_low == pytest.approx(0.0, abs=ZERO_DELTA_TOLERANCE)
    assert reported.interval_high == pytest.approx(0.0, abs=ZERO_DELTA_TOLERANCE)


def test_zero_is_reported_as_zero_rather_than_as_a_number_that_would_pass(
    uncensored_results,
) -> None:
    """The failing direction stated as such: zero must not read as a pass.

    The delta is compared against a floor derived from the input's own censoring
    bias, and an input with no censoring in its training split has none to
    derive. What must not happen is a "passing" delta arriving from a cohort
    where the censoring term did nothing at all — so the number reported is zero
    and not, for instance, the magnitude of the sampler's own disagreement
    between two independent runs.
    """
    reported = realized_delta(uncensored_results)

    assert reported.delta == pytest.approx(0.0, abs=ZERO_DELTA_TOLERANCE)
    assert reported.interval_high - reported.interval_low == pytest.approx(
        0.0, abs=ZERO_DELTA_TOLERANCE
    )
