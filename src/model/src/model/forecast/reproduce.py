"""`forecast-reproduce`: re-derive a recorded run and compare it, line by line.

FR-022, FR-023, FR-032, FR-040. The second console entry point of AD-003, and a
different job from producing a run: folding it into a flag on `forecast-fit`
would put the reproduction check behind the thing it checks.

**It writes nothing.** No run row, no artifact, no pointer — the only thing that
lands on disk is the reproduction report FR-040 enumerates, so the job can be
pointed at a production database and be a read. Two gates run before the sampler
is reached: a moved **row hash** or **split assignment hash** refuses and names
which moved with both values (FR-023, DV-015, DV-017), while a moved **fixture
digest** against unchanged rows is a provenance warning that completes with a
zero exit (DV-016) — the rows the fit read are unchanged, and only the chain back
to the upstream artifact has broken.

The verdict is one of SC-018's **three** outcomes, never two. Agreement is per
line on the median and the 80th percentile within AD-004's published 5.0 days,
together with exact equality of the manifest's provenance fields; but AD-004
publishes a **basis condition** with that number, and where any line's realized
predictive effective sample size falls below half the run's draw count the
comparison is reported as *outside the tolerance's stated basis* rather than
passing or failing it.

**The optional draw-digest claim (FR-032) never reaches the exit status.** It is
published where bitwise equality holds and reported as a scope limit where it
does not, in either of two readings — the observed pin differs, or the pin
matches and does not determine bitwise numerics. FR-022's outcome is the gate,
and it is never bitwise equality of draws.
"""

from __future__ import annotations

import argparse
import dataclasses
import sys
import time
import uuid
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path
from typing import TextIO

import numpy as np
from numpy.typing import NDArray
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.orm import Session

from model.forecast.compare import (
    MEDIAN_PROBABILITY,
    P80_PROBABILITY,
    PERCENTILE_CONVENTION,
    CompareError,
    nearest_rank_percentile,
    within_tolerance,
)
from model.forecast.config import (
    REPRODUCTION_PREDICTIVE_ESS_FRACTION_MIN,
    REPRODUCTION_TOLERANCE_DAYS,
    ConfigError,
    split_seed_entropy,
)
from model.forecast.design import DesignError
from model.forecast.diagnostics import DiagnosticsError
from model.forecast.fit import (
    DerivedArtifacts,
    FitError,
    derive_artifacts,
    roster_index,
    sample_run,
)
from model.forecast.manifest import (
    ARTIFACT_SCHEMA_VERSION,
    MODEL_VERSION,
    ManifestError,
    code_revision,
    library_versions,
    read_fixture_provenance,
    roster_hash_of,
)
from model.forecast.model import ModelError, training_frame
from model.forecast.paths import ForecastPathError, reproduction_report_path
from model.forecast.posterior import PosteriorError
from model.forecast.read import ProcurementInput, ReadError, read_lines_and_events
from model.forecast.report import (
    EMITTED_REPORT_KINDS,
    RefusedAttempt,
    ReportError,
    UnmetPrecondition,
    write_refusal_report,
)
from model.forecast.sample import SampleError
from model.forecast.serialize import SerializeError, input_data_hash, split_assignment_hash
from model.forecast.shrinkage import ShrinkageError
from model.forecast.split import SplitError, assign_split
from model.forecast.write import WriteError
from model.schema.url import DatabaseUrlNotConfiguredError, get_database_url

__all__ = [
    "DIGEST_CLAIM_EQUAL",
    "DIGEST_CLAIM_SCOPE_LIMIT",
    "DIGEST_SCOPE_PIN_DIFFERS",
    "DIGEST_SCOPE_PIN_DOES_NOT_DETERMINE_NUMERICS",
    "HELD_OUT_STORE",
    "LINE_POSTERIOR_STORE",
    "OUTCOME_AGREES",
    "OUTCOME_DISAGREES",
    "OUTCOME_OUTSIDE_BASIS",
    "PROVENANCE_FIELDS",
    "REPRODUCTION_SECTION_FIELDS",
    "REPRODUCTION_SECTION_TITLES",
    "DigestClaim",
    "LineComparison",
    "ProvenanceIdentity",
    "RecordedRun",
    "Refit",
    "Reproduction",
    "ReproduceError",
    "ReproductionOutcome",
    "ReproductionRefusal",
    "StoredArtifact",
    "compare_reproduction",
    "digest_claim",
    "main",
    "read_recorded_run",
    "refit_recorded",
    "render_reproduction_report",
    "run_reproduce",
    "write_reproduction_report",
]


class ReproduceError(RuntimeError):
    """Raised when the reproduction cannot proceed for a reason this module owns.

    A `RuntimeError`, following `FitError`: most cases are the environment's —
    no run to reproduce, a recorded shape that does not divide, a database that
    moved under the job. Every refusal in this package is reported the same way,
    a message on standard error and one non-zero exit status class, so a consumer
    tests the status against zero rather than against a particular value.
    """


class ReproductionRefusal(ReproduceError):
    """FR-023's refusal, carrying the field set its report has to record.

    A subclass rather than a flag, so which hash moved and both its values
    travel with the exception and the emitter does not reconstruct from a message
    what the gate already knew. The set is FR-017's **two**-field form — the
    precondition and its realized value — because a recorded digest is a
    precondition rather than a measured metric with a threshold direction.
    """

    def __init__(self, message: str, *, preconditions: Sequence[UnmetPrecondition]) -> None:
        super().__init__(message)
        self.preconditions = tuple(preconditions)


#: Every refusal this job reports as a message rather than as a traceback. The
#: same list `fit.py` keeps and for the same reason: an unlisted exception is a
#: defect and keeps its traceback, because a message-only report of a bug is a
#: bug nobody can locate.
_REPORTED_FAILURES: tuple[type[Exception], ...] = (
    CompareError,
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
    ReproduceError,
    SampleError,
    SerializeError,
    ShrinkageError,
    SplitError,
    WriteError,
)

#: The two stores FR-022's population ranges over, named as the schema names
#: them. Stated rather than left open: the tolerance is derived across both, and
#: scoping the comparison to one would leave the other's reproduction unclaimed
#: while still borrowing a number derived from both.
LINE_POSTERIOR_STORE = "line_posterior"
HELD_OUT_STORE = "held_out_prediction"

#: FR-043's provenance identity — the set DV-018 compares for **exact** equality.
#: `seed_entropy` is deliberately absent: it is an *input* the re-fit is driven
#: from rather than an outcome it produces, so comparing it would assert that
#: this job passed on the value it read.
PROVENANCE_FIELDS: tuple[str, ...] = (
    "code_commit",
    "code_worktree_dirty",
    "library_versions",
    "model_version",
    "artifact_schema_version",
    "roster_hash",
    "split_seed_entropy",
)

#: SC-018's three outcomes. Three and not two: AD-004 publishes a basis
#: condition with its tolerance, and a comparison taken outside that basis is
#: neither a pass nor a failure — the scope-limit treatment FR-032 already uses
#: for a digest mismatch.
OUTCOME_AGREES = "agrees"
OUTCOME_DISAGREES = "disagrees"
OUTCOME_OUTSIDE_BASIS = "outside the tolerance's stated basis"

