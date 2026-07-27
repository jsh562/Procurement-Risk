"""price table version

Revision ID: 0100
Revises: 0010
Create Date: 2026-07-26

E004's first revision, and the first in the `0100`-`0199` block {SAD:ADR-0013}
reserves for it (TR-018). E003 owns this directory, the Alembic configuration
and the runner; this epic authors revisions into it and builds no tooling of
its own (TR-017).

**What this table is for.** A recorded cost is only auditable if the rates it
was computed from are recoverable, so `llm_invocation` pins the *version* of the
rate table it priced against rather than only the resulting figure. This
revision creates the version header; `0101` creates the rates themselves.

**Why `snapshot_date` and `source_url` are mandatory** (TR-081). Principle I
forbids an unattributable number, and a rate table whose origin is unrecorded
makes every derived cost unattributable one hop up. Both are `NOT NULL` rather
than nullable-with-a-convention, so a version that cannot say where its rates
came from is not representable at all. `note` is genuinely optional and says so
by being the only nullable column here.

**`snapshot_date` is not `effective_from`.** The first is when the published
rates were *captured*; the second, on `0101`'s rows, is when a rate *takes
effect*. One snapshot can legitimately carry several effective-from rows for one
model, including a scheduled future change, so conflating the two would make
TR-039's within-version lookup impossible to express. Neither may be substituted
for the other and the distinction is recorded here because the two are easy to
read as synonyms.

**Re-runnable on its own** (TR-050). Alembic's ledger already makes re-running
the *chain* a no-op, and E003's revisions rely on that alone. E004's TR-050 asks
for more: each file must also survive being run against a database where its
objects already exist, so that a lost or reset ledger is a recoverable
inconvenience rather than a hard failure. Hence `IF NOT EXISTS`, and hence every
constraint is declared *inline* in `CREATE TABLE` rather than added by
`ALTER TABLE ADD CONSTRAINT` — the latter has no `IF NOT EXISTS` form in
PostgreSQL 16 and would raise on the second run, which is the case this
requirement exists to survive.
"""

from collections.abc import Sequence

from alembic import op

# Revision identifiers, used by Alembic.
#
# TR-018: `0100` opens E004's reserved block. `down_revision` is E003's head,
# so the two epics' chains are one chain — ordering is `down_revision` and only
# `down_revision`; the numeric prefix is a block claim, never a comparison the
# runner makes.
revision: str = "0100"
down_revision: str | Sequence[str] | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create `price_table_version`.

    Every constraint is named, following E003's `pk_<table>`,
    `ck_<table>__<rule>` convention. Two mechanical reasons, both E003's and
    both still true here: a server-generated name cannot be relied on by a later
    forward migration's `DROP CONSTRAINT`, and a test asserting *which* rule
    rejected a row matches on the constraint name — never on message text, which
    is locale- and version-dependent.
    """
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS price_table_version (
            -- Human-readable and configuration-pinnable, e.g.
            -- `2026-07-25-published`. A surrogate integer would make the
            -- configuration pin unreadable and a diff of the pin
            -- uninformative -- the pin is read by people deciding whether a
            -- cost is still current.
            version_id text NOT NULL,

            -- TR-081. When the published rates were captured. NOT NULL, so the
            -- CHECK below cannot pass vacuously: a CHECK rejects only on
            -- *false*, and any comparison against NULL is NULL, which a CHECK
            -- accepts.
            snapshot_date date NOT NULL,

            -- TR-081. Provenance for a figure the product publishes.
            source_url text NOT NULL,

            -- The one genuinely optional column: what changed, and why the
            -- snapshot was taken. Absence carries no meaning.
            note text,

            -- When the row was inserted, as opposed to when the rates were
            -- published. Both are needed to audit a seeded table, which is why
            -- this is not derivable from `snapshot_date`.
            created_at timestamptz NOT NULL,

            CONSTRAINT pk_price_table_version PRIMARY KEY (version_id),

            -- Slug shape: lowercase alphanumeric segments joined by single
            -- hyphens. Anchored at both ends, because an unanchored pattern
            -- would accept a slug with anything wrapped around it.
            CONSTRAINT ck_price_table_version__slug
                CHECK (version_id ~ '^[a-z0-9]+(-[a-z0-9]+)*$'),

            -- A blank URL satisfies NOT NULL and carries no provenance at all,
            -- which is the failure TR-081 exists to prevent rather than a
            -- lesser version of it.
            CONSTRAINT ck_price_table_version__source_url_present
                CHECK (btrim(source_url) <> '')
        )
        """
    )


def downgrade() -> None:
    """Refuse: migrations in this project are forward-only.

    Kept as a raising stub rather than deleted, following E003's arrangement in
    this directory: Alembic calls this attribute when a downgrade is requested,
    and a missing one fails with an unexplained `AttributeError` instead of
    stating the policy.
    """
    raise NotImplementedError(
        "This migration is forward-only and defines no downgrade. "
        "To undo a schema change, author a new forward revision; to recover a "
        "database, restore it from a backup."
    )
