"""provenance privileges

Revision ID: 0009
Revises: 0008
Create Date: 2026-07-26 09:55:36.369611

TR-084, TR-086, SC-028: `extracted_value`, `extracted_value_contributing_chunk`
and `extraction_failure` become append-only by *privilege* rather than by policy.
A correction stays a remove-and-reload of the affected chunks, in the order the
`RESTRICT` citation edges permit, and never an in-place edit of a stored
citation, page, confidence, or outcome.

**Three tables, not two, and the third is the one that mattered most.** This
revision was first written against the two tables TR-084 then named, and flagged
the omission rather than widening the rule on its own authority: with
`extracted_value_contributing_chunk` still mutable by the application role, a
value's *citation set* could be silently truncated -- `DELETE` the contributor
rows at ordinals 2 and 3 -- without a single statement touching either named
table. The parent value row is unchanged, its `source_chunk_count` still says 3,
and the provenance view now returns one source. That is the G-1 shape exactly:
a reader sees an incomplete citation set and nothing indicates it was ever
longer. TR-084 was amended in `spec.md` to name all three tables, and this
revision now revokes on all three.

**This revision creates a role, and that is not incidental -- it is the only way
the requirement can be true at all.** The deployment `docker-compose.yml`
declares (TR-037 freezes it) has exactly one role, `procurement`, and that role is
a SUPERUSER. A superuser bypasses every privilege check, so `REVOKE UPDATE,
DELETE ON extracted_value FROM procurement` is accepted by the server, recorded
in the catalog, and changes nothing: the next `UPDATE` still succeeds. Written
that way TR-084 would have been a migration that reads as enforcement and
enforces nothing, which is the precise failure Principle I names -- a guarantee
that invites trust it has not earned. So the revoke needs a non-superuser subject
to be revoked *from*, and this revision creates one: `procurement_app`.

**The guarantee this establishes is latent, not active, and that is disclosed
rather than papered over.** Nothing connects as `procurement_app` today --
`DATABASE_URL` is frozen by E001 and names `procurement`, and the new role is
`NOLOGIN` because there is no connection for it to serve. The privilege fact is
real, is asserted against the catalog, and is exercised by tests through
`SET LOCAL ROLE`; it becomes an *operational* guarantee on the day the
application's connection role changes, and not before. `data-model.md`
§Disclosed Gaps records this as **G-11** with its reversal trigger and
production-scale alternative, per Principle VII. Do not describe TR-084 as fully
enforced in the deployed configuration; describe it as enforced for the
application role, which the deployed configuration does not yet use.

**Why a privilege and not one of the alternatives.**

* *A rule or a trigger.* `CREATE RULE ... AS ON UPDATE DO INSTEAD NOTHING` would
  silently discard the update rather than refuse it, which inverts Principle III
  -- it converts a visible failure into an invisible one. A `BEFORE UPDATE ...
  RAISE` trigger would refuse loudly, but this schema carries **zero triggers by
  design** (data-model.md; the one deferrable constraint lives in `0007`), and a
  trigger is bypassed by `ALTER TABLE ... DISABLE TRIGGER`, which any table owner
  can issue. A privilege is checked by the server before the statement is planned
  and there is no per-statement escape from it short of changing the grant.
* *A security-barrier view.* That controls what rows are *visible*; it does not
  refuse a write against the base table, which the application can still name.
* *`REVOKE ... FROM PUBLIC`.* PUBLIC holds no table privileges by default, so
  this is a no-op today, and it does not constrain a future explicit grant to a
  named role. Revoking from the role that would actually hold the privilege is
  the statement with content.

**Idempotence (TR-003).** Re-application is a no-op by Alembic's
`alembic_version` bookkeeping, and no "have I already run?" guard belongs in the
body. The `IF NOT EXISTS` guard around `CREATE ROLE` is a different thing, and is
the same case `0001` documents for `CREATE EXTENSION`: **a role is a cluster-wide
object and `alembic_version` is per-database**, so the bookkeeping genuinely
cannot see it. A second database in the same cluster being migrated for the first
time -- which is exactly what `test_migration_chain.py`'s `scratch_database`
fixture does on every run -- reaches this revision with `procurement_app` already
present. Without the guard that run dies on a duplicate-object error, and the
chain would be unapplicable to any cluster that had ever applied it.

**TR-086: the migration role is not touched.** No statement here revokes anything
from `procurement`, and the remove-and-reload correction path is therefore
unchanged. Nothing needed to be granted to it either -- it owns every table in
the schema.

**No `ALTER DEFAULT PRIVILEGES`, deliberately.** It would have saved later
revisions from granting explicitly, at the cost of making "the application role
can write it" the automatic property of every table created afterwards, in this
epic and in every later one sharing the database. That is the wrong default for a
schema whose provenance tables are append-only: a future append-only table would
silently acquire `UPDATE` and `DELETE` and its author would have to know to
revoke them. Fail closed instead -- a revision that adds a table grants to
`procurement_app` explicitly or the role cannot touch it.
"""

