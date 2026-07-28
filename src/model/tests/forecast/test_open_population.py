"""T038 — DV-001 / SC-001: the open population, as stored rather than as built.

One `line_posterior` row per line open at the run's as-of date, exactly one, and
`forecast_run.open_line_count` equal to that count. Asserted over the rows the
shared `forecast-fit` invocation committed, against the open set recomputed from
`purchase_order_line` and `lifecycle_event` by `censoring.py`'s dated indicator —
never against the count the job reported, which would compare the run with
itself.

**G-5 is why the reverse direction is here too.** `line_posterior` carries no
anchor column and E007 may not add one, so nothing in the schema prevents a row
for a line that was already closed at the anchor. DV-001 is the whole mechanism.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from forecast.conftest import EmittedRun
from model.forecast.censoring import censoring_indicator
from model.forecast.read import read_lines_and_events

#: Module-level SQL, never assembled from values (Ruff S608).
POSTERIOR_LINES_SQL = text("SELECT po_line_id FROM line_posterior WHERE run_id = :run_id")
POSTERIOR_COUNTS_SQL = text(
    """
    SELECT count(*) AS rows_written, count(DISTINCT po_line_id) AS lines_covered
    FROM line_posterior WHERE run_id = :run_id
    """
)
RUN_POPULATION_SQL = text(
    "SELECT as_of_date, open_line_count FROM forecast_run WHERE run_id = :run_id"
)


def _open_line_ids(db_session: Session, emitted_run: EmittedRun) -> set:
    """The lines open at the run's own as-of date, recomputed from the schema.

    Read through `read.py` and judged by `censoring.py`, which is the pair the
    job itself uses — the independence this test rests on is that the count is
    recomputed from the rows rather than trusted from the run row, not that a
    second censoring rule is invented here to disagree with the delivered one.
    """
    procurement_input = read_lines_and_events(db_session)
    return {
        line.po_line_id
        for line in procurement_input.lines
        if censoring_indicator(line, emitted_run.as_of_date)
    }


def test_every_line_open_at_the_anchor_carries_a_stored_posterior(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """DV-001's forward direction, over stored rows.

    Set equality rather than a count comparison: two counts agree while naming
    different lines, and the failure that produces — a closed line forecast in
    place of an open one — is invisible in every aggregate the run publishes.
    """
    stored = set(
        db_session.execute(POSTERIOR_LINES_SQL, {"run_id": emitted_run.run_id}).scalars()
    )
    expected = _open_line_ids(db_session, emitted_run)

    assert expected, (
        f"no line is open at {emitted_run.as_of_date}, so this test would pass vacuously; "
        f"`ck_forecast_run__open_line_count_positive` makes such a run unrepresentable"
    )
    assert stored == expected, (
        f"the stored open population differs from the one the censoring indicator finds at "
        f"{emitted_run.as_of_date}: {len(expected - stored)} open line(s) have no row and "
        f"{len(stored - expected)} stored row(s) name a line that was not open"
    )


def test_no_line_closed_at_the_anchor_carries_a_stored_posterior(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """G-5's direction: the population is disjoint on the closed side by test only.

    `held_out_prediction` is fenced structurally by `ck_held_out_prediction__
    line_delivered` and its anchor foreign key; `line_posterior` has no such
    fence, so a total-duration row anchored on the order date would satisfy every
    delivered constraint. This is the mechanism `data-model.md` names in its
    place.
    """
    stored = set(
        db_session.execute(POSTERIOR_LINES_SQL, {"run_id": emitted_run.run_id}).scalars()
    )
    procurement_input = read_lines_and_events(db_session)
    closed = {
        line.po_line_id
        for line in procurement_input.lines
        if not censoring_indicator(line, emitted_run.as_of_date)
    }

    assert closed, "the cohort holds no closed line, so this direction asserts nothing"
    assert not (stored & closed)


def test_each_open_line_carries_exactly_one_row_under_the_run(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """"Exactly one" is half of DV-001 and is not implied by the set equality above.

    `pk_line_posterior` is `(run_id, po_line_id)` so a duplicate is unstorable,
    and this asserts that the delivered key is doing that job rather than that
    the writer happened not to try — the two counts below are equal only if no
    line was written twice under this run.
    """
    counts = db_session.execute(
        POSTERIOR_COUNTS_SQL, {"run_id": emitted_run.run_id}
    ).mappings().one()

    assert counts["rows_written"] == counts["lines_covered"]


def test_the_run_row_publishes_the_open_line_count_it_actually_wrote(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """SC-001's count column, checked against the rows rather than against itself.

    `open_line_count` is what a reader trusts without running the query, so it is
    the one number in the run row a wrong write would leave uncontradicted —
    `ck_forecast_run__open_line_count_positive` bounds it below and nothing
    delivered compares it against the child rows.
    """
    row = db_session.execute(
        RUN_POPULATION_SQL, {"run_id": emitted_run.run_id}
    ).mappings().one()
    stored = set(
        db_session.execute(POSTERIOR_LINES_SQL, {"run_id": emitted_run.run_id}).scalars()
    )

    assert row["as_of_date"] == emitted_run.as_of_date
    assert row["open_line_count"] == len(stored)
    assert row["open_line_count"] == len(_open_line_ids(db_session, emitted_run))
