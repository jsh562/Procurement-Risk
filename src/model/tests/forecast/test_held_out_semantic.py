"""T062 — DV-040: the held-out duration semantic **measured**, not labelled.

`ck_held_out_prediction__duration_semantic` admits one string, and a string
agrees with itself. `fk_held_out_prediction__line_anchor` proves the *anchor* and
reaches the *duration* not at all — it fixes where the clock starts and says
nothing about what was measured from there. So an implementation that stored
as-of-anchored **remaining** durations under the order-date label would satisfy
every constraint on this table, be graded against the wrong quantity by E014, and
be reported by nothing. This file is the check that closes that.

**The band is published before any draw is seen**, which is the whole force of
the comparison. It is stated below as two multiples of the training split's
Kaplan–Meier median, derived from the sampling error of a median at the fit's own
log-scale spread — a quantity fixed by the dataset, not by the run — and it is
written here rather than computed from the result it judges. The estimator is
AD-008's, reused from `ablation.py` rather than re-implemented, so no new
machinery enters and the reference is the same non-parametric one SC-008 is
judged against.

**This is deliberately not a comparison against each line's observed outcome.**
That is grading, and FR-026 reserves the verdict for the evaluation harness; this
epic records the inputs a calibration verdict needs and publishes none of its
own. What is asserted is a distributional statement about a stored population
against a reference computed from a *different* population — the training split —
by a *different* estimator.
"""

from __future__ import annotations

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from forecast.conftest import EmittedRun, FitInput
from model.forecast.ablation import kaplan_meier_floor

#: Module-level SQL, never assembled from values (Ruff S608).
HELD_OUT_DRAWS_SQL = text(
    """
    SELECT h.po_line_id, h.anchor_date, h.draws
    FROM held_out_prediction h WHERE h.run_id = :run_id ORDER BY h.po_line_id
    """
)
RUN_ANCHOR_SQL = text("SELECT as_of_date FROM forecast_run WHERE run_id = :run_id")

#: Read for use as a **control** only. The two populations are never compared as
#: though they measured the same thing; what is asserted of this one is that it
#: misses the band the other is judged by.
OPEN_DRAWS_SQL = text("SELECT draws FROM line_posterior WHERE run_id = :run_id")

# ---------------------------------------------------------------------------
# The band, published here and derived from nothing this run produced
# ---------------------------------------------------------------------------
#
# The stored held-out draws are total durations from each line's own order date,
# so their pooled median estimates the cohort's median total duration. The
# training split's Kaplan–Meier median estimates the same quantity, by a
# non-parametric estimator, on the other half of the split. Two estimates of one
# number, and the band is how far apart two such estimates may legitimately fall.
#
# **Where the width comes from.** The median of a lognormal sample carries a
# standard error of about `1.253·sigma/sqrt(n)` in log units. E005's datasheet
# publishes the dataset's log-scale spread at `sigma ~ 0.53`, and a 0.25 split of
# 175 delivered lines leaves roughly 44 gradeable ones (L-3), so the standard
# error is about `1.253 * 0.53 / sqrt(44) ~ 0.10` — a multiplicative
# `exp(+-0.10)`. Three standard errors is `exp(+-0.30) ~ [0.74, 1.35]`, rounded
# outward to the two multiples below. Every input to that derivation — the
# published spread, the split fraction, the delivered-line count — is fixed
# before the fit runs, which is what "published before any draw is seen" means
# operationally rather than as a promise.
#
# The band is wide on purpose. It is not a calibration statement and must not
# become one: it separates "the stored quantity is a total duration from the
# line's order date" from "the stored quantity is something else", and the
# something else it is aimed at misses by a factor near two, not by a few points.

#: The narrowest and widest ratio of the stored held-out median to the training
#: split's Kaplan-Meier median that the band admits.
BAND_LOW_MULTIPLE = 0.70
BAND_HIGH_MULTIPLE = 1.40


def _pooled_held_out_draws(db_session: Session, emitted_run: EmittedRun) -> np.ndarray:
    """Every stored held-out draw, pooled across lines and sorted ascending.

    Pooled rather than a median of per-line medians, for the reason
    `aggregate_median_forecast` gives on the other population: the lines differ
    in vendor, category and realized rework path, and an average of their medians
    summarises the cohort's composition as much as its durations.
    """
    rows = db_session.execute(HELD_OUT_DRAWS_SQL, {"run_id": emitted_run.run_id}).mappings().all()

    assert rows, (
        "the shared run stored no held-out prediction, so there is no population to measure "
        "a semantic over and this file would pass vacuously"
    )
    return np.sort(np.concatenate([np.asarray(row["draws"], dtype=float) for row in rows]))


def _kaplan_meier_median(fit_input: FitInput, emitted_run: EmittedRun) -> float:
    """The reference: AD-008's estimator on the **training** split alone.

    Reused rather than re-implemented, and computed over the training side, so
    the reference shares no line with the population it judges and no step with
    the fit that produced it — `ablation.py` cannot reach the sampler, the graph
    or the lognormal family at all.
    """
    return kaplan_meier_floor(
        fit_input.procurement_input.lines, fit_input.split, emitted_run.as_of_date
    ).kaplan_meier_median


