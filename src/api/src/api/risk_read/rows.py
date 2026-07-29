"""Building a ranked row's figures.

FR-003, FR-004, FR-005, FR-006, FR-009, FR-017, FR-020, FR-027, FR-030,
FR-041, FR-053, FR-054.

This module is where the stored artifacts become the four comparison quantities
a coordinator compares rows on — and where three things are *withheld*, each for
a reason worth stating:

**The draws never leave.** FR-053. A client holding four thousand draws is one
`mean()` away from the point estimate this product exists to refuse. What
crosses the boundary is a quantile pair and a rounded probability, both already
finished.

**No probability is rendered as a float.** FR-008 fixes the display at whole
percent with half-up rounding and a subtracted complement; a raw float invites
the client to round it, and two independent roundings of 0.4951 and 0.5049 give
a mandated pair summing to 101. The arithmetic happens once, here, where it is
unit-tested.

**Expected harm is not on the row.** FR-041. With criticality displayed beside
it, publishing the score would surrender the mean overrun to one division — and
`need_by + mean_overrun` is a mean delivery date, which is exactly the single
predicted date FR-007 forbids. The score orders the list; the *rank* is how the
ordering reaches the coordinator (FR-048).

**Which measure a probability is travels with it.** FR-017. A point read from
the survival grid and an upper bound on the residual tail are rendered in
different words, and an unmarked figure is one refactor from being rendered as
a point.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from math import ceil
from typing import Any, Final

from api.compute.probability import PercentFigure, complement, percent_figure
from api.risk_read.query import Conventions, OpenLine
from api.risk_read.states import ResolvedLine, RowState

__all__ = [
    "MEASURE_POINT",
    "MEASURE_UPPER_BOUND",
    "QUANTILE_PERCENTS",
    "RowInputs",
    "build_primary",
    "build_secondary",
    "calendar_margin_days",
    "identity",
    "miss_probability",
    "need_by",
    "quantile_days",
]

MEASURE_POINT: Final[str] = "point"
MEASURE_UPPER_BOUND: Final[str] = "upper_bound"

#: FR-004's pair. Fixed, and not a parameter: an interface that could ask for a
#: third quantile could ask for one, and one quantile standing alone is a point
#: estimate with extra steps (FR-041).
QUANTILE_PERCENTS: Final[tuple[int, int]] = (50, 80)


@dataclass(frozen=True)
class RowInputs:
    """What one ranked row is built from."""

    resolved: ResolvedLine
    as_of_date: date
    horizon_days: int
    conventions: Conventions
    today: date
    #: FR-003. The draw count of the run this row's figures were computed from.
    #: Distinct from `conventions.draw_count`, which comes from
    #: `schema_constants` and describes the shape the schema declares. Nothing
    #: constrains the two to agree — `forecast_run.draw_count` carries only its
    #: own positivity check — so publishing the schema's number beside a figure
    #: computed from a differently-sized run would state a denominator that
    #: figure never had. The figure's copy is authoritative (FR-003).
    run_draw_count: int = 0


def quantile_days(draws: tuple[float, ...], percentile: int) -> int:
    """The quantile in whole days, nearest-rank, one-based, no interpolation.

    The convention is `schema_constants`' and is published beside the figure
    (FR-003), because a median read by a different rule is a different number
    and nothing on the screen would say so. Whole days because the row states
    days: a quantile of 34.6 days is not a more precise answer, it is the same
    answer with a decimal that implies the model resolves hours.
    """
    if not draws:
        raise ValueError("a quantile of no draws is not a smaller number, it is no number")
    rank = max(1, ceil(percentile * len(draws) / 100))
    return int(round(draws[rank - 1]))


def calendar_margin_days(need_by: date, as_of: date) -> int:
    """Days from the run's anchor to the need-by date.

    FR-009. Negative where the need-by date precedes the as-of date, and it
    takes **no forecast input** — deliberately. A margin derived from a
    predicted delivery date would let a reader subtract it from the need-by date
    and reconstruct that date, defeating FR-007 through arithmetic rather than
    through a field.
    """
    return (need_by - as_of).days


def miss_probability(inputs: RowInputs) -> dict[str, Any] | None:
    """The probability of missing the need-by date, in both directions.

    Returns ``None`` where the row's state withholds it — which under FR-054 is
    an *explicit empty* rather than a structural absence, because the figure
    exists in the general case and this row's state removed it. The state
    travels beside it in the row, so no consumer has to infer which state
    suppressed the figure from the bare fact that it is missing.

    Two states withhold it, for opposite reasons:

    - ``already_late`` (FR-030): the probability is 1 by construction and
      therefore says nothing. The quantile pair is kept, because how much
      *further* slip is coming is the question that remains open.
    - the states that suppress every figure: there is nothing to withhold.
    """
    line = inputs.resolved.line
    state = inputs.resolved.state

    if inputs.resolved.suppresses_figures or state is RowState.ALREADY_LATE:
        return None

    offset = (line.effective_need_by_date - inputs.as_of_date).days

    if state is RowState.BEYOND_HORIZON:
        # FR-017. The grid stops at the horizon, so only the residual tail mass
        # is available — and only as a bound. Never extrapolated past where the
        # model was fitted.
        if line.residual_tail_mass is None:
            raise ValueError(
                "A line beyond the horizon with no residual tail mass cannot state a bound. "
                "`ck_line_posterior__residual_matches_grid_tail` makes this unreachable from "
                "storage, so it is a defect in the read rather than in the data."
            )
        return _both_directions(line.residual_tail_mass, measure=MEASURE_UPPER_BOUND)

    if line.survival is None:  # pragma: no cover - suppressed states return above
        raise ValueError("a covered line has a survival array by construction")

    # One-based over `k = 1..horizon_days`, so the offset indexes at `- 1`. The
    # value is read directly: `survival[k]` is P(not delivered by day k), which
    # *is* the probability of missing a need-by at offset k. No complement.
    return _both_directions(line.survival[offset - 1], measure=MEASURE_POINT)


def _both_directions(stored: float, *, measure: str) -> dict[str, Any]:
    """FR-006's pair, in a fixed order with the chance of missing first."""
    miss = percent_figure(stored)
    on_time = complement(miss)
    return {
        "measure": measure,
        "bounded": miss.bounded,
        "miss": _percent(miss),
        "on_time": _percent(on_time),
    }


