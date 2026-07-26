"""document

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-26 03:38:39.922137

TR-046: the corpus manifest key gets a table, so a page citation resolves to a
named source document rather than to a bare page number. TR-041 declares the key
format here -- lowercase kebab slug, 3 to 128 characters -- because E002's
manifest key space is not frozen yet; E002 and E006 adopt this format (TR-077,
gap G-9), and if they cannot, this epic amends rather than both drifting.

TR-057: a manifest key identifies one fixed *revision* of a source document. A
superseding revision is loaded as a new row under a distinct key, so every
citation already taken keeps resolving to the revision it was extracted from.
Nothing here updates a document in place.

TR-074: one row per source-and-project pair. `project_id` is NOT NULL on every
document, including a public reference standard, because SC-006 requires every
chunk to carry a project identifier and the chunk inherits it through the
composite foreign key. A standard referenced by three projects is three rows
under three manifest keys -- so there is deliberately **no** global uniqueness
constraint on anything but the key itself.

TR-078: a later change to the key space is a forward migration that updates
`document_id` in place; `fk_chunk__document` (revision 0004) carries
`ON UPDATE CASCADE`, and extracted-value citations reference the *chunk*, so
they are untouched and no loaded row is reloaded.

**Layer-conditional provenance (TR-075, TR-087)** is the substance of this
revision. `project-instructions.md` v1.2.0 states the Data Provenance rule per
layer rather than as one universal list, and this table enforces it at the
storage boundary:

    field group                                     REAL        SYNTHETIC
    source_ref, issuing_body, retrieval_date        required    rejected
    generator_id, generation_seed, generated_at,    rejected    required
        fixture_hashes, roster_hash
    license_basis, source_kind, document_id,        required    required
        document_type, project_id, title

Both directions are enforced, and that is the whole point: a generated document
MUST NOT carry retrieval provenance it does not have, because a fabricated
issuing body is indistinguishable downstream from a verified one -- exactly the
failure Principle I exists to prevent, and the case Principle III tells us to
record as absent instead. Permitting absence on the wrong layer would leave the
fabrication representable, so each field carries *two* named checks: one
requiring it on its own layer, one rejecting it on the other.
"""

from collections.abc import Sequence

from alembic import op

