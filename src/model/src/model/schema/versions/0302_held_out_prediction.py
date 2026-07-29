"""held out prediction

Revision ID: 0302
Revises: 0301
Create Date: 2026-07-27

**Two objects, one revision, and the order is not negotiable.** This revision
adds `uq_purchase_order_line__order_anchor` to E003's delivered
`purchase_order_line`, and then creates `held_out_prediction` whose composite
foreign key `fk_held_out_prediction__line_anchor` targets it. PostgreSQL requires
a foreign key's referenced column list to be covered by a unique constraint that
already exists, so splitting these into two revisions would leave the second one
unapplicable on its own -- and would break `test_chain_applies_to_an_empty_database`
at exactly the boundary it exists to find.

**`held_out_prediction` exists because the delivered schema admits no
alternative.** A held-out line has already delivered, so under the run's own
as-of-date anchor it carries a *negative* remaining duration that
`ck_line_posterior__draws_non_negative` rejects; `line_posterior.survival` is
NOT NULL and cannot express the absence; and
`ck_schema_constants__anchor_convention` pins the run-level convention
`/src/api` reads, so a per-row anchor is unrepresentable there. The held-out
population therefore gets its own table, anchored at **the line's own order
date**, holding a **total** duration rather than a remaining one (FR-008,
FR-012, FR-029).

**The anchor is a foreign key, not a comment.** `anchor_date` could have been a
plain column with a test asserting it equals the line's order date. It is a
composite FK instead, in the exact idiom of the delivered
`fk_extracted_value__chunk_page`. A mis-anchored prediction is the silent failure
Principle III names: every other constraint passes, E014 grades the row against
the wrong origin, and nothing anywhere reports a problem. The FK makes it
unrepresentable. `line_is_closed` rides in the same key, carrying the delivered
`ck_pol__closed_iff_delivered` into the referenced tuple, so a prediction row can
only name a line that actually delivered -- the same idiom as
`uq_lifecycle_event__id_line_terminal`.

**`ON UPDATE RESTRICT`, a deliberate departure from E003's convention.** E003
sets `ON UPDATE CASCADE` on composite FKs whose parent key has a mutable column,
so a legitimate correction propagates. Here it must not: cascading a corrected
`order_date` would silently re-anchor draws that were computed against the old
one, producing exactly the mis-anchored row this FK exists to prevent. Refusing
forces a refit, which is the correct outcome. `order_date` corrections are in any
case unreachable through E005's loader, which refuses on content divergence
rather than updating.

**The three array helpers are called, never re-declared.** `fn_is_sorted_ascending`,
`fn_is_non_increasing` and `fn_all_within_unit_interval` are E003's, delivered by
`0008`. A second copy under an E007 name would be a second thing to keep in step,
and **DV-026** asserts that this epic declares exactly one function of its own --
`fn_vendor_shrinkage_wellformed`, in `0300`.

**Recorded deviations from `data-model.md`, both inherited verbatim from E003's
`0008` and both null-handling repairs**: the two length checks are written
`coalesce(array_length(...), 0) = ...` because `array_length('{}', 1)` is NULL
rather than 0 and a `CHECK` accepts NULL; and the two `_1d` checks additionally
assert `array_lower(..., 1) = 1`, because PostgreSQL array subscripts need not
start at 1 and `survival[horizon_days]` on a lower-bound-0 array is out of range
and therefore NULL. Neither deviation adds a constraint name.

**`uq_purchase_order_line__order_anchor` lands on a populated table and rejects
nothing.** Its leading column is already that table's primary key, so the
constraint is satisfied by every existing and future row -- a proof, not an
assertion. It is additive, it changes no existing constraint, and it exists
solely as an FK target, exactly as the delivered `uq_chunk__chunk_page` does.
That it is a change to another epic's delivered table is disclosed as **G-14**.

Forward-only: `downgrade()` raises.
"""

from collections.abc import Sequence

from alembic import op

# Revision identifiers, used by Alembic.
revision: str = "0302"
down_revision: str | Sequence[str] | None = "0301"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


