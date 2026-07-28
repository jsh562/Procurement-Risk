"""T125 — FR-010's day-grid identity over emitted rows in **both** stores.

`survival[k]` is the fraction of a line's stored draws **exceeding** `k`, on a
grid indexed `k = 1..horizon_days` with no `S(0)` element. Three separable
claims live in that sentence and this file takes them one at a time:

1. **the identity**, recomputed from the stored `draws` by `searchsorted` over
   the ascending column rather than by the broadcast comparison
   `test_stored_arrays.py` uses. Two implementations of one definition, so an
   agreement is evidence about the stored curve and not about a single
   expression evaluated twice;
2. **the origin** — element one is day one. An off-by-one grid satisfies every
   length check, every monotonicity check and the residual comparison, and shifts
   every published percentile by a day;
3. **the strictness** — a draw landing exactly on day `k` counts as delivered.

**The third claim is not observable over these rows, and this file says so.**
The stored draws are continuous quantities; not one of the run's draws lands
exactly on an integer day, so recomputing the whole grid with `>=` produces the
identical array. That is demonstrated below rather than asserted, and the
convention is fixed where it can be — `posterior.survival_grid`'s property tier,
which constructs the tie it needs. A test claiming to check strictness here would
be reporting a distinction the data cannot make.

**Both stores, because the identity is anchor-blind and the stores are not.**
What `line_posterior` and `held_out_prediction` disagree about is what a draw
*means* (FR-029, and `test_duration_semantics.py`), never how the grid counts
them. Every array invariant on the second store is E007's own re-declaration —
no delivered constraint reaches it — so a property that holds on the first
because E003 enforces it holds on the second only if E007 enforced it too.
"""

from __future__ import annotations

import numpy as np
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from forecast.conftest import EmittedRun

#: Module-level SQL, never assembled from values (Ruff S608). Two statements
#: rather than one built from a table name, and keyed by store so a failure names
#: the population that diverged.
STORED_GRID_SQL = {
    "line_posterior": text(
        "SELECT po_line_id, draws, survival FROM line_posterior "
        "WHERE run_id = :run_id ORDER BY po_line_id"
    ),
    "held_out_prediction": text(
        "SELECT po_line_id, draws, survival FROM held_out_prediction "
        "WHERE run_id = :run_id ORDER BY po_line_id"
    ),
}
TOLERANCE_SQL = text("SELECT probability_sum_tolerance FROM schema_constants")

#: Both artifact populations, named rather than derived from the mapping above,
#: so a store dropped from it is a collection error and not a halved test run.
ARTIFACT_STORES = ("line_posterior", "held_out_prediction")

#: The first grid element's day. FR-010 indexes `k = 1..horizon_days`, so there
#: is no `S(0)`, and this is the number an off-by-one implementation gets wrong.
FIRST_GRID_DAY = 1


def stored_rows(db_session: Session, emitted_run: EmittedRun, store: str) -> list:
    """Every artifact row of the shared run in one store, in `po_line_id` order."""
    rows = (
        db_session.execute(STORED_GRID_SQL[store], {"run_id": emitted_run.run_id})
        .mappings()
        .all()
    )
    assert rows, f"the shared run stored no `{store}` row, so nothing here is measurable"
    return list(rows)


def tail_fraction(draws: np.ndarray, days: np.ndarray) -> np.ndarray:
    """`count(draws > day) / draw_count` for each day, by binary search.

    The draws are stored ascending — `ck_*__draws_sorted` requires it — so the
    insertion point of `day` on the right is the number of draws at or below it,
    and everything after that exceeds it. A different computation from the
    broadcast comparison the other file uses, which is the point of computing it
    again at all.
    """
    at_or_below = np.searchsorted(draws, days, side="right")
    return (draws.size - at_or_below) / draws.size


@pytest.fixture
def tolerance(db_session: Session) -> float:
    """The published agreement tolerance, read over the connection (AD-009).

    Read rather than written here: `schema_constants.probability_sum_tolerance`
    is the number E003 publishes for exactly this comparison, and a literal in
    this file would be a second home for it.
    """
    return float(db_session.execute(TOLERANCE_SQL).scalar_one())


