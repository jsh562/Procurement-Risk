"""`forecast-fit`: read, hash, split, fit, condition, write, publish.

The offline console entry point of {SAD:ADR-0011}. **Standard output carries
exactly one line — the `run_id` — and nothing on a refusal**; every diagnostic
goes to standard error, and the exit status is zero exactly on completion
(FR-039).

Two gates, at two points, leaving two different kinds of evidence. The
**pre-sampling preconditions** (T081) refuse before the sampler runs, so nothing
is sampled — FR-035's four-chain minimum and FR-021's zero-open-line case. The
**post-sampling diagnostics gate** (T082) refuses after sampling and before the
first statement, so a sample was taken and nothing was written (FR-017). A run
can breach one while satisfying the other, which is why they are two obligations
and not one clause. Both are ordering rather than rollback (AD-010): no
statement is issued on either refusing path, so there is nothing to roll back.
Every refusal also emits a report file (FR-037), which is not a write to any
store SC-015 quantifies over.
"""

from __future__ import annotations

import argparse
import dataclasses
import math
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

import numpy as np
from numpy.typing import NDArray
from sqlalchemy import Engine, create_engine

from model.forecast.ablation import (
    MINIMUM_ABLATION_SEEDS,
    AblationError,
    SeedResult,
    kaplan_meier_floor,
    realized_delta,
)
from model.forecast.censoring import CensoringError, censoring_indicator, elapsed_days
from model.forecast.config import (
    CHAINS,
    CHAINS_MIN,
    DRAWS_PER_CHAIN,
    LIFECYCLE_TRANSITIONS,
    TUNING_DRAWS_PER_CHAIN,
    ConfigError,
    RunShape,
    monitored_parameter_names,
    read_run_shape,
)
from model.forecast.design import DesignError, category_index, vendor_index
from model.forecast.diagnostics import (
    DiagnosticRow,
    DiagnosticsError,
    blocking_breaches,
    evaluate_diagnostics,
    refusal_lines,
)
from model.forecast.manifest import (
    POPULATION_RANK_HELD_OUT_PREDICTION,
    POPULATION_RANK_LINE_POSTERIOR,
    VENDOR_SHRINKAGE_HDI_PROBABILITY,
    ArtifactDigest,
    ManifestError,
    artifact_hash_over,
    build_manifest,
    draw_digest,
    read_fixture_provenance,
)
from model.forecast.model import (
    CATEGORY_DIM,
    TRANSITION_KEYS,
    VENDOR_DIM,
    VENDOR_OFFSET,
    ModelError,
    SojournFrame,
    build_model,
    covariate_names,
    training_frame,
)
from model.forecast.paths import ForecastPathError
from model.forecast.posterior import (
    PosteriorError,
    conditional_remaining_draws,
    survival_grid,
    total_duration_draws,
)
from model.forecast.read import LineRow, ProcurementInput, ReadError, read_lines_and_events
from model.forecast.report import (
    AblationOutcome,
    RefusedAttempt,
    ReportError,
    SampledShape,
    UnmetPrecondition,
    write_refusal_report,
    write_run_report,
)
from model.forecast.sample import SampleError, sample_posterior
from model.forecast.serialize import SerializeError, input_data_hash
from model.forecast.shrinkage import ShrinkageError, VendorShrinkage, vendor_shrinkage
from model.forecast.split import HELD_OUT, TRAIN, SplitError, SplitResult, assign_split
from model.forecast.write import (
    HeldOutPredictionRow,
    LinePosteriorRow,
    WriteError,
    write_artifact_set,
)
from model.procurement.lifecycle import state_sequence
from model.schema.url import DatabaseUrlNotConfiguredError, get_database_url

if TYPE_CHECKING:  # pragma: no cover - imported for the annotation only
    import xarray as xr

__all__ = [
    "ABLATION_CHAINS",
    "ABLATION_DRAWS_PER_CHAIN",
    "ABLATION_SEEDS",
    "ABLATION_TUNING_DRAWS",
    "FitError",
    "RefusedRun",
    "aggregate_median_forecast",
    "censoring_ablation",
    "censoring_ignoring_frame",
    "main",
    "run_fit",
]


class FitError(RuntimeError):
    """Raised when the pipeline cannot proceed for a reason this module owns.

    Every refusal in this package is reported the same way — a message on
    standard error and one non-zero exit status class — so the category is
    carried by the reason text rather than by the number, and a consumer tests
    against zero (`plan.md` § Error Handling Strategy).
    """


class RefusedRun(FitError):
    """A gate refusing a run, carrying the evidence its report has to record.

    A subclass rather than a flag, so the two structured refusals travel with
    the field sets FR-017 fixes — the two-field set for an unmet precondition,
    the five-field set for a breached blocking diagnostic — and the emitter does
    not have to reconstruct from a message what the gate already knew. Every
    other failure this job reports is an ordinary refusal whose report carries
    the reason and no field set, which is correct: it breached no published
    threshold.
    """

    def __init__(
        self,
        message: str,
        *,
        preconditions: Sequence[UnmetPrecondition] = (),
        diagnostics: Sequence[DiagnosticRow] = (),
    ) -> None:
        super().__init__(message)
        self.preconditions = tuple(preconditions)
        self.diagnostics = tuple(diagnostics)


#: Every refusal this job reports as a message rather than as a traceback. Each
#: is a named type from a module in this package, plus the two the environment
#: raises. An unlisted exception is a defect and keeps its traceback, because a
#: message-only report of a bug is a bug nobody can locate.
_REPORTED_FAILURES: tuple[type[Exception], ...] = (
    AblationError,
    CensoringError,
    ConfigError,
    DatabaseUrlNotConfiguredError,
    DesignError,
    DiagnosticsError,
    FitError,
    ForecastPathError,
    ManifestError,
    ModelError,
    PosteriorError,
    ReadError,
    ReportError,
    SampleError,
    SerializeError,
    ShrinkageError,
    SplitError,
    WriteError,
)

#: The uniform's open interval, as the widest doubles strictly inside `(0, 1)`.
#: `Generator.random()` returns `[0, 1)`, and `F⁻¹(0)` is zero — `posterior.py`
#: refuses either endpoint rather than letting an infinite draw sort last and
#: turn a line's residual into 1.
_SMALLEST_UNIFORM = float(np.nextafter(0.0, 1.0))
_LARGEST_UNIFORM = float(np.nextafter(1.0, 0.0))

