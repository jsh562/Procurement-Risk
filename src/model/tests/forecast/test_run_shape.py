"""T044, T045, T086 — DV-014, DV-035 and NC-10: the run shape, pinned by test.

No delivered constraint binds a run to the published draw count or grid horizon,
and one must not be added: E003's own schema suite inserts fixture runs at five
draws over a three-day horizon, deliberately and legally, using an unequal pair
so a transposition cannot pass. A `CHECK` would fail that fixture. G-4 records
the gap and this file is the mechanism.

**HINT-005 is the whole of how the comparison is made.** The expected pair is
read from `schema_constants` over the connection, never written here — this
module's own source is asserted to carry neither literal, so a future edit that
"simplifies" the comparison into a constant fails rather than quietly becoming a
fourth home for a number that already has one.

T045 is the failing direction. A run emitted at the fixture shape must fail the
same predicate the emitted run passes, otherwise the predicate is satisfied by
anything that stores two integers.

**T086 adds DV-035, the third pinned quantity: `chain_count`.** The four-chain
minimum is a *precondition in the job* (FR-035) and no column, no constraint and
no other rule asserts it — so without this, a run at two chains would cite the
four-chain convention its R-hat and ESS thresholds are justified at while
satisfying every check in the epic. Scoped exactly as DV-014 is, to the runs
this tier's own invocation emitted, because E003's delivered fixtures are
legally inserted at other shapes. The failing direction is **NC-14**, in
`test_refusal_controls.py`, where the job refuses below the minimum with nothing
sampled — an assertion this file cannot make, since a refused run stores no row
for it to read.
"""

from __future__ import annotations

import dataclasses
import uuid
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from forecast.conftest import EmittedRun, StoredRun
from model.forecast.config import CHAINS_MIN, read_run_shape
from model.forecast.manifest import draw_digest
from model.forecast.posterior import survival_grid
from model.forecast.write import LinePosteriorRow, insert_artifact_set

#: Module-level SQL, never assembled from values (Ruff S608).
RUN_SHAPE_SQL = text("SELECT draw_count, horizon_days FROM forecast_run WHERE run_id = :run_id")
CHAIN_COUNT_SQL = text("SELECT chain_count FROM forecast_run WHERE run_id = :run_id")
POSTERIOR_SHAPE_SQL = text(
    """
    SELECT draw_count, horizon_days, array_length(draws, 1) AS drawn,
           array_length(survival, 1) AS gridded
    FROM line_posterior WHERE run_id = :run_id
    """
)

#: E003's delivered fixture shape, and NC-10's plant. Unequal on purpose, so a
#: writer that transposed the pair would still fail the predicate below rather
#: than pass it by symmetry.
FIXTURE_DRAW_COUNT = 5
FIXTURE_HORIZON_DAYS = 3

#: This file, read back to assert how the comparison is made.
THIS_MODULE = Path(__file__)


def assert_committed_shape(db_session: Session, run_id: uuid.UUID) -> None:
    """The predicate DV-014 names, over one run the caller emitted.

    Scoped to a single `run_id` and never to the table, which is load-bearing
    rather than pedantic: E003's fixtures live in `forecast_run` too, and a
    whole-table assertion would fail on them while a run-scoped one fails only on
    an E007 run at the wrong shape. The expected values come from
    `read_run_shape`, which reads the published row over this same connection.
    """
    published = read_run_shape(db_session)
    row = db_session.execute(RUN_SHAPE_SQL, {"run_id": run_id}).mappings().one()

    assert row["draw_count"] == published.draw_count, (
        f"run {run_id} records {row['draw_count']} draws against the "
        f"{published.draw_count} `schema_constants` publishes"
    )
    assert row["horizon_days"] == published.horizon_days, (
        f"run {run_id} records a {row['horizon_days']}-day grid against the "
        f"{published.horizon_days} `schema_constants` publishes"
    )


