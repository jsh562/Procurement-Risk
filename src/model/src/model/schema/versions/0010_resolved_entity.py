"""resolved entity

Revision ID: 0010
Revises: 0009
Create Date: 2026-07-26 10:15:10.655911

Two tables: `resolved_entity` and `resolved_entity_member` (OBJ6, TR-034,
TR-035, TR-045). `data-model.md` §Migration Sequence puts them last in the chain
by design -- P2 is droppable, and every P1 objective has completed by `0009`, so
this revision can be removed from the chain without taking an objective with it.

**TR-045 completes here, and it completes by what this revision *is* rather than
by anything it forbids.** `extracted_value` carries no foreign key to
`purchase_order_line` and never will; `0006`'s
`test_no_foreign_key_from_extracted_value_targets_a_purchase_order_line_relation`
asserts that continuously. What was missing until now was the *sanctioned*
join -- and it is this table. A value and a line are related only by both being
members of the same `resolved_entity`, which means every such link is an
explicit, dated, reviewable row that names the identity it was merged under. A
direct FK would have made the same claim invisibly and irreversibly, on the row
itself, with nothing recording who decided the two were the same material.
E009 populates this table; this epic only makes it storable.

**The XOR, and why it is two constraints rather than one.** A member points at
an `extracted_value` **or** a `purchase_order_line`, never both and never
neither. `ck_rem__exactly_one_target` carries the cardinality --
`num_nonnulls(extracted_value_id, po_line_id) = 1`, which is *false* (not NULL)
for both the two-target and the zero-target row, so the `CHECK` refuses both.
`ck_rem__kind_agrees` separately ties the discriminator to whichever column is
populated, so `member_kind` cannot describe the row wrongly. Neither implies the
other: the first admits a row whose kind says `purchase_order_line` while the
`extracted_value_id` is the populated one, and the second admits a row with both
columns null and a kind of `purchase_order_line`.

**"A record cannot belong to two entities" is a plain `UNIQUE`, and its
correctness is entirely a fact about NULL handling (TR-035, OBJ6 VC2).**
`uq_rem__extracted_value UNIQUE (extracted_value_id)` and `uq_rem__po_line
UNIQUE (po_line_id)` are declared without `NULLS NOT DISTINCT`, so PostgreSQL's
default `NULLS DISTINCT` applies: every one of the many line members holds a
NULL `extracted_value_id`, and NULLs never collide with one another, so those
rows coexist freely -- while any two rows naming the *same* extracted value
collide immediately. This is the combination that makes the XOR and the
uniqueness rule compose: without `NULLS DISTINCT` the second line member ever
written would be rejected as a duplicate NULL, and the table would hold at most
one member of each kind in the entire database. Written as
`UNIQUE NULLS NOT DISTINCT` -- one keyword away, and legal PostgreSQL 15+ syntax
-- this schema would be silently unusable. `test_resolved_entity.py` asserts
`pg_index.indnullsnotdistinct` is false on both indexes rather than inferring it
from the many-line-members case passing.

A partial unique index (`... WHERE extracted_value_id IS NOT NULL`) would reach
the same end state and is *not* used: it is a second mechanism for a rule the
default already gives, it does not appear in `information_schema` as a table
constraint, and `data-model.md` declares plain `UNIQUE` (TR-083).

**Grants are explicit, and must be (TR-084's consequence).** `0009` deliberately
declined `ALTER DEFAULT PRIVILEGES`, on the ground that "the application role can
write it" is the wrong automatic property for a schema holding append-only
tables -- a future append-only table would silently acquire `UPDATE` and
`DELETE`. The cost of failing closed is paid here: without the explicit `GRANT`
below, `procurement_app` could not so much as `SELECT` either table. Both are
ordinary mutable tables -- an entity is a *revisable* judgement about identity,
unlike a citation -- so all four verbs are granted and nothing is taken back.

**Recorded deviation from `data-model.md` (TR-083).**
`ck_resolved_entity__agreement_non_empty` is declared
`cardinality(agreement_attribute_names) >= 1` and is written here with two
further conjuncts. The declared form is right about the empty array --
`cardinality('{}')` is `0`, where `array_length('{}', 1)` would have been NULL
and the `CHECK` would have *accepted* an entity agreeing on nothing (the trap
`0008` records against `ck_line_posterior__draws_length`). It is wrong one level
down: `cardinality(ARRAY[NULL]::text[])` is `1`, so an array holding a single
NULL passes, as does `ARRAY['']`. Both are an entity declaring one agreement
attribute that names nothing -- the same defect the empty array is refused for,
one subscript deeper. Verified by inserting each row against the declared form.
The deviation strengthens a check `data-model.md` already declares and adds no
constraint name, so the object inventory T052 audits is unchanged.

What the strengthening does *not* close is a blank element sitting *alongside* a
real one (`ARRAY['manufacturer', '']`): refusing that needs a per-element scan,
which a `CHECK` cannot do without a helper function, and a new function would be
an object absent from `data-model.md`. Such an element names no vocabulary term,
which is exactly the runtime consequence gap **G-6** already discloses.

**G-6 is not closed here, deliberately.** `agreement_attribute_names` elements
are `field_vocabulary` terms, and PostgreSQL has no array-element foreign key.
The gap is disclosed with its reversal trigger and its production-scale
alternative (a `resolved_entity_agreement_attribute` child table with a real FK,
replacing the array), and covered by a test that asserts what the schema
actually does -- accepts the row -- rather than a guarantee it does not make.
"""