#: How the run's root entropy is split. Three children: one whose grandchildren
#: seed the sampler's chains, one that seeds the per-draw uniforms of the
#: inverse-CDF conditioning, and one for the held-out population's total-duration
#: draws. Spawned rather than derived by arithmetic, which is the property
#: `forecast_run.seed_entropy` is stored as text to preserve.
#:
#: The held-out population takes a **stream of its own** rather than continuing
#: the conditioning one. `SeedSequence.spawn` keys its children by position, so
#: children 0 and 1 are the same objects whether two or three are spawned — which
#: means adding this population moves no open line's draws, and FR-022's
#: reproduction of a run recorded before it existed is unaffected. Sharing one
#: generator would instead have made every open line's draws depend on how many
#: held-out lines the split happened to produce.
_SAMPLER_STREAM = 0
_CONDITIONING_STREAM = 1
_HELD_OUT_STREAM = 2


# ---------------------------------------------------------------------------
# The total-duration lognormal, aggregated from AD-001's sojourns
# ---------------------------------------------------------------------------
#
# AD-002 conditions **one** lognormal on elapsed time, and AD-001 fits one
# lognormal **per lifecycle transition**. Something has to turn the second into
# the first, and this is it. `data-model.md` limitation L-1 already records the
# consequence in the epic's own words: "the aggregate is a sum of lognormals
# rather than a lognormal", so the total-duration law is an *approximation* of
# that sum and is disclosed as one rather than presented as exact.
#
# The approximation is Fenton–Wilkinson: match the sum's mean and variance and
# read a lognormal off them. It is used rather than a numerical convolution for a
# reason DV-005 makes structural — the stored open-line draws have to be the
# conditional law of a single duration distribution, `count(draws > k)/n`
# agreeing with `S(e+k)/S(e)`, and only a closed-form parent gives that identity
# exactly. A per-draw convolution would produce a plausible draw set that no
# invariant could pin.
#
# **What the path is, and what it omits.** A line's total duration from its order
# date is the sum of the sojourns along `state_sequence(L)` — E005's own state
# walk, reused rather than re-derived — where `L` is the number of rework loops
# the line has *already* taken at the anchor. Future loops are therefore not
# predicted, which biases an open line at a decision point short. That is stated
# here rather than hidden: the rework sub-model is fitted and monitored, and
# folding its probability into the aggregate is a separate modelling decision
# with its own disclosure.


def _transition_positions() -> dict[tuple[str, str], int]:
    """Each legal edge's position in the parameter vector, from `config.py`'s order.

    Built from the same tuple `model.py` builds its coordinate values from, so a
    leg named here and a leg named in the graph are the same index. No second
    sort: `LIFECYCLE_TRANSITIONS` already imposed the order, and re-imposing one
    would be a second opinion about which parameter belongs to which transition.
    """
    return {edge: position for position, edge in enumerate(LIFECYCLE_TRANSITIONS)}


def _leg_positions(rework_loops: int) -> NDArray[np.int64]:
    """The transition positions a line with `rework_loops` loops walks, in order.

    Repeats are intended and load-bearing: `submitted → under_review` is walked
    once per loop plus once on the clean pass, and summing the vector below over
    the repeated index is what makes a reworked line's duration longer than a
    clean one's.
    """
    states = state_sequence(rework_loops)
    positions = _transition_positions()
    return np.asarray(
        [positions[(source, target)] for source, target in zip(states, states[1:], strict=False)],
        dtype=np.int64,
    )


def _total_duration_lognormal(
    mu_sojourn: NDArray[np.float64],
    sigma_sojourn: NDArray[np.float64],
    group_offset: NDArray[np.float64],
    legs: NDArray[np.int64],
) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
    """`(mu, sigma)` per posterior draw for one line's total duration.

    Fenton–Wilkinson over the legs: each leg's mean is
    `exp(mu + g + sigma²/2)` and its variance `(exp(sigma²) − 1)·exp(2(mu + g) +
    sigma²)`; the sums are matched by `sigma_total² = log(1 + V/M²)` and
    `mu_total = log(M) − sigma_total²/2`. Vectorised over draws, so each posterior
    draw carries its own aggregate rather than the aggregate of a summary.
    """
    location = mu_sojourn[:, legs] + group_offset[:, None]
    scale_squared = np.square(sigma_sojourn[:, legs])
    leg_mean = np.exp(location + 0.5 * scale_squared)
    leg_variance = np.expm1(scale_squared) * np.exp(2.0 * location + scale_squared)
    total_mean = leg_mean.sum(axis=1)
    total_variance = leg_variance.sum(axis=1)
    sigma_squared = np.log1p(total_variance / np.square(total_mean))
    return np.log(total_mean) - 0.5 * sigma_squared, np.sqrt(sigma_squared)


# ---------------------------------------------------------------------------
# Reading the posterior
# ---------------------------------------------------------------------------


def _flattened(posterior: xr.Dataset, name: str, dimension: str | None) -> NDArray[np.float64]:
    """One posterior variable as `(draw, …)`, chains concatenated in chain order.

    Transposed by dimension *name* rather than by position, so a future ArviZ
    that reorders its dimensions cannot silently reinterpret the vendor axis as
    the draw axis — which is a mis-index that preserves every shape.
    """
    variable = posterior[name]
    order = ("chain", "draw") if dimension is None else ("chain", "draw", dimension)
    values = np.asarray(variable.transpose(*order).values, dtype=float)
    return values.reshape(values.shape[0] * values.shape[1], *values.shape[2:])


def _posterior_dataset(idata: xr.DataTree) -> xr.Dataset:
    """The posterior group as a `Dataset`.

    ArviZ 1.x returns an `xarray.DataTree` rather than the retired
    `InferenceData`, so the group is a child node and `to_dataset()` is what
    turns it into the frame every read below indexes.
    """
    return idata["posterior"].to_dataset()


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------


def _roster(lines: Sequence[LineRow]) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """The vendor and material-category index, taken from **every** line read.

    Every line, not the training side: FR-019 records a shrinkage weight for
    every vendor including one whose lines all landed in the held-out set, and an
    index built from the training frame would drop exactly that vendor. Sorted so
    the index is stable across processes — the run that writes a posterior is not
    the run that reads it, and `design.py` refuses to sort on the way in.
    """
    vendors = tuple(sorted({line.vendor_id for line in lines}))
    categories = tuple(sorted({line.material_category for line in lines}))
    vendor_index(vendors)
    category_index(categories)
    return vendors, categories