#: G-14. The FK target, on E003's delivered table.
#:
#: `ALTER TABLE ... ADD CONSTRAINT ... UNIQUE` rather than a bare
#: `CREATE UNIQUE INDEX`: a foreign key may reference either, but only a table
#: constraint appears in `information_schema.table_constraints` and only a
#: constraint can be dropped by name in a later forward revision without
#: reasoning about which index backs it. It is also what `data-model.md`
#: declares (TR-083).
#:
#: `NOT VALID` is deliberately not used, here or anywhere in this epic: the
#: constraint validates every existing row at the moment it lands, which is
#: free, because the leading column is already the primary key.
ADD_ORDER_ANCHOR_UNIQUE_KEY = """
ALTER TABLE purchase_order_line
    ADD CONSTRAINT uq_purchase_order_line__order_anchor
    UNIQUE (po_line_id, order_date, is_closed)
"""


#: One row per held-out **delivered** line per run, holding both arrays.
#:
#: TR-031's shape, borrowed from `line_posterior`: `draws` and `survival` are two
#: NOT NULL columns of *one* row, so "the draws were written but the survival
#: curve was not" is not a state the database can be in. FR-013's atomicity is
#: carried by that, with no trigger and no deferrable constraint.
#:
#: Every array invariant the delivered `line_posterior` carries is re-declared
#: here under E007's own constraint names, because no delivered constraint
#: reaches this table. Their **parity** with the delivered set -- so a future
#: strengthening cannot land on one store only -- is **DV-027**.
CREATE_HELD_OUT_PREDICTION = """
CREATE TABLE held_out_prediction (
    run_id uuid NOT NULL,
    po_line_id uuid NOT NULL,

    -- Copies of the run's shape, and not redundant storage: they are the
    -- columns `fk_held_out_prediction__run_shape` compares, which is what turns
    -- "as long as its run says" into a referential fact rather than a value the
    -- writer chose. The two `array_length` checks below then compare each array
    -- against a number already proven to be the run's own.
    draw_count integer NOT NULL,
    horizon_days integer NOT NULL,

    -- **The line's own `order_date`, proved by the FK rather than asserted.**
    -- `date`, matching `purchase_order_line.order_date` exactly -- a composite
    -- foreign key compares values, so a `timestamptz` here would not be a
    -- looser version of the same claim, it would fail to reference at all.
    anchor_date date NOT NULL,

    -- Carries the delivered `ck_pol__closed_iff_delivered` into the referenced
    -- key: `is_closed` is an unforgeable synonym for delivered, so a prediction
    -- row can only name a line that actually delivered and can therefore be
    -- graded. The column-level check below then pins it to true on this side,
    -- so the two artifact populations are structurally disjoint here. The
    -- reverse direction -- an order-date-anchored row written into
    -- `line_posterior` -- is not structurally excluded and is **G-5**.
    line_is_closed boolean NOT NULL,

    -- Per row, unlike the open population's, which rides on the run. Each
    -- population records its anchor and its semantic where the population
    -- lives, so no reader infers either.
    anchor_convention text NOT NULL,
    duration_semantic text NOT NULL,

    -- **Total** duration in days from the line's order date -- the quantity its
    -- observed outcome can be graded against -- ascending, so
    -- `draws[ceil(p * draw_count)]` is a direct one-based subscript with no
    -- interpolation and no read-time sort. Fractional values are permitted and
    -- expected: AD-004's day tolerance is measured against unrounded draws, and
    -- rounding at storage would quantise the very quantity the tolerance bounds.
    --
    -- The declared size in `double precision[]` is deliberately absent:
    -- PostgreSQL ignores declared array dimensions entirely, so
    -- `double precision[4000]` would document an intention and enforce nothing.
    -- Cardinality lives in `ck_held_out_prediction__draws_length`.
    draws double precision[] NOT NULL,

    -- `survival[k] = P(not yet delivered at end of day anchor_date + k)`, for
    -- k = 1..horizon_days. **Read by E014 only** -- E010 must never read this
    -- table, because its `survival[d - as_of_date]` contract is false here
    -- and it has no way to distinguish the two anchors. That contract has no
    -- schema mechanism; it is ADR-0018's, and **G-19**.
    survival double precision[] NOT NULL,

    -- `P(total > horizon_days)`. Reachable and expected on this population: a
    -- 380-day delivery under a 365-day grid gives a survival array that never
    -- reaches the outcome. The grid cannot express the observation; the draws
    -- can, and the draws are what E014 grades.
    residual_tail_mass double precision NOT NULL,

    -- 32 raw bytes over the serialized draws, in the layout
    -- `forecast_run.draw_serialization` names. `bytea` rather than hex text for
    -- the reason `line_posterior.draw_digest` is: the digest covers bytes, and
    -- a text rendering would depend on `extra_float_digits` and the session
    -- locale, so the same draws would digest differently in two sessions.
    draw_digest bytea NOT NULL,

    CONSTRAINT pk_held_out_prediction PRIMARY KEY (run_id, po_line_id),

    -- The composite FK that carries both array lengths, exactly as
    -- `fk_line_posterior__run_shape` does. `MATCH FULL` as declared, and here
    -- it is equivalent to MATCH SIMPLE rather than a repair of it: all three
    -- referencing columns are NOT NULL, so the partially-null referencing row
    -- MATCH SIMPLE would silently skip is unrepresentable.
    CONSTRAINT fk_held_out_prediction__run_shape
        FOREIGN KEY (run_id, draw_count, horizon_days)
        REFERENCES forecast_run (run_id, draw_count, horizon_days)
        MATCH FULL
        ON DELETE CASCADE
        ON UPDATE CASCADE,

    -- **The anchor, as a referential fact.** See the module docstring for why
    -- `ON UPDATE RESTRICT` departs from E003's convention here and must.
    -- `MATCH FULL` again over three NOT NULL columns.
    CONSTRAINT fk_held_out_prediction__line_anchor
        FOREIGN KEY (po_line_id, anchor_date, line_is_closed)
        REFERENCES purchase_order_line (po_line_id, order_date, is_closed)
        MATCH FULL
        ON DELETE RESTRICT
        ON UPDATE RESTRICT,

    -- The line delivered. Redundant against the FK only in the presence of a
    -- `purchase_order_line` row whose `is_closed` is true, which is precisely
    -- what makes it worth stating separately: this check is false on the row
    -- itself, so the server names the actual rule rather than reporting a
    -- reference to a tuple that does not exist.
    CONSTRAINT ck_held_out_prediction__line_delivered
        CHECK (line_is_closed),

    CONSTRAINT ck_held_out_prediction__anchor_convention
        CHECK (anchor_convention = 'line_order_date'),

    CONSTRAINT ck_held_out_prediction__duration_semantic
        CHECK (duration_semantic = 'total_duration_from_line_order_date'),

    -- One dimension, subscripts starting at 1. The `array_lower` conjunct is
    -- E003's recorded deviation and it is load-bearing twice here: the
    -- percentile convention subscripts `draws` directly, and the residual check
    -- below reads `survival[horizon_days]`.
    CONSTRAINT ck_held_out_prediction__draws_1d
        CHECK (array_ndims(draws) = 1 AND array_lower(draws, 1) = 1),

    -- `coalesce(..., 0)`, E003's recorded deviation:
    -- `array_length('{}'::float8[], 1)` is NULL, not 0, and a `CHECK` accepts
    -- NULL -- so the declared form admits an artifact row with no draws at all.
    -- `ck_forecast_run__draw_count_positive` guarantees the target is positive,
    -- so the substituted 0 can never match it.
    CONSTRAINT ck_held_out_prediction__draws_length
        CHECK (coalesce(array_length(draws, 1), 0) = draw_count),

    -- E003's helper, called and not re-declared (DV-026). No `IS NULL` guard is
    -- needed around this STRICT function: the argument is the NOT NULL `draws`
    -- column. Its *elements* can be null and the helper returns false for those.
    CONSTRAINT ck_held_out_prediction__draws_sorted
        CHECK (fn_is_sorted_ascending(draws)),

    -- One subscript is enough because the array is sorted ascending, so
    -- `draws[1]` is its minimum. **Sufficient here in a way it is not for
    -- `line_posterior`**: a total duration measured from the line's own order
    -- date is non-negative by construction, with nothing to clip -- unlike a
    -- re-based remaining duration, where clipping a negative value to zero
    -- would satisfy the same check (FR-029).
    --
    -- The null branch is closed by a sibling rather than left open:
    -- `draws[1]` could be NULL only for an array with a null first element,
    -- which `ck_held_out_prediction__draws_sorted` refuses -- including the
    -- single-element `'{NULL}'` case no adjacent-pair comparison reaches.
    CONSTRAINT ck_held_out_prediction__draws_non_negative
        CHECK (draws[1] >= 0.0),

    CONSTRAINT ck_held_out_prediction__survival_1d
        CHECK (array_ndims(survival) = 1 AND array_lower(survival, 1) = 1),

    CONSTRAINT ck_held_out_prediction__survival_length
        CHECK (coalesce(array_length(survival, 1), 0) = horizon_days),

    -- A survival curve cannot rise: a delivery does not un-happen. Ties are
    -- allowed and common -- a day with no probability mass leaves it flat.
    CONSTRAINT ck_held_out_prediction__survival_monotone
        CHECK (fn_is_non_increasing(survival)),

    -- Every element a probability, inclusive at both ends. This is also the
    -- check that makes every element definite -- the helper refuses a NULL
    -- element and, because `NaN <= 1.0` is false, a NaN one -- which is what
    -- lets the residual check below be a plain comparison.
    CONSTRAINT ck_held_out_prediction__survival_unit_interval
        CHECK (fn_all_within_unit_interval(survival)),

    CONSTRAINT ck_held_out_prediction__residual_range
        CHECK (residual_tail_mass >= 0.0 AND residual_tail_mass <= 1.0),

    -- **The array and the residual account for the full distribution, to a
    -- tolerance.** `abs(a - b) <= 1e-9`, never `a = b`: both operands are
    -- `double precision` computed by independent paths, and exact equality
    -- between two independently computed binary floats is a coin flip on the
    -- last bit. That independence is what makes this a genuine agreement test
    -- rather than a tautology, and exactly why it cannot be an equality.
    --
    -- `1e-9` is the delivered `schema_constants.probability_sum_tolerance`, in
    -- the same form as `ck_line_posterior__residual_matches_grid_tail`. The
    -- tolerance is mirrored rather than tightened, and it is the **third**
    -- place this literal appears in the DDL for one published constant --
    -- which is why the drift test in `tests/schema/test_constants_agreement.py`
    -- enumerates every constraint carrying a double-precision literal rather
    -- than naming one by hand (G-3).
    --
    -- The null branch is closed by construction: `survival[horizon_days]` is in
    -- subscript range because the length and lower-bound checks fix both, and
    -- the element is non-null because the unit-interval helper refuses nulls.
    CONSTRAINT ck_held_out_prediction__residual_matches_grid_tail
        CHECK (abs(survival[horizon_days] - residual_tail_mass) <= 1e-9),

    -- 32 bytes = SHA-256, counted in bytes on a `bytea`.
    CONSTRAINT ck_held_out_prediction__draw_digest_length
        CHECK (octet_length(draw_digest) = 32)
)
"""

