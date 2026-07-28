"""T039, T060 — DV-004 and DV-003 over the rows **both** artifact stores hold.

The property tier already asserts the grid identity over the array `posterior.py`
returns. This is the other half `data-model.md` names: the same identity over the
stored row, recomputed from the stored `draws` after a `double precision[]` round
trip through the driver and the column. What that adds is everything between the
two — the writer's list adaptation, the array subscripting, the column order —
none of which the in-memory assertion reaches.

**T060 quantifies every one of these over `held_out_prediction` as well**, and
that is not a convenience. No delivered constraint reaches that table: every
array invariant on it is re-declared under E007's own names (ADR-0018 accepts
that duplication), so a property that holds on `line_posterior` because E003
enforces it holds on the second store only if E007 enforced it too. Parametrizing
one test body over both stores is what makes a divergence a failure here rather
than a discovery downstream — DV-027 pairs the constraint *definitions*, and this
pairs their effect on real rows.

The horizon and the tolerance are read from the run row and from
`schema_constants` over the connection (AD-009), never written here: a literal
365 in this file would be a fourth home for a number that already has one.
"""

from __future__ import annotations

import numpy as np
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from forecast.conftest import EmittedRun

#: Module-level SQL, never assembled from values (Ruff S608). Two statements
#: rather than one built from a table name, for exactly that reason — and keyed
#: by the store so a test failure names which population diverged.
STORED_ARRAYS_SQL = {
    "line_posterior": text(
        """
        SELECT po_line_id, draw_count, horizon_days, draws, survival, residual_tail_mass
        FROM line_posterior WHERE run_id = :run_id ORDER BY po_line_id
        """
    ),
    "held_out_prediction": text(
        """
        SELECT po_line_id, draw_count, horizon_days, draws, survival, residual_tail_mass
        FROM held_out_prediction WHERE run_id = :run_id ORDER BY po_line_id
        """
    ),
}
RUN_SHAPE_SQL = text("SELECT draw_count, horizon_days FROM forecast_run WHERE run_id = :run_id")
TOLERANCE_SQL = text("SELECT probability_sum_tolerance FROM schema_constants")

#: Both artifact populations, so every assertion below runs twice. Named rather
#: than derived from the dictionary, so a store silently dropped from the mapping
#: is a collection error and not a quietly halved test run.
ARTIFACT_STORES = ("line_posterior", "held_out_prediction")


def _stored(db_session: Session, emitted_run: EmittedRun, store: str) -> list:
    """Every artifact row of the shared run in one store, in `po_line_id` order."""
    rows = (
        db_session.execute(STORED_ARRAYS_SQL[store], {"run_id": emitted_run.run_id})
        .mappings()
        .all()
    )
    assert rows, f"the shared run stored no `{store}` row, so nothing here is measurable"
    return list(rows)


@pytest.mark.parametrize("store", ARTIFACT_STORES)
def test_every_stored_survival_curve_is_the_strict_tail_count_of_its_own_draws(
    db_session: Session, emitted_run: EmittedRun, store: str
) -> None:
    """DV-004 at the Integration tier: `survival[k] = count(draws > k)/draw_count`.

    Recomputed from the stored `draws` column with a strict `>`, so a draw landing
    exactly on day `k` has delivered by the end of that day. The comparison runs
    over every `k` of every row rather than a sample of them, because a grid that
    is right at day 1 and wrong at day 300 is a curve nothing downstream rejects.

    The identity is anchor-blind and so is `survival_grid`: what the two
    populations disagree about is what a draw *means*, never how the grid counts
    them, which is why one body serves both.
    """
    tolerance = float(db_session.execute(TOLERANCE_SQL).scalar_one())
    days = None
    for row in _stored(db_session, emitted_run, store):
        draws = np.asarray(row["draws"], dtype=float)
        survival = np.asarray(row["survival"], dtype=float)
        if days is None or days.size != survival.size:
            days = np.arange(1, survival.size + 1, dtype=float)
        expected = np.count_nonzero(draws[:, None] > days, axis=0) / draws.size

        assert np.max(np.abs(survival - expected)) <= tolerance, (
            f"{store} line {row['po_line_id']}'s stored grid disagrees with its own stored "
            f"draws by {np.max(np.abs(survival - expected)):.3e}, past the published "
            f"tolerance of {tolerance:.3e}; the grid is a pure function of the draws"
        )


