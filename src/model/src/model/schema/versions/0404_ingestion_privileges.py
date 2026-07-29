"""ingestion privileges

Revision ID: 0404
Revises: 0403
Create Date: 2026-07-27

FR-066, FR-041, SC-024, VR-011. The last revision of E006's block, so the
grant-then-revoke reads in one place, and it follows E003's `0009` shape exactly:
grant the ordinary four verbs, then take two back, so the append-only rule reads
as a deliberate revoke rather than as an omission -- and an omission is
indistinguishable from having forgotten.

**The end state, and it is what VR-011 asserts against `has_table_privilege`
under `SET LOCAL ROLE procurement_app`:**

* `ingestion_run` -- `SELECT, INSERT, UPDATE`. `UPDATE` is required and is not a
  provenance edit: `finished_at` and the two run-failure columns are written
  after the row is inserted, the last of them in a *fresh* transaction after a
  rollback (`data-model.md` §Write Order). `DELETE` withheld -- dropping a run is
  never part of promotion.
* The six other tables -- `SELECT, INSERT` only. These rows *are* provenance: an
  association silently repointed at a different run makes SC-021 true and
  meaningless.
* `v_active_ingestion_generation` -- `SELECT`.

Thirteen refusals in total: six tables x two verbs, plus `DELETE` on
`ingestion_run`.

**`ingestion_run_document` has no `UPDATE`, and the reason is {SAD:ADR-0020}.**
An earlier design granted it for write-order step 0a's `active -> superseded`
flip, on the understanding that the ingestion job performed that flip on every
re-ingest. It no longer can: the flip names a generation that steps 0c-0g then
delete, and the application role holds `DELETE` on none of those tables, so a
re-ingest under `procurement_app` would flip the row and then fail on the first
delete -- an aborted transaction preceded by a pointless privilege. The flip
moved to the schema-owning role together with the removal it names, so the grant
follows it. An unexercised grant is worse than a missing one here, because
grant-then-revoke is how this schema says a restriction was meant.

**No `ALTER DEFAULT PRIVILEGES`, and every object is granted explicitly.**
`0009`'s `GRANT SELECT ON ALL TABLES IN SCHEMA public` covered only the tables
existing then, and `0009` declined default privileges deliberately: a future
append-only table would otherwise acquire `UPDATE` and `DELETE` silently and its
author would have to know to revoke them. Fail closed instead -- a revision that
adds a table grants to `procurement_app` explicitly or the role cannot touch it.

**Reach, restated rather than inherited silently.** The deployed connection role
is the SUPERUSER `procurement`, which bypasses every privilege check, so this
guarantee is latent exactly as E003's **G-11** records: real, catalogued,
asserted by test under `SET LOCAL ROLE`, and not operative against the role the
application actually connects as. SC-024 is an E006 criterion and must not be
reported as fully enforced in the deployed configuration. Carried as **G-6**.
"""

from collections.abc import Sequence

from alembic import op

# Revision identifiers, used by Alembic.
#
# FR-040: `revision` doubles as the four-digit filename prefix. Ordering is
# `down_revision` and only `down_revision`.
revision: str = "0404"
down_revision: str | Sequence[str] | None = "0403"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: The non-superuser role E003's `0009` creates and this revision constrains.
#: A module constant only so the name appears once in prose; every statement
#: below spells it out as a literal, because assembling an identifier into SQL by
#: formatting is the pattern Ruff S608 exists to stop even where the input is a
#: constant. The role is not created here: `0009` creates it, guarded, and a
#: second creation would be a second answer about what it is.
APPLICATION_ROLE = "procurement_app"


def upgrade() -> None:
    """Grant the four verbs on E006's objects, then take back six tables' two.

    Re-application is a no-op by virtue of Alembic's `alembic_version`
    bookkeeping (VR-014). Do not add a "have I already run?" guard here.

    `USAGE` on the schema is already held from `0009` and is not re-granted: it
    is a schema-level privilege, not a per-table one, and re-granting it here
    would suggest it had lapsed.
    """
    # Every table this epic adds, in one statement. The blanket grant is what
    # makes the revoke below load-bearing rather than decorative.
    op.execute(
        """
        GRANT SELECT, INSERT, UPDATE, DELETE ON
            ingestion_run,
            ingestion_run_document,
            ingestion_run_chunk,
            ingestion_run_extracted_value,
            ingestion_run_extraction_failure,
            extracted_value_line_item,
            extracted_value_parse_signal
        TO procurement_app
        """
    )

    # The view is read-only by intent and by grant. It is not swept into the
    # four-verb statement above and then narrowed: an auto-updatable view can
    # carry `INSERT` in PostgreSQL, so granting one here -- even to revoke it a
    # statement later -- would state that writing through the view had ever been
    # contemplated.
    op.execute("GRANT SELECT ON v_active_ingestion_generation TO procurement_app")

    # FR-066, VR-011. Twelve of the thirteen refusals: six tables, two verbs.
    # `ingestion_run_document` is among them for the {SAD:ADR-0020} reason the
    # module docstring gives -- the promotion that would need the `UPDATE` also
    # needs `DELETE` this role does not have, so the grant would be unexercisable.
    op.execute(
        """
        REVOKE UPDATE, DELETE ON
            ingestion_run_document,
            ingestion_run_chunk,
            ingestion_run_extracted_value,
            ingestion_run_extraction_failure,
            extracted_value_line_item,
            extracted_value_parse_signal
        FROM procurement_app
        """
    )

    # The thirteenth. `UPDATE` stays: `finished_at` and the two run-failure
    # columns are written after insert, and the run-level failure write happens
    # in a fresh transaction after a rollback, so the job must be able to issue
    # it. `DELETE` goes: promotion stops at the generation row and never removes
    # a run (§Operator Procedures, step 6), so the privilege would authorise only
    # the one deletion the design forbids.
    op.execute("REVOKE DELETE ON ingestion_run FROM procurement_app")


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