#: FR-032's optional draw-digest claim, in its **two** dispositions. There is no
#: failing one, and an earlier revision carried a third: a mismatch inside the
#: recorded pin resolved to `failed`, on the premise that "inside it the
#: environment is the recorded one". That premise is false. `library_versions`
#: records package versions and cannot record BLAS thread count, reduction
#: order, instruction set or scheduling — every one of which moves the low bits
#: of a floating reduction — so the pin does not determine bitwise numerics and
#: a mismatch inside it is an environment difference the pin cannot see. FR-022
#: is explicit that reproduction is the day tolerance and "never bitwise
#: equality of draws" ({SAD:ADR-0009}), so this claim is published where it
#: holds and degrades everywhere else.
DIGEST_CLAIM_EQUAL = "equal"
DIGEST_CLAIM_SCOPE_LIMIT = "scope limit"

#: **Why** a scope limit was reported. Two different facts about a run and a
#: reader is owed both: an observed pin outside the recorded one says the
#: environment moved in a dimension the manifest records, while a matching pin
#: says it moved in a dimension the manifest does not record. Neither is a
#: failure, and collapsing them would tell a reader only that the digests
#: differed — which they already knew from `differing_lines`.
DIGEST_SCOPE_PIN_DIFFERS = "the observed environment differs from the recorded pin"
DIGEST_SCOPE_PIN_DOES_NOT_DETERMINE_NUMERICS = (
    "the observed environment matches the whole recorded pin, and the pin does not determine "
    "bitwise numerics"
)

#: Module-level SQL, never assembled from values (Ruff S608).
ACTIVE_RUN_SQL = text("SELECT run_id FROM v_active_forecast_run")
RECORDED_RUN_SQL = text("SELECT * FROM forecast_run WHERE run_id = :run_id")
RECORDED_ASSIGNMENT_SQL = text(
    """
    SELECT l.project_id, l.po_number, l.line_number, a.split_side, a.is_censored,
           a.canonical_ordinal
    FROM forecast_split_assignment a
    JOIN purchase_order_line l ON l.po_line_id = a.po_line_id
    WHERE a.run_id = :run_id
    """
)
RECORDED_POSTERIOR_SQL = text(
    """
    SELECT po_line_id, draws, draw_digest FROM line_posterior
    WHERE run_id = :run_id ORDER BY po_line_id
    """
)
RECORDED_HELD_OUT_SQL = text(
    """
    SELECT po_line_id, draws, draw_digest FROM held_out_prediction
    WHERE run_id = :run_id ORDER BY po_line_id
    """
)


# ---------------------------------------------------------------------------
# What a recorded run looks like from the outside
# ---------------------------------------------------------------------------


# `eq=False` because `draws` is an array and a generated `__eq__` would compare
# elementwise, yielding an array where a bool is expected.
@dataclasses.dataclass(frozen=True, slots=True, eq=False)
class StoredArtifact:
    """One stored line's draws and the digest recorded over them."""

    po_line_id: uuid.UUID
    draws: NDArray[np.float64]
    draw_digest: bytes


@dataclasses.dataclass(frozen=True, slots=True)
class ProvenanceIdentity:
    """FR-043's field set, in the shape both sides of the comparison produce it.

    One type for the recorded run and for the re-run, so the comparison is a
    field-for-field equality between two instances of one structure rather than
    a hand-written list of seven comparisons that could lose a field.
    """

    code_commit: str
    code_worktree_dirty: bool
    library_versions: dict[str, str]
    model_version: str
    artifact_schema_version: int
    roster_hash: str
    split_seed_entropy: str

    def differing_fields(self, other: ProvenanceIdentity) -> tuple[str, ...]:
        """Every provenance field on which the two runs are not exactly equal."""
        return tuple(
            name for name in PROVENANCE_FIELDS if getattr(self, name) != getattr(other, name)
        )


# `eq=False` because the artifact members hold arrays.
@dataclasses.dataclass(frozen=True, slots=True, eq=False)
class RecordedRun:
    """The run being reproduced, read back out of the five stores.

    Everything the re-fit is driven from and everything the comparison is made
    against, in one record: a caller able to obtain the manifest without the
    artifacts would eventually compare a re-fit against a run it had not read.
    """

    run_id: uuid.UUID
    as_of_date: date
    horizon_days: int
    chain_count: int
    draw_count: int
    tuning_count: int
    seed_entropy: str
    input_data_hash: str
    split_assignment_hash: str
    input_fixture_digest: str
    provenance: ProvenanceIdentity
    artifacts: dict[str, dict[uuid.UUID, StoredArtifact]]

    @property
    def draws_per_chain(self) -> int:
        """The realized per-chain draw count, proved to divide.

        The two columns are stored separately and the sampler takes the per-chain
        figure, so a recorded pair that does not divide describes a run this job
        cannot re-drive — refused rather than rounded, because a re-fit at a
        different shape would produce a comparison of two different runs.
        """
        if self.chain_count <= 0 or self.draw_count % self.chain_count:
            raise ReproduceError(
                f"run {self.run_id} records {self.draw_count} draws over "
                f"{self.chain_count} chains, which does not divide; the sampler takes a "
                f"per-chain count, so there is no shape this job could re-drive the run at"
            )
        return self.draw_count // self.chain_count


@contextmanager
def _reading(target: Engine | Connection | Session) -> Iterator[Connection | Session]:
    """A connection to read over, whether the caller brought an engine or not.

    This job issues no statement that writes, so a caller inside a rolled-back
    transaction gets exactly the same behaviour as one holding an engine — which
    is what lets the refusal paths be exercised against a mutated database
    without committing the mutation.
    """
    if isinstance(target, Engine):
        with target.connect() as connection:
            yield connection
        return
    yield target


def _artifacts(
    connection: Connection | Session, statement, run_id: uuid.UUID
) -> dict[uuid.UUID, StoredArtifact]:
    """One store's rows for one run, keyed by line."""
    return {
        row["po_line_id"]: StoredArtifact(
            po_line_id=row["po_line_id"],
            draws=np.asarray(row["draws"], dtype=float),
            draw_digest=bytes(row["draw_digest"]),
        )
        for row in connection.execute(statement, {"run_id": run_id}).mappings()
    }


def read_recorded_run(
    target: Engine | Connection | Session, run_id: uuid.UUID | None = None
) -> RecordedRun:
    """The run to reproduce, defaulting to whichever one is **active**.

    The default is the pointer rather than the most recent row, for the reason
    `write.py` refuses a recency fallback: "which forecast is current" is a
    stored boolean, and ordering by `created_at` would make a superseded run
    indistinguishable from the live one. A run named explicitly is read whether
    it is active or not, because reproducing a superseded run is a legitimate
    thing to want.
    """
    with _reading(target) as connection:
        if run_id is None:
            active = list(connection.execute(ACTIVE_RUN_SQL).scalars())
            if len(active) != 1:
                raise ReproduceError(
                    f"no run was named and `v_active_forecast_run` returns {len(active)} "
                    f"rows, so there is nothing to reproduce. Name one with `--run-id`, or "
                    f"publish a run first"
                )
            run_id = active[0]
        row = connection.execute(RECORDED_RUN_SQL, {"run_id": run_id}).mappings().one_or_none()
        if row is None:
            raise ReproduceError(
                f"`forecast_run` holds no row for {run_id}; a reproduction is a claim about "
                f"a recorded run, and there is no manifest here to read one from"
            )
        artifacts = {
            LINE_POSTERIOR_STORE: _artifacts(connection, RECORDED_POSTERIOR_SQL, run_id),
            HELD_OUT_STORE: _artifacts(connection, RECORDED_HELD_OUT_SQL, run_id),
        }
    return RecordedRun(
        run_id=row["run_id"],
        as_of_date=row["as_of_date"],
        horizon_days=int(row["horizon_days"]),
        chain_count=int(row["chain_count"]),
        draw_count=int(row["draw_count"]),
        tuning_count=int(row["tuning_count"]),
        seed_entropy=row["seed_entropy"],
        input_data_hash=row["input_data_hash"],
        split_assignment_hash=row["split_assignment_hash"],
        input_fixture_digest=row["input_fixture_digest"],
        provenance=ProvenanceIdentity(
            code_commit=row["code_commit"],
            code_worktree_dirty=bool(row["code_worktree_dirty"]),
            library_versions=dict(row["library_versions"]),
            model_version=row["model_version"],
            artifact_schema_version=int(row["artifact_schema_version"]),
            roster_hash=row["roster_hash"],
            split_seed_entropy=row["split_seed_entropy"],
        ),
        artifacts=artifacts,
    )


