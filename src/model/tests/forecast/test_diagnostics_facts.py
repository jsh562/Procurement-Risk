"""T085 — DV-012: a stored run breached nothing, and treedepth is the exception.

Two claims, and both are **database facts** rather than assertions about the
writer. `ck_forecast_diagnostic__blocking_rows_passed` refuses a blocking row
whose `passed` is false outright, and `ck_forecast_diagnostic__blocking_matches_
metric` pins `is_blocking` to `metric <> 'max_treedepth_hits'` so neither
classification can be edited row by row. Combined with the foreign key, a
non-converged fit has nowhere to put its evidence and no run row to attach it
to — which is mechanism 3 of § The Refusal Guarantee, "a writer that skips the
gate".

So this file asserts each claim twice: once over the rows the tier's own run
stored, and once by planting the row the claim forbids and reading back the
constraint that refused it. The plant is what makes the first half mean
something — a run whose diagnostics all passed satisfies "every blocking row
passed" whether or not anything is enforcing it.

**Which direction is silent matters here.** The check constrains only rows where
`is_blocking`, so what it admits is a wrongly-*passed* row. That is T078's
territory — the arithmetic — and this file covers the half the database owns.
"""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from forecast.conftest import EmittedRun
from model.forecast.diagnostics import threshold_for

#: Module-level SQL, never assembled from values (Ruff S608).
BLOCKING_ROWS_SQL = text(
    """
    SELECT metric, parameter_name, observed_value, threshold_value, threshold_direction,
           is_blocking, passed
    FROM forecast_diagnostic WHERE run_id = :run_id
    """
)
PLANT_SQL = text(
    """
    INSERT INTO forecast_diagnostic (
        diagnostic_id, run_id, diagnostic_scope, parameter_name, metric,
        observed_value, threshold_value, threshold_direction, is_blocking, passed
    )
    VALUES (
        :diagnostic_id, :run_id, :diagnostic_scope, :parameter_name, :metric,
        :observed_value, :threshold_value, :threshold_direction, :is_blocking, :passed
    )
    """
)

#: A parameter name no run monitors, so a plant cannot collide with a stored row
#: on `uq_forecast_diagnostic__run_metric_parameter` and be refused for the
#: wrong reason.
PLANTED_PARAMETER = "planted_parameter_no_run_monitors"

#: The one metric FR-018 makes reported rather than blocking.
TREEDEPTH = "max_treedepth_hits"


def _plant(db_session: Session, run_id: uuid.UUID, **overrides) -> None:
    """Insert one diagnostic row, built from the published bar and then doctored.

    Every field defaults to a value the constraints accept, so a plant is
    refused by the rule under test and never by an unrelated one. The caller
    overrides exactly the field whose rule is being demonstrated.
    """
    metric = overrides.pop("metric", "r_hat")
    published = threshold_for(metric)
    scoped = published.diagnostic_scope == "parameter"
    parameters = {
        "diagnostic_id": uuid.uuid4(),
        "run_id": run_id,
        "diagnostic_scope": published.diagnostic_scope,
        "parameter_name": PLANTED_PARAMETER if scoped else None,
        "metric": metric,
        "observed_value": published.threshold_value,
        "threshold_value": published.threshold_value,
        "threshold_direction": published.threshold_direction,
        "is_blocking": published.is_blocking,
        "passed": True,
    }
    parameters.update(overrides)
    db_session.execute(PLANT_SQL, parameters)


def test_every_stored_blocking_row_passed(db_session: Session, emitted_run: EmittedRun) -> None:
    """DV-012's first half, over the run this tier emitted.

    A published run's stored evidence says its sampler converged, and every
    blocking row is part of that claim. The rows are read back from the store
    rather than taken from the gate, because a run that shipped is a run whose
    evidence survived the write.
    """
    rows = db_session.execute(BLOCKING_ROWS_SQL, {"run_id": emitted_run.run_id}).mappings().all()
    failing = [dict(row) for row in rows if row["is_blocking"] and not row["passed"]]

    assert rows, "the emitted run stored no diagnostic row at all"
    assert not failing, (
        f"{len(failing)} blocking row(s) did not pass and were stored anyway — first "
        f"{failing[0]}. `ck_forecast_diagnostic__blocking_rows_passed` is supposed to "
        f"make that unrepresentable"
    )


