"""T041 — DV-036 / SC-006: `covariate_names` as a measurement, not a label.

`ck_forecast_run__covariates_non_empty` reaches non-emptiness and stops there, so
three plausible strings satisfy it whatever the fit used and SC-006 would be
discharged by a constraint that never looked at the design. What this file
asserts is the step the constraint cannot take: the recorded list equals the set
the fit's own input frame carries, rebuilt over the same rows at the same anchor.

The second half is what makes the first half mean anything. A test comparing the
recorded list against `model.COVARIATES` would pass against a hard-coded literal,
so the measurement is also exercised in the direction where it must *drop* a
name — a frame with no censored row records no `days_in_state`, and a frame with
one stratum records no `lifecycle_state`.
"""

from __future__ import annotations

import dataclasses

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from forecast.conftest import EmittedRun, FitInput
from model.forecast.model import COVARIATES, covariate_names

#: Module-level SQL, never assembled from values (Ruff S608).
RECORDED_COVARIATES_SQL = text(
    "SELECT covariate_names FROM forecast_run WHERE run_id = :run_id"
)


def _recorded(db_session: Session, emitted_run: EmittedRun) -> tuple[str, ...]:
    """The covariate list as the run row stores it, in column order."""
    return tuple(
        db_session.execute(
            RECORDED_COVARIATES_SQL, {"run_id": emitted_run.run_id}
        ).scalar_one()
    )


def test_the_recorded_covariate_list_equals_the_fit_frames_own_set(
    db_session: Session, emitted_run: EmittedRun, fit_input: FitInput
) -> None:
    """DV-036: the recorded list is what the design matrix actually carried.

    Compared as an ordered tuple rather than as a set, because the column is a
    `text[]` a reader indexes — two lists holding the same names in different
    orders describe the same fit but not the same published artifact, and only
    one of them is what the writer stored.
    """
    measured = covariate_names(fit_input.frame)

    assert _recorded(db_session, emitted_run) == measured, (
        f"the run records {_recorded(db_session, emitted_run)} while the frame the fit was "
        f"built from carries {measured}; the recorded list is required to be a measurement "
        f"of the design rather than a label written beside it"
    )


def test_every_recorded_covariate_is_one_of_the_three_the_model_admits(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """FR-002's three covariates, and no fourth arriving through a free-text column.

    `covariate_names` is `text[]` with a non-emptiness check, so any string is
    storable. AD-001 gives each of the three exactly one way of entering the
    graph, and a name outside that set describes an entry route the model has no
    parameter for.
    """
    recorded = _recorded(db_session, emitted_run)

    assert recorded, "the run recorded no covariate at all"
    assert set(recorded) <= set(COVARIATES)
    assert len(set(recorded)) == len(recorded)


def test_the_covariate_set_is_measured_because_a_frame_without_one_drops_it(
    fit_input: FitInput,
) -> None:
    """The direction that separates a measurement from a constant.

    Two counterfactual frames, each built by removing one covariate's *entry
    condition* from the real frame rather than by constructing a toy: with no
    censored row of positive duration nothing truncates, so days-in-state enters
    nowhere; with one transition stratum the lifecycle state selects between
    nothing. A hard-coded list would report all three in both cases.
    """
    frame = fit_input.frame
    full = covariate_names(frame)

    assert set(full) == set(COVARIATES), (
        f"the committed cohort is expected to exercise all three covariates; it carries "
        f"{full}, so the two counterfactuals below would not be measuring a drop"
    )

    uncensored = dataclasses.replace(frame, is_censored=np.zeros_like(frame.is_censored))
    single_stratum = dataclasses.replace(
        frame, transition_index=np.zeros_like(frame.transition_index)
    )

    assert "days_in_state" not in covariate_names(uncensored)
    assert "lifecycle_state" not in covariate_names(single_stratum)


def test_the_recorded_list_is_not_merely_the_constraints_non_emptiness(
    db_session: Session, emitted_run: EmittedRun, fit_input: FitInput
) -> None:
    """SC-006 is not discharged by `ck_forecast_run__covariates_non_empty`.

    The constraint admits `['a']` and `['x', 'y', 'z']` alike, so the assertion
    that matters is that the recorded value moves with the design: a frame whose
    decision rows are removed records two covariates, and the run — whose frame
    has them — records three. Both are non-empty and only one is this fit's.
    """
    without_decisions = dataclasses.replace(
        fit_input.frame, is_decision=np.zeros_like(fit_input.frame.is_decision)
    )
    reduced = covariate_names(without_decisions)

    assert reduced, "the counterfactual frame must still satisfy the non-emptiness constraint"
    assert reduced != _recorded(db_session, emitted_run)
    assert "approval_cycle_count" not in reduced
