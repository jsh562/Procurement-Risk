"""AD-001's multi-state sojourn model, assembled as a PyMC graph.

One lognormal per lifecycle transition, its log-location shifted by a partially
pooled vendor and material-category term, plus a rework-versus-forward
sub-model at the state machine's one decision point. FR-002's three covariates
enter here and nowhere else: the lifecycle state selects the sojourn stratum,
days-in-state is the truncation point of the open sojourn, and the
approval-cycle count is the rework covariate.

The index mapping comes from `design.py` and is never re-derived (A-002) — a
swapped mapping is shape-preserving, samples cleanly and attributes every
effect to the wrong vendor. A censored row contributes a survival and a
completed one a density, the epic's headline silent failure, so the expression
deciding it is extracted as `log_contribution_terms` and asserted against
`likelihood.py`. The group scales are half-t, not uniform: at twelve vendors τ
is weakly identified (`research.md` § Partial pooling).
"""

from __future__ import annotations

import math
import uuid
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

import numpy as np
import pymc as pm
import pytensor.tensor as pt
from numpy.typing import NDArray
from pytensor.tensor.variable import TensorVariable

from model.forecast.censoring import censoring_indicator
from model.forecast.config import LIFECYCLE_TRANSITIONS
from model.forecast.design import design_matrix
from model.forecast.read import LifecycleEventRow, LineRow
from model.forecast.split import TRAIN, SplitResult

__all__ = [
    "CATEGORY_DIM",
    "COVARIATES",
    "LOG_CONTRIBUTION",
    "REWORK_OBSERVATION",
    "REWORK_TARGET",
    "TRANSITION_DIM",
    "TRANSITION_KEYS",
    "VENDOR_DIM",
    "VENDOR_OFFSET",
    "VENDOR_OFFSET_Z",
    "ModelError",
    "SojournFrame",
    "build_model",
    "build_sojourn_frame",
    "covariate_names",
    "log_contribution_terms",
    "training_frame",
]


class ModelError(ValueError):
    """Raised when an input frame or a graph cannot be built as specified.

    A `ValueError`: every case is something the caller handed over — a line
    whose event walk contradicts itself, a held-out line offered to the training
    frame, or a roster with nothing to pool over. Named as its own type so a
    refusal is distinguishable from a PyMC shape error, and a `ValueError`
    subclass so it is catchable either way.
    """


# ---------------------------------------------------------------------------
# The state machine, read from `config.py` rather than restated
# ---------------------------------------------------------------------------

#: The dimension names the graph's dimensioned variables carry. The coordinate
#: *values* are what ArviZ flattens into `mu_sojourn[submitted__under_review]`
#: and `vendor_offset[VND-…]`, which is the form `config.MONITORED_PARAMETERS`
#: and `config.monitored_parameter_names` are written in — so the monitored set
#: can be compared against an `az.summary` index without a naming translation.
TRANSITION_DIM = "transition"
VENDOR_DIM = "vendor"
CATEGORY_DIM = "material_category"

#: `source__target` for each of the seven legal edges, in `config.py`'s order.
TRANSITION_KEYS: tuple[str, ...] = tuple(
    f"{source}__{target}" for source, target in LIFECYCLE_TRANSITIONS
)

#: The rework branch out of the decision state. A committed name rather than a
#: derivation: "which of two exits is the loop" is a domain fact, not a graph
#: property, and `_validated_topology` refuses at build time if the transition
#: set ever stops supporting it.
REWORK_TARGET = "revise_and_resubmit"

#: The named nodes a caller reaches into the graph for. The per-row log
#: contribution is a `Potential` rather than a `Deterministic` so it is summed
#: into the log-density without being stored once per draw.
LOG_CONTRIBUTION = "sojourn_log_contribution"
REWORK_OBSERVATION = "rework_observed"

