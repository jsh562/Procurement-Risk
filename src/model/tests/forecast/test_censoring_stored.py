"""T048 — DV-029 / SC-007: the *stored* censoring indicator against its source.

FR-004 requires the indicator and the as-of date it was derived from to be
stored, and forbids re-inferring censoring at read time. That is what leaves
`forecast_split_assignment.is_censored` unchecked against the events it came
from: nothing downstream reads those events again, so a wrong value is a wrong
value forever. DV-029 closes it once, over the stored rows, by a path
independent of the one that wrote them.

**The independent path is SQL over `lifecycle_event`, reached through
`run_id`.** `censoring_indicator` is deliberately not called: the job derived
the stored value with it, and comparing the column against the function that
produced it would assert that one expression is itself. The recomputation here
joins each assignment row to its line's terminal event and to *the run's own*
`as_of_date`, which is the only date FR-004 says the answer is meaningful at.

**The discriminating case is constructed, because the delivered input does not
carry one at the shared anchor.** Every terminal event in the committed dataset
falls on or before 2026-03-31, so at 2026-04-01 `is_censored` and `NOT
is_closed` agree on all 199 lines and the whole rule would be satisfied by a
writer that copied the loader's column. A line whose terminal event postdates
the anchor separates them: `is_closed` is the load snapshot's answer to an
undated question, and the run asks a dated one. That case is produced here by
moving one closed line's terminal event past the anchor inside the test's own
rolled-back transaction, then storing a variant assignment set through the real
writer.
"""

from __future__ import annotations

import dataclasses
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from forecast.conftest import EmittedRun, StoredRun
from model.forecast.read import read_lines_and_events
from model.forecast.serialize import input_data_hash
from model.forecast.split import TRAIN, assign_split
from model.forecast.write import insert_artifact_set

#: Module-level SQL, never assembled from values (Ruff S608).
#:
#: The recomputation. `LEFT JOIN` rather than `EXISTS` so a line with no
#: terminal event at all and a line whose terminal event has not happened yet
#: reach the comparison by the same route — they are the same answer to FR-003's
#: dated question, and a query that treated them differently would be two rules
#: sharing a column. The anchor comes from `forecast_run` through `run_id`, so
#: the date the indicator is judged at is the run's own and never this file's.
STORED_AGAINST_EVENTS_SQL = text(
    """
    SELECT a.po_line_id,
           a.is_censored AS stored,
           (t.event_id IS NULL) AS recomputed,
           l.is_closed
    FROM forecast_split_assignment a
    JOIN forecast_run r ON r.run_id = a.run_id
    JOIN purchase_order_line l ON l.po_line_id = a.po_line_id
    LEFT JOIN lifecycle_event t
           ON t.po_line_id = a.po_line_id
          AND t.is_terminal
          AND (t.occurred_at AT TIME ZONE 'UTC')::date <= r.as_of_date
    WHERE a.run_id = :run_id
    ORDER BY a.canonical_ordinal
    """
)

#: The same recomputation at a date supplied by the caller rather than by the
#: run. Used only by the non-vacuity control: a rule that agreed at every anchor
#: would be agreeing with something other than the dated question.
EVENTS_AT_ARBITRARY_ANCHOR_SQL = text(
    """
    SELECT a.po_line_id, (t.event_id IS NULL) AS recomputed
    FROM forecast_split_assignment a
    LEFT JOIN lifecycle_event t
           ON t.po_line_id = a.po_line_id
          AND t.is_terminal
          AND (t.occurred_at AT TIME ZONE 'UTC')::date <= :anchor
    WHERE a.run_id = :run_id
    """
)

RUN_ANCHOR_SQL = text("SELECT as_of_date FROM forecast_run WHERE run_id = :run_id")

NULL_INDICATOR_COUNT_SQL = text(
    """
    SELECT count(*) FROM forecast_split_assignment
    WHERE run_id = :run_id AND is_censored IS NULL
    """
)

#: One line that is closed at the shared anchor, chosen by canonical ordinal so
#: the choice is a property of the dataset rather than of the planner's mood.
CLOSED_LINE_AT_ANCHOR_SQL = text(
    """
    SELECT l.po_line_id, l.closing_event_id
    FROM purchase_order_line l
    JOIN forecast_split_assignment a
      ON a.po_line_id = l.po_line_id AND a.run_id = :run_id
    JOIN lifecycle_event t ON t.event_id = l.closing_event_id
    WHERE l.is_closed
      AND (t.occurred_at AT TIME ZONE 'UTC')::date <= :anchor
    ORDER BY a.canonical_ordinal
    LIMIT 1
    """
)

MOVE_TERMINAL_EVENT_SQL = text(
    "UPDATE lifecycle_event SET occurred_at = :occurred_at WHERE event_id = :event_id"
)

