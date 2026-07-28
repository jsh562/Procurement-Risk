"""value associations

Revision ID: 0303
Revises: 0302
Create Date: 2026-07-27

The two value-level associations: line-item membership (FR-059, SC-046) and the
parse signals a confidence was computed from (FR-063, FR-057, FR-046, SC-026).
One revision for both, on the same grounds as `0302`: they share the
`uq_ingestion_run_extracted_value__value_generation` target and no intermediate
head is useful.

**Both reference the run-output association rather than `extracted_value`
directly**, and three things follow that a direct foreign key cannot give. A row
cannot exist for a value with no run attribution; its `run_id` and `document_id`
**cannot disagree** with the value's own, because they are the same referenced
key; and the grouping key is generation-scoped, so a promotion's removal step
finds every row of the generation it is replacing by keyed lookup rather than by
joining back through `extracted_value`.

**`extracted_value_line_item` -- why the item ordinal and not the source chunk.**
Keying on the source chunk would make an over-long item entry split across two
chunks silently become two line items. The ordinal is assigned by the extractor
from the printed item order and survives the split, because both chunks' values
carry the same ordinal. **`0` is a declared group, not a sentinel**: a transmittal
prints its submittal number, submittal date and approval date once for the
document, and those values have no printed item to belong to. Admitting group 0
is what lets SC-046 stay literally absolute over every extracted value instead of
being narrowed afterwards to the values that happen to belong to a printed item.

**`extracted_value_parse_signal` -- the inputs to a computed confidence, stored
where they can be read back.** Two of the three signals exist in no column
anywhere: nothing records that a printed label matched a known alternate rather
than the canonical form, and nothing records that an invocation validated only
after a repair (`extraction_failure.repair_attempt_count` covers failures, and a
value that repaired successfully produces no failure row). Without them,
"recompute the confidence from its signals" reduces to reading the confidence and
comparing it with itself, and SC-026 passes on a tautology.

**The page-split signal is deliberately not an independent boolean.** It already
exists as E003's `extracted_value.source_chunk_count`, so a `page_split boolean`
here would be a second answer that can disagree with the value's own provenance
-- and the disagreement would be invisible, because the recomputation would read
the copy while the citation read the original. Carried as the count and held
equal by `fk_extracted_value_parse_signal__value_count` against E003's
**existing** `uq_extracted_value__id_source_count`, so page-split is
`source_chunk_count > 1` and cannot differ from what
`extracted_value_contributing_chunk` actually holds. No object is added to
`extracted_value` ({SAD:ADR-0017}).

**No `confidence` column here, and no admissibility `CHECK`.** The score is
`extracted_value.confidence`, E003's, and copying it beside its inputs would
create the one thing SC-026 exists to detect. Admissibility under the floor is
not a `CHECK` because a signal row cannot see `ingestion_run.confidence_floor` or
the three weights -- they are columns two joins away, and one hard-coding 0.80
would reject a legitimate value under a run that declared a different policy.
What *is* enforced, on `ingestion_run` in `0300`, is that the declared policy
honours FR-057's two named exclusions; each stored value's conformance to it is
tested by VR-026 and the residual is **G-9**.
"""

from collections.abc import Sequence

from alembic import op