from collections.abc import Sequence

from alembic import op

# Revision identifiers, used by Alembic.
#
# TR-004: `revision` doubles as the four-digit filename prefix -- 0001-0099 is
# this epic's reserved block, 0100-0199 is E004's. Ordering is `down_revision`
# and only `down_revision`; the numbers are never compared to decide what runs
# next, so a gap or an out-of-order id is a naming defect, not a broken chain.
revision: str = "0009"
down_revision: str | Sequence[str] | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The non-superuser role the application is intended to connect as, and the
#: subject of every statement in this revision. Named in `data-model.md`
#: §Disclosed Gaps G-11; `test_extraction.py` restates it as its own constant
#: rather than importing it, because a version module's name is not an identifier.
#:
#: A module constant only so the name appears once in prose; every statement
#: below spells it out as a literal. These are DDL strings with no value to
#: interpolate, and assembling an identifier into SQL by formatting is the
#: pattern Ruff S608 exists to stop, even where the input is a constant.
APPLICATION_ROLE = "procurement_app"

#: Guarded `CREATE ROLE`. `CREATE ROLE IF NOT EXISTS` does not exist in
#: PostgreSQL 16, so the existence test is written out; `pg_catalog.pg_roles` is
#: readable by any role, so this works regardless of who runs the migration.
#:
#: `NOLOGIN` is the honest state: no connection string names this role, so giving
#: it a password would create a credential nothing issues and nothing rotates.
#: Granting `LOGIN` is a one-statement forward migration on the day the
#: application's connection role changes -- see G-11's reversal trigger.
CREATE_APPLICATION_ROLE = """
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_catalog.pg_roles WHERE rolname = 'procurement_app') THEN
        CREATE ROLE procurement_app NOLOGIN;
    END IF;
END
$$
"""


def upgrade() -> None:
    """Create the application role, grant it ordinary DML, then take back two verbs.

    The order matters and is the point of the revision. The blanket grant is what
    makes the revoke load-bearing: the application role is given the same
    `SELECT, INSERT, UPDATE, DELETE` on every table as on any other, and then
    `UPDATE` and `DELETE` are taken back on the three provenance tables
    specifically. Granting `SELECT, INSERT` there in the first place would reach
    the same end state, but it would express the rule as an omission -- and an
    omission is indistinguishable from having forgotten, which is exactly what a
    reader of a security-relevant migration must not have to guess about.

    What the end state must be, and what `T049` asserts against the catalog:

    * `procurement_app` holds `SELECT` and `INSERT` on `extracted_value`,
      `extracted_value_contributing_chunk` and `extraction_failure`, and holds
      neither `UPDATE` nor `DELETE`. Append-only, not read-only -- ingestion must
      still be able to write a citation.
    * `procurement_app` holds all four verbs on every other table of the schema.
    * `procurement` holds all four everywhere, including the three provenance
      tables (TR-086).

    PostgreSQL records no negative grant: after the revoke there is simply no
    `UPDATE` row for this grantee in `information_schema.role_table_grants`.
    "The revoke is recorded" therefore means the privilege is absent from the
    catalog while its siblings are present, which is what `has_table_privilege`
    reports and what the test asserts.
    """
    op.execute(CREATE_APPLICATION_ROLE)

    # Reaching the schema at all. Without USAGE, every grant below is unusable
    # and the tests would report a rejection that looks like the one they want
    # for entirely the wrong reason.
    op.execute("GRANT USAGE ON SCHEMA public TO procurement_app")

    # Every table and view that exists at this revision -- the eleven data tables
    # of `0002`-`0008` plus the three views. `ON ALL TABLES` rather than an
    # enumeration: the object inventory is `data-model.md`'s to declare and T052's
    # to audit, and restating it here would create a second list to keep in step.
    op.execute(
        "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO procurement_app"
    )

    # `ON ALL TABLES` swept up Alembic's bookkeeping table, which is not
    # application data. An application role able to rewrite `alembic_version` can
    # make a database claim any revision it likes, so the migration runner would
    # then be reasoning about a schema version the application chose. Read is
    # kept -- knowing which revision it is talking to is legitimate.
    op.execute("REVOKE ALL ON alembic_version FROM procurement_app")
    op.execute("GRANT SELECT ON alembic_version TO procurement_app")

    # TR-084. The whole requirement, in one statement.
    #
    # `extracted_value_contributing_chunk` is named here because TR-084 names it.
    # This revision first revoked on two tables and recorded the omission as an
    # observation rather than widening a requirement's scope on its own
    # authority; the requirement was then amended to name all three, and the
    # third is the one that closes the quiet path -- deleting contributor rows
    # truncates a citation set without touching either other table, and the
    # provenance view simply returns fewer sources than `source_chunk_count`
    # declares (gap G-1's runtime shape, arrived at by privilege rather than by
    # a partial write).
    op.execute(
        "REVOKE UPDATE, DELETE ON "
        "extracted_value, extracted_value_contributing_chunk, extraction_failure "
        "FROM procurement_app"
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