ONE_STORED_ASSIGNMENT_SQL = text(
    """
    SELECT a.is_censored, l.is_closed
    FROM forecast_split_assignment a
    JOIN purchase_order_line l ON l.po_line_id = a.po_line_id
    WHERE a.run_id = :run_id AND a.po_line_id = :po_line_id
    """
)

#: How far past the anchor the moved terminal event lands. Any positive number
#: works; 45 days is far enough that a timezone conversion could not account for
#: the difference, which keeps the case about the date rather than about an hour.
TERMINAL_EVENT_OFFSET_DAYS = 45

#: How far before the run's anchor the non-vacuity control asks the same
#: question. Two months of this dataset's deliveries, so the two answers cannot
#: coincide by there being nothing between the dates.
CONTROL_LOOKBACK_DAYS = 60


@dataclass(frozen=True, slots=True)
class MovedTerminalEvent:
    """One line whose terminal event was pushed past the anchor, and its runs.

    `shipped_run_id` is the run that was written *before* the move and
    `variant_run_id` the one written after it, so the pair holds the same line's
    stored answer under two different event histories. Both live inside the
    test's transaction and neither is committed.
    """

    po_line_id: uuid.UUID
    moved_to: date
    variant_run_id: uuid.UUID


def _rows(db_session: Session, run_id: uuid.UUID):
    """Every assignment row of one run beside its recomputation from the events."""
    return db_session.execute(STORED_AGAINST_EVENTS_SQL, {"run_id": run_id}).mappings().all()


def _disagreements(rows) -> list:
    """The rows whose stored indicator differs from the recomputed one."""
    return [row for row in rows if bool(row["stored"]) != bool(row["recomputed"])]


@pytest.fixture
def moved_terminal_event(
    db_session: Session, emitted_run: EmittedRun, stored_run: StoredRun
) -> MovedTerminalEvent:
    """A closed line whose terminal event now postdates the run's anchor.

    The event is moved first, then a *second* assignment set is derived over the
    changed rows by the same `assign_split` the job calls and stored through the
    real `insert_artifact_set`. The variant carries the shipped run's own
    artifact rows: this file's subject is `forecast_split_assignment`, and
    DV-001's population claim is `test_open_population.py`'s to make.
    """
    parameters = {"run_id": emitted_run.run_id, "anchor": emitted_run.as_of_date}
    chosen = db_session.execute(CLOSED_LINE_AT_ANCHOR_SQL, parameters).mappings().one()
    moved_to = emitted_run.as_of_date + timedelta(days=TERMINAL_EVENT_OFFSET_DAYS)
    db_session.execute(
        MOVE_TERMINAL_EVENT_SQL,
        {
            "event_id": chosen["closing_event_id"],
            "occurred_at": datetime.combine(moved_to, time(12, 0), tzinfo=UTC),
        },
    )

    procurement_input = read_lines_and_events(db_session)
    row_hash = input_data_hash(procurement_input)
    split = assign_split(procurement_input.lines, emitted_run.as_of_date, row_hash)
    training = sum(1 for row in split.assignments if row.split_side == TRAIN)
    manifest = dataclasses.replace(
        stored_run.manifest,
        run_id=uuid.uuid4(),
        input_data_hash=row_hash,
        split_assignment_hash=split.split_assignment_hash,
        training_line_count=training,
        held_out_fraction_realized=1.0 - training / len(split.assignments),
        open_line_count=len(stored_run.line_posteriors),
    )
    insert_artifact_set(db_session, manifest, split.assignments, stored_run.line_posteriors)
    return MovedTerminalEvent(
        po_line_id=chosen["po_line_id"], moved_to=moved_to, variant_run_id=manifest.run_id
    )


# ---------------------------------------------------------------------------
# DV-029 over the shipped run
# ---------------------------------------------------------------------------


