"""ingestion run

Revision ID: 0400
Revises: 0303
Create Date: 2026-07-27

E006's first revision, and the first in the `0400`-`0499` block (FR-040).
`down_revision` is `0303`, E007's head -- ordering is `down_revision` and only
`down_revision`, so the jump from `0303` to `0400` is a naming convention doing
its job and not a gap in the chain.

**Both the block and the parent moved on 2026-07-28, and for one reason.** This
revision was authored as `0300` on `0103`, E004's head at the time. E007
claimed `0300`-`0399` concurrently -- same baseline, same
scan-for-the-highest-in-use rule, and correct when it was claimed -- and landed
on the default branch first with four revisions of its own, also chaining from
`0103`. Filenames differ, so git merges both sides without a conflict and the
breakage appears only as duplicate revision identifiers and two heads. E007
landed first, so E006 renumbered into `0400`-`0499` and re-parented onto E007's
head. `0200`-`0299` remains E005's reserved-but-empty claim and is not taken.

**This revision is gated (FR-047, SC-034).** It may not be authored before
E003's TR-081 amendment has landed on the default branch: E003's document
described `extracted_value.confidence` as an agent-asserted score, and this
epic writes a *computed* confidence into it (FR-031, FR-057). Writing computed
values into a column the normative artifact calls agent-asserted would mislead
every reader who trusts that document. T001 verifies the amendment; this
revision declares `after:T001` in `tasks.md` and the gate is recorded here so a
reader of the migration chain alone still meets it.

**One row per execution, and the only home of agent identity in the project.**
E003's TR-082 deliberately omits a per-row agent column from `extracted_value`
on the explicit grounds that E006 records agent identity at run granularity, so
`agent_id` here is not a duplicate of anything -- it is the sole answer to "who
is responsible for this citation". That is why it carries a *grammar* check as
well as a presence check (FR-038): a presence check alone accepts `x`, and a
value naming only a human or only a build answers half the question. Two
constraints rather than one, following this schema's pattern everywhere -- a row
rejected for being blank is distinguishable from one rejected for naming only a
person.

**The confidence floor and the three deduction weights are columns, not code
constants (FR-032, FR-046, FR-057).** A weight left in code is whatever happens
to be checked out: recomputing a stored confidence after a weight change
produces a different number with no symptom at all, because the recomputation
succeeds and simply agrees with a policy the row was never scored under. With
floor and weights on the run row, a stored score is checkable against *the
policy that produced it*. Two things follow that were unavailable while they
lived in code, and both are in this revision:

1. FR-057's two named exclusions become single-row `CHECK`s written over the
   columns rather than over the literals `0.80`/`0.15`/`0.10`/`0.25`, so a run
   declaring a floor that fails to reject what the requirement says it must
   reject is unstorable rather than merely wrong.
2. The order of application is part of the record: confidence is
   `((1.0 - alternate) - page_split) - repaired`, left to right, skipping absent
   terms. `double precision` addition is not associative, so declaring the order
   is what lets SC-026's "reproduces the stored value exactly" mean bit equality
   (`data-model.md` §ingestion_run).

**No `status` column, and its absence is the decision ({SAD:ADR-0019}).**
FR-055 marks a run's work *per document*, so generation state lives on
`ingestion_run_document` (revision `0401`). A run that reloads 3 of 51 documents
leaves 48 documents' rows owned by earlier runs, so a run-level flag would have
to be active and superseded at once. VR-022 asserts this column list carries no
`status`, so a later revision cannot reintroduce the flag ADR-0019 rejected
without failing the build.

**No count columns.** Chunks written, values stored, repaired rate, confidence
distribution: all published by the ingestion report and all recomputable by
query over the associations `0402` adds. A stored count is a second answer that
can disagree with the rows, and the first thing a reader does on disagreement is
trust the smaller number.
"""

from collections.abc import Sequence

from alembic import op

