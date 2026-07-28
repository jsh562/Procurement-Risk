"""T063 — DV-006 / SC-009 / G-6: every line assigned, once, in a contiguous order.

"Every line is assigned to exactly one side" is half enforced and half not.
`pk_forecast_split_assignment` gives *at most once per run*, and
`uq_forecast_split_assignment__run_ordinal` gives at most one line per position.
Neither can see `purchase_order_line`, and neither can see a **gap**: a `CHECK`
admits no sibling row and no second table, and a deferred one is impossible. G-6
records that, and this file is what covers it.

The gap matters more than it looks. `canonical_ordinal` stands for a position in
the sequence `split_assignment_hash` is taken over, so an assignment numbered
1, 2, 4 is well formed by every constraint the table carries and is not the thing
that was hashed — the digest would be recomputable from the table and would agree
with itself while describing a sequence with a hole in it.

E005 is the only writer of `purchase_order_line` and E007 writes none of it, so
counting against the whole table is exact rather than approximate: there is no
concurrent load that could add a line between the two counts.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from forecast.conftest import EmittedRun

#: Module-level SQL, never assembled from values (Ruff S608).
COVERAGE_SQL = text(
    """
    SELECT (SELECT count(*) FROM purchase_order_line) AS lines_present,
           (SELECT count(*) FROM forecast_split_assignment WHERE run_id = :run_id) AS assigned,
           (SELECT count(DISTINCT po_line_id) FROM forecast_split_assignment
             WHERE run_id = :run_id) AS lines_covered
    """
)
UNASSIGNED_LINES_SQL = text(
    """
    SELECT l.po_line_id FROM purchase_order_line l
    WHERE NOT EXISTS (
        SELECT 1 FROM forecast_split_assignment a
        WHERE a.run_id = :run_id AND a.po_line_id = l.po_line_id
    )
    """
)
ORDINAL_SEQUENCE_SQL = text(
    """
    SELECT a.canonical_ordinal, l.project_id, l.po_number, l.line_number, a.split_side
    FROM forecast_split_assignment a
    JOIN purchase_order_line l ON l.po_line_id = a.po_line_id
    WHERE a.run_id = :run_id
    ORDER BY a.canonical_ordinal
    """
)
ORDINAL_BOUNDS_SQL = text(
    """
    SELECT min(canonical_ordinal) AS lowest, max(canonical_ordinal) AS highest,
           count(DISTINCT canonical_ordinal) AS distinct_positions, count(*) AS rows_written
    FROM forecast_split_assignment WHERE run_id = :run_id
    """
)

#: The two values `ck_forecast_split_assignment__side` admits.
SIDES = frozenset({"train", "held_out"})


def test_every_line_in_the_table_is_assigned_exactly_once_under_the_run(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """DV-006's count, against `purchase_order_line` rather than against itself.

    Three numbers rather than two: rows written, distinct lines covered, and
    lines present. Equality of the first two says no line was assigned twice —
    which the primary key already forbids, and this is what shows the key is
    doing that job — and equality with the third says none was left out, which
    nothing in the schema can say.
    """
    counts = db_session.execute(COVERAGE_SQL, {"run_id": emitted_run.run_id}).mappings().one()

    assert counts["lines_present"] > 0, (
        "`purchase_order_line` is empty, so this test would pass vacuously; the fit reads "
        "the schema and never the fixture file"
    )
    assert counts["assigned"] == counts["lines_covered"], (
        f"{counts['assigned']} assignment rows cover only {counts['lines_covered']} lines, "
        f"so a line is assigned twice under one run"
    )
    assert counts["assigned"] == counts["lines_present"], (
        f"{counts['assigned']} of {counts['lines_present']} lines are assigned; the split is "
        f"a total function over the cohort, and an unassigned line is one the fit neither "
        f"trained on nor held out"
    )


def test_no_line_is_left_without_a_side(db_session: Session, emitted_run: EmittedRun) -> None:
    """The same claim by anti-join, which names the lines rather than a difference.

    A count comparison reports *how many* went missing; this reports *which*. The
    two are the same assertion and the second is what a failure needs, because
    the interesting case is a systematic omission — a whole vendor, or every line
    ordered after some date — and a number cannot show that.
    """
    missing = list(
        db_session.execute(UNASSIGNED_LINES_SQL, {"run_id": emitted_run.run_id}).scalars()
    )

    assert not missing, (
        f"{len(missing)} line(s) carry no split assignment under this run — first "
        f"{missing[0]}. `fk_forecast_split_assignment__line` stops an assignment naming a "
        f"line that does not exist; nothing stops a line existing with no assignment"
    )


def test_the_canonical_ordinal_is_contiguous_from_one(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """The gap `uq_forecast_split_assignment__run_ordinal` cannot see.

    The unique key forbids two lines at one position and permits any set of
    positions at all. What the hash needs is a permutation of `1..n`, so the
    bounds and the distinct count together are the statement: lowest 1, highest
    `n`, `n` distinct values over `n` rows admits nothing else.
    """
    bounds = db_session.execute(
        ORDINAL_BOUNDS_SQL, {"run_id": emitted_run.run_id}
    ).mappings().one()

    assert bounds["rows_written"] > 0
    assert bounds["lowest"] == 1, (
        f"the ordinals start at {bounds['lowest']}; `data-model.md` § Canonical order numbers "
        f"the sequence from 1 and `ck_forecast_split_assignment__ordinal_positive` only "
        f"bounds it below"
    )
    assert bounds["distinct_positions"] == bounds["rows_written"]
    assert bounds["highest"] == bounds["rows_written"], (
        f"the ordinals run to {bounds['highest']} over {bounds['rows_written']} rows, so the "
        f"sequence the split hash was taken over has a hole in it — well formed by every "
        f"constraint, and not the thing that was hashed"
    )


def test_the_ordinal_order_is_ascending_natural_key_order(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """SC-009's canonical order, read back through the join it is defined over.

    Ascending `(project_id, po_number, line_number)` on the referenced line. The
    natural key is unique — `uq_purchase_order_line__natural` makes it so — which
    is why the order is total and no tie-break exists to specify. Asserted over
    the stored ordinal rather than over the split object, because the stored
    value is what makes the hash recomputable from this table alone, without
    re-reading `purchase_order_line`.
    """
    rows = db_session.execute(
        ORDINAL_SEQUENCE_SQL, {"run_id": emitted_run.run_id}
    ).mappings().all()
    keys = [(row["project_id"], row["po_number"], row["line_number"]) for row in rows]

    assert rows, "the run wrote no split assignment"
    assert keys == sorted(keys), (
        "the stored ordinals do not follow ascending natural-key order, so a reader "
        "recomputing the split hash by that order would serialize a different sequence"
    )
    assert len(set(keys)) == len(keys), (
        "two assignments share a natural key, which `uq_purchase_order_line__natural` makes "
        "unrepresentable upstream — the join has found something the delivered key forbids"
    )
    assert {row["split_side"] for row in rows} <= SIDES
