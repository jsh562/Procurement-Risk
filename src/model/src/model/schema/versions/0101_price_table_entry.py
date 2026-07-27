"""price table entry

Revision ID: 0101
Revises: 0100
Create Date: 2026-07-26

The rates themselves (TR-015). Four billing classes per model per effective
date, inside one version.

**Why four rate columns and not one** (TR-015). The provider bills cache-write
and cache-read input tokens outside the ordinary input count and at different
multipliers. Folding them into a single input rate silently corrupts every
recomputed cost — silently, because the arithmetic still produces a number and
nothing compares it to the invoice. Each class gets its own column and its own
token count on `llm_invocation`, so `SC-006`'s "recomputation reproduces the
stored cost exactly" is decidable rather than approximate.

**The primary key is what makes the lookup deterministic** (TR-046, VR-010).
`(price_table_version_id, model_id, effective_from)` is the natural key, and
making it the *primary* key is not bookkeeping: TR-039 selects "the latest
`effective_from` at or before the pricing timestamp", and two rows for one model
on one date inside one version would make that ambiguous. No application-side
tie-break could be principled, and an `ORDER BY ... LIMIT 1` would resolve it
arbitrarily while looking correct. The database refuses to represent the
ambiguity instead.

Its index also serves the lookup — `WHERE version = ? AND model_id = ? AND
effective_from <= ? ORDER BY effective_from DESC LIMIT 1` has the key's leading
columns as its own — so no secondary index is added.

**`NUMERIC(12,6)`, never `double precision`** (TR-049). SC-006 asserts exact
reproduction, and binary floating point cannot carry that claim. Scale 6 is
fixed by the requirement rather than chosen here.

**Units and currency live in the column names** (`_usd_per_mtok`) rather than a
currency column. Beyond readability, it means a second currency cannot be added
without a visible schema change — a currency column would let one arrive as a
row nobody reviews.

**Restrictive referential actions** (TR-046). `ON DELETE RESTRICT ON UPDATE
RESTRICT` on the version FK. The price tables are append-only and `version_id`
is a mutable natural key *by type*, so both actions are stated rather than left
to the default: deletion and re-identification are each an error, not a silent
orphaning nor a silent re-pointing of every historical cost that cited the old
identifier.
"""

from collections.abc import Sequence

from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "0101"
down_revision: str | Sequence[str] | None = "0100"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create `price_table_entry`.

    Ordered after `0100` because the FK below needs its referent to exist; a
    foreign key to a missing table fails at DDL time, not at insert time.
    """
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS price_table_entry (
            price_table_version_id text NOT NULL,

            -- Matched by exact, case-sensitive equality against
            -- `llm_invocation.gen_ai_response_model` (TR-057). No
            -- normalization, casefolding, prefix match, or nearest-model
            -- fallback exists on the lookup path -- TR-016 forbids the
            -- nearest-match half, and exact case-sensitive equality is the
            -- positive half. Both are needed for the lookup to be decidable.
            model_id text NOT NULL,

            -- A DATE, compared against `pricing_timestamp` as UTC calendar
            -- dates (CD-1, TR-057). The zone is named at the comparison site
            -- rather than here, because a bare `timestamptz::date` cast
            -- resolves against the session `TimeZone` -- a value neither this
            -- migration nor the data model controls, which would let one row
            -- price differently on two machines.
            effective_from date NOT NULL,

            -- All four classes are always present in the provider's published
            -- table. A missing class is a data error, not a zero rate, which
            -- is why none of these is nullable and none defaults.
            input_usd_per_mtok numeric(12, 6) NOT NULL,
            cache_write_usd_per_mtok numeric(12, 6) NOT NULL,
            cache_read_usd_per_mtok numeric(12, 6) NOT NULL,
            output_usd_per_mtok numeric(12, 6) NOT NULL,

            CONSTRAINT pk_price_table_entry
                PRIMARY KEY (price_table_version_id, model_id, effective_from),

            CONSTRAINT fk_price_table_entry__version
                FOREIGN KEY (price_table_version_id)
                REFERENCES price_table_version (version_id)
                ON DELETE RESTRICT ON UPDATE RESTRICT,

            -- A negative rate is never valid and would silently produce a
            -- negative cost -- a number, passing every arithmetic test, wrong
            -- in a direction nobody audits for. All four sit on NOT NULL
            -- columns, so none can pass vacuously.
            CONSTRAINT ck_price_table_entry__input_rate_non_negative
                CHECK (input_usd_per_mtok >= 0),
            CONSTRAINT ck_price_table_entry__cache_write_rate_non_negative
                CHECK (cache_write_usd_per_mtok >= 0),
            CONSTRAINT ck_price_table_entry__cache_read_rate_non_negative
                CHECK (cache_read_usd_per_mtok >= 0),
            CONSTRAINT ck_price_table_entry__output_rate_non_negative
                CHECK (output_usd_per_mtok >= 0),

            -- A blank model identifier satisfies NOT NULL and matches no
            -- response model, so every invocation citing it would price as
            -- absent for a reason that is really a seeding defect.
            CONSTRAINT ck_price_table_entry__model_id_present
                CHECK (btrim(model_id) <> '')
        )
        """
    )


def downgrade() -> None:
    """Refuse: migrations in this project are forward-only."""
    raise NotImplementedError(
        "This migration is forward-only and defines no downgrade. "
        "To undo a schema change, author a new forward revision; to recover a "
        "database, restore it from a backup."
    )
