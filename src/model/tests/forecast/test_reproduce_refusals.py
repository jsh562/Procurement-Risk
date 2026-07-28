"""T101 — NC-3 / DV-015 / DV-017 / SC-019: a moved digest refuses, and names itself.

Two cases, and the point of there being two is that each must name **that**
input. A mutated procurement row moves the input row hash; a mutated split
assignment moves the split assignment hash; and a refusal that said only "an
input hash moved" would send an operator to look at the wrong one.

The two are separable because they are recomputed from different things. The row
hash covers the rows the fit read, taken over E005's compared-content projection;
the split hash is recomputed from the stored `forecast_split_assignment` rows
under the recorded serialization, which is DV-017's own form. `description` sits
inside the first projection and outside the second, and `split_side` sits inside
the second and is not a procurement column at all, so each mutation moves exactly
one digest.

**Both refusals are pre-sampling**, so this file costs no fit. The mutations are
issued inside the tier's rolled-back transaction and the job is driven over that
same session, which is sound because `forecast-reproduce` writes nothing: it
reads, samples in memory, and emits one file. Committing a mutation of E005's
rows to reach the same assertion would be a mutation of data of record.
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from forecast.conftest import EmittedRun
from model.forecast.paths import REFUSAL_REPORT_PREFIX
from model.forecast.read import read_lines_and_events
from model.forecast.reproduce import (
    ReproductionRefusal,
    read_recorded_run,
    run_reproduce,
)
from model.forecast.serialize import input_data_hash

#: Module-level SQL, never assembled from values (Ruff S608).
MOVE_ONE_ROW_SQL = text(
    """
    UPDATE purchase_order_line SET description = description || :suffix
    WHERE po_line_id = (SELECT po_line_id FROM purchase_order_line
                        ORDER BY project_id, po_number, line_number LIMIT 1)
    """
)
MOVE_ONE_SPLIT_ASSIGNMENT_SQL = text(
    """
    UPDATE forecast_split_assignment
    SET split_side = CASE split_side WHEN 'train' THEN 'held_out' ELSE 'train' END
    WHERE run_id = :run_id
      AND canonical_ordinal = (SELECT min(canonical_ordinal)
                               FROM forecast_split_assignment WHERE run_id = :run_id)
    """
)

#: A suffix on one line's description. Inside E005's compared-content
#: projection, so the row hash moves; outside `SPLIT_ASSIGNMENT_FIELDS`, so the
#: stored split's own digest does not.
ROW_MUTATION_SUFFIX = " (moved for DV-015)"

#: What each refusal must name. The words a reader searches the message for,
#: rather than the whole sentence — the assertion is that the *right input* is
#: identified, not that the prose is unchanged.
ROW_HASH_LABEL = "input row hash"
SPLIT_HASH_LABEL = "split assignment hash"


def reproduce_refusal(
    db_session: Session, emitted_run: EmittedRun, report_root: Path
) -> ReproductionRefusal:
    """Drive the job against the mutated session and return the refusal it raised.

    Raised rather than returned as a verdict, because a refusal is not a
    comparison with a negative outcome — nothing was sampled and nothing was
    compared, and the two must not be reportable as the same thing.
    """
    with pytest.raises(ReproductionRefusal) as caught:
        run_reproduce(
            db_session, emitted_run.run_id, report_root=report_root, log=io.StringIO()
        )
    return caught.value


def test_a_mutated_row_refuses_and_names_the_input_row_hash(
    db_session: Session, emitted_run: EmittedRun, report_root: Path
) -> None:
    """DV-015's case: the rows are not the rows this run was fitted from.

    Both values are asserted present — the recorded digest and the one the rows
    now serialize to — because FR-023's field set is "which hash moved **and both
    values**", and a message naming only the mismatch leaves an operator without
    the number to compare against.
    """
    recorded = read_recorded_run(db_session, emitted_run.run_id)
    db_session.execute(MOVE_ONE_ROW_SQL, {"suffix": ROW_MUTATION_SUFFIX})
    moved = input_data_hash(read_lines_and_events(db_session))

    refusal = reproduce_refusal(db_session, emitted_run, report_root)
    message = str(refusal)

    assert moved != recorded.input_data_hash
    assert ROW_HASH_LABEL in message
    assert recorded.input_data_hash in message
    assert moved in message
    assert SPLIT_HASH_LABEL not in message, (
        "the refusal named the split as well. The split is keyed on the input row hash, so "
        "it necessarily re-derives differently — reporting it beside the cause names a "
        "consequence and leaves a reader unable to tell which input actually moved"
    )


def test_a_mutated_split_assignment_refuses_and_names_the_split(
    db_session: Session, emitted_run: EmittedRun, report_root: Path
) -> None:
    """DV-017's case: the held-out split this run was trained against has moved.

    The row hash is asserted *unchanged* in the same breath, which is what makes
    this the split's case rather than a second row case: the mutation is to
    `forecast_split_assignment`, a table the input row hash does not cover.
    """
    recorded = read_recorded_run(db_session, emitted_run.run_id)
    before = input_data_hash(read_lines_and_events(db_session))
    db_session.execute(MOVE_ONE_SPLIT_ASSIGNMENT_SQL, {"run_id": emitted_run.run_id})

    refusal = reproduce_refusal(db_session, emitted_run, report_root)
    message = str(refusal)

    assert before == recorded.input_data_hash
    assert input_data_hash(read_lines_and_events(db_session)) == recorded.input_data_hash
    assert SPLIT_HASH_LABEL in message
    assert recorded.split_assignment_hash in message
    assert ROW_HASH_LABEL not in message, (
        "the refusal named the row hash on a run whose rows are unchanged; the two "
        "dispositions must be separable or SC-019's 'names which one moved' is unmeetable"
    )


def test_each_refusal_carries_the_two_field_set_and_emits_its_report(
    db_session: Session, emitted_run: EmittedRun, report_root: Path
) -> None:
    """FR-037 / SC-033: the durable half of the record, with the same field set.

    A refused reproduction writes no row anywhere — this job writes none even
    when it succeeds — so the stderr text and this file are the whole of the
    evidence. The field set is FR-017's **two**-field form, because a recorded
    digest is a precondition rather than a measured metric: there is no threshold
    direction to report and none is rendered.
    """
    recorded = read_recorded_run(db_session, emitted_run.run_id)
    db_session.execute(MOVE_ONE_ROW_SQL, {"suffix": ROW_MUTATION_SUFFIX})

    refusal = reproduce_refusal(db_session, emitted_run, report_root)
    emitted = sorted(report_root.iterdir())
    body = emitted[0].read_text(encoding="utf-8")

    assert len(refusal.preconditions) == 1
    assert refusal.preconditions[0].precondition.strip()
    assert refusal.preconditions[0].realized_value.strip()

    assert len(emitted) == 1
    assert emitted[0].name.startswith(f"{REFUSAL_REPORT_PREFIX}-")
    assert "- **Precondition**:" in body
    assert "- **Realized value**:" in body
    assert "- **Threshold direction**:" not in body
    assert recorded.input_data_hash in body
    assert "nothing was sampled" in body


def test_the_unmutated_run_reaches_the_gate_without_refusing(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """The positive control, and it costs no fit either.

    Both assertions above are satisfied by a gate that refuses everything, which
    would be indistinguishable from a working one until a reproduction was
    actually wanted. This checks the same recomputations against an unmutated
    database and finds them equal — the shared reproduction in
    `test_reproduction.py` is what then carries the claim through to a verdict.
    """
    recorded = read_recorded_run(db_session, emitted_run.run_id)

    assert input_data_hash(read_lines_and_events(db_session)) == recorded.input_data_hash
    assert recorded.split_assignment_hash
    assert recorded.artifacts["line_posterior"]