def test_every_stored_indicator_agrees_with_the_lifecycle_events_it_came_from(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """DV-029, row by row, over the run the shared invocation committed.

    Compared per line rather than by counting censored rows: two counts agree
    while naming different lines, and a line stored open that had delivered
    contributes a survival term where it owes a density — the failure FR-003
    calls out in both directions and the one no aggregate would show.
    """
    rows = _rows(db_session, emitted_run.run_id)

    assert rows, "the shared run stored no split assignment to compare"
    assert not _disagreements(rows), (
        f"{len(_disagreements(rows))} of {len(rows)} stored `is_censored` values disagree "
        f"with `lifecycle_event` at {emitted_run.as_of_date} — first "
        f"{_disagreements(rows)[0]['po_line_id']}. The stored indicator is never re-derived "
        f"downstream (FR-004), so a wrong value here is wrong for the life of the run"
    )


def test_both_answers_actually_occur_so_the_agreement_is_not_vacuous(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """A run in which every line is censored would satisfy the rule above trivially.

    Both values have to be present for the comparison to have separated
    anything, and the open side additionally has to be non-empty for the run to
    exist at all — `ck_forecast_run__open_line_count_positive`.
    """
    stored = [bool(row["stored"]) for row in _rows(db_session, emitted_run.run_id)]

    assert any(stored)
    assert not all(stored)


def test_the_recomputation_answers_differently_at_a_different_anchor(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """The control on the *recomputation*: it has to be a function of the date.

    A query that returned the same set at every anchor would agree with the
    stored column for a reason that has nothing to do with censoring, and DV-029
    would pass while asserting only that two constants match. Sixty days earlier
    this dataset has strictly more lines still open.
    """
    parameters = {"run_id": emitted_run.run_id}
    at_anchor = {
        row["po_line_id"]
        for row in _rows(db_session, emitted_run.run_id)
        if bool(row["recomputed"])
    }
    earlier = {
        row["po_line_id"]
        for row in db_session.execute(
            EVENTS_AT_ARBITRARY_ANCHOR_SQL,
            parameters | {"anchor": emitted_run.as_of_date - timedelta(days=CONTROL_LOOKBACK_DAYS)},
        ).mappings()
        if bool(row["recomputed"])
    }

    assert at_anchor < earlier


def test_the_run_records_the_as_of_date_the_indicator_was_derived_from(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """FR-004's other half: the indicator is stored *with* the date it answers at.

    Censoring is meaningless without an anchor, so a stored indicator whose date
    was not also stored could not be checked by this file or by anyone else. The
    date is read through `run_id` — the same join the recomputation uses — and
    no assignment row may leave the indicator itself unstored.
    """
    anchor = db_session.execute(RUN_ANCHOR_SQL, {"run_id": emitted_run.run_id}).scalar_one()
    unstored = db_session.execute(
        NULL_INDICATOR_COUNT_SQL, {"run_id": emitted_run.run_id}
    ).scalar_one()

    assert anchor == emitted_run.as_of_date
    assert unstored == 0


# ---------------------------------------------------------------------------
# The discriminating case: `is_closed` against the dated question
# ---------------------------------------------------------------------------


def test_a_line_closed_by_an_event_after_the_anchor_is_stored_censored(
    db_session: Session, moved_terminal_event: MovedTerminalEvent
) -> None:
    """The case `is_closed` gets wrong, and the reason the column is not the source.

    The loader's flag is a snapshot answer to an undated question; the run asks
    a dated one. This line is still marked closed and its terminal event now
    falls 45 days past the anchor, so at the anchor it had not delivered and the
    stored indicator must say censored — against the column, not with it.
    """
    row = (
        db_session.execute(
            ONE_STORED_ASSIGNMENT_SQL,
            {
                "run_id": moved_terminal_event.variant_run_id,
                "po_line_id": moved_terminal_event.po_line_id,
            },
        )
        .mappings()
        .one()
    )

    assert row["is_closed"] is True
    assert row["is_censored"] is True, (
        f"line {moved_terminal_event.po_line_id} is marked closed by an event on "
        f"{moved_terminal_event.moved_to}, after the run's anchor, and was stored uncensored. "
        f"That is `is_closed` answering for the dated question FR-003 asks"
    )


def test_the_variant_run_still_agrees_with_the_events_over_every_other_line(
    db_session: Session, moved_terminal_event: MovedTerminalEvent
) -> None:
    """DV-029 again over the constructed run, so the case is not a special one.

    The moved event changes exactly one line's answer. If the whole assignment
    set no longer agreed with `lifecycle_event`, the case above would be
    evidence about a broken derivation rather than about the distinction between
    a dated question and a snapshot column.
    """
    rows = _rows(db_session, moved_terminal_event.variant_run_id)

    assert len(rows) > 1
    assert not _disagreements(rows)


def test_the_shipped_runs_stored_answer_does_not_move_when_the_events_move(
    db_session: Session, emitted_run: EmittedRun, moved_terminal_event: MovedTerminalEvent
) -> None:
    """ "Stored, and not re-inferred at read time" — made observable (FR-004).

    The same line is read back under the run that was written *before* the event
    moved. Its answer is still the one that was true when the run was written,
    because the value is a stored column rather than something recomputed on the
    way out; a read-time derivation would now report the new events against the
    old run's anchor and quietly rewrite a published run's split.
    """
    row = (
        db_session.execute(
            ONE_STORED_ASSIGNMENT_SQL,
            {"run_id": emitted_run.run_id, "po_line_id": moved_terminal_event.po_line_id},
        )
        .mappings()
        .one()
    )

    assert row["is_censored"] is False
