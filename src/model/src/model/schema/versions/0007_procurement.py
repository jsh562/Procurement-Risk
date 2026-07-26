"""procurement

Revision ID: 0007
Revises: 0006
Create Date: 2026-07-26 09:16:44.052596

One helper function, two tables, one view, and one deferrable foreign key in a
single revision, and **this one cannot be split**. `data-model.md` §Migration
Sequence assigns `fn_is_legal_lifecycle_transition`, `purchase_order_line`,
`lifecycle_event`, the deferred closing FK, and
`v_purchase_order_line_current_state` all to `0007` with the note "Line and event
must be created in one migration -- the FK cycle cannot be split".

The cycle is real, not stylistic: `lifecycle_event.po_line_id` references
`purchase_order_line`, and `purchase_order_line.closing_event_id` references
`lifecycle_event`. Splitting across revisions leaves an intermediate head at
which one of the two references cannot be declared, and a forward-only chain
(TR-002) cannot repair such a state -- it can only add to it. The order *within*
this revision is therefore: function, line table, event table, then the closing
FK by `ALTER TABLE`, which is the only sequence in which every referent exists
when it is needed.

TR-020 -- the line carries project reference, vendor reference, material
category, order date, need-by date, criticality, lifecycle state, and the
open-or-closed indicator, plus the roster hash (TR-024) and the four extra match
fields `data-model.md` names for E009's identity resolution (`manufacturer`,
`part_number`, `description`, `quantity`, `unit_of_measure`). Those four are here
rather than in E009's own migration because E009 resolves identity *between* an
extracted value and a purchase-order line: with no manufacturer or part number on
the line there is nothing to match against, and adding them later would mean
either a rewrite or a nullable pair that the normalization checks on
`resolved_entity` could not rely on.

TR-025 -- the identifier formats E001 froze: `^PRJ-[0-9]{3}$` and
`^VND-[0-9]{3}$`, as named `CHECK` constraints on NOT NULL columns. TR-024 adds
`^sha256:[0-9a-f]{64}$` for the roster hash, the same literal `0003` uses for
`document.roster_hash` and `0008` will use for `forecast_run`.

TR-023 -- `need_by_date >= order_date`. Not `>`: a same-day need-by is a real
procurement case (an expedited order placed and required the same day) and
excluding it would force a writer to shift one of the two dates.

TR-066 -- an open line persists with **no** lifecycle event at all and is
identifiable as right-censored. Nothing in this revision requires an event to
exist: `fk_lifecycle_event__line` points the other way, and the closing FK's
referencing triple is all-null on an open line, which `MATCH FULL` accepts
outright. "Identifiable" is carried three ways -- `is_closed = false`,
`closing_event_id IS NULL` (the two tied together by
`ck_pol__closed_iff_closing_event`), the partial index
`ix_purchase_order_line__open`, and the `is_right_censored` column of
`v_purchase_order_line_current_state`. Censoring is the modelling fact the whole
survival arm rests on, so it is a first-class state here rather than an absence
inferred at read time.

TR-021, TR-067 -- the closed-line rule is carried by
`fk_purchase_order_line__closing_event`, `DEFERRABLE INITIALLY DEFERRED`, the
**only** deferrable constraint in the schema. See the ALTER TABLE below for why
the two extra referencing columns are generated and what that buys.

TR-022 -- repeated review cycles are an ordered event sequence, with
`fk_lifecycle_event__chain` making a broken or forged history unrepresentable
rather than merely detectable.
"""

from collections.abc import Sequence

from alembic import op

from model.schema.helpers import FN_IS_LEGAL_LIFECYCLE_TRANSITION