def _open_lines(lines: Sequence[LineRow], as_of_date: date) -> tuple[LineRow, ...]:
    """The population `line_posterior` holds: open at the anchor, by the dated test.

    `censoring.py`'s indicator and not the loader's `is_closed` column, because a
    run asks a dated question: a line closed by an event that has not happened yet
    at this as-of date is open, however the column reads.
    """
    return tuple(line for line in lines if censoring_indicator(line, as_of_date))


#: The state a line passes through once per rework loop. Counted from the events
#: themselves rather than from a stored figure: E003 keeps no approval-cycle
#: column precisely because it is derivable, and deriving it here uses the same
#: rule `model.py` uses to build the fit's own rework covariate.
_REWORK_STATE = "revise_and_resubmit"


def _rework_loops(line: LineRow, as_of_date: date) -> int:
    """How many rework loops the line has already taken **at the anchor**.

    Dated, because an open line's forward path is the one it has left to walk
    from where it stands on the as-of date, and a loop it has not taken yet is
    not in it.
    """
    return sum(
        1
        for event in line.events
        if event.to_state == _REWORK_STATE
        and event.occurred_at.astimezone(UTC).date() <= as_of_date
    )


def _walked_rework_loops(line: LineRow) -> int:
    """How many rework loops the line took **in total**, with no date cut-off.

    The held-out population's counterpart, and the difference is the anchor
    again. Its draws are a total duration from the line's own order date over
    the whole path it actually walked, so a loop is in that path whenever it
    happened — cutting the count at the run's as-of date would put the run's
    anchor back inside a quantity anchored on the line's, which is the
    mis-anchoring {SAD:ADR-0018} separates the two stores to prevent. The two
    counts coincide on this dataset, whose closure column and dated indicator
    agree at the committed anchor; they are written separately because the
    agreement is a property of the data rather than of the rule.
    """
    return sum(1 for event in line.events if event.to_state == _REWORK_STATE)


# `eq=False` because every field is an array or a mapping over one.
@dataclasses.dataclass(frozen=True, slots=True, eq=False)
class _TotalDurationLaw:
    """The fitted total-duration law, read once and evaluated per line.

    **One parent for both populations**, which is the whole reason it is a type
    rather than two inlined blocks. An open line's conditional remainder and a
    held-out line's total duration are the *same* distribution consumed two
    ways; deriving it twice would let the two drift into two laws, and no stored
    constraint on either store compares them.
    """

    mu_sojourn: NDArray[np.float64]
    sigma_sojourn: NDArray[np.float64]
    vendor_offsets: NDArray[np.float64]
    category_offsets: NDArray[np.float64]
    vendor_at: Mapping[str, int]
    category_at: Mapping[str, int]

    def for_line(
        self, line: LineRow, rework_loops: int
    ) -> tuple[NDArray[np.float64], NDArray[np.float64]]:
        """`(mu, sigma)` per posterior draw for this line's total duration.

        The caller supplies the loop count rather than the anchor, because the
        two populations count loops differently and that difference is theirs to
        state rather than this object's to guess.
        """
        group = self.vendor_offsets[:, self.vendor_at[line.vendor_id]] + (
            self.category_offsets[:, self.category_at[line.material_category]]
        )
        return _total_duration_lognormal(
            self.mu_sojourn, self.sigma_sojourn, group, _leg_positions(rework_loops)
        )


def _total_duration_law(
    posterior: xr.Dataset, vendors: Sequence[str], categories: Sequence[str]
) -> _TotalDurationLaw:
    """The posterior's four variables, flattened over chains and indexed by name."""
    mu_sojourn = _flattened(posterior, "mu_sojourn", "transition")
    if mu_sojourn.shape[1] != len(TRANSITION_KEYS):
        raise FitError(
            f"the posterior carries {mu_sojourn.shape[1]} transition-level locations against "
            f"{len(TRANSITION_KEYS)} legal edges; the leg index would then name a "
            f"parameter that belongs to a different transition"
        )
    return _TotalDurationLaw(
        mu_sojourn=mu_sojourn,
        sigma_sojourn=_flattened(posterior, "sigma_sojourn", "transition"),
        vendor_offsets=_flattened(posterior, VENDOR_OFFSET, VENDOR_DIM),
        category_offsets=_flattened(posterior, "category_offset", CATEGORY_DIM),
        vendor_at=vendor_index(vendors),
        category_at=category_index(categories),
    )


def _uniforms(count: int, rng: np.random.Generator) -> NDArray[np.float64]:
    """One independent uniform per posterior draw, strictly inside `(0, 1)`.

    AD-004's published basis condition: each stored draw carries its own
    inverse-CDF randomness, so the predictive sequence is decorrelated and its
    effective sample size is not the parameter ESS.
    """
    return np.clip(rng.random(count), _SMALLEST_UNIFORM, _LARGEST_UNIFORM)


def _line_posteriors(
    open_lines: Sequence[LineRow],
    posterior: xr.Dataset,
    vendors: Sequence[str],
    categories: Sequence[str],
    as_of_date: date,
    horizon_days: int,
    rng: np.random.Generator,
) -> tuple[LinePosteriorRow, ...]:
    """One artifact row per open line: conditional remaining draws, grid, residual.

    The draws are AD-002's inverse-CDF conditioning on the line's own elapsed
    time, so every stored value is a **remaining** duration and strictly
    positive. Never a re-based total draw — that puts a point mass of size `F(e)`
    at exactly zero and still satisfies every delivered constraint (HINT-004).
    """
    law = _total_duration_law(posterior, vendors, categories)

    rows: list[LinePosteriorRow] = []
    for line in open_lines:
        elapsed = elapsed_days(line, as_of_date)
        if elapsed < 0:
            raise FitError(
                f"line {line.natural_key} was ordered on {line.order_date}, after the as-of "
                f"date {as_of_date}; a line that has not been ordered yet has no elapsed time "
                f"to condition on and no remaining duration to forecast"
            )
        mu, sigma = law.for_line(line, _rework_loops(line, as_of_date))
        draws = conditional_remaining_draws(
            _uniforms(mu.size, rng), mu, sigma, float(elapsed)
        )
        grid = survival_grid(draws, horizon_days)
        rows.append(
            LinePosteriorRow(
                po_line_id=line.po_line_id,
                draws=draws,
                survival=grid.survival,
                residual_tail_mass=grid.residual_tail_mass,
                draw_digest=draw_digest(draws),
            )
        )
    return tuple(rows)