# ---------------------------------------------------------------------------
# The two gates (T099, T100 — FR-023, DV-015, DV-016, DV-017)
# ---------------------------------------------------------------------------


def _moved_inputs(
    recorded: RecordedRun,
    observed_row_hash: str,
    observed_split_hash: str,
    rederived_split_hash: str,
) -> tuple[UnmetPrecondition, ...]:
    """Every recorded input digest that no longer matches what is present.

    All of them are evaluated rather than the first refusing, for the reason
    FR-017 gives about diagnostics: an operator handed one moved hash returns to
    discover the next. Each carries the two-field set — the precondition and its
    realized value — which is FR-023's "which hash moved and both values".

    The split is checked against two recomputations, and only one of them is
    independent of the row hash. The first is taken over the stored
    `forecast_split_assignment` rows — DV-017's own form, and the only one that
    moves when an assignment row is edited. The second re-derives the assignment
    from the input under AD-011's three determinants, and is **suppressed when
    the row hash has moved**: the split is keyed on that hash, so it necessarily
    re-derives differently, and reporting it would name a consequence beside the
    cause and leave a reader unable to tell which input actually moved.
    """
    unmet: list[UnmetPrecondition] = []
    if observed_row_hash != recorded.input_data_hash:
        unmet.append(
            UnmetPrecondition(
                precondition=(
                    f"the **input row hash** recorded on run {recorded.run_id} is still what "
                    f"the rows in the database serialize to — recorded "
                    f"`{recorded.input_data_hash}` (FR-023, DV-015)"
                ),
                realized_value=(
                    f"the rows now serialize to `{observed_row_hash}`; the rows are not the "
                    f"rows this run was fitted from"
                ),
            )
        )
    if observed_split_hash != recorded.split_assignment_hash:
        unmet.append(
            UnmetPrecondition(
                precondition=(
                    f"the **split assignment hash** recorded on run {recorded.run_id} is "
                    f"still what its stored assignment rows serialize to — recorded "
                    f"`{recorded.split_assignment_hash}` (FR-023, DV-017)"
                ),
                realized_value=(
                    f"`forecast_split_assignment` now serializes to `{observed_split_hash}`; "
                    f"the held-out split this run was trained against has moved"
                ),
            )
        )
    if (
        observed_row_hash == recorded.input_data_hash
        and rederived_split_hash != recorded.split_assignment_hash
    ):
        unmet.append(
            UnmetPrecondition(
                precondition=(
                    f"the **split assignment hash** re-derived from the input under AD-011's "
                    f"three determinants reproduces the recorded "
                    f"`{recorded.split_assignment_hash}`"
                ),
                realized_value=(
                    f"re-deriving the assignment gives `{rederived_split_hash}`; the split is "
                    f"a pure function of the input hash and two committed constants, so a "
                    f"different value means the derivation itself has moved"
                ),
            )
        )
    return tuple(unmet)