# Revision identifiers, used by Alembic.
#
# FR-040: `revision` doubles as the four-digit filename prefix -- `0400`-`0499`
# is E006's reserved block, declared in `tests/checks/test_migration_ranges.py`.
# The numbers are never compared to decide what runs next, so a gap between
# `0303` and `0400` is the block partition working rather than a broken chain.
revision: str = "0400"
down_revision: str | Sequence[str] | None = "0303"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create `ingestion_run` and its operational listing index.

    Re-application is a no-op by virtue of Alembic's `alembic_version`
    bookkeeping (VR-014). Do not add a "have I already run?" guard here.

    Every constraint is explicitly named, following `pk_<table>`,
    `uq_<table>__<purpose>`, `ck_<table>__<rule>`, `fk_<table>__<target>`, and
    `ix_<table>__<purpose>`. Two mechanical reasons, not stylistic ones: a
    server-generated name cannot be referenced by a later forward migration's
    `ALTER TABLE ... DROP CONSTRAINT`, and a test asserting *which* rule rejected
    a row matches on the constraint name -- never on message text, which is
    locale- and version-dependent (`data-model.md` §Conventions).

    Every `CHECK` constraining a single column's value domain sits on a `NOT
    NULL` column, so none can be satisfied vacuously by a null. The four checks
    that do touch a nullable column -- `finished_at`, `run_failure_kind`,
    `run_failure_detail` -- are each written as an explicit null *test*, so the
    expression is definitely true or false on a null rather than null-valued,
    and each is recorded in `data-model.md` §Nullable-column checks.
    """
    # --- ingestion_run (T009) ------------------------------------------------
    op.execute(
        """
        CREATE TABLE ingestion_run (
            -- Generated in the job process before the first write, never by a
            -- database default: the run row is inserted first and every
            -- association in `0402` resolves to it.
            run_id uuid NOT NULL,

            -- FR-038. The composite of the invoking principal and the executing
            -- build. See the module docstring for why this column is the reason
            -- the table exists.
            agent_id text NOT NULL,

            -- The model the extraction requests were issued against. Not a
            -- foreign key to `llm_invocation.gen_ai_request_model`: E004 owns
            -- that table and a run is not an invocation. An FR-043 input-tuple
            -- member, so a run under a different model reloads every document
            -- rather than skipping it and replaying fixtures recorded against
            -- the previous model.
            provider_model text NOT NULL,

            -- FR-017: a boundary change must be attributable. A pySBD version
            -- bump is a chunker-version bump.
            chunker_version text NOT NULL,

            -- Recorded here *as well as* on every chunk (E003's
            -- `chunk.embedding_model_id`). The per-chunk copy is what lets
            -- retrieval refuse to serve a mixed vector space; the per-run copy
            -- is what makes the FR-043 input tuple computable without reading a
            -- chunk.
            embedding_model_id text NOT NULL,
            embedding_model_revision text NOT NULL,

            -- One element per committed manifest (real layer, synthetic layer).
            corpus_manifest_digests text[] NOT NULL,

            -- FR-043 input-tuple members. The declared transmittal field subset
            -- of FR-058 is folded into the schema digest rather than given a
            -- column: the subset decides which failures exist, so a change to it
            -- must invalidate a document's generation exactly as a schema change
            -- does, and folding it in gets that for free.
            extraction_prompt_digest text NOT NULL,
            extraction_schema_digest text NOT NULL,

            -- FR-045. Same two values and the same spelling as
            -- `llm_invocation.resolution_mode`, deliberately -- a reader
            -- comparing a run against its invocations must not have to
            -- translate.
            resolution_mode text NOT NULL,

            -- FR-070: the trace identifier every extraction invocation of this
            -- run is issued under, and therefore the join that makes SC-011 a
            -- reconciliation rather than a contract. Not a foreign key:
            -- `llm_invocation.trace_id` is not unique and must not be, since a
            -- run issues many invocations under one identifier.
            run_trace_id text NOT NULL,

            -- FR-032, FR-057: the floor is 0.80, declared before the run.
            -- Stored per run rather than as a schema constant, so "the floor was
            -- not moved to fit the distribution" is auditable from the row that
            -- used it.
            confidence_floor double precision NOT NULL,

            -- FR-057's three deductions: 0.15 where the printed label matched a
            -- known alternate, 0.10 where the value was assembled across a page
            -- break, 0.25 where the invocation validated only after a repair.
            deduction_alternate_label double precision NOT NULL,
            deduction_page_split double precision NOT NULL,
            deduction_repaired double precision NOT NULL,

            started_at timestamptz NOT NULL,

            -- NULL means the run is in flight or aborted. Per-document
            -- transactions (FR-054) mean an aborted run's committed documents
            -- are still legitimate generations, so a NULL here does *not*
            -- invalidate them.
            finished_at timestamptz,

            -- FR-056. Five run-level failures, disjoint by construction from
            -- `extraction_failure`'s seven per-field outcomes.
            run_failure_kind text,
            run_failure_detail text,

            CONSTRAINT pk_ingestion_run PRIMARY KEY (run_id),

            -- The trim set is spelled out because single-argument `btrim` strips
            -- *spaces only*: an agent identity of one tab would otherwise
            -- satisfy a bare `btrim(agent_id) <> ''` while naming nobody.
            --
            -- `\\u000B` rather than `data-model.md`'s literal vertical tab, and
            -- never `\\v`: PostgreSQL's escape-string syntax has no `\\v`, so
            -- `E'\\v'` is the *letter* `v` -- which would admit a
            -- vertical-tab-only value and reject a legitimate value of `vvv`.
            -- E003's `0004` and `0006` record the same correction.
            CONSTRAINT ck_ingestion_run__agent_id_present
                CHECK (btrim(agent_id, E' \\t\\n\\r\\f\\u000B') <> ''),

            -- FR-038's principal-and-build grammar, so a run naming only one
            -- half is refused on write. The provider model is deliberately not a
            -- member of the grammar: it has its own column, and a second copy
            -- inside this text would be a second answer nothing compares.
            --
            -- An ordinary (non-`E`) string literal, so the backslash before the
            -- `+` reaches the regular expression as an escape of the literal
            -- plus sign rather than being consumed by the string parser.
            --
            -- Written as two adjacent literals separated by a newline, which
            -- SQL concatenates into one constant before the expression is
            -- parsed. The grammar is 100 characters on its own and the whole
            -- line would exceed the 100-column lint limit; splitting it with
            -- `||` instead would store a concatenation *expression* in
            -- `pg_constraint`, so `pg_get_constraintdef` would no longer report
            -- the grammar as a single readable pattern.
            CONSTRAINT ck_ingestion_run__agent_id_format
                CHECK (agent_id ~ '^principal=(human|automation):[A-Za-z0-9._-]+; '
                                  'build=[A-Za-z0-9._-]+@[A-Za-z0-9._-]+\\+[0-9a-f]{7,40}$'),

            CONSTRAINT ck_ingestion_run__provider_model_present
                CHECK (btrim(provider_model, E' \\t\\n\\r\\f\\u000B') <> ''),
            CONSTRAINT ck_ingestion_run__chunker_version_present
                CHECK (btrim(chunker_version, E' \\t\\n\\r\\f\\u000B') <> ''),
            CONSTRAINT ck_ingestion_run__embedding_model_id_present
                CHECK (btrim(embedding_model_id, E' \\t\\n\\r\\f\\u000B') <> ''),
            CONSTRAINT ck_ingestion_run__embedding_model_revision_present
                CHECK (btrim(embedding_model_revision, E' \\t\\n\\r\\f\\u000B') <> ''),

            -- `coalesce(array_length(...), 0)`, never the bare call:
            -- `array_length('{}', 1)` is NULL, not 0, and a `CHECK` accepts NULL
            -- -- so the bare form would admit a run that read no manifest at
            -- all. Same trap E003's `0008` and `0010` record.
            --
            -- `fn_all_sha256_prefixed` is E003's existing IMMUTABLE helper from
            -- `0003`, reused rather than re-declared: a second helper with the
            -- same body would be a second answer, and E006 declares no function
            -- of its own.
            CONSTRAINT ck_ingestion_run__corpus_manifest_digests
                CHECK (
                    coalesce(array_length(corpus_manifest_digests, 1), 0) >= 1
                    AND fn_all_sha256_prefixed(corpus_manifest_digests)
                ),

            CONSTRAINT ck_ingestion_run__extraction_prompt_digest_format
                CHECK (extraction_prompt_digest ~ '^sha256:[0-9a-f]{64}$'),
            CONSTRAINT ck_ingestion_run__extraction_schema_digest_format
                CHECK (extraction_schema_digest ~ '^sha256:[0-9a-f]{64}$'),

            CONSTRAINT ck_ingestion_run__resolution_mode
                CHECK (resolution_mode IN ('record', 'replay')),

            -- The same 32-hex W3C form and the same two checks as
            -- `llm_invocation.trace_id`. The all-zero value is defined as
            -- invalid by that specification, so a NOT NULL admitting it would
            -- enforce presence without enforcing meaning.
            CONSTRAINT ck_ingestion_run__run_trace_id_format
                CHECK (run_trace_id ~ '^[0-9a-f]{32}$'),
            CONSTRAINT ck_ingestion_run__run_trace_id_not_all_zero
                CHECK (run_trace_id <> repeat('0', 32)),

            -- Inclusive at both ends, matching
            -- `ck_extracted_value__confidence_range`: a floor of 0.0 admits
            -- everything and a floor of 1.0 admits only a perfect score, and
            -- both are declarable positions.
            CONSTRAINT ck_ingestion_run__confidence_floor_range
                CHECK (confidence_floor >= 0.0 AND confidence_floor <= 1.0),
            CONSTRAINT ck_ingestion_run__deduction_alternate_label_range
                CHECK (deduction_alternate_label >= 0.0 AND deduction_alternate_label <= 1.0),
            CONSTRAINT ck_ingestion_run__deduction_page_split_range
                CHECK (deduction_page_split >= 0.0 AND deduction_page_split <= 1.0),
            CONSTRAINT ck_ingestion_run__deduction_repaired_range
                CHECK (deduction_repaired >= 0.0 AND deduction_repaired <= 1.0),

            -- FR-057's first named exclusion, as a database fact. The
            -- requirement states the floor by what it rejects rather than by its
            -- number, so this is written over the columns and hard-codes neither
            -- 0.80 nor 0.25: any weight-and-floor combination that fails to
            -- reject a repaired invocation is unstorable.
            CONSTRAINT ck_ingestion_run__floor_excludes_repair
                CHECK (confidence_floor > 1.0 - deduction_repaired),

            -- FR-057's second named exclusion, on the same footing. Both are
            -- single-row checks and could not have been written before the
            -- weights were columns; that is the concrete payoff of moving them
            -- onto the row.
            CONSTRAINT ck_ingestion_run__floor_excludes_alt_split
                CHECK (
                    confidence_floor > 1.0 - deduction_alternate_label - deduction_page_split
                ),

            -- Nullable-column check 1 of 4. `finished_at IS NULL OR ...` is
            -- definitely *true* on a null rather than null-valued. Absence is
            -- admitted deliberately: an aborted or in-flight run has no finish,
            -- and forcing one would fabricate a completion.
            CONSTRAINT ck_ingestion_run__finished_after_started
                CHECK (finished_at IS NULL OR finished_at >= started_at),

            -- Nullable-column check 2 of 4. FR-056's five kinds. No member is
            -- shared with `ck_extraction_failure__outcome`'s seven, so a missing
            -- fixture cannot be recorded as though the model produced something
            -- unusable when nothing was ever asked (VR-007 reads both bodies out
            -- of `pg_constraint` and intersects them).
            CONSTRAINT ck_ingestion_run__failure_kind_domain
                CHECK (
                    run_failure_kind IS NULL
                    OR run_failure_kind IN (
                        'corpus_digest_mismatch',
                        'document_id_collision',
                        'oversized_sentence',
                        'fixture_missing',
                        'provider_unreachable'
                    )
                ),

            -- Nullable-column check 3 of 4, and the one that closes the null
            -- branch of the domain check above -- which is why both exist rather
            -- than one. Both operands are null *tests*, so the expression is
            -- never null-valued. A failure without a stated cause is not
            -- representable.
            CONSTRAINT ck_ingestion_run__failure_detail_iff_kind
                CHECK ((run_failure_kind IS NULL) = (run_failure_detail IS NULL)),

            -- Nullable-column check 4 of 4. SC-044's "the run does not report
            -- completion", as a database fact. Deliberately an implication and
            -- not a biconditional: a run may finish cleanly with no failure, so
            -- the reverse direction would reject every successful run.
            CONSTRAINT ck_ingestion_run__failed_run_unfinished
                CHECK (run_failure_kind IS NULL OR finished_at IS NULL)
        )
        """
    )

    # Operational listing only -- "show me the recent runs" for a human. This
    # index is **never** the selection mechanism: the active generation is
    # selected through `ingestion_run_document` (revision `0401`), not by taking
    # the most recent run. Same discipline E003 fixes for
    # `ix_forecast_run__created_at`; a query reading
    # `ORDER BY started_at DESC LIMIT 1` to decide what is current is the defect,
    # not this index.
    op.execute("CREATE INDEX ix_ingestion_run__started_at ON ingestion_run (started_at DESC)")


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