def test_treedepth_is_the_only_non_blocking_row(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """DV-012's second half: FR-018's classification, as it was actually stored.

    Treedepth is reported and never blocking; the other five always block. The
    assertion is an equality over the non-blocking set rather than a check that
    treedepth is non-blocking, because the latter is satisfied by a run in which
    everything is non-blocking.
    """
    rows = db_session.execute(BLOCKING_ROWS_SQL, {"run_id": emitted_run.run_id}).mappings().all()
    reported = {row["metric"] for row in rows if not row["is_blocking"]}
    blocking = {row["metric"] for row in rows if row["is_blocking"]}

    assert reported == {TREEDEPTH}
    assert TREEDEPTH not in blocking
    assert len(blocking) == 5


def test_a_failing_blocking_row_cannot_be_stored(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """The plant that makes the first claim mean something.

    The row is well formed in every other respect — published metric, published
    threshold, the direction that metric requires, `is_blocking` matching the
    metric — and its `passed` is arithmetic against its own two numbers, so
    `…__passed_matches_threshold` is satisfied. The only thing wrong with it is
    that a blocking metric breached, which is exactly the evidence a
    non-converged fit would want to leave behind.
    """
    published = threshold_for("r_hat")

    with pytest.raises(IntegrityError) as refused, db_session.begin_nested():
        _plant(
            db_session,
            emitted_run.run_id,
            observed_value=published.threshold_value + 1.0,
            passed=False,
        )

    assert "ck_forecast_diagnostic__blocking_rows_passed" in str(refused.value), (
        f"the plant was refused by something other than the blocking-rows rule: {refused.value}"
    )


def test_a_verdict_its_own_numbers_refute_cannot_be_stored(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """The route around the rule above: claim the breach passed.

    This is the silent direction. A row breaching its bar but marked `passed`
    satisfies `…__blocking_rows_passed` completely — the run would ship with
    evidence asserting a convergence it did not have — and it is
    `…__passed_matches_threshold` that closes it, because `passed` is arithmetic
    rather than an opinion.
    """
    published = threshold_for("r_hat")

    with pytest.raises(IntegrityError) as refused, db_session.begin_nested():
        _plant(
            db_session,
            emitted_run.run_id,
            observed_value=published.threshold_value + 1.0,
            passed=True,
        )

    assert "ck_forecast_diagnostic__passed_matches_threshold" in str(refused.value)


def test_treedepth_cannot_be_reclassified_as_blocking(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """FR-018 as a database fact, in the direction that would refuse a good run.

    Treedepth is an efficiency concern rather than a validity one, so a run that
    hit the cap still ships. A row recording it as blocking would combine with
    `…__blocking_rows_passed` to make that run unstorable — a gate nobody
    published, created by editing one row's classification.
    """
    with pytest.raises(IntegrityError) as refused, db_session.begin_nested():
        _plant(db_session, emitted_run.run_id, metric=TREEDEPTH, is_blocking=True)

    assert "ck_forecast_diagnostic__blocking_matches_metric" in str(refused.value)


def test_a_blocking_metric_cannot_be_reclassified_as_reported(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """The same fact in the direction that would let a breach ship.

    `is_blocking = false` on `ebfmi` puts the row outside
    `…__blocking_rows_passed`'s quantifier, so a run whose energy diagnostic
    failed could store its own evidence and publish. The biconditional refuses
    it, which is what "neither classification can be edited row by row" means.
    """
    published = threshold_for("ebfmi")

    with pytest.raises(IntegrityError) as refused, db_session.begin_nested():
        _plant(
            db_session,
            emitted_run.run_id,
            metric="ebfmi",
            observed_value=published.threshold_value - 0.1,
            is_blocking=False,
            passed=False,
        )

    assert "ck_forecast_diagnostic__blocking_matches_metric" in str(refused.value)