def _refusal(unmet: Sequence[UnmetPrecondition]) -> ReproductionRefusal:
    """FR-023's message: which digest moved, with both of its values.

    Nothing is sampled and nothing is written on this path. The gate runs before
    the sampler is reached, which is what makes "pre-sampling" a property of the
    control flow rather than a claim about it.
    """
    stated = "; ".join(f"{item.precondition} — realized: {item.realized_value}" for item in unmet)
    return ReproductionRefusal(
        f"{len(unmet)} recorded input digest(s) no longer match what is present, so the "
        f"reproduction refuses before sampling and nothing was written: {stated}",
        preconditions=unmet,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class FixtureComparison:
    """DV-016's disposition: a break in the chain back to the upstream artifact.

    Two comparisons rather than one, because the fixture digest has two
    counterparts and they answer different questions: the value **this run
    recorded** says whether the file has moved since the fit, and E005's
    published sidecar says whether the file agrees with its own provenance. Each
    is a warning and never a refusal — the rows the fit read are unchanged.
    """

    observed_digest: str
    recorded_digest: str
    published_digest: str

    @property
    def agrees_with_recorded(self) -> bool:
        return self.observed_digest == self.recorded_digest

    @property
    def agrees_with_published(self) -> bool:
        return self.observed_digest == self.published_digest

    @property
    def agrees(self) -> bool:
        """Whether the fixture on disk still answers to both of its counterparts."""
        return self.agrees_with_recorded and self.agrees_with_published

    def warnings(self) -> tuple[str, ...]:
        """The provenance warning text, naming the break and both values."""
        broken: list[str] = []
        if not self.agrees_with_recorded:
            broken.append(
                f"provenance warning: the committed fixture digests to "
                f"`{self.observed_digest}` against the `{self.recorded_digest}` this run "
                f"recorded at fit time. The rows this reproduction read are unchanged, so it "
                f"proceeds and only the chain back to the upstream artifact has broken "
                f"(FR-023, DV-016)"
            )
        if not self.agrees_with_published:
            broken.append(
                f"provenance warning: the committed fixture digests to "
                f"`{self.observed_digest}` against a published `{self.published_digest}`. "
                f"This is a break between the file and its own sidecar, not a reason to "
                f"refuse: the reproduction is sound and completes with a zero exit"
            )
        return tuple(broken)


# ---------------------------------------------------------------------------
# The re-fit (T096)
# ---------------------------------------------------------------------------


# `eq=False` because the artifact rows hold arrays.
@dataclasses.dataclass(frozen=True, slots=True, eq=False)
class Refit:
    """The second run, in memory. It is never written to any store.

    Carries its own provenance identity rather than a `run_id`, which is what
    FR-022 asks for on this side of the comparison: no run row was written, so
    the re-run is identified by the manifest fields it would have recorded.
    """

    provenance: ProvenanceIdentity
    artifacts: dict[str, dict[uuid.UUID, StoredArtifact]]
    predictive_ess: dict[uuid.UUID, float]
    fixture: FixtureComparison
    chain_count: int
    draws_per_chain: int


def _stored_shape(derived: DerivedArtifacts) -> dict[str, dict[uuid.UUID, StoredArtifact]]:
    """The re-fit's two populations in the shape the recorded side is read in.

    Reduced to the two fields the comparison uses — the draws and the digest —
    so the pairing below is between two objects of one type rather than between
    a database row and an artifact row that merely resemble each other.
    """
    return {
        LINE_POSTERIOR_STORE: {
            row.po_line_id: StoredArtifact(row.po_line_id, row.draws, row.draw_digest)
            for row in derived.line_posteriors
        },
        HELD_OUT_STORE: {
            row.po_line_id: StoredArtifact(row.po_line_id, row.draws, row.draw_digest)
            for row in derived.held_out_predictions
        },
    }


def _predictive_ess(
    sequences: Mapping[uuid.UUID, NDArray[np.float64]], chains: int, draws_per_chain: int
) -> dict[uuid.UUID, float]:
    """AD-004's basis condition, **measured** per line rather than assumed.

    The effective sample size of the *predictive* sequence, which is not the
    parameter ESS the diagnostics gate floors at 400: each stored draw carries
    independent residual and inverse-CDF randomness, and that decorrelates the
    sequence. Measured on the re-fit because it is the only side whose draw
    *order* survives — the stored arrays are sorted, and a sort is a permutation
    that destroys every autocorrelation an ESS is estimated from.
    """
    import arviz as az

    measured: dict[uuid.UUID, float] = {}
    for po_line_id, sequence in sequences.items():
        values = np.asarray(sequence, dtype=float)
        if values.size != chains * draws_per_chain:
            raise ReproduceError(
                f"line {po_line_id}'s predictive sequence carries {values.size} draws "
                f"against a shape of {chains} x {draws_per_chain}; the effective sample "
                f"size is estimated per chain, so a sequence that does not reshape has no "
                f"chain structure to estimate it from"
            )
        measured[po_line_id] = float(az.ess(values.reshape(chains, draws_per_chain)))
    return measured


def refit_recorded(
    target: Engine | Connection | Session,
    recorded: RecordedRun,
    *,
    repo_root: Path | str | None = None,
    cores: int = 1,
    log: TextIO = sys.stderr,
) -> Refit:
    """Run both gates, then re-derive the run at its own recorded shape.

    Every parameter of the second fit comes off the recorded manifest — the
    anchor, the seed entropy, the chain and draw counts, the tuning draws and the
    horizon — so what is under test is the code path rather than a shape this
    function chose. The derivation goes through `fit.py`'s own two seams, which
    is the whole reason they are public: a second sequence written here would
    agree today and drift silently, and the drift would surface as a reproduction
    failure attributed to the model.
    """
    note = _notes(log)
    with _reading(target) as connection:
        procurement_input = read_lines_and_events(connection)
    note(
        f"read {len(procurement_input.lines)} lines and "
        f"{len(procurement_input.events)} lifecycle events"
    )
    observed_row_hash = input_data_hash(procurement_input)

    with _reading(target) as connection:
        stored_assignments = (
            connection.execute(RECORDED_ASSIGNMENT_SQL, {"run_id": recorded.run_id})
            .mappings()
            .all()
        )
    if not stored_assignments:
        raise ReproduceError(
            f"run {recorded.run_id} has no `forecast_split_assignment` rows, so the split "
            f"hash FR-023 refuses on cannot be recomputed from what is present"
        )
    observed_split_hash = split_assignment_hash(
        [_AssignmentRow(**dict(row)) for row in stored_assignments]
    )
    split = assign_split(procurement_input.lines, recorded.as_of_date, observed_row_hash)

    # ---- GATE (T099): a moved row hash or split hash refuses, before sampling.
    unmet = _moved_inputs(
        recorded, observed_row_hash, observed_split_hash, split.split_assignment_hash
    )
    if unmet:
        raise _refusal(unmet)
    note(f"input row hash {observed_row_hash}")
    note(f"split assignment hash {observed_split_hash}")

    # ---- GATE (T100): a moved fixture digest against unchanged rows **warns**.
    provenance = read_fixture_provenance(repo_root)
    fixture = FixtureComparison(
        observed_digest=provenance.observed_digest,
        recorded_digest=recorded.input_fixture_digest,
        published_digest=provenance.published_digest,
    )
    for warning in fixture.warnings():
        note(warning)

    vendors, categories = roster_index(procurement_input.lines)
    frame = training_frame(procurement_input.lines, split, vendors, categories, recorded.as_of_date)
    draws_per_chain = recorded.draws_per_chain
    note(
        f"re-fitting run {recorded.run_id} at {recorded.chain_count} chains x "
        f"{draws_per_chain} draws with {recorded.tuning_count} tuning draws, seed "
        f"{recorded.seed_entropy}"
    )
    sampled = sample_run(
        frame,
        seed_entropy=int(recorded.seed_entropy),
        chains=recorded.chain_count,
        draws=draws_per_chain,
        tune=recorded.tuning_count,
        cores=cores,
    )
    derived = derive_artifacts(
        sampled,
        procurement_input.lines,
        split,
        vendors,
        categories,
        as_of_date=recorded.as_of_date,
        horizon_days=recorded.horizon_days,
    )
    return Refit(
        provenance=_observed_provenance(procurement_input, repo_root),
        artifacts=_stored_shape(derived),
        predictive_ess=_predictive_ess(
            derived.predictive_sequences, recorded.chain_count, draws_per_chain
        ),
        fixture=fixture,
        chain_count=recorded.chain_count,
        draws_per_chain=draws_per_chain,
    )


@dataclasses.dataclass(frozen=True, slots=True)
class _AssignmentRow:
    """A stored assignment row in the shape `split_assignment_hash` reads.

    A record rather than the mapping the driver returns, because the digest is
    defined over attributes and `serialize.py` deliberately accepts a protocol so
    the hash can be recomputed from a database read — which is DV-017's own form.
    """

    project_id: str
    po_number: str
    line_number: int
    split_side: str
    is_censored: bool
    canonical_ordinal: int


def _observed_provenance(
    procurement_input: ProcurementInput, repo_root: Path | str | None
) -> ProvenanceIdentity:
    """The re-run's own manifest provenance, measured exactly as a fit measures it.

    Read from the same functions `build_manifest` reads them from rather than
    copied off the recorded run, which is the entire point: a field taken from
    the run under comparison would be equal by construction and would assert
    nothing about the environment this reproduction actually ran in.
    """
    revision = code_revision(repo_root)
    return ProvenanceIdentity(
        code_commit=revision.commit,
        code_worktree_dirty=revision.worktree_dirty,
        library_versions=library_versions(),
        model_version=MODEL_VERSION,
        artifact_schema_version=ARTIFACT_SCHEMA_VERSION,
        roster_hash=roster_hash_of(procurement_input),
        split_seed_entropy=split_seed_entropy(),
    )


# ---------------------------------------------------------------------------
# The comparison (T097 — FR-022, AD-004, SC-018)
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True, slots=True)
class LineComparison:
    """One line's median or 80th percentile on both sides, with its delta.

    The unit FR-022 requires the outcome to be reported in: the realized value,
    the value it is compared against, the delta between them, the tolerance that
    delta is judged by, and the basis condition measured on the same line.
    """

    store: str
    po_line_id: uuid.UUID
    probability: float
    recorded: float
    reproduced: float
    predictive_ess: float
    draw_count: int

    @property
    def quantity(self) -> str:
        """`median` or `P80`, as a reader names them rather than as a number."""
        if self.probability == MEDIAN_PROBABILITY:
            return "median"
        return f"P{self.probability * 100:.0f}"

    @property
    def delta_days(self) -> float:
        """Re-run minus recorded, signed, in days.

        Signed rather than absolute so the report shows which direction a line
        moved; the tolerance is applied to the magnitude, because a re-run five
        days short breaches exactly as one five days long.
        """
        return self.reproduced - self.recorded

    @property
    def within_basis(self) -> bool:
        """Whether AD-004's published basis condition holds on this line."""
        return self.predictive_ess >= REPRODUCTION_PREDICTIVE_ESS_FRACTION_MIN * self.draw_count

    @property
    def agrees(self) -> bool:
        """Whether this comparison sits inside the published day tolerance."""
        return within_tolerance(self.delta_days, REPRODUCTION_TOLERANCE_DAYS)