def _held_out_delivered(lines: Sequence[LineRow], split: SplitResult) -> tuple[LineRow, ...]:
    """The population `held_out_prediction` holds: held out **and** delivered.

    Membership is the split side and the loader's `is_closed` **column**, not
    `censoring.py`'s dated indicator — deliberately, and for a structural reason
    rather than a stylistic one. `fk_held_out_prediction__line_anchor` references
    `(po_line_id, order_date, is_closed)`, so the column is what every stored row
    is proved against; selecting on a different reading of "delivered" would
    build rows the foreign key then refuses. `manifest.py` counts
    `held_out_uncensored_event_count` the same way, which is what makes DV-028's
    comparison of the two a comparison rather than a coincidence.

    Returned in the input's canonical order, which `read.py` established, so the
    rows are built in a fixed sequence and the conditioning stream is consumed
    deterministically.
    """
    held_out = {
        assignment.po_line_id
        for assignment in split.assignments
        if assignment.split_side == HELD_OUT
    }
    return tuple(line for line in lines if line.po_line_id in held_out and line.is_closed)


def _held_out_predictions(
    held_out_lines: Sequence[LineRow],
    posterior: xr.Dataset,
    vendors: Sequence[str],
    categories: Sequence[str],
    horizon_days: int,
    rng: np.random.Generator,
) -> tuple[HeldOutPredictionRow, ...]:
    """One gradeable prediction per held-out delivered line, anchored at its order date.

    **No as-of date in the signature.** The quantity is a total duration from the
    line's own order date, so the run's anchor has no part in it, and the surest
    way to keep it out is to have nothing to pass it through — the same reason
    `total_duration_draws` takes no elapsed time. The anchor that *is* recorded
    is `line.order_date`, and `fk_held_out_prediction__line_anchor` is what
    proves the stored value is that line's rather than a plausible date.

    The grid and residual are the same `survival_grid` the open population uses:
    it is anchor-blind, and a second implementation would be a second convention.
    """
    law = _total_duration_law(posterior, vendors, categories)

    rows: list[HeldOutPredictionRow] = []
    for line in held_out_lines:
        mu, sigma = law.for_line(line, _walked_rework_loops(line))
        draws = total_duration_draws(_uniforms(mu.size, rng), mu, sigma)
        grid = survival_grid(draws, horizon_days)
        rows.append(
            HeldOutPredictionRow(
                po_line_id=line.po_line_id,
                anchor_date=line.order_date,
                draws=draws,
                survival=grid.survival,
                residual_tail_mass=grid.residual_tail_mass,
                draw_digest=draw_digest(draws),
            )
        )
    return tuple(rows)


def _training_line_counts(
    split: SplitResult, lines: Sequence[LineRow], vendors: Sequence[str]
) -> dict[str, int]:
    """Training lines per vendor, with **every** roster vendor present.

    A vendor whose lines all landed in the held-out set gets a zero rather than
    an absent key: FR-019 publishes a weight for it, `shrinkage.py` gives `n = 0`
    an arithmetic answer of exactly zero rather than a special case, and a missing
    key would read as an oversight.
    """
    training = {
        assignment.po_line_id
        for assignment in split.assignments
        if assignment.split_side == TRAIN
    }
    counts = dict.fromkeys(vendors, 0)
    for line in lines:
        if line.po_line_id in training:
            counts[line.vendor_id] += 1
    return counts


def _vendor_shrinkage(
    posterior: xr.Dataset, training_line_counts: Mapping[str, int]
) -> dict[str, VendorShrinkage]:
    """Realized ρⱼ per vendor, as a median with an interval (FR-019).

    `tau_vendor` is the between-vendor spread the fit posted. The residual scale
    is the **root mean square of `sigma_sojourn` across the transition set**,
    which is the pooled within-vendor log-scale a per-vendor mean of `n`
    observations is measured against — a representative aggregate, stated here
    because the sojourn model has seven residual scales and ρ is defined against
    one.
    """
    tau = _flattened(posterior, "tau_vendor", None)
    scales = _flattened(posterior, "sigma_sojourn", "transition")
    sigma = np.sqrt(np.mean(np.square(scales), axis=1))
    return vendor_shrinkage(tau, sigma, training_line_counts, VENDOR_SHRINKAGE_HDI_PROBABILITY)


# ---------------------------------------------------------------------------
# The censoring ablation's seed loop (T051 — FR-033)
# ---------------------------------------------------------------------------
#
# `ablation.py` owns the arithmetic and may reach neither the sampler nor the
# graph — that prohibition is what makes AD-008's floor independent of the fit,
# and it is asserted over that module's own imports. The loop that *produces*
# the per-seed medians has to fit, so it lives here and hands its results back
# across the boundary as values.

#: The seeds the ablation is repeated over. **Committed constants, never drawn
#: per run**, for the reason `SPLIT_SEED` is one: a per-run seed set would let a
#: re-run resample until the delta cleared the floor, which is FR-028's
#: prohibition reached by another route. Three rather than two, so a single
#: outlying seed does not decide the reported median on its own.
ABLATION_SEEDS: tuple[int, ...] = (20260731, 20260732, 20260733)

#: The shape each ablation fit is run at — small, and **its own** rather than the
#: run's. The ablation is six fits beside the run's one, and what it measures is
#: a *difference* between two fits at the same shape and the same seed: the
#: censoring term is either in the likelihood or it is not, and a longer chain
#: estimates that difference more precisely rather than differently. The interval
#: over repeated seeds is what reports the residual sampling noise, which is the
#: whole reason FR-033 requires one.
ABLATION_CHAINS = 2
ABLATION_DRAWS_PER_CHAIN = 50
ABLATION_TUNING_DRAWS = 200


def censoring_ignoring_frame(frame: SojournFrame) -> SojournFrame:
    """The comparator's frame: the same rows with the censoring contribution gone.

    **An ablation comparator and never a baseline** (Principle VIII, FR-033).
    It is this epic's own model with one term removed, so beating it says
    something about that term and nothing about the model's standing against
    anything a reader might otherwise use — and an ablation beaten by the full
    model is the weakest comparison available, which is why the label travels
    with it into the report.

    Clearing `is_censored` is exactly what "omitting the censoring contribution"
    means in `log_contribution_terms`: every row then takes the density branch,
    so a line still open at the anchor enters the fit as though it had delivered
    on the anchor date. The design matrix, the transition indices and the rows
    themselves are untouched.

    `is_decision` is cleared on precisely the rows that were censored, and that
    is not a second ablation — it is what keeps this one to a single term.
    `decision_rows` is `is_decision & ~is_censored`, so clearing the censoring
    flag alone would hand the rework Bernoulli a censored decision point as an
    observed "did not rework", inventing the branch the mixture exists to
    marginalise over. Held fixed, the Bernoulli's data is the aware fit's, and
    the two graphs differ in the censoring term and nowhere else.
    """
    return dataclasses.replace(
        frame,
        is_censored=np.zeros_like(frame.is_censored),
        is_decision=frame.is_decision & ~frame.is_censored,
    )


