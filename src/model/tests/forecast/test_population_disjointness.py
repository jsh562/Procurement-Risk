"""T067 — DV-030 / G-5: three populations, exhaustive and pairwise disjoint.

Under one run every line falls in exactly one of three sets. **Open** lines hold
a `line_posterior` row and no `held_out_prediction` row. **Held-out delivered**
lines hold a `held_out_prediction` row and no `line_posterior` row. **Training
delivered** lines hold neither — a training line that already delivered is not
open, so there is nothing to forecast, and it trained the model, so a prediction
for it could not be graded. It is the largest of the three, roughly 131 of the
199 lines, and it is stated as its own set rather than left as the complement,
because a population defined only by subtraction is one nobody counts.

**Disjointness is structural on one side only, and this file is the other side.**
`ck_held_out_prediction__line_delivered` plus the anchor foreign key make a
still-open line unable to receive a prediction. Nothing prevents the reverse: an
order-date-anchored row written into `line_posterior` satisfies every delivered
constraint, because that table carries no anchor column and E007 may not add one.
That is **G-5**, and DV-030 is its stated mechanism — cross-store uniqueness
asserted as a property over both stores rather than inferred from each store's
own population test.

The counts are checked against `forecast_split_assignment` rather than against
`purchase_order_line`, which is deliberate: the split is the run's own view of
the cohort, DV-006 is what ties that view to the whole table, and quantifying
here over the split keeps this rule about the *populations* rather than about
completeness twice.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from forecast.conftest import EmittedRun

#: Module-level SQL, never assembled from values (Ruff S608).
BOTH_STORES_SQL = text(
    """
    SELECT p.po_line_id
    FROM line_posterior p
    JOIN held_out_prediction h ON h.run_id = p.run_id AND h.po_line_id = p.po_line_id
    WHERE p.run_id = :run_id
    """
)
POPULATION_COUNTS_SQL = text(
    """
    SELECT
      count(*) FILTER (WHERE p.po_line_id IS NOT NULL AND h.po_line_id IS NULL) AS open_only,
      count(*) FILTER (WHERE h.po_line_id IS NOT NULL AND p.po_line_id IS NULL) AS held_out_only,
      count(*) FILTER (WHERE p.po_line_id IS NULL AND h.po_line_id IS NULL) AS neither,
      count(*) FILTER (WHERE p.po_line_id IS NOT NULL AND h.po_line_id IS NOT NULL) AS both,
      count(*) AS assigned
    FROM forecast_split_assignment a
    LEFT JOIN line_posterior p ON p.run_id = a.run_id AND p.po_line_id = a.po_line_id
    LEFT JOIN held_out_prediction h ON h.run_id = a.run_id AND h.po_line_id = a.po_line_id
    WHERE a.run_id = :run_id
    """
)
THIRD_POPULATION_SQL = text(
    """
    SELECT count(*) FROM forecast_split_assignment a
    JOIN purchase_order_line l ON l.po_line_id = a.po_line_id
    LEFT JOIN line_posterior p ON p.run_id = a.run_id AND p.po_line_id = a.po_line_id
    LEFT JOIN held_out_prediction h ON h.run_id = a.run_id AND h.po_line_id = a.po_line_id
    WHERE a.run_id = :run_id AND p.po_line_id IS NULL AND h.po_line_id IS NULL
      AND NOT (a.split_side = 'train' AND l.is_closed)
    """
)
ARTIFACTS_WITHOUT_AN_ASSIGNMENT_SQL = text(
    """
    SELECT (
        SELECT count(*) FROM line_posterior p
        WHERE p.run_id = :run_id AND NOT EXISTS (
            SELECT 1 FROM forecast_split_assignment a
            WHERE a.run_id = p.run_id AND a.po_line_id = p.po_line_id)
    ) AS unassigned_posteriors,
    (
        SELECT count(*) FROM held_out_prediction h
        WHERE h.run_id = :run_id AND NOT EXISTS (
            SELECT 1 FROM forecast_split_assignment a
            WHERE a.run_id = h.run_id AND a.po_line_id = h.po_line_id)
    ) AS unassigned_predictions
    """
)


def _counts(db_session: Session, emitted_run: EmittedRun):
    """The four cells of the cross-store join, over the run's assigned lines."""
    return db_session.execute(
        POPULATION_COUNTS_SQL, {"run_id": emitted_run.run_id}
    ).mappings().one()