@dataclasses.dataclass(frozen=True, slots=True)
class DigestClaim:
    """FR-032's optional draw-digest equality claim, published with its pin.

    Two dispositions, and **neither of them fails the run**. Equal digests are a
    bitwise reproduction and are published as one, because a stronger positive
    claim is worth keeping where it holds. Anything else is a reported scope
    limit: FR-032 requires the claim to degrade "when the observed environment
    differs", and the recorded pin does not span the environment — it records
    package versions, not the BLAS thread count or reduction order a floating
    sum is sensitive to. What the pin *does* decide is `scope_reason`, which
    names which kind of difference was observed.
    """

    verdict: str
    differing_lines: tuple[uuid.UUID, ...]
    recorded_pin: dict[str, str]
    observed_pin: dict[str, str]

    @property
    def differing_pin_keys(self) -> tuple[str, ...]:
        """Every key of the **whole** recorded set on which the pins differ.

        The whole set rather than a chosen subset of it (SC-030), so no
        alternative pairing of operands satisfies the criterion — an
        implementation comparing only `pymc` would report agreement on an
        environment whose BLAS had changed underneath it.
        """
        keys = sorted(set(self.recorded_pin) | set(self.observed_pin))
        return tuple(
            key for key in keys if self.recorded_pin.get(key) != self.observed_pin.get(key)
        )

    @property
    def scope_reason(self) -> str | None:
        """Why the claim degraded, or `None` where it did not degrade at all.

        Derived rather than stored, because both operands it is decided from are
        already on the record and a second field could disagree with them.
        """
        if not self.differing_lines:
            return None
        if self.differing_pin_keys:
            return DIGEST_SCOPE_PIN_DIFFERS
        return DIGEST_SCOPE_PIN_DOES_NOT_DETERMINE_NUMERICS


def digest_claim(
    recorded: Mapping[str, Mapping[uuid.UUID, StoredArtifact]],
    reproduced: Mapping[str, Mapping[uuid.UUID, StoredArtifact]],
    recorded_pin: Mapping[str, str],
    observed_pin: Mapping[str, str],
) -> DigestClaim:
    """The draw-digest claim, and the pin it is published with (FR-032, DV-019, SC-030).

    Equal digests are a bitwise reproduction and are published as one. Unequal
    digests are a **scope limit in every case** — never a failure — and the pin
    decides only which reason is reported. Outside the pin the environment
    differs in a dimension the manifest records; inside it the environment still
    differs, in one the manifest does not: `library_versions` cannot record a
    BLAS thread count, a reduction order, a processor instruction set or a
    scheduling decision, and each of those moves the low bits of a floating
    reduction while every recorded version holds still. An earlier revision
    resolved that second case to a failure on the stated premise that "inside it
    the environment is the recorded one", which is the premise this docstring
    exists to withdraw.

    FR-022's reproduction verdict is never this — it is the day tolerance, and
    explicitly "never bitwise equality of draws" — which is why this returns its
    own disposition rather than folding into that one, and why no disposition it
    returns reaches the exit status.
    """
    differing = tuple(
        po_line_id
        for store, rows in recorded.items()
        for po_line_id, artifact in sorted(rows.items(), key=lambda pair: str(pair[0]))
        if po_line_id not in reproduced.get(store, {})
        or reproduced[store][po_line_id].draw_digest != artifact.draw_digest
    )
    return DigestClaim(
        verdict=DIGEST_CLAIM_EQUAL if not differing else DIGEST_CLAIM_SCOPE_LIMIT,
        differing_lines=differing,
        recorded_pin=dict(recorded_pin),
        observed_pin=dict(observed_pin),
    )


# `eq=False` because the comparison members reach arrays through their runs.
@dataclasses.dataclass(frozen=True, slots=True, eq=False)
class ReproductionOutcome:
    """FR-038's unit for FR-022: the deltas, the tolerance, and one verdict.

    The two operands are named on the record rather than left to the reader:
    the recorded run by `run_id`, the re-run by its manifest provenance fields,
    because a verdict that does not name what it compared does not resolve to the
    artifacts it was computed from.
    """

    run_id: uuid.UUID
    as_of_date: date
    recorded_provenance: ProvenanceIdentity
    reproduced_provenance: ProvenanceIdentity
    comparisons: tuple[LineComparison, ...]
    unpaired: tuple[tuple[str, uuid.UUID], ...]
    fixture: FixtureComparison
    claim: DigestClaim
    chain_count: int
    draws_per_chain: int
    wall_clock_seconds: float

    @property
    def draw_count(self) -> int:
        return self.chain_count * self.draws_per_chain

    @property
    def differing_provenance_fields(self) -> tuple[str, ...]:
        return self.recorded_provenance.differing_fields(self.reproduced_provenance)

    @property
    def outside_basis(self) -> tuple[LineComparison, ...]:
        """Every comparison whose line fell below AD-004's predictive ESS floor."""
        return tuple(row for row in self.comparisons if not row.within_basis)

    @property
    def breaches(self) -> tuple[LineComparison, ...]:
        """Every comparison outside the published day tolerance, in table order."""
        return tuple(row for row in self.comparisons if not row.agrees)

    @property
    def worst(self) -> LineComparison:
        """The largest absolute delta — the realized value the verdict is about."""
        return max(self.comparisons, key=lambda row: abs(row.delta_days))

    @property
    def verdict(self) -> str:
        """One of SC-018's three outcomes, resolved in a stated order.

        Provenance first, because exact equality of the manifest's provenance
        fields is a precondition of the comparison rather than a question about
        its precision: two runs on different code are not two runs of one thing,
        and no basis condition qualifies that. Then the basis condition, which
        AD-004 places ahead of the tolerance — outside it the comparison is
        neither a pass nor a failure. Then the tolerance itself.
        """
        if self.differing_provenance_fields or self.unpaired:
            return OUTCOME_DISAGREES
        if self.outside_basis:
            return OUTCOME_OUTSIDE_BASIS
        return (
            OUTCOME_AGREES
            if within_tolerance(
                [row.delta_days for row in self.comparisons], REPRODUCTION_TOLERANCE_DAYS
            )
            else OUTCOME_DISAGREES
        )

    @property
    def exit_status(self) -> int:
        """Zero exactly when every required action completed (FR-017, FR-039).

        **FR-022's three outcomes govern this and nothing else does.**
        `OUTCOME_DISAGREES` is the single non-zero case;
        `OUTCOME_OUTSIDE_BASIS` is a reported scope limit and exits zero, and so
        does every disposition of the optional digest claim. This deliberately
        does **not** read `self.claim.verdict`: an earlier revision did, which
        made a bitwise digest mismatch fail the job and put the reproduction
        gate on exactly the quantity FR-022 says it is never expressed as.
        """
        return 1 if self.verdict == OUTCOME_DISAGREES else 0