def test_the_stored_held_out_median_lands_inside_the_published_band(
    db_session: Session, emitted_run: EmittedRun, fit_input: FitInput
) -> None:
    """DV-040. The stored draws measure a total duration from each line's order date.

    The failing implementation this is aimed at is not hypothetical: it is the
    open population's own arithmetic applied to this store, which every
    constraint here accepts. Its pooled median is far below the reference,
    because conditioning on an elapsed time of months discards the whole body of
    the distribution and keeps a tail.
    """
    reference = _kaplan_meier_median(fit_input, emitted_run)
    pooled = _pooled_held_out_draws(db_session, emitted_run)
    realized = float(np.median(pooled))
    ratio = realized / reference

    assert BAND_LOW_MULTIPLE <= ratio <= BAND_HIGH_MULTIPLE, (
        f"the stored held-out draws pool to a median of {realized:.1f} days against a "
        f"training-split Kaplan-Meier median of {reference:.1f} — a ratio of {ratio:.3f}, "
        f"outside the pre-published band [{BAND_LOW_MULTIPLE}, {BAND_HIGH_MULTIPLE}]. Either "
        f"the stored quantity is not a total duration from each line's own order date, or "
        f"the fit and the non-parametric estimate of the same cohort disagree by more than "
        f"three standard errors of a median at the dataset's published spread"
    )


def test_the_same_draws_read_as_remaining_durations_fall_outside_the_band(
    db_session: Session, emitted_run: EmittedRun, fit_input: FitInput
) -> None:
    """The negative control, without which the band above is a band around anything.

    The mis-anchored implementation is constructed here from the stored draws
    themselves, by the identity that defines conditioning: the conditional law at
    elapsed `e` is the parent law truncated at `e` and re-based, so
    `{T - e : T > e}` over a line's own total draws **is** the remaining-duration
    draw set that implementation would have produced for it. No re-fit and no
    posterior are needed, and the control is therefore exact rather than
    approximated.

    Each held-out line delivered before the run's anchor, so its elapsed time
    there is months and the truncation keeps only the far tail. If this lands
    inside the band, the band is not separating the two quantities and the test
    above is decoration.
    """
    reference = _kaplan_meier_median(fit_input, emitted_run)
    anchor = db_session.execute(RUN_ANCHOR_SQL, {"run_id": emitted_run.run_id}).scalar_one()
    rows = db_session.execute(HELD_OUT_DRAWS_SQL, {"run_id": emitted_run.run_id}).mappings().all()

    surviving = []
    for row in rows:
        elapsed = float((anchor - row["anchor_date"]).days)
        draws = np.asarray(row["draws"], dtype=float)
        surviving.append(draws[draws > elapsed] - elapsed)
    control = np.concatenate([kept for kept in surviving if kept.size])

    assert control.size, (
        "no draw of any held-out line survives to the run's anchor, so the mis-anchored "
        "implementation would have stored nothing at all — outside the band by construction"
    )
    ratio = float(np.median(control)) / reference

    assert not (BAND_LOW_MULTIPLE <= ratio <= BAND_HIGH_MULTIPLE), (
        f"the same stored draws, read as remaining durations from the run's as-of date, pool "
        f"to a median ratio of {ratio:.3f}, which the band "
        f"[{BAND_LOW_MULTIPLE}, {BAND_HIGH_MULTIPLE}] admits. The band then does not "
        f"distinguish the two duration semantics and DV-040 is measuring nothing"
    )


def test_the_open_populations_median_also_falls_outside_the_band(
    db_session: Session, emitted_run: EmittedRun, fit_input: FitInput
) -> None:
    """A second control, and the one a mixed-up writer would actually produce.

    The control above transforms the held-out draws; this one takes the other
    store's stored draws as they are. It is the population an implementation
    would land here by writing `line_posterior`'s rows into
    `held_out_prediction` — the merge Option B of ADR-0018 was rejected for — and
    it must miss the band too, or the band cannot tell the two stores apart.
    """
    reference = _kaplan_meier_median(fit_input, emitted_run)
    stored = db_session.execute(OPEN_DRAWS_SQL, {"run_id": emitted_run.run_id}).scalars().all()

    assert stored, "the shared run stored no `line_posterior` row to use as a control"
    open_draws = np.sort(np.concatenate([np.asarray(draws, dtype=float) for draws in stored]))
    ratio = float(np.median(open_draws)) / reference

    assert not (BAND_LOW_MULTIPLE <= ratio <= BAND_HIGH_MULTIPLE), (
        f"the open population's conditional remaining draws pool to a median ratio of "
        f"{ratio:.3f}, inside the band the held-out population is judged by. The two stores "
        f"hold different quantities and the band must separate them"
    )