def test_no_line_holds_an_artifact_row_in_both_stores_under_one_run(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """**G-5's other direction**, stated as cross-store uniqueness.

    The whole reason the two stores exist is that they hold different quantities
    anchored at different dates. A line in both is a line with two forecasts that
    no reader can reconcile — E010 would read the as-of-anchored one and E014 the
    order-date-anchored one, and neither would see a conflict, because neither
    can see the other's table.
    """
    overlapping = list(
        db_session.execute(BOTH_STORES_SQL, {"run_id": emitted_run.run_id}).scalars()
    )

    assert not overlapping, (
        f"{len(overlapping)} line(s) hold an artifact row in both stores under this run — "
        f"first {overlapping[0]}. Nothing in the schema refuses this: the held-out side is "
        f"fenced by `ck_held_out_prediction__line_delivered` and the anchor key, and "
        f"`line_posterior` carries no anchor column at all"
    )


def test_the_three_populations_are_exhaustive_over_the_runs_assigned_lines(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """The three counts sum to the split assignment count, with no fourth cell.

    Exhaustiveness is what makes the third population countable. Without it, a
    line that fell out of both stores for an unrelated reason would be
    indistinguishable from a training delivered line, and the set nobody counts
    would have absorbed it.
    """
    counts = _counts(db_session, emitted_run)
    populations = counts["open_only"] + counts["held_out_only"] + counts["neither"]

    assert counts["assigned"] > 0
    assert counts["both"] == 0
    assert populations == counts["assigned"], (
        f"{counts['open_only']} + {counts['held_out_only']} + {counts['neither']} = "
        f"{populations} against {counts['assigned']} assigned lines"
    )


def test_each_of_the_three_populations_is_non_empty(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """All three cells populated, so exhaustiveness is not satisfied by two of them.

    A run in which every line landed in one population would satisfy the sum
    above and would make the disjointness claims vacuous. The committed dataset
    produces roughly 24 open, 44 held-out delivered and 131 training delivered
    lines, and none of those is an accident of the shuffle: the split fraction is
    committed and the closure column is the loader's.
    """
    counts = _counts(db_session, emitted_run)

    assert counts["open_only"] > 0, "no open line, so DV-001's population is empty"
    assert counts["held_out_only"] > 0, "no held-out delivered line, so DV-002's is empty"
    assert counts["neither"] > 0, (
        "every assigned line carries an artifact row, so the third population — training "
        "lines that already delivered — is empty and the exhaustiveness claim is a claim "
        "about two sets"
    )


def test_the_third_population_is_exactly_the_training_delivered_lines(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """The set nobody counts, counted — and identified rather than inferred.

    The cell above is "holds neither artifact row". This asserts that cell *is*
    `split_side = 'train' AND is_closed`, so the third population is a described
    set rather than a residue. A line that is open and lost its posterior row
    would land in the same cell and would be silently reclassified as a training
    delivered line by any test that only counted.
    """
    unexplained = db_session.execute(
        THIRD_POPULATION_SQL, {"run_id": emitted_run.run_id}
    ).scalar_one()

    assert unexplained == 0, (
        f"{unexplained} line(s) hold no artifact row and are not training delivered lines. "
        f"Each is either an open line whose forecast was not written or a held-out delivered "
        f"line whose prediction was not, and the exhaustiveness count cannot tell which"
    )


def test_every_artifact_row_names_a_line_the_run_assigned(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """Disjointness over the assignment means nothing if a store reaches past it.

    Neither store has a foreign key to `forecast_split_assignment` — both key to
    `purchase_order_line` — so an artifact row for a line the run never assigned
    is representable, and it would sit outside every count above rather than
    breaking one. It would also leave the artifact hash undefined, since the
    ordering is by `canonical_ordinal` and that line has none (DV-031).
    """
    row = db_session.execute(
        ARTIFACTS_WITHOUT_AN_ASSIGNMENT_SQL, {"run_id": emitted_run.run_id}
    ).mappings().one()

    assert row["unassigned_posteriors"] == 0
    assert row["unassigned_predictions"] == 0
