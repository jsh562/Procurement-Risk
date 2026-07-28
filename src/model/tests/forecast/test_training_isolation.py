"""T065 — DV-008 / SC-011 / FR-007: no held-out line reached the fitted parameters.

Asserted over **the fit's own input frame**, and the phrase is the requirement
rather than a stylistic preference. FR-007 is a claim about what the likelihood
saw; the database holds what the run *stored*, and a run can store a correct
split while having built its design matrix from every line. The two are different
facts and only one of them is FR-007's.

`SojournFrame.po_line_ids` is what makes the claim checkable at all — it names
the line each row came from, aligned on the same axis as `duration_days`, the
censoring flag and the design matrix. `data-model.md` records this rule's
enforcement point as "the model's design matrix is built from the `train` side
only", and every assertion below is over that matrix's own row axis.

**Recorded as a proxy, which is what DV-008 asks for.** The frame is the input to
`build_model`, not the sampler's internal state: what is proved is that no
held-out line's row was offered to the likelihood. That a correctly built model
then ignores rows it was never given is PyMC's, not this epic's, and claiming
more from this evidence would be claiming more than it carries.

The frame here is rebuilt by `training_frame` over the same rows and the same
split the shared run used, through the delivered module — so what is asserted is
the behaviour of the function the fit calls, not of a re-authored copy that could
agree with a wrong implementation by having been written from the same mistake.
"""

from __future__ import annotations

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from forecast.conftest import EmittedRun, FitInput
from model.forecast.split import HELD_OUT, TRAIN

#: Module-level SQL, never assembled from values (Ruff S608). Read to confirm the
#: stored split and the in-memory one describe the same sides — the fit isolates
#: over the object, and a reader checks the claim against the table.
STORED_SIDES_SQL = text(
    """
    SELECT po_line_id, split_side FROM forecast_split_assignment WHERE run_id = :run_id
    """
)


def _held_out_ids(fit_input: FitInput) -> set:
    """The lines the split declared held out, from the split object itself."""
    return {
        assignment.po_line_id
        for assignment in fit_input.split.assignments
        if assignment.split_side == HELD_OUT
    }


def test_no_held_out_line_appears_in_the_fits_design_matrix(fit_input: FitInput) -> None:
    """DV-008. The row axis of the frame `build_model` was handed, line by line.

    Set disjointness rather than a count: the counts agree whenever the frame has
    the right *size*, and a frame that dropped one training line and admitted one
    held-out line has exactly the right size. Naming the intersection is what
    distinguishes the two.
    """
    held_out = _held_out_ids(fit_input)
    in_frame = set(fit_input.frame.po_line_ids)

    assert held_out, (
        "the split held nothing out, so this test would pass vacuously; the committed "
        "fraction is 0.25 over 199 lines"
    )
    assert in_frame, "the training frame is empty, so there is nothing to have isolated"
    assert not (in_frame & held_out), (
        f"{len(in_frame & held_out)} held-out line(s) contributed sojourn rows to the fit's "
        f"design matrix. Every parameter the run published is then partly a function of the "
        f"lines it will be graded on, and the held-out predictions are in-sample fits"
    )


def test_every_line_the_frame_carries_is_on_the_training_side(fit_input: FitInput) -> None:
    """The same claim from the other direction, which excludes an unassigned line.

    Disjointness from the held-out set is satisfied by a line with no assignment
    at all — it is in neither set. `training_frame` refuses one for exactly that
    reason, and this asserts the refusal held: membership of the `train` side is
    positive rather than "not held out".
    """
    training = {
        assignment.po_line_id
        for assignment in fit_input.split.assignments
        if assignment.split_side == TRAIN
    }
    in_frame = set(fit_input.frame.po_line_ids)

    assert in_frame <= training, (
        f"{len(in_frame - training)} line(s) in the design matrix carry no `train` "
        f"assignment; a line admitted to the fit on the assumption that it is training data "
        f"is FR-007 discharged by an assumption"
    )


def test_the_frames_row_axis_is_aligned_with_every_array_it_carries(
    fit_input: FitInput
) -> None:
    """The alignment the isolation claim rests on, asserted rather than assumed.

    `po_line_ids` proves nothing about `duration_days` unless the two are one
    axis. A frame whose identifier vector was the training lines and whose
    durations were every line's would pass the two tests above and would still
    have fitted on the held-out data.
    """
    frame = fit_input.frame
    rows = frame.row_count

    assert len(frame.po_line_ids) == rows
    assert frame.duration_days.shape == (rows,)
    assert frame.is_censored.shape == (rows,)
    assert frame.transition_index.shape == (rows,)
    assert frame.design.shape[0] == rows


def test_the_excluded_lines_are_excluded_rather_than_silently_absent(
    fit_input: FitInput
) -> None:
    """A line missing from the frame is either held out or declared excluded.

    `training_frame` records the lines it dropped as unstarted at the anchor
    rather than dropping them quietly, so the three sets — in the frame, held
    out, excluded — account for every line the fit read. Without this, a frame
    that lost training rows for some unrelated reason would satisfy the isolation
    claim by having less data, which is the opposite of the property.
    """
    lines = {line.po_line_id for line in fit_input.procurement_input.lines}
    in_frame = set(fit_input.frame.po_line_ids)
    excluded = set(fit_input.frame.excluded_po_line_ids)
    held_out = _held_out_ids(fit_input)

    assert in_frame | excluded | held_out == lines, (
        f"{len(lines - (in_frame | excluded | held_out))} line(s) are neither in the frame, "
        f"nor declared excluded, nor held out — so they left the fit without being recorded "
        f"as having left it"
    )
    assert not (excluded & in_frame)


def test_isolation_is_asserted_over_the_frame_rather_than_over_the_database(
    db_session: Session, emitted_run: EmittedRun, fit_input: FitInput
) -> None:
    """The distinction DV-008 draws, made visible instead of stated.

    The stored assignment and the split the frame was built from describe the
    same sides — checked here, because a reader will want to reach the claim from
    the table. What the table cannot show is the isolation itself: a run whose
    design matrix used every line writes exactly these rows. The evidence is the
    frame, and this test is where the two are tied together and told apart.
    """
    stored = {
        row["po_line_id"]: row["split_side"]
        for row in db_session.execute(
            STORED_SIDES_SQL, {"run_id": emitted_run.run_id}
        ).mappings()
    }
    in_memory = {
        assignment.po_line_id: assignment.split_side
        for assignment in fit_input.split.assignments
    }

    assert stored == in_memory, (
        "the stored split and the split the training frame was built from disagree, so the "
        "isolation asserted above is about a different assignment than the one the run "
        "published"
    )
    stored_held_out = {line for line, side in stored.items() if side == HELD_OUT}

    assert not (set(fit_input.frame.po_line_ids) & stored_held_out)
    assert np.all(np.isfinite(fit_input.frame.duration_days))
