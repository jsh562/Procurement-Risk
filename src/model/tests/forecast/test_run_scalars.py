"""T066 — DV-028 / SC-012: every run-row scalar against the child rows it summarises.

Four numbers ride on `forecast_run` that are derivable from the rows written in
the same transaction. **None of them is carried by a constraint, and none can
be**: a `CHECK` sees one row and these are counts over siblings, so each is a
value a reader trusts precisely because it saves them a query — which makes it
the one number in the run row a wrong write would leave uncontradicted.

Obligated **per scalar** rather than once for the set, which is DV-028's own
wording. A single test over all four reports the first disagreement and hides
the rest, and the four fail for different reasons: `training_line_count` and
`held_out_fraction_realized` are counts over `forecast_split_assignment`,
`held_out_uncensored_event_count` is that table joined to the delivered closure
column, and `open_line_count` is the `line_posterior` count. Each is one SQL
comparison and each names its own store.

`open_line_count` has its own file — `test_open_population.py`, DV-001 — and is
asserted here too, against the child-row count rather than against the censoring
indicator. The two are different claims: DV-001 says the run forecast the right
lines, and this says the run's own summary of that is arithmetic rather than a
label. `held_out_uncensored_event_count` carries the same doubling deliberately,
since DV-028 makes it both a split count and the `held_out_prediction` count, and
a run in which those two disagree is one whose gradeable population and whose
published event count describe different sets.
"""

from __future__ import annotations

import math

from sqlalchemy import text
from sqlalchemy.orm import Session

from forecast.conftest import EmittedRun

#: Module-level SQL, never assembled from values (Ruff S608). One statement per
#: scalar, so a failure reports the comparison rather than a row of numbers.
RUN_SCALARS_SQL = text(
    """
    SELECT training_line_count, held_out_fraction_realized,
           held_out_uncensored_event_count, open_line_count, held_out_fraction_declared
    FROM forecast_run WHERE run_id = :run_id
    """
)
TRAINING_ROWS_SQL = text(
    """
    SELECT count(*) FROM forecast_split_assignment
    WHERE run_id = :run_id AND split_side = 'train'
    """
)
SPLIT_SHARE_SQL = text(
    """
    SELECT count(*) FILTER (WHERE split_side = 'held_out') AS held_out,
           count(*) AS assigned
    FROM forecast_split_assignment WHERE run_id = :run_id
    """
)
HELD_OUT_EVENTS_SQL = text(
    """
    SELECT count(*) FROM forecast_split_assignment a
    JOIN purchase_order_line l ON l.po_line_id = a.po_line_id
    WHERE a.run_id = :run_id AND a.split_side = 'held_out' AND l.is_closed
    """
)
PREDICTION_ROWS_SQL = text(
    "SELECT count(*) FROM held_out_prediction WHERE run_id = :run_id"
)
POSTERIOR_ROWS_SQL = text("SELECT count(*) FROM line_posterior WHERE run_id = :run_id")

#: `held_out_fraction_realized` is a ratio of two integers stored as a `double
#: precision`, so it is compared within one representation step rather than for
#: equality — the same reason every other float comparison in this epic carries a
#: tolerance. This is not the published `probability_sum_tolerance`: that constant
#: governs probabilities and this is a share of a count, so borrowing it would put
#: a probability tolerance on a quantity that is not one.
RATIO_TOLERANCE = 1e-12


def _run_row(db_session: Session, emitted_run: EmittedRun):
    """The scalars the run published, read once for every comparison below."""
    return db_session.execute(RUN_SCALARS_SQL, {"run_id": emitted_run.run_id}).mappings().one()


