"""T126 — FR-029: both recorded duration semantics, over the rows a run emitted.

The two artifact populations hold different quantities and each records which:

- an **open** line's draws are the *remaining* duration conditional on the line
  having survived its elapsed time, anchored at the run's as-of date, recorded
  once per run on `forecast_run.open_line_draw_semantic`;
- a **held-out delivered** line's draws are the *total* duration from that line's
  own order date — the quantity its observed outcome can be graded against —
  recorded per row on `held_out_prediction.duration_semantic`, beside the anchor
  convention its `anchor_date` follows.

**Recording a semantic is not measuring it, and this file only reaches the
recording.** Both columns are single-value `CHECK`s, and a label agrees with
itself: the re-based implementation FR-029 forbids would satisfy them
identically. SC-013 says so outright. The measured counterparts exist and are
named here so a reader is not left to notice the asymmetry — **SC-027** in
`test_conditioning.py` for the open population, **DV-040** in
`test_held_out_semantic.py` for the held-out one.

What this file adds beyond the two labels is the check neither the labels nor
the constraints make: that the **anchor each label names is the anchor the rows
actually carry**. `fk_held_out_prediction__line_anchor` fixes the anchor to the
line's order date and reaches the duration not at all; nothing at all fixes the
open population's origin, because it has no anchor column — its origin is the
run's own as-of date, and that is a property of where the rows live rather than
of anything stored on them. Both are asserted below against the delivered
`purchase_order_line` rows and against the run row.
"""

from __future__ import annotations

import numpy as np
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from forecast.conftest import EmittedRun
from model.forecast.manifest import OPEN_LINE_DRAW_SEMANTIC
from model.forecast.write import HELD_OUT_ANCHOR_CONVENTION, HELD_OUT_DURATION_SEMANTIC

#: Module-level SQL, never assembled from values (Ruff S608).
RUN_SEMANTIC_SQL = text(
    "SELECT open_line_draw_semantic, as_of_date FROM forecast_run WHERE run_id = :run_id"
)

#: Every held-out row beside the line it predicts, so the recorded anchor is
#: compared against the delivered table's own `order_date` rather than against
#: itself. `is_closed` comes along because the composite foreign key resolves
#: against it and the semantic is only defined for a line that finished.
HELD_OUT_ROWS_SQL = text(
    """
    SELECT h.po_line_id, h.anchor_date, h.anchor_convention, h.duration_semantic,
           l.order_date, l.is_closed
    FROM held_out_prediction h
    JOIN purchase_order_line l ON l.po_line_id = h.po_line_id
    WHERE h.run_id = :run_id ORDER BY h.po_line_id
    """
)

OPEN_DRAWS_SQL = text(
    "SELECT po_line_id, draws FROM line_posterior WHERE run_id = :run_id ORDER BY po_line_id"
)

#: The columns `line_posterior` would need in order to carry an anchor of its
#: own. Their absence is what makes the run's as-of date the open population's
#: only possible origin, so it is asserted rather than assumed.
ANCHOR_COLUMNS = ("anchor_date", "anchor_convention", "duration_semantic")

OPEN_STORE_COLUMNS_SQL = text(
    "SELECT column_name FROM information_schema.columns "
    "WHERE table_schema = 'public' AND table_name = 'line_posterior'"
)

#: The origin each label names, spelled as the label spells it. The two strings
#: are compared against the anchors the rows carry, which is the whole of what
#: this file adds to a `CHECK` that admits one value.
OPEN_ORIGIN_PHRASE = "run_as_of_date"
HELD_OUT_ORIGIN_PHRASE = "line_order_date"


@pytest.fixture
def run_row(db_session: Session, emitted_run: EmittedRun):
    """The shared run's recorded open-line semantic and its anchor."""
    return db_session.execute(RUN_SEMANTIC_SQL, {"run_id": emitted_run.run_id}).mappings().one()


@pytest.fixture
def held_out_rows(db_session: Session, emitted_run: EmittedRun) -> list:
    """Every held-out prediction of the shared run, joined to its own line."""
    rows = db_session.execute(HELD_OUT_ROWS_SQL, {"run_id": emitted_run.run_id}).mappings().all()
    assert rows, "the shared run stored no held-out prediction, so this file would pass vacuously"
    return list(rows)


# ---------------------------------------------------------------------------
# The two recorded labels
# ---------------------------------------------------------------------------