def aggregate_median_forecast(rows: Sequence[LinePosteriorRow]) -> float:
    """SC-008's measured quantity: the aggregate median forecast over open lines.

    The nearest-rank median over every open line's conditional remaining draws,
    pooled — `schema_constants.percentile_convention`, so the figure is a draw
    the sampler produced rather than the midpoint of two it did not. Pooled
    rather than a median of per-line medians, because the open lines sit at very
    different elapsed times and the average of their medians is a summary of the
    cohort's composition as much as of its forecast.
    """
    if not rows:
        raise FitError(
            "the aggregate median forecast was asked for over no line at all; SC-008 "
            "compares a median over the open population, and an empty population has none"
        )
    pooled = np.sort(np.concatenate([row.draws for row in rows]))
    return float(pooled[max(math.ceil(0.5 * pooled.size), 1) - 1])


def censoring_ablation(
    lines: Sequence[LineRow],
    split: SplitResult,
    vendor_ids: Sequence[str],
    material_categories: Sequence[str],
    as_of_date: date,
    *,
    horizon_days: int,
    seeds: Sequence[int] = ABLATION_SEEDS,
    chains: int = ABLATION_CHAINS,
    draws: int = ABLATION_DRAWS_PER_CHAIN,
    tune: int = ABLATION_TUNING_DRAWS,
    cores: int = 1,
    log: TextIO | None = None,
) -> tuple[SeedResult, ...]:
    """Fit both arms once per seed and return the paired aggregate medians.

    Two fits per seed, **from the same seed**: the chain seeds and the
    conditioning uniforms are respawned identically for each arm, so within a
    seed the only thing that differs between the two runs is the censoring term
    in the likelihood. Across seeds they differ in everything the sampler is
    entitled to, which is what the reported interval measures.

    The seed set is checked before anything is sampled. `realized_delta` refuses
    a single seed and a repeated identifier anyway, but discovering that after
    six fits would spend the cost and then decline to report it.
    """
    requested = tuple(seeds)
    if len(requested) < MINIMUM_ABLATION_SEEDS or len(set(requested)) != len(requested):
        raise AblationError(
            f"the ablation was asked for seeds {requested}; FR-033 requires the delta to "
            f"carry an interval over at least {MINIMUM_ABLATION_SEEDS} *distinct* repeated "
            f"seeds, and a repeated identifier is one seed's outcome under two labels"
        )
    note = _notes(log) if log is not None else None

    aware = training_frame(lines, split, vendor_ids, material_categories, as_of_date)
    ignoring = censoring_ignoring_frame(aware)
    open_lines = _open_lines(lines, as_of_date)
    if not open_lines:
        raise FitError(
            f"no line is open at {as_of_date}, so the ablation has no forecast population to "
            f"take an aggregate median over and SC-008's comparison has no operands"
        )

    results: list[SeedResult] = []
    for seed in requested:
        medians = [
            _arm_median(
                frame,
                open_lines,
                vendor_ids,
                material_categories,
                as_of_date,
                horizon_days,
                seed=seed,
                chains=chains,
                draws=draws,
                tune=tune,
                cores=cores,
            )
            for frame in (aware, ignoring)
        ]
        if note is not None:
            note(
                f"ablation seed {seed}: censoring-aware median {medians[0]:.2f} days against "
                f"a censoring-ignoring {medians[1]:.2f}"
            )
        results.append(
            SeedResult(
                seed=seed,
                censoring_aware_median=medians[0],
                censoring_ignoring_median=medians[1],
            )
        )
    return tuple(results)


def _arm_median(
    frame: SojournFrame,
    open_lines: Sequence[LineRow],
    vendor_ids: Sequence[str],
    material_categories: Sequence[str],
    as_of_date: date,
    horizon_days: int,
    *,
    seed: int,
    chains: int,
    draws: int,
    tune: int,
    cores: int,
) -> float:
    """One arm of one seed: sample, condition every open line, summarise.

    The entropy is respawned from `seed` inside this function rather than passed
    in already split, so the two arms of a seed are handed byte-identical chain
    seeds and byte-identical conditioning uniforms. Splitting once outside and
    reusing the generator would advance it between the arms and put sampler
    noise into the difference the ablation reports.
    """
    streams = np.random.SeedSequence(seed).spawn(2)
    chain_seeds = [
        int(child.generate_state(1, dtype=np.uint32)[0])
        for child in streams[_SAMPLER_STREAM].spawn(chains)
    ]
    idata = sample_posterior(
        build_model(frame),
        random_seed=chain_seeds,
        chains=chains,
        draws=draws,
        tune=tune,
        cores=cores,
    )
    rows = _line_posteriors(
        open_lines,
        _posterior_dataset(idata),
        vendor_ids,
        material_categories,
        as_of_date,
        horizon_days,
        np.random.default_rng(streams[_CONDITIONING_STREAM]),
    )
    return aggregate_median_forecast(rows)


# ---------------------------------------------------------------------------
# The two gates (T081, T082 — FR-021, FR-035, FR-017, FR-037)
# ---------------------------------------------------------------------------


def _unmet_preconditions(
    chains: int, open_line_count: int, as_of_date: date
) -> tuple[UnmetPrecondition, ...]:
    """Every blocking precondition that is unmet, evaluated before sampling.

    Both are evaluated rather than the first refusing, for the reason FR-017
    gives about diagnostics: an operator handed one unmet precondition returns
    to discover the next. Each carries the two-field set FR-017 fixes for a
    pre-sampling refusal — the precondition and its realized value — and no
    threshold direction, because a precondition is not a measured metric.
    """
    unmet: list[UnmetPrecondition] = []
    if chains < CHAINS_MIN:
        unmet.append(
            UnmetPrecondition(
                precondition=(
                    f"at least {CHAINS_MIN} chains — the published minimum "
                    f"(`spec.md` § Published Constants, a **blocking precondition**), and "
                    f"the basis the R-hat and ESS thresholds are stated at (FR-035)"
                ),
                realized_value=f"{chains} chain(s) requested",
            )
        )
    if open_line_count <= 0:
        unmet.append(
            UnmetPrecondition(
                precondition=(
                    "at least one line open at the as-of date — an empty forecast set "
                    "reads as an absence of risk, and "
                    "`ck_forecast_run__open_line_count_positive` makes it "
                    "unrepresentable (FR-021)"
                ),
                realized_value=f"0 line(s) open at {as_of_date.isoformat()}",
            )
        )
    return tuple(unmet)