def test_the_training_line_count_equals_the_training_assignments(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """`training_line_count` = the `train` rows of `forecast_split_assignment`.

    `ck_forecast_run__training_line_count_positive` bounds it below and nothing
    compares it against anything. It is the "alone" in FR-033's "computed on the
    training split alone" — a reader checking whether the floor came off 149
    lines or 199 has no other way to tell — so a number that drifted from the
    rows would misreport the evidence rather than the result.
    """
    published = _run_row(db_session, emitted_run)["training_line_count"]
    counted = db_session.execute(TRAINING_ROWS_SQL, {"run_id": emitted_run.run_id}).scalar_one()

    assert counted > 0, "the run assigned no line to the training side"
    assert published == counted, (
        f"the run row publishes {published} training lines against {counted} rows with "
        f"`split_side = 'train'`"
    )


def test_the_realized_held_out_fraction_equals_the_held_out_share(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """`held_out_fraction_realized` = held-out rows over all rows, under the run.

    The *realized* fraction and never the declared one. They are two columns
    because they answer different questions — what the configuration asked for,
    and what the rounded per-stratum quotas actually produced — and a run in
    which the realized value was silently copied from the declared one would
    satisfy `ck_forecast_run__realized_fraction_range` and report the
    configuration back to its reader as a measurement.
    """
    row = _run_row(db_session, emitted_run)
    counts = db_session.execute(SPLIT_SHARE_SQL, {"run_id": emitted_run.run_id}).mappings().one()
    realized = counts["held_out"] / counts["assigned"]

    assert counts["assigned"] > 0
    assert math.isclose(row["held_out_fraction_realized"], realized, abs_tol=RATIO_TOLERANCE), (
        f"the run row publishes a realized held-out fraction of "
        f"{row['held_out_fraction_realized']!r} against {counts['held_out']}/"
        f"{counts['assigned']} = {realized!r} rows actually held out"
    )
    assert abs(realized - row["held_out_fraction_declared"]) <= 1.0 / counts["assigned"], (
        f"the realized fraction {realized:.4f} is more than one line away from the declared "
        f"{row['held_out_fraction_declared']:.4f}; the stratified quotas round to the "
        f"nearest whole line, so the two cannot differ by more than that"
    )


def test_the_held_out_uncensored_event_count_equals_both_of_its_definitions(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """DV-028 gives this scalar two derivations, and they must agree with each other.

    It is the count of held-out assignments joined to a delivered line, **and**
    it is the `held_out_prediction` row count — the same population reached from
    the split side and from the artifact side. Asserting both is what makes this
    more than a restatement: a run whose gradeable rows and whose published event
    count describe different sets is one where L-3's precision statement is about
    a population that was not stored.
    """
    published = _run_row(db_session, emitted_run)["held_out_uncensored_event_count"]
    from_split = db_session.execute(
        HELD_OUT_EVENTS_SQL, {"run_id": emitted_run.run_id}
    ).scalar_one()
    from_artifacts = db_session.execute(
        PREDICTION_ROWS_SQL, {"run_id": emitted_run.run_id}
    ).scalar_one()

    assert from_split > 0, (
        "no held-out line has delivered, so the event count is zero and every comparison "
        "here passes on an empty population"
    )
    assert published == from_split, (
        f"the run row publishes {published} held-out uncensored events against "
        f"{from_split} held-out assignments on a delivered line"
    )
    assert published == from_artifacts, (
        f"the run row publishes {published} held-out uncensored events against "
        f"{from_artifacts} stored `held_out_prediction` row(s); the count and the gradeable "
        f"population have to be the same set (DV-002)"
    )


def test_the_open_line_count_equals_the_stored_posterior_rows(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """`open_line_count` = the `line_posterior` count, which is DV-028's fourth clause.

    `ck_forecast_run__open_line_count_positive` makes an empty forecast set
    unrepresentable — FR-021 as a database fact — and stops there. That the
    number equals the rows written is this comparison's, and that those rows are
    the *right* lines is DV-001's, in `test_open_population.py`.
    """
    published = _run_row(db_session, emitted_run)["open_line_count"]
    counted = db_session.execute(POSTERIOR_ROWS_SQL, {"run_id": emitted_run.run_id}).scalar_one()

    assert published == counted, (
        f"the run row publishes {published} open lines against {counted} stored "
        f"`line_posterior` row(s)"
    )


def test_the_four_scalars_account_for_every_assigned_line(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """The scalars are mutually consistent, not merely individually right.

    Training lines plus held-out lines is the whole cohort, and the held-out
    event count is a subset of the held-out side. Each comparison above is
    against one query and none of them would notice a set of numbers that were
    each defensible and jointly impossible.
    """
    row = _run_row(db_session, emitted_run)
    counts = db_session.execute(SPLIT_SHARE_SQL, {"run_id": emitted_run.run_id}).mappings().one()

    assert row["training_line_count"] + counts["held_out"] == counts["assigned"]
    assert row["held_out_uncensored_event_count"] <= counts["held_out"], (
        "more held-out lines have delivered than are held out at all"
    )
    assert row["open_line_count"] <= counts["assigned"]