#: The non-centered vendor hierarchy's two nodes (AD-012). `VENDOR_OFFSET_Z` is
#: the sampled unit-scale vector and `VENDOR_OFFSET` the `Deterministic` product
#: `tau_vendor · z` that the design matrix multiplies. Named because a caller
#: reaching into the graph now has to know that the second is derived — every
#: other monitored name in this module is a sampled parameter, and AD-006's set
#: assumed that of all of them until this decision.
VENDOR_OFFSET_Z = "vendor_offset_z"
VENDOR_OFFSET = "vendor_offset"

#: FR-002's three covariates under AD-001's mapping. `covariate_names` reports
#: which of them a given frame actually carries, which is the measurement
#: DV-036 compares `forecast_run.covariate_names` against.
COVARIATES: tuple[str, ...] = ("lifecycle_state", "days_in_state", "approval_cycle_count")


def _outgoing() -> dict[str, tuple[int, ...]]:
    """Each source state's outgoing transitions, as positions in `config.py`'s order."""
    states: dict[str, list[int]] = {}
    for position, (source, _) in enumerate(LIFECYCLE_TRANSITIONS):
        states.setdefault(source, []).append(position)
    return {source: tuple(positions) for source, positions in states.items()}


OUTGOING: dict[str, tuple[int, ...]] = _outgoing()


def _validated_topology() -> tuple[str, int, int]:
    """The decision state and its two branch positions, or a refusal.

    The mixture below marginalises an open sojourn over the exits its state
    admits, and the rework sub-model is a Bernoulli over one of them. Both are
    written for *one* two-way decision point, which is what the seven legal
    edges describe. An eighth edge would leave that arithmetic quietly wrong
    rather than broken — a state with three exits would have one of them
    dropped from the mixture — so the shape is checked instead of assumed.
    """
    branching = [source for source, exits in OUTGOING.items() if len(exits) > 1]
    if len(branching) != 1:
        raise ModelError(
            f"the sojourn model is written for exactly one branching lifecycle state, "
            f"found {sorted(branching)}. The censored mixture and the rework Bernoulli "
            f"both range over a two-way decision, and a different topology would drop "
            f"exits from the mixture without changing any shape"
        )
    decision = branching[0]
    exits = OUTGOING[decision]
    if len(exits) != 2:
        raise ModelError(
            f"the decision state {decision!r} has {len(exits)} exits; the "
            f"rework-versus-forward sub-model is a Bernoulli over two"
        )
    rework = [position for position in exits if LIFECYCLE_TRANSITIONS[position][1] == REWORK_TARGET]
    if len(rework) != 1:
        raise ModelError(
            f"no single exit of {decision!r} reaches {REWORK_TARGET!r}, so there is no "
            f"branch for the rework probability to be the probability of"
        )
    forward = next(position for position in exits if position != rework[0])
    return decision, forward, rework[0]


# ---------------------------------------------------------------------------
# Priors. Weakly informative, chosen from the durations' own scale and fixed
# here — not adjusted after a diagnostic was seen (FR-028's discipline applied
# to the thing the split constants apply it to).
# ---------------------------------------------------------------------------

#: A sojourn leg of about a week on the log-day scale, with a prior wide enough
#: to admit anything from a same-day step to most of a year.
PRIOR_POPULATION_LOCATION_MU = 2.0
PRIOR_POPULATION_LOCATION_SD = 1.5

#: How far one transition's intercept may sit from the population location.
#: A fixed scale rather than a fitted one, so the monitored set stays exactly
#: the set `config.py` enumerates (AD-006).
PRIOR_TRANSITION_LOCATION_SD = 1.5

#: Half-normal on each transition's log-scale.
PRIOR_SOJOURN_SCALE_SD = 1.0

#: Half-t rather than uniform on the two group scales: at twelve vendors and
#: twenty categories τ is weakly identified and a uniform puts its mass where
#: the data cannot answer (`research.md` § Partial pooling).
PRIOR_GROUP_SCALE_NU = 4.0
PRIOR_GROUP_SCALE_SD = 0.5

#: The rework sub-model on the logit scale, centred on no information.
PRIOR_REWORK_INTERCEPT_SD = 1.5
PRIOR_REWORK_BETA_SD = 1.0

_LOG_TWO_PI = math.log(2.0 * math.pi)
_SQRT_TWO = math.sqrt(2.0)

