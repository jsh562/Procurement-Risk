"""schema constants

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-26 01:35:39.811627

TR-043: the constants this schema is built around are recorded exactly once, in
one row of one table, rather than restated in each consumer. TR-047: `/src/api`
reads that row over the database connection -- it never imports this package,
which is what keeps the serving boundary free of the modeling stack.

TR-079: the row is seeded by this migration, in the same revision that creates
the table, so the table is never observable empty. It follows that the row is
recoverable only by re-applying the chain; there is no loader script and no
setup step to forget.

Structure carries the singleton guarantee. `singleton boolean PRIMARY KEY`
constrained `CHECK (singleton)` admits exactly one value, so a second row is
impossible rather than merely discouraged -- the second insert collides on the
primary key (invariant 24 in the data model's invariant map).

"""

from collections.abc import Sequence

from alembic import op

# Revision identifiers, used by Alembic.
#
# TR-004: `revision` doubles as the four-digit filename prefix -- 0001-0099 is
# this epic's reserved block, 0100-0199 is E004's. Ordering is `down_revision`
# and only `down_revision`; the numbers are never compared to decide what runs
# next, so a gap or an out-of-order id is a naming defect, not a broken chain.
revision: str = "0002"
down_revision: str | Sequence[str] | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create `schema_constants` and seed its one row.

    TR-003: re-application is a no-op by virtue of Alembic's `alembic_version`
    bookkeeping. Do not add a "have I already run?" guard here.

    Every constraint is named. An unnamed constraint gets a server-generated
    name that a later forward migration cannot reliably `ALTER TABLE ... DROP
    CONSTRAINT`, and that a test asserting *which* rule rejected a row cannot
    match on. Names follow `ck_<table>__<rule>` throughout the schema.

    TR-039: every range and closed-set `CHECK` below sits on a `NOT NULL`
    column, so none can be satisfied vacuously by a null.

    `vector_dimension` is written as the literal 384 rather than derived. Per
    TR-076 the DDL literal governs and the row is the published copy: the
    revision that declares `chunk.embedding vector(384)` cannot read its
    dimension out of a table this same chain is still building. TR-048 / SC-019
    close the gap with a test that compares this row against the declared
    typmod; drift is repaired here, never by altering that column.
    """
    op.execute(
        """
        CREATE TABLE schema_constants (
            singleton boolean NOT NULL,
            vector_dimension integer NOT NULL,
            survival_horizon_days integer NOT NULL,
            draw_count integer NOT NULL,
            probability_sum_tolerance double precision NOT NULL,
            anchor_date_convention text NOT NULL,
            percentile_convention text NOT NULL,
            CONSTRAINT pk_schema_constants PRIMARY KEY (singleton),
            CONSTRAINT ck_schema_constants__singleton
                CHECK (singleton),
            CONSTRAINT ck_schema_constants__vector_dimension_positive
                CHECK (vector_dimension > 0),
            CONSTRAINT ck_schema_constants__horizon_positive
                CHECK (survival_horizon_days > 0),
            CONSTRAINT ck_schema_constants__draw_count_positive
                CHECK (draw_count > 0),
            CONSTRAINT ck_schema_constants__tolerance_range
                CHECK (probability_sum_tolerance > 0 AND probability_sum_tolerance < 1),
            CONSTRAINT ck_schema_constants__anchor_convention
                CHECK (anchor_date_convention = 'run_as_of_date'),
            CONSTRAINT ck_schema_constants__percentile_convention
                CHECK (percentile_convention = 'nearest_rank_one_based_no_interpolation')
        )
        """
    )

    # TR-056: three of these six were chosen during planning rather than
    # measured, and each is recorded in the data model as a scope decision with
    # its evidence and reversal trigger (Principle VII). TR-033: the remaining
    # two publish the conventions every reported figure is computed under --
    # one as-of date per run, and percentiles by nearest rank, one-based, with
    # no interpolation. Naming them here is what makes a number reproducible by
    # someone who did not write the code that produced it.
    op.execute(
        """
        INSERT INTO schema_constants (
            singleton,
            vector_dimension,
            survival_horizon_days,
            draw_count,
            probability_sum_tolerance,
            anchor_date_convention,
            percentile_convention
        ) VALUES (
            true,
            384,
            365,
            4000,
            1e-9,
            'run_as_of_date',
            'nearest_rank_one_based_no_interpolation'
        )
        """
    )


def downgrade() -> None:
    """Refuse: migrations in this project are forward-only.

    TR-002. Kept as a raising stub rather than deleted, because Alembic calls
    this attribute when a downgrade is requested and a missing one would fail
    with an unexplained AttributeError instead of stating the policy.
    """
    raise NotImplementedError(
        "This migration is forward-only (TR-002) and defines no downgrade. "
        "To undo a schema change, author a new forward revision; to recover a "
        "database, restore it from a backup."
    )
