"""chunk

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-26 04:41:12.508433

TR-009: the retrievable unit. One row carries its document reference, project
identifier, document type, specification section, page number, and ordinal
position, so a retrieved passage can be named without a second lookup.

TR-011: `embedding vector(384)`. The dimension is enforced by the *type*, not by
a check -- pgvector rejects a mismatched literal at cast time with a dimension
error, before any constraint is consulted, which is both cheaper and impossible
to write a passing row around. 384 is fixed by ADR-0012 and appears here as a
DDL literal. Per TR-076 that literal governs and the `schema_constants`
`vector_dimension` row seeded in `0002` is the published copy of it; T050 /
SC-019 compares the two and drift is repaired in `0002`, never by altering this
column. There is deliberately no third place the number is written.

TR-012: `embedding_model_id` and `embedding_model_revision` are NOT NULL on
every chunk, so two vector spaces in one corpus are *detectable* rather than
silently mixed. Agreement across rows is a cross-row property a `CHECK` cannot
see -- disclosed as gap G-8, tested at build time and refused at serving time.

TR-014: the rejection surface. No searchable text (`ck_chunk__body_text_present`),
no page number (`page_number NOT NULL`), no or malformed document reference
(`fk_chunk__document`), and a project identifier off the frozen format
(`ck_chunk__project_id_format`) are each refused at the storage boundary.

TR-058: exactly one page per chunk, which is why `page_number` is NOT NULL and
scalar. A value spanning several pages is not a chunk spanning several pages --
it is a multi-source value carrying one contributing chunk per page, which `0006`
represents. `uq_chunk__chunk_page (chunk_id, page_number)` exists to make that
possible: it is the key `extracted_value.(source_chunk_id, cited_page)` and
`extracted_value_contributing_chunk.(chunk_id, page_number)` reference, so a
citation whose page disagrees with its source chunk's page has no referent at all
rather than a wrong one (TR-017, STF-006). Redundant against the primary key by
design -- a composite foreign key needs a matching unique key to point at, and
the primary key alone cannot carry the page.

TR-010, TR-038: `search_vector` is a *stored generated* column and its text-search
configuration is named explicitly. The two-argument
`to_tsvector('pg_catalog.english', ...)` form is not a style preference -- the
one-argument form reads `default_text_search_config` from the session, is
therefore no better than STABLE, and PostgreSQL refuses it in a generated column
outright. Naming the configuration is what makes the stored vector a function of
the row alone: two sessions whose session default differs produce byte-identical
vectors, which is the whole of TR-038. Chosen over an expression index because an
expression index would force every call site to repeat the configuration name,
and one that forgot it would silently match nothing.

Weight labels carry no numbers of their own -- A heading, B part number, C
section, D body, scored by `ts_rank`'s default array `{0.1, 0.2, 0.4, 1.0}` for
`{D, C, B, A}`. Relevance tuning is therefore a query-time weight array and never
a migration, a property E008 is expected to rely on.

Every field in that expression is `coalesce`d. `tsvector || tsvector` is NULL if
either operand is NULL, so a single NULL column -- and `heading`, `part_numbers`,
and `spec_section` are all nullable -- would empty the *whole* vector rather than
just its own arm, leaving the chunk unfindable instead of merely badly ranked.
`body_text` is NOT NULL and is wrapped for the same reason regardless: the
expression is frozen into the table at creation, and nothing about a column's
nullability today is visible at the point a later revision relaxes it.

TR-013: both retrieval arms live on this one table, so switching between them
needs no DDL and no second relation. `ix_chunk__embedding_hnsw` is the
approximate serving path ADR-0005 requires; the exact path is the same column
with that index not used, which the planner does whenever a scan costs less (or
under `SET enable_seqscan = off`'s inverse, when it is forced to prefer the
index). The opclass is `vector_cosine_ops` because ADR-0005's similarity measure
is cosine: an index built for another operator class is simply never used by a
`<=>` ordering, and that failure is a silent full scan rather than an error.
"""

from collections.abc import Sequence

from alembic import op

# Revision identifiers, used by Alembic.
#
# TR-004: `revision` doubles as the four-digit filename prefix -- 0001-0099 is
# this epic's reserved block, 0100-0199 is E004's. Ordering is `down_revision`
# and only `down_revision`; the numbers are never compared to decide what runs
# next, so a gap or an out-of-order id is a naming defect, not a broken chain.
revision: str = "0004"
down_revision: str | Sequence[str] | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: TR-011, TR-076, ADR-0012. The one place this epic writes the dimension as
#: code. It is interpolated into the DDL below rather than repeated inline so
#: that "the DDL literal governs" has a single referent a reader can find, and
#: so the `vector(...)` typmod and any future column of the same space cannot
#: drift apart within this file. It is a module constant of this migration and
#: is *not* imported by anything: `schema_constants.vector_dimension` is what
#: every consumer reads (TR-047), because the serving boundary must not import
#: the modeling package to learn the shape of a column.
EMBEDDING_DIMENSION = 384