#: A hierarchy of one member pools over nothing: `ZeroSumNormal` would pin its
#: single offset at zero and τ would be identified by its prior alone.
MIN_HIERARCHY_MEMBERS = 2


# ---------------------------------------------------------------------------
# The input frame: one row per sojourn
# ---------------------------------------------------------------------------


# `eq=False` because the fields are arrays and a generated `__eq__` would
# compare elementwise, yielding an array where a bool is expected.
@dataclass(frozen=True, slots=True, eq=False)
class SojournFrame:
    """The fit's rows: one per sojourn, completed or currently open.

    Every array is aligned on the same row axis, and `po_line_ids` names the
    line each row came from — which is what makes FR-007 checkable over the
    fit's own input rather than over the database (DV-008).

    `transition_index` is the realized edge for a completed row. A censored row
    has not taken an edge yet, so its entry repeats `forward_transition_index`:
    the density branch is still evaluated for it and then discarded, and a
    sentinel would be an out-of-bounds index into the parameter vector.
    """

    po_line_ids: tuple[uuid.UUID, ...]
    duration_days: NDArray[np.float64]
    is_censored: NDArray[np.bool_]
    transition_index: NDArray[np.int64]
    forward_transition_index: NDArray[np.int64]
    rework_transition_index: NDArray[np.int64]
    is_decision: NDArray[np.bool_]
    reworked: NDArray[np.bool_]
    approval_cycle_count: NDArray[np.float64]
    design: NDArray[np.float64]
    vendor_ids: tuple[str, ...]
    material_categories: tuple[str, ...]
    excluded_po_line_ids: tuple[uuid.UUID, ...]

    @property
    def row_count(self) -> int:
        """How many sojourns the fit sees — completed and censored together."""
        return int(self.duration_days.size)

    @property
    def decision_rows(self) -> NDArray[np.int64]:
        """The completed decision points, which are the rework Bernoulli's data.

        A censored decision point is deliberately absent: its branch has not
        been chosen, so observing one would invent the outcome the mixture
        exists to marginalise over.
        """
        return np.flatnonzero(self.is_decision & ~self.is_censored).astype(np.int64)


def _occurred_on(event: LifecycleEventRow) -> date:
    """The calendar day an event happened on, in UTC — `censoring.py`'s rule."""
    moment = event.occurred_at
    if moment.tzinfo is not None and moment.utcoffset() is not None:
        return moment.astimezone(UTC).date()
    return moment.date()


def _checked_anchor(as_of_date: date) -> None:
    """An as-of date is a calendar day, never an instant.

    `datetime` subclasses `date`, so an accidental one would pass a type test
    and then decide a boundary case — an event on the as-of date — by an hour
    nobody supplied. The same refusal `censoring.py` makes, made before the
    walk rather than once per line.
    """
    if isinstance(as_of_date, datetime) or not isinstance(as_of_date, date):
        raise ModelError(
            f"an as-of date is a `datetime.date`, found {type(as_of_date).__name__}; the "
            f"run's anchor is a calendar day, and an instant would move the truncation "
            f"point of every open sojourn by a fraction no caller stated"
        )


def _observed_events(line: LineRow, as_of_date: date) -> tuple[LifecycleEventRow, ...]:
    """The line's events at or before the anchor, in the order they happened.

    `read.py` returns them in `sequence_no` order and the ordering is re-checked
    here rather than trusted: a walk read out of order produces negative
    sojourns, which `likelihood.py` refuses far from the line that caused it.
    """
    ordered = tuple(sorted(line.events, key=lambda event: event.sequence_no))
    if ordered != tuple(line.events):
        raise ModelError(
            f"line {line.natural_key} carries its events out of `sequence_no` order; the "
            f"sojourn walk reads consecutive pairs, so a reordered history would measure "
            f"legs between events that never followed one another"
        )
    return tuple(event for event in ordered if _occurred_on(event) <= as_of_date)


