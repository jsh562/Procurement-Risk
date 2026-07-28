"""`forecast-fit`: read, hash, split, fit, condition, write, publish.

The offline console entry point of {SAD:ADR-0011}. **Standard output carries
exactly one line — the `run_id` — and nothing on a refusal**; every diagnostic
goes to standard error, and the exit status is zero exactly on completion
(FR-039). The pre-sampling preconditions and the post-sampling diagnostics gate
are *not* here yet: US4 inserts them at the two marked seams, ahead of the first
statement, because the refusal guarantee is achieved by ordering rather than by
rollback (AD-010).
"""

from __future__ import annotations

import argparse
import sys
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, date
from pathlib import Path
from typing import TYPE_CHECKING, TextIO

import numpy as np
from numpy.typing import NDArray
from sqlalchemy import Engine, create_engine

from model.forecast.censoring import CensoringError, censoring_indicator, elapsed_days
from model.forecast.config import (
    CHAINS,
    DRAWS_PER_CHAIN,
    LIFECYCLE_TRANSITIONS,
    TUNING_DRAWS_PER_CHAIN,
    ConfigError,
    read_run_shape,
)
from model.forecast.design import DesignError, category_index, vendor_index
from model.forecast.manifest import (
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
    build_model,
    covariate_names,
    training_frame,
)
from model.forecast.paths import ForecastPathError
from model.forecast.posterior import PosteriorError, conditional_remaining_draws, survival_grid
from model.forecast.read import LineRow, ReadError, read_lines_and_events
from model.forecast.report import ReportError, write_run_report
from model.forecast.sample import SampleError, sample_posterior
from model.forecast.serialize import SerializeError, input_data_hash
from model.forecast.shrinkage import ShrinkageError, VendorShrinkage, vendor_shrinkage
from model.forecast.split import TRAIN, SplitError, SplitResult, assign_split
from model.forecast.write import LinePosteriorRow, WriteError, write_artifact_set
from model.procurement.lifecycle import state_sequence
from model.schema.url import DatabaseUrlNotConfiguredError, get_database_url

if TYPE_CHECKING:  # pragma: no cover - imported for the annotation only
    import xarray as xr

__all__ = ["FitError", "main", "run_fit"]


class FitError(RuntimeError):
    """Raised when the pipeline cannot proceed for a reason this module owns.

    Every refusal in this package is reported the same way — a message on
    standard error and one non-zero exit status class — so the category is
    carried by the reason text rather than by the number, and a consumer tests
    against zero (`plan.md` § Error Handling Strategy).
    """


