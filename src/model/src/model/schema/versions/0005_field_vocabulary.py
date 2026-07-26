"""field vocabulary

Revision ID: 0005
Revises: 0004
Create Date: 2026-07-26 08:50:52.842279

TR-044: the extraction field vocabulary is a *lookup table with a foreign key*,
deliberately not a PostgreSQL `ENUM`. Four properties of an enum decide this, and
none of them is a matter of taste (research: *Closed vocabularies*):

1. An enum value can never be removed, so a forward-only chain (TR-002) has no
   way to retire a term at all -- only to stop using it and hope.
2. Enum ordering is fixed at creation, so a term inserted later sorts by when it
   was thought of rather than by anything meaningful.
3. Comparing an enum against text needs an explicit cast, which is friction on
   every join and a trap at every call site.
4. A value added by `ALTER TYPE ... ADD VALUE` is unusable until the enclosing
   transaction commits. A single revision that adds a term and backfills rows
   with it therefore fails at *runtime*, not at review -- exactly the shape a
   forward-only migration wants to be able to take.

So growth is an ordinary `INSERT` in a later revision: no DDL, no `ACCESS
EXCLUSIVE` type lock, no rewrite (SC-021, OBJ3 VC7). And the table is a join
surface, which an enum is not -- `label` and `description` are readable through
the same reference the foreign key already needs.

TR-079: the 22 rows are seeded by *this* revision, in the same transaction that
creates the table, so the table is never observable empty and there is no loader
script to forget. Recovery is re-applying the chain against a rebuilt database;
loss of a *referenced* row is prevented instead by the restricting foreign keys
`0006` and its siblings declare.

`uq_field_vocabulary__name_kind (field_name, value_kind)` is redundant against
the primary key and exists for one mechanical reason: it is the key
`extracted_value.(field_name, value_kind)` references in `0006`. Carrying the
declared kind into the child row is what reduces "the typed numeric column is
populated exactly for numeric fields" from a cross-table lookup to a single-row
`CHECK`. Column order is the referenced key's order and is load-bearing -- a
composite foreign key matches its parent key positionally, so `(value_kind,
field_name)` here would leave `0006` with nothing to point at.

`retired_at` is nullable so a term can be retired *without deletion*: the join
surface stays intact for the historical rows that cite it, which a `DELETE` would
either orphan or (under RESTRICT) refuse. Retirement is advisory at the storage
boundary -- disclosed as gap G-7, since a foreign key checks existence and cannot
see a sibling column's value. The recorded escalation is a partial unique index
on `(field_name, value_kind) WHERE retired_at IS NULL`, which removes the
referent; it is not built now because no consumer filters on it yet.

TR-083: every term below is declared in `data-model.md` §Seeded Data, and its
`label` / `description` are the reader-facing semantics that artifact makes
normative. No term is invented here.
"""

from collections.abc import Sequence

from alembic import op

