"""extraction

Revision ID: 0006
Revises: 0005
Create Date: 2026-07-26 08:58:11.622401

Three tables and one view in a single revision, deliberately. `data-model.md`
§Migration Prefixes assigns `extracted_value`, `extracted_value_contributing_chunk`,
`extraction_failure`, and `v_extracted_value_provenance` all to `0006` and states
it must not be split: the contributor table's `fk_evcc__value_count` references a
unique key on `extracted_value`, and the view reads both tables, so any split
leaves an intermediate head at which TR-018's provenance is unreadable and the
contributor table has no parent to point at. A forward-only chain (TR-002) cannot
repair such a state; it can only add to it.

TR-015 -- the citation is *structural*, not validated. `source_chunk_id`,
`cited_page`, and `confidence` are all NOT NULL, so an unattributable value is
unrepresentable rather than merely detectable. Principle I: "an unattributable
number is a defect, not a rough edge" -- the storage boundary is where that is
enforced, because every other layer can be bypassed.

TR-017 -- the cited page cannot disagree with its source chunk's page.
`fk_extracted_value__chunk_page` references `uq_chunk__chunk_page (chunk_id,
page_number)`, created in `0004` for exactly this purpose. **A composite foreign
key, not a trigger**, and the difference is not stylistic:

1. A trigger is a `BEFORE INSERT OR UPDATE` function that can be disabled
   (`ALTER TABLE ... DISABLE TRIGGER`), is skipped by `COPY ... FREEZE` paths a
   bulk loader may take, and has no referential action -- so a chunk's page
   changing underneath a citation is invisible to it.
2. A foreign key is checked by the same machinery that checks every other
   reference, participates in `ON UPDATE CASCADE` so a legitimate page
   correction propagates rather than silently invalidating the citation, and is
   readable from `pg_constraint` by the constraint audit (T049).
3. A citation whose page differs from its source chunk's page then has *no
   referent at all*, rather than a referent plus a rule saying it is wrong.

TR-045 -- the value is canonical text plus an *optional* typed numeric, and
"the typed numeric is populated exactly for numeric fields" is reduced to a
single-row `CHECK`. That reduction is the whole reason `fk_extracted_value__field`
is composite: it carries `field_vocabulary.value_kind` into the row, so
`ck_extracted_value__numeric_iff_number_kind` needs no cross-table lookup. A
`CHECK` cannot query another table, and the alternative -- a trigger, or an
`IMMUTABLE` function lying about immutability to read the vocabulary -- would be
either bypassable or wrong after a vocabulary correction.

TR-045, SC-023 -- **no foreign key to `purchase_order_line`, or to any other
target record.** The value's only outbound references are its source chunk and
its field name. E009 owns that join through `resolved_entity_member`, which is
the single sanctioned join surface; a direct FK here would make an identity merge
representable in two places, and Principle III's bias toward refusal depends on
there being exactly one.

TR-082 -- agent identity is recorded at *ingestion-run* granularity by E006, not
per extracted value, so there is no agent column here by design and its absence
is not an omission. `extracted_at` is the only per-row temporal fact.

TR-059, TR-060 -- the anchor `(source_chunk_id, cited_page)` **is contributor
1**, and `extracted_value_contributing_chunk` holds contributors 2..N only. Two
things follow. TR-015's non-nullable citation stays meaningful for multi-source
values (the anchor is a column, so it cannot be absent), and "is the anchor also
a row in the contributor table?" has one answer rather than a convention. Ordinal
1 *denotes the anchor* and carries no precedence meaning: ordinals are identity
within the set, which is why `v_extracted_value_provenance` unions without an
`ORDER BY` (TR-060).

TR-019, TR-061 -- a failed extraction is representable *only* as an
`extraction_failure` row. The two halves: a value row with nothing in it is
structurally impossible (`value_text` NOT NULL and non-blank, citation and
confidence NOT NULL), and a value with no identifiable source page cannot be
stored as a value at all -- it becomes a failure with `outcome =
'missing_citation'`, which is why that member of the outcome set exists.
"""

from collections.abc import Sequence

from alembic import op