def _line_rows(
    line: LineRow, as_of_date: date, decision: str, forward: int, rework: int
) -> list[dict[str, object]]:
    """One line's sojourns: every completed leg, then the open one if it has one.

    The completed legs are consecutive event pairs. The open leg exists exactly
    when `censoring.py` says the line is censored at the anchor — the same
    authority the split's stratum is drawn from, so the fit and the split cannot
    disagree about which lines are open.
    """
    observed = _observed_events(line, as_of_date)
    if not observed:
        return []

    rows: list[dict[str, object]] = []
    loops = 0
    for start, end in zip(observed, observed[1:], strict=False):
        source, target = start.to_state, end.to_state
        if end.from_state is not None and end.from_state != source:
            raise ModelError(
                f"line {line.natural_key} leaves {source!r} by an event recorded as coming "
                f"from {end.from_state!r}; the walk contradicts itself and the leg between "
                f"them measures nothing"
            )
        position = _transition_position(line, source, target)
        rows.append(
            _row(
                line=line,
                duration=(_occurred_on(end) - _occurred_on(start)).days,
                censored=False,
                transition=position,
                source=source,
                cycles=loops,
                reworked=target == REWORK_TARGET,
                decision=decision,
                forward=forward,
                rework=rework,
            )
        )
        if target == REWORK_TARGET:
            loops += 1

    if censoring_indicator(line, as_of_date):
        last = observed[-1]
        rows.append(
            _row(
                line=line,
                duration=(as_of_date - _occurred_on(last)).days,
                censored=True,
                transition=None,
                source=last.to_state,
                cycles=loops,
                reworked=False,
                decision=decision,
                forward=forward,
                rework=rework,
            )
        )
    return rows


def _transition_position(line: LineRow, source: str, target: str) -> int:
    """Where `(source, target)` sits in `config.LIFECYCLE_TRANSITIONS`."""
    try:
        return LIFECYCLE_TRANSITIONS.index((source, target))
    except ValueError as exc:
        raise ModelError(
            f"line {line.natural_key} walks {source!r} → {target!r}, which is not one of "
            f"the seven legal edges. `fn_is_legal_lifecycle_transition` makes that "
            f"unrepresentable, so the two reads saw different databases"
        ) from exc


def _row(
    *,
    line: LineRow,
    duration: int,
    censored: bool,
    transition: int | None,
    source: str,
    cycles: int,
    reworked: bool,
    decision: str,
    forward: int,
    rework: int,
) -> dict[str, object]:
    """One row's fields, with its state's exits resolved to parameter positions."""
    exits = OUTGOING.get(source)
    if not exits:
        raise ModelError(
            f"line {line.natural_key} sits in {source!r}, which has no outgoing legal "
            f"transition; a terminal state cannot hold an open sojourn, so the line's "
            f"censoring status and its event walk disagree"
        )
    if duration < 0:
        raise ModelError(
            f"line {line.natural_key} produced a sojourn of {duration} days out of "
            f"{source!r}; elapsed time before the leg began is a caller defect rather "
            f"than a value, and the lognormal has no support there"
        )
    at_decision = source == decision
    primary = forward if at_decision else exits[0]
    return {
        "po_line_id": line.po_line_id,
        "line": line,
        "duration": float(duration),
        "censored": censored,
        "transition": primary if transition is None else transition,
        "forward": primary,
        "rework": rework if at_decision else primary,
        "is_decision": at_decision,
        "reworked": reworked,
        "cycles": float(cycles),
    }