# Revision identifiers, used by Alembic.
#
# TR-004: `revision` doubles as the four-digit filename prefix -- 0001-0099 is
# this epic's reserved block, 0100-0199 is E004's. Ordering is `down_revision`
# and only `down_revision`; the numbers are never compared to decide what runs
# next, so a gap or an out-of-order id is a naming defect, not a broken chain.
revision: str = "0005"
down_revision: str | Sequence[str] | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create `field_vocabulary` and seed its 22 terms.

    TR-003: re-application is a no-op by virtue of Alembic's `alembic_version`
    bookkeeping. Do not add a "have I already run?" guard here, and do not make
    the seed `ON CONFLICT DO NOTHING` for the same reason -- it would hide a
    genuine collision rather than prevent a re-run that cannot happen.

    Every constraint is named, following `pk_<table>`, `uq_<table>__<purpose>`,
    and `ck_<table>__<rule>`. Two mechanical reasons: a server-generated name
    cannot be relied on by a later forward migration's `ALTER TABLE ... DROP
    CONSTRAINT`, and a test asserting *which* rule rejected a row matches on the
    constraint name -- never on message text, which is locale- and
    version-dependent.

    TR-039: every `CHECK` below sits on a `NOT NULL` column, so none can be
    satisfied vacuously. That matters more than it looks: a `CHECK` rejects only
    on *false*, and `btrim(col, ...) <> ''` on a NULL column evaluates to NULL,
    which a check accepts. A presence check on a nullable column therefore passes
    precisely the row it exists to catch, and would need `coalesce(col, '')`.
    `label` and `description` are NOT NULL, so the bare form is correct here --
    verified by inserting the violating rows, not by reading the expression.
    `retired_at` is the one nullable column and deliberately carries no check.
    """
    op.execute(
        """
        CREATE TABLE field_vocabulary (
            field_name text NOT NULL,
            value_kind text NOT NULL,
            label text NOT NULL,
            description text NOT NULL,

            -- Nullable by design: retirement without deletion. See the module
            -- docstring and disclosed gap G-7 -- advisory at this boundary, and
            -- so carrying no check of its own.
            retired_at date,

            -- TR-044: the natural key. An upstream concept already owns the
            -- identifier, so there is no surrogate uuid here (data-model.md's
            -- surrogate-key rule).
            CONSTRAINT pk_field_vocabulary PRIMARY KEY (field_name),

            -- Frozen identifier shape: lowercase, starts with a letter, then
            -- letters, digits, or underscores, 3-64 characters total. The
            -- vocabulary is a join surface and these names appear in queries and
            -- in `resolved_entity.agreement_attribute_names`, so mixed case or a
            -- leading digit would be a quoting hazard rather than a cosmetic
            -- one. `{2,63}` follows the anchored first character, giving the
            -- 3-64 total.
            CONSTRAINT ck_field_vocabulary__name_format
                CHECK (field_name ~ '^[a-z][a-z0-9_]{2,63}$'),

            -- The closed set. `value_kind` drives which of `extracted_value`'s
            -- two value columns is populated (TR-045), so a fourth kind is not a
            -- new label -- it is a change to that table's single-row rule. Adding
            -- one is therefore a deliberate migration, which is what this check
            -- makes it. `date` terms store ISO-8601 text in `value_text` and
            -- leave `value_number` NULL; there is no third typed column.
            CONSTRAINT ck_field_vocabulary__value_kind
                CHECK (value_kind IN ('text', 'number', 'date')),

            -- TR-083. A term whose label or description is blank documents
            -- nothing, so both are NOT NULL *and* checked non-blank.
            --
            -- The trim set is spelled out because single-argument `btrim` strips
            -- *spaces only*: a label of one tab, newline, or vertical tab would
            -- otherwise satisfy a bare `btrim(label) <> ''` while being just as
            -- unreadable as ''. The same six-character set is the idiom every
            -- presence check in this schema carries.
            --
            -- `\\u000B` is the vertical tab, and writing it that way is not
            -- optional: PostgreSQL's escape-string syntax has no `\\v`, and an
            -- unrecognized escape drops the backslash and keeps the letter, so
            -- `E'\\v'` puts a literal `v` in the trim set. That single typo
            -- produces two defects at once -- a vertical-tab-only label passes
            -- (11 missing from the set) and a legitimate label of `vvv` is
            -- rejected (118 wrongly in it). Octal `\\013` is equivalent.
            CONSTRAINT ck_field_vocabulary__label_present
                CHECK (btrim(label, E' \\t\\n\\r\\f\\u000B') <> ''),
            CONSTRAINT ck_field_vocabulary__description_present
                CHECK (btrim(description, E' \\t\\n\\r\\f\\u000B') <> ''),

            -- The composite foreign-key target `0006` needs. Redundant against
            -- `pk_field_vocabulary` on purpose: a composite foreign key must
            -- reference a unique key that carries *both* columns, and the primary
            -- key alone cannot carry the kind. Order is `(field_name,
            -- value_kind)` because that is the order
            -- `extracted_value.fk_extracted_value__field` declares.
            CONSTRAINT uq_field_vocabulary__name_kind
                UNIQUE (field_name, value_kind)
        )
        """
    )

    # TR-044, TR-079, TR-083: the 22 terms, transcribed from `data-model.md`
    # §Seeded Data -- name, kind, label, and description each verbatim. They are
    # drawn from the three document worlds the product reconciles: the
    # specification (what was required), the submittal (what was proposed), and
    # the purchase order (what was bought).
    #
    # One `INSERT` with 22 tuples rather than 22 statements: it is one round
    # trip, and the whole seed either lands or does not, so the table cannot be
    # left half-populated by a failure partway down the list.
    op.execute(
        """
        INSERT INTO field_vocabulary (field_name, value_kind, label, description) VALUES
            ('manufacturer', 'text', 'Manufacturer',
             'Manufacturer or brand named for the material item.'),
            ('part_number', 'text', 'Part Number',
             'Vendor or manufacturer catalogue number as printed.'),
            ('model_number', 'text', 'Model Number',
             'Model designation where distinct from the part number.'),
            ('product_description', 'text', 'Product Description',
             'Free-text description of the material item.'),
            ('specification_section', 'text', 'Specification Section',
             'MasterFormat division and section reference.'),
            ('material_category', 'text', 'Material Category',
             'Trade-level grouping of the item.'),
            ('finish_or_grade', 'text', 'Finish or Grade',
             'Surface finish, alloy, or material grade.'),
            ('compliance_standard', 'text', 'Compliance Standard',
             'Referenced standard the item must satisfy (cited, never reproduced).'),
            ('quantity', 'number', 'Quantity',
             'Ordered or specified count.'),
            ('unit_of_measure', 'text', 'Unit of Measure',
             'Unit the quantity is expressed in.'),
            ('unit_price', 'number', 'Unit Price',
             'Price per unit as stated on the source document.'),
            ('extended_price', 'number', 'Extended Price',
             'Line total as stated on the source document.'),
            ('quoted_lead_time_days', 'number', 'Quoted Lead Time (days)',
             'The single optimistic integer this product replaces with a distribution.'),
            ('warranty_period_months', 'number', 'Warranty Period (months)',
             'Stated warranty duration.'),
            ('submittal_number', 'text', 'Submittal Number',
             'Submittal register identifier.'),
            ('submittal_status', 'text', 'Submittal Status',
             'Review outcome as stated on the submittal.'),
            ('submittal_date', 'date', 'Submittal Date',
             'Date the submittal was transmitted, ISO-8601 in `value_text`.'),
            ('approval_date', 'date', 'Approval Date',
             'Date review was completed, ISO-8601 in `value_text`.'),
            ('purchase_order_number', 'text', 'Purchase Order Number',
             'Purchase order identifier as printed.'),
            ('order_date', 'date', 'Order Date',
             'Date the order was placed, ISO-8601 in `value_text`.'),
            ('promised_delivery_date', 'date', 'Promised Delivery Date',
             'Vendor-stated delivery date, ISO-8601 in `value_text`.'),
            ('required_on_site_date', 'date', 'Required On-Site Date',
             'Need-by date as stated on the source document.')
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