# Revision identifiers, used by Alembic.
#
# TR-004: `revision` doubles as the four-digit filename prefix -- 0001-0099 is
# this epic's reserved block, 0100-0199 is E004's. Ordering is `down_revision`
# and only `down_revision`; the numbers are never compared to decide what runs
# next, so a gap or an out-of-order id is a naming defect, not a broken chain.
revision: str = "0007"
down_revision: str | Sequence[str] | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the transition helper, the two procurement tables, and the closing FK.

    TR-003: re-application is a no-op by virtue of Alembic's `alembic_version`
    bookkeeping. Do not add a "have I already run?" guard here.

    Every constraint is named, following `pk_<table>`, `uq_<table>__<purpose>`,
    `fk_<table>__<purpose>`, `ck_<table>__<rule>`, and `ix_<table>__<purpose>`.
    Two mechanical reasons: a server-generated name cannot be relied on by a
    later forward migration's `ALTER TABLE ... DROP CONSTRAINT`, and a test
    asserting *which* rule rejected a row matches on the constraint name -- never
    on message text, which is locale- and version-dependent. `pol` is the
    abbreviation `data-model.md` declares for `purchase_order_line`'s checks:
    `ck_purchase_order_line__need_by_not_before_order` is 48 characters and
    survives, but `ck_purchase_order_line__closed_iff_closing_event` and its
    neighbours crowd PostgreSQL's 63-byte identifier limit, and a silently
    truncated name is one a test can never match.

    TR-039: every `CHECK` here that constrains a single column's value domain
    sits on a `NOT NULL` column, so none can pass vacuously. A `CHECK` rejects
    only on *false*, and any comparison against NULL is NULL, which a check
    *accepts* -- so on a nullable column `btrim(col, ...) <> ''` yields NULL for
    exactly the blank row it exists to catch and lets it through. The two checks
    on nullable columns in this revision are biconditionals or have their null
    branch closed by a sibling check; both are called out where they appear. Every
    check was verified by inserting the violating row, never by reading the
    expression.
    """
    # --- fn_is_legal_lifecycle_transition (T028) ----------------------------
    #
    # The DDL lives in `model.schema.helpers` rather than inline; see that
    # module's docstring for why, and for the recorded restriction that changing
    # one of these functions is a two-step forward migration under a new name.
    # Created first, because `ck_lifecycle_event__legal_transition` below calls
    # it and a `CHECK` referencing a missing function fails at DDL time.
    op.execute(FN_IS_LEGAL_LIFECYCLE_TRANSITION)

    # --- purchase_order_line (T029) -----------------------------------------
    #
    # Created before `lifecycle_event` so `fk_lifecycle_event__line` has a
    # referent, and *without* the closing FK, which is added by ALTER TABLE after
    # the event table exists. That ordering is forced by the cycle described in
    # the module docstring.
    op.execute(
        """
        CREATE TABLE purchase_order_line (
            po_line_id uuid NOT NULL,

            -- TR-025: the formats E001 froze. Regex checks on NOT NULL columns,
            -- so neither can be satisfied by absence.
            project_id text NOT NULL,
            vendor_id text NOT NULL,

            -- The natural key, with `line_number`. E005 regenerates the roster,
            -- so `uq_purchase_order_line__natural` is what makes a reload
            -- idempotent rather than duplicating every line.
            po_number text NOT NULL,
            line_number integer NOT NULL,

            material_category text NOT NULL,

            -- The four extra match fields E009 needs, per the module docstring.
            -- All NOT NULL and non-blank: a line with a blank manufacturer or
            -- part number cannot participate in identity resolution at all, and
            -- storing it as blank rather than refusing it would make a
            -- non-match indistinguishable from a missing field -- exactly the
            -- silent failure Principle III says to convert into a visible one.
            description text NOT NULL,
            manufacturer text NOT NULL,
            part_number text NOT NULL,
            quantity numeric NOT NULL,
            unit_of_measure text NOT NULL,

            -- Calendar anchors, so `date` rather than `timestamptz`: a need-by
            -- date is a day, not an instant, and giving it a time zone would
            -- make "is this line late" depend on where the reader sits.
            order_date date NOT NULL,
            need_by_date date NOT NULL,

            -- Ordinal band, 5 = most critical. `smallint` and a range check
            -- rather than a lookup table: the band has no attributes to carry
            -- and E017's override is additive against this column.
            criticality smallint NOT NULL,

            lifecycle_state text NOT NULL,

            -- TR-020's open-or-closed indicator, and TR-066's censoring flag.
            -- Stored rather than derived, because the whole worklist filters on
            -- it (`ix_purchase_order_line__open`) and because it is one half of
            -- the biconditional that ties closure to a named closing event.
            is_closed boolean NOT NULL,

            -- TR-021: the pointer at the terminal delivery event. Nullable, and
            -- null is the *ordinary* state -- most lines are open.
            closing_event_id uuid,

            -- TR-065 rung 1, and the reason this shape works at all. Both extra
            -- referencing columns of the closing FK are GENERATED ... STORED
            -- from `closing_event_id`, so all three are null together on an open
            -- line and all three are non-null together on a closed one. `MATCH
            -- FULL` then accepts the all-null case outright and enforces the
            -- full triple otherwise, with no partial-match skip to reason about
            -- -- a partially-null triple is not merely forbidden, it is
            -- unrepresentable, because a writer cannot set these two columns.
            --
            -- The alternative -- two plain nullable columns plus
            -- `ck_pol__closing_terminal_true` and
            -- `ck_pol__closing_triple_null_together` -- would put the same
            -- invariant behind two more checks on nullable columns, which is
            -- exactly the shape §Nullable-Column Checks exists to keep small.
            -- See the ALTER TABLE below for the cost this choice carries
            -- (HINT-003).
            closing_event_po_line_id uuid
                GENERATED ALWAYS AS
                (CASE WHEN closing_event_id IS NULL THEN NULL ELSE po_line_id END)
                STORED,
            closing_event_terminal boolean
                GENERATED ALWAYS AS
                (CASE WHEN closing_event_id IS NULL THEN NULL ELSE true END)
                STORED,

            -- TR-024: the roster this line was generated from, in the frozen
            -- format. E005's roster is regenerable, so this is what ties a
            -- stored line back to the exact input that produced it -- Principle
            -- I at the storage boundary.
            roster_hash text NOT NULL,

            created_at timestamptz NOT NULL DEFAULT now(),

            CONSTRAINT pk_purchase_order_line PRIMARY KEY (po_line_id),

            -- TR-025. Anchored `^`/`$` -- without both, 'xPRJ-001y' would match.
            CONSTRAINT ck_pol__project_id_format
                CHECK (project_id ~ '^PRJ-[0-9]{3}$'),
            CONSTRAINT ck_pol__vendor_id_format
                CHECK (vendor_id ~ '^VND-[0-9]{3}$'),

            -- The trim set is spelled out because single-argument `btrim` strips
            -- *spaces only*: a `po_number` of one tab would otherwise satisfy a
            -- bare `btrim(po_number) <> ''` while naming nothing.
            --
            -- No `coalesce` wrapper on any presence check in this table, and
            -- none is needed -- every column carrying one is NOT NULL, so the
            -- comparison is a definite boolean. A `coalesce` would be required
            -- the moment one of them was relaxed to nullable.
            --
            -- **Deviation from data-model.md, deliberate (TR-083).** That
            -- artifact spells the trim set `E' \\t\\n\\r\\f\\v'`, which
            -- PostgreSQL does not read as whitespace: its escape-string syntax
            -- has no `\\v`, and an unrecognized escape drops the backslash and
            -- keeps the character, so `E'\\v'` is the *letter* `v`. Written as
            -- declared, these checks would admit a vertical-tab-only value (11
            -- missing from the set) and reject a legitimate value of `vvv` (118
            -- wrongly in it) -- one typo producing both a hole and a false
            -- rejection. `\\u000B` is the character the artifact means; octal
            -- `\\013` is equivalent. Revisions `0004` and `0006` record the same
            -- deviation, and every presence check data-model.md declares is
            -- affected.
            CONSTRAINT ck_pol__po_number_present
                CHECK (btrim(po_number, E' \\t\\n\\r\\f\\u000B') <> ''),

            -- Line numbers are one-based, as printed on the purchase order.
            CONSTRAINT ck_pol__line_number_positive
                CHECK (line_number >= 1),

            CONSTRAINT ck_pol__material_category_present
                CHECK (btrim(material_category, E' \\t\\n\\r\\f\\u000B') <> ''),
            CONSTRAINT ck_pol__description_present
                CHECK (btrim(description, E' \\t\\n\\r\\f\\u000B') <> ''),
            CONSTRAINT ck_pol__manufacturer_present
                CHECK (btrim(manufacturer, E' \\t\\n\\r\\f\\u000B') <> ''),
            CONSTRAINT ck_pol__part_number_present
                CHECK (btrim(part_number, E' \\t\\n\\r\\f\\u000B') <> ''),
            CONSTRAINT ck_pol__uom_present
                CHECK (btrim(unit_of_measure, E' \\t\\n\\r\\f\\u000B') <> ''),

            -- Strictly positive. A zero-quantity line is not a line, and
            -- `numeric` (not `integer`) because quantities are measured as well
            -- as counted -- 12.5 linear metres of conduit is a real line.
            CONSTRAINT ck_pol__quantity_positive
                CHECK (quantity > 0),

            -- TR-023, OBJ4 VC1. `>=`, not `>` -- see the module docstring.
            -- Both columns are NOT NULL, so this cannot pass on a missing date.
            CONSTRAINT ck_pol__need_by_not_before_order
                CHECK (need_by_date >= order_date),

            -- `BETWEEN` is inclusive at both ends: 1 and 5 are both valid bands.
            CONSTRAINT ck_pol__criticality_band
                CHECK (criticality BETWEEN 1 AND 5),

            -- The same seven states `ck_lifecycle_event__to_state` declares. The
            -- two lists are duplicated rather than shared, because a `CHECK`
            -- cannot read a lookup table and an enum type cannot be extended
            -- usefully inside a forward-only chain (data-model.md §field_vocabulary
            -- records the same reasoning). Adding `cancelled` therefore touches
            -- both, and the migration that does so must touch both.
            CONSTRAINT ck_pol__lifecycle_state
                CHECK (lifecycle_state IN (
                    'submitted',
                    'under_review',
                    'revise_and_resubmit',
                    'approved',
                    'released_for_fabrication',
                    'shipped',
                    'delivered'
                )),

            -- TR-066, invariant 14. The first half of "an open line is
            -- right-censored": closure and the closing pointer agree, in both
            -- directions. A biconditional, so `is_closed = true` with no pointer
            -- is refused as firmly as a pointer on an open line.
            --
            -- This is a check on the nullable `closing_event_id`, and it cannot
            -- pass vacuously: `closing_event_id IS NOT NULL` is a definite
            -- boolean whatever the column holds, and `is_closed` is NOT NULL, so
            -- the equality is never NULL. Registered in §Nullable-Column Checks.
            --
            -- Note what this check does *not* do: it cannot verify the pointed-at
            -- event exists or is terminal, because a `CHECK` cannot read another
            -- table. That half is the deferred FK's, below -- and the two are
            -- only worth anything together. This one is immediate, so a closed
            -- line without a pointer fails at INSERT; the FK is deferred, so the
            -- pointer's referent is proven at COMMIT.
            CONSTRAINT ck_pol__closed_iff_closing_event
                CHECK (is_closed = (closing_event_id IS NOT NULL)),

            -- The second half. `delivered` is the only terminal state, so
            -- closure and the state cannot disagree. Together with the check
            -- above, `is_closed` is pinned from both sides: an open line has no
            -- closing pointer *and* is not in `delivered`, which is precisely
            -- "no delivery event, right-censored".
            CONSTRAINT ck_pol__closed_iff_delivered
                CHECK (is_closed = (lifecycle_state = 'delivered')),

            -- TR-024, OBJ4 VC5. Lowercase hex only -- `[0-9a-f]`, spelled as a
            -- character class rather than relying on a locale's notion of a hex
            -- digit, so the comparison is byte-wise and stays true across an ICU
            -- upgrade. Exactly 64 digits, anchored, with the literal `sha256:`
            -- prefix: an unprefixed digest and a truncated one are both refused.
            CONSTRAINT ck_pol__roster_hash_format
                CHECK (roster_hash ~ '^sha256:[0-9a-f]{64}$'),

            -- The natural key, for E005's generator and for idempotent reloads.
            -- `po_line_id` is a surrogate a job invents; this is the identity the
            -- source document actually carries.
            CONSTRAINT uq_purchase_order_line__natural
                UNIQUE (project_id, po_number, line_number)
        )
        """
    )

    # Plain b-tree indexes; separate statements because `CREATE TABLE` admits
    # only constraints inline.
    #
    # The per-vendor index T030's description also names is this one -- it is an
    # index on `purchase_order_line`, so it is created here with its table rather
    # than after `lifecycle_event`. Per-vendor aggregation is E014's and E019's
    # read pattern (TR-020 rationale).
    op.execute("CREATE INDEX ix_purchase_order_line__vendor ON purchase_order_line (vendor_id)")

    # Worklist ordering: one project's lines by when they are needed.
    op.execute(
        """
        CREATE INDEX ix_purchase_order_line__project_need_by
        ON purchase_order_line (project_id, need_by_date)
        """
    )

    # TR-066: the right-censored set, which is the worklist's default filter.
    # Partial rather than a plain index on `(is_closed, need_by_date)`: the
    # predicate is the query's own, so the index holds only open lines and the
    # planner can use it without rechecking `is_closed`. It also makes "which
    # lines are censored" a readable schema object rather than a convention in a
    # query somewhere.
    op.execute(
        """
        CREATE INDEX ix_purchase_order_line__open
        ON purchase_order_line (need_by_date)
        WHERE NOT is_closed
        """
    )

    # --- lifecycle_event (T030) ---------------------------------------------
    #
    # TR-022: repeated review cycles as an ordered sequence. `sequence_no` is the
    # order and `occurred_at` is the timestamp, and they are separate on purpose
    # -- two events can share a wall-clock second, and the position in the
    # history is what the chain FK reasons about. That they agree
    # (`occurred_at` increasing with `sequence_no`) is cross-row and therefore
    # disclosed as gap G-4, covered by a test rather than claimed here.
    op.execute(
        """
        CREATE TABLE lifecycle_event (
            event_id uuid NOT NULL,
            po_line_id uuid NOT NULL,

            -- The position in this line's history, one-based and contiguous.
            -- Contiguity is not asserted by a check -- it falls out of
            -- `fk_lifecycle_event__chain`: event n's generated
            -- `prev_sequence_no` is n-1, and the FK requires that row to exist,
            -- so a gap is unrepresentable and sequence 1 is the only entry
            -- point.
            sequence_no integer NOT NULL,

            -- Generated, not written. It exists so the chain FK can be
            -- declarative: a foreign key can only compare columns, so "the
            -- previous event on this line" has to *be* a column. Deriving it
            -- rather than accepting it is what stops a writer pointing event 5
            -- at event 2 and inventing a history.
            prev_sequence_no integer
                GENERATED ALWAYS AS
                (CASE WHEN sequence_no = 1 THEN NULL ELSE sequence_no - 1 END)
                STORED,

            -- NULL exactly on the opening event. Nullable by necessity: the
            -- first event has no predecessor state, and inventing a sentinel
            -- like 'none' would put a non-state into the closed set and into the
            -- transition table.
            from_state text,

            to_state text NOT NULL,

            -- TR-021: the flag the closing FK carries into its referenced key.
            -- Stored rather than derived so it can be part of a unique key --
            -- a foreign key must reference actual columns, and this is the
            -- column that makes "the closing pointer cannot name a non-terminal
            -- event" a referential fact.
            is_terminal boolean NOT NULL,

            occurred_at timestamptz NOT NULL,

            -- Free text, uncontrolled by design: a reviewer's comment has no
            -- schema and no downstream consumer that parses it.
            note text,

            CONSTRAINT pk_lifecycle_event PRIMARY KEY (event_id),

            -- One-based positions.
            CONSTRAINT ck_lifecycle_event__sequence_positive
                CHECK (sequence_no >= 1),

            -- The same seven states as `ck_pol__lifecycle_state`; see the note
            -- there on why the list is duplicated rather than shared.
            CONSTRAINT ck_lifecycle_event__to_state
                CHECK (to_state IN (
                    'submitted',
                    'under_review',
                    'revise_and_resubmit',
                    'approved',
                    'released_for_fabrication',
                    'shipped',
                    'delivered'
                )),

            -- Invariant 10: the terminal flag cannot be forged. A biconditional,
            -- so `is_terminal = true` on a `shipped` event is refused *and*
            -- `is_terminal = false` on a `delivered` event is refused. That
            -- second direction is the one that matters for TR-021: without it, a
            -- writer could deliver a line and leave the flag false, and the
            -- closing FK -- which matches on `is_terminal = true` -- would have
            -- no row to find while the line looked delivered.
            --
            -- Both operands are NOT NULL, so this is never NULL and never
            -- vacuous. It is also what makes the deferred FK's terminal-flag
            -- carry meaningful: the FK proves the pointed-at event claims to be
            -- terminal, and this check proves the claim is true.
            CONSTRAINT ck_lifecycle_event__terminal_iff_delivered
                CHECK (is_terminal = (to_state = 'delivered')),

            -- The first of three checks on the nullable `from_state`, and the
            -- one that makes the other two safe. A biconditional against the NOT
            -- NULL `sequence_no`, so `IS NULL` is a definite boolean and the
            -- check is never NULL: position 1 has no predecessor state, and
            -- every later position must state one.
            CONSTRAINT ck_lifecycle_event__first_has_no_predecessor
                CHECK ((sequence_no = 1) = (from_state IS NULL)),

            -- The only legal opening event. This is the `(NULL, 'submitted')`
            -- row of data-model.md's transition table, and it is a check rather
            -- than a row in `fn_is_legal_lifecycle_transition` because that
            -- function is STRICT and yields NULL on a null argument -- which a
            -- CHECK accepts. Closing the null branch here, in a definite
            -- boolean, is what stops a line's history starting at `shipped`.
            CONSTRAINT ck_lifecycle_event__first_is_submitted
                CHECK (from_state IS NOT NULL OR to_state = 'submitted'),

            -- Invariant 12, TR-022: only legal transitions exist. The
            -- `from_state IS NULL` guard is load-bearing -- the helper is STRICT,
            -- so calling it with NULL yields NULL and the row would be
            -- *accepted*. The guard makes the opening event's branch explicit,
            -- and the two checks above are what close it, so nothing here passes
            -- vacuously.
            CONSTRAINT ck_lifecycle_event__legal_transition
                CHECK (
                    from_state IS NULL
                    OR fn_is_legal_lifecycle_transition(from_state, to_state)
                ),

            -- Rework loops repeat *states*, not positions (TR-022, OBJ4 VC3):
            -- two rejections give two `revise_and_resubmit -> submitted` pairs
            -- at four distinct sequence numbers, and this is what keeps them
            -- distinct and separately recoverable.
            CONSTRAINT uq_lifecycle_event__line_sequence
                UNIQUE (po_line_id, sequence_no),

            -- The chain FK's target. Redundant against the unique key above by
            -- design: a composite foreign key must reference a unique key
            -- carrying *every* column it compares, and `(po_line_id,
            -- sequence_no)` alone cannot carry `to_state`.
            CONSTRAINT uq_lifecycle_event__line_sequence_state
                UNIQUE (po_line_id, sequence_no, to_state),

            -- TR-067: the closing FK's target, and the point at which the
            -- terminal flag enters a referenced key. Redundant against
            -- `pk_lifecycle_event` for the same reason -- the primary key alone
            -- cannot carry `po_line_id` or `is_terminal`, so a pointer
            -- referencing only `event_id` could name another line's event, or a
            -- non-terminal one.
            CONSTRAINT uq_lifecycle_event__id_line_terminal
                UNIQUE (event_id, po_line_id, is_terminal),

            -- Events belong to a line. RESTRICT: dropping a line with history
            -- must be an explicit, ordered operation, not a silent cascade
            -- (§Referential Actions). Deleting a line therefore means deleting
            -- its events and the line in one transaction, events first.
            CONSTRAINT fk_lifecycle_event__line
                FOREIGN KEY (po_line_id)
                REFERENCES purchase_order_line (po_line_id)
                ON DELETE RESTRICT,

            -- Invariant 11, TR-022. The self-referencing composite FK:
            -- `from_state` must be the `to_state` of the immediately preceding
            -- event *on the same line*. Three columns, so it proves all three
            -- facts at once -- same line, immediately previous position, and
            -- states that meet -- where a check could prove none of them.
            --
            -- **Deviation from data-model.md, deliberate and verified
            -- (TR-083).** That artifact declares this FK `MATCH FULL`. Declared
            -- `MATCH SIMPLE` here -- the default, written explicitly rather than
            -- omitted -- because `MATCH FULL` makes the *opening event
            -- unrepresentable*, and with it every line's entire history.
            --
            -- On the opening event `prev_sequence_no` is NULL (generated, from
            -- `sequence_no = 1`) and `from_state` is NULL (forced by
            -- `ck_lifecycle_event__first_has_no_predecessor`), while
            -- `po_line_id` is NOT NULL -- so the referencing triple is
            -- *partially* null. MATCH FULL permits all-null and requires
            -- all-matching, and rejects everything in between: it does not skip
            -- a partially-null row, it refuses it. Verified against PG 16 --
            -- under MATCH FULL a sequence-1 event is rejected with
            -- ForeignKeyViolation naming this constraint (SQLSTATE 23503), so no
            -- line could hold a single event; under MATCH SIMPLE the same
            -- insert is accepted, the chained sequence-2 event is accepted, and a
            -- forged `from_state` at sequence 3 is still rejected by this
            -- constraint.
            --
            -- The skip MATCH SIMPLE introduces is confined to exactly the rows
            -- that have no predecessor, and confined *by a constraint* rather
            -- than by argument. MATCH SIMPLE skips the check when any
            -- referencing column is null; `po_line_id` is NOT NULL,
            -- `prev_sequence_no` is generated and null iff `sequence_no = 1`,
            -- and `from_state` is null iff `sequence_no = 1` by the immediate
            -- biconditional `ck_lifecycle_event__first_has_no_predecessor`. So
            -- the null pattern is a function of `sequence_no` alone, a writer
            -- cannot produce a null `from_state` at any later position, and the
            -- skipped rows are precisely the ones for which "the previous event"
            -- does not exist. `ck_lifecycle_event__first_is_submitted` then
            -- closes what the FK cannot see about those rows.
            --
            -- The alternative that would preserve MATCH FULL is a third
            -- generated column, `prev_po_line_id GENERATED ALWAYS AS (CASE WHEN
            -- sequence_no = 1 THEN NULL ELSE po_line_id END) STORED`, making the
            -- triple all-null together the way the closing FK's triple is. It is
            -- strictly stronger -- it would reject a null `from_state` at
            -- sequence 5 even if the biconditional above were ever dropped --
            -- and it is recorded here as the named strengthening if that check is
            -- ever relaxed. Not taken now: it adds a column to a normative column
            -- list to guard against the removal of a constraint in the same
            -- table, where changing one keyword and pinning the null pattern to
            -- an existing check is the smaller change.
            --
            -- ON UPDATE RESTRICT rather than CASCADE, unlike every other
            -- composite FK in the schema: renumbering a line's sequence is a
            -- rewrite of its history, not a correction to propagate
            -- (§Referential Actions).
            --
            -- **Cost, recorded (data-model.md §lifecycle_event).** This
            -- constraint is *not* deferrable, so a line's events must be
            -- inserted in ascending `sequence_no` and deleted in descending
            -- order. That is a reasonable demand on a generator, and it buys a
            -- fully declarative event chain. **If E005 later needs unordered
            -- bulk load, this is the one constraint to drop** -- dropping it
            -- costs the chain guarantee and nothing else, and no other object in
            -- the schema depends on it.
            CONSTRAINT fk_lifecycle_event__chain
                FOREIGN KEY (po_line_id, prev_sequence_no, from_state)
                REFERENCES lifecycle_event (po_line_id, sequence_no, to_state)
                MATCH SIMPLE
                ON DELETE RESTRICT
                ON UPDATE RESTRICT
        )
        """
    )

    # Per-line event retrieval in time order -- the detail view's read, and the
    # input to E007's days-in-state derivation.
    op.execute(
        """
        CREATE INDEX ix_lifecycle_event__line_occurred
        ON lifecycle_event (po_line_id, occurred_at)
        """
    )

    # Terminal-event lookup for closure. Partial: terminal events are one row per
    # closed line out of ~1,500, so the index is a fraction of the table's size
    # and holds exactly the rows the closure path looks for. It also indexes the
    # referencing side of nothing -- `fk_purchase_order_line__closing_event`
    # points *at* this table, and PostgreSQL indexes neither side of a foreign
    # key automatically, so this is what keeps the deferred check from scanning.
    op.execute(
        """
        CREATE INDEX ix_lifecycle_event__terminal
        ON lifecycle_event (po_line_id)
        WHERE is_terminal
        """
    )

    # --- v_purchase_order_line_current_state (T030) -------------------------
    #
    # The read surface for gap G-3 -- "does `purchase_order_line.lifecycle_state`
    # agree with the highest-sequence event's `to_state`, for open lines" -- which
    # is cross-row and therefore *not* carried by a constraint. The view is what
    # makes the disagreement observable in one read, so the covering test is an
    # assertion about data rather than a join a test author has to reinvent.
    #
    # It is also G-3's recorded production-scale alternative: if `lifecycle_state`
    # is ever dropped from the line, `current_state` here becomes the answer and
    # no consumer has to change where it looks.
    #
    # LEFT JOIN LATERAL, not an inner join: a line with **no events at all** must
    # still appear, with `current_state` NULL. That is TR-066's right-censored
    # line, and an inner join would make the one row the censoring test cares
    # about invisible.
    #
    # `ORDER BY sequence_no DESC LIMIT 1` inside the lateral is the definition of
    # "current" -- highest position wins. It orders by `sequence_no` and not by
    # `occurred_at`: position is the authority (the chain FK enforces it), while
    # timestamp agreement is only disclosed as G-4. This is unrelated to
    # §forecast_run's prohibition on `ORDER BY created_at DESC LIMIT 1`, which
    # forbids *selecting the active run* by recency; here the ordering column is
    # the ordering the schema itself guarantees.
    op.execute(
        """
        CREATE VIEW v_purchase_order_line_current_state AS
            SELECT
                line.po_line_id,
                line.project_id,
                line.vendor_id,
                line.po_number,
                line.line_number,
                line.need_by_date,

                -- The stored state, as written on the line.
                line.lifecycle_state,

                -- The derived state: the `to_state` of the highest-sequence
                -- event. NULL when the line has no events yet.
                latest.to_state AS current_state,
                latest.sequence_no AS latest_sequence_no,
                latest.occurred_at AS latest_occurred_at,

                line.is_closed,
                line.closing_event_id,

                -- TR-066: right-censored means no delivery event. Written as
                -- `closing_event_id IS NULL` rather than `NOT is_closed`
                -- because the closing pointer is the column the deferred FK
                -- actually proves; `ck_pol__closed_iff_closing_event` makes the
                -- two equivalent, so this exposes the one with a referent
                -- behind it.
                (line.closing_event_id IS NULL) AS is_right_censored
            FROM purchase_order_line AS line
            LEFT JOIN LATERAL (
                SELECT
                    event.to_state,
                    event.sequence_no,
                    event.occurred_at
                FROM lifecycle_event AS event
                WHERE event.po_line_id = line.po_line_id
                ORDER BY event.sequence_no DESC
                LIMIT 1
            ) AS latest ON true
        """
    )

    # --- fk_purchase_order_line__closing_event (T031) -----------------------
    #
    # TR-021, TR-067, OBJ4 VC2, invariant 13. **The only deferrable constraint in
    # the whole schema**, and the last statement in this revision because it is
    # the second half of the cycle: it can only be declared once
    # `lifecycle_event` and its `uq_lifecycle_event__id_line_terminal` exist.
    #
    # What the three referencing columns buy, one each:
    #
    #   closing_event_id          the event exists
    #   closing_event_po_line_id  it belongs to *this* line, not another
    #   closing_event_terminal    it is terminal -- and
    #                             `ck_lifecycle_event__terminal_iff_delivered`
    #                             makes that flag unforgeable, so "terminal"
    #                             means `to_state = 'delivered'` and cannot mean
    #                             anything else
    #
    # So the pointer cannot name a non-terminal event, cannot name another line's
    # event, and cannot dangle. None of that is a trigger, and none of it can be
    # disabled, skipped by a bulk-load path, or left un-revalidated by a restore.
    #
    # DEFERRABLE INITIALLY DEFERRED because the invariant is genuinely
    # end-of-transaction: the insert order is line -> events -> commit, and at the
    # moment the closed line is written its terminal event does not exist yet.
    # An immediate constraint would make the correct write order impossible in
    # either direction -- the line needs the event and the event needs the line.
    # STF-004 records this: OBJ4 VC2's original insert-time wording could not hold
    # under this mechanism and was restated at the commit boundary.
    #
    # **HINT-003, and a recorded verification item.** `ON DELETE` must stay `NO
    # ACTION`. PostgreSQL refuses `SET NULL` and `SET DEFAULT` against a
    # generated column -- verified against PG 16, which rejects the ALTER with
    # `invalid ON DELETE action for foreign key constraint containing generated
    # column` (SQLSTATE 42601) -- and two of the three referencing columns are
    # generated. This is not a preference that a later revision may tidy up: the
    # generated-column shape (which is what makes the triple null exactly when
    # the line is open) and `NO ACTION` come as a pair. Deleting a line therefore
    # means deleting its events and the line in one transaction; the deferral is
    # what makes that possible, since the line's pointer is dangling in the
    # middle of it.
    #
    # `MATCH FULL` is doing real work here rather than stating intent. An open
    # line's triple is all-null, which MATCH FULL accepts with no referent
    # required; a closed line's is fully populated, and MATCH FULL enforces every
    # column. Because the two extra columns are generated, the partially-null
    # case MATCH SIMPLE would silently skip is unrepresentable -- a writer has no
    # way to produce it.
    op.execute(
        """
        ALTER TABLE purchase_order_line
            ADD CONSTRAINT fk_purchase_order_line__closing_event
            FOREIGN KEY (closing_event_id, closing_event_po_line_id, closing_event_terminal)
            REFERENCES lifecycle_event (event_id, po_line_id, is_terminal)
            MATCH FULL
            ON DELETE NO ACTION
            ON UPDATE NO ACTION
            DEFERRABLE INITIALLY DEFERRED
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