# Revision identifiers, used by Alembic.
#
# TR-004: `revision` doubles as the four-digit filename prefix -- 0001-0099 is
# this epic's reserved block, 0100-0199 is E004's. Ordering is `down_revision`
# and only `down_revision`; the numbers are never compared to decide what runs
# next, so a gap or an out-of-order id is a naming defect, not a broken chain.
revision: str = "0006"
down_revision: str | Sequence[str] | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the three extraction tables and the provenance view.

    TR-003: re-application is a no-op by virtue of Alembic's `alembic_version`
    bookkeeping. Do not add a "have I already run?" guard here.

    Every constraint is named, following `pk_<table>`, `uq_<table>__<purpose>`,
    `ck_<table>__<rule>`, `fk_<table>__<target>`, and `ix_<table>__<purpose>`.
    Two mechanical reasons, not stylistic ones: a server-generated name cannot be
    relied on by a later forward migration's `ALTER TABLE ... DROP CONSTRAINT`,
    and a test asserting *which* rule rejected a row matches on the constraint
    name -- never on message text, which is locale- and version-dependent. The
    contributor table's constraints use the `evcc` abbreviation `data-model.md`
    declares, because `ck_extracted_value_contributing_chunk__...` exceeds
    PostgreSQL's 63-byte identifier limit and would be silently truncated -- two
    such names could then collide, and a test matching the untruncated name would
    never match.

    TR-039: every `CHECK` constraining a single column's value domain sits on a
    `NOT NULL` column, so none can be satisfied vacuously by a null. That is why
    none of them needs the `coalesce` wrapper `0003`'s layer-conditional checks
    require: a `CHECK` rejects only on *false*, and any comparison against NULL
    is NULL, which a check *accepts*. `value_number` is the one nullable column
    carrying a check, and it is a biconditional against the NOT NULL `value_kind`
    -- `(value_kind = 'number') = (value_number IS NOT NULL)` is never NULL,
    because `IS NOT NULL` is a definite boolean by construction. Verified by
    inserting the violating rows, not by reading the expressions.
    """
    # --- extracted_value (T021) --------------------------------------------
    op.execute(
        """
        CREATE TABLE extracted_value (
            extracted_value_id uuid NOT NULL,

            -- TR-015: the citation, NOT NULL both halves. An extracted value
            -- that cannot name its source page is not a weaker value -- it is
            -- not a value, and TR-061 routes it to `extraction_failure`.
            source_chunk_id uuid NOT NULL,
            cited_page integer NOT NULL,

            field_name text NOT NULL,

            -- TR-045: carried in from `field_vocabulary` by the composite
            -- foreign key below, so the numeric-column rule can be a single-row
            -- CHECK. It is denormalized on purpose and cannot drift: the FK is
            -- what keeps it equal to the vocabulary's own value.
            value_kind text NOT NULL,

            -- TR-045: canonical text on every row, whatever the kind. `date`
            -- terms store ISO-8601 here and leave `value_number` NULL; there is
            -- no third typed column.
            value_text text NOT NULL,

            -- Nullable by design -- populated exactly on numeric-kind fields,
            -- which `ck_extracted_value__numeric_iff_number_kind` makes
            -- biconditional rather than merely permitted.
            value_number numeric,

            -- TR-016, TR-054, TR-081: `double precision`, a self-reported score
            -- and not a calibrated probability. NOT NULL per TR-015.
            confidence double precision NOT NULL,

            provenance_kind text NOT NULL,
            source_chunk_count smallint NOT NULL,

            -- TR-082: the only per-row temporal fact. Agent identity lives at
            -- ingestion-run granularity in E006, so no agent column appears
            -- here; see the module docstring.
            extracted_at timestamptz NOT NULL DEFAULT now(),

            CONSTRAINT pk_extracted_value PRIMARY KEY (extracted_value_id),

            -- Pages are one-based. Absence is refused by the column's NOT NULL
            -- rather than by this check, which is what keeps the check from
            -- being satisfiable by a null. Redundant against
            -- `fk_extracted_value__chunk_page` for any page a chunk actually
            -- has, and kept because it states the domain independently of
            -- whichever rows happen to exist in `chunk`.
            CONSTRAINT ck_extracted_value__cited_page_positive
                CHECK (cited_page >= 1),

            -- The trim set is spelled out because single-argument `btrim` strips
            -- *spaces only*: a value of one tab or newline would otherwise
            -- satisfy a bare `btrim(value_text) <> ''` while carrying no value
            -- at all -- which is half of TR-019's "no partial value row"
            -- (data-model.md §extraction_failure).
            --
            -- No `coalesce` wrapper, and none is needed: `value_text` is NOT
            -- NULL, so the comparison is never NULL and a blank value is a
            -- definite false rather than the vacuous pass a check on a nullable
            -- column would give.
            --
            -- **Deviation from data-model.md, deliberate (TR-083).** That
            -- artifact spells the set `E' \\t\\n\\r\\f\\v'`, which PostgreSQL
            -- does not read as whitespace: its escape-string syntax has no
            -- `\\v`, and an unrecognized escape drops the backslash and keeps
            -- the character, so `E'\\v'` is the *letter* `v`. Written as
            -- declared, this check would let a vertical-tab-only value through
            -- (11 missing from the set) and reject a legitimate value of `vvv`
            -- (118 wrongly in it) -- one typo producing both a hole and a false
            -- rejection. `\\u000B` is the character the artifact means; octal
            -- `\\013` is equivalent. `0004` records the same deviation for
            -- `ck_chunk__body_text_present`, and every presence check
            -- data-model.md declares is affected.
            CONSTRAINT ck_extracted_value__value_text_present
                CHECK (btrim(value_text, E' \\t\\n\\r\\f\\u000B') <> ''),

            -- TR-045, reduced to one row. The biconditional is what makes this
            -- exact rather than permissive: a numeric-kind field *must* carry
            -- the typed numeric, and a text- or date-kind field must *not*.
            --
            -- This is the one check on a nullable column in this revision, and
            -- it cannot pass vacuously: `value_number IS NOT NULL` is a definite
            -- boolean whatever `value_number` holds, and `value_kind` is NOT
            -- NULL, so the equality is never NULL.
            CONSTRAINT ck_extracted_value__numeric_iff_number_kind
                CHECK ((value_kind = 'number') = (value_number IS NOT NULL)),

            -- TR-016, TR-054: inclusive at *both* ends. `>= 0.0` and `<= 1.0`,
            -- not `>` / `<` -- a genuinely certain extraction reports 1.0 and a
            -- genuinely worthless one reports 0.0, and excluding either end
            -- would force a writer to fabricate an epsilon. Paired with the
            -- column's NOT NULL per TR-039, without which a missing confidence
            -- would pass this check by evaluating to NULL.
            CONSTRAINT ck_extracted_value__confidence_range
                CHECK (confidence >= 0.0 AND confidence <= 1.0),

            -- The closed set. Single- versus multi-source provenance is a fact
            -- about the row, so it is stated rather than inferred at read time.
            CONSTRAINT ck_extracted_value__provenance_kind
                CHECK (provenance_kind IN ('single_chunk', 'multi_chunk')),

            -- The anchor is contributor 1, so the count is at least 1 on every
            -- row and there is no "zero sources" state to represent.
            CONSTRAINT ck_extracted_value__source_count_positive
                CHECK (source_chunk_count >= 1),

            -- The two provenance facts cannot disagree. A biconditional, so
            -- `multi_chunk` with a count of 1 is refused as firmly as
            -- `single_chunk` with a count of 3. Both operands are NOT NULL.
            CONSTRAINT ck_extracted_value__provenance_agrees_with_count
                CHECK ((source_chunk_count > 1) = (provenance_kind = 'multi_chunk')),

            -- TR-017, OBJ3 VC5, SC-008. References `chunk`'s composite unique
            -- key `uq_chunk__chunk_page`, created in `0004` for this. The
            -- referencing column order is the referenced key's order and is
            -- load-bearing -- a composite foreign key matches its parent key
            -- positionally, so `(cited_page, source_chunk_id)` would not
            -- compile against it.
            --
            -- `MATCH FULL` is declared for intent even though both columns are
            -- NOT NULL and it is therefore equivalent to the default here: were
            -- one ever relaxed to nullable, `MATCH SIMPLE` would skip the check
            -- entirely on a partially-null pair -- which is precisely the
            -- unattributable row TR-015 exists to forbid.
            --
            -- RESTRICT on delete: dropping a chunk must not silently orphan the
            -- citations that resolve through it. CASCADE on update so a
            -- legitimate page correction on the parent propagates rather than
            -- deadlocking against its own children.
            CONSTRAINT fk_extracted_value__chunk_page
                FOREIGN KEY (source_chunk_id, cited_page)
                REFERENCES chunk (chunk_id, page_number)
                MATCH FULL
                ON DELETE RESTRICT
                ON UPDATE CASCADE,

            -- TR-044 membership plus the kind-carrying trick described in the
            -- module docstring. References `uq_field_vocabulary__name_kind`,
            -- created in `0005` in this exact column order.
            --
            -- RESTRICT on delete: vocabulary terms are *retired*
            -- (`field_vocabulary.retired_at`), never deleted, and this is what
            -- makes that a rule rather than a habit (TR-079).
            CONSTRAINT fk_extracted_value__field
                FOREIGN KEY (field_name, value_kind)
                REFERENCES field_vocabulary (field_name, value_kind)
                MATCH FULL
                ON DELETE RESTRICT
                ON UPDATE CASCADE,

            -- The foreign-key target contributor rows point at. Redundant
            -- against `pk_extracted_value` on purpose: a composite foreign key
            -- must reference a unique key carrying *both* columns, and the
            -- primary key alone cannot carry the declared count. This is what
            -- makes "a contributor ordinal cannot exceed the count its value
            -- declares" a per-row check on the child rather than a cross-table
            -- lookup -- see `fk_evcc__value_count`.
            CONSTRAINT uq_extracted_value__id_source_count
                UNIQUE (extracted_value_id, source_chunk_count)

            -- TR-045, SC-023: there is deliberately no foreign key to
            -- `purchase_order_line` or any other target record here, and its
            -- absence is asserted from `pg_constraint` by the extraction tests.
            -- `resolved_entity_member` (E009, migration `0010`) is the one
            -- sanctioned join surface.
        )
        """
    )

    # Plain b-tree indexes, no constraint semantics. Separate statements because
    # `CREATE TABLE` admits only constraints inline.
    #
    # The chunk index is not redundant against `fk_extracted_value__chunk_page`:
    # PostgreSQL creates no index on the *referencing* side of a foreign key, so
    # without this every delete of a chunk would sequentially scan this table to
    # enforce RESTRICT.
    op.execute("CREATE INDEX ix_extracted_value__chunk ON extracted_value (source_chunk_id)")
    op.execute("CREATE INDEX ix_extracted_value__field ON extracted_value (field_name)")

    # --- extracted_value_contributing_chunk (T022) --------------------------
    #
    # Contributors 2..N. The anchor on `extracted_value` is contributor 1 and
    # never appears here; `ck_evcc__ordinal_min` is what enforces that, and it is
    # why the ordinal floor is 2 rather than 1.
    op.execute(
        """
        CREATE TABLE extracted_value_contributing_chunk (
            extracted_value_id uuid NOT NULL,
            contributor_ordinal smallint NOT NULL,

            -- Denormalized from the parent and held equal to it by
            -- `fk_evcc__value_count`. It exists so the ordinal bound below can
            -- be a single-row CHECK: a CHECK cannot read another table, so
            -- without this column "the ordinal is within the declared count"
            -- would need a trigger.
            source_chunk_count smallint NOT NULL,

            -- TR-058: one page per chunk, so a value spanning three pages is
            -- three contributing chunks, not one chunk with three pages.
            chunk_id uuid NOT NULL,
            page_number integer NOT NULL,

            -- TR-059: the ordinal is identity within the contributor set, so it
            -- is part of the key. A value cannot have two contributor 2s.
            CONSTRAINT pk_extracted_value_contributing_chunk
                PRIMARY KEY (extracted_value_id, contributor_ordinal),

            -- TR-059, TR-060: ordinal 1 *denotes the anchor* and the anchor is
            -- a pair of columns on `extracted_value`, so 1 is not available
            -- here. Writing the anchor a second time as a contributor row would
            -- double-count it against `source_chunk_count` and make the
            -- provenance view return it twice.
            CONSTRAINT ck_evcc__ordinal_min
                CHECK (contributor_ordinal >= 2),

            -- The contributor rows cannot exceed the count the value declares.
            -- Per-row and exact, because `source_chunk_count` is pinned to the
            -- parent's by `fk_evcc__value_count` -- the two constraints only
            -- work as a pair, and neither alone is worth anything: this check
            -- alone would compare against a number the child invented, and the
            -- FK alone would allow ordinal 9 under a declared count of 3.
            CONSTRAINT ck_evcc__ordinal_within_declared_count
                CHECK (contributor_ordinal <= source_chunk_count),

            -- Pages are one-based; absence is the column's NOT NULL.
            CONSTRAINT ck_evcc__page_positive
                CHECK (page_number >= 1),

            -- References `uq_extracted_value__id_source_count`. CASCADE on
            -- delete because a contributor row has no meaning without its value
            -- -- unlike the chunk references, which RESTRICT. CASCADE on update
            -- so a corrected count on the parent carries down rather than
            -- leaving the child pinned to a stale one.
            CONSTRAINT fk_evcc__value_count
                FOREIGN KEY (extracted_value_id, source_chunk_count)
                REFERENCES extracted_value (extracted_value_id, source_chunk_count)
                MATCH FULL
                ON DELETE CASCADE
                ON UPDATE CASCADE,

            -- TR-017 again, for the non-anchor contributors: the same
            -- `uq_chunk__chunk_page` target, so a contributor citing a page its
            -- chunk does not have has no referent either.
            CONSTRAINT fk_evcc__chunk_page
                FOREIGN KEY (chunk_id, page_number)
                REFERENCES chunk (chunk_id, page_number)
                MATCH FULL
                ON DELETE RESTRICT
                ON UPDATE CASCADE,

            -- One contributor row per chunk. Two ordinals naming the same chunk
            -- would inflate the recovered contributor count while the citation
            -- set stayed the same size.
            CONSTRAINT uq_evcc__value_chunk
                UNIQUE (extracted_value_id, chunk_id)
        )
        """
    )

    # TR-018, OBJ3 VC3: the full citation set of any value in one read, anchor
    # included. `UNION ALL`, not `UNION` -- `UNION` would deduplicate, and the
    # only way two rows could collide is a defect (`uq_evcc__value_chunk` and
    # `ck_evcc__ordinal_min` already make the anchor unrepeatable), so `UNION`
    # would hide it and pay for a sort to do so.
    #
    # TR-060: no `ORDER BY`. Ordinals are identity within the set and carry no
    # precedence meaning, so ordering the view would invite a reader to treat
    # ordinal 2 as "more primary" than ordinal 3. A consumer that wants an order
    # states one.
    #
    # `1::smallint` matches `contributor_ordinal`'s type exactly; without the
    # cast the union's column resolves to `integer` and the view's shape would
    # differ from the table's for no reason.
    op.execute(
        """
        CREATE VIEW v_extracted_value_provenance AS
            SELECT
                extracted_value_id,
                1::smallint AS contributor_ordinal,
                source_chunk_id AS chunk_id,
                cited_page AS page_number
            FROM extracted_value
            UNION ALL
            SELECT
                extracted_value_id,
                contributor_ordinal,
                chunk_id,
                page_number
            FROM extracted_value_contributing_chunk
        """
    )

    # --- extraction_failure (T023) -----------------------------------------
    #
    # TR-019: the only representation of a failed extraction. Note what this
    # table does *not* have -- no confidence and no value column of any kind, so
    # it cannot be used to smuggle in a half-extracted value, and no
    # `extracted_value_id`, because a failure is not an annotation on a value.
    op.execute(
        """
        CREATE TABLE extraction_failure (
            extraction_failure_id uuid NOT NULL,

            -- The attempted source. Present and NOT NULL even for
            -- `missing_citation`: what is missing in that case is a citation for
            -- the *value*, not knowledge of which chunk was read.
            source_chunk_id uuid NOT NULL,
            attempted_page integer NOT NULL,

            -- The attempted field, from the same vocabulary a successful value
            -- draws on -- so "which fields fail most" is answerable by joining
            -- the two tables against one term list.
            field_name text NOT NULL,

            outcome text NOT NULL,
            repair_attempt_count smallint NOT NULL,
            detail text NOT NULL,
            failed_at timestamptz NOT NULL DEFAULT now(),

            CONSTRAINT pk_extraction_failure PRIMARY KEY (extraction_failure_id),

            CONSTRAINT ck_extraction_failure__page_positive
                CHECK (attempted_page >= 1),

            -- TR-061: `missing_citation` is the member that makes TR-015
            -- coherent. A value whose source page cannot be identified is
            -- unstorable as a value -- the citation columns over on
            -- `extracted_value` are NOT NULL -- so it has to land somewhere, and
            -- this is that somewhere. Without this outcome the only options
            -- would be discarding the observation or relaxing TR-015.
            --
            -- The other six: nothing found, found but unparseable, parsed but
            -- not coercible to the declared kind, coerced but violating the
            -- schema, below the confidence threshold (Principle III -- recorded
            -- absent rather than stored wrong), and repair budget exhausted.
            CONSTRAINT ck_extraction_failure__outcome
                CHECK (outcome IN (
                    'no_value_found',
                    'unparseable_value',
                    'type_coercion_failed',
                    'schema_violation',
                    'missing_citation',
                    'confidence_below_threshold',
                    'repair_budget_exhausted'
                )),

            -- Zero is the ordinary case: a failure recorded without any repair
            -- attempt. The repair *budget* is E006's policy and deliberately not
            -- an upper bound here -- a schema constant would have to change by
            -- migration every time the policy was tuned, and a row recorded
            -- under the old budget would then violate the new one.
            CONSTRAINT ck_extraction_failure__repair_count_non_negative
                CHECK (repair_attempt_count >= 0),

            -- A failure record whose detail is blank explains nothing, which
            -- defeats the point of recording it. Same widened trim set and same
            -- reasoning as `ck_extracted_value__value_text_present` above,
            -- including the `\\u000B` deviation from data-model.md's `\\v`.
            CONSTRAINT ck_extraction_failure__detail_present
                CHECK (btrim(detail, E' \\t\\n\\r\\f\\u000B') <> ''),

            -- The same `uq_chunk__chunk_page` target the value tables use, so a
            -- failure cannot name a page its chunk does not have either. The
            -- attempt is as traceable as the success.
            --
            -- **Recorded reading of data-model.md (TR-083).** That artifact
            -- declares this FK in abbreviated form -- "belongs_to: `chunk` via
            -- composite FK `(source_chunk_id, attempted_page)`" -- and states
            -- only `ON DELETE RESTRICT` for it, in §Referential Actions. The
            -- `MATCH FULL` and `ON UPDATE CASCADE` here are taken from the
            -- fully-spelled sibling FKs against the same parent key
            -- (`fk_extracted_value__chunk_page`, `fk_evcc__chunk_page`), because
            -- three foreign keys onto one unique key behaving differently under
            -- a page correction is a difference nothing in the artifact asks
            -- for, and the odd one out would block the very update the other two
            -- are configured to propagate.
            CONSTRAINT fk_extraction_failure__chunk_page
                FOREIGN KEY (source_chunk_id, attempted_page)
                REFERENCES chunk (chunk_id, page_number)
                MATCH FULL
                ON DELETE RESTRICT
                ON UPDATE CASCADE,

            -- Single-column here, unlike `fk_extracted_value__field`: a failure
            -- stores no typed value, so it has no numeric-column rule to reduce
            -- and no reason to carry `value_kind`. References
            -- `pk_field_vocabulary`. RESTRICT on delete per §Referential
            -- Actions -- a term with failures against it is retired, not
            -- deleted.
            --
            -- **Recorded reading of data-model.md (TR-083).** Its `ON UPDATE
            -- CASCADE` rule is phrased over "the composite FKs whose parent key
            -- includes a mutable column", and this one is single-column, so read
            -- literally it would default to `NO ACTION`. Declared CASCADE
            -- anyway, because the rule's stated purpose is that "a legitimate
            -- parent-side correction propagates rather than deadlocking":
            -- `field_name` is the mutable column in question, and leaving this
            -- FK at `NO ACTION` would make a term rename fail here while
            -- `fk_extracted_value__field` cascaded -- so the cascade the
            -- artifact does ask for would be unusable in practice. The
            -- "composite" wording describes which FKs happened to need saying,
            -- not a property the behaviour depends on.
            CONSTRAINT fk_extraction_failure__field
                FOREIGN KEY (field_name)
                REFERENCES field_vocabulary (field_name)
                ON DELETE RESTRICT
                ON UPDATE CASCADE
        )
        """
    )

    # "Which fields failed on this chunk" is the diagnostic query, and it also
    # serves the RESTRICT enforcement on chunk deletes by its leading column.
    op.execute(
        """
        CREATE INDEX ix_extraction_failure__chunk_field
        ON extraction_failure (source_chunk_id, field_name)
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