def _paired(
    recorded: RecordedRun, refit: Refit
) -> tuple[tuple[LineComparison, ...], tuple[tuple[str, uuid.UUID], ...]]:
    """Every stored line in **both** stores, paired across the two runs.

    A line present on one side and absent from the other is reported as unpaired
    rather than skipped: dropping it would let a re-fit that produced half the
    population agree with the half it produced, which is a comparison of a subset
    reported as a comparison of the population.
    """
    comparisons: list[LineComparison] = []
    unpaired: list[tuple[str, uuid.UUID]] = []
    for store in (LINE_POSTERIOR_STORE, HELD_OUT_STORE):
        left, right = recorded.artifacts[store], refit.artifacts[store]
        unpaired.extend((store, key) for key in sorted(set(left) ^ set(right), key=str))
        for po_line_id in sorted(set(left) & set(right), key=str):
            for probability in (MEDIAN_PROBABILITY, P80_PROBABILITY):
                comparisons.append(
                    LineComparison(
                        store=store,
                        po_line_id=po_line_id,
                        probability=probability,
                        recorded=nearest_rank_percentile(left[po_line_id].draws, probability),
                        reproduced=nearest_rank_percentile(right[po_line_id].draws, probability),
                        predictive_ess=refit.predictive_ess[po_line_id],
                        draw_count=recorded.draw_count,
                    )
                )
    return tuple(comparisons), tuple(unpaired)


def compare_reproduction(
    recorded: RecordedRun, refit: Refit, *, wall_clock_seconds: float = 0.0
) -> ReproductionOutcome:
    """Pair every stored line across both stores and resolve one verdict.

    The population is every line in `line_posterior` **and** every line in
    `held_out_prediction`, because the published tolerance is derived across both
    and scoping the comparison to one store would leave the other's reproduction
    unclaimed while still borrowing a number derived from both.
    """
    comparisons, unpaired = _paired(recorded, refit)
    if not comparisons:
        raise ReproduceError(
            f"run {recorded.run_id} paired no line at all across the two stores, so there "
            f"is no comparison to publish; a harness that paired nothing would otherwise "
            f"report agreement having compared nothing"
        )
    return ReproductionOutcome(
        run_id=recorded.run_id,
        as_of_date=recorded.as_of_date,
        recorded_provenance=recorded.provenance,
        reproduced_provenance=refit.provenance,
        comparisons=comparisons,
        unpaired=unpaired,
        fixture=refit.fixture,
        claim=digest_claim(
            recorded.artifacts,
            refit.artifacts,
            recorded.provenance.library_versions,
            refit.provenance.library_versions,
        ),
        chain_count=refit.chain_count,
        draws_per_chain=refit.draws_per_chain,
        wall_clock_seconds=float(wall_clock_seconds),
    )


# ---------------------------------------------------------------------------
# The reproduction report (T098 — FR-040, FR-038) [COMPLETES FR-040]
# ---------------------------------------------------------------------------

#: The third of FR-040's three kinds, with a closed schema of its own. Closed for
#: the reason the run report's is: SC-026's absence check is a structural
#: predicate over declared fields rather than the term search FR-040 rejects, and
#: a section that could carry an undeclared field is a place a verdict about
#: forecast *quality* could live unexamined.
REPRODUCTION_SECTION_TITLES: tuple[str, ...] = (
    "Compared Runs",
    "Input Provenance",
    "Reproduction Outcome",
    "Per-Line Comparison",
    "Draw-Digest Claim",
    "Emitted Report Set",
)

#: Every field name any section of this report may carry. `Per-Line Comparison`
#: renders its eight as **table columns** rather than as bullets, because the
#: population is ~136 comparisons and a bulleted list of them is not a document
#: anybody reads — the field names are the header row.
REPRODUCTION_SECTION_FIELDS: dict[str, tuple[str, ...]] = {
    "Compared Runs": (
        "Recorded run",
        "Re-run",
        "Re-run provenance",
        "As-of date",
        "Sampling shape",
        "Wall clock",
    ),
    "Input Provenance": (
        "Input row hash",
        "Split assignment hash",
        "Fixture file digest",
        "Fixture digest agreement",
        "Provenance field equality",
    ),
    "Reproduction Outcome": (
        "Measure",
        "Realized value",
        "Decision criterion",
        "Basis condition",
        "Verdict",
    ),
    "Per-Line Comparison": (
        "Store",
        "Line",
        "Quantity",
        "Recorded",
        "Re-run",
        "Delta",
        "Predictive ESS",
        "Verdict",
    ),
    "Draw-Digest Claim": (
        "Recorded library pin",
        "Observed library pin",
        "Digest agreement",
        "Decision criterion",
        "Verdict",
    ),
    "Emitted Report Set": ("Report kind", "This file"),
}


def _compared_runs_section(outcome: ReproductionOutcome) -> list[str]:
    """FR-022's "identify both runs it compared", as fields rather than as prose."""
    provenance = outcome.reproduced_provenance
    return [
        f"- **Recorded run**: `{outcome.run_id}` — the run whose manifest was read.",
        "- **Re-run**: no `run_id` — this job writes no run row, no artifact and no "
        "pointer, so the re-run is identified by the manifest provenance fields it would "
        "have recorded (FR-022).",
        f"- **Re-run provenance**: code `{provenance.code_commit}`, worktree "
        f"{'modified' if provenance.code_worktree_dirty else 'clean'}, model "
        f"`{provenance.model_version}`, artifact schema "
        f"{provenance.artifact_schema_version}, roster `{provenance.roster_hash}`, split "
        f"seed `{provenance.split_seed_entropy}`.",
        f"- **As-of date**: {outcome.as_of_date.isoformat()}",
        f"- **Sampling shape**: {outcome.chain_count} chains x {outcome.draws_per_chain} "
        f"draws = {outcome.draw_count} draws per line, re-driven from the recorded manifest "
        f"rather than from this job's defaults.",
        f"- **Wall clock**: {outcome.wall_clock_seconds:.1f} seconds — recorded, with no "
        f"criterion and therefore no verdict.",
    ]


def _input_provenance_section(outcome: ReproductionOutcome, recorded: RecordedRun) -> list[str]:
    """The two digests that refuse and the one that warns, with their dispositions."""
    fixture = outcome.fixture
    if fixture.agrees:
        agreement = "agrees with the digest this run recorded and with the digest E005 publishes"
    else:
        agreement = "; ".join(fixture.warnings())
    differing = outcome.differing_provenance_fields
    equality = (
        f"**exactly equal** on all {len(PROVENANCE_FIELDS)} fields FR-043 names"
        if not differing
        else f"**differs on** {', '.join(f'`{name}`' for name in differing)}"
    )
    return [
        f"- **Input row hash**: `{recorded.input_data_hash}` — unchanged; a moved value "
        f"refuses before sampling and names itself (FR-023, DV-015).",
        f"- **Split assignment hash**: `{recorded.split_assignment_hash}` — unchanged, "
        f"recomputed both from the stored assignment rows and from the input (DV-017).",
        f"- **Fixture file digest**: `{fixture.observed_digest}`",
        f"- **Fixture digest agreement**: {agreement}.",
        f"- **Provenance field equality**: {equality}.",
    ]


