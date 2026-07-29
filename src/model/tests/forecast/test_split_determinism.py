"""T069 — NC-13 / AD-011 / FR-005: the split hash reproduces, and moves when it should.

What makes the split *evidence* rather than a by-product is not the order it is
written in. Ordering inside one transaction has no external visibility, and
committing the split before the fit is prohibited outright, since SC-015 requires
a refused run to leave no row in any store. What rules out a split chosen to suit
a fit is that there is no freedom left to exercise: the assignment is a pure
function of `input_data_hash` and two committed configuration constants,
`HELD_OUT_FRACTION` and `SPLIT_SEED`, neither of which is a call argument or a
command-line flag.

`test_split_properties.py` asserts that purity over synthetic cohorts. **This
file asserts it over the run that shipped**, and over the real rows in the
database, which is a different claim in two respects: the digest under comparison
is the one a `forecast_run` row actually recorded, and the mutation is a real
change to a real `purchase_order_line` row rather than a second literal handed to
the same function.

NC-13 needs both directions, and the second is the one that carries the weight. A
split that ignored its key entirely — the implementation this exists to exclude —
passes the reproduction half perfectly.

Every mutation runs inside `begin_nested()` on this tier's rolled-back
transaction, so the committed dataset is never altered: what the assertions see
is a database state that exists only for the duration of a savepoint.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import text
from sqlalchemy.orm import Session

from forecast.conftest import EmittedRun, FitInput
from model.forecast.config import HELD_OUT_FRACTION, SPLIT_SEED
from model.forecast.read import read_lines_and_events
from model.forecast.serialize import input_data_hash
from model.forecast.split import assign_split

#: Module-level SQL, never assembled from values (Ruff S608).
RECORDED_HASHES_SQL = text(
    """
    SELECT input_data_hash, split_assignment_hash, split_seed_entropy,
           held_out_fraction_declared, as_of_date
    FROM forecast_run WHERE run_id = :run_id
    """
)
STORED_ASSIGNMENT_SQL = text(
    """
    SELECT a.po_line_id, a.split_side, a.is_censored, a.canonical_ordinal
    FROM forecast_split_assignment a WHERE a.run_id = :run_id ORDER BY a.canonical_ordinal
    """
)
A_MUTABLE_LINE_SQL = text(
    """
    SELECT po_line_id, need_by_date FROM purchase_order_line
    ORDER BY project_id, po_number, line_number LIMIT 1
    """
)
MOVE_NEED_BY_DATE_SQL = text(
    "UPDATE purchase_order_line SET need_by_date = :need_by_date WHERE po_line_id = :po_line_id"
)


def _recorded(db_session: Session, emitted_run: EmittedRun):
    """The four provenance values the run committed alongside its split."""
    return db_session.execute(RECORDED_HASHES_SQL, {"run_id": emitted_run.run_id}).mappings().one()


def _split_now(db_session: Session, emitted_run: EmittedRun):
    """Read the rows as they stand and re-derive the split from them.

    The whole chain, every time: rows, then the digest over them, then the
    assignment keyed on that digest. Re-reading rather than caching is what lets
    the mutation test below observe the key move — a cached digest would make the
    second derivation a repeat of the first.
    """
    procurement_input = read_lines_and_events(db_session)
    row_hash = input_data_hash(procurement_input)
    return row_hash, assign_split(procurement_input.lines, emitted_run.as_of_date, row_hash)


def test_the_recorded_split_hash_is_reproduced_from_the_rows_in_the_database(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """NC-13's passing direction, against the run that shipped.

    Both digests are compared, not only the split's: a matching split hash on top
    of a moved input hash would mean the assignment was reproduced from different
    rows, which is the case FR-023 refuses on and not the one this asserts.
    """
    recorded = _recorded(db_session, emitted_run)
    row_hash, split = _split_now(db_session, emitted_run)

    assert row_hash == recorded["input_data_hash"], (
        f"the rows now in the database hash to {row_hash} against the run's recorded "
        f"{recorded['input_data_hash']}; the split below would then be reproduced from a "
        f"different input than the one the run read"
    )
    assert split.split_assignment_hash == recorded["split_assignment_hash"], (
        "the split re-derived from the same rows, the same anchor and the same two committed "
        "constants does not reproduce the recorded hash, so the assignment is not a pure "
        "function of its declared determinants (AD-011)"
    )


def test_the_re_derived_assignment_matches_the_stored_rows_line_by_line(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """The digest agreeing is not the assignment agreeing, so both are stated.

    A hash comparison is a comparison of 32 bytes and reports nothing about which
    line moved. This compares the four stored columns per line against the
    re-derivation, which is what a failure needs — and it is also what shows the
    stored ordinal is the position the digest was taken in rather than a number
    that merely happens to be unique.
    """
    _, split = _split_now(db_session, emitted_run)
    stored = {
        row["po_line_id"]: (row["split_side"], row["is_censored"], row["canonical_ordinal"])
        for row in db_session.execute(
            STORED_ASSIGNMENT_SQL, {"run_id": emitted_run.run_id}
        ).mappings()
    }
    re_derived = {
        assignment.po_line_id: (
            assignment.split_side,
            assignment.is_censored,
            assignment.canonical_ordinal,
        )
        for assignment in split.assignments
    }

    assert stored, "the run wrote no split assignment"
    assert stored == re_derived


def test_one_mutated_row_moves_both_the_input_hash_and_the_split(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """**NC-13's failing direction**, and the half that makes the pair mean something.

    One column of one line is moved by a day, inside a savepoint. The mutated
    field is `need_by_date` deliberately: it is inside E005's compared-content
    projection, so the input hash covers it, and it reaches neither the censoring
    indicator nor the stratum — so what moves is the *key*, and the reassignment
    that follows is the key doing its job rather than the stratification
    responding to a changed cohort.

    A split that ignored `input_data_hash` altogether reproduces the same
    assignment here and fails on the second assertion. One that keyed on it
    reassigns roughly half the lines, which is what the third measures rather
    than assumes — an implementation that moved one line would satisfy an
    inequality and would not be a keyed shuffle.
    """
    before_hash, before = _split_now(db_session, emitted_run)
    line = db_session.execute(A_MUTABLE_LINE_SQL).mappings().one()

    with db_session.begin_nested() as mutation:
        db_session.execute(
            MOVE_NEED_BY_DATE_SQL,
            {
                "po_line_id": line["po_line_id"],
                "need_by_date": line["need_by_date"] + timedelta(days=1),
            },
        )
        after_hash, after = _split_now(db_session, emitted_run)
        mutation.rollback()

    assert after_hash != before_hash, (
        "moving `need_by_date` on one line did not move `input_data_hash`; the field is "
        "inside E005's compared-content projection, so a hash blind to it is covering fewer "
        "rows than FR-014 says it does"
    )
    assert after.split_assignment_hash != before.split_assignment_hash, (
        "the input hash moved and the split assignment hash did not, so the assignment is "
        "not keyed on the input at all — which is the implementation NC-13 exists to "
        "exclude, and it passes every reproduction test above"
    )
    moved = sum(
        1
        for first, second in zip(before.assignments, after.assignments, strict=True)
        if first.split_side != second.split_side
    )

    assert moved > 1, (
        f"only {moved} line(s) changed side under a different key; a keyed shuffle "
        f"reassigns a substantial share of the cohort, and a single moved line is a split "
        f"that reacted to the mutated row rather than to the key"
    )


def test_the_rolled_back_mutation_leaves_the_split_where_it_was(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """The control on the mutation: the savepoint restored the committed rows.

    Without it, the test above could pass by having permanently altered the
    dataset — and every later assertion in this tier would then be measuring a
    database this file damaged. Stated as its own test rather than as a trailing
    assertion, so it fails under its own name.
    """
    recorded = _recorded(db_session, emitted_run)
    row_hash, split = _split_now(db_session, emitted_run)

    assert row_hash == recorded["input_data_hash"]
    assert split.split_assignment_hash == recorded["split_assignment_hash"]


def test_neither_determinant_of_the_split_is_a_per_run_value(
    db_session: Session, emitted_run: EmittedRun, fit_input: FitInput
) -> None:
    """AD-011's constants, checked against what the run actually recorded.

    `test_split_properties.py` asserts that neither is a parameter of
    `assign_split`. This asserts the run wrote the committed values rather than
    values of its own: `split_seed_entropy` and `held_out_fraction_declared` are
    columns, so a job that took them from somewhere else would record whatever it
    used and nothing would object. FR-028's prohibition is carried by the commit
    history (G-11), and this is where the recorded value is tied back to it.
    """
    recorded = _recorded(db_session, emitted_run)

    assert recorded["split_seed_entropy"] == str(SPLIT_SEED)
    assert recorded["held_out_fraction_declared"] == HELD_OUT_FRACTION
    assert recorded["as_of_date"] == emitted_run.as_of_date
    assert fit_input.split.split_assignment_hash == recorded["split_assignment_hash"]