def test_the_run_records_the_open_populations_conditional_remaining_semantic(
    run_row,
) -> None:
    """FR-029's open half, as the run row records it.

    Compared against the module constant rather than against a string written
    here, so the writer and this assertion cannot drift apart; and the label is
    required to name its origin, because "remaining" without an anchor is the
    ambiguity FR-029 was written to remove.
    """
    recorded = run_row["open_line_draw_semantic"]

    assert recorded == OPEN_LINE_DRAW_SEMANTIC
    assert "conditional_remaining" in recorded
    assert OPEN_ORIGIN_PHRASE in recorded


def test_every_held_out_row_records_the_total_duration_semantic_and_its_anchor(
    held_out_rows: list,
) -> None:
    """FR-029's held-out half, per row rather than per run.

    Every row, not a `DISTINCT`: the labels are per-row columns, and a single
    divergent row is a prediction that will be graded against a quantity it does
    not hold. The anchor convention is asserted beside the semantic because
    neither is meaningful alone — a total duration from an unstated origin is
    not a duration.
    """
    for row in held_out_rows:
        assert row["duration_semantic"] == HELD_OUT_DURATION_SEMANTIC
        assert row["anchor_convention"] == HELD_OUT_ANCHOR_CONVENTION
        assert HELD_OUT_ORIGIN_PHRASE in row["duration_semantic"]


def test_the_two_populations_record_different_semantics(run_row) -> None:
    """One label for both stores would make the distinction unreadable.

    The whole content of FR-029's second sentence is that the semantic *differs*
    between the populations and must be recorded as such. Two stores agreeing on
    a label would satisfy every `CHECK` on both and tell a grader nothing.
    """
    assert run_row["open_line_draw_semantic"] != HELD_OUT_DURATION_SEMANTIC
    assert OPEN_ORIGIN_PHRASE != HELD_OUT_ORIGIN_PHRASE


# ---------------------------------------------------------------------------
# The anchors those labels name
# ---------------------------------------------------------------------------


def test_each_held_out_anchor_is_its_own_lines_order_date_before_the_run(
    held_out_rows: list, run_row
) -> None:
    """The label says "from the line's order date"; the rows are checked to be.

    Compared against `purchase_order_line.order_date` through the join rather
    than against the stored value itself. Every anchor must also fall before the
    run's as-of date — a held-out line delivered before the anchor by
    construction — which is what makes the total duration a different quantity
    from the remaining one rather than the same number under another name.
    """
    for row in held_out_rows:
        assert row["anchor_date"] == row["order_date"]
        assert row["anchor_date"] < run_row["as_of_date"]
        assert row["is_closed"]


def test_the_open_population_carries_no_anchor_of_its_own(db_session: Session) -> None:
    """The open population's origin is the run's, which is why it has no column.

    Asserted rather than assumed: an anchor column on `line_posterior` would be
    a second origin for a quantity whose semantic is recorded once per run, and
    a reader would have no way to know which of the two a given curve was
    measured from.
    """
    columns = set(db_session.execute(OPEN_STORE_COLUMNS_SQL).scalars())

    assert columns.isdisjoint(ANCHOR_COLUMNS)
    assert {"draws", "survival", "residual_tail_mass"} <= columns


def test_no_open_line_draw_is_exactly_zero(db_session: Session, emitted_run: EmittedRun) -> None:
    """The one signature of the re-based implementation FR-029 forbids.

    Re-basing a total duration by subtracting elapsed days and clipping puts a
    point mass of size `F(elapsed)` at exactly zero, which
    `ck_line_posterior__draws_non_negative` admits without complaint. Its absence
    is consistency between the recorded label and the rows — not the measurement,
    which is SC-027's in `test_conditioning.py`.
    """
    rows = db_session.execute(OPEN_DRAWS_SQL, {"run_id": emitted_run.run_id}).mappings().all()

    assert rows, "the shared run stored no `line_posterior` row"
    for row in rows:
        draws = np.asarray(row["draws"], dtype=float)
        assert np.all(draws > 0.0), (
            f"line {row['po_line_id']} stores {int(np.count_nonzero(draws == 0.0))} draw(s) at "
            f"exactly zero; that is the point mass a re-based total duration leaves behind, "
            f"and the recorded conditional-remaining label would then describe rows that are "
            f"not conditional at all"
        )