def _precondition_refusal(unmet: Sequence[UnmetPrecondition]) -> RefusedRun:
    """FR-035's message: every unmet precondition and its realized value.

    Nothing is sampled and nothing is written on this path — the exit names a
    *precondition* rather than a breached diagnostic, because FR-017's
    enumeration does not fit a gate that ran before there was anything to
    measure.
    """
    stated = "; ".join(
        f"{item.precondition} — realized: {item.realized_value}" for item in unmet
    )
    return RefusedRun(
        f"{len(unmet)} pre-sampling precondition(s) unmet, so nothing was sampled and "
        f"nothing was written: {stated}",
        preconditions=unmet,
    )


def _diagnostics_refusal(breaches: Sequence[DiagnosticRow]) -> RefusedRun:
    """FR-017's message: **every** breached blocking diagnostic, not the first.

    One complete five-field set per breach — metric, the parameter where the
    metric is parameter-scoped, the realized value, the threshold and the
    threshold's direction — assembled by `diagnostics.py` so the stream and the
    emitted report cannot describe the same breach differently (DV-038).
    """
    return RefusedRun(
        f"{len(breaches)} blocking diagnostic(s) breached after sampling, so no run "
        f"record, no posterior, no held-out prediction, no split assignment and no "
        f"diagnostic row was written and the active-run pointer is unmoved: "
        + "; ".join(refusal_lines(breaches)),
        diagnostics=breaches,
    )


def _emit_refusal_report(
    exc: BaseException,
    *,
    as_of_date: date,
    input_data_hash: str,
    attempted_at: datetime,
    wall_clock_seconds: float,
    sampled_shape: SampledShape | None,
    report_root: Path | str | None,
) -> Path:
    """Write the durable half of the pair G-8 names, for **any** refusal.

    Quantified over every refusal this job reports rather than over the two
    structured gates, because FR-037 says every refusal emits one — and the
    field sets ride on `RefusedRun` where a gate produced them, so an ordinary
    failure records its reason and no threshold it never breached.
    """
    return write_refusal_report(
        RefusedAttempt(
            as_of_date=as_of_date,
            input_data_hash=input_data_hash,
            attempted_at=attempted_at,
            reason=f"{type(exc).__name__}: {exc}",
            wall_clock_seconds=wall_clock_seconds,
            sampled_shape=sampled_shape,
            preconditions=getattr(exc, "preconditions", ()),
            diagnostics=getattr(exc, "diagnostics", ()),
        ),
        report_root,
    )


# A mutable record, unlike everything else in this module: it exists so the
# refusal emitter can say how far the attempt got, and "how far" is by
# definition not known when the attempt starts.
@dataclasses.dataclass(slots=True)
class _Progress:
    """What the refusal report needs to know about the attempt's own history.

    `sampled_shape` stays `None` until the sampler returns, which is exactly
    FR-035's evidence — *nothing was sampled* — and becomes the realized shape
    afterwards, which is FR-017's — a sample was taken and nothing was written.
    `published` is what keeps a failure *after* transaction 2 from being filed
    as a refusal of a run that shipped.
    """

    sampled_shape: SampledShape | None = None
    published: bool = False


def run_fit(
    engine: Engine,
    *,
    as_of_date: date,
    seed_entropy: int,
    chains: int = CHAINS,
    draws: int = DRAWS_PER_CHAIN,
    tune: int = TUNING_DRAWS_PER_CHAIN,
    cores: int = 1,
    report_root: Path | str | None = None,
    repo_root: Path | str | None = None,
    log: TextIO = sys.stderr,
) -> uuid.UUID:
    """Read and hash the input, then fit under the refusal report's cover.

    **The read precedes both preconditions, and that is FR-037's doing rather
    than a relaxation of FR-035.** A refused attempt has no `run_id` — FR-017
    forbids writing the run row — so its evidence is filed under an identifier
    built from the as-of date, the **input row hash** and the attempt's instant,
    and an attempt that has not read has no name to file that evidence under.
    Reading is neither sampling nor writing: no statement is issued on any
    refusing path below, so "nothing is sampled and nothing is written" holds
    exactly as stated.
    """
    started = time.monotonic()
    attempted_at = datetime.now(UTC)
    note = _notes(log)
    progress = _Progress()

    with engine.connect() as connection:
        procurement_input = read_lines_and_events(connection)
        shape = read_run_shape(connection)
    note(
        f"read {len(procurement_input.lines)} lines and "
        f"{len(procurement_input.events)} lifecycle events"
    )
    row_hash = input_data_hash(procurement_input)
    note(f"input row hash {row_hash}")

    try:
        return _fit_and_publish(
            engine,
            procurement_input=procurement_input,
            shape=shape,
            row_hash=row_hash,
            as_of_date=as_of_date,
            seed_entropy=seed_entropy,
            chains=chains,
            draws=draws,
            tune=tune,
            cores=cores,
            report_root=report_root,
            repo_root=repo_root,
            log=log,
            note=note,
            started=started,
            progress=progress,
        )
    except _REPORTED_FAILURES as exc:
        if progress.published:
            # The run committed and something after it failed. That is a defect
            # in a later step, not a refusal, and filing it as one would put a
            # published run's identifier in a file that says nothing was written.
            raise
        emitted = _emit_refusal_report(
            exc,
            as_of_date=as_of_date,
            input_data_hash=row_hash,
            attempted_at=attempted_at,
            wall_clock_seconds=time.monotonic() - started,
            sampled_shape=progress.sampled_shape,
            report_root=report_root,
        )
        note(f"refusal report at {emitted}")
        raise