def upgrade() -> None:
    """Create the `chunk` table.

    TR-003: re-application is a no-op by virtue of Alembic's `alembic_version`
    bookkeeping. Do not add a "have I already run?" guard here.

    Every constraint is named, following `pk_<table>`, `uq_<table>__<purpose>`,
    `ck_<table>__<rule>`, `fk_<table>__<target>`, and `ix_<table>__<purpose>`.
    Two mechanical reasons, not stylistic ones: a server-generated name cannot be
    relied on by a later forward migration's `ALTER TABLE ... DROP CONSTRAINT`,
    and a test asserting *which* rule rejected a row matches on the constraint
    name -- never on message text, which is locale- and version-dependent.

    TR-039: every `CHECK` below constrains a single column's value domain and
    every one of them sits on a `NOT NULL` column, so none can be satisfied
    vacuously by a null. That is why none needs the `coalesce` wrapper the
    layer-conditional checks in `0003` require -- there is no conditional check
    on this table, and no nullable column carries a check at all.
    """
    op.execute(
        f"""
        CREATE TABLE chunk (
            chunk_id uuid NOT NULL,
            document_id text NOT NULL,
            document_type text NOT NULL,
            project_id text NOT NULL,
            page_number integer NOT NULL,
            ordinal integer NOT NULL,
            spec_section text,
            heading text,
            part_numbers text,
            body_text text NOT NULL,

            -- TR-010, TR-038, TR-083: data-model.md's declared expression,
            -- arm for arm and weight for weight. Stored, generated, and pinned
            -- to a *named* configuration; see the module docstring for why the
            -- one-argument `to_tsvector` form is not merely discouraged here
            -- but rejected by the server, and why every arm is `coalesce`d.
            --
            -- One deviation from that artifact's code block, recorded rather
            -- than hidden: it leaves the `body_text` arm bare and `coalesce`s
            -- only the three nullable ones, while its own prose states every
            -- field is `coalesce`d. `body_text` is NOT NULL, so the two forms
            -- are indistinguishable on every row this schema admits; the
            -- wrapper is kept because a generated expression is frozen into the
            -- table and the day a later revision relaxes that column is not a
            -- day anyone re-reads this line.
            search_vector tsvector GENERATED ALWAYS AS (
                setweight(to_tsvector('pg_catalog.english', coalesce(heading, '')), 'A')
                || setweight(
                    to_tsvector('pg_catalog.english', coalesce(part_numbers, '')), 'B'
                )
                || setweight(
                    to_tsvector('pg_catalog.english', coalesce(spec_section, '')), 'C'
                )
                || setweight(to_tsvector('pg_catalog.english', coalesce(body_text, '')), 'D')
            ) STORED,

            embedding vector({EMBEDDING_DIMENSION}) NOT NULL,
            embedding_model_id text NOT NULL,
            embedding_model_revision text NOT NULL,
            created_at timestamptz NOT NULL DEFAULT now(),

            CONSTRAINT pk_chunk PRIMARY KEY (chunk_id),

            -- TR-014. Frozen by E001 and adopted verbatim, not re-derived; the
            -- same pattern `document.project_id` carries, so the composite
            -- foreign key below cannot pair a well-formed chunk identifier with
            -- a malformed document one.
            CONSTRAINT ck_chunk__project_id_format
                CHECK (project_id ~ '^PRJ-[0-9]{{3}}$'),

            -- TR-014, TR-058. Pages are one-based. Absence is refused by the
            -- column's NOT NULL rather than by this check, which is what keeps
            -- the check from being satisfiable by a null.
            CONSTRAINT ck_chunk__page_positive
                CHECK (page_number >= 1),

            -- Ordinal position within the document is zero-based: it is an
            -- index into the chunker's output sequence, not a page number.
            CONSTRAINT ck_chunk__ordinal_non_negative
                CHECK (ordinal >= 0),

            -- TR-014, the "no searchable text" rejection.
            --
            -- No `coalesce` wrapper, and none is needed: `body_text` is NOT
            -- NULL, so the comparison is never NULL and an empty body is a
            -- definite false rather than the vacuous pass a check on a nullable
            -- column would give.
            --
            -- The trim set is spelled out because single-argument `btrim` strips
            -- *spaces only*: a body of nothing but tabs or newlines would
            -- otherwise satisfy this check while producing an empty
            -- `search_vector` one column above -- no searchable text at all,
            -- which is precisely the row TR-014 refuses. The same six-character
            -- set is now the idiom every presence check in this schema carries,
            -- so the rule reads the same on every column that has one.
            --
            -- **Deviation from data-model.md, deliberate (TR-083).** That
            -- artifact spells the set `E' \\t\\n\\r\\f\\v'`, which PostgreSQL does
            -- not read as whitespace: its escape-string syntax has no `\\v`, and
            -- an unrecognized escape drops the backslash and keeps the
            -- character, so `E'\\v'` is the *letter* `v`. Written as declared,
            -- this check would let a vertical-tab-only body through (11 missing
            -- from the set) and reject a legitimate body of `vvv` (118 in it) --
            -- one typo producing both a hole and a false rejection, measured on
            -- PostgreSQL 16, not inferred. `\\u000B` is the same character the
            -- artifact means; octal `\\013` is equivalent. data-model.md needs
            -- correcting to match, and every presence check it declares is
            -- affected, not only this one.
            CONSTRAINT ck_chunk__body_text_present
                CHECK (btrim(body_text, E' \\t\\n\\r\\f\\u000B') <> ''),

            -- TR-012. Recorded per chunk, and required, so a vector-space
            -- mismatch inside one corpus is detectable at all. Both are NOT
            -- NULL, so these are presence checks against blank, not against
            -- absence -- and both use the same widened trim set, because a
            -- model identity of one tab names no model.
            CONSTRAINT ck_chunk__embedding_model_id_present
                CHECK (btrim(embedding_model_id, E' \\t\\n\\r\\f\\u000B') <> ''),
            CONSTRAINT ck_chunk__embedding_model_revision_present
                CHECK (btrim(embedding_model_revision, E' \\t\\n\\r\\f\\u000B') <> ''),

            -- TR-014, TR-046, TR-078, SC-018. References `document`'s composite
            -- unique key `uq_document__id_type_project`, created in `0003`. The
            -- referencing column order is the referenced key's order and is
            -- load-bearing: it is what makes the chunk's denormalized
            -- `document_type` and `project_id` unable to disagree with its
            -- document, rather than merely unlikely to.
            --
            -- `MATCH FULL` is declared for intent even though all three columns
            -- are NOT NULL and it is therefore equivalent to the default here:
            -- were one ever relaxed to nullable, `MATCH SIMPLE` would skip the
            -- check entirely on a partially-null triple.
            --
            -- RESTRICT on delete: dropping a document must not silently orphan
            -- the citations that resolve through its chunks. CASCADE on update
            -- because TR-078 makes a manifest-key change a forward migration
            -- that updates `document_id` in place; extracted-value citations
            -- reference the *chunk*, so they are untouched by it.
            CONSTRAINT fk_chunk__document
                FOREIGN KEY (document_id, document_type, project_id)
                REFERENCES document (document_id, document_type, project_id)
                MATCH FULL
                ON DELETE RESTRICT
                ON UPDATE CASCADE,

            -- TR-017, TR-058, STF-006: the citation foreign-key target. See the
            -- module docstring -- redundant against `pk_chunk` on purpose,
            -- because a composite foreign key must reference a unique key that
            -- carries the page, and the primary key does not.
            CONSTRAINT uq_chunk__chunk_page
                UNIQUE (chunk_id, page_number),

            -- Ordinal position is unique within a document, so the chunker
            -- cannot emit two chunks claiming the same position in one source.
            CONSTRAINT uq_chunk__document_ordinal
                UNIQUE (document_id, ordinal)
        )
        """
    )

    # Plain b-tree indexes, no constraint semantics. Created as separate
    # statements because `CREATE TABLE` admits only constraints inline.
    op.execute("CREATE INDEX ix_chunk__document_page ON chunk (document_id, page_number)")
    op.execute("CREATE INDEX ix_chunk__project ON chunk (project_id)")

    # TR-010, the lexical arm. GIN over the stored vector, not GiST: GIN is the
    # inverted index `tsvector` is designed for, and it indexes the *stored*
    # column, so building and verifying it never re-runs `to_tsvector`.
    op.execute("CREATE INDEX ix_chunk__search_vector ON chunk USING gin (search_vector)")

    # TR-013, ADR-0005, the approximate vector arm. `vector_cosine_ops` because
    # the similarity measure is cosine -- an HNSW index under a different opclass
    # is not merely slower for `<=>`, it is not considered by the planner at all,
    # and the symptom is a silent sequential scan rather than an error.
    #
    # `m` and `ef_construction` are pgvector's defaults, stated rather than
    # inherited: they are build-time parameters, so changing them later means
    # rebuilding the index, and a reader comparing the served recall against the
    # index that produced it needs to see the numbers without consulting a
    # version's defaults. Query-time recall is `hnsw.ef_search`, which is a
    # session setting and deliberately not fixed here -- data-model.md records
    # 100 for the retrieval design's 50 candidates per arm.
    #
    # This index is what makes TR-013's *approximate* path exist. The exact path
    # needs nothing: it is the same column on the same table, reached whenever
    # the planner costs a scan lower than this index or is told not to use it.
    op.execute(
        """
        CREATE INDEX ix_chunk__embedding_hnsw ON chunk
        USING hnsw (embedding vector_cosine_ops)
        WITH (m = 16, ef_construction = 64)
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