from collections.abc import Sequence

from alembic import op

# Revision identifiers, used by Alembic.
#
# TR-004: `revision` doubles as the four-digit filename prefix -- 0001-0099 is
# this epic's reserved block, 0100-0199 is E004's. Ordering is `down_revision`
# and only `down_revision`; the numbers are never compared to decide what runs
# next, so a gap or an out-of-order id is a naming defect, not a broken chain.
revision: str = "0010"
down_revision: str | Sequence[str] | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create `resolved_entity`, `resolved_entity_member`, and their grants.

    TR-003: re-application is a no-op by virtue of Alembic's `alembic_version`
    bookkeeping. Do not add a "have I already run?" guard here.

    Every constraint is named, following `pk_<table>`, `uq_<table>__<purpose>`,
    `fk_<table>__<purpose>`, `ck_<table>__<rule>`, and `ix_<table>__<purpose>`,
    with `rem` as `data-model.md`'s declared short form for
    `resolved_entity_member` on the constraints it names that way. A
    server-generated name cannot be relied on by a later forward migration's
    `ALTER TABLE ... DROP CONSTRAINT`, and a test asserting *which* rule rejected
    a row matches on the constraint name -- never on message text, which is
    locale- and version-dependent.

    TR-039: every `CHECK` here that constrains a single column's value domain
    sits on a `NOT NULL` column. The two checks that mention nullable columns --
    `ck_rem__exactly_one_target` and `ck_rem__kind_agrees` -- are null-safe by
    construction rather than by `coalesce`: `num_nonnulls` counts nulls and never
    returns one, and `IS NOT NULL` is a definite boolean whatever the column
    holds. Both are registered in §Nullable-Column Checks. Every check was
    verified by inserting the violating row, never by reading the expression.
    """
    # --- resolved_entity (T045) ---------------------------------------------
    #
    # The confirmed cross-document identity: one row per material that E009 has
    # decided is the same thing in a specification, a submittal, and a purchase
    # order. Principle III governs what gets written here at all -- an uncertain
    # pair is withheld and routed to review rather than merged -- but that is
    # E009's rule to apply. What the schema carries is that a merge, once made,
    # is normalized, unique, and says what it agreed on.
    op.execute(
        """
        CREATE TABLE resolved_entity (
            resolved_entity_id uuid NOT NULL,

            -- Normalized, not raw. The raw manufacturer strings live on the
            -- rows this entity is resolved *from* -- `extracted_value.value_text`
            -- and `purchase_order_line.manufacturer` -- and are not restated
            -- here, so there is no second copy to drift.
            normalized_manufacturer text NOT NULL,
            normalized_part_number text NOT NULL,

            -- TR-034: the attributes the members were found to agree on, as a
            -- `text[]` of `field_vocabulary` terms. An array rather than a child
            -- table because the set is read whole and never joined to, and
            -- because a child table would let an entity exist with no agreement
            -- recorded at all. The cost is G-6: no element-level foreign key.
            agreement_attribute_names text[] NOT NULL,

            created_at timestamptz NOT NULL DEFAULT now(),

            CONSTRAINT pk_resolved_entity PRIMARY KEY (resolved_entity_id),

            -- Normalization is asserted, not assumed. Two conjuncts, and they
            -- are different claims: `= lower(...)` says the value has been
            -- case-folded, and the `btrim` says it is not blank.
            --
            -- `lower()` rather than a collation-dependent normalization -- a
            -- `CHECK` must stay true across OS and ICU upgrades, and existing
            -- rows are never rechecked, so a rule whose truth depends on the
            -- collation library would quietly become a lie after a base-image
            -- bump rather than failing loudly.
            --
            -- The trim set is spelled out because single-argument `btrim` strips
            -- *spaces only*: a manufacturer of one tab would otherwise satisfy a
            -- bare `btrim(normalized_manufacturer) <> ''` while naming nothing.
            -- No `coalesce` wrapper, and none is needed -- the column is NOT
            -- NULL, so both conjuncts are definite booleans.
            --
            -- `\\u000B` and never `\\v`, matching what `data-model.md` declares
            -- for this column and what revisions `0003`-`0008` all carry.
            -- PostgreSQL's escape-string syntax has no `\\v`: an unrecognized
            -- escape drops the backslash and keeps the character, so `E'\\v'` is
            -- the *letter* `v`. That one typo would both open a hole (U+000B
            -- absent from the set, so a vertical-tab-only manufacturer is
            -- accepted) and cause a false rejection (a legitimate part number of
            -- `vvv` trimmed to nothing). Both halves are tested.
            CONSTRAINT ck_resolved_entity__manufacturer_normalized
                CHECK (
                    normalized_manufacturer = lower(normalized_manufacturer)
                    AND btrim(normalized_manufacturer, E' \\t\\n\\r\\f\\u000B') <> ''
                ),

            CONSTRAINT ck_resolved_entity__part_number_normalized
                CHECK (
                    normalized_part_number = lower(normalized_part_number)
                    AND btrim(normalized_part_number, E' \\t\\n\\r\\f\\u000B') <> ''
                ),

            -- An entity that agreed on nothing is not a resolved entity.
            --
            -- `cardinality`, never `array_length(..., 1)`: the latter is NULL on
            -- an empty array, and a `CHECK` rejects only on *false*, so the
            -- declared rule would have accepted `'{}'` -- the exact row it
            -- exists to refuse. `cardinality('{}')` is `0`.
            --
            -- The two further conjuncts are the recorded deviation; see the
            -- module docstring. `array_position(arr, NULL)` is the documented
            -- way to ask whether an array contains a NULL element and returns
            -- the first such subscript, so `IS NULL` here means "no NULL
            -- element". `array_to_string` skips NULLs and concatenates the rest,
            -- so a blank result means every element was NULL or blank.
            CONSTRAINT ck_resolved_entity__agreement_non_empty
                CHECK (
                    cardinality(agreement_attribute_names) >= 1
                    AND array_position(agreement_attribute_names, NULL) IS NULL
                    AND btrim(
                        array_to_string(agreement_attribute_names, ''),
                        E' \\t\\n\\r\\f\\u000B'
                    ) <> ''
                ),

            -- The identity itself. Two entities for one normalized
            -- manufacturer-and-part pair would mean the merge had been made
            -- twice and the members split across both, which is the silent
            -- corruption Principle III names -- invisible, and propagating.
            CONSTRAINT uq_resolved_entity__normalized_identity
                UNIQUE (normalized_manufacturer, normalized_part_number)
        )
        """
    )

    # --- resolved_entity_member (T046) --------------------------------------
    #
    # TR-035, TR-045. The only sanctioned join between an extracted value and a
    # purchase-order line, and the reason `extracted_value` needs no foreign key
    # to `purchase_order_line`.
    op.execute(
        """
        CREATE TABLE resolved_entity_member (
            member_id uuid NOT NULL,
            resolved_entity_id uuid NOT NULL,

            -- The discriminator. Redundant with "which column is populated" and
            -- kept anyway, tied to it by `ck_rem__kind_agrees`: a reader
            -- filtering members by kind should not have to write
            -- `WHERE extracted_value_id IS NOT NULL` and be silently wrong the
            -- day a third member kind appears.
            member_kind text NOT NULL,

            -- Exactly one of these is populated on every row; see
            -- `ck_rem__exactly_one_target`. Nullable is what makes the XOR
            -- expressible in one table at all, and it is also what makes the two
            -- UNIQUE constraints below depend on NULLS DISTINCT.
            extracted_value_id uuid,
            po_line_id uuid,

            added_at timestamptz NOT NULL DEFAULT now(),

            CONSTRAINT pk_resolved_entity_member PRIMARY KEY (member_id),

            -- A closed set, duplicated from nothing: there is no lookup table of
            -- member kinds, because the set is fixed by the schema's own shape
            -- -- one member per nullable target column -- and adding a third
            -- kind means adding a column and a foreign key in the same
            -- migration that extends this list.
            CONSTRAINT ck_rem__member_kind
                CHECK (member_kind IN ('extracted_value', 'purchase_order_line')),

            -- The XOR, in the only form that refuses *both* failure modes.
            -- `num_nonnulls` returns 2 when both targets are set and 0 when
            -- neither is, and both are definite integers -- so the comparison is
            -- *false*, not NULL, and the `CHECK` refuses rather than accepts.
            -- This is a check on nullable columns that cannot pass vacuously,
            -- and it is registered as such in §Nullable-Column Checks.
            --
            -- The obvious alternative, `(extracted_value_id IS NULL) <>
            -- (po_line_id IS NULL)`, is equivalent today and stops being so the
            -- moment a third target column is added -- it would then accept a
            -- row with two of the three set. `num_nonnulls` extends by naming
            -- the new column.
            CONSTRAINT ck_rem__exactly_one_target
                CHECK (num_nonnulls(extracted_value_id, po_line_id) = 1),

            -- The discriminator cannot lie. A biconditional, so a row claiming
            -- `extracted_value` while carrying a `po_line_id` is refused as
            -- firmly as the reverse. `member_kind` is NOT NULL and `IS NOT NULL`
            -- is always a definite boolean, so this equality is never NULL.
            CONSTRAINT ck_rem__kind_agrees
                CHECK (
                    (member_kind = 'extracted_value') = (extracted_value_id IS NOT NULL)
                ),

            -- ON DELETE CASCADE, and it is the only cascade in this revision:
            -- membership has no meaning without the entity (§Referential
            -- Actions). Discarding a merge that turned out to be wrong is one
            -- statement, and it leaves no orphaned rows claiming an identity
            -- that no longer exists.
            CONSTRAINT fk_rem__entity
                FOREIGN KEY (resolved_entity_id)
                REFERENCES resolved_entity (resolved_entity_id)
                ON DELETE CASCADE,

            -- ON DELETE RESTRICT on both targets, unlike the entity edge.
            -- Deleting a cited value or a purchase-order line that a merge
            -- depends on must be an explicit, ordered operation: a cascade here
            -- would let a reload of one document silently shrink an entity's
            -- membership, and the entity would go on asserting an identity it
            -- could no longer evidence.
            CONSTRAINT fk_rem__extracted_value
                FOREIGN KEY (extracted_value_id)
                REFERENCES extracted_value (extracted_value_id)
                ON DELETE RESTRICT,

            CONSTRAINT fk_rem__po_line
                FOREIGN KEY (po_line_id)
                REFERENCES purchase_order_line (po_line_id)
                ON DELETE RESTRICT,

            -- TR-035, OBJ6 VC2: a record cannot belong to two entities.
            --
            -- Plain `UNIQUE`, which means `NULLS DISTINCT` -- see the module
            -- docstring for why that default is the whole mechanism and why
            -- `NULLS NOT DISTINCT` would make the table hold one member of each
            -- kind for the entire database.
            CONSTRAINT uq_rem__extracted_value UNIQUE (extracted_value_id),
            CONSTRAINT uq_rem__po_line UNIQUE (po_line_id)
        )
        """
    )

    # OBJ6 VC1 -- recovering every member of one entity is the read this table
    # exists to serve, and it is a `resolved_entity_id` lookup. The primary key
    # leads with `member_id` and cannot serve it, and PostgreSQL indexes neither
    # side of a foreign key automatically, so this also keeps `fk_rem__entity`'s
    # cascade from scanning the table on every entity delete.
    #
    # The other two foreign keys need no such index: `uq_rem__extracted_value`
    # and `uq_rem__po_line` are backed by unique indexes on exactly those
    # columns, which the RESTRICT checks use.
    op.execute("CREATE INDEX ix_rem__entity ON resolved_entity_member (resolved_entity_id)")

    # --- grants (T045, T046) ------------------------------------------------
    #
    # Explicit, because `0009` deliberately declined `ALTER DEFAULT PRIVILEGES`
    # so that a table added later fails closed rather than silently acquiring
    # `UPDATE` and `DELETE` for the application role. Without these two
    # statements `procurement_app` could not read either table, and the schema's
    # P2 half would be inaccessible to the role the application is intended to
    # connect as.
    #
    # All four verbs, and nothing revoked afterwards. These are not provenance
    # tables: a resolved entity is a revisable judgement about identity, and
    # E009 must be able to withdraw a merge it later finds unsupported --
    # Principle III's "withhold rather than merge" is worth nothing if a merge
    # already made cannot be taken back. `extracted_value` and
    # `extraction_failure` remain the only tables in this schema from which
    # `UPDATE` and `DELETE` are withheld (TR-084).
    #
    # Named tables rather than `ON ALL TABLES IN SCHEMA public`: this revision
    # grants on what this revision creates. `ON ALL TABLES` would silently
    # re-grant every table `0002`-`0008` created, undoing `0009`'s revoke on the
    # two provenance tables and on `alembic_version` -- a re-grant is not blocked
    # by an earlier revoke, because PostgreSQL records no negative grant.
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON resolved_entity TO procurement_app")
    op.execute("GRANT SELECT, INSERT, UPDATE, DELETE ON resolved_entity_member TO procurement_app")


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