def _fit_and_publish(
    engine: Engine,
    *,
    procurement_input: ProcurementInput,
    shape: RunShape,
    row_hash: str,
    as_of_date: date,
    seed_entropy: int,
    chains: int,
    draws: int,
    tune: int,
    cores: int,
    report_root: Path | str | None,
    repo_root: Path | str | None,
    log: TextIO,
    note: Callable[[str], None],
    started: float,
    progress: _Progress,
) -> uuid.UUID:
    """Fit one run and publish it, returning the `run_id` written.

    Every step's output feeds exactly one successor, in the order the epic's
    guarantees require: split from the input hash and two committed constants,
    build the training frame from the `train` side only, sample, derive both
    derived artifacts, then write. The two gates sit at the two seams, and
    nothing between them issues a statement — which is what makes the refusal
    guarantee ordering rather than rollback (AD-010).
    """
    lines = procurement_input.lines

    # ---- SEAM (T081): the pre-sampling preconditions. Fewer than `CHAINS_MIN`
    # chains (FR-035); no open line at the anchor (FR-021). Both are evaluated,
    # both are named with their realized values, and the refusal happens before
    # the sampler is reached — so nothing is sampled and nothing is written.
    open_lines = _open_lines(lines, as_of_date)
    note(f"{len(open_lines)} line(s) open at {as_of_date}")
    unmet = _unmet_preconditions(chains, len(open_lines), as_of_date)
    if unmet:
        raise _precondition_refusal(unmet)

    fixture = read_fixture_provenance(repo_root)
    if not fixture.digest_matches_published:
        note(
            f"provenance warning: the committed fixture digests to "
            f"{fixture.observed_digest} against a published {fixture.published_digest}. The "
            f"rows this fit read are unchanged, so the run proceeds and only the chain back "
            f"to the upstream artifact has broken (FR-023)"
        )

    split = assign_split(lines, as_of_date, row_hash)
    note(f"split assignment hash {split.split_assignment_hash}")

    vendors, categories = _roster(lines)
    frame = training_frame(lines, split, vendors, categories, as_of_date)
    note(
        f"training frame: {frame.row_count} sojourns over {len(vendors)} vendors and "
        f"{len(categories)} material categories, {len(frame.excluded_po_line_ids)} line(s) "
        f"excluded as unstarted at the anchor"
    )

    # Spawned **once**: `SeedSequence.spawn` advances an internal counter, so
    # calling it twice would hand out six children and make which three were used
    # depend on the order the calls happened to be written in.
    streams = np.random.SeedSequence(seed_entropy).spawn(3)
    chain_seeds = [
        int(child.generate_state(1, dtype=np.uint32)[0])
        for child in streams[_SAMPLER_STREAM].spawn(chains)
    ]
    note(f"sampling {chains} chains x {draws} draws with {tune} tuning draws")
    idata = sample_posterior(
        build_model(frame),
        random_seed=chain_seeds,
        chains=chains,
        draws=draws,
        tune=tune,
        cores=cores,
    )
    progress.sampled_shape = SampledShape(chains, draws, tune)

    # ---- SEAM (T082): the post-sampling diagnostics gate, after sampling and
    # **before the first statement**. Every breached blocking diagnostic carries
    # its own complete field set — metric, parameter where parameter-scoped,
    # realized value, threshold and threshold direction — and the run refuses
    # with no store touched and the pointer unmoved (FR-017).
    monitored = monitored_parameter_names(vendors, categories)
    diagnostics = evaluate_diagnostics(idata, monitored)
    breaches = blocking_breaches(diagnostics)
    note(
        f"{len(diagnostics)} monitored diagnostic(s) over {len(monitored)} parameter(s): "
        f"{len(breaches)} blocking breach(es)"
    )
    if breaches:
        raise _diagnostics_refusal(breaches)

    posterior = _posterior_dataset(idata)
    if shape.draw_count != chains * draws:
        note(
            f"realized shape {chains * draws} draws against a declared "
            f"{shape.draw_count}; the run records what it produced, and DV-014 is what "
            f"asserts the declared pair on a run at the committed shape"
        )
    line_posteriors = _line_posteriors(
        open_lines,
        posterior,
        vendors,
        categories,
        as_of_date,
        shape.horizon_days,
        np.random.default_rng(streams[_CONDITIONING_STREAM]),
    )

    held_out_lines = _held_out_delivered(lines, split)
    held_out_predictions = _held_out_predictions(
        held_out_lines,
        posterior,
        vendors,
        categories,
        shape.horizon_days,
        np.random.default_rng(streams[_HELD_OUT_STREAM]),
    )
    note(
        f"{len(held_out_predictions)} held-out line(s) have already delivered and carry a "
        f"gradeable prediction anchored at their own order date"
    )

    training_counts = _training_line_counts(split, lines, vendors)
    weights = _vendor_shrinkage(posterior, training_counts)
    # Across **two** populations, not within one: `population_rank` is what
    # orders them, and it is recomputable from the stored rows alone by joining
    # each artifact row to `forecast_split_assignment` (DV-031). A hash taken
    # over one store would be a digest of half the artifact set that no reader
    # could tell from a digest of all of it.
    artifact_hash = artifact_hash_over(
        [
            *(
                ArtifactDigest(POPULATION_RANK_LINE_POSTERIOR, ordinal, row.draw_digest)
                for ordinal, row in _in_canonical_order(line_posteriors, split)
            ),
            *(
                ArtifactDigest(POPULATION_RANK_HELD_OUT_PREDICTION, ordinal, row.draw_digest)
                for ordinal, row in _in_canonical_order(held_out_predictions, split)
            ),
        ]
    )

    # A plain connection, and not either of the two transactions
    # `write_artifact_set` opens: the manifest reads `schema_constants` over the
    # connection (AD-009) and writes nothing, and assembling it inside
    # transaction 1 would make its own reads part of the write it describes.
    with engine.connect() as connection:
        manifest = build_manifest(
            connection,
            procurement_input=procurement_input,
            input_data_hash=row_hash,
            as_of_date=as_of_date,
            split=split,
            covariate_names=covariate_names(frame),
            vendor_shrinkage=weights,
            open_line_count=len(line_posteriors),
            seed_entropy=seed_entropy,
            chain_count=chains,
            draw_count=int(line_posteriors[0].draws.size),
            tuning_count=tune,
            artifact_hash=artifact_hash,
            wall_clock_seconds=time.monotonic() - started,
            repo_root=repo_root,
            fixture=fixture,
        )
    run_id = write_artifact_set(
        engine, manifest, split.assignments, line_posteriors, held_out_predictions, diagnostics
    )
    progress.published = True
    note(
        f"wrote {len(line_posteriors)} line_posterior row(s), "
        f"{len(held_out_predictions)} held_out_prediction row(s) and "
        f"{len(diagnostics)} forecast_diagnostic row(s), and published run {run_id}"
    )

    # The floor is derived here, from the training split and the input rows alone
    # — no posterior, no trace, nothing this run fitted (AD-008). It could as
    # easily be derived before the sampler ran; what makes the ordering
    # structural rather than conventional is that `ablation.py` cannot reach a
    # fitted quantity at all.
    floor = kaplan_meier_floor(lines, split, as_of_date)
    note(
        f"censoring floor {floor.floor:.4f} from a Kaplan-Meier median of "
        f"{floor.kaplan_meier_median:.1f} days against a naive completed mean of "
        f"{floor.naive_completed_mean:.1f}, over {floor.training_line_count} training line(s)"
    )
    delta = realized_delta(
        censoring_ablation(
            lines,
            split,
            vendors,
            categories,
            as_of_date,
            horizon_days=shape.horizon_days,
            log=log,
        )
    )
    note(
        f"realized ablation delta {delta.delta:.4f} over seeds {list(delta.seeds)}, interval "
        f"[{delta.interval_low:.4f}, {delta.interval_high:.4f}]"
    )

    report = write_run_report(
        manifest,
        procurement_input=procurement_input,
        training_line_counts=training_counts,
        ablation=AblationOutcome(delta=delta, floor=floor),
        fixture_digest_agrees=fixture.digest_matches_published,
        report_root=report_root,
    )
    note(f"run report at {report}")
    return run_id