# Revision identifiers, used by Alembic.
#
# FR-040: `revision` doubles as the four-digit filename prefix. Ordering is
# `down_revision` and only `down_revision`.
revision: str = "0303"
down_revision: str | Sequence[str] | None = "0302"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the line-item and parse-signal associations with their indexes.

    Re-application is a no-op by virtue of Alembic's `alembic_version`
    bookkeeping (VR-014). Do not add a "have I already run?" guard here.

    Every composite foreign key here is `MATCH FULL` for the reason `0302`
    records, and every edge is `ON DELETE RESTRICT`: `RESTRICT` cannot be
    deferred, which is what forces the promotion's removal to proceed strictly
    leaf-up and makes these two tables the first rows it deletes.
    """
    # --- extracted_value_line_item (T012) ------------------------------------
    op.execute(
        """
        CREATE TABLE extracted_value_line_item (
            -- The value, alone, is the key. SC-046 requires every value to
            -- belong to *exactly one* line item, and a primary key on the value
            -- is what makes a second membership unrepresentable rather than
            -- merely wrong.
            extracted_value_id uuid NOT NULL,

            run_id uuid NOT NULL,
            document_id text NOT NULL,

            -- Real items are one-based, matching the printed item numbering on
            -- the transmittal. `0` means "printed once for the whole document".
            item_ordinal smallint NOT NULL,

            CONSTRAINT pk_extracted_value_line_item PRIMARY KEY (extracted_value_id),

            -- Loosened by one value against an item-scoped reading, on a column
            -- this epic owns, so nothing moves at the E003 boundary. A reader
            -- selects `item_ordinal = 0` for document-scoped values and `>= 1`
            -- to iterate items; neither is a pattern match on a magic number.
            CONSTRAINT ck_extracted_value_line_item__ordinal_non_negative
                CHECK (item_ordinal >= 0),

            -- No `field_name` column, and its absence is decided rather than
            -- forgotten: a "one manufacturer per line item" rule would need the
            -- field denormalized here and held equal by a composite FK against a
            -- unique key on `extracted_value` that E006 may not add -- and the
            -- rule is not universally true anyway, since an item may legitimately
            -- cite two compliance standards. Left unasserted rather than
            -- half-enforced; disclosed as **G-5**.
            CONSTRAINT fk_extracted_value_line_item__run_output
                FOREIGN KEY (extracted_value_id, run_id, document_id)
                REFERENCES ingestion_run_extracted_value
                    (extracted_value_id, run_id, document_id)
                MATCH FULL
                ON DELETE RESTRICT
                ON UPDATE CASCADE
        )
        """
    )

    # The grouping read -- "every value of item 3 of this document" -- and the
    # index the promotion's RESTRICT-ordered delete uses.
    op.execute(
        """
        CREATE INDEX ix_extracted_value_line_item__item
        ON extracted_value_line_item (run_id, document_id, item_ordinal)
        """
    )

    # --- extracted_value_parse_signal (T012) ---------------------------------
    op.execute(
        """
        CREATE TABLE extracted_value_parse_signal (
            -- The value alone is the key, so a second, disagreeing signal row
            -- for one value is unrepresentable (invariant 19).
            extracted_value_id uuid NOT NULL,

            run_id uuid NOT NULL,
            document_id text NOT NULL,

            -- FR-057's 0.15 deduction. A named vocabulary rather than a boolean
            -- `was_alternate`: the column says which of two stated things the
            -- label was, not whether an unstated default did not hold.
            label_match text NOT NULL,

            -- FR-057's 0.10 deduction, carried as the count rather than as a
            -- boolean and held equal to the value's own by
            -- `fk_extracted_value_parse_signal__value_count` below.
            source_chunk_count smallint NOT NULL,

            -- FR-057's 0.25 deduction. A boolean and not a count: the deduction
            -- applies once for "validated only after a repair" regardless of how
            -- many attempts were spent, and a count here would invite the
            -- deduction to be scaled by it -- arithmetic the requirement does not
            -- state. `boolean NOT NULL` needs no CHECK; the type is the domain.
            validated_after_repair boolean NOT NULL,

            CONSTRAINT pk_extracted_value_parse_signal PRIMARY KEY (extracted_value_id),

            -- Alternates resolve against the field-label vocabulary E002
            -- committed, which is where the closed set lives; this column
            -- records only which side of it the printed label fell on.
            CONSTRAINT ck_extracted_value_parse_signal__label_match
                CHECK (label_match IN ('canonical', 'alternate')),

            -- The anchor is contributor 1, so the count is at least 1 on every
            -- row and there is no "zero sources" state to represent -- the same
            -- floor as `ck_extracted_value__source_count_positive`, which the
            -- composite FK below holds this column equal to.
            CONSTRAINT ck_extracted_value_parse_signal__source_count_positive
                CHECK (source_chunk_count >= 1),

            CONSTRAINT fk_extracted_value_parse_signal__run_output
                FOREIGN KEY (extracted_value_id, run_id, document_id)
                REFERENCES ingestion_run_extracted_value
                    (extracted_value_id, run_id, document_id)
                MATCH FULL
                ON DELETE RESTRICT
                ON UPDATE CASCADE,

            -- Targets E003's existing `uq_extracted_value__id_source_count`, the
            -- unique key `0006` declared for `extracted_value_contributing_chunk`
            -- to reference. RESTRICT here is also what forces a signal row to be
            -- deleted before its value: a CASCADE would let a value removal
            -- silently take its parse signals with it and hide a mis-ordered
            -- removal (invariant 23).
            CONSTRAINT fk_extracted_value_parse_signal__value_count
                FOREIGN KEY (extracted_value_id, source_chunk_count)
                REFERENCES extracted_value (extracted_value_id, source_chunk_count)
                MATCH FULL
                ON DELETE RESTRICT
                ON UPDATE CASCADE
        )
        """
    )

    # Referencing-side index for the generation foreign key; the promotion's
    # leaf-up delete finds this generation's signal rows through it.
    op.execute(
        """
        CREATE INDEX ix_extracted_value_parse_signal__generation
        ON extracted_value_parse_signal (run_id, document_id)
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
