"""T047 — FR-013, FR-015, SC-020, SC-023: the order the two transactions impose.

Three claims, and the middle one is the reason the other two are in the same
file. **FR-013**: `draws` and `survival` are two NOT NULL columns of one row
written by one statement, so "the draws were written and the survival curve was
not" is not a state the database can be in — there is no second row to be missing
and nothing for a deferred constraint to police. **FR-015**: the active pointer
is set explicitly, in a transaction of its own, on a run whose every artifact was
already durable. **SC-020**: every manifest field is present on the stored row.

The pointer half is driven step by step rather than through `write_artifact_set`,
because what is under assertion is the state *between* the two units of work —
a complete run nobody is serving — and a single call gives no moment to observe
it. This tier isolates by an outer transaction that is rolled back, so each
`commit()` below releases and re-opens a savepoint rather than committing; the
ordering it establishes is the same one, and nothing here reaches the pointer a
committed run is holding.
"""

from __future__ import annotations

import dataclasses
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from forecast.conftest import EmittedRun, StoredRun
from model.forecast.write import (
    ACTIVATE_RUN_SQL,
    CLEAR_ACTIVE_RUN_SQL,
    LINE_POSTERIOR_INSERT,
    insert_artifact_set,
    set_active_run,
)

#: Module-level SQL, never assembled from values (Ruff S608).
RUN_ROW_SQL = text("SELECT * FROM forecast_run WHERE run_id = :run_id")
RUN_POINTER_SQL = text("SELECT is_active FROM forecast_run WHERE run_id = :run_id")
ACTIVE_VIEW_SQL = text("SELECT run_id FROM v_active_forecast_run")
CHILD_COUNTS_SQL = text(
    """
    SELECT (SELECT count(*) FROM line_posterior WHERE run_id = :run_id) AS posteriors,
           (SELECT count(*) FROM forecast_split_assignment WHERE run_id = :run_id) AS assignments
    """
)
ARRAY_NULLABILITY_SQL = text(
    """
    SELECT column_name, is_nullable FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name = 'line_posterior'
      AND column_name IN ('draws', 'survival')
    """
)
DISAGREEING_ARRAYS_SQL = text(
    """
    SELECT count(*) AS disagreeing FROM line_posterior
    WHERE run_id = :run_id
      AND (draws IS NULL OR survival IS NULL
           OR array_length(draws, 1) <> draw_count
           OR array_length(survival, 1) <> horizon_days)
    """
)

#: The column the pointer must never be derived from (E003's TR-027).
RECENCY_COLUMN = "created_at"


def test_a_run_is_complete_and_unpublished_after_the_first_unit_of_work(
    db_session: Session, stored_run: StoredRun
) -> None:
    """AD-010's first half: every artifact durable, `is_active` still false.

    `is_active` is omitted from `RUN_INSERT` so the delivered default applies,
    which is what makes "inserted inactive" a property of the schema rather than
    a value the writer remembered to pass. A failure during publication therefore
    leaves a complete run nobody is serving rather than a half-written one
    somebody is.
    """
    manifest = dataclasses.replace(stored_run.manifest, run_id=uuid.uuid4())
    insert_artifact_set(db_session, manifest, stored_run.assignments, stored_run.line_posteriors)
    db_session.commit()

    counts = db_session.execute(CHILD_COUNTS_SQL, {"run_id": manifest.run_id}).mappings().one()

    assert counts["posteriors"] == len(stored_run.line_posteriors)
    assert counts["assignments"] == len(stored_run.assignments)
    assert db_session.execute(RUN_POINTER_SQL, {"run_id": manifest.run_id}).scalar_one() is False
    assert manifest.run_id not in set(db_session.execute(ACTIVE_VIEW_SQL).scalars())


def test_the_pointer_is_set_explicitly_in_a_second_unit_of_work(
    db_session: Session, stored_run: StoredRun
) -> None:
    """FR-015: publication is a statement, never a consequence of being newest.

    The run is written first and published second, and between the two it is
    invisible to `v_active_forecast_run`. Afterwards the view returns exactly it
    and nothing else, because `set_active_run` clears whichever run was live
    before setting this one — `ix_forecast_run__single_active` makes the pair of
    statements necessary rather than tidy.
    """
    manifest = dataclasses.replace(stored_run.manifest, run_id=uuid.uuid4())
    insert_artifact_set(db_session, manifest, stored_run.assignments, stored_run.line_posteriors)
    db_session.commit()
    before = set(db_session.execute(ACTIVE_VIEW_SQL).scalars())

    set_active_run(db_session, manifest.run_id)
    db_session.commit()

    assert manifest.run_id not in before
    assert db_session.execute(RUN_POINTER_SQL, {"run_id": manifest.run_id}).scalar_one() is True
    assert set(db_session.execute(ACTIVE_VIEW_SQL).scalars()) == {manifest.run_id}


def test_neither_pointer_statement_consults_the_creation_timestamp() -> None:
    """ "Explicit, never implied by recency" — asserted over the two statements.

    A recency fallback would make a superseded run indistinguishable from the
    live one, which is exactly the failure E003's TR-027 records. Neither
    statement reads `created_at`, and neither orders by anything at all: the
    partial unique index makes at most one row match `WHERE is_active`.
    """
    for statement in (CLEAR_ACTIVE_RUN_SQL, ACTIVATE_RUN_SQL):
        rendered = str(statement).lower()

        assert RECENCY_COLUMN not in rendered
        assert "order by" not in rendered


def test_the_two_arrays_are_one_row_written_by_one_statement(db_session: Session) -> None:
    """FR-013 structurally: neither array can be observed without the other.

    Both are NOT NULL columns of `line_posterior` and both are named by the single
    `INSERT` the writer issues, so there is no interleaving in which one is
    present and the other is not — which is why E003's invariant 21 needs no
    trigger and no deferred constraint.
    """
    nullability = {
        row["column_name"]: row["is_nullable"]
        for row in db_session.execute(ARRAY_NULLABILITY_SQL).mappings()
    }
    rendered = str(LINE_POSTERIOR_INSERT)

    assert nullability == {"draws": "NO", "survival": "NO"}
    assert rendered.count("INSERT INTO") == 1
    assert ";" not in rendered
    assert "draws" in rendered
    assert "survival" in rendered


def test_no_stored_row_holds_two_arrays_of_disagreeing_length(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """The same claim over the rows the emitted run actually committed.

    Each array is compared against the row's own `draw_count` and `horizon_days`,
    which `fk_line_posterior__run_shape` proves are this run's values — so a row
    whose two halves came from different draw sets is caught here rather than
    inferred from the fact that the writer intended otherwise.
    """
    assert (
        db_session.execute(DISAGREEING_ARRAYS_SQL, {"run_id": emitted_run.run_id}).scalar_one() == 0
    )


def test_every_manifest_field_is_present_on_the_stored_run_row(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """SC-020: the run row carries a value in every column it declares.

    Quantified over the columns the database reports rather than over a list
    written here, so a column added by a later revision is covered without this
    test being edited — and an absent provenance field is a run that cannot be
    traced, which is Principle I's failure rather than a cosmetic gap.
    """
    row = db_session.execute(RUN_ROW_SQL, {"run_id": emitted_run.run_id}).mappings().one()
    absent = sorted(name for name, value in row.items() if value is None)

    assert not absent, f"the emitted run records nothing in {absent}"
