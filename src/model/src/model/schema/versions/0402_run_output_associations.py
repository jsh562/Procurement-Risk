"""run output associations

Revision ID: 0402
Revises: 0401
Create Date: 2026-07-27

The three run-output associations -- one per target table -- their three
referencing-side indexes, and the redundant unique key the two value-level
associations of `0403` reference. One revision for all three tables: they share
one foreign-key target (`pk_ingestion_run_document`) and no intermediate head is
useful.

**Associations rather than a `run_id` column, and that is FR-039's whole shape.**
`chunk`, `extracted_value`, and `extraction_failure` are E003's tables and E006
adds zero columns to them ({SAD:ADR-0017}, VR-015). Run attribution is therefore
carried by three tables whose **primary key is the target row's own identifier**,
which is what makes SC-021's "exactly one ingestion run per row" a uniqueness
fact rather than a convention: a second association row for one chunk is a
primary-key collision. The other half -- that an association row exists at all --
is cross-table absence, is not expressible without a deferred constraint trigger
this schema does not use, and is disclosed as **G-1** and covered by VR-001's
corpus-wide anti-join.

**Why `document_id` is on the association at all.** It is derivable, from
`chunk.document_id` or from a value's source chunk two joins away. It is carried
anyway because the generation is keyed on `(run_id, document_id)`: without the
column an association could not reference the generation row, and a promotion
replacing one document's generation would have no way to find the rows it must
remove first. The cost is disclosed as **G-2** -- `chunk` has no unique key on
`(chunk_id, document_id)` for a composite foreign key to reference, and E006 may
not add one.

**Each association carries exactly three columns and one index.** PostgreSQL
creates no index on the *referencing* side of a foreign key, so without
`ix_*__generation` every promotion's removal step sequentially scans the
association to enforce `RESTRICT` -- and under {SAD:ADR-0020} that scan is on the
promotion path, not on a background job.
"""

from collections.abc import Sequence

from alembic import op

# Revision identifiers, used by Alembic.
#
# FR-040: `revision` doubles as the four-digit filename prefix. Ordering is
# `down_revision` and only `down_revision`.
revision: str = "0402"
down_revision: str | Sequence[str] | None = "0401"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the three run-output associations and their generation indexes.

    Re-application is a no-op by virtue of Alembic's `alembic_version`
    bookkeeping (VR-014). Do not add a "have I already run?" guard here.

    Every generation foreign key is `MATCH FULL`. Both columns are `NOT NULL`
    here, so it is equivalent to the default today; it is declared for intent,
    because were either column ever relaxed to nullable, `MATCH SIMPLE` would
    skip the check entirely on a partially-null pair -- which is precisely the
    unattributable row FR-039 exists to forbid.

    `RESTRICT` on every edge. Nothing E006 adds cascades on delete, because a
    cascade is exactly the silent teardown FR-041 forbids: correction is an
    ordered operator procedure, and an edge that deletes itself when its parent
    goes hides the ordering the procedure depends on.
    """
    # --- ingestion_run_chunk (T011) ------------------------------------------
    op.execute(
        """
        CREATE TABLE ingestion_run_chunk (
            chunk_id uuid NOT NULL,
            run_id uuid NOT NULL,
            document_id text NOT NULL,

            -- Invariant 1: a chunk resolves to at most one ingestion run. The
            -- target's identifier *is* the key, so a second attribution is
            -- unrepresentable rather than merely wrong.
            CONSTRAINT pk_ingestion_run_chunk PRIMARY KEY (chunk_id),

            CONSTRAINT fk_ingestion_run_chunk__chunk
                FOREIGN KEY (chunk_id)
                REFERENCES chunk (chunk_id)
                ON DELETE RESTRICT
                ON UPDATE CASCADE,

            CONSTRAINT fk_ingestion_run_chunk__generation
                FOREIGN KEY (run_id, document_id)
                REFERENCES ingestion_run_document (run_id, document_id)
                MATCH FULL
                ON DELETE RESTRICT
                ON UPDATE CASCADE
        )
        """
    )

    # Also the "all chunks this generation wrote" read. Without it the RESTRICT
    # enforcement on a generation delete scans ~15,000 rows.
    op.execute(
        """
        CREATE INDEX ix_ingestion_run_chunk__generation
        ON ingestion_run_chunk (run_id, document_id)
        """
    )

    # --- ingestion_run_extracted_value (T011) --------------------------------
    op.execute(
        """
        CREATE TABLE ingestion_run_extracted_value (
            extracted_value_id uuid NOT NULL,
            run_id uuid NOT NULL,
            document_id text NOT NULL,

            CONSTRAINT pk_ingestion_run_extracted_value PRIMARY KEY (extracted_value_id),

            -- Redundant against the primary key by design, exactly as
            -- `uq_chunk__chunk_page` is. It exists to be the foreign-key target
            -- of the two value-level associations `0403` adds -- both
            -- `extracted_value_line_item` and `extracted_value_parse_signal`
            -- need all three columns in one referenced key, and a primary key on
            -- the value alone cannot carry the generation.
            CONSTRAINT uq_ingestion_run_extracted_value__value_generation
                UNIQUE (extracted_value_id, run_id, document_id),

            CONSTRAINT fk_ingestion_run_extracted_value__value
                FOREIGN KEY (extracted_value_id)
                REFERENCES extracted_value (extracted_value_id)
                ON DELETE RESTRICT
                ON UPDATE CASCADE,

            CONSTRAINT fk_ingestion_run_extracted_value__generation
                FOREIGN KEY (run_id, document_id)
                REFERENCES ingestion_run_document (run_id, document_id)
                MATCH FULL
                ON DELETE RESTRICT
                ON UPDATE CASCADE
        )
        """
    )

    # The referencing-side index, and the join E009 walks from a value to the
    # models that produced it.
    op.execute(
        """
        CREATE INDEX ix_ingestion_run_extracted_value__generation
        ON ingestion_run_extracted_value (run_id, document_id)
        """
    )

    # --- ingestion_run_extraction_failure (T011) -----------------------------
    op.execute(
        """
        CREATE TABLE ingestion_run_extraction_failure (
            extraction_failure_id uuid NOT NULL,
            run_id uuid NOT NULL,
            document_id text NOT NULL,

            CONSTRAINT pk_ingestion_run_extraction_failure PRIMARY KEY (extraction_failure_id),

            CONSTRAINT fk_ingestion_run_extraction_failure__failure
                FOREIGN KEY (extraction_failure_id)
                REFERENCES extraction_failure (extraction_failure_id)
                ON DELETE RESTRICT
                ON UPDATE CASCADE,

            CONSTRAINT fk_ingestion_run_extraction_failure__generation
                FOREIGN KEY (run_id, document_id)
                REFERENCES ingestion_run_document (run_id, document_id)
                MATCH FULL
                ON DELETE RESTRICT
                ON UPDATE CASCADE
        )
        """
    )

    op.execute(
        """
        CREATE INDEX ix_ingestion_run_extraction_failure__generation
        ON ingestion_run_extraction_failure (run_id, document_id)
        """
    )


def downgrade() -> None:
    """Refuse: migrations in this project are forward-only.

    VR-014. Kept as a raising stub rather than deleted, because Alembic calls
    this attribute when a downgrade is requested and a missing one would fail
    with an unexplained AttributeError instead of stating the policy.
    """
    raise NotImplementedError(
        "This migration is forward-only (VR-014) and defines no downgrade. "
        "To undo a schema change, author a new forward revision; to recover a "
        "database, restore it from a backup."
    )
