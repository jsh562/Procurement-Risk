"""seed price table

Revision ID: 0103
Revises: 0102
Create Date: 2026-07-26

Seeds one sourced price-table version and its entries (TR-015, TR-081).

**Where these numbers came from, and why that matters more than the numbers.**
Principle I forbids an unattributable figure, and every cost this product
publishes is derived from this table — so a seeded rate whose origin is not
recorded makes every derived cost unattributable one hop up. `source_url` and
`snapshot_date` are therefore mandatory columns rather than documentation, and
the values below were read from the published pricing document on the snapshot
date rather than recalled or estimated.

**Cache-write rate is the 5-minute rate, and that is a decision.** The published
table lists two cache-write rates — 1.25x base input for a 5-minute cache and 2x
for a one-hour cache. The gateway records a single `cache_write_input_tokens`
count with no TTL dimension, so exactly one of the two must be seeded. The
5-minute rate is chosen because it is the default TTL, which makes the seeded
figure right for the ordinary case and understated for a one-hour cache. That
direction is deliberate: a *recorded* cost that is too low is visible against an
invoice, whereas seeding the higher rate would silently overstate every ordinary
call and look like nothing was wrong. **Reversal**: if the gateway ever records
the cache TTL, `model_id` cannot carry the distinction — it would need a fourth
key column, which is a new version and a schema change under TR-069.

**Two rows for one model, and this is the case the schema was shaped for.** The
published document states an introductory rate for one model in effect through
31 August 2026, with a higher standard rate from 1 September 2026. Both are
seeded, in one version, distinguished by `effective_from` — which is precisely
why `snapshot_date` (when rates were captured) and `effective_from` (when a rate
takes effect) are separate columns and why the primary key carries the latter.
An invocation priced on either side of that boundary resolves the correct rate
with no code change and no second version.

**`ON CONFLICT DO NOTHING`** (TR-050) makes this file re-runnable on its own, so
a lost or reset ledger is a recoverable inconvenience rather than a hard failure.
It also fixes the correction procedure: because a re-run of *this* file will not
overwrite an existing row, a rate correction is a new higher-numbered migration
seeding a new version — never an edit to this file, which would be a silent
no-op on every database that already ran it.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "0103"
down_revision: str | Sequence[str] | None = "0102"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The pinnable identifier. Slug-shaped by `0100`'s CHECK, and dated so a diff
#: of the configuration pin says *when* the rates were captured without anyone
#: having to query the table.
VERSION_ID = "2026-07-26-published"

#: Read from the published pricing document on the snapshot date.
SOURCE_URL = "https://platform.claude.com/docs/en/about-claude/pricing"

SNAPSHOT_DATE = "2026-07-26"

#: `(model_id, effective_from, input, cache_write_5m, cache_read, output)`, in
#: USD per million tokens, exactly as published. Written as a table rather than
#: as generated SQL so a reviewer can compare it line by line against the
#: source — the whole value of this seed is that the comparison is possible.
RATES: tuple[tuple[str, str, str, str, str, str], ...] = (
    # model_id            effective_from  input    cache_wr  cache_rd  output
    ("claude-opus-5", SNAPSHOT_DATE, "5.000000", "6.250000", "0.500000", "25.000000"),
    ("claude-fable-5", SNAPSHOT_DATE, "10.000000", "12.500000", "1.000000", "50.000000"),
    ("claude-haiku-4-5", SNAPSHOT_DATE, "1.000000", "1.250000", "0.100000", "5.000000"),
    # The introductory rate, in effect on the snapshot date...
    ("claude-sonnet-5", SNAPSHOT_DATE, "2.000000", "2.500000", "0.200000", "10.000000"),
    # ...and the scheduled standard rate, published in the same document with a
    # stated start date. Seeding it now means the boundary needs no intervention
    # on the day it arrives.
    ("claude-sonnet-5", "2026-09-01", "3.000000", "3.750000", "0.300000", "15.000000"),
)

NOTE = (
    "Initial seed. Rates read from the published pricing document on the "
    "snapshot date. Cache-write rate is the 5-minute (1.25x input) rate, not "
    "the 1-hour (2x) rate -- the gateway records no cache TTL, so one had to "
    "be chosen; see revision 0103's docstring."
)


#: Bound rather than interpolated. Every value below is a module constant in
#: this file, so string interpolation would be safe in fact — but it would be
#: safe by inspection rather than by construction, and the next editor who
#: sources a rate from somewhere else inherits an injection site that already
#: looks fine. Parameters make the values *data*, which is also what stops
#: `NOTE`'s apostrophes needing an escaping rule anyone has to remember.
#:
#: Both statements are literal text with fixed placeholders, matching E003's
#: convention in this directory of never assembling SQL from values.
_INSERT_VERSION = sa.text(
    """
    INSERT INTO price_table_version (
        version_id, snapshot_date, source_url, note, created_at
    ) VALUES (
        :version_id, CAST(:snapshot_date AS date), :source_url, :note, now()
    )
    ON CONFLICT (version_id) DO NOTHING
    """
)

_INSERT_ENTRY = sa.text(
    """
    INSERT INTO price_table_entry (
        price_table_version_id, model_id, effective_from,
        input_usd_per_mtok, cache_write_usd_per_mtok,
        cache_read_usd_per_mtok, output_usd_per_mtok
    ) VALUES (
        :version_id, :model_id, CAST(:effective_from AS date),
        CAST(:input_rate AS numeric), CAST(:cache_write AS numeric),
        CAST(:cache_read AS numeric), CAST(:output_rate AS numeric)
    )
    ON CONFLICT (price_table_version_id, model_id, effective_from) DO NOTHING
    """
)


def upgrade() -> None:
    """Insert the version header, then its entries.

    Ordered by the foreign key: an entry whose version does not yet exist is
    rejected at insert time, so the header goes first. `now()` is acceptable for
    `created_at` here in a way it is not on `llm_invocation` — this column
    records when the *row was seeded*, which is genuinely the moment the
    statement runs, whereas an invocation's `created_at` must survive a spool
    detour and therefore cannot be a database default.

    One statement per rate rather than a single multi-row `VALUES`: five rows
    make the loop's cost irrelevant, and a per-row statement keeps the SQL a
    fixed string instead of one assembled from the data it carries.
    """
    connection = op.get_bind()
    connection.execute(
        _INSERT_VERSION,
        {
            "version_id": VERSION_ID,
            "snapshot_date": SNAPSHOT_DATE,
            "source_url": SOURCE_URL,
            "note": NOTE,
        },
    )

    for model_id, effective_from, input_rate, cache_write, cache_read, output_rate in RATES:
        connection.execute(
            _INSERT_ENTRY,
            {
                "version_id": VERSION_ID,
                "model_id": model_id,
                "effective_from": effective_from,
                "input_rate": input_rate,
                "cache_write": cache_write,
                "cache_read": cache_read,
                "output_rate": output_rate,
            },
        )


def downgrade() -> None:
    """Refuse: migrations in this project are forward-only."""
    raise NotImplementedError(
        "This migration is forward-only and defines no downgrade. "
        "To undo a schema change, author a new forward revision; to recover a "
        "database, restore it from a backup."
    )