def build_sojourn_frame(
    lines: Iterable[LineRow],
    vendor_ids: Sequence[str],
    material_categories: Sequence[str],
    as_of_date: date,
) -> SojournFrame:
    """Every line's sojourns at the anchor, with the design matrix `design.py` builds.

    The matrix is built by calling `design_matrix` over the *rows* — one line
    repeated once per sojourn it contributed — rather than by expanding a
    per-line matrix here. Re-deriving the expansion would be a second opinion
    about which column belongs to which vendor, which is exactly the
    shape-preserving mis-index A-002 records (`design.py` § docstring).

    A line with no event at or before the anchor has not started yet; it is
    excluded and named in `excluded_po_line_ids` rather than dropped silently,
    because a line that vanished from the fit is otherwise indistinguishable
    from one that never existed (Principle III).
    """
    _checked_anchor(as_of_date)
    decision, forward, rework = _validated_topology()

    rows: list[dict[str, object]] = []
    excluded: list[uuid.UUID] = []
    for line in lines:
        produced = _line_rows(line, as_of_date, decision, forward, rework)
        if produced:
            rows.extend(produced)
        else:
            excluded.append(line.po_line_id)

    if not rows:
        raise ModelError(
            "the sojourn frame is empty; every line supplied had no lifecycle event at or "
            "before the as-of date, so there is no leg for any transition's lognormal to "
            "be fitted on"
        )

    def column(key: str, dtype: type) -> NDArray[np.generic]:
        return np.array([row[key] for row in rows], dtype=dtype)

    return SojournFrame(
        po_line_ids=tuple(row["po_line_id"] for row in rows),  # type: ignore[misc]
        duration_days=column("duration", float).astype(np.float64),
        is_censored=column("censored", bool).astype(np.bool_),
        transition_index=column("transition", np.int64).astype(np.int64),
        forward_transition_index=column("forward", np.int64).astype(np.int64),
        rework_transition_index=column("rework", np.int64).astype(np.int64),
        is_decision=column("is_decision", bool).astype(np.bool_),
        reworked=column("reworked", bool).astype(np.bool_),
        approval_cycle_count=column("cycles", float).astype(np.float64),
        design=design_matrix((row["line"] for row in rows), vendor_ids, material_categories),
        vendor_ids=tuple(vendor_ids),
        material_categories=tuple(material_categories),
        excluded_po_line_ids=tuple(excluded),
    )


def training_frame(
    lines: Iterable[LineRow],
    split: SplitResult,
    vendor_ids: Sequence[str],
    material_categories: Sequence[str],
    as_of_date: date,
) -> SojournFrame:
    """The frame FR-007 permits: the `train` side, filtered before the matrix is built.

    Structural rather than conventional. A caller cannot hand the fit a
    held-out line by forgetting a filter, because the filter is the
    constructor: the assignment decides membership and an unassigned line is
    refused rather than assumed to be training data.
    """
    sides = {assignment.po_line_id: assignment.split_side for assignment in split.assignments}
    rows = tuple(lines)
    unassigned = [line.natural_key for line in rows if line.po_line_id not in sides]
    if unassigned:
        raise ModelError(
            f"{len(unassigned)} line(s) carry no split assignment — first {unassigned[0]}. "
            f"FR-007 is a claim about every line, so an unassigned one cannot be admitted "
            f"to the fit on the assumption that it is training data"
        )
    training = tuple(line for line in rows if sides[line.po_line_id] == TRAIN)
    if not training:
        raise ModelError(
            "the split assigned no line to the training side, so the fit would have no "
            "observation and every parameter would be its prior"
        )
    return build_sojourn_frame(training, vendor_ids, material_categories, as_of_date)


def covariate_names(frame: SojournFrame) -> tuple[str, ...]:
    """Which of FR-002's three covariates this frame actually carries.

    A measurement rather than a label (DV-036). Under AD-001 each covariate has
    one way of entering, and each is reported present only when that way is
    exercised: the lifecycle state has to select between at least two strata,
    days-in-state has to truncate at least one open sojourn at a positive
    elapsed time, and the approval-cycle count needs a decision point for its
    coefficient to be a coefficient of anything.
    """
    entered = []
    if np.unique(frame.transition_index).size > 1:
        entered.append("lifecycle_state")
    if bool(np.any(frame.is_censored & (frame.duration_days > 0.0))):
        entered.append("days_in_state")
    if frame.decision_rows.size > 0:
        entered.append("approval_cycle_count")
    return tuple(name for name in COVARIATES if name in entered)


# ---------------------------------------------------------------------------
# The likelihood expression, extracted so `likelihood.py` can be its oracle
# ---------------------------------------------------------------------------