#: Every refusal this job reports as a message rather than as a traceback. Each
#: is a named type from a module in this package, plus the two the environment
#: raises. An unlisted exception is a defect and keeps its traceback, because a
#: message-only report of a bug is a bug nobody can locate.
_REPORTED_FAILURES: tuple[type[Exception], ...] = (
    CensoringError,
    ConfigError,
    DatabaseUrlNotConfiguredError,
    DesignError,
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

#: How the run's root entropy is split. Two children: one whose grandchildren
#: seed the sampler's chains, one that seeds the per-draw uniforms of the
#: inverse-CDF conditioning. Spawned rather than derived by arithmetic, which is
#: the property `forecast_run.seed_entropy` is stored as text to preserve.
_SAMPLER_STREAM = 0
_CONDITIONING_STREAM = 1


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


def _rework_loops(line: LineRow, as_of_date: date) -> int:
    """How many rework loops the line has already taken at the anchor.

    Counted from the events themselves rather than from a stored figure: E003
    keeps no approval-cycle column precisely because it is derivable, and
    deriving it here uses the same rule `model.py` uses to build the fit's own
    rework covariate.
    """
    rework_state = "revise_and_resubmit"
    return sum(
        1
        for event in line.events
        if event.to_state == rework_state
        and event.occurred_at.astimezone(UTC).date() <= as_of_date
    )


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

    One independent uniform per posterior draw, which is what AD-004's published
    basis condition describes: each stored draw carries its own residual and
    inverse-CDF randomness, so the predictive sequence is decorrelated and its
    effective sample size is not the parameter ESS.
    """
    mu_sojourn = _flattened(posterior, "mu_sojourn", "transition")
    sigma_sojourn = _flattened(posterior, "sigma_sojourn", "transition")
    vendor_offsets = _flattened(posterior, VENDOR_OFFSET, VENDOR_DIM)
    category_offsets = _flattened(posterior, "category_offset", CATEGORY_DIM)
    if mu_sojourn.shape[1] != len(TRANSITION_KEYS):
        raise FitError(
            f"the posterior carries {mu_sojourn.shape[1]} transition-level locations against "
            f"{len(TRANSITION_KEYS)} legal edges; the leg index below would then name a "
            f"parameter that belongs to a different transition"
        )
    vendor_at = vendor_index(vendors)
    category_at = category_index(categories)

    rows: list[LinePosteriorRow] = []
    for line in open_lines:
        elapsed = elapsed_days(line, as_of_date)
        if elapsed < 0:
            raise FitError(
                f"line {line.natural_key} was ordered on {line.order_date}, after the as-of "
                f"date {as_of_date}; a line that has not been ordered yet has no elapsed time "
                f"to condition on and no remaining duration to forecast"
            )
        group = vendor_offsets[:, vendor_at[line.vendor_id]] + (
            category_offsets[:, category_at[line.material_category]]
        )
        mu, sigma = _total_duration_lognormal(
            mu_sojourn, sigma_sojourn, group, _leg_positions(_rework_loops(line, as_of_date))
        )
        uniforms = np.clip(rng.random(mu.size), _SMALLEST_UNIFORM, _LARGEST_UNIFORM)
        draws = conditional_remaining_draws(uniforms, mu, sigma, float(elapsed))
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
    """Fit one run and publish it, returning the `run_id` written.

    Every step's output feeds exactly one successor, in the order the epic's
    guarantees require: read the schema (never a copy), hash the rows read, split
    from that hash and two committed constants, build the training frame from the
    `train` side only, sample, derive both derived artifacts, then write. The two
    seams below are where US4 inserts the refusals; nothing between them issues a
    statement, which is what makes the guarantee ordering rather than rollback.
    """
    started = time.monotonic()
    note = _notes(log)

    # ---- SEAM (T081): the pre-sampling preconditions belong here, before the
    # first read. Schema head is not 0303; fewer than `CHAINS_MIN` chains; no
    # open line at the anchor. Each refuses naming the precondition and its
    # realized value, and nothing is sampled and nothing written.

    with engine.connect() as connection:
        procurement_input = read_lines_and_events(connection)
        shape = read_run_shape(connection)
    lines = procurement_input.lines
    note(f"read {len(lines)} lines and {len(procurement_input.events)} lifecycle events")

    row_hash = input_data_hash(procurement_input)
    note(f"input row hash {row_hash}")
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
    # calling it twice would hand out four children and make which two were used
    # depend on the order the calls happened to be written in.
    streams = np.random.SeedSequence(seed_entropy).spawn(2)
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

    # ---- SEAM (T082): the post-sampling diagnostics gate belongs here, after
    # sampling and **before the first statement**. Every breached blocking
    # diagnostic is reported with its parameter, realized value, threshold and
    # threshold direction; the run refuses and no store is touched.

    posterior = _posterior_dataset(idata)
    if shape.draw_count != chains * draws:
        note(
            f"realized shape {chains * draws} draws against a declared "
            f"{shape.draw_count}; the run records what it produced, and DV-014 is what "
            f"asserts the declared pair on a run at the committed shape"
        )
    open_lines = _open_lines(lines, as_of_date)
    note(f"{len(open_lines)} line(s) open at {as_of_date}")
    if not open_lines:
        # FR-021's refusal is a **pre-sampling** precondition and belongs at the
        # T081 seam above, where it costs nothing and leaves no sampler output.
        # This is the same condition caught late, so an empty forecast set is
        # named rather than surfacing as an index error two functions from here.
        raise FitError(
            f"no line is open at {as_of_date}, so this run has nothing to forecast. "
            f"`ck_forecast_run__open_line_count_positive` makes an empty forecast set "
            f"unrepresentable (FR-021), and the condition is knowable before sampling"
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

    training_counts = _training_line_counts(split, lines, vendors)
    weights = _vendor_shrinkage(posterior, training_counts)
    artifact_hash = artifact_hash_over(
        ArtifactDigest(POPULATION_RANK_LINE_POSTERIOR, ordinal, row.draw_digest)
        for ordinal, row in _in_canonical_order(line_posteriors, split)
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
    run_id = write_artifact_set(engine, manifest, split.assignments, line_posteriors)
    note(f"wrote {len(line_posteriors)} line_posterior row(s) and published run {run_id}")

    report = write_run_report(
        manifest,
        procurement_input=procurement_input,
        training_line_counts=training_counts,
        fixture_digest_agrees=fixture.digest_matches_published,
        report_root=report_root,
    )
    note(f"run report at {report}")
    return run_id


def _in_canonical_order(
    line_posteriors: Sequence[LinePosteriorRow], split: SplitResult
) -> list[tuple[int, LinePosteriorRow]]:
    """Each artifact row paired with its line's `canonical_ordinal`, ascending.

    The ordinal comes from the split assignment rather than from the order the
    rows were built in, which is what makes the artifact hash recomputable from
    the stored rows alone: a reader joins each row's `(run_id, po_line_id)` to
    `forecast_split_assignment` and reproduces this sequence (DV-031).
    """
    ordinals = {
        assignment.po_line_id: assignment.canonical_ordinal for assignment in split.assignments
    }
    missing = [row.po_line_id for row in line_posteriors if row.po_line_id not in ordinals]
    if missing:
        raise FitError(
            f"{len(missing)} artifact row(s) name a line with no split assignment — first "
            f"{missing[0]}. The artifact hash is ordered by `canonical_ordinal`, so an "
            f"unassigned line leaves the digest's input undefined"
        )
    return sorted(
        ((ordinals[row.po_line_id], row) for row in line_posteriors), key=lambda pair: pair[0]
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
