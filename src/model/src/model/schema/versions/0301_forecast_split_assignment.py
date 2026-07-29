"""forecast split assignment

Revision ID: 0301
Revises: 0300
Create Date: 2026-07-27

`forecast_split_assignment` -- one row per line per run, recording which side of
the train/held-out split the line landed on, whether it was censored, and the
position it occupied in the canonical serialized order (FR-005, FR-006, FR-007).

**Why the ordinal is stored rather than recomputed.** The split assignment hash
is taken over the array of `{"project_id","po_number","line_number","split_side",
"is_censored"}` ordered by `canonical_ordinal`, so storing the position makes the
digest recomputable from this table alone (DV-017) -- without re-reading
`purchase_order_line` and re-deriving the natural-key order. The order itself is
ascending `(project_id, po_number, line_number)`, which
`uq_purchase_order_line__natural` makes unique, so the order is **total** and no
tie-break exists to specify.

**Half of "every line is assigned to exactly one side" is structural and half is
not.** `pk_forecast_split_assignment (run_id, po_line_id)` gives *at most once
per run*. *At least once* is a count against `purchase_order_line`, which a
`CHECK` cannot see, and `canonical_ordinal` contiguity is likewise cross-row.
Both are **DV-006** and are disclosed as **G-6**.

**`is_censored` is stored, not derived at read time.** FR-004 forbids
re-deriving the censoring indicator when the split is read, and a stored
`boolean` is that prohibition honoured. What is *not* duplicated here is the
as-of date it was derived from: that is `forecast_run.as_of_date`, reached
through `run_id`, which is the run's primary key -- so the date is functionally
determined by a column already on the row. Duplicating it would create a second
place for one date to be wrong.

**Grants are explicit because `0009` declined `ALTER DEFAULT PRIVILEGES`.**
`0009` ran `GRANT ... ON ALL TABLES IN SCHEMA public` against the tables that
existed at that point, so a table created later inherits nothing -- E003's own
`0010` had to grant explicitly for the same reason. `UPDATE` is withheld: an
artifact row is written once inside transaction 1 and never edited (FR-034).
`DELETE` is retained so discarding a run is a plain operation rather than a
reliance on the privilege model of a cascading referential action. E003's
**G-11** applies unchanged -- the deployed process connects as a superuser, so
this is a latent fact about `procurement_app` rather than an active restriction
on the connecting role, which is why **DV-032** asserts the writer's side too.

No column here carries a `DEFAULT` (TR-063), no constraint is deferrable
(TR-051), and no trigger is declared. Forward-only: `downgrade()` raises.
"""

from collections.abc import Sequence

from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "0301"
down_revision: str | Sequence[str] | None = "0300"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: The table, with every constraint named and declared in the same statement.
#:
#: Created whole rather than as `CREATE TABLE` followed by `ALTER TABLE ... ADD
#: CONSTRAINT`: no row can predate any check, which is what makes "no E007
#: revision adds a constraint existing rows were never validated against" true
#: of this revision by construction rather than by inspection.
CREATE_FORECAST_SPLIT_ASSIGNMENT = """
CREATE TABLE forecast_split_assignment (
    run_id uuid NOT NULL,
    po_line_id uuid NOT NULL,

    -- Which side of the split. `text` + `CHECK` rather than a native `ENUM`,
    -- per E003's convention for every closed value set in this schema.
    split_side text NOT NULL,

    -- FR-004's stored censoring indicator, and the stratum the split was
    -- balanced on. A `boolean` and not a derivation: re-deriving it at read
    -- time is what FR-004 forbids.
    is_censored boolean NOT NULL,

    -- The line's position in the canonical serialized order, so the split
    -- assignment hash is recomputable from this table alone (DV-017).
    canonical_ordinal integer NOT NULL,

    -- **"At most once per run" as a database fact.** The other half of the
    -- completeness claim -- that every line appears at all -- is a count
    -- against `purchase_order_line` (DV-006, G-6).
    CONSTRAINT pk_forecast_split_assignment PRIMARY KEY (run_id, po_line_id),

    -- Two lines cannot claim one position in the serialized order, so the
    -- hash's input is a permutation-free sequence. Without this, two lines
    -- sharing an ordinal would make the serialized array's order depend on
    -- whatever secondary order the reader's query happened to produce.
    CONSTRAINT uq_forecast_split_assignment__run_ordinal
        UNIQUE (run_id, canonical_ordinal),

    -- An assignment belongs to its run and dies with it: a single
    -- `DELETE FROM forecast_run WHERE run_id = ...` discards the whole artifact
    -- set. ON UPDATE CASCADE follows E003's convention for a parent key that
    -- could legitimately be corrected.
    CONSTRAINT fk_forecast_split_assignment__run
        FOREIGN KEY (run_id)
        REFERENCES forecast_run (run_id)
        ON DELETE CASCADE
        ON UPDATE CASCADE,

    -- ON DELETE RESTRICT, unlike the run FK: a line with a recorded split
    -- cannot be deleted out from under it. A cascade here would let a roster
    -- correction quietly delete part of a published run's evidence.
    CONSTRAINT fk_forecast_split_assignment__line
        FOREIGN KEY (po_line_id)
        REFERENCES purchase_order_line (po_line_id)
        ON DELETE RESTRICT
        ON UPDATE CASCADE,

    CONSTRAINT ck_forecast_split_assignment__side
        CHECK (split_side IN ('train', 'held_out')),

    -- One-based, matching the percentile convention and the array lower bounds
    -- elsewhere in this schema. Zero and negative are not positions.
    CONSTRAINT ck_forecast_split_assignment__ordinal_positive
        CHECK (canonical_ordinal >= 1)
)
"""

#: Reverse lookup: which runs held this line out. The primary key leads with
#: `run_id`, so it cannot serve a `po_line_id` lookup, and PostgreSQL indexes
#: neither side of a foreign key automatically -- so this also keeps
#: `fk_forecast_split_assignment__line`'s RESTRICT check from scanning the table
#: on every line delete.
CREATE_PO_LINE_INDEX = """
CREATE INDEX ix_forecast_split_assignment__po_line
ON forecast_split_assignment (po_line_id)
"""

#: FR-034. `UPDATE` is absent by intent, not by omission.
GRANT_TO_APPLICATION_ROLE = (
    "GRANT SELECT, INSERT, DELETE ON forecast_split_assignment TO procurement_app"
)


def upgrade() -> None:
    """Create the table, its reverse index, and the application role's grant.

    TR-003: re-application is a no-op by virtue of Alembic's `alembic_version`
    bookkeeping. Do not add a "have I already run?" guard here.
    """
    op.execute(CREATE_FORECAST_SPLIT_ASSIGNMENT)
    op.execute(CREATE_PO_LINE_INDEX)
    op.execute(GRANT_TO_APPLICATION_ROLE)


def downgrade() -> None:
    """Refuse: migrations in this project are forward-only."""
    raise NotImplementedError(
        "This migration is forward-only and defines no downgrade. "
        "To undo a schema change, author a new forward revision; to recover a "
        "database, restore it from a backup. Dropping `forecast_split_assignment` "
        "discards the record of which lines trained each published run, which no "
        "other stored column can reconstruct."
    )
