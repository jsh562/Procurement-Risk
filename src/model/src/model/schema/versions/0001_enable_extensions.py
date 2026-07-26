"""enable extensions

Revision ID: 0001
Revises:
Create Date: 2026-07-26 01:30:25.569564

TR-006: enabling an extension is a migration, never a step in a setup document.
A README instruction is not applied by `alembic upgrade head`, so a database that
skipped it fails later at the first `vector(384)` column with an error that names
a missing type rather than a missing setup step. This revision is the base of the
chain (`down_revision = None`) because `chunk.embedding` in `0004` cannot be
declared until the type exists.

The pinned image is `pgvector/pgvector:pg16`, which ships the extension's files;
`CREATE EXTENSION` registers them in *this* database. Should the migration role
ever lack the privilege to do so, that surfaces here, on an empty database, and
not partway through creating tables.

"""

from collections.abc import Sequence

from alembic import op

# Revision identifiers, used by Alembic.
#
# TR-004: `revision` doubles as the four-digit filename prefix -- 0001-0099 is
# this epic's reserved block, 0100-0199 is E004's. Ordering is `down_revision`
# and only `down_revision`; the numbers are never compared to decide what runs
# next, so a gap or an out-of-order id is a naming defect, not a broken chain.
revision: str = "0001"
down_revision: str | Sequence[str] | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Enable `vector`, the only extension this schema requires.

    TR-003: re-application is a no-op by virtue of Alembic's `alembic_version`
    bookkeeping. Do not add a "have I already run?" guard here.

    `IF NOT EXISTS` is nonetheless kept, and is not that guard. It covers the
    case Alembic's bookkeeping cannot see: a database where the extension is
    already present -- installed by an image's init script, restored from a dump,
    or shared with another schema -- being stamped and migrated for the first
    time. Without it that database is unmigratable, and the failure would be a
    duplicate-object error rather than anything actionable.

    No schema is named. The extension installs into the first schema on the
    search path, which is `public`: the schema the whole data model uses, and the
    only one E001's frozen DATABASE_URL can reach (it carries no
    `options=-csearch_path`, and TR-037 forbids adding one).
    """
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")


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
