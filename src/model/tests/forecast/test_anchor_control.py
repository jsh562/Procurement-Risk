"""T058, T059 — DV-023 / NC-5 / SC-002: the anchor is structural, and it is proved.

`anchor_date` could have been a plain column with a test asserting it equals the
line's order date. It is `fk_held_out_prediction__line_anchor` instead, because a
mis-anchored prediction is the silent failure Principle III names: every other
constraint on the row passes, E014 grades it against the wrong origin, and
nothing anywhere reports a problem.

**A test that only checks the emitted rows cannot tell a working foreign key from
a dropped one**, since the writer would produce correctly anchored rows either
way. So this file is a *positive control* in two halves. T058 reads the
constraint out of `pg_constraint` and asserts its shape — the key, its target,
and the two referential actions — so a dropped or weakened constraint fails here
rather than being discovered by a wrong grade. T059 plants a row the constraint
must refuse, so the constraint is exercised rather than merely observed to exist.

Every plant runs inside `begin_nested()`, so the refusal rolls back to a
savepoint and the session survives to make the next assertion. Nothing here is
committed: this tier isolates by an outer transaction that is discarded whole.

**`pytest.raises` sits outside the savepoint and not inside it**, which is not a
style choice. Written the other way round, `pytest.raises` swallows the
`IntegrityError` first, `begin_nested` then sees an ordinary exit and issues
`RELEASE SAVEPOINT` against a transaction PostgreSQL has already put in the
failed state — so the test fails on `InFailedSqlTransaction` rather than on the
constraint it is about. The savepoint has to unwind before anything catches.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from forecast.conftest import EmittedRun

#: Module-level SQL, never assembled from values (Ruff S608).
ANCHOR_CONSTRAINT_SQL = text(
    """
    SELECT c.conname, c.contype, c.confupdtype, c.confdeltype, c.confmatchtype,
           pg_get_constraintdef(c.oid) AS definition,
           t.relname AS on_table, r.relname AS references_table
    FROM pg_constraint c
    JOIN pg_class t ON t.oid = c.conrelid
    JOIN pg_class r ON r.oid = c.confrelid
    WHERE c.conname = 'fk_held_out_prediction__line_anchor'
    """
)
ORDER_ANCHOR_TARGET_SQL = text(
    """
    SELECT pg_get_constraintdef(oid) AS definition
    FROM pg_constraint WHERE conname = 'uq_purchase_order_line__order_anchor'
    """
)
STORED_ANCHORS_SQL = text(
    """
    SELECT h.po_line_id, h.anchor_date, h.line_is_closed, l.order_date, l.is_closed
    FROM held_out_prediction h
    JOIN purchase_order_line l ON l.po_line_id = h.po_line_id
    WHERE h.run_id = :run_id
    """
)
A_STORED_PREDICTION_SQL = text(
    """
    SELECT po_line_id, draw_count, horizon_days, anchor_date, anchor_convention,
           duration_semantic, draws, survival, residual_tail_mass, draw_digest
    FROM held_out_prediction WHERE run_id = :run_id ORDER BY po_line_id LIMIT 1
    """
)
AN_OPEN_LINE_SQL = text(
    """
    SELECT po_line_id, order_date FROM purchase_order_line
    WHERE NOT is_closed ORDER BY po_line_id LIMIT 1
    """
)
DELETE_PREDICTION_SQL = text(
    "DELETE FROM held_out_prediction WHERE run_id = :run_id AND po_line_id = :po_line_id"
)
PLANT_PREDICTION_SQL = text(
    """
    INSERT INTO held_out_prediction (
        run_id, po_line_id, draw_count, horizon_days,
        anchor_date, line_is_closed, anchor_convention, duration_semantic,
        draws, survival, residual_tail_mass, draw_digest
    )
    VALUES (
        :run_id, :po_line_id, :draw_count, :horizon_days,
        :anchor_date, :line_is_closed, :anchor_convention, :duration_semantic,
        :draws, :survival, :residual_tail_mass, :draw_digest
    )
    """
)

#: PostgreSQL's `pg_constraint` codes for the three referential properties this
#: key departs from convention on. `r` is RESTRICT, `f` is MATCH FULL.
RESTRICT_ACTION = "r"
MATCH_FULL = "f"

#: The three referencing columns and the three they resolve against, in order.
REFERENCING_COLUMNS = ("po_line_id", "anchor_date", "line_is_closed")
REFERENCED_COLUMNS = ("po_line_id", "order_date", "is_closed")


def _a_stored_prediction(db_session: Session, emitted_run: EmittedRun):
    """One committed prediction, used as the template every plant is a variant of.

    A real stored row rather than a hand-built one, so a plant differs from an
    accepted row in exactly the value under test — every array, every label and
    the digest are the ones the database already holds, and nothing else can be
    the reason a plant is refused.
    """
    row = (
        db_session.execute(A_STORED_PREDICTION_SQL, {"run_id": emitted_run.run_id})
        .mappings()
        .first()
    )

    assert row is not None, (
        "the shared run stored no held-out prediction, so there is no accepted row to plant "
        "a variant of and the rejection below would prove nothing"
    )
    return row


def _plant(db_session: Session, emitted_run: EmittedRun, **overrides) -> None:
    """Issue one planted `INSERT`, letting whatever the server raises propagate."""
    row = _a_stored_prediction(db_session, emitted_run)
    parameters = {
        "run_id": emitted_run.run_id,
        "po_line_id": row["po_line_id"],
        "draw_count": row["draw_count"],
        "horizon_days": row["horizon_days"],
        "anchor_date": row["anchor_date"],
        "line_is_closed": True,
        "anchor_convention": row["anchor_convention"],
        "duration_semantic": row["duration_semantic"],
        "draws": list(row["draws"]),
        "survival": list(row["survival"]),
        "residual_tail_mass": float(row["residual_tail_mass"]),
        "draw_digest": bytes(row["draw_digest"]),
        **overrides,
    }
    # The template's own line already holds a row under this run, so the plant
    # would otherwise collide with `pk_held_out_prediction` and be refused for a
    # reason that has nothing to do with the anchor.
    db_session.execute(
        DELETE_PREDICTION_SQL,
        {"run_id": emitted_run.run_id, "po_line_id": parameters["po_line_id"]},
    )
    db_session.execute(PLANT_PREDICTION_SQL, parameters)


# ---------------------------------------------------------------------------
# T058 — the positive control: the constraint is present and is what it claims
# ---------------------------------------------------------------------------


def test_the_anchor_foreign_key_is_present_with_the_shape_the_data_model_declares(
    db_session: Session,
) -> None:
    """DV-023's positive control: a dropped or renamed key fails here.

    Read out of `pg_constraint` rather than probed by behaviour, because the
    three properties below are separately droppable and only one of them shows up
    in a rejection test. `MATCH FULL` is equivalent to MATCH SIMPLE here — all
    three referencing columns are NOT NULL — and is asserted anyway, because that
    equivalence is a property of the *columns* and would stop holding the moment
    one of them became nullable.
    """
    row = db_session.execute(ANCHOR_CONSTRAINT_SQL).mappings().first()

    assert row is not None, (
        "`fk_held_out_prediction__line_anchor` does not exist. The anchor is then a plain "
        "column with a comment beside it, and a prediction graded against the wrong origin "
        "is representable — G-14 records exactly this as the rejected cheaper alternative"
    )
    assert row["contype"] == "f"
    assert row["on_table"] == "held_out_prediction"
    assert row["references_table"] == "purchase_order_line"
    assert row["confmatchtype"] == MATCH_FULL
    for column in REFERENCING_COLUMNS + REFERENCED_COLUMNS:
        assert column in row["definition"], (
            f"{column!r} is absent from {row['definition']!r}; the key must carry all three "
            f"columns, or the anchor is proved of a narrower tuple than it claims"
        )


def test_the_anchor_key_refuses_to_cascade_a_corrected_order_date(db_session: Session) -> None:
    """`ON UPDATE RESTRICT`, the deliberate departure from E003's convention.

    E003 cascades on composite keys whose parent has a mutable column, so a
    legitimate correction propagates. Here it must not: cascading a corrected
    `order_date` would silently re-anchor draws computed against the old one,
    producing exactly the mis-anchored row this key exists to prevent. Refusing
    forces a refit. Asserted because a later revision "harmonising" the actions
    with the rest of the schema would look like tidying and would reopen the hole.
    """
    row = db_session.execute(ANCHOR_CONSTRAINT_SQL).mappings().one()

    assert row["confupdtype"] == RESTRICT_ACTION
    assert row["confdeltype"] == RESTRICT_ACTION


def test_the_foreign_keys_target_exists_on_the_delivered_table(db_session: Session) -> None:
    """The key needs a unique constraint to resolve against, and that is G-14.

    `uq_purchase_order_line__order_anchor` is E007 adding an object to another
    epic's delivered table. It is additive and rejects no previously legal row —
    its leading column is already the primary key — but the foreign key above is
    unapplicable without it, so its absence would be the same hole by a different
    route.
    """
    definition = db_session.execute(ORDER_ANCHOR_TARGET_SQL).scalar_one_or_none()

    assert definition is not None, "`uq_purchase_order_line__order_anchor` does not exist"
    for column in REFERENCED_COLUMNS:
        assert column in definition


def test_every_stored_anchor_is_its_own_lines_order_date(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """The passing direction, over the rows the shared run committed.

    Implied by the foreign key and asserted anyway, for the reason the two halves
    of this file exist: the constraint proves it of every stored row, and this
    proves the constraint was in force when these particular rows were written.
    """
    rows = db_session.execute(STORED_ANCHORS_SQL, {"run_id": emitted_run.run_id}).mappings().all()

    assert rows, "the shared run stored no held-out prediction to check an anchor of"
    for row in rows:
        assert row["anchor_date"] == row["order_date"], (
            f"line {row['po_line_id']} is anchored at {row['anchor_date']} against an order "
            f"date of {row['order_date']}"
        )
        assert row["line_is_closed"] is True
        assert row["is_closed"] is True


# ---------------------------------------------------------------------------
# T059 — NC-5: the planted row the constraint must refuse
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("offset_days", [-1, 1, 400])
def test_a_planted_row_anchored_off_its_lines_order_date_is_rejected(
    db_session: Session, emitted_run: EmittedRun, offset_days: int
) -> None:
    """**NC-5 / SC-002.** The failing direction, without which T058 proves nothing.

    A row identical to an accepted one except for its `anchor_date` satisfies
    every check on the table — the labels, both array shapes, the sorted draws,
    the monotone survival curve, the residual agreement, the digest length — and
    is refused by the foreign key alone. A single day off is enough, and is the
    case a test comparing dates by eye would be least likely to catch.

    `IntegrityError` rather than a bare exception: it is SQLAlchemy's wrapper for
    the server's referential-integrity class, so a plant refused for some other
    reason — a check, a duplicate key — fails this test rather than passing it.
    """
    row = _a_stored_prediction(db_session, emitted_run)
    moved = row["anchor_date"] + timedelta(days=offset_days)

    with pytest.raises(IntegrityError) as refused, db_session.begin_nested():
        _plant(db_session, emitted_run, anchor_date=moved)

    assert "fk_held_out_prediction__line_anchor" in str(refused.value), (
        f"the plant was refused by something other than the anchor key: {refused.value}"
    )


def test_a_planted_row_naming_a_line_that_has_not_delivered_is_rejected(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """The other half the composite key carries: the line actually delivered.

    `line_is_closed` rides in the referenced tuple, so the delivered
    `ck_pol__closed_iff_delivered` biconditional reaches this table — a
    prediction can only name a line that has a terminal event, which is what
    makes it gradeable at all. The plant uses a real open line with its real
    order date, so the anchor is correct and the *only* thing wrong is that the
    line has not delivered.
    """
    open_line = db_session.execute(AN_OPEN_LINE_SQL).mappings().first()

    assert open_line is not None, "no open line exists, so this plant cannot be constructed"

    with pytest.raises(IntegrityError) as refused, db_session.begin_nested():
        _plant(
            db_session,
            emitted_run,
            po_line_id=open_line["po_line_id"],
            anchor_date=open_line["order_date"],
        )

    assert "fk_held_out_prediction__line_anchor" in str(refused.value)


def test_a_planted_row_claiming_the_line_is_open_is_rejected_by_its_own_check(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """`ck_held_out_prediction__line_delivered`, which is redundant and is worth having.

    Setting the flag false makes the referenced tuple unresolvable too, so the
    foreign key would refuse it — but the check fires on the row itself, so the
    server names the actual rule rather than reporting a reference to a tuple
    that does not exist. That is the whole reason the data model declares both.
    """
    with pytest.raises(IntegrityError) as refused, db_session.begin_nested():
        _plant(db_session, emitted_run, line_is_closed=False)

    assert "ck_held_out_prediction__line_delivered" in str(refused.value)


def test_the_unaltered_template_row_is_accepted(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """The control on the controls: the plant machinery itself stores a legal row.

    Without this, every rejection above is satisfied by a helper that builds an
    unstorable row for some reason nobody stated, and the constraint under test
    would never have been the thing that refused.
    """
    with db_session.begin_nested():
        _plant(db_session, emitted_run)
        stored = (
            db_session.execute(A_STORED_PREDICTION_SQL, {"run_id": emitted_run.run_id})
            .mappings()
            .first()
        )

        assert stored is not None
