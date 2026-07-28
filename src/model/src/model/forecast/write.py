"""Two transactions, per AD-010: the artifact set, then the pointer.

Transaction 1 inserts the run row, the split assignments and the artifact rows;
transaction 2 sets `is_active`. Splitting the pointer off is what lets a run be
written and reviewed before it becomes the one downstream readers see, which is
what makes FR-015's "explicit, never implied by recency" operable. Both arrays
of an artifact row are columns of **one** row written by **one** statement, so no
reader can observe them in disagreement (FR-013). Artifact rows are inserted once
and never updated — `UPDATE` was withheld from the grant deliberately (FR-034).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass

import numpy as np
from numpy.typing import NDArray
from sqlalchemy import Connection, Engine, text
from sqlalchemy.orm import Session

from model.forecast.manifest import RunManifest, draw_digest
from model.forecast.split import SplitAssignment

__all__ = [
    "ACTIVATE_RUN_SQL",
    "CLEAR_ACTIVE_RUN_SQL",
    "LINE_POSTERIOR_INSERT",
    "RUN_INSERT",
    "SPLIT_ASSIGNMENT_INSERT",
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


def _checked_posterior(row: LinePosteriorRow, manifest: RunManifest) -> dict[str, object]:
    """One artifact row's bind parameters, with every shape proved before the write.

    Checked here rather than left to the constraints, because a constraint
    violation inside transaction 1 rolls back a whole run and reports a column
    name; this names the line and the quantity. The digest is recomputed by the
    same function that produced it, which is not an independent check and is not
    claimed as one — it catches a row paired with another row's digest, which is
    the failure a shared code path can still make.
    """
    draws = _float_list(row.draws, f"line {row.po_line_id} draws")
    survival = _float_list(row.survival, f"line {row.po_line_id} survival")
    if len(draws) != manifest.draw_count:
        raise WriteError(
            f"line {row.po_line_id} carries {len(draws)} draws against the run's recorded "
            f"draw_count of {manifest.draw_count}; `ck_line_posterior__draws_length` compares "
            f"the array against this run's own value, proved by the shape foreign key"
        )
    if len(survival) != manifest.horizon_days:
        raise WriteError(
            f"line {row.po_line_id} carries a {len(survival)}-day grid against the run's "
            f"horizon of {manifest.horizon_days}; the grid runs k = 1..horizon_days with no "
            f"S(0) element"
        )
    if draw_digest(row.draws) != row.draw_digest:
        raise WriteError(
            f"line {row.po_line_id}'s draw digest does not cover its own draws. The run's "
            f"`artifact_hash` is taken over these digests, so storing one that belongs to a "
            f"different row would make the artifact hash a value recorded rather than derived"
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


# ---------------------------------------------------------------------------
# Transaction 1 — the artifact set
# ---------------------------------------------------------------------------


def insert_artifact_set(
    connection: Connection | Session,
    manifest: RunManifest,
    split_assignments: Sequence[SplitAssignment],
    line_posteriors: Sequence[LinePosteriorRow],
) -> uuid.UUID:
    """Every statement of transaction 1, issued in the order the keys force.

    The run row first, because every other row is its child; then the split
    assignments, which every line has; then the open population's artifacts. The
    ordering is not a policy choice and carries no guarantee of its own — inside
    one transaction it has no external visibility. What the *transaction* carries
    is that a failure at any point rolls all of it back together, which is what
    makes SC-015's enumeration across stores hold with no per-store mechanism.

    Issues no `COMMIT`. The caller owns the transaction boundary, so this function
    can be extended by the tasks that add `held_out_prediction` and
    `forecast_diagnostic` to the same transaction without either of them needing
    to know how it was opened.
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
        [_checked_posterior(row, manifest) for row in line_posteriors],
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
        run_id = insert_artifact_set(connection, manifest, split_assignments, line_posteriors)
    with _unit_of_work(target) as connection:
        set_active_run(connection, run_id)
    return run_id