def _in_canonical_order[Row: (LinePosteriorRow, HeldOutPredictionRow)](
    artifact_rows: Sequence[Row], split: SplitResult
) -> list[tuple[int, Row]]:
    """Each artifact row paired with its line's `canonical_ordinal`, ascending.

    The ordinal comes from the split assignment rather than from the order the
    rows were built in, which is what makes the artifact hash recomputable from
    the stored rows alone: a reader joins each row's `(run_id, po_line_id)` to
    `forecast_split_assignment` and reproduces this sequence (DV-031).

    Quantified over either population, because the ordinal is a property of the
    *line* and both stores key on one. Only `population_rank` distinguishes them,
    and that is the caller's to supply. The two row types are named in the
    constraint rather than left to a protocol, so a third store cannot join them
    by accident.
    """
    ordinals = {
        assignment.po_line_id: assignment.canonical_ordinal for assignment in split.assignments
    }
    missing = [row.po_line_id for row in artifact_rows if row.po_line_id not in ordinals]
    if missing:
        raise FitError(
            f"{len(missing)} artifact row(s) name a line with no split assignment — first "
            f"{missing[0]}. The artifact hash is ordered by `canonical_ordinal`, so an "
            f"unassigned line leaves the digest's input undefined"
        )
    return sorted(
        ((ordinals[row.po_line_id], row) for row in artifact_rows), key=lambda pair: pair[0]
    )


def _notes(log: TextIO) -> Callable[[str], None]:
    """A one-line diagnostic writer bound to the caller's stream.

    Every diagnostic goes here and never to standard output, which carries the
    single `run_id` line and nothing else (FR-039). Taking the stream as an
    argument is what lets a test capture the two separately without patching a
    module global.
    """

    def note(message: str) -> None:
        print(message, file=log)

    return note


# ---------------------------------------------------------------------------
# The console entry point
# ---------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    """The job's arguments. Nothing here overrides a committed constant.

    `HELD_OUT_FRACTION` and `SPLIT_SEED` are deliberately **absent**: AD-005
    makes both committed configuration precisely so a re-fit cannot reshuffle the
    split until a vendor lands favourably, and a flag would be that prohibition
    reached by another route (SC-032). The shape flags are the opposite case —
    they widen or narrow a run's cost, are recorded on the run, and gate nothing.
    """
    parser = argparse.ArgumentParser(
        prog="forecast-fit",
        description=(
            "Fit the delivery forecast model and write one run. Prints the run_id on "
            "standard output; every diagnostic goes to standard error."
        ),
    )
    parser.add_argument(
        "--as-of-date",
        required=True,
        type=date.fromisoformat,
        help=(
            "the run's anchor, ISO YYYY-MM-DD. Required rather than defaulted from a clock: "
            "the dataset's convention is committed dates, and a clock default would move "
            "every open line's elapsed time between two otherwise identical runs."
        ),
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help=(
            "the run's root entropy. Omitted, one is drawn from the operating system and "
            "recorded verbatim in forecast_run.seed_entropy, which is what makes the run "
            "reproducible; a constant default here would put an unrecorded value behind "
            "every published run."
        ),
    )
    parser.add_argument("--chains", type=int, default=CHAINS)
    parser.add_argument("--draws", type=int, default=DRAWS_PER_CHAIN)
    parser.add_argument("--tune", type=int, default=TUNING_DRAWS_PER_CHAIN)
    parser.add_argument(
        "--cores",
        type=int,
        default=1,
        help=(
            "sampler worker processes, one chain each. Defaults to 1: PyMC's default of one "
            "worker per chain spawns processes, which on Windows re-imports the entry point "
            "and deadlocks. Raise it deliberately where the platform allows."
        ),
    )
    parser.add_argument(
        "--report-root",
        type=Path,
        default=None,
        help="where the run report is written; defaults to this checkout's report tree.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the fit. **Standard output carries one line: the `run_id`** (FR-039).

    Exit zero exactly on completion. On a refusal the reason is written to
    standard error, standard output carries nothing at all — no partial line, no
    placeholder — and the status is the single non-zero class every refusal in
    this job shares, so a consumer tests against zero rather than against a
    particular value.
    """
    arguments = _parser().parse_args(argv)
    entropy = (
        arguments.seed if arguments.seed is not None else int(np.random.SeedSequence().entropy)
    )
    try:
        engine = create_engine(get_database_url())
        try:
            run_id = run_fit(
                engine,
                as_of_date=arguments.as_of_date,
                seed_entropy=entropy,
                chains=arguments.chains,
                draws=arguments.draws,
                tune=arguments.tune,
                cores=arguments.cores,
                report_root=arguments.report_root,
            )
        finally:
            engine.dispose()
    except _REPORTED_FAILURES as exc:
        print(f"forecast-fit refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(run_id)
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through the console script
    raise SystemExit(main())