def _percent(figure: PercentFigure) -> dict[str, Any]:
    """FR-053's encoding: the rounded integer and the finished display string.

    Never the stored value — the interface has no second rounding to perform,
    and cannot invent one.
    """
    return {"percent": figure.percent, "display": figure.display}


def _duration_pair(inputs: RowInputs) -> dict[str, Any]:
    """FR-004's single labelled pair.

    Nested under one object rather than carried as two sibling scalars, which is
    what makes "not independently sortable" structural rather than a rule the
    interface has to remember. Both members are always present, including on a
    near-degenerate posterior where they are equal: the contract offers no way
    to collapse them to one figure, because one quantile standing alone is the
    point estimate this product refuses.

    `counted_from` and `as_of_date` travel on every pair because an unanchored
    median of thirty days on a ten-day-old run reads ten days more optimistic
    than it is.
    """
    line = inputs.resolved.line
    assert line.draws is not None  # a ranked, non-suppressed row has draws

    return {
        "unit": "days",
        "counted_from": "run_as_of_date",
        "as_of_date": inputs.as_of_date.isoformat(),
        "median": _quantile(line, 50),
        "eightieth": _quantile(line, 80),
        "reference_class": {
            "basis": "posterior_predictive_draws",
            # The run's own count, not `schema_constants`'. This describes the
            # draws behind *this* figure.
            "draw_count": inputs.run_draw_count,
            "percentile_convention": inputs.conventions.percentile_convention,
        },
    }


def _quantile(line: OpenLine, percentile: int) -> dict[str, Any]:
    """One quantile with FR-005's complementary frequency.

    `later_percent` is what turns "the median is 34 days" into "half of
    comparable orders land by this day" — a proportion of a population rather
    than a commitment about this one line, which is the reading a bare quantile
    invites and the research says non-experts actually make.
    """
    assert line.draws is not None
    return {
        "quantile_percent": percentile,
        "days": quantile_days(line.draws, percentile),
        "later_percent": 100 - percentile,
    }


def identity(line: OpenLine) -> dict[str, Any]:
    """FR-027's identity quantity, carried as one unit.

    Vendor, manufacturer, part number and material category are deliberately
    absent: no requirement needs them, and a field present in the payload is a
    column waiting to be added against FR-027's cap.

    Shared with the unranked row rather than written twice — an excluded line
    carries the *same* identity, and two copies of this shape would drift.
    """
    return {
        "project_id": line.project_id,
        "po_number": line.po_number,
        "line_number": line.line_number,
        "description": line.description,
    }


def need_by(line: OpenLine) -> dict[str, Any]:
    """The effective need-by date and the recorded value it would replace.

    Both travel even with no override in force, so an adjustment is auditable in
    the payload rather than only on screen, and `unsaved` gives FR-031's
    "visibly marked as unsaved" a field to bind to. Until US2 the two dates are
    equal and the source is the record.
    """
    return {
        "date": line.effective_need_by_date.isoformat(),
        "date_of_record": line.need_by_date.isoformat(),
        "source": "session_override" if line.is_adjusted else "record",
        "unsaved": line.is_adjusted,
    }


def build_primary(inputs: RowInputs) -> dict[str, Any]:
    """FR-027's four comparison quantities, in FR-032's reading order.

    Identity, need-by date, miss probability, quantile pair — the same order in
    the payload, in the rendered document, and in the accessibility tree's
    traversal, so a coordinator hearing the row and one seeing it meet it in the
    same sequence.
    """
    return {
        "identity": identity(inputs.resolved.line),
        "need_by": need_by(inputs.resolved.line),
        "miss_probability": miss_probability(inputs),
        "duration_pair": _duration_pair(inputs),
    }


def build_secondary(inputs: RowInputs) -> dict[str, Any]:
    """FR-009's explanatory context — exactly the three items FR-027 admits.

    The as-of date, the criticality, and the calendar margin: the two inputs to
    the harm score that are not the distribution, plus the frame everything is
    counted from. The score itself is absent under FR-041, and the set is closed
    at three because FR-027 says "the as-of date, the criticality, and the
    calendar margin -- in the secondary region and nothing else", and that
    closure is the counting procedure SC-014 is evaluated by.

    **FR-029's row-level stale mark is not a member here, and that is the
    requirement rather than an omission.** An earlier revision added an
    ``as_of_is_stale`` boolean and amended the contract to admit a fourth
    member. FR-029 states the mark "needs no new figure and *no new field*; the
    response already carries the run's staleness and each row's as-of date" —
    so the interface composes it from ``meta.forecast_run.stale`` and the
    ``as_of_date`` on this row. Adding the field and then widening the contract
    to fit was moving the boundary to match the implementation, which is the
    direction this codebase refuses elsewhere.
    """
    line = inputs.resolved.line
    return {
        "as_of_date": inputs.as_of_date.isoformat(),
        "criticality": line.criticality,
        "calendar_margin_days": calendar_margin_days(
            line.effective_need_by_date, inputs.as_of_date
        ),
    }