def _outcome_section(outcome: ReproductionOutcome) -> list[str]:
    """FR-038's unit for SC-018: measure, value, criterion with direction, verdict.

    The basis condition is a field of its own rather than a footnote, because
    AD-004 publishes it *with* the number: a reader holding a 5.0-day tolerance
    and not holding the predictive-ESS condition cannot tell an agreement from a
    comparison taken outside the tolerance's stated basis.
    """
    worst = outcome.worst
    breaches = outcome.breaches
    outside = outcome.outside_basis
    if outcome.verdict == OUTCOME_AGREES:
        verdict = (
            f"**agrees** — every one of {len(outcome.comparisons)} per-line comparisons sits "
            f"within {REPRODUCTION_TOLERANCE_DAYS:.1f} days, and the manifest's provenance "
            f"fields are exactly equal."
        )
    elif outcome.verdict == OUTCOME_OUTSIDE_BASIS:
        verdict = (
            f"**outside the tolerance's stated basis** — neither a pass nor a failure. "
            f"{len({row.po_line_id for row in outside})} line(s) realize a predictive "
            f"effective sample size below "
            f"{REPRODUCTION_PREDICTIVE_ESS_FRACTION_MIN:.2f} x {outcome.draw_count}, which is "
            f"the condition AD-004 published the 5.0-day tolerance under. Reported as a scope "
            f"limit, the treatment FR-032 already uses for a digest mismatch."
        )
    else:
        parts = []
        if outcome.differing_provenance_fields:
            parts.append(
                f"the manifest's provenance fields differ on "
                f"{', '.join(f'`{name}`' for name in outcome.differing_provenance_fields)}"
            )
        if outcome.unpaired:
            parts.append(
                f"{len(outcome.unpaired)} stored line(s) have no counterpart — first "
                f"{outcome.unpaired[0][1]} in `{outcome.unpaired[0][0]}`"
            )
        if breaches:
            parts.append(
                f"{len(breaches)} comparison(s) fall outside the tolerance, the largest on "
                f"line {breaches[0].po_line_id} ({breaches[0].store}, "
                f"{breaches[0].quantity}) at {breaches[0].delta_days:+.2f} days"
            )
        verdict = f"**disagrees** — {'; '.join(parts)}."
    return [
        "- **Measure**: the per-line absolute difference between the recorded run and the "
        "re-run, on each line's median and 80th percentile, over both artifact stores "
        f"under the `{PERCENTILE_CONVENTION}` convention. Never bitwise equality of draws, "
        "and never an aggregate — an aggregate can agree while individual lines move in "
        "compensating directions.",
        f"- **Realized value**: largest absolute delta {abs(worst.delta_days):.2f} days, on "
        f"line {worst.po_line_id} ({worst.store}, {worst.quantity}); "
        f"{len(outcome.comparisons)} comparisons over "
        f"{len({row.po_line_id for row in outcome.comparisons})} lines.",
        f"- **Decision criterion**: at or below {REPRODUCTION_TOLERANCE_DAYS:.1f} days — a "
        f"**ceiling**, so the passing direction is downward — together with **exact** "
        f"equality of the manifest's provenance fields. Pre-registered at AD-004 before any "
        f"reproduction result existed and never widened after seeing a comparison.",
        f"- **Basis condition**: the realized per-line **predictive** effective sample size, "
        f"which is not the parameter ESS the diagnostics gate floors at 400, must reach "
        f"{REPRODUCTION_PREDICTIVE_ESS_FRACTION_MIN:.2f} x {outcome.draw_count} = "
        f"{REPRODUCTION_PREDICTIVE_ESS_FRACTION_MIN * outcome.draw_count:.0f}. Realized "
        f"minimum {min(row.predictive_ess for row in outcome.comparisons):.0f} over "
        f"{len(outcome.outside_basis)} comparison(s) below it.",
        f"- **Verdict**: {verdict}",
    ]