# Revision identifiers, used by Alembic.
#
# TR-004: `revision` doubles as the four-digit filename prefix -- 0001-0099 is
# this epic's reserved block, 0100-0199 is E004's. Ordering is `down_revision`
# and only `down_revision`; the numbers are never compared to decide what runs
# next, so a gap or an out-of-order id is a naming defect, not a broken chain.
revision: str = "0003"
down_revision: str | Sequence[str] | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create `fn_all_sha256_prefixed` and the `document` table.

    TR-003: re-application is a no-op by virtue of Alembic's `alembic_version`
    bookkeeping. Do not add a "have I already run?" guard here.

    Every constraint is named, following `pk_<table>`, `uq_<table>__<purpose>`,
    and `ck_<table>__<rule>`. Two reasons, both mechanical rather than stylistic:
    a server-generated name cannot be relied on by a later forward migration's
    `ALTER TABLE ... DROP CONSTRAINT`, and a test asserting *which* rule rejected
    a row matches on the constraint name -- never on message text, which is
    locale- and version-dependent.
    """
    # A `CHECK` admits no subquery, so `fixture_hashes` cannot be validated
    # element-wise inline -- `unnest` in a check expression is a subquery and is
    # rejected at DDL time. The validation goes through an IMMUTABLE helper
    # instead, the same shape the array invariants on `line_posterior` use
    # (`fn_is_sorted_ascending`, revision 0008).
    #
    # IMMUTABLE STRICT PARALLEL SAFE, arguments only: no lookup, no
    # `current_setting`, no collation-dependent comparison. That is what makes it
    # sound inside a check -- a validated check is emitted with the table ahead
    # of the data, so a restore re-proves the invariant row by row, and the
    # constraint records the function's identity rather than its text.
    #
    # `~` against a character-class pattern is byte-wise here, not collation
    # dependent: the class is spelled `[0-9a-f]` explicitly rather than relying
    # on a locale's notion of a hex digit.
    #
    # Recorded restriction: `CREATE OR REPLACE FUNCTION` does *not* re-validate
    # existing rows. Changing this function is therefore a two-step forward
    # migration -- new function under a new name, new check, drop the old --
    # never an in-place replace.
    #
    # `bool_and` over an empty array yields NULL, which is why the emptiness
    # test lives in the caller rather than here: the helper's contract is
    # "every element matches", vacuously true of no elements.
    op.execute(
        """
        CREATE FUNCTION fn_all_sha256_prefixed(hashes text[])
        RETURNS boolean
        LANGUAGE sql
        IMMUTABLE STRICT PARALLEL SAFE
        AS $$
            SELECT coalesce(
                bool_and(element ~ '^sha256:[0-9a-f]{64}$'),
                true
            )
            FROM unnest(hashes) AS element
        $$
        """
    )

    # TR-039: every check below that constrains a single column's *value domain*
    # sits on a NOT NULL column, so none can be satisfied vacuously by a null.
    # The conditional provenance checks are the deliberate exception, and each
    # one's null branch is closed by its opposite-layer twin: `source_kind` is
    # NOT NULL and closed-set, so for any row exactly one of the pair is the
    # active branch and neither layer has an unchecked field.
    op.execute(
        """
        CREATE TABLE document (
            document_id text NOT NULL,
            document_type text NOT NULL,
            project_id text NOT NULL,
            title text NOT NULL,
            source_kind text NOT NULL,
            source_ref text,
            issuing_body text,
            generator_id text,
            generation_seed text,
            generated_at date,
            fixture_hashes text[],
            license_basis text NOT NULL,
            retrieval_date date,
            roster_hash text,
            loaded_at timestamptz NOT NULL DEFAULT now(),

            CONSTRAINT pk_document PRIMARY KEY (document_id),

            -- TR-041. `char_length`, not `length`: the latter is an alias here
            -- but the former is what the declared format means -- characters,
            -- not bytes -- and stays correct if the column type ever changes.
            CONSTRAINT ck_document__id_format
                CHECK (
                    document_id ~ '^[a-z0-9]+(-[a-z0-9]+)*$'
                    AND char_length(document_id) BETWEEN 3 AND 128
                ),
            CONSTRAINT ck_document__type
                CHECK (
                    document_type IN (
                        'specification',
                        'submittal',
                        'purchase_order',
                        'rfi',
                        'transmittal',
                        'drawing',
                        'reference_standard'
                    )
                ),

            -- Frozen by E001 and adopted verbatim, not re-derived.
            CONSTRAINT ck_document__project_id_format
                CHECK (project_id ~ '^PRJ-[0-9]{3}$'),

            -- Presence, not mere non-nullness. The trim set is spelled out
            -- because single-argument `btrim` strips *spaces only*, so a title
            -- of one tab would otherwise pass a check whose entire purpose is
            -- to reject a document that is not named. Every presence check in
            -- this schema now carries the same six-character set for that
            -- reason: space, tab, newline, carriage return, form feed, and
            -- vertical tab.
            --
            -- **`\\u000B`, not `\\v`.** PostgreSQL's escape-string syntax defines
            -- `\\b \\f \\n \\r \\t` plus octal, hex, and `\\uXXXX` -- and for any
            -- other character after a backslash it *drops the backslash and
            -- keeps the character*. So `E'\\v'` is not a vertical tab, it is the
            -- letter `v`, silently and with no error. Written that way this
            -- check would both miss a vertical-tab-only title (11 absent from
            -- the set) and reject a legitimate title of `vvv` (118 present in
            -- it) -- a hole and a false rejection from one typo. `\\u000B` is
            -- fixed-length and unambiguous; octal `\\013` is equivalent. Both
            -- effects were measured on PostgreSQL 16, not inferred from the
            -- documentation.
            --
            -- This is therefore a *knowing* deviation from data-model.md, which
            -- declares `E' \\t\\n\\r\\f\\v'` on every presence check in the schema
            -- (TR-083). The artifact needs correcting to this spelling; the
            -- character it means is unambiguous, and propagating the literal
            -- text would have shipped the defect on every table.
            --
            -- Doubled backslashes throughout: this DDL is a non-raw Python
            -- string, so `\\t` here is the two characters the server's `E''`
            -- literal then interprets. A single `\\t` would embed a real tab in
            -- the SQL text -- harmless inside a literal, fatal in the `--`
            -- comment above it, where a real newline would end the comment
            -- mid-sentence and leave the rest as SQL tokens.
            CONSTRAINT ck_document__title_present
                CHECK (btrim(title, E' \\t\\n\\r\\f\\u000B') <> ''),

            -- The discriminator every conditional check below keys on. Closed
            -- set and NOT NULL, so no row escapes both branches of a pair.
            CONSTRAINT ck_document__source_kind
                CHECK (source_kind IN ('REAL', 'SYNTHETIC')),

            -- Retrieval provenance: required on REAL, rejected on SYNTHETIC.
            --
            -- `btrim(coalesce(col, ''), ...)`, never a bare `btrim(col, ...)`. A
            -- check constraint rejects a row only when its expression is
            -- *false*, and `btrim(NULL, ...) <> ''` is NULL, not false -- so
            -- `source_kind <> 'REAL' OR btrim(source_ref, ...) <> ''` evaluates
            -- to `false OR NULL` = NULL on the one row it exists to catch, and a
            -- REAL row with no source reference is accepted. The `coalesce` maps
            -- absent to blank, which the comparison then definitely rejects.
            -- Every presence check below is written this way for that reason;
            -- the ones phrased `IS NOT NULL` are already three-valued-safe.
            --
            -- The trim set is the second, independent half of the idiom, and it
            -- is the same on a conditional check as on an unconditional one:
            -- `coalesce` closes the *absent* case, the explicit set closes the
            -- *whitespace-only* case, and a REAL document whose `source_ref` is
            -- one tab is no more traceable than one with none.
            CONSTRAINT ck_document__real_has_source_ref
                CHECK (
                    source_kind <> 'REAL'
                    OR btrim(coalesce(source_ref, ''), E' \\t\\n\\r\\f\\u000B') <> ''
                ),
            CONSTRAINT ck_document__synthetic_has_no_source_ref
                CHECK (source_kind <> 'SYNTHETIC' OR source_ref IS NULL),
            CONSTRAINT ck_document__real_has_issuing_body
                CHECK (
                    source_kind <> 'REAL'
                    OR btrim(coalesce(issuing_body, ''), E' \\t\\n\\r\\f\\u000B') <> ''
                ),
            CONSTRAINT ck_document__synthetic_has_no_issuing_body
                CHECK (source_kind <> 'SYNTHETIC' OR issuing_body IS NULL),
            CONSTRAINT ck_document__real_has_retrieval_date
                CHECK (source_kind <> 'REAL' OR retrieval_date IS NOT NULL),
            CONSTRAINT ck_document__synthetic_has_no_retrieval_date
                CHECK (source_kind <> 'SYNTHETIC' OR retrieval_date IS NULL),

            -- Generation provenance: required on SYNTHETIC, rejected on REAL.
            CONSTRAINT ck_document__synthetic_has_generator
                CHECK (
                    source_kind <> 'SYNTHETIC'
                    OR btrim(coalesce(generator_id, ''), E' \\t\\n\\r\\f\\u000B') <> ''
                ),
            CONSTRAINT ck_document__real_has_no_generator
                CHECK (source_kind <> 'REAL' OR generator_id IS NULL),
            CONSTRAINT ck_document__synthetic_has_seed
                CHECK (
                    source_kind <> 'SYNTHETIC'
                    OR btrim(coalesce(generation_seed, ''), E' \\t\\n\\r\\f\\u000B') <> ''
                ),
            CONSTRAINT ck_document__real_has_no_seed
                CHECK (source_kind <> 'REAL' OR generation_seed IS NULL),
            CONSTRAINT ck_document__synthetic_has_generated_at
                CHECK (source_kind <> 'SYNTHETIC' OR generated_at IS NOT NULL),
            CONSTRAINT ck_document__real_has_no_generated_at
                CHECK (source_kind <> 'REAL' OR generated_at IS NULL),

            -- `coalesce(array_length(...), 0)`: `array_length` of both NULL and
            -- the empty array is NULL, so a bare `>= 1` would evaluate to NULL
            -- and the whole conditional would pass vacuously on a SYNTHETIC row
            -- carrying `'{}'::text[]` -- the one shape this check exists to
            -- reject. Wrapping it makes the null branch a definite false.
            CONSTRAINT ck_document__synthetic_has_fixture_hashes
                CHECK (
                    source_kind <> 'SYNTHETIC'
                    OR (
                        coalesce(array_length(fixture_hashes, 1), 0) >= 1
                        AND fn_all_sha256_prefixed(fixture_hashes)
                    )
                ),
            CONSTRAINT ck_document__real_has_no_fixture_hashes
                CHECK (source_kind <> 'REAL' OR fixture_hashes IS NULL),

            -- Required on both layers. Replicated with the row on purpose: the
            -- per-project duplication TR-074 mandates carries the license basis
            -- along with it, so no corpus location ends up mixing licenses.
            CONSTRAINT ck_document__license_basis_present
                CHECK (btrim(license_basis, E' \\t\\n\\r\\f\\u000B') <> ''),

            -- Generation provenance too, and therefore a *pair* like every
            -- other field in that group. Required and well-formed on SYNTHETIC:
            -- a generated document came from a roster, so the roster it came
            -- from is recorded. `coalesce` for the same three-valued reason as
            -- above -- `NULL ~ pattern` is NULL, not false.
            --
            -- Rejected on REAL for exactly the reason `generator_id` is: a
            -- retrieved document was not generated from a roster, so a roster
            -- hash on one is a claim about provenance it does not have.
            -- Permitting absence rather than enforcing it would leave a
            -- malformed or fabricated roster hash representable on the layer
            -- that has no roster at all, which is the asymmetry TR-075 exists
            -- to close.
            CONSTRAINT ck_document__synthetic_has_roster_hash
                CHECK (
                    source_kind <> 'SYNTHETIC'
                    OR coalesce(roster_hash, '') ~ '^sha256:[0-9a-f]{64}$'
                ),
            CONSTRAINT ck_document__real_has_no_roster_hash
                CHECK (source_kind <> 'REAL' OR roster_hash IS NULL),

            -- TR-046, SC-018: the foreign-key target `fk_chunk__document`
            -- (revision 0004) references. Column order is the referenced key's
            -- order and is load-bearing -- it is what lets a chunk carry
            -- `document_type` and `project_id` denormalized without either
            -- being able to disagree with its document.
            CONSTRAINT uq_document__id_type_project
                UNIQUE (document_id, document_type, project_id)
        )
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
