"""T039 — DV-004 and DV-003 over the rows `line_posterior` actually holds.

The property tier already asserts the grid identity over the array `posterior.py`
returns. This is the other half `data-model.md` names: the same identity over the
stored row, recomputed from the stored `draws` after a `double precision[]` round
trip through the driver and the column. What that adds is everything between the
two — the writer's list adaptation, the array subscripting, the column order —
none of which the in-memory assertion reaches.

The horizon and the tolerance are read from the run row and from
`schema_constants` over the connection (AD-009), never written here: a literal
365 in this file would be a fourth home for a number that already has one.
"""

from __future__ import annotations

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from forecast.conftest import EmittedRun

#: Module-level SQL, never assembled from values (Ruff S608).
STORED_ARRAYS_SQL = text(
    """
    SELECT po_line_id, draw_count, horizon_days, draws, survival, residual_tail_mass
    FROM line_posterior WHERE run_id = :run_id ORDER BY po_line_id
    """
)
RUN_SHAPE_SQL = text("SELECT draw_count, horizon_days FROM forecast_run WHERE run_id = :run_id")
TOLERANCE_SQL = text("SELECT probability_sum_tolerance FROM schema_constants")


def _stored(db_session: Session, emitted_run: EmittedRun) -> list:
    """Every artifact row of the shared run, as mappings in `po_line_id` order."""
    rows = db_session.execute(STORED_ARRAYS_SQL, {"run_id": emitted_run.run_id}).mappings().all()
    assert rows, "the shared run stored no `line_posterior` row, so nothing here is measurable"
    return list(rows)


def test_every_stored_survival_curve_is_the_strict_tail_count_of_its_own_draws(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """DV-004 at the Integration tier: `survival[k] = count(draws > k)/draw_count`.

    Recomputed from the stored `draws` column with a strict `>`, so a draw landing
    exactly on day `k` has delivered by the end of that day. The comparison runs
    over every `k` of every row rather than a sample of them, because a grid that
    is right at day 1 and wrong at day 300 is a curve nothing downstream rejects.
    """
    tolerance = float(db_session.execute(TOLERANCE_SQL).scalar_one())
    days = None
    for row in _stored(db_session, emitted_run):
        draws = np.asarray(row["draws"], dtype=float)
        survival = np.asarray(row["survival"], dtype=float)
        if days is None or days.size != survival.size:
            days = np.arange(1, survival.size + 1, dtype=float)
        expected = np.count_nonzero(draws[:, None] > days, axis=0) / draws.size

        assert np.max(np.abs(survival - expected)) <= tolerance, (
            f"line {row['po_line_id']}'s stored grid disagrees with its own stored draws by "
            f"{np.max(np.abs(survival - expected)):.3e}, past the published tolerance of "
            f"{tolerance:.3e}; the grid is a pure function of the draws"
        )


def test_every_stored_survival_curve_is_a_non_increasing_probability(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """A survivor function's two structural properties, read back out of the column.

    `ck_line_posterior__survival_unit_interval` and `…__survival_non_increasing`
    are delivered constraints, so this asserts the stored value satisfies what the
    schema claims of it rather than what the writer intended — the two differ
    exactly when a constraint is not doing the job its name says.
    """
    for row in _stored(db_session, emitted_run):
        survival = np.asarray(row["survival"], dtype=float)

        assert np.all((survival >= 0.0) & (survival <= 1.0)), (
            f"line {row['po_line_id']} stored a survival value outside `[0, 1]`"
        )
        assert np.all(np.diff(survival) <= 0.0), (
            f"line {row['po_line_id']}'s stored curve rises somewhere; a survivor function "
            f"cannot recover mass it has already spent"
        )


def test_every_stored_array_is_as_long_as_the_run_row_declares(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """The lengths are the run's own, read from the run row rather than assumed.

    `fk_line_posterior__run_shape` proves each row's `(draw_count, horizon_days)`
    pair belongs to this run, and the two `array_length` checks compare the arrays
    against numbers already proven correct. What is asserted here is the step
    those constraints cannot take: that the pair on the child row is the pair the
    run published, so the grid runs `k = 1..horizon_days` with no `S(0)` element.
    """
    run = db_session.execute(RUN_SHAPE_SQL, {"run_id": emitted_run.run_id}).mappings().one()
    for row in _stored(db_session, emitted_run):
        assert row["draw_count"] == run["draw_count"]
        assert row["horizon_days"] == run["horizon_days"]
        assert len(row["draws"]) == run["draw_count"]
        assert len(row["survival"]) == run["horizon_days"]


def test_every_stored_draw_vector_is_ascending_and_non_negative(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """The canonical order a percentile lookup depends on, over the stored column.

    `schema_constants.percentile_convention` reads `draws[ceil(p·n)]`, so an
    unsorted array makes every published percentile the wrong draw while every
    length and range check still passes — which is why the order is a stored
    property rather than a convention the reader is asked to honour.
    """
    for row in _stored(db_session, emitted_run):
        draws = np.asarray(row["draws"], dtype=float)

        assert np.all(np.diff(draws) >= 0.0), f"line {row['po_line_id']} stored unsorted draws"
        assert np.all(draws >= 0.0)
        assert np.all(np.isfinite(draws))


def test_every_stored_residual_is_the_tail_mass_past_the_horizon(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """DV-003 over stored rows: the residual and the grid's tail are one comparison.

    Recomputed from the stored draws by a count rather than read off the grid, and
    then compared against the grid's last element as well — `data-model.md`
    § Conventions makes them the same strict `>` at `k = horizon_days`, so a
    residual that agrees with the draws but not with the curve beside it is a row
    whose two halves were taken over different draw sets.
    """
    tolerance = float(db_session.execute(TOLERANCE_SQL).scalar_one())
    for row in _stored(db_session, emitted_run):
        draws = np.asarray(row["draws"], dtype=float)
        beyond = np.count_nonzero(draws > float(row["horizon_days"])) / draws.size

        assert abs(float(row["residual_tail_mass"]) - beyond) <= tolerance
        assert abs(float(row["residual_tail_mass"]) - float(row["survival"][-1])) <= tolerance