def log_contribution_terms(
    frame: SojournFrame,
    mu_sojourn: TensorVariable,
    sigma_sojourn: TensorVariable,
    group_offsets: TensorVariable,
    rework_intercept: TensorVariable,
    rework_approval_cycle_beta: TensorVariable,
) -> TensorVariable:
    """Per-row log contribution: a density if completed, a survival if censored.

    `likelihood.py`'s arithmetic in PyTensor, term for term — the closed-form
    `½·erfc(z/√2)` survival rather than `1 − Φ(z)`, and `S(0) = 1` exactly, so a
    leg censored on the day it began is not penalised for existing.

    An open sojourn out of the decision state has not chosen its exit, so its
    survival is the two-branch mixture `(1−p)·S_forward(d) + p·S_rework(d)` with
    `p` from the rework sub-model. Every other state has one exit and the
    mixture collapses to it. The branch is a separate factor rather than part of
    this one, so a *completed* leg contributes its density here and its realized
    branch through the Bernoulli — a joint, not a double count.
    """
    positive = frame.duration_days > 0.0
    # Censored rows may sit at zero and their contribution is fixed below, so the
    # substitute keeps `log` out of its invalid domain without changing a value
    # that is used. `likelihood.py` makes the same substitution in the same place.
    log_t = np.log(np.where(positive, frame.duration_days, 1.0))
    group = pt.dot(pt.as_tensor(frame.design), group_offsets)

    def log_survival(at: NDArray[np.int64]) -> TensorVariable:
        z = (log_t - (mu_sojourn[at] + group)) / sigma_sojourn[at]
        survives = pt.log(0.5 * pt.erfc(z / _SQRT_TWO))
        return pt.where(positive, survives, 0.0)

    own = frame.transition_index
    z_own = (log_t - (mu_sojourn[own] + group)) / sigma_sojourn[own]
    log_density = -log_t - pt.log(sigma_sojourn[own]) - 0.5 * _LOG_TWO_PI - 0.5 * pt.sqr(z_own)

    logit = rework_intercept + rework_approval_cycle_beta * frame.approval_cycle_count
    # `log σ(η)` and `log(1−σ(η))` through softplus: the direct spelling loses the
    # whole tail once the logit is a few units from zero, and that tail is where
    # a line with several approval cycles sits.
    log_rework = -pt.softplus(-logit)
    log_forward = -pt.softplus(logit)

    survives_forward = log_survival(frame.forward_transition_index)
    survives_rework = log_survival(frame.rework_transition_index)
    mixed = pt.logaddexp(log_forward + survives_forward, log_rework + survives_rework)
    survives = pt.where(frame.is_decision, mixed, survives_forward)

    return pt.where(frame.is_censored, survives, log_density)


# ---------------------------------------------------------------------------
# The graph
# ---------------------------------------------------------------------------


