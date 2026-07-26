"""The two procurement tables, their event chain, and the schema's one deferrable FK.

Everything here is migration `0007`: `purchase_order_line`, `lifecycle_event`,
`v_purchase_order_line_current_state`, and
`fk_purchase_order_line__closing_event`. Three groups, one per task:

* **T032 -- censoring and the deferred closing pointer (TR-021, TR-066,
  TR-067).** An open line persists with *no* lifecycle event and is identifiable
  as right-censored, which is the modelling fact the whole survival arm rests on.
  Then the schema's only `DEFERRABLE INITIALLY DEFERRED` constraint, proven in
  the two steps `conftest.force_constraints_immediate` documents -- because this
  is the one place in the tier where a test can pass having asserted nothing.
* **T033 -- the event chain, rework loops, and two disclosed gaps (TR-022).**
  Two review cycles on one line, recoverable in `sequence_no` order; the
  transition helper refusing an illegal edge; the terminal flag unforgeable in
  *both* directions; and gaps G-3 and G-4 asserted as the disclosures they are.
* **T034 -- dates and the frozen identifier formats (TR-023, TR-024, TR-025).**
  An inverted order/need-by pair refused and a same-day pair *accepted*, plus
  malformed `PRJ-###`, `VND-###`, and roster hashes.

**Why the deferral needs two steps, and why one of them alone is worthless.**
`conftest.db_session` runs every test inside an outer transaction that is rolled
back in teardown, so no `COMMIT` is ever reached and a deferred constraint never
fires on its own (HINT-002). A test that wrote a violating row and asserted
nothing happened would be green and empty. So
`test_the_closing_pointer_is_deferred_to_the_commit_boundary_and_then_enforced`
asserts both halves: the dangling pointer is **accepted** mid-transaction while
its referent provably does not exist -- that is the deferral -- and
`force_constraints_immediate` then raises `ForeignKeyViolation` naming
`fk_purchase_order_line__closing_event` -- that is the enforcement. Step 1 alone
proves nothing; step 2 alone would pass identically against an immediate
constraint. Both were verified by removing the other: dropping the forced check
makes the enforcement assertion report "accepted", and pointing the same line at
a legitimately terminal event makes the forced check pass, which is asserted as
its own test rather than left as a claim.

**Two disclosed gaps, asserted as disclosures.** `data-model.md`'s
gap-disclosure record is the authority for what each one claims, and neither
test asserts a guarantee the schema does not make:

* **G-3** -- an open line's `lifecycle_state` agreeing with its highest-sequence
  event's `to_state` is **not** enforced; it is cross-row. The disclosure records
  the runtime outcome as "worklist filters on state and history reads disagree",
  with the production-scale alternative of dropping `lifecycle_state` and
  deriving current state through `v_purchase_order_line_current_state`. The test
  asserts the disagreement is accepted and visible in one read of that view --
  and, separately, that the *closed* half **is** carried, by
  `ck_pol__closed_iff_delivered` plus the deferred FK, which is where the gap
  stops.
* **G-4** -- `occurred_at` increasing with `sequence_no` is **not** enforced;
  also cross-row. The disclosure records that "events ordered by `occurred_at`
  and by `sequence_no` can differ, so days-in-state derivations become
  negative". The test asserts exactly that: the out-of-order row is accepted, the
  derived interval *is* negative, and the view still reports the highest
  `sequence_no` as current -- position is the authority the chain FK enforces,
  and the timestamp is not.

**`MATCH SIMPLE` on `fk_lifecycle_event__chain` is deliberate, and T030 found out
why.** `MATCH FULL` refuses a partially-null referencing triple rather than
skipping it, which makes the opening event -- and therefore every line's entire
history -- unrepresentable. The skip is confined by
`ck_lifecycle_event__first_has_no_predecessor`, which pins the null pattern to
`sequence_no = 1`, so this file asserts the opening event inserts, that a null
predecessor at a later position is refused by that check, and that a *forged*
predecessor at a later position is still refused by the FK.

**Never on message text.** Every rejection names the psycopg subclass and the
constraint that must have produced it, through `conftest.assert_rejects`; naming
the class is naming the SQLSTATE, since psycopg derives one class per state. The
exception is `NOT NULL`, which on PostgreSQL 16 carries `column_name` and **no
`constraint_name` at all** (nameable `NOT NULL` constraints arrive in 17); those
assert the column, which is every bit as specific.

**Isolation.** Every test runs on `db_session` and leaves nothing behind, the
`ALTER TABLE` probes included -- DDL is transactional in PostgreSQL, so the
rejected `ON DELETE SET NULL` re-declaration is rolled back with everything else.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

import psycopg
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

#: `conftest.assert_rejects` as seen through its fixture. Requested rather than
#: imported for the reason that fixture's docstring gives: the import form relies
#: on pytest having put this directory on `sys.path`.
RejectionAsserter = Callable[[Session, type[psycopg.Error], str], AbstractContextManager[None]]

#: `conftest.force_constraints_immediate` as seen through its fixture.
ConstraintForcer = Callable[[Session], None]

# --------------------------------------------------------------------------- #
# The state machine, as data-model.md §State Machines declares it
# --------------------------------------------------------------------------- #

#: The clean path to closure: six events, no rework. `delivered` is the only
#: terminal state, so this is the only sequence that can produce a closing event.
PATH_TO_DELIVERED: tuple[str, ...] = (
    "submitted",
    "under_review",
    "approved",
    "released_for_fabrication",
    "shipped",
    "delivered",
)

#: The same path stopped one event short. Used wherever a test needs a real
#: non-terminal event to point at.
PATH_TO_SHIPPED: tuple[str, ...] = PATH_TO_DELIVERED[:-1]

#: Two rework cycles on one line (TR-022, OBJ4 VC3): rejected twice, resubmitted
#: twice, approved on the third review. Nine events, and the pair
#: `revise_and_resubmit -> submitted` appears twice at *distinct positions* --
#: which is what `uq_lifecycle_event__line_sequence` keeps separately
#: recoverable. Nothing bounds the number of cycles: transition 4 returns to a
#: state that already has an outgoing edge.
TWO_REWORK_CYCLES: tuple[str, ...] = (
    "submitted",
    "under_review",
    "revise_and_resubmit",
    "submitted",
    "under_review",
    "revise_and_resubmit",
    "submitted",
    "under_review",
    "approved",
)

#: When the first event of a history happens. Later events default to one day
#: apart, so the fixtures satisfy G-4's monotonicity even though the schema does
#: not require it -- a fixture that violated it by accident would make the G-4
#: test's own perturbation meaningless.
FIRST_EVENT_AT = datetime(2026, 3, 3, 9, 0, tzinfo=UTC)

# --------------------------------------------------------------------------- #
# `pg_constraint` / `pg_attribute` code letters, named
# --------------------------------------------------------------------------- #

#: `confdeltype` / `confupdtype`: `a` is NO ACTION, `r` is RESTRICT.
NO_ACTION = "a"
RESTRICT = "r"

#: `confmatchtype`: `f` is MATCH FULL, `s` is MATCH SIMPLE.
MATCH_FULL = "f"
MATCH_SIMPLE = "s"

#: `attgenerated`: `s` is a STORED generated column; `''` is an ordinary one.
STORED = "s"

# --------------------------------------------------------------------------- #
# Row builders
# --------------------------------------------------------------------------- #

#: A well-formed roster hash in the format E001 froze -- `sha256:` plus 64
#: lowercase hex digits (TR-024). Every valid row below carries this one, so a
#: test aiming at another constraint cannot trip `ck_pol__roster_hash_format` on
#: the way there.
ROSTER_HASH = "sha256:" + "3f2a" * 16

#: The natural key is `(project_id, po_number, line_number)`, so a test needing a
#: second line on the same purchase order varies `line_number` and nothing else.
#: Spelled as constants because a `UniqueViolation` from
#: `uq_purchase_order_line__natural` in a test aimed at some other rule is
#: exactly the misattribution `assert_rejects` exists to catch.
FIRST_LINE_NUMBER = 7
SECOND_LINE_NUMBER = 8

LINE_INSERT = text(
    """
    INSERT INTO purchase_order_line (
        po_line_id, project_id, vendor_id, po_number, line_number,
        material_category, description, manufacturer, part_number,
        quantity, unit_of_measure, order_date, need_by_date, criticality,
        lifecycle_state, is_closed, closing_event_id, roster_hash
    )
    VALUES (
        :po_line_id, :project_id, :vendor_id, :po_number, :line_number,
        :material_category, :description, :manufacturer, :part_number,
        :quantity, :unit_of_measure, :order_date, :need_by_date, :criticality,
        :lifecycle_state, :is_closed, :closing_event_id, :roster_hash
    )
    """
)

#: The two extra referencing columns of the closing FK are absent from the insert
#: above and cannot be added to it: they are `GENERATED ALWAYS ... STORED`, and
#: PostgreSQL refuses a write to either (`GeneratedAlways`, SQLSTATE 428C9 --
#: asserted by `test_a_writer_cannot_set_either_generated_referencing_column`).
#: That refusal is what makes a partially-null triple unrepresentable rather than
#: merely forbidden, which is in turn what lets the FK be `MATCH FULL` with no
#: partial-match case to reason about.
EVENT_INSERT = text(
    """
    INSERT INTO lifecycle_event (
        event_id, po_line_id, sequence_no, from_state, to_state,
        is_terminal, occurred_at, note
    )
    VALUES (
        :event_id, :po_line_id, :sequence_no, :from_state, :to_state,
        :is_terminal, :occurred_at, :note
    )
    """
)


def line_row(*, line_number: int = FIRST_LINE_NUMBER, **overrides: Any) -> dict[str, Any]:
    """A valid **open** `purchase_order_line` row -- no closing pointer, not closed.

    Perturbing exactly one field of an otherwise-valid row is what makes a
    rejection attributable. Break two at once and PostgreSQL reports whichever
    rule it evaluated first, so the test names one constraint and is satisfied by
    another.

    Open is the default because it is the ordinary state: most lines are open,
    and TR-066 makes "open with no event at all" a first-class state rather than
    an absence inferred at read time.
    """
    row: dict[str, Any] = {
        "po_line_id": uuid4(),
        "project_id": "PRJ-001",
        "vendor_id": "VND-014",
        "po_number": "PO-88213",
        "line_number": line_number,
        "material_category": "piping",
        "description": '6" carbon steel pipe, seamless',
        "manufacturer": "Grinnell",
        "part_number": "GR-2001-06",
        "quantity": 12.5,
        "unit_of_measure": "m",
        "order_date": date(2026, 3, 2),
        "need_by_date": date(2026, 6, 1),
        "criticality": 4,
        "lifecycle_state": "submitted",
        "is_closed": False,
        "closing_event_id": None,
        "roster_hash": ROSTER_HASH,
    }
    row.update(overrides)
    return row


def closed_line_row(closing_event_id: UUID, **overrides: Any) -> dict[str, Any]:
    """A valid **closed** line naming `closing_event_id` as its terminal event.

    All three of `is_closed`, `lifecycle_state` and the pointer move together,
    because two immediate biconditionals tie them: `ck_pol__closed_iff_delivered`
    and `ck_pol__closed_iff_closing_event`. A builder that set only one would be
    rejected by whichever of those fired first, and no test using it would ever
    reach the deferred FK.

    `closing_event_id` may well name an event that does not exist yet -- that is
    the ordinary write order the deferral exists for (line -> events -> commit),
    not a special case.
    """
    return line_row(
        lifecycle_state="delivered",
        is_closed=True,
        closing_event_id=closing_event_id,
        **overrides,
    )


def event_row(
    po_line_id: UUID,
    sequence_no: int,
    from_state: str | None,
    to_state: str,
    **overrides: Any,
) -> dict[str, Any]:
    """One `lifecycle_event` row, with `is_terminal` derived from `to_state`.

    Derived rather than passed, because `ck_lifecycle_event__terminal_iff_delivered`
    is a biconditional and an honest writer has no freedom here at all: the flag
    is a function of the state. The two tests that *forge* it pass
    `is_terminal=` explicitly, which is the only way to produce the row those
    tests need and makes the forgery visible at the call site.
    """
    row: dict[str, Any] = {
        "event_id": uuid4(),
        "po_line_id": po_line_id,
        "sequence_no": sequence_no,
        "from_state": from_state,
        "to_state": to_state,
        "is_terminal": to_state == "delivered",
        "occurred_at": FIRST_EVENT_AT + timedelta(days=sequence_no - 1),
        "note": None,
    }
    row.update(overrides)
    return row


# --------------------------------------------------------------------------- #
# Insert / read helpers
# --------------------------------------------------------------------------- #


def insert_line(session: Session, row: Mapping[str, Any]) -> UUID:
    """Insert `row` into `purchase_order_line` and return its id."""
    session.execute(LINE_INSERT, dict(row))
    return row["po_line_id"]


def insert_event(session: Session, row: Mapping[str, Any]) -> UUID:
    """Insert `row` into `lifecycle_event` and return its id."""
    session.execute(EVENT_INSERT, dict(row))
    return row["event_id"]


def insert_history(
    session: Session,
    po_line_id: UUID,
    states: Sequence[str],
    *,
    event_ids: Sequence[UUID] | None = None,
    occurred_at: Sequence[datetime] | None = None,
) -> tuple[UUID, ...]:
    """Insert `states` as a chained history on `po_line_id`, returning the event ids.

    Ascending `sequence_no` from 1, with each event's `from_state` taken from its
    predecessor's `to_state`. Ascending order is not a convenience:
    `fk_lifecycle_event__chain` is **not** deferrable, so event *n* cannot be
    written before *n-1* exists. That cost is recorded in `data-model.md`
    §`lifecycle_event`, and this helper is where it is paid.

    `event_ids` is accepted so a caller can decide an event's id *before* writing
    the line that points at it -- which is what makes a closed line writable in
    the declared order without an intervening `UPDATE`.
    """
    if event_ids is not None and len(event_ids) != len(states):
        raise ValueError(f"{len(states)} states need {len(states)} ids, got {len(event_ids)}")
    if occurred_at is not None and len(occurred_at) != len(states):
        raise ValueError(f"{len(states)} states need {len(states)} timestamps")

    inserted: list[UUID] = []
    previous_state: str | None = None
    for index, state in enumerate(states):
        overrides: dict[str, Any] = {}
        if event_ids is not None:
            overrides["event_id"] = event_ids[index]
        if occurred_at is not None:
            overrides["occurred_at"] = occurred_at[index]
        inserted.append(
            insert_event(
                session,
                event_row(po_line_id, index + 1, previous_state, state, **overrides),
            )
        )
        previous_state = state
    return tuple(inserted)


EVENT_COUNT = text("SELECT count(*) FROM lifecycle_event WHERE po_line_id = :po_line_id")


def event_count(session: Session, po_line_id: UUID) -> int:
    """How many lifecycle events this line has. Zero is a legitimate answer (TR-066)."""
    return session.execute(EVENT_COUNT, {"po_line_id": po_line_id}).scalar_one()


EVENT_EXISTS = text("SELECT count(*) FROM lifecycle_event WHERE event_id = :event_id")


def event_exists(session: Session, event_id: UUID) -> bool:
    """Whether any row of `lifecycle_event` carries `event_id`."""
    return session.execute(EVENT_EXISTS, {"event_id": event_id}).scalar_one() > 0


HISTORY_OF_LINE = text(
    """
    SELECT sequence_no, from_state, to_state, is_terminal, occurred_at
    FROM lifecycle_event
    WHERE po_line_id = :po_line_id
    ORDER BY sequence_no
    """
)


def transitions_of(session: Session, po_line_id: UUID) -> list[tuple[int, str | None, str]]:
    """This line's history as `(sequence_no, from_state, to_state)`, in position order.

    Ordered by `sequence_no` and not by `occurred_at`, which is the reading
    `data-model.md` §State Machines specifies for recovering rework cycles --
    and, per gap G-4, the only one of the two orderings the schema guarantees.
    """
    rows = session.execute(HISTORY_OF_LINE, {"po_line_id": po_line_id}).all()
    return [(row.sequence_no, row.from_state, row.to_state) for row in rows]


CURRENT_STATE_ROW = text(
    """
    SELECT
        lifecycle_state,
        current_state,
        latest_sequence_no,
        latest_occurred_at,
        is_closed,
        closing_event_id,
        is_right_censored
    FROM v_purchase_order_line_current_state
    WHERE po_line_id = :po_line_id
    """
)


def current_state_row(session: Session, po_line_id: UUID) -> Any | None:
    """This line's row of `v_purchase_order_line_current_state`, or None if absent.

    `one_or_none` rather than `one`, because the *presence* of the row is the
    assertion in the censoring test: the view's `LEFT JOIN LATERAL` is what keeps
    an event-less line visible, and an inner join would return nothing here.
    """
    return session.execute(CURRENT_STATE_ROW, {"po_line_id": po_line_id}).one_or_none()


GENERATED_CLOSING_TRIPLE = text(
    """
    SELECT closing_event_id, closing_event_po_line_id, closing_event_terminal
    FROM purchase_order_line
    WHERE po_line_id = :po_line_id
    """
)

#: The predicate of `ix_purchase_order_line__open`, run as a query. TR-066 calls
#: the right-censored set the worklist's default filter, so this is the read the
#: partial index exists to serve.
OPEN_LINES_ON_PROJECT = text(
    """
    SELECT po_line_id
    FROM purchase_order_line
    WHERE project_id = :project_id AND NOT is_closed
    ORDER BY need_by_date
    """
)


def assert_not_null_violation(session: Session, row: Mapping[str, Any], column: str) -> None:
    """Assert `row` is refused as a `NOT NULL` violation naming `column`.

    Deliberately not routed through `conftest.assert_rejects`, for a reason that
    is a property of PostgreSQL 16 rather than a preference: a `NOT NULL`
    violation reports `column_name` and carries **no `constraint_name` at all**,
    because catalogued, nameable `NOT NULL` constraints only arrive in 17.
    Forcing this through a helper that requires a constraint name would prove the
    helper's error path and nothing about the schema.

    Asserting the column is not the weaker claim. It is what distinguishes this
    rejection from a null in any *other* required column of the same row.
    """
    savepoint = session.begin_nested()
    with pytest.raises(Exception) as rejection:  # noqa: B017 -- narrowed immediately below
        session.execute(LINE_INSERT, dict(row))
    if savepoint.is_active:
        savepoint.rollback()

    original = getattr(rejection.value, "orig", rejection.value)
    assert isinstance(original, psycopg.errors.NotNullViolation), (
        f"a line with no {column} must be refused as a NOT NULL violation "
        f"(SQLSTATE 23502); got {type(original).__name__} "
        f"(SQLSTATE {getattr(original, 'sqlstate', None)})"
    )
    assert original.diag.column_name == column, (
        f"the rejection must name {column}, or some other required column was null and this "
        f"test never reached the rule it claims to cover; got "
        f"{original.diag.column_name!r} on {original.diag.table_name!r}"
    )


# --------------------------------------------------------------------------- #
# T032 -- censoring and the deferred closing pointer (TR-021, TR-066, TR-067)
# --------------------------------------------------------------------------- #

CLOSING_FOREIGN_KEY = "fk_purchase_order_line__closing_event"


def test_an_open_line_persists_with_no_lifecycle_event(db_session: Session) -> None:
    """TR-066: an open line stands on its own, with no event of any kind.

    Nothing in `0007` requires an event to exist. `fk_lifecycle_event__line`
    points the other way, and the closing FK's referencing triple is all-null on
    an open line, which `MATCH FULL` accepts outright with no referent. So a line
    that has been raised and not yet reviewed is a complete, valid row -- which is
    what makes right-censoring representable at all. A schema that demanded a
    first event would force a generator to invent one.

    Read back rather than merely inserted: the three columns that carry censoring
    (`is_closed`, `closing_event_id`, and the absence of events) are asserted
    from the stored row, and the line is asserted to be returned by the
    right-censored worklist read that `ix_purchase_order_line__open` serves.
    """
    row = line_row()
    po_line_id = insert_line(db_session, row)

    assert event_count(db_session, po_line_id) == 0, (
        "an open line must persist with no lifecycle event at all -- if this is nonzero the "
        "fixture wrote a history and the censoring case was never exercised"
    )

    stored = db_session.execute(GENERATED_CLOSING_TRIPLE, {"po_line_id": po_line_id}).one()
    assert stored.closing_event_id is None, "an open line names no closing event"
    assert stored.closing_event_po_line_id is None, (
        "the generated referencing columns are null exactly when `closing_event_id` is, "
        "which is what makes MATCH FULL's all-null case the open line"
    )
    assert stored.closing_event_terminal is None, (
        "the third referencing column too -- a partially-null triple would be a case "
        "MATCH FULL rejects and MATCH SIMPLE would silently skip"
    )

    censored = db_session.execute(
        OPEN_LINES_ON_PROJECT, {"project_id": row["project_id"]}
    ).scalars()
    assert po_line_id in set(censored), (
        "TR-066 makes the right-censored set the worklist's default filter, so an open line "
        "must be returned by the `WHERE NOT is_closed` read that `ix_purchase_order_line__open` "
        "indexes"
    )


def test_the_current_state_view_returns_an_open_line_that_has_no_events(
    db_session: Session,
) -> None:
    """TR-066: `v_purchase_order_line_current_state` keeps an event-less line visible.

    This is the assertion the view's `LEFT JOIN LATERAL` exists for. An inner
    join would hide **exactly** the row this test is about -- the line with no
    lifecycle event, which is the right-censored line the survival arm depends on
    -- and every other assertion about the view would still pass. So the first
    thing asserted is that the row comes back at all.

    `current_state` is then NULL rather than absent, which is the difference
    between "this line has no history yet" and "this line does not exist": a
    reader can tell them apart, and G-3's recorded production-scale alternative
    (drop `lifecycle_state`, derive current state here) stays available.
    """
    po_line_id = insert_line(db_session, line_row())

    view = current_state_row(db_session, po_line_id)

    assert view is not None, (
        "the view must return a line with no events. If this is None the lateral join has "
        "become an inner join, and the one row TR-066 is about -- the right-censored line -- "
        "is invisible to every consumer of this view"
    )
    assert view.current_state is None, (
        "with no events there is no derived current state; NULL is the answer, not a row "
        f"that fails to appear. Got {view.current_state!r}"
    )
    assert view.latest_sequence_no is None, "no events means no latest position"
    assert view.latest_occurred_at is None, "no events means no latest timestamp"
    assert view.is_right_censored is True, (
        "right-censored means no delivery event, exposed as `closing_event_id IS NULL` -- "
        "the column the deferred FK actually proves"
    )
    assert view.is_closed is False, "and the stored indicator agrees with it"
    assert view.lifecycle_state == "submitted", (
        "the stored state is still reported alongside the derived one; the view exposes both "
        "so a disagreement (gap G-3) is readable in a single row"
    )


def test_a_pointer_on_an_open_line_is_rejected(
    db_session: Session, assert_rejects: RejectionAsserter
) -> None:
    """Invariant 14: `ck_pol__closed_iff_closing_event` is a biconditional.

    An open line carrying a closing pointer is refused as firmly as a closed line
    carrying none. Immediate, not deferred -- this check reads only columns of the
    row in front of it, so there is nothing to wait for. It is the half of the
    closed-line rule a `CHECK` *can* carry; the FK below carries the half that
    needs another table.
    """
    with assert_rejects(
        db_session, psycopg.errors.CheckViolation, "ck_pol__closed_iff_closing_event"
    ):
        insert_line(db_session, line_row(closing_event_id=uuid4()))


def test_a_closed_line_naming_no_event_is_rejected(
    db_session: Session, assert_rejects: RejectionAsserter
) -> None:
    """The other direction of the same biconditional: closed means *named*.

    A line may not report itself delivered while pointing at nothing. Without
    this direction, closure would be a claim with no referent and the deferred FK
    -- which has only the pointer to work from -- would have nothing to check.
    """
    with assert_rejects(
        db_session, psycopg.errors.CheckViolation, "ck_pol__closed_iff_closing_event"
    ):
        insert_line(db_session, line_row(lifecycle_state="delivered", is_closed=True))


def test_the_closing_pointer_is_deferred_to_the_commit_boundary_and_then_enforced(
    db_session: Session,
    assert_rejects: RejectionAsserter,
    force_constraints_immediate: ConstraintForcer,
) -> None:
    """TR-021, TR-067, OBJ4 VC2: the deferral and the enforcement, in that order.

    **This is the one test in the tier that can pass vacuously**, and the two
    steps are what stop it. `db_session` never reaches a real `COMMIT`, so a
    `DEFERRABLE INITIALLY DEFERRED` constraint never fires on its own (HINT-002).

    *Step 1 -- the deferral.* A closed line naming an event that does not exist
    is **accepted** mid-transaction. That is not a hole: it is the write order the
    deferral exists for. At the moment a closed line is written, its terminal
    event has not been inserted yet, and an immediate constraint would make the
    correct order impossible in either direction -- the line needs the event and
    the event needs the line. The referent's absence is asserted, because
    acceptance only means "deferred" while there is genuinely nothing to point at.

    *Step 2 -- the enforcement.* `SET CONSTRAINTS ALL IMMEDIATE` runs the pending
    check at a point this test chooses, raising the same class, SQLSTATE, and
    `constraint_name` diagnostic `COMMIT` would have raised.

    Neither step is sufficient alone. Step 1 by itself asserts that nothing
    happened, which is what a schema with no constraint at all would also
    produce. Step 2 by itself would pass identically against an *immediate*
    constraint, since forcing an immediate check on a never-deferred constraint
    looks the same from here. Both were confirmed by removal -- see the module
    docstring, and `test_a_closed_line_naming_its_own_terminal_event_passes_the_forced_check`
    for the other side of the control.
    """
    # Step 1 -- the deferral.
    ghost_event_id = uuid4()
    po_line_id = insert_line(db_session, closed_line_row(ghost_event_id))

    assert not event_exists(db_session, ghost_event_id), (
        "the pointed-at event must genuinely not exist, or acceptance below would prove "
        "nothing about deferral -- it would just be a satisfied foreign key"
    )
    assert event_count(db_session, po_line_id) == 0, (
        "and the line must have no events at all, so the only reason the insert stood is "
        "that the check has not run yet"
    )

    triple = db_session.execute(GENERATED_CLOSING_TRIPLE, {"po_line_id": po_line_id}).one()
    assert triple.closing_event_id == ghost_event_id, (
        "the dangling pointer is stored as written -- accepted, not silently dropped or "
        f"nulled. Got {triple.closing_event_id!r}"
    )
    assert triple.closing_event_po_line_id == po_line_id, (
        "the generated column carries *this* line's id, which is the column that stops a "
        "pointer naming another line's event"
    )
    assert triple.closing_event_terminal is True, (
        "and the generated terminal flag is true, so the FK will demand an event whose own "
        "`is_terminal` is true -- a flag `ck_lifecycle_event__terminal_iff_delivered` makes "
        "unforgeable"
    )

    # Step 2 -- the enforcement.
    with assert_rejects(db_session, psycopg.errors.ForeignKeyViolation, CLOSING_FOREIGN_KEY):
        force_constraints_immediate(db_session)


def test_a_closed_line_naming_its_own_terminal_event_passes_the_forced_check(
    db_session: Session, force_constraints_immediate: ConstraintForcer
) -> None:
    """The other side of the deferral control: a legitimate closure survives the check.

    Without this test, every "the forced check raises" assertion in this file
    would also pass against a constraint that rejected *everything* -- a
    mis-declared FK naming the wrong referenced key, say. So the honest path is
    asserted end to end, in the declared write order: the closed line first,
    naming an event id that does not exist yet; then the full history through to
    `delivered`, ascending, because `fk_lifecycle_event__chain` is not deferrable;
    then the forced check, which must not raise.

    The view is read afterwards to confirm the closure is visible as a closure --
    `is_right_censored` false, and the derived current state agreeing with the
    stored one, which is the case G-3 leaves *outside* its gap.
    """
    event_ids = tuple(uuid4() for _ in PATH_TO_DELIVERED)
    terminal_event_id = event_ids[-1]

    po_line_id = insert_line(db_session, closed_line_row(terminal_event_id))
    insert_history(db_session, po_line_id, PATH_TO_DELIVERED, event_ids=event_ids)

    # Must not raise. If it does, the constraint is rejecting valid closures and
    # every rejection asserted elsewhere in this file is uninformative.
    force_constraints_immediate(db_session)

    view = current_state_row(db_session, po_line_id)
    assert view is not None, "a closed line is still a line and still appears in the view"
    assert view.is_right_censored is False, (
        "a line with a delivery event is not right-censored -- that is the whole distinction "
        "the survival arm reads"
    )
    assert view.closing_event_id == terminal_event_id, "and the pointer resolves to that event"
    assert view.current_state == "delivered", (
        "the derived state is the highest-sequence event's `to_state`, which for a closed "
        f"line must be `delivered`. Got {view.current_state!r}"
    )
    assert view.lifecycle_state == view.current_state, (
        "for a *closed* line the stored and derived states cannot disagree -- that is what "
        "`ck_pol__closed_iff_delivered` plus this FK carry, and it is why G-3's disclosure "
        "is scoped to open lines only"
    )
    assert view.latest_sequence_no == len(PATH_TO_DELIVERED), (
        "the terminal event is the last position in the history, not an extra row beside it"
    )


def test_a_closing_pointer_at_a_non_terminal_event_is_rejected_at_the_forced_check(
    db_session: Session,
    assert_rejects: RejectionAsserter,
    force_constraints_immediate: ConstraintForcer,
) -> None:
    """TR-067: the pointer cannot name a non-terminal event.

    Note what is *not* violated here. The line's own checks are all satisfied: it
    is closed, it says `delivered`, and it names a pointer, so
    `ck_pol__closed_iff_delivered` and `ck_pol__closed_iff_closing_event` both
    pass. The history is a legal chain. Everything a single-row `CHECK` can see is
    in order, and the line still claims a closure its own events do not support.

    Only the FK catches it, and only because `closing_event_terminal` -- generated
    `true` whenever the pointer is non-null -- is matched against the referenced
    event's `is_terminal`, which is `false` on `shipped`. That is the third
    referencing column doing the work no check could.
    """
    event_ids = tuple(uuid4() for _ in PATH_TO_SHIPPED)
    shipped_event_id = event_ids[-1]

    po_line_id = insert_line(db_session, closed_line_row(shipped_event_id))
    insert_history(db_session, po_line_id, PATH_TO_SHIPPED, event_ids=event_ids)

    assert event_exists(db_session, shipped_event_id), (
        "the pointed-at event must exist, or this would be the dangling-pointer case again "
        "and the non-terminal column would never be compared"
    )

    with assert_rejects(db_session, psycopg.errors.ForeignKeyViolation, CLOSING_FOREIGN_KEY):
        force_constraints_immediate(db_session)


def test_a_closing_pointer_at_another_lines_terminal_event_is_rejected_at_the_forced_check(
    db_session: Session,
    assert_rejects: RejectionAsserter,
    force_constraints_immediate: ConstraintForcer,
) -> None:
    """TR-067: the pointer cannot borrow another line's terminal event.

    The referenced event here is entirely legitimate -- it exists, it is
    `delivered`, its `is_terminal` is true, and it closes its own line correctly.
    A foreign key on `event_id` alone would accept this and two lines would report
    one delivery, which is a silent over-count in every downstream survival read.

    `closing_event_po_line_id` is what refuses it: generated from the *borrowing*
    line's own id, so the triple cannot be made to match. The borrowing line is
    given no events of its own, which isolates the failure to that one column.
    """
    donor_event_ids = tuple(uuid4() for _ in PATH_TO_DELIVERED)
    donor_terminal_event_id = donor_event_ids[-1]
    donor_line_id = insert_line(db_session, closed_line_row(donor_terminal_event_id))
    insert_history(db_session, donor_line_id, PATH_TO_DELIVERED, event_ids=donor_event_ids)

    borrower_line_id = insert_line(
        db_session,
        closed_line_row(donor_terminal_event_id, line_number=SECOND_LINE_NUMBER),
    )
    assert event_count(db_session, borrower_line_id) == 0, (
        "the borrowing line has no history of its own, so the only thing wrong with it is "
        "whose event it names"
    )

    with assert_rejects(db_session, psycopg.errors.ForeignKeyViolation, CLOSING_FOREIGN_KEY):
        force_constraints_immediate(db_session)


CLOSING_FOREIGN_KEY_CATALOGUE = text(
    """
    SELECT condeferrable, condeferred, confmatchtype, confdeltype, confupdtype
    FROM pg_constraint
    WHERE conname = :conname AND conrelid = 'purchase_order_line'::regclass
    """
)

GENERATED_COLUMNS_OF_LINE = text(
    """
    SELECT attname, attgenerated
    FROM pg_attribute
    WHERE attrelid = 'purchase_order_line'::regclass AND attgenerated <> ''
    ORDER BY attname
    """
)


def test_the_closing_foreign_key_is_deferred_match_full_and_no_action_in_the_catalogue(
    db_session: Session,
) -> None:
    """HINT-003: `ON DELETE` is `NO ACTION`, and the catalogue says why it must be.

    Read from `pg_constraint` rather than inferred from behaviour, because two of
    these five facts have no observable behaviour to assert from a test that never
    commits and never deletes.

    `NO ACTION` is not a preference a later revision may tidy up. PostgreSQL
    forbids `SET NULL` and `SET DEFAULT` against a generated column, and two of
    the three referencing columns here are generated `STORED` -- asserted
    alongside, because that is the fact that *forces* the referential action. The
    generated-column shape and `NO ACTION` come as a pair, and
    `test_on_delete_rewriting_the_closing_key_is_refused_at_ddl_time` shows the
    server refusing the alternative outright.
    """
    catalogue = db_session.execute(
        CLOSING_FOREIGN_KEY_CATALOGUE, {"conname": CLOSING_FOREIGN_KEY}
    ).one()

    assert catalogue.condeferrable is True, (
        f"{CLOSING_FOREIGN_KEY} is the schema's one deferrable constraint; if this is false "
        "the write order line -> events -> commit is impossible and every closed line has to "
        "be built by a second UPDATE"
    )
    assert catalogue.condeferred is True, "and INITIALLY DEFERRED, so the default is the deferral"
    assert catalogue.confmatchtype == MATCH_FULL, (
        "MATCH FULL, which accepts the open line's all-null triple with no referent and "
        f"enforces every column otherwise. Got {catalogue.confmatchtype!r}"
    )
    assert catalogue.confdeltype == NO_ACTION, (
        "ON DELETE NO ACTION, forced by the generated referencing columns (HINT-003) -- "
        f"PostgreSQL rejects SET NULL and SET DEFAULT against them. Got "
        f"{catalogue.confdeltype!r}"
    )
    assert catalogue.confupdtype == NO_ACTION, "ON UPDATE NO ACTION, for the same reason"

    generated = {
        row.attname: row.attgenerated for row in db_session.execute(GENERATED_COLUMNS_OF_LINE)
    }
    assert generated == {
        "closing_event_po_line_id": STORED,
        "closing_event_terminal": STORED,
    }, (
        "exactly the two extra referencing columns are generated STORED. That is what makes "
        "the triple null together on an open line and non-null together on a closed one -- and "
        f"it is why ON DELETE cannot be anything but NO ACTION. Got {generated!r}"
    )


def _redeclare_closing_key_with(action: str) -> str:
    """The closing FK's own declaration, with `ON DELETE` replaced by `action`.

    A named function rather than an f-string at the call site, so the only value
    ever interpolated is one of the two literals in `FORBIDDEN_DELETE_ACTIONS`
    below. Ruff S608 exists because SQL assembled from values is how injection
    happens; there is no value here that came from anywhere but this module.
    """
    return (
        "ALTER TABLE purchase_order_line "
        "ADD CONSTRAINT tmp_closing_event_action "
        "FOREIGN KEY (closing_event_id, closing_event_po_line_id, closing_event_terminal) "
        "REFERENCES lifecycle_event (event_id, po_line_id, is_terminal) "
        "MATCH FULL "
        f"ON DELETE {action} "
        "DEFERRABLE INITIALLY DEFERRED"
    )


#: The two referential actions PostgreSQL refuses against a generated column,
#: which is the whole content of HINT-003. `CASCADE` is *not* here: it is legal
#: against generated columns and would be rejected for a different reason
#: (nothing), so including it would blur what this test proves.
FORBIDDEN_DELETE_ACTIONS = ("SET NULL", "SET DEFAULT")


@pytest.mark.parametrize("action", FORBIDDEN_DELETE_ACTIONS)
def test_on_delete_rewriting_the_closing_key_is_refused_at_ddl_time(
    db_session: Session, action: str
) -> None:
    """HINT-003, evidenced rather than described: the server refuses the alternative.

    Re-declaring the same foreign key with `ON DELETE SET NULL` (or `SET
    DEFAULT`) is rejected when the DDL is *parsed* -- SQLSTATE 42601,
    `invalid ON DELETE action for foreign key constraint containing generated
    column` -- so `NO ACTION` in the migration is the only declaration that
    exists, not the one that was preferred.

    Asserted on the psycopg subclass and its SQLSTATE, never on the message: a
    syntax-level rejection carries no `constraint_name` for `assert_rejects` to
    match, since the constraint is never created.

    DDL is transactional in PostgreSQL, so the attempted `ALTER TABLE` leaves
    nothing behind even in the runs where it might have succeeded; the savepoint
    and the fixture's outer rollback both discard it.
    """
    savepoint = db_session.begin_nested()
    with pytest.raises(Exception) as rejection:  # noqa: B017 -- narrowed immediately below
        db_session.execute(text(_redeclare_closing_key_with(action)))
    if savepoint.is_active:
        savepoint.rollback()

    original = getattr(rejection.value, "orig", rejection.value)
    assert isinstance(original, psycopg.errors.SyntaxError), (
        f"ON DELETE {action} against a generated referencing column must be refused at DDL "
        f"time (SQLSTATE 42601); got {type(original).__name__} "
        f"(SQLSTATE {getattr(original, 'sqlstate', None)})"
    )
    assert original.sqlstate == "42601", (
        f"and refused as a syntax-level error rather than at some later stage; got "
        f"{original.sqlstate}"
    )


LINE_INSERT_WRITING_A_GENERATED_COLUMN = text(
    """
    INSERT INTO purchase_order_line (
        po_line_id, project_id, vendor_id, po_number, line_number,
        material_category, description, manufacturer, part_number,
        quantity, unit_of_measure, order_date, need_by_date, criticality,
        lifecycle_state, is_closed, closing_event_id, closing_event_terminal, roster_hash
    )
    VALUES (
        :po_line_id, :project_id, :vendor_id, :po_number, :line_number,
        :material_category, :description, :manufacturer, :part_number,
        :quantity, :unit_of_measure, :order_date, :need_by_date, :criticality,
        :lifecycle_state, :is_closed, :closing_event_id, true, :roster_hash
    )
    """
)


def test_a_writer_cannot_set_either_generated_referencing_column(db_session: Session) -> None:
    """Why `MATCH FULL` has no partial-match case to reason about here.

    `MATCH SIMPLE` skips the referential check when *any* referencing column is
    null, which is the hole the §Conventions rule warns about. It is closed here
    not by argument but because a writer cannot produce a partially-null triple at
    all: `closing_event_terminal` and `closing_event_po_line_id` are `GENERATED
    ALWAYS`, and PostgreSQL refuses the write with `GeneratedAlways` (SQLSTATE
    428C9). A forged `closing_event_terminal = true` on an open line -- the row
    that would otherwise slip past a `MATCH SIMPLE` FK -- is unrepresentable, not
    merely forbidden.

    That is the property `data-model.md` leans on when it records rung 1 of
    TR-065's ladder as sufficient, with no `ck_pol__closing_triple_null_together`
    needed and no trigger in the schema.
    """
    savepoint = db_session.begin_nested()
    with pytest.raises(Exception) as rejection:  # noqa: B017 -- narrowed immediately below
        db_session.execute(LINE_INSERT_WRITING_A_GENERATED_COLUMN, line_row())
    if savepoint.is_active:
        savepoint.rollback()

    original = getattr(rejection.value, "orig", rejection.value)
    assert isinstance(original, psycopg.errors.GeneratedAlways), (
        "a write to a GENERATED ALWAYS column must be refused (SQLSTATE 428C9); got "
        f"{type(original).__name__} (SQLSTATE {getattr(original, 'sqlstate', None)})"
    )


# --------------------------------------------------------------------------- #
# T033 -- the event chain, rework loops, and gaps G-3 / G-4 (TR-022)
# --------------------------------------------------------------------------- #

#: `TWO_REWORK_CYCLES` as `(sequence_no, from_state, to_state)`, written out
#: rather than derived from that tuple. Deriving the expectation with the same
#: expression that produced the inserts would assert only that the code agrees
#: with itself; spelled out, the assertion is against
#: `data-model.md` §State Machines.
EXPECTED_REWORK_HISTORY: tuple[tuple[int, str | None, str], ...] = (
    (1, None, "submitted"),
    (2, "submitted", "under_review"),
    (3, "under_review", "revise_and_resubmit"),
    (4, "revise_and_resubmit", "submitted"),
    (5, "submitted", "under_review"),
    (6, "under_review", "revise_and_resubmit"),
    (7, "revise_and_resubmit", "submitted"),
    (8, "submitted", "under_review"),
    (9, "under_review", "approved"),
)

#: The rework edge, and the positions it occupies in the history above. Two
#: occurrences of one *state pair* at two distinct *positions* -- which is the
#: distinction TR-022 and OBJ4 VC3 turn on.
REWORK_EDGE = ("revise_and_resubmit", "submitted")
REWORK_POSITIONS = (4, 7)


def test_two_rework_cycles_are_recoverable_in_sequence_order(db_session: Session) -> None:
    """TR-022, OBJ4 VC3: two review cycles on one line, read back in position order.

    The claim is recoverability, so the history is read back rather than
    inferred: nine events, each `from_state` equal to its predecessor's
    `to_state`, and the rework edge appearing twice at two distinct positions.
    Nothing bounds the number of cycles -- transition 4 of the state machine
    returns to a state that already has an outgoing edge -- so "two" here is an
    instance, not a limit.

    Approval-cycle count and days-in-state are *derived* from these rows by E007
    and never stored, so the count of reviews is asserted as a derivation over the
    recovered history rather than read from a column that should not exist.
    """
    po_line_id = insert_line(db_session, line_row())
    insert_history(db_session, po_line_id, TWO_REWORK_CYCLES)

    recovered = transitions_of(db_session, po_line_id)

    assert tuple(recovered) == EXPECTED_REWORK_HISTORY, (
        "the whole history must come back in `sequence_no` order, each event's `from_state` "
        f"being its predecessor's `to_state`. Got {recovered!r}"
    )

    rework_positions = tuple(
        sequence_no
        for sequence_no, from_state, to_state in recovered
        if (from_state, to_state) == REWORK_EDGE
    )
    assert rework_positions == REWORK_POSITIONS, (
        "two rejections give two `revise_and_resubmit -> submitted` pairs, and they are "
        "separately recoverable because they sit at distinct positions -- the states repeat, "
        f"the positions do not. Got {rework_positions!r}"
    )

    positions = [sequence_no for sequence_no, _, _ in recovered]
    assert len(set(positions)) == len(positions), (
        "no position is reused; `uq_lifecycle_event__line_sequence` is what keeps two cycles "
        "from collapsing into one row"
    )

    reviews = sum(1 for _, _, to_state in recovered if to_state == "under_review")
    assert reviews == 3, (
        "three reviews -- two rejected, one approved -- derived from the event rows rather "
        f"than stored anywhere. Got {reviews}"
    )


def test_a_rework_cycle_cannot_reuse_a_position(
    db_session: Session, assert_rejects: RejectionAsserter
) -> None:
    """TR-022: rework repeats *states*, never positions.

    The second event at position 3 here is a legal transition in its own right --
    `under_review -> revise_and_resubmit` is edge 3 of the state machine, and the
    chain FK is satisfied because position 2 does end in `under_review`. It is
    refused solely for occupying a position that is taken, which is what makes a
    line's history a sequence rather than a bag.

    The colliding row deliberately carries a *different* `to_state` from the one
    already at position 3, so only `uq_lifecycle_event__line_sequence` can fire.
    An identical duplicate would violate `uq_lifecycle_event__line_sequence_state`
    as well, and the test would name a constraint chosen by index order.
    """
    po_line_id = insert_line(db_session, line_row())
    insert_history(db_session, po_line_id, ("submitted", "under_review", "approved"))

    with assert_rejects(
        db_session, psycopg.errors.UniqueViolation, "uq_lifecycle_event__line_sequence"
    ):
        insert_event(
            db_session,
            event_row(po_line_id, 3, "under_review", "revise_and_resubmit"),
        )


CHAIN_FOREIGN_KEY = "fk_lifecycle_event__chain"

CHAIN_FOREIGN_KEY_CATALOGUE = text(
    """
    SELECT confmatchtype, confdeltype, confupdtype, condeferrable
    FROM pg_constraint
    WHERE conname = :conname AND conrelid = 'lifecycle_event'::regclass
    """
)


def test_the_opening_event_inserts_because_the_chain_is_match_simple(
    db_session: Session,
) -> None:
    """TR-022: the opening event exists, and `MATCH SIMPLE` is why -- deliberately.

    T030 found this the hard way. `fk_lifecycle_event__chain` compares
    `(po_line_id, prev_sequence_no, from_state)`, and on a sequence-1 event the
    last two are null while `po_line_id` is not -- a *partially* null triple.
    `MATCH FULL` permits all-null and requires all-matching and **rejects
    everything between**: it does not skip such a row, it refuses it. Under
    `MATCH FULL` no line could hold even a single event, so every line's entire
    history became unrepresentable. `data-model.md` records the correction.

    The skip `MATCH SIMPLE` introduces is confined to exactly the rows with no
    predecessor, and confined by a constraint rather than by argument:
    `ck_lifecycle_event__first_has_no_predecessor` makes the null pattern a
    function of `sequence_no` alone. The two tests after this one are that
    confinement -- a null predecessor at a later position, and a forged one.

    The match type is read from `pg_constraint` as well as exercised, because
    "accepted" alone would also be true of a schema that had dropped the
    constraint entirely.
    """
    po_line_id = insert_line(db_session, line_row())

    opening_event_id = insert_event(db_session, event_row(po_line_id, 1, None, "submitted"))
    insert_event(db_session, event_row(po_line_id, 2, "submitted", "under_review"))

    assert event_exists(db_session, opening_event_id), (
        "the opening event must persist -- under MATCH FULL this insert is rejected with "
        "ForeignKeyViolation and no line can hold any history at all"
    )
    assert transitions_of(db_session, po_line_id) == [
        (1, None, "submitted"),
        (2, "submitted", "under_review"),
    ], "and the chained second event follows it"

    catalogue = db_session.execute(
        CHAIN_FOREIGN_KEY_CATALOGUE, {"conname": CHAIN_FOREIGN_KEY}
    ).one()
    assert catalogue.confmatchtype == MATCH_SIMPLE, (
        "MATCH SIMPLE, written explicitly in the migration rather than left to the default, "
        f"because the deviation from data-model.md's original MATCH FULL is deliberate. Got "
        f"{catalogue.confmatchtype!r}"
    )
    assert catalogue.confdeltype == RESTRICT, (
        "ON DELETE RESTRICT: a line's events are deleted in descending position order, not cascaded"
    )
    assert catalogue.confupdtype == RESTRICT, (
        "ON UPDATE RESTRICT, unlike every other composite FK in the schema -- renumbering a "
        "line's history is a rewrite, not a correction to propagate"
    )
    assert catalogue.condeferrable is False, (
        "and not deferrable, which is the recorded cost: events must be inserted in ascending "
        "`sequence_no`. The schema has exactly one deferrable constraint and this is not it"
    )


def test_a_forged_predecessor_state_at_a_later_sequence_is_rejected(
    db_session: Session, assert_rejects: RejectionAsserter
) -> None:
    """TR-022: `from_state` must be the previous event's `to_state`, on this line.

    The forged event is internally plausible, which is the point:
    `revise_and_resubmit -> submitted` is a legal edge, so
    `ck_lifecycle_event__legal_transition` accepts it, and its position follows
    the last one contiguously. What it is not is *continuous* with the history it
    claims to extend -- position 2 ended in `under_review`, not in
    `revise_and_resubmit`.

    Only the composite FK can see that, and it sees three facts at once: same
    line, immediately previous position, and states that meet. A check could prove
    none of them, which is why a forged history is unrepresentable here rather
    than merely detectable by a later audit.
    """
    po_line_id = insert_line(db_session, line_row())
    insert_history(db_session, po_line_id, ("submitted", "under_review"))

    with assert_rejects(db_session, psycopg.errors.ForeignKeyViolation, CHAIN_FOREIGN_KEY):
        insert_event(db_session, event_row(po_line_id, 3, "revise_and_resubmit", "submitted"))


def test_a_null_predecessor_at_a_later_sequence_is_rejected(
    db_session: Session, assert_rejects: RejectionAsserter
) -> None:
    """The constraint that confines `MATCH SIMPLE`'s skip to the opening event.

    `MATCH SIMPLE` skips the referential check when any referencing column is
    null, so a null `from_state` at position 2 would be a free pass past
    `fk_lifecycle_event__chain`. It never reaches the FK:
    `ck_lifecycle_event__first_has_no_predecessor` is the biconditional
    `(sequence_no = 1) = (from_state IS NULL)`, so the null pattern is a function
    of position alone and a writer cannot produce a null predecessor anywhere
    else.

    This is the assertion that turns the `MATCH SIMPLE` deviation from an argument
    into a pinned property. If this check were ever dropped, the recorded
    strengthening is a third generated column and a return to `MATCH FULL`.
    """
    po_line_id = insert_line(db_session, line_row())
    insert_history(db_session, po_line_id, ("submitted",))

    with assert_rejects(
        db_session,
        psycopg.errors.CheckViolation,
        "ck_lifecycle_event__first_has_no_predecessor",
    ):
        insert_event(db_session, event_row(po_line_id, 2, None, "under_review"))


def test_a_history_that_does_not_open_at_submitted_is_rejected(
    db_session: Session, assert_rejects: RejectionAsserter
) -> None:
    """`(NULL, 'submitted')` is the only legal opening event.

    The opening event is the one row the chain FK cannot speak about and the
    transition helper cannot either -- the helper is `STRICT`, so it returns NULL
    on a null `from_state`, and a `CHECK` accepts NULL. Both of the mechanisms
    that carry the rest of the state machine are silent here.

    `ck_lifecycle_event__first_is_submitted` closes that branch in a definite
    boolean, which is what stops a line's history starting at `shipped` and
    skipping every review it should have had.
    """
    po_line_id = insert_line(db_session, line_row())

    with assert_rejects(
        db_session, psycopg.errors.CheckViolation, "ck_lifecycle_event__first_is_submitted"
    ):
        insert_event(db_session, event_row(po_line_id, 1, None, "under_review"))


#: Illegal edges, each chosen so that *only* the transition helper can reject it.
#: The predecessor exists at the previous position, the `to_state` is in the
#: seven-state set, and `is_terminal` is derived honestly -- so no other
#: constraint on the row is violated and the rejection is attributable.
ILLEGAL_TRANSITIONS = (
    ("submitted", "approved"),
    ("submitted", "shipped"),
    ("submitted", "delivered"),
    ("submitted", "revise_and_resubmit"),
)


@pytest.mark.parametrize(("from_state", "to_state"), ILLEGAL_TRANSITIONS)
def test_an_illegal_transition_is_rejected(
    db_session: Session,
    assert_rejects: RejectionAsserter,
    from_state: str,
    to_state: str,
) -> None:
    """TR-022, invariant 12: only the seven declared edges exist.

    Enforced by `ck_lifecycle_event__legal_transition`, which calls the immutable
    helper `fn_is_legal_lifecycle_transition`. Immutable is load-bearing rather
    than decorative: the check is emitted as validated, so a dump-and-restore
    re-proves the invariant row by row. A version of the helper that read a
    lookup table would pass or fail depending on what had been loaded so far in
    the same restore.

    `submitted -> delivered` is included because it is the transition that would
    let a line skip its entire fabrication history and still close, and it must be
    refused for the *transition* rather than for the terminal flag, which the row
    sets honestly.
    """
    po_line_id = insert_line(db_session, line_row())
    insert_history(db_session, po_line_id, ("submitted",))

    with assert_rejects(
        db_session, psycopg.errors.CheckViolation, "ck_lifecycle_event__legal_transition"
    ):
        insert_event(db_session, event_row(po_line_id, 2, from_state, to_state))


TERMINAL_FLAG_CHECK = "ck_lifecycle_event__terminal_iff_delivered"


def test_a_terminal_flag_forged_true_on_a_non_delivered_state_is_rejected(
    db_session: Session, assert_rejects: RejectionAsserter
) -> None:
    """Invariant 10, first direction: `is_terminal` cannot be claimed.

    If a `submitted` event could carry `is_terminal = true`, the closing FK --
    which matches on the flag, not on the state -- would accept it as a closure
    and a line would be delivered on the strength of having been raised. The
    referenced key `uq_lifecycle_event__id_line_terminal` carries the flag
    precisely so the FK can compare it, and this check is what makes the compared
    value true rather than merely asserted.
    """
    po_line_id = insert_line(db_session, line_row())

    with assert_rejects(db_session, psycopg.errors.CheckViolation, TERMINAL_FLAG_CHECK):
        insert_event(db_session, event_row(po_line_id, 1, None, "submitted", is_terminal=True))


def test_a_delivered_event_forged_not_terminal_is_rejected(
    db_session: Session, assert_rejects: RejectionAsserter
) -> None:
    """Invariant 10, second direction -- and this is the one TR-021 needs.

    Without it, a writer could deliver a line and leave `is_terminal` false. The
    line would read as delivered in its history while the closing FK -- which
    matches `closing_event_terminal = true` against the event's own flag -- would
    have **no row to find**. The closure would be unrepresentable for a line that
    had genuinely been delivered, and the only way out would be to leave the line
    open, misreporting a completed delivery as right-censored: a silent error in
    exactly the direction the survival arm cannot afford.

    So the biconditional runs both ways, and the honest history up to `shipped` is
    inserted first so this row is rejected for its flag and nothing else.
    """
    po_line_id = insert_line(db_session, line_row())
    insert_history(db_session, po_line_id, PATH_TO_SHIPPED)

    with assert_rejects(db_session, psycopg.errors.CheckViolation, TERMINAL_FLAG_CHECK):
        insert_event(
            db_session,
            event_row(
                po_line_id,
                len(PATH_TO_DELIVERED),
                "shipped",
                "delivered",
                is_terminal=False,
            ),
        )


def test_gap_g3_an_open_lines_stored_state_may_disagree_with_its_latest_event(
    db_session: Session,
) -> None:
    """Gap G-3, asserted as the disclosure it is -- not as a guarantee.

    Whether an **open** line's `lifecycle_state` agrees with its highest-sequence
    event's `to_state` is **not enforced**, and `data-model.md` records why: the
    comparison is cross-row, and no `CHECK` can see a sibling row. The
    gap-disclosure record states the runtime outcome:

        "At runtime an open line's `lifecycle_state` may disagree with its latest
        event, so worklist filters on state and history reads disagree."

    That is what this test asserts. The line claims `under_review` while its only
    event says `submitted`, the row is accepted, and the disagreement is visible
    in one read of `v_purchase_order_line_current_state` -- which is the covering
    test the gap table names, run in its detecting form, so the query that would
    fail a build on real data is the one exercised here.

    Note the direction of the harm and its limit: both readings resolve
    correctly on their own, and no citation or event is fabricated. It is an
    inconsistency between two answers, not a wrong one -- which is why the
    recorded production-scale alternative is to stop storing the second answer:
    drop `lifecycle_state` from the line and let `current_state` here be the
    answer, with no consumer changing where it looks. That the column exists is
    asserted below, because the alternative depends on it.
    """
    po_line_id = insert_line(db_session, line_row(lifecycle_state="under_review"))
    insert_history(db_session, po_line_id, ("submitted",))

    view = current_state_row(db_session, po_line_id)
    assert view is not None, "the line is stored"

    assert view.lifecycle_state == "under_review", "the stored state, as the worklist filters it"
    assert view.current_state == "submitted", (
        "the derived state, as a history read computes it -- the `to_state` of the "
        f"highest-sequence event. Got {view.current_state!r}"
    )
    assert view.lifecycle_state != view.current_state, (
        "G-3's disclosed runtime outcome is exactly this disagreement, accepted by the "
        "schema and detectable only by a query. If these ever agree here, the fixture "
        "stopped exercising the gap"
    )
    assert view.is_right_censored is True, (
        "and the line is still right-censored throughout -- the gap is about which state is "
        "current, never about whether the line has been delivered"
    )
    assert view.latest_sequence_no == 1, (
        "one event, at position 1; `current_state` is that event's `to_state` and not a "
        "coincidence of ordering"
    )


def test_the_closed_half_of_g3_is_carried_by_a_constraint(
    db_session: Session, assert_rejects: RejectionAsserter
) -> None:
    """Where G-3 stops: the closed case is enforced, not disclosed.

    The gap is scoped to open lines because closure is pinned from both sides.
    `ck_pol__closed_iff_delivered` refuses a closed line whose state is anything
    but `delivered`, and the deferred FK then proves a real terminal event behind
    the pointer. A closed line therefore cannot disagree with its history at all,
    and the disclosure would be overstated if it were written without that scope.
    """
    with assert_rejects(db_session, psycopg.errors.CheckViolation, "ck_pol__closed_iff_delivered"):
        insert_line(
            db_session,
            line_row(lifecycle_state="shipped", is_closed=True, closing_event_id=uuid4()),
        )


#: Position 2 happens eight days *before* position 1. Out of order by a wide
#: margin, so no clock skew or truncation could account for it.
OUT_OF_ORDER_TIMESTAMPS = (
    FIRST_EVENT_AT + timedelta(days=10),
    FIRST_EVENT_AT + timedelta(days=2),
)

DAYS_IN_STATE = text(
    """
    SELECT
        sequence_no,
        occurred_at - lag(occurred_at) OVER (ORDER BY sequence_no) AS elapsed
    FROM lifecycle_event
    WHERE po_line_id = :po_line_id
    ORDER BY sequence_no
    """
)


def test_gap_g4_occurred_at_need_not_increase_with_sequence_no(db_session: Session) -> None:
    """Gap G-4, asserted as the disclosure it is -- not as a guarantee.

    `lifecycle_event.occurred_at` increasing with `sequence_no` is **not
    enforced**: it is cross-row, like G-3. The gap-disclosure record states the
    runtime outcome:

        "At runtime events ordered by `occurred_at` and by `sequence_no` can
        differ, so days-in-state derivations become negative."

    Both halves are asserted here. The out-of-order row is accepted, and the
    days-in-state derivation E007 will run over these rows *is* negative -- which
    is the reversal trigger the disclosure names ("a negative days-in-state value
    reaches the fit job"), computed by the same window function a consumer would
    use.

    The two columns are separate by design, not by omission: two events can share
    a wall-clock second, and the position in the history is what
    `fk_lifecycle_event__chain` reasons about. So the third assertion is the one
    that says which of the two orderings is authoritative -- the view reports
    position 2 as current even though it carries the earlier timestamp, because
    `ORDER BY sequence_no DESC LIMIT 1` is the definition of "current" and
    position is the ordering the schema actually guarantees.

    The recorded alternatives, if this ever needs closing: a deferred constraint
    trigger, or a generated `prev_occurred_at` carried through the chain FK so
    monotonicity becomes a single-row check.
    """
    po_line_id = insert_line(db_session, line_row())

    # Accepted. No constraint compares one event's timestamp with another's, so
    # there is nothing here to reject it; if this raised, the gap would be closed
    # and the disclosure would be the thing that is wrong.
    insert_history(
        db_session,
        po_line_id,
        ("submitted", "under_review"),
        occurred_at=OUT_OF_ORDER_TIMESTAMPS,
    )

    elapsed_by_position = {
        row.sequence_no: row.elapsed
        for row in db_session.execute(DAYS_IN_STATE, {"po_line_id": po_line_id})
    }

    assert elapsed_by_position[1] is None, "the first event has no predecessor to measure from"
    assert elapsed_by_position[2] < timedelta(0), (
        "G-4's disclosed runtime outcome is a *negative* days-in-state derivation, which is "
        "also its reversal trigger. This is the detection query a build fails on, since no "
        f"constraint carries it. Got {elapsed_by_position[2]!r}"
    )

    view = current_state_row(db_session, po_line_id)
    assert view is not None, "the line is stored"
    assert view.latest_sequence_no == 2, (
        "position, not timestamp, decides what is current -- the view orders by `sequence_no` "
        "DESC because that is the ordering the chain FK guarantees"
    )
    assert view.current_state == "under_review", "so the current state is position 2's `to_state`"
    assert view.latest_occurred_at == OUT_OF_ORDER_TIMESTAMPS[1], (
        "and the reported timestamp is position 2's, which is the *earlier* of the two -- the "
        "disagreement between the two orderings, visible in a single row"
    )


# --------------------------------------------------------------------------- #
# T034 -- dates and the frozen identifier formats (TR-023, TR-024, TR-025)
# --------------------------------------------------------------------------- #

#: Inverted order/need-by pairs. One day apart and a month apart: an off-by-one
#: in the comparison would let the first through, and a reversed comparison would
#: let neither.
INVERTED_DATE_PAIRS = (
    (date(2026, 4, 2), date(2026, 4, 1)),
    (date(2026, 4, 30), date(2026, 4, 1)),
)


@pytest.mark.parametrize(("order_date", "need_by_date"), INVERTED_DATE_PAIRS)
def test_a_need_by_date_before_the_order_date_is_rejected(
    db_session: Session,
    assert_rejects: RejectionAsserter,
    order_date: date,
    need_by_date: date,
) -> None:
    """TR-023, OBJ4 VC1: a line cannot be needed before it was ordered.

    Both columns are `NOT NULL`, so this cannot pass on a missing date -- a check
    rejects only on *false*, and any comparison against NULL is NULL, which a
    check accepts. `test_a_line_missing_a_format_checked_column_is_rejected_as_a_not_null_violation`
    asserts that pairing directly.
    """
    with assert_rejects(
        db_session, psycopg.errors.CheckViolation, "ck_pol__need_by_not_before_order"
    ):
        insert_line(db_session, line_row(order_date=order_date, need_by_date=need_by_date))


def test_a_need_by_date_on_the_order_date_is_accepted(db_session: Session) -> None:
    """TR-023: the constraint is `>=`, not `>` -- a same-day need-by is a real case.

    An expedited order placed and required the same day happens, and excluding it
    would force a writer to shift one of the two dates by a day to store the line
    at all -- a fabricated date, recorded as fact, to satisfy a comparison. That
    is precisely the silent corruption the storage boundary exists to prevent.

    This is the single assertion that distinguishes `>=` from `>`, so both dates
    are read back rather than merely inserted: a coerced or shifted value could
    not pass as acceptance.
    """
    same_day = date(2026, 4, 1)
    po_line_id = insert_line(db_session, line_row(order_date=same_day, need_by_date=same_day))

    stored = db_session.execute(
        text("SELECT order_date, need_by_date FROM purchase_order_line WHERE po_line_id = :i"),
        {"i": po_line_id},
    ).one()

    assert stored.order_date == same_day, "the order date round-trips unchanged"
    assert stored.need_by_date == same_day, "and so does the need-by date"
    assert stored.need_by_date == stored.order_date, (
        "the boundary case of `need_by_date >= order_date` is an ordinary row. If this test "
        "ever fails the constraint has been tightened to `>` and every same-day line now "
        "needs a fabricated date"
    )


#: Malformed project references. Each fails `^PRJ-[0-9]{3}$` for one reason: too
#: few digits, too many, wrong case, non-digits, missing separator, unanchored on
#: the left, unanchored on the right, trailing whitespace, empty, and the *other*
#: entity's format -- which would otherwise be the easiest confusion to ship.
MALFORMED_PROJECT_IDS = (
    "PRJ-01",
    "PRJ-0001",
    "prj-001",
    "PRJ-ABC",
    "PRJ001",
    "xPRJ-001",
    "PRJ-001y",
    "PRJ-001 ",
    "",
    "VND-001",
)


@pytest.mark.parametrize("project_id", MALFORMED_PROJECT_IDS)
def test_a_malformed_project_reference_is_rejected(
    db_session: Session, assert_rejects: RejectionAsserter, project_id: str
) -> None:
    """TR-025: the `PRJ-###` format E001 froze, anchored at both ends.

    `xPRJ-001` and `PRJ-001y` are the two cases that separate `~ '^PRJ-[0-9]{3}$'`
    from a regex missing one anchor, and a missing anchor is not cosmetic: a
    project reference with a prefix is a *different* project, silently joined to
    the wrong one by every read that compares the column.
    """
    with assert_rejects(db_session, psycopg.errors.CheckViolation, "ck_pol__project_id_format"):
        insert_line(db_session, line_row(project_id=project_id))


#: The same ten shapes against `^VND-[0-9]{3}$`, including `PRJ-001` -- the
#: mirror-image confusion. The two checks are separate constraints on separate
#: columns, so one being right says nothing about the other.
MALFORMED_VENDOR_IDS = (
    "VND-01",
    "VND-0001",
    "vnd-014",
    "VND-ABC",
    "VND014",
    "xVND-014",
    "VND-014y",
    "VND-014 ",
    "",
    "PRJ-001",
)


@pytest.mark.parametrize("vendor_id", MALFORMED_VENDOR_IDS)
def test_a_malformed_vendor_reference_is_rejected(
    db_session: Session, assert_rejects: RejectionAsserter, vendor_id: str
) -> None:
    """TR-025: the `VND-###` format, asserted independently of the project format.

    Tested separately rather than parametrized over both columns, because these
    are two constraints and a schema that had spelled one of them with the wrong
    prefix would pass a test that only exercised the other.
    """
    with assert_rejects(db_session, psycopg.errors.CheckViolation, "ck_pol__vendor_id_format"):
        insert_line(db_session, line_row(vendor_id=vendor_id))


#: Malformed roster hashes against `^sha256:[0-9a-f]{64}$`. The 63-digit and
#: uppercase-hex cases are the two that matter most and the two a hand-rolled
#: check most often admits: `{64}` written as `+` accepts the first, and a
#: case-insensitive class or a locale-dependent notion of "hex digit" accepts the
#: second. Uppercase hex is the same digest by value and a different string by
#: bytes, so admitting it would make one roster hash two, and TR-024's tie from a
#: stored line back to the exact input that produced it would stop being a
#: comparison anyone can make.
MALFORMED_ROSTER_HASHES = (
    "sha256:" + "a" * 63,
    "sha256:" + "a" * 65,
    "sha256:" + "AB" * 32,
    "sha256:" + "3F2A" * 16,
    "sha256:" + "g" * 64,
    "a" * 64,
    "sha1:" + "a" * 40,
    "SHA256:" + "a" * 64,
    "sha256:" + "a" * 64 + " ",
    "sha256:",
    "",
)


@pytest.mark.parametrize("roster_hash", MALFORMED_ROSTER_HASHES)
def test_a_malformed_roster_hash_is_rejected(
    db_session: Session, assert_rejects: RejectionAsserter, roster_hash: str
) -> None:
    """TR-024, OBJ4 VC5: `sha256:` plus exactly 64 lowercase hex digits.

    The roster is regenerable, so this column is what ties a stored line back to
    the exact input that produced it -- Principle I at the storage boundary. A
    hash that is truncated, re-cased, or missing its algorithm prefix cannot serve
    that purpose, and an unprefixed digest is worse than a missing one: it looks
    comparable and is not.
    """
    with assert_rejects(db_session, psycopg.errors.CheckViolation, "ck_pol__roster_hash_format"):
        insert_line(db_session, line_row(roster_hash=roster_hash))


def test_a_roster_hash_using_every_lowercase_hex_digit_is_accepted(db_session: Session) -> None:
    """TR-024: the accepted side of the same character class.

    All sixteen digits appear, four times each, so a class written `[0-9a-e]` or
    `[1-9a-f]` -- an omission that would reject a perfectly good digest roughly
    always -- fails here. Read back, because a check that accepted the row while
    something else truncated it would otherwise look like success.
    """
    every_digit = "sha256:" + "0123456789abcdef" * 4
    po_line_id = insert_line(db_session, line_row(roster_hash=every_digit))

    stored = db_session.execute(
        text("SELECT roster_hash FROM purchase_order_line WHERE po_line_id = :i"),
        {"i": po_line_id},
    ).scalar_one()
    assert stored == every_digit, (
        f"a well-formed roster hash must round-trip unchanged; got {stored!r}"
    )


#: The columns TR-023, TR-024 and TR-025's checks sit on. Each is `NOT NULL`,
#: which is what makes those checks non-vacuous (TR-039): a `CHECK` rejects only
#: on *false*, and every comparison against NULL is NULL, which a check accepts.
FORMAT_CHECKED_NOT_NULL_COLUMNS = (
    "project_id",
    "vendor_id",
    "roster_hash",
    "order_date",
    "need_by_date",
    "po_number",
)


@pytest.mark.parametrize("column", FORMAT_CHECKED_NOT_NULL_COLUMNS)
def test_a_line_missing_a_format_checked_column_is_rejected_as_a_not_null_violation(
    db_session: Session, column: str
) -> None:
    """TR-039: none of the format or date checks can be satisfied by absence.

    This is the paired half of the three tests above, and without it each of them
    would be one relaxed column away from vacuous: a nullable `roster_hash` makes
    `roster_hash ~ '^sha256:...'` evaluate to NULL for the row it exists to catch,
    and the check *accepts* it. So the pairing is asserted directly rather than
    read off the DDL.

    A `NOT NULL` violation carries `column_name` and no constraint name on
    PostgreSQL 16, so the column is what is asserted -- see
    `assert_not_null_violation`.
    """
    assert_not_null_violation(db_session, line_row(**{column: None}), column)
