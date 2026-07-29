"""ingestion run document

Revision ID: 0401
Revises: 0400
Create Date: 2026-07-27

The generation record: one row per `(run, document)` pair a run actually
ingested, plus the partial unique index that makes "one live generation per
document" a database guarantee, plus the view every consumer reads it through.
One revision for all three, because the view reads both this table and
`ingestion_run` and cannot be split from either -- an intermediate head at which
the view is missing is a state a forward-only chain can only add to.

**This table, and not `ingestion_run`, is where generation status lives, and the
reason is FR-043** ({SAD:ADR-0019}). A run skips documents whose input tuple is
unchanged and creates no rows for them, so a run that reloads 3 of 51 documents
leaves the other 48 documents' live rows owned by earlier runs. A run-level flag
would therefore have to read `active` and `superseded` at once. Generation state
is per `(run, document)` and nowhere else.

**`superseded` is a within-transaction state ({SAD:ADR-0020}).** Promotion marks
the predecessor, removes it leaf-up, and only then inserts the successor as
`active` -- all inside the successor document's single transaction (FR-054), so
every *committed* row carries `status = 'active'`. The value stays in the
vocabulary rather than being reduced to a boolean because it is what names the
generation the promotion is about to delete: the delete statements select on it.

**Why the removal happens at all, rather than the predecessor being retained.**
E003's `chunk` carries `uq_chunk__document_ordinal UNIQUE (document_id,
ordinal)`, scoped to the document because at the time it was written there was no
generation to scope it by. Chunk ordinals are zero-based, so two resident
generations of one document both contain `(document_id, 0)` and the second
generation's first chunk insert is rejected. E006 may add no constraint to
`chunk` and may not widen that one ({SAD:ADR-0017}), so retention was not
expensive but *unstorable*, and {SAD:ADR-0020} makes promotion remove the prior
generation instead.

**The partial unique index cannot be deferred, and that fixes a write order.**
`CREATE UNIQUE INDEX ... WHERE` produces an *index*, not a constraint, and
PostgreSQL admits `DEFERRABLE` only on constraints -- no deferral setting rescues
the reverse order. Insert-before-removal raises a unique violation on the insert
(VR-017). Every statement is in one transaction, so a crash at any point rolls
back to the old generation intact and active, which is the correct state to fail
into.
"""

from collections.abc import Sequence

from alembic import op