def build_model(frame: SojournFrame) -> pm.Model:
    """AD-001's graph over one training frame.

    The parameter names are `config.MONITORED_PARAMETERS`' names and the
    coordinate values are what ArviZ flattens them by, so the fitted set and the
    monitored set are one enumeration rather than two that agree today.

    Both hierarchies are zero-sum. Plain offsets would leave an exact additive
    ridge — add a constant to every vendor offset, subtract it from every
    transition intercept, and the likelihood is unchanged — which the priors
    identify only weakly and which shows up as an unconverged `mu_population`
    rather than as an error. Constraining the offsets to sum to zero removes the
    ridge instead of asking the sampler to negotiate it.

    The **vendor** hierarchy is additionally non-centered (AD-012):
    `vendor_offset = tau_vendor · z` with `z ~ ZeroSumNormal(1)`, so the sampled
    coordinates are `z` and `tau_vendor` and `vendor_offset` is a
    `Deterministic`. `tau_vendor` posts a mean near 0.15 against residual
    log-scales of 0.71–1.13, which puts the vendor level at the neck of a funnel
    a centered parameterization occasionally sticks a chain in. The zero-sum
    constraint is kept: it is what removes the additive ridge, and that reason is
    unchanged by the change of coordinates.

    **The category hierarchy stays centered, and that was measured rather than
    assumed.** `tau_category` posts near 0.22 — only about 1.5× `tau_vendor`, so
    the scales alone do not settle it — but across the same six seeds the
    centered category level breached nothing in either parameterization, and
    non-centering it as well costs efficiency for no gain: E-BFMI minimum falls
    from 0.76–0.87 to 0.69–0.77, `tau_category`'s worst bulk ESS falls from 2,310
    to 1,386, and maximum tree depth rises from 6 to 7. Twenty categories against
    twelve vendors is the likely reason — the category level is the better
    identified of the two, and non-centering a level the data already identifies
    trades one funnel for its mirror image.
    """
    _validated_topology()
    if len(frame.vendor_ids) < MIN_HIERARCHY_MEMBERS:
        raise ModelError(
            f"the vendor hierarchy has {len(frame.vendor_ids)} member(s); partial pooling "
            f"over one group pools over nothing and τ would be identified by its prior "
            f"alone, which is a posterior about the prior"
        )
    if len(frame.material_categories) < MIN_HIERARCHY_MEMBERS:
        raise ModelError(
            f"the material-category hierarchy has {len(frame.material_categories)} "
            f"member(s); partial pooling over one group pools over nothing"
        )

    coords = {
        TRANSITION_DIM: TRANSITION_KEYS,
        VENDOR_DIM: frame.vendor_ids,
        CATEGORY_DIM: frame.material_categories,
    }
    with pm.Model(coords=coords) as model:
        mu_population = pm.Normal(
            "mu_population", mu=PRIOR_POPULATION_LOCATION_MU, sigma=PRIOR_POPULATION_LOCATION_SD
        )
        tau_vendor = pm.HalfStudentT(
            "tau_vendor", nu=PRIOR_GROUP_SCALE_NU, sigma=PRIOR_GROUP_SCALE_SD
        )
        tau_category = pm.HalfStudentT(
            "tau_category", nu=PRIOR_GROUP_SCALE_NU, sigma=PRIOR_GROUP_SCALE_SD
        )
        # AD-012. `z` is sampled at unit scale and `tau_vendor` multiplies it
        # afterwards, so the two are a priori independent and the sampler never
        # has to traverse a neck whose width is one of its own coordinates. Both
        # are monitored alongside the product: R-hat on the product can look
        # healthy while the `z` it derives from has not mixed.
        vendor_offset_z = pm.ZeroSumNormal(VENDOR_OFFSET_Z, sigma=1.0, dims=VENDOR_DIM)
        vendor_offset = pm.Deterministic(
            VENDOR_OFFSET, tau_vendor * vendor_offset_z, dims=VENDOR_DIM
        )
        category_offset = pm.ZeroSumNormal("category_offset", sigma=tau_category, dims=CATEGORY_DIM)
        mu_sojourn = pm.Normal(
            "mu_sojourn",
            mu=mu_population,
            sigma=PRIOR_TRANSITION_LOCATION_SD,
            dims=TRANSITION_DIM,
        )
        sigma_sojourn = pm.HalfNormal(
            "sigma_sojourn", sigma=PRIOR_SOJOURN_SCALE_SD, dims=TRANSITION_DIM
        )
        rework_intercept = pm.Normal("rework_intercept", mu=0.0, sigma=PRIOR_REWORK_INTERCEPT_SD)
        rework_beta = pm.Normal("rework_approval_cycle_beta", mu=0.0, sigma=PRIOR_REWORK_BETA_SD)

        # Vendor block first, then category block — `design.py`'s column order,
        # which is the one thing this module must not have an opinion of its own
        # about.
        group_offsets = pt.concatenate([vendor_offset, category_offset])
        pm.Potential(
            LOG_CONTRIBUTION,
            log_contribution_terms(
                frame,
                mu_sojourn,
                sigma_sojourn,
                group_offsets,
                rework_intercept,
                rework_beta,
            ),
        )

        decided = frame.decision_rows
        if decided.size:
            pm.Bernoulli(
                REWORK_OBSERVATION,
                logit_p=(rework_intercept + rework_beta * frame.approval_cycle_count[decided]),
                observed=frame.reworked[decided].astype(np.int8),
            )
    return model
