"""Two transactions, per AD-010: the artifact set, then the pointer.

Transaction 1 inserts the run row, the split assignments and **both** artifact
populations; transaction 2 sets `is_active`. Splitting the pointer off is what
lets a run be written and reviewed before it becomes the one downstream readers
see, which is what makes FR-015's "explicit, never implied by recency" operable.
Both arrays of an artifact row are columns of **one** row written by **one**
statement, so no reader can observe them in disagreement (FR-013). Artifact rows
are inserted once and never updated — `UPDATE` was withheld from the grant
deliberately (FR-034).

The two populations are two statements against two tables in one transaction,
per {SAD:ADR-0018}: `line_posterior` holds open lines anchored at the run's
as-of date, `held_out_prediction` holds held-out lines that already delivered,
anchored per row at each line's own order date. Everything they share — the
array shapes, the digest agreement — is checked by one function here, so the
duplication ADR-0018 accepts in the schema is not repeated in the writer.

`forecast_diagnostic` is step 5 of that same transaction, so a run's evidence
that it converged is durable exactly when its artifacts are: there is no state
in which the posteriors exist and the diagnostics do not, and none in which a
constraint rejects a diagnostic row after the artifacts have committed.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date, datetime

import numpy as np
from numpy.typing import NDArray
from sqlalchemy import Connection, Engine, text
from sqlalchemy.orm import Session

from model.forecast.diagnostics import (
    PARAMETER_METRICS,
    RUN_METRICS,
    DiagnosticRow,
    blocking_breaches,
    monitored_parameter_coverage,
)
from model.forecast.manifest import RunManifest, draw_digest
from model.forecast.split import SplitAssignment

__all__ = [
    "ACTIVATE_RUN_SQL",
    "CLEAR_ACTIVE_RUN_SQL",
    "DIAGNOSTIC_INSERT",
    "HELD_OUT_ANCHOR_CONVENTION",
    "HELD_OUT_DURATION_SEMANTIC",
    "HELD_OUT_PREDICTION_INSERT",
    "LINE_POSTERIOR_INSERT",
    "RUN_INSERT",
    "SPLIT_ASSIGNMENT_INSERT",
    "HeldOutPredictionRow",
    "LinePosteriorRow",
    "WriteError",
    "insert_artifact_set",
    "set_active_run",
    "write_artifact_set",
]


class WriteError(RuntimeError):
    """Raised when a row the writer assembled cannot be stored as the run says.

    A `RuntimeError` and never a refusal of the *run*: by the time this module
    runs, the diagnostics gate has passed and the run was judged sound, so a
    disagreement here is a defect in the writer. `plan.md` § Error Handling makes
    that distinction load-bearing — the data outcome is identical to a refusal,
    which is exactly why the report has to tell them apart.
    """


# ---------------------------------------------------------------------------
# Statements. Module-level and parameterised, never assembled from values.
# ---------------------------------------------------------------------------

#: Every column one fit writes, named explicitly. `is_active` and `created_at`
#: are **omitted** so their delivered defaults apply: a run is inserted inactive
#: (FR-015) and `created_at` is the database's answer to "when", not the job's.
#:
#: The two `jsonb` columns are cast server-side from the canonical text
#: `RunManifest.row_parameters` renders, following E003's own forecast fixtures
#: rather than relying on a driver's dict adaptation — the serialization those
#: bytes were produced under is the one the run row records.
RUN_INSERT = text(
    """
    INSERT INTO forecast_run (
        run_id, code_commit, code_worktree_dirty, input_data_hash, seed_entropy,
        chain_count, draw_count, tuning_count, library_versions, artifact_hash,
        draw_serialization, artifact_schema_version, model_version, as_of_date,
        horizon_days, wall_clock_seconds, roster_hash,
        covariate_names, open_line_draw_semantic, input_fixture_digest, input_layer,
        input_datasheet_ref, canonical_serialization, split_seed_entropy,
        split_assignment_hash, held_out_fraction_declared, held_out_fraction_realized,
        held_out_uncensored_event_count, vendor_shrinkage, open_line_count,
        training_line_count
    )
    VALUES (
        :run_id, :code_commit, :code_worktree_dirty, :input_data_hash, :seed_entropy,
        :chain_count, :draw_count, :tuning_count, CAST(:library_versions AS jsonb),
        :artifact_hash, :draw_serialization, :artifact_schema_version, :model_version,
        :as_of_date, :horizon_days, :wall_clock_seconds, :roster_hash,
        :covariate_names, :open_line_draw_semantic, :input_fixture_digest,
        :input_layer, :input_datasheet_ref, :canonical_serialization, :split_seed_entropy,
        :split_assignment_hash, :held_out_fraction_declared, :held_out_fraction_realized,
        :held_out_uncensored_event_count, CAST(:vendor_shrinkage AS jsonb), :open_line_count,
        :training_line_count
    )
    """
)

#: One row per line per run, in canonical order. `canonical_ordinal` is stored so
#: the split hash is recomputable from this table alone (DV-017), without
#: re-reading `purchase_order_line`.
SPLIT_ASSIGNMENT_INSERT = text(
    """
    INSERT INTO forecast_split_assignment (
        run_id, po_line_id, split_side, is_censored, canonical_ordinal
    )
    VALUES (:run_id, :po_line_id, :split_side, :is_censored, :canonical_ordinal)
    """
)

#: **FR-013 as one statement.** `draws` and `survival` are two NOT NULL columns
#: of the same row, so "the draws were written and the survival curve was not" is
#: not a state the database can be in — there is no second row to be missing and
#: nothing for a trigger or a deferred constraint to police (E003 invariant 21).
#:
#: `draw_count` and `horizon_days` are copied onto the row because they are the
#: columns `fk_line_posterior__run_shape` compares: the foreign key proves they
#: are this run's own values, and the two `array_length` checks then compare each
#: array against a number already proven correct.
LINE_POSTERIOR_INSERT = text(
    """
    INSERT INTO line_posterior (
        run_id, po_line_id, draw_count, horizon_days,
        draws, survival, residual_tail_mass, draw_digest
    )
    VALUES (
        :run_id, :po_line_id, :draw_count, :horizon_days,
        :draws, :survival, :residual_tail_mass, :draw_digest
    )
    """
)

#: The two labels `held_out_prediction` records **per row**, and the only two
#: values `ck_held_out_prediction__anchor_convention` and
#: `…__duration_semantic` admit. Per row rather than on the run, unlike the open
#: population's `forecast_run.open_line_draw_semantic`: each population records
#: its anchor and its semantic where the population lives, and this one's anchor
#: is a per-line date. Named here, beside the statement that writes them, so the
#: label and the column it lands in are one fact.
#:
#: Neither is evidence on its own — both are single-value checks a re-anchored
#: implementation satisfies identically — which is why DV-040 measures the
#: semantic over the stored draws and `fk_held_out_prediction__line_anchor`
#: proves the anchor.
HELD_OUT_ANCHOR_CONVENTION = "line_order_date"
HELD_OUT_DURATION_SEMANTIC = "total_duration_from_line_order_date"

#: The second artifact population, written in the same transaction as the first
#: (`data-model.md` § Write order, step 4). One row per held-out **delivered**
#: line, and the same one-statement shape `LINE_POSTERIOR_INSERT` has, so FR-013
#: holds on this store for the same structural reason: `draws` and `survival` are
#: two NOT NULL columns of one row.
#:
#: `line_is_closed` is bound to a literal `true` rather than carried on the row.
#: The value the writer could supply is not evidence of anything — what makes it
#: true is `fk_held_out_prediction__line_anchor`, which resolves
#: `(po_line_id, anchor_date, line_is_closed)` against the delivered
#: `purchase_order_line` key, so a prediction naming an open line or a wrong
#: order date has no referent. A column the writer chose freely would be the
#: comment the foreign key exists to replace.
HELD_OUT_PREDICTION_INSERT = text(
    """
    INSERT INTO held_out_prediction (
        run_id, po_line_id, draw_count, horizon_days,
        anchor_date, line_is_closed, anchor_convention, duration_semantic,
        draws, survival, residual_tail_mass, draw_digest
    )
    VALUES (
        :run_id, :po_line_id, :draw_count, :horizon_days,
        :anchor_date, true, :anchor_convention, :duration_semantic,
        :draws, :survival, :residual_tail_mass, :draw_digest
    )
    """
)

#: Step 5 of `data-model.md` § Write order — every monitored parameter and every
#: run-level metric, in the same transaction as the artifacts they justify.
#:
#: `diagnostic_id` is bound rather than defaulted: `0303` declares the column
#: NOT NULL with no default, because the natural key is
#: `(run_id, metric, parameter_name)` and the surrogate exists only so a primary
#: key has something non-nullable to key on.
#:
#: Nothing here computes `passed`, `is_blocking`, the scope or the direction.
#: Each is pinned to the metric by one of `0303`'s agreement checks, so the
#: writer copies the row `diagnostics.py` assembled and the database re-derives
#: the verdict from the two numbers beside it.
DIAGNOSTIC_INSERT = text(
    """
    INSERT INTO forecast_diagnostic (
        diagnostic_id, run_id, diagnostic_scope, parameter_name, metric,
        observed_value, threshold_value, threshold_direction, is_blocking, passed
    )
    VALUES (
        :diagnostic_id, :run_id, :diagnostic_scope, :parameter_name, :metric,
        :observed_value, :threshold_value, :threshold_direction, :is_blocking, :passed
    )
    """
)

#: Transaction 2, first half. `WHERE is_active` rather than a `run_id` predicate:
#: `ix_forecast_run__single_active` makes at most one row match, so this clears
#: whichever run was live without the writer having to know which it was.
CLEAR_ACTIVE_RUN_SQL = text("UPDATE forecast_run SET is_active = false WHERE is_active")

#: Transaction 2, second half.
ACTIVATE_RUN_SQL = text("UPDATE forecast_run SET is_active = true WHERE run_id = :run_id")


# `eq=False` because two fields are arrays and a generated `__eq__` would compare
# elementwise, yielding an array where a bool is expected.
@dataclass(frozen=True, slots=True, eq=False)
class LinePosteriorRow:
    """One open line's artifact, in the shape `line_posterior` stores it.

    `draw_digest` is carried rather than computed here on purpose: the run row's
    `artifact_hash` is taken over every artifact row's digest, and the run row is
    inserted first, so the digests exist before this module is called. What this
    module does instead is **recompute** each one and refuse a mismatch — a stored
    digest that does not cover its own row's draws is a label, and nothing
    downstream would ever notice.
    """

    po_line_id: uuid.UUID
    draws: NDArray[np.float64]
    survival: NDArray[np.float64]
    residual_tail_mass: float
    draw_digest: bytes


# `eq=False` for the same reason `LinePosteriorRow` carries it.
@dataclass(frozen=True, slots=True, eq=False)
class HeldOutPredictionRow:
    """One held-out delivered line's gradeable prediction, as the store holds it.

    `anchor_date` is the line's **own** `order_date` and is carried rather than
    derived here, because the writer has no line to read it from — what proves it
    is the composite foreign key, which resolves the anchor against
    `purchase_order_line` and fails outright on a value that is not that line's.

    `draws` are **total** durations from that anchor, never remaining ones. The
    two are interchangeable to every constraint on this table, so nothing in this
    module can tell them apart; `posterior.total_duration_draws` is where the
    quantity is fixed and DV-040 is where it is measured.
    """

    po_line_id: uuid.UUID
    anchor_date: date
    draws: NDArray[np.float64]
    survival: NDArray[np.float64]
    residual_tail_mass: float
    draw_digest: bytes


def _float_list(values: NDArray[np.float64] | Sequence[float], where: str) -> list[float]:
    """A one-dimensional array as the `double precision[]` the driver adapts.

    A Python list, not a NumPy array: psycopg adapts a list of floats to a
    PostgreSQL array with subscripts from 1, which is what
    `ck_line_posterior__draws_1d` requires and what a hand-built array literal
    would have to reproduce by hand.
    """
    array = np.asarray(values, dtype=float)
    if array.ndim != 1 or array.size == 0:
        raise WriteError(
            f"{where} is not a non-empty one-dimensional array (shape {array.shape}); an "
            f"empty array passes `array_ndims` as NULL and would be admitted by every "
            f"length check the delivered table carries"
        )
    if not np.all(np.isfinite(array)):
        raise WriteError(
            f"{where} carries a non-finite value; a NaN compares false against every "
            f"threshold and would be stored as a probability nothing rejects"
        )
    return [float(value) for value in array]


def _checked_artifact(
    row: LinePosteriorRow | HeldOutPredictionRow, manifest: RunManifest, store: str
) -> dict[str, object]:
    """One artifact row's bind parameters, with every shape proved before the write.

    Checked here rather than left to the constraints, because a constraint
    violation inside transaction 1 rolls back a whole run and reports a column
    name; this names the line and the quantity. The digest is recomputed by the
    same function that produced it, which is not an independent check and is not
    claimed as one — it catches a row paired with another row's digest, which is
    the failure a shared code path can still make.

    **One function over both stores, and that is DV-027's discipline applied to
    the writer.** The seven array invariants are declared twice in the schema
    because a new table cannot inherit them; they are checked once here, so a
    strengthening cannot land on one population and miss the other.
    """
    draws = _float_list(row.draws, f"{store} line {row.po_line_id} draws")
    survival = _float_list(row.survival, f"{store} line {row.po_line_id} survival")
    if len(draws) != manifest.draw_count:
        raise WriteError(
            f"{store} line {row.po_line_id} carries {len(draws)} draws against the run's "
            f"recorded draw_count of {manifest.draw_count}; `ck_{store}__draws_length` "
            f"compares the array against this run's own value, proved by the shape foreign key"
        )
    if len(survival) != manifest.horizon_days:
        raise WriteError(
            f"{store} line {row.po_line_id} carries a {len(survival)}-day grid against the "
            f"run's horizon of {manifest.horizon_days}; the grid runs k = 1..horizon_days "
            f"with no S(0) element"
        )
    if draw_digest(row.draws) != row.draw_digest:
        raise WriteError(
            f"{store} line {row.po_line_id}'s draw digest does not cover its own draws. The "
            f"run's `artifact_hash` is taken over these digests, so storing one that belongs "
            f"to a different row would make the artifact hash a value recorded rather than "
            f"derived"
        )
    return {
        "run_id": manifest.run_id,
        "po_line_id": row.po_line_id,
        "draw_count": manifest.draw_count,
        "horizon_days": manifest.horizon_days,
        "draws": draws,
        "survival": survival,
        "residual_tail_mass": float(row.residual_tail_mass),
        "draw_digest": row.draw_digest,
    }


def _checked_prediction(row: HeldOutPredictionRow, manifest: RunManifest) -> dict[str, object]:
    """A held-out row's bind parameters: the shared shape, plus the two labels.

    The anchor is passed through unexamined on purpose. Nothing this module could
    compare it against would be independent — the writer never read the line — so
    a check here would be the writer agreeing with itself. The foreign key is the
    check, and it resolves against the delivered table rather than against a value
    in this process.
    """
    if isinstance(row.anchor_date, datetime) or not isinstance(row.anchor_date, date):
        raise WriteError(
            f"line {row.po_line_id}'s anchor is a {type(row.anchor_date).__name__}; "
            f"`held_out_prediction.anchor_date` is a `date` and the composite foreign key "
            f"compares values, so an instant would fail to reference at all rather than "
            f"reference something looser"
        )
    return {
        **_checked_artifact(row, manifest, "held_out_prediction"),
        "anchor_date": row.anchor_date,
        "anchor_convention": HELD_OUT_ANCHOR_CONVENTION,
        "duration_semantic": HELD_OUT_DURATION_SEMANTIC,
    }


def _checked_diagnostics(diagnostics: Sequence[DiagnosticRow]) -> None:
    """Every diagnostic row proved storable before the first one is issued.

    Three claims, each of which the database would otherwise report as a
    constraint name inside a transaction that has already written a run. A
    blocking breach is mechanism 3 of § The Refusal Guarantee firing — "a writer
    that skips the gate" — and is a **defect in the writer**, not a refusal of
    the run, because the gate has already passed by the time this module runs. A
    repeated natural key is what `uq_forecast_diagnostic__run_metric_parameter`
    refuses. A partially covered parameter is DV-011, which no `CHECK` can see.
    """
    breaches = blocking_breaches(diagnostics)
    if breaches:
        raise WriteError(
            f"{len(breaches)} blocking diagnostic row(s) did not pass and reached the "
            f"writer — first {breaches[0].described()}. "
            f"`ck_forecast_diagnostic__blocking_rows_passed` refuses the row outright, so "
            f"this is the gate having been skipped rather than a run legitimately refusing"
        )
    keys = [(row.metric, row.parameter_name) for row in diagnostics]
    if len(set(keys)) != len(keys):
        raise WriteError(
            "two diagnostic rows share a metric and parameter under one run; "
            "`uq_forecast_diagnostic__run_metric_parameter` is `NULLS NOT DISTINCT`, so "
            "the second insert is refused after the first has already been issued"
        )
    partial = sorted(
        name
        for name, metrics in monitored_parameter_coverage(diagnostics).items()
        if metrics != frozenset(PARAMETER_METRICS)
    )
    if partial:
        raise WriteError(
            f"{len(partial)} monitored parameter(s) carry fewer than the three "
            f"parameter-scope metrics — first {partial[0]}. DV-011 requires no parameter "
            f"be partially covered, and a `CHECK` admits no sibling row (G-7), so a run "
            f"recording an R-hat and omitting its ESS would store cleanly"
        )
    run_scoped = sorted(row.metric for row in diagnostics if row.parameter_name is None)
    if diagnostics and run_scoped != sorted(RUN_METRICS):
        raise WriteError(
            f"the run-scope rows are {run_scoped} rather than {sorted(RUN_METRICS)}; "
            f"DV-011 requires exactly three, and a missing E-BFMI row is a blocking "
            f"metric nobody recorded a verdict for"
        )


# ---------------------------------------------------------------------------
# Transaction 1 — the artifact set
# ---------------------------------------------------------------------------


def insert_artifact_set(
    connection: Connection | Session,
    manifest: RunManifest,
    split_assignments: Sequence[SplitAssignment],
    line_posteriors: Sequence[LinePosteriorRow],
    held_out_predictions: Sequence[HeldOutPredictionRow] = (),
    diagnostics: Sequence[DiagnosticRow] = (),
) -> uuid.UUID:
    """Every statement of transaction 1, issued in the order the keys force.

    The run row first, because every other row is its child; then the split
    assignments, which every line has; then **both** artifact populations. The
    ordering is not a policy choice and carries no guarantee of its own — inside
    one transaction it has no external visibility. What the *transaction* carries
    is that a failure at any point rolls all of it back together, which is what
    makes SC-015's enumeration across stores hold with no per-store mechanism —
    and splitting the artifacts into two stores is precisely why that enumeration
    has to name every store rather than one (ADR-0018 § Consequences/Negative).

    Issues no `COMMIT`. The caller owns the transaction boundary, so this function
    can be extended by the task that adds `forecast_diagnostic` to the same
    transaction without it needing to know how the transaction was opened.

    `held_out_predictions` defaults to empty rather than being required, because
    the population it describes is a *measurement* of the split rather than a
    structural necessity: a run whose held-out side happened to hold no delivered
    line writes no row here and is well formed. Whether the population the run
    actually has was written is DV-002's question and DV-028's, both asserted
    over the stored rows against the run row's own counts — not something this
    function can answer, since it is handed the rows rather than the lines.

    `diagnostics` defaults to empty for a different and narrower reason: a run
    the job emits always carries the complete set, and the default exists so a
    test re-emitting a variant of a stored run drives the artifact statements
    without re-deriving a posterior summary it is making no claim about. The
    completeness of what a *run* stores is DV-011's question, asserted over the
    stored rows; what this function refuses is a set that is internally
    incoherent — a blocking breach, a repeated natural key, a parameter covered
    by fewer than three metrics.
    """
    if not split_assignments:
        raise WriteError(
            "no split assignments were passed; every line is assigned exactly once per run "
            "(DV-006), and a run row with no assignment makes its own split hash "
            "unrecomputable"
        )
    if not line_posteriors:
        raise WriteError(
            "no open-line artifacts were passed; `ck_forecast_run__open_line_count_positive` "
            "makes an empty forecast set unrepresentable, which is FR-021 as a database fact"
        )
    if len({row.po_line_id for row in line_posteriors}) != len(line_posteriors):
        raise WriteError(
            "two open-line artifacts name one line; `pk_line_posterior` is "
            "`(run_id, po_line_id)`, so the second insert would be refused after the first "
            "had already been issued"
        )
    if len(line_posteriors) != manifest.open_line_count:
        raise WriteError(
            f"{len(line_posteriors)} open-line artifact(s) against a recorded "
            f"`open_line_count` of {manifest.open_line_count}; DV-001 compares the two, and "
            f"the count column is what a reader trusts without running the query"
        )
    held_out_lines = {row.po_line_id for row in held_out_predictions}
    if len(held_out_lines) != len(held_out_predictions):
        raise WriteError(
            "two held-out predictions name one line; `pk_held_out_prediction` is "
            "`(run_id, po_line_id)`, so the second insert would be refused after the first "
            "had already been issued"
        )
    both = held_out_lines & {row.po_line_id for row in line_posteriors}
    if both:
        raise WriteError(
            f"{len(both)} line(s) carry an artifact row in **both** stores under one run — "
            f"first {sorted(map(str, both))[0]}. The two populations are structurally "
            f"disjoint on the held-out side only (`ck_held_out_prediction__line_delivered` "
            f"plus the anchor foreign key); the other direction is G-5, and nothing in the "
            f"schema refuses an order-date-anchored row written into `line_posterior`. "
            f"DV-030 asserts it over the stored rows; this refuses it before the write"
        )
    _checked_diagnostics(diagnostics)

    connection.execute(RUN_INSERT, manifest.row_parameters())
    connection.execute(
        SPLIT_ASSIGNMENT_INSERT,
        [
            {
                "run_id": manifest.run_id,
                "po_line_id": assignment.po_line_id,
                "split_side": assignment.split_side,
                "is_censored": assignment.is_censored,
                "canonical_ordinal": assignment.canonical_ordinal,
            }
            for assignment in sorted(split_assignments, key=lambda row: row.canonical_ordinal)
        ],
    )
    connection.execute(
        LINE_POSTERIOR_INSERT,
        [_checked_artifact(row, manifest, "line_posterior") for row in line_posteriors],
    )
    # Step 4 of `data-model.md` § Write order, and `executemany` is skipped
    # entirely on an empty sequence rather than issuing a statement with no
    # parameter sets, which psycopg refuses.
    if held_out_predictions:
        connection.execute(
            HELD_OUT_PREDICTION_INSERT,
            [_checked_prediction(row, manifest) for row in held_out_predictions],
        )
    # Step 5, and the last statement of transaction 1. Last because every
    # diagnostic row is the run's child and because ordering inside one
    # transaction carries no external guarantee anyway — what it does carry is
    # that a rejected diagnostic rolls the artifacts back with it.
    if diagnostics:
        connection.execute(
            DIAGNOSTIC_INSERT,
            [row.row_parameters(manifest.run_id) for row in diagnostics],
        )
    return manifest.run_id


# ---------------------------------------------------------------------------
# Transaction 2 — the pointer (T034)
# ---------------------------------------------------------------------------


def set_active_run(connection: Connection | Session, run_id: uuid.UUID) -> None:
    """Clear whichever run is active, then set this one — explicitly (FR-015).

    Two statements and never one, because `ix_forecast_run__single_active` makes a
    second active run unrepresentable: setting the new pointer before clearing the
    old one would collide with it. Neither statement reads `created_at`, and no
    query anywhere in this package orders by it — "which forecast is current" is a
    stored boolean, and a recency fallback would make a superseded run
    indistinguishable from the live one (E003's TR-027).

    Issues no `COMMIT`, for the same reason `insert_artifact_set` does not: the
    caller owns the boundary, and `write_artifact_set` is what makes this the
    second of two.
    """
    connection.execute(CLEAR_ACTIVE_RUN_SQL)
    activated = connection.execute(ACTIVATE_RUN_SQL, {"run_id": run_id})
    if activated.rowcount != 1:
        raise WriteError(
            f"activating run {run_id} matched {activated.rowcount} rows rather than one. The "
            f"pointer is set explicitly on a run that exists; matching none means transaction "
            f"1 did not commit, and the pointer must not be left describing nothing"
        )


# ---------------------------------------------------------------------------
# The two transactions together
# ---------------------------------------------------------------------------


@contextmanager
def _unit_of_work(target: Engine | Connection | Session) -> Iterator[Connection | Session]:
    """One committed unit of work, whether the caller brought an engine or not.

    An `Engine` is the production path and gets a real `begin()` per unit, which
    is what makes the two transactions two. A `Connection` or `Session` is the
    test path: this tier isolates by an outer transaction that is rolled back, so
    the `commit()` below releases and re-opens a savepoint instead. Both roll back
    on an exception, which is the property the refusal guarantee's mechanism 2
    rests on.
    """
    if isinstance(target, Engine):
        with target.begin() as connection:
            yield connection
        return
    try:
        yield target
    except BaseException:
        target.rollback()
        raise
    target.commit()


def write_artifact_set(
    target: Engine | Connection | Session,
    manifest: RunManifest,
    split_assignments: Sequence[SplitAssignment],
    line_posteriors: Sequence[LinePosteriorRow],
    held_out_predictions: Sequence[HeldOutPredictionRow] = (),
    diagnostics: Sequence[DiagnosticRow] = (),
) -> uuid.UUID:
    """Write one run's artifact set, then publish it. Two transactions (AD-010).

    Returns the `run_id` written, which is the value FR-039 puts on standard
    output as the job's single line.

    The order is the whole of the decision. Transaction 1 commits a complete but
    **unpublished** run — every artifact durable, `is_active` still false by the
    delivered default — and only then does transaction 2 move the pointer. A
    failure during publication therefore leaves a complete run nobody is serving
    rather than a half-written one somebody is, and a refusal before either
    transaction opens cannot move the pointer at all, because the only statement
    that writes it has not run.
    """
    with _unit_of_work(target) as connection:
        run_id = insert_artifact_set(
            connection,
            manifest,
            split_assignments,
            line_posteriors,
            held_out_predictions,
            diagnostics,
        )
    with _unit_of_work(target) as connection:
        set_active_run(connection, run_id)
    return run_id