@pytest.mark.parametrize("store", ARTIFACT_STORES)
def test_every_stored_grid_is_the_strict_tail_fraction_of_its_own_draws(
    db_session: Session, emitted_run: EmittedRun, store: str, tolerance: float
) -> None:
    """FR-010 over emitted rows, by a second implementation of the definition.

    Every element of every row, never a sample of them: a curve that is right on
    day 1 and wrong on day 300 passes every structural check the schema carries
    and is wrong exactly where a planner reads it.
    """
    for row in stored_rows(db_session, emitted_run, store):
        draws = np.asarray(row["draws"], dtype=float)
        survival = np.asarray(row["survival"], dtype=float)
        days = np.arange(FIRST_GRID_DAY, FIRST_GRID_DAY + survival.size, dtype=float)
        expected = tail_fraction(draws, days)
        divergence = float(np.max(np.abs(survival - expected)))

        assert divergence <= tolerance, (
            f"{store} line {row['po_line_id']}'s stored grid diverges from a binary-search "
            f"recomputation over its own stored draws by {divergence:.3e}, past the published "
            f"tolerance of {tolerance:.3e}; the grid is a pure function of the draws"
        )


@pytest.mark.parametrize("store", ARTIFACT_STORES)
def test_the_first_grid_element_is_day_one_rather_than_day_zero(
    db_session: Session, emitted_run: EmittedRun, store: str, tolerance: float
) -> None:
    """The origin, checked where the rows can tell the two apart — and where they cannot.

    `S(0)` is 1.0 by construction for a non-negative duration, so a grid shifted
    by one day differs from a correct one only on the rows carrying a draw
    inside the first day. Those rows are found rather than assumed; a store with
    none of them reports *why* the claim is unobservable there — its shortest
    draw exceeds a day — instead of passing as though it had been checked.
    """
    distinguishing = 0
    for row in stored_rows(db_session, emitted_run, store):
        draws = np.asarray(row["draws"], dtype=float)
        survival = np.asarray(row["survival"], dtype=float)
        shifted = float(np.count_nonzero(draws > 0.0) / draws.size)
        expected = float(np.count_nonzero(draws > FIRST_GRID_DAY) / draws.size)

        assert abs(float(survival[0]) - expected) <= tolerance
        if abs(shifted - expected) > tolerance:
            distinguishing += 1

    if distinguishing == 0:
        shortest = min(
            float(np.asarray(row["draws"], dtype=float).min())
            for row in stored_rows(db_session, emitted_run, store)
        )
        assert shortest > FIRST_GRID_DAY, (
            f"no `{store}` row distinguishes a day-one origin from a day-zero one, and its "
            f"shortest stored draw is {shortest:.4f} days — so the rows *could* have "
            f"distinguished them and the grid agrees with both readings, which the identity "
            f"above should have made impossible"
        )


@pytest.mark.parametrize("store", ARTIFACT_STORES)
def test_the_strictness_clause_is_not_observable_over_these_rows(
    db_session: Session, emitted_run: EmittedRun, store: str
) -> None:
    """An honest record rather than a check that cannot fail.

    FR-010 fixes a tie-breaking convention: a draw landing exactly on day `k`
    has delivered by the end of it. Over stored `double precision` draws no tie
    occurs — the demonstration is the recomputation under `>=`, which returns
    the identical grid — so nothing here evidences the convention, and a test
    named for it would be a detector that has never had anything to detect.
    `test_posterior_properties.py` fixes the convention where a tie can be
    constructed.
    """
    exact_hits = 0
    for row in stored_rows(db_session, emitted_run, store):
        draws = np.asarray(row["draws"], dtype=float)
        days = np.arange(FIRST_GRID_DAY, FIRST_GRID_DAY + len(row["survival"]), dtype=float)
        strict = tail_fraction(draws, days)
        inclusive = (draws.size - np.searchsorted(draws, days, side="left")) / draws.size
        row_hits = int(np.count_nonzero(np.isin(draws, days)))
        exact_hits += row_hits

        assert np.array_equal(strict, inclusive) == (row_hits == 0)

    assert exact_hits == 0, (
        f"{exact_hits} stored draw(s) in `{store}` land exactly on an integer day, so the "
        f"strict convention **is** observable over these rows and this file should assert it "
        f"rather than record it as uncheckable"
    )