def test_the_emitted_run_carries_the_shape_the_database_publishes(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """DV-014 over the run this tier's own `forecast-fit` invocation returned.

    The run identifier comes from the job's standard output rather than from a
    query, so what is asserted is the shape of a run E007 emitted — not the shape
    of whatever row happens to be in the table.
    """
    assert_committed_shape(db_session, emitted_run.run_id)


def test_the_realized_sampling_shape_is_the_shape_the_run_records(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """The recorded draw count is the product the invocation actually produced.

    `manifest.py` records the **realized** number of draws per line rather than
    the declared constant, so this closes the one route by which a run could
    record the published pair without having produced it: the chains and draws
    the job was asked for multiply out to the number stored beside them.
    """
    row = db_session.execute(RUN_SHAPE_SQL, {"run_id": emitted_run.run_id}).mappings().one()

    assert emitted_run.chain_count * emitted_run.draws_per_chain == row["draw_count"]


def test_every_artifact_row_repeats_the_runs_own_shape(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """The shape reaches the arrays, which is what makes the pin worth having.

    `fk_line_posterior__run_shape` proves each child row's pair belongs to this
    run and the two `array_length` checks compare each array against it, so the
    chain from `schema_constants` to a stored array is complete only if the pair
    on the child row is the run's own and the arrays match it.
    """
    published = read_run_shape(db_session)
    rows = db_session.execute(
        POSTERIOR_SHAPE_SQL, {"run_id": emitted_run.run_id}
    ).mappings().all()

    assert rows, "the emitted run stored no artifact row to carry a shape"
    for row in rows:
        assert row["draw_count"] == published.draw_count
        assert row["horizon_days"] == published.horizon_days
        assert row["drawn"] == published.draw_count
        assert row["gridded"] == published.horizon_days


def test_every_run_this_tier_emits_records_the_four_chain_minimum(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """DV-035 / T086: `chain_count` at the published minimum, on an emitted run.

    Read from the stored column rather than from the invocation's own argument
    list, which is what makes this an assertion about the run and not about the
    test: the number the run *recorded* is the number a later reader has, and it
    is the number FR-014 requires the manifest to carry.

    At or above rather than equal. FR-035 sets a *minimum* and permits more; the
    committed shape happens to run at it, and a rule written as equality would
    fail a legitimate eight-chain run while catching nothing a `>=` misses.
    """
    recorded = db_session.execute(
        CHAIN_COUNT_SQL, {"run_id": emitted_run.run_id}
    ).scalar_one()

    assert recorded == emitted_run.chain_count, (
        f"the run records {recorded} chains against the {emitted_run.chain_count} this "
        f"invocation asked for; the recorded value is what a later reader has"
    )
    assert recorded >= CHAINS_MIN, (
        f"the run records {recorded} chains against a published minimum of {CHAINS_MIN}. "
        f"Below it, the R-hat and ESS thresholds this run was judged against cite a "
        f"convention that does not hold, while the gate would pass or refuse on the same "
        f"figures (FR-035, DV-035)"
    )


def test_the_comparison_reads_the_published_row_rather_than_a_literal(
    db_session: Session
) -> None:
    """HINT-005, asserted over this file rather than promised in its docstring.

    The published pair appears nowhere in this module's source, so the predicate
    above cannot be satisfied by a constant that agrees with the database today
    and stops agreeing when E003 amends its own row.
    """
    published = read_run_shape(db_session)
    source = THIS_MODULE.read_text(encoding="utf-8")

    assert str(published.draw_count) not in source
    assert str(published.horizon_days) not in source


def test_a_run_at_the_delivered_fixture_shape_fails_the_same_predicate(
    db_session: Session, stored_run: StoredRun
) -> None:
    """NC-10: the shape E003's suite legally passes must fail this assertion.

    The run is emitted through the real writer, so it is storable — every
    delivered constraint admits it, which is exactly the point. The arrays are
    the emitted run's own, truncated to the fixture shape and re-gridded by
    `posterior.py`, so the row is internally consistent and the only thing wrong
    with it is the pair DV-014 pins.
    """
    rows = tuple(
        _at_fixture_shape(row) for row in stored_run.line_posteriors
    )
    manifest = dataclasses.replace(
        stored_run.manifest,
        run_id=uuid.uuid4(),
        draw_count=FIXTURE_DRAW_COUNT,
        horizon_days=FIXTURE_HORIZON_DAYS,
    )
    insert_artifact_set(db_session, manifest, stored_run.assignments, rows)

    with pytest.raises(AssertionError):
        assert_committed_shape(db_session, manifest.run_id)


def _at_fixture_shape(row: LinePosteriorRow) -> LinePosteriorRow:
    """One stored artifact row, re-cut to the delivered fixture shape.

    The grid and the residual are recomputed by `survival_grid` over the
    truncated draws rather than sliced off the stored curve, so the row satisfies
    `ck_line_posterior__residual_matches_grid_tail` and the non-increasing check
    on its own terms — a row that failed a constraint would prove nothing about
    the predicate, because the insert would never happen.
    """
    draws = row.draws[:FIXTURE_DRAW_COUNT]
    grid = survival_grid(draws, FIXTURE_HORIZON_DAYS)
    return LinePosteriorRow(
        po_line_id=row.po_line_id,
        draws=draws,
        survival=grid.survival,
        residual_tail_mass=grid.residual_tail_mass,
        draw_digest=draw_digest(draws),
    )