def _per_line_section(outcome: ReproductionOutcome) -> list[str]:
    """Every realized per-line delta, which FR-022 requires the outcome to carry."""
    lines = [
        f"{len(outcome.comparisons)} comparisons — every stored line in both stores, at two "
        f"quantities each. The deltas are signed (re-run minus recorded); the tolerance is "
        f"applied to the magnitude.",
        "",
        "| Store | Line | Quantity | Recorded | Re-run | Delta | Predictive ESS | Verdict |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for row in outcome.comparisons:
        if not row.within_basis:
            verdict = "outside basis"
        else:
            verdict = "within" if row.agrees else "**outside**"
        lines.append(
            f"| `{row.store}` | `{row.po_line_id}` | {row.quantity} | {row.recorded:.2f} | "
            f"{row.reproduced:.2f} | {row.delta_days:+.2f} | {row.predictive_ess:.0f} | "
            f"{verdict} |"
        )
    return lines


def _pin_agreement(differing_keys: Sequence[str]) -> str:
    """How the observed environment stands against the whole recorded pin."""
    if not differing_keys:
        return "the pins are identical on every key"
    return "the pins differ on " + ", ".join(f"`{key}`" for key in differing_keys)


def _digest_claim_section(outcome: ReproductionOutcome) -> list[str]:
    """FR-032's optional claim, published with the pin it is reported against.

    The two degraded readings are rendered apart rather than as one, because
    they are different facts about the run: one says the environment moved in a
    dimension `library_versions` records, the other says it moved in a dimension
    `library_versions` cannot record. Both are scope limits and neither is a
    failure, which the text states in those words on both branches so a reader
    scanning for the disposition finds the same phrase either way.
    """
    claim = outcome.claim
    differing_keys = claim.differing_pin_keys
    if claim.verdict == DIGEST_CLAIM_EQUAL:
        verdict = (
            "**equal** — every stored line's draw digest is reproduced bit for bit. "
            "Published as an additional claim, and a stronger one than the tolerance above; "
            "FR-022's reproduction verdict is the day tolerance and is never this."
        )
    elif claim.scope_reason == DIGEST_SCOPE_PIN_DIFFERS:
        verdict = (
            f"**scope limit, not a failure** — {len(claim.differing_lines)} line(s) digest "
            f"differently under an environment that differs from the recorded pin on "
            f"{', '.join(f'`{key}`' for key in differing_keys)}. Bitwise equality was never "
            f"claimed across library versions, so the claim degrades rather than fails "
            f"(FR-032, SC-030)."
        )
    else:
        verdict = (
            f"**scope limit, not a failure** — {len(claim.differing_lines)} line(s) digest "
            f"differently while the observed environment matches the whole recorded pin. "
            f"The pin is not a numeric determinant: `library_versions` records package "
            f"versions and cannot record the BLAS thread count, the reduction order, the "
            f"processor instruction set or the scheduling that decide the low bits of a "
            f"floating sum, so an in-pin mismatch is still an environment difference — one "
            f"the manifest has no field for. FR-022's tolerance above is the gate, and it is "
            f"never bitwise equality of draws (FR-032, SC-030, DV-019, G-21). First line "
            f"{claim.differing_lines[0]}."
        )
    return [
        f"- **Recorded library pin**: "
        f"{', '.join(f'`{k}` {v}' for k, v in sorted(claim.recorded_pin.items()))}",
        f"- **Observed library pin**: "
        f"{', '.join(f'`{k}` {v}' for k, v in sorted(claim.observed_pin.items()))}",
        f"- **Digest agreement**: {len(claim.differing_lines)} line(s) of "
        f"{len({row.po_line_id for row in outcome.comparisons})} compared differ.",
        f"- **Decision criterion**: every stored draw digest equal, compared against the "
        f"**whole recorded `library_versions` set** rather than a chosen subset of it — "
        f"{_pin_agreement(differing_keys)}. The criterion decides what is *published*, never "
        f"the exit status: this claim has no failing disposition.",
        f"- **Verdict**: {verdict}",
    ]


def _emitted_set_section() -> list[str]:
    """FR-040's membership, stated in the artifact rather than only in a document."""
    lines = [
        "Exactly three report kinds are emitted by this epic, enumerated rather than left "
        "as a category:",
        "",
    ]
    lines += [
        f"- **Report kind** — {name}: {description}" for name, description in EMITTED_REPORT_KINDS
    ]
    lines += ["", "- **This file**: reproduction report."]
    return lines


def render_reproduction_report(outcome: ReproductionOutcome, recorded: RecordedRun) -> str:
    """The whole reproduction report, as Markdown under its declared schema.

    Deterministic: no clock read and no environment read. Every figure comes from
    the outcome the comparison produced, so the file and the verdict the job
    exited on cannot disagree about what happened.
    """
    bodies: dict[str, list[str]] = {
        "Compared Runs": _compared_runs_section(outcome),
        "Input Provenance": _input_provenance_section(outcome, recorded),
        "Reproduction Outcome": _outcome_section(outcome),
        "Per-Line Comparison": _per_line_section(outcome),
        "Draw-Digest Claim": _digest_claim_section(outcome),
        "Emitted Report Set": _emitted_set_section(),
    }
    missing = [title for title in REPRODUCTION_SECTION_TITLES if title not in bodies]
    if missing:
        raise ReportError(
            f"the reproduction report's declared schema names section(s) {missing} that this "
            f"renderer does not emit; a declared section with no body is a field a reader is "
            f"owed and does not get"
        )
    parts: list[str] = [
        "# Forecast Reproduction Report",
        "",
        f"Run `{outcome.run_id}` re-derived from its own recorded manifest and compared "
        f"line by line. Every field below belongs to this report's declared schema; nothing "
        f"here is a coverage threshold, a calibration verdict, or a judgement on forecast "
        f"quality — the verdict is about whether the run *reproduces*, which is a different "
        f"question and the only one this job asks (FR-026).",
        "",
    ]
    for ordinal, title in enumerate(REPRODUCTION_SECTION_TITLES, start=1):
        parts += [f"## {ordinal}. {title}", "", *bodies[title], ""]
    return "\n".join(parts).rstrip("\n") + "\n"


def write_reproduction_report(
    outcome: ReproductionOutcome, recorded: RecordedRun, report_root: Path | str | None = None
) -> Path:
    """Render the report and write it to `paths.reproduction_report_path`.

    Named by the **recorded** run's identifier, because a reproduction has none
    of its own and that is the identifier a reader arrives holding.
    """
    text_body = render_reproduction_report(outcome, recorded)
    target = reproduction_report_path(outcome.run_id, report_root)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(text_body.encode("utf-8"))
    return target


# ---------------------------------------------------------------------------
# The job
# ---------------------------------------------------------------------------


def _notes(log: TextIO) -> Callable[[str], None]:
    """A one-line diagnostic writer bound to the caller's stream.

    Every diagnostic goes here and never to standard output, which carries the
    single reproduced `run_id` and nothing else (FR-039). Taking the stream as an
    argument is what lets a test capture the two separately.
    """

    def note(message: str) -> None:
        print(message, file=log)

    return note


# `eq=False` because every member reaches an array through its run.
@dataclasses.dataclass(frozen=True, slots=True, eq=False)
class Reproduction:
    """One completed reproduction: both operands, the verdict, and the file.

    The two runs travel with the outcome rather than being discarded, because a
    verdict is a claim *about* them — a caller holding only the verdict cannot
    check it, and this job writes nothing to any store for one to be read back
    from later.
    """

    recorded: RecordedRun
    refit: Refit
    outcome: ReproductionOutcome
    report: Path


def run_reproduce(
    target: Engine | Connection | Session,
    run_id: uuid.UUID | None = None,
    *,
    report_root: Path | str | None = None,
    repo_root: Path | str | None = None,
    cores: int = 1,
    log: TextIO = sys.stderr,
) -> Reproduction:
    """Read the recorded run, re-fit it, compare, and emit the report.

    A refusal here emits a refusal report of its own (FR-037) and re-raises: the
    stderr text and that file are the only surviving record of why a reproduction
    refused, and this job writes no row anywhere to leave a trace in.
    """
    started = time.monotonic()
    attempted_at = datetime.now(UTC)
    note = _notes(log)
    recorded = read_recorded_run(target, run_id)
    note(f"reproducing run {recorded.run_id} at as-of date {recorded.as_of_date}")

    try:
        refit = refit_recorded(target, recorded, repo_root=repo_root, cores=cores, log=log)
    except _REPORTED_FAILURES as exc:
        emitted = write_refusal_report(
            RefusedAttempt(
                as_of_date=recorded.as_of_date,
                input_data_hash=recorded.input_data_hash,
                attempted_at=attempted_at,
                reason=f"{type(exc).__name__}: {exc}",
                wall_clock_seconds=time.monotonic() - started,
                sampled_shape=None,
                preconditions=getattr(exc, "preconditions", ()),
            ),
            report_root,
        )
        note(f"refusal report at {emitted}")
        raise

    outcome = compare_reproduction(recorded, refit, wall_clock_seconds=time.monotonic() - started)
    emitted = write_reproduction_report(outcome, recorded, report_root)
    note(f"reproduction report at {emitted}")
    claimed = outcome.claim.verdict
    if outcome.claim.scope_reason is not None:
        claimed = f"{claimed} ({outcome.claim.scope_reason})"
    note(f"verdict: {outcome.verdict}; draw-digest claim: {claimed}")
    return Reproduction(recorded=recorded, refit=refit, outcome=outcome, report=emitted)


def _parser() -> argparse.ArgumentParser:
    """The job's arguments. Nothing here overrides a recorded value.

    The shape, the seed, the anchor and the horizon are read off the manifest and
    are deliberately **not** flags: a reproduction driven at a shape the operator
    chose is a comparison of two different runs, and offering the choice would be
    the move AD-005 makes the split's constants committed to prevent.
    """
    parser = argparse.ArgumentParser(
        prog="forecast-reproduce",
        description=(
            "Re-derive a recorded forecast run and compare it line by line. Prints the "
            "reproduced run_id on standard output; every diagnostic goes to standard error."
        ),
    )
    parser.add_argument(
        "--run-id",
        type=uuid.UUID,
        default=None,
        help=(
            "the run to reproduce. Omitted, the active run is used — the stored pointer "
            "rather than the most recent row, because recency would make a superseded run "
            "indistinguishable from the live one."
        ),
    )
    parser.add_argument(
        "--cores",
        type=int,
        default=1,
        help=(
            "sampler worker processes, one chain each. Defaults to 1 for the reason "
            "`forecast-fit` does: PyMC's default spawns processes, which on Windows "
            "re-imports the entry point and deadlocks."
        ),
    )
    parser.add_argument(
        "--report-root",
        type=Path,
        default=None,
        help="where the reproduction report is written; defaults to this checkout's tree.",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Reproduce one run. **Standard output carries one line: the `run_id`.**

    Exit zero exactly when every required action completed, which includes every
    scope limit: AD-004's outside-the-basis outcome, and FR-032's digest claim in
    either of its degraded readings, are reported rather than failed. The only
    non-zero completion is FR-022's `disagrees`. A refusal writes its reason to
    standard error, puts nothing at all on standard output, and exits the single
    non-zero class every refusal in this package shares — so a consumer tests the
    status against zero rather than against a particular value.
    """
    arguments = _parser().parse_args(argv)
    try:
        engine = create_engine(get_database_url())
        try:
            reproduction = run_reproduce(
                engine,
                arguments.run_id,
                report_root=arguments.report_root,
                cores=arguments.cores,
            )
        finally:
            engine.dispose()
    except _REPORTED_FAILURES as exc:
        print(f"forecast-reproduce refused: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(reproduction.outcome.run_id)
    return reproduction.outcome.exit_status


if __name__ == "__main__":  # pragma: no cover - exercised through the console script
    raise SystemExit(main())