# Revision identifiers, used by Alembic.
#
# FR-040: `revision` doubles as the four-digit filename prefix. Ordering is
# `down_revision` and only `down_revision`.
revision: str = "0401"
down_revision: str | Sequence[str] | None = "0400"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create `ingestion_run_document`, its two indexes, and the active view.

    Re-application is a no-op by virtue of Alembic's `alembic_version`
    bookkeeping (VR-014). Do not add a "have I already run?" guard here.

    Ordering inside this revision matters only in that the view is created last,
    after both relations it reads exist.
    """
    # --- ingestion_run_document (T010) ---------------------------------------
    op.execute(
        """
        CREATE TABLE ingestion_run_document (
            run_id uuid NOT NULL,
            document_id text NOT NULL,

            -- `NOT NULL` and the `CHECK` together are what make the state space
            -- two. A `CHECK` rejects only on *false*, so a NULL status would
            -- pass it; and `status = 'active'` evaluates to NULL for a NULL
            -- status, so the row would fall out of the partial index predicate
            -- as well. A NULL-status generation would be neither active nor
            -- superseded -- a third state arrived at by omission (VR-023).
            status text NOT NULL,

            -- FR-043's seven-member tuple reduced to one comparison, computed
            -- over the document's *own* manifest content hash and not the
            -- whole-corpus digest: a corpus-wide digest would make any change to
            -- any document reload all 51, which is the opposite of what FR-043
            -- asks for. `provider_model` is a member, so a run under a different
            -- model reloads rather than replaying fixtures recorded against the
            -- previous one.
            input_tuple_digest text NOT NULL,

            -- The instant the document's single transaction (FR-054) committed.
            -- Per-document rather than per-run because that is the granularity
            -- at which durability is actually achieved.
            committed_at timestamptz NOT NULL DEFAULT now(),

            -- Also the foreign-key target for all three run-output associations
            -- in `0402`, which is why it is a composite of exactly these two
            -- columns and in this order.
            CONSTRAINT pk_ingestion_run_document PRIMARY KEY (run_id, document_id),

            CONSTRAINT ck_ingestion_run_document__status
                CHECK (status IN ('active', 'superseded')),

            CONSTRAINT ck_ingestion_run_document__tuple_digest_format
                CHECK (input_tuple_digest ~ '^sha256:[0-9a-f]{64}$'),

            -- RESTRICT: a run cannot be dropped while any generation still
            -- points at it -- the removal order is leaf-up and this is what
            -- enforces it. The promotion's removal stops at the generation row
            -- and never touches `ingestion_run`, so a replaced run's
            -- configuration record survives its rows.
            CONSTRAINT fk_ingestion_run_document__run
                FOREIGN KEY (run_id)
                REFERENCES ingestion_run (run_id)
                ON DELETE RESTRICT
                ON UPDATE CASCADE,

            -- `ON UPDATE CASCADE` because `document_id` is a natural text key
            -- and E003's G-9 keeps a format correction open as a live
            -- possibility; this mirrors `fk_chunk__document` so a correction
            -- propagates here too rather than deadlocking.
            CONSTRAINT fk_ingestion_run_document__document
                FOREIGN KEY (document_id)
                REFERENCES document (document_id)
                ON DELETE RESTRICT
                ON UPDATE CASCADE
        )
        """
    )

    # FR-055, SC-043, VR-002. At most one active generation per document, as a
    # database guarantee: a second activation fails on write rather than
    # producing two live generations that readers silently union.
    #
    # The same mechanism as E003's `ix_forecast_run__single_active`, at a
    # different scope, and the difference is the point: that index is global and
    # holds at most one row in total, this one is keyed on `document_id` and
    # legitimately holds up to 51.
    #
    # At commit it does work its name does not suggest. Every committed row is
    # active ({SAD:ADR-0020}), so over committed state it behaves as
    # `UNIQUE (document_id)` -- at most one generation row per document, full
    # stop. The primary key is `(run_id, document_id)`, so without this index two
    # runs could each hold a committed row for one document. This is what makes
    # "the promotion actually removed its predecessor" enforced rather than
    # trusted, and it fires one statement earlier than
    # `uq_chunk__document_ordinal` would, on the row that is actually wrong.
    #
    # Kept partial rather than narrowed to `UNIQUE (document_id)`: {SAD:ADR-0019}
    # carries this form forward and {SAD:ADR-0020} does not reopen it, the two
    # are indistinguishable over committed state, and the partial form is the one
    # that still works if the removal is ever unbundled from promotion.
    op.execute(
        """
        CREATE UNIQUE INDEX ix_ingestion_run_document__single_active
        ON ingestion_run_document (document_id)
        WHERE status = 'active'
        """
    )

    # Full index, not partial, and it is not redundant against the partial one.
    # It serves the RESTRICT check on a `document` delete and the promotion's own
    # lookup of the row it is about to mark and remove: a row mid-promotion is
    # `superseded` and falls out of the partial predicate, and it is exactly then
    # that the promotion needs to find it. Both reads are otherwise a sequential
    # scan.
    op.execute(
        """
        CREATE INDEX ix_ingestion_run_document__document
        ON ingestion_run_document (document_id)
        """
    )

    # --- v_active_ingestion_generation (T010) --------------------------------
    #
    # FR-055, SC-043, VR-018. The single place the filtering obligation is
    # discharged, and the place run attribution is obtained at all: which chunker
    # version, which embedding revision, which run produced the row in hand.
    #
    # One view, not three. Every consumer joins its own target table to that
    # table's run-output association and then to this view, so the predicate is
    # written once and a reader that forgets it is visible as a missing *join*
    # rather than as a missing `WHERE` buried in a filter list. Per-target views
    # were rejected: they would have to select the 384-dimension embedding column
    # or omit it, and either choice is wrong for one of E008's two retrieval
    # arms.
    #
    # No `LIMIT` and no recency fallback, both following `v_active_forecast_run`
    # exactly. A `LIMIT` would *conceal* a second active generation rather than
    # the index preventing one. Zero rows for a document is legal and meaningful:
    # it says "this document has not been ingested under the current inputs", and
    # a consumer must be able to tell that apart from "ingested under them".
    #
    # No `status` column is exposed: every row returned is active by
    # construction, and carrying the column would invite a reader to filter on it
    # again and conclude the view does not.
    #
    # The columns are enumerated rather than `SELECT *`-ed because this view
    # spans two tables and a star would be ambiguous about which side a column
    # came from; `v_active_forecast_run` reads one table and can afford the star.
    op.execute(
        """
        CREATE VIEW v_active_ingestion_generation AS
            SELECT d.document_id, d.run_id, d.input_tuple_digest, d.committed_at,
                   r.agent_id, r.provider_model, r.chunker_version,
                   r.embedding_model_id, r.embedding_model_revision,
                   r.resolution_mode, r.confidence_floor, r.started_at, r.finished_at
            FROM ingestion_run_document d
            JOIN ingestion_run r ON r.run_id = d.run_id
            WHERE d.status = 'active'
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