#: Reverse lookup from a line, and the index that keeps
#: `fk_held_out_prediction__line_anchor`'s RESTRICT check off a full scan on
#: every line delete. The primary key leads with `run_id` and cannot serve it.
CREATE_PO_LINE_INDEX = """
CREATE INDEX ix_held_out_prediction__po_line
ON held_out_prediction (po_line_id)
"""

#: FR-034, the same grant `0301` makes and for the same reasons: `UPDATE` is
#: withheld because an artifact row is written once and never edited, `DELETE`
#: is retained so discarding a run is a plain operation.
GRANT_TO_APPLICATION_ROLE = "GRANT SELECT, INSERT, DELETE ON held_out_prediction TO procurement_app"


def upgrade() -> None:
    """Add the FK target, then the table that references it, then the grant.

    TR-003: re-application is a no-op by virtue of Alembic's `alembic_version`
    bookkeeping. Do not add a "have I already run?" guard here.

    The order inside this body is forced by PostgreSQL, not chosen: a foreign
    key whose referenced column list is not covered by an existing unique
    constraint is refused at DDL time.
    """
    op.execute(ADD_ORDER_ANCHOR_UNIQUE_KEY)
    op.execute(CREATE_HELD_OUT_PREDICTION)
    op.execute(CREATE_PO_LINE_INDEX)
    op.execute(GRANT_TO_APPLICATION_ROLE)


def downgrade() -> None:
    """Refuse: migrations in this project are forward-only."""
    raise NotImplementedError(
        "This migration is forward-only and defines no downgrade. "
        "To undo a schema change, author a new forward revision; to recover a "
        "database, restore it from a backup. Dropping `held_out_prediction` "
        "discards the only gradeable artifact population a published run has, "
        "and dropping `uq_purchase_order_line__order_anchor` would first require "
        "dropping the foreign key that proves each prediction's anchor."
    )