@pytest.mark.parametrize("store", ARTIFACT_STORES)
def test_every_stored_survival_curve_is_a_non_increasing_probability(
    db_session: Session, emitted_run: EmittedRun, store: str
) -> None:
    """A survivor function's two structural properties, read back out of the column.

    On `line_posterior` these are delivered constraints and this asserts the
    stored value satisfies what the schema claims of it — the two differ exactly
    when a constraint is not doing the job its name says. On
    `held_out_prediction` the constraints are E007's own re-declarations, so the
    same assertion is also the check that the re-declaration works.
    """
    for row in _stored(db_session, emitted_run, store):
        survival = np.asarray(row["survival"], dtype=float)

        assert np.all((survival >= 0.0) & (survival <= 1.0)), (
            f"{store} line {row['po_line_id']} stored a survival value outside `[0, 1]`"
        )
        assert np.all(np.diff(survival) <= 0.0), (
            f"{store} line {row['po_line_id']}'s stored curve rises somewhere; a survivor "
            f"function cannot recover mass it has already spent"
        )


@pytest.mark.parametrize("store", ARTIFACT_STORES)
def test_every_stored_array_is_as_long_as_the_run_row_declares(
    db_session: Session, emitted_run: EmittedRun, store: str
) -> None:
    """The lengths are the run's own, read from the run row rather than assumed.

    Each store's `…__run_shape` foreign key proves its rows' `(draw_count,
    horizon_days)` pair belongs to this run, and the two `array_length` checks
    compare the arrays against numbers already proven correct. What is asserted
    here is the step those constraints cannot take: that the pair on the child
    row is the pair the run published, so the grid runs `k = 1..horizon_days`
    with no `S(0)` element.
    """
    run = db_session.execute(RUN_SHAPE_SQL, {"run_id": emitted_run.run_id}).mappings().one()
    for row in _stored(db_session, emitted_run, store):
        assert row["draw_count"] == run["draw_count"]
        assert row["horizon_days"] == run["horizon_days"]
        assert len(row["draws"]) == run["draw_count"]
        assert len(row["survival"]) == run["horizon_days"]


@pytest.mark.parametrize("store", ARTIFACT_STORES)
def test_every_stored_draw_vector_is_ascending_and_non_negative(
    db_session: Session, emitted_run: EmittedRun, store: str
) -> None:
    """The canonical order a percentile lookup depends on, over the stored column.

    `schema_constants.percentile_convention` reads `draws[ceil(p·n)]` and applies
    identically to both populations — only the anchor and the duration semantic
    differ — so an unsorted array makes every published percentile the wrong draw
    while every length and range check still passes.
    """
    for row in _stored(db_session, emitted_run, store):
        draws = np.asarray(row["draws"], dtype=float)

        assert np.all(np.diff(draws) >= 0.0), (
            f"{store} line {row['po_line_id']} stored unsorted draws"
        )
        assert np.all(draws >= 0.0)
        assert np.all(np.isfinite(draws))


@pytest.mark.parametrize("store", ARTIFACT_STORES)
def test_every_stored_residual_is_the_tail_mass_past_the_horizon(
    db_session: Session, emitted_run: EmittedRun, store: str
) -> None:
    """DV-003 over stored rows: the residual and the grid's tail are one comparison.

    Recomputed from the stored draws by a count rather than read off the grid, and
    then compared against the grid's last element as well — `data-model.md`
    § Conventions makes them the same strict `>` at `k = horizon_days`, so a
    residual that agrees with the draws but not with the curve beside it is a row
    whose two halves were taken over different draw sets.

    On the held-out store this is the check ADR-0018 rejected Option D to keep:
    dropping the survival array would have left `residual_tail_mass` with nothing
    to be checked against, and a residual checked against nothing is a copy.
    """
    tolerance = float(db_session.execute(TOLERANCE_SQL).scalar_one())
    for row in _stored(db_session, emitted_run, store):
        draws = np.asarray(row["draws"], dtype=float)
        beyond = np.count_nonzero(draws > float(row["horizon_days"])) / draws.size

        assert abs(float(row["residual_tail_mass"]) - beyond) <= tolerance
        assert abs(float(row["residual_tail_mass"]) - float(row["survival"][-1])) <= tolerance
