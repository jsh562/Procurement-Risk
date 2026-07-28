"""T057 — DV-002 / SC-013: the held-out population, as stored rather than as built.

One `held_out_prediction` row per line that is **held out and already
delivered**, exactly one, and no row for any other line. Asserted over the rows
the shared `forecast-fit` invocation committed, against the membership rule
recomputed by SQL from `forecast_split_assignment` and `purchase_order_line` —
never against a count the job reported, which would compare the run with itself.

**Membership is the split side and the loader's `is_closed` column**, and the
column rather than `censoring.py`'s dated indicator on purpose:
`fk_held_out_prediction__line_anchor` references
`(po_line_id, order_date, is_closed)`, so the column is what every stored row is
already proved against. Selecting on a different reading of "delivered" here
would assert against a population no constraint holds of.

Half of "no other line" is structural — `ck_held_out_prediction__line_delivered`
plus the anchor foreign key — and half is not: nothing stops the writer omitting
a line it should have written. That half is this file's, and SC-013's recording
clause is here too, because a population whose semantic is not on the record is
one a reader has to infer.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from forecast.conftest import EmittedRun
from model.forecast.write import HELD_OUT_ANCHOR_CONVENTION, HELD_OUT_DURATION_SEMANTIC

#: Module-level SQL, never assembled from values (Ruff S608).
PREDICTION_LINES_SQL = text(
    "SELECT po_line_id FROM held_out_prediction WHERE run_id = :run_id"
)
HELD_OUT_DELIVERED_SQL = text(
    """
    SELECT a.po_line_id
    FROM forecast_split_assignment a
    JOIN purchase_order_line l ON l.po_line_id = a.po_line_id
    WHERE a.run_id = :run_id AND a.split_side = 'held_out' AND l.is_closed
    """
)
ASSIGNED_LINES_SQL = text(
    "SELECT po_line_id FROM forecast_split_assignment WHERE run_id = :run_id"
)
PREDICTION_COUNTS_SQL = text(
    """
    SELECT count(*) AS rows_written, count(DISTINCT po_line_id) AS lines_covered
    FROM held_out_prediction WHERE run_id = :run_id
    """
)
PREDICTION_RUN_JOIN_SQL = text(
    """
    SELECT count(*) AS predictions, count(r.run_id) AS runs_joined,
           count(DISTINCT h.run_id) AS distinct_runs
    FROM held_out_prediction h
    JOIN forecast_run r ON r.run_id = h.run_id
    WHERE h.run_id = :run_id
    """
)
RECORDED_SEMANTICS_SQL = text(
    """
    SELECT DISTINCT anchor_convention, duration_semantic
    FROM held_out_prediction WHERE run_id = :run_id
    """
)
OPEN_SEMANTIC_SQL = text(
    "SELECT open_line_draw_semantic FROM forecast_run WHERE run_id = :run_id"
)


def _stored_lines(db_session: Session, emitted_run: EmittedRun) -> set:
    """The lines the run wrote a held-out prediction for."""
    return set(
        db_session.execute(PREDICTION_LINES_SQL, {"run_id": emitted_run.run_id}).scalars()
    )


def _expected_lines(db_session: Session, emitted_run: EmittedRun) -> set:
    """The membership rule, evaluated by the database over its own two tables."""
    return set(
        db_session.execute(HELD_OUT_DELIVERED_SQL, {"run_id": emitted_run.run_id}).scalars()
    )


def test_every_held_out_delivered_line_carries_a_stored_prediction(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """DV-002's forward direction, over stored rows.

    Set equality rather than a count comparison: two counts agree while naming
    different lines, and a prediction written for the wrong line is graded
    against the wrong outcome with every constraint on the row still passing.
    """
    stored = _stored_lines(db_session, emitted_run)
    expected = _expected_lines(db_session, emitted_run)

    assert expected, (
        "no line is both held out and delivered under this run, so this test would pass "
        "vacuously; the committed dataset splits 175 delivered lines at 0.25, which is "
        "roughly 44 gradeable predictions (L-3)"
    )
    assert stored == expected, (
        f"the stored held-out population differs from the one the split and the delivered "
        f"closure column describe: {len(expected - stored)} held-out delivered line(s) have "
        f"no prediction and {len(stored - expected)} stored row(s) name a line that is not "
        f"held out, or that has not delivered"
    )


def test_no_other_line_carries_a_stored_prediction(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """DV-002's other half, stated as a disjointness over the assigned population.

    The structural part of this is real and is not what is being tested: a line
    that has not delivered cannot receive a row, because
    `ck_held_out_prediction__line_delivered` pins the flag true and the anchor
    foreign key resolves it against `purchase_order_line`. What no constraint
    reaches is a **training** delivered line — it satisfies the flag, it has an
    order date, and its prediction would be scored against a line the model was
    fitted on.
    """
    stored = _stored_lines(db_session, emitted_run)
    assigned = set(
        db_session.execute(ASSIGNED_LINES_SQL, {"run_id": emitted_run.run_id}).scalars()
    )
    expected = _expected_lines(db_session, emitted_run)
    others = assigned - expected

    assert others, "every assigned line is held out and delivered, so this asserts nothing"
    assert not (stored & others), (
        f"{len(stored & others)} stored prediction(s) name a line that is not in the "
        f"held-out delivered population — a training line's prediction is graded against a "
        f"line the fit already saw, which is FR-007 undone at the storage layer"
    )


def test_each_held_out_line_carries_exactly_one_row_under_the_run(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """"Exactly one" is half of DV-002 and is not implied by the set equality above.

    `pk_held_out_prediction` is `(run_id, po_line_id)`, so a duplicate is
    unstorable; this asserts the key is doing that job rather than that the
    writer happened not to try, because the two counts below are equal only if
    no line was written twice under this run.
    """
    counts = db_session.execute(
        PREDICTION_COUNTS_SQL, {"run_id": emitted_run.run_id}
    ).mappings().one()

    assert counts["rows_written"] == counts["lines_covered"]
    assert counts["rows_written"] == len(_expected_lines(db_session, emitted_run))


def test_every_stored_prediction_is_joinable_to_exactly_one_run(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """SC-013's "each joinable to exactly one run", as a join rather than a claim.

    `fk_held_out_prediction__run_shape` makes an orphan unrepresentable and the
    primary key makes a second run's row a different row; what this measures is
    that the join actually resolves for every row the run wrote — an inner join
    that loses a row would report a prediction attached to nothing.
    """
    row = db_session.execute(
        PREDICTION_RUN_JOIN_SQL, {"run_id": emitted_run.run_id}
    ).mappings().one()

    assert row["predictions"] > 0
    assert row["runs_joined"] == row["predictions"]
    assert row["distinct_runs"] == 1


def test_each_population_records_its_own_anchor_convention_and_duration_semantic(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """SC-013's recording clause, for both populations in one place.

    The held-out population carries its labels **per row**, because its anchor is
    a per-line date; the open population carries its semantic on the run, because
    it lives in the delivered `line_posterior` which E007 may not alter and
    because the semantic is a property of the run's whole open set.

    Recording is not measuring, and this test claims only the recording — both
    columns are single-value checks that a re-anchored implementation satisfies
    identically. The anchor's measured counterpart is DV-023's rejection control
    and the duration's is DV-040.
    """
    recorded = db_session.execute(
        RECORDED_SEMANTICS_SQL, {"run_id": emitted_run.run_id}
    ).mappings().all()
    open_semantic = db_session.execute(
        OPEN_SEMANTIC_SQL, {"run_id": emitted_run.run_id}
    ).scalar_one()

    assert len(recorded) == 1, (
        f"the run's held-out rows carry {len(recorded)} distinct label pairs; one population "
        f"has one anchor convention and one duration semantic"
    )
    assert recorded[0]["anchor_convention"] == HELD_OUT_ANCHOR_CONVENTION
    assert recorded[0]["duration_semantic"] == HELD_OUT_DURATION_SEMANTIC
    assert open_semantic != recorded[0]["duration_semantic"], (
        "the two populations record the same duration semantic, so the record no longer "
        "distinguishes a conditional remainder from a total duration — which is the whole "
        "of what ADR-0018 separates the stores to keep distinguishable"
    )
