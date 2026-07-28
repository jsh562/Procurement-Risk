"""T042 — DV-009's membership half, over `forecast_run.vendor_shrinkage` as stored.

`fn_vendor_shrinkage_wellformed` enforces *shape*: `{median, hpdi_low,
hpdi_high}` per member, ordered, inside `[0,1]`. It cannot enforce *membership*,
because a `CHECK` admits no subquery against `purchase_order_line` — that is G-9,
and this file is the mechanism `data-model.md` puts in its place.

The vendor with no training line is the boundary SC-004 names first, and the
committed cohort does not contain one: its smallest vendor keeps three training
lines after the split. So the case is *emitted* rather than waited for — a run is
re-emitted with the weights the delivered module produces for a roster in which
one vendor is entirely held out, and the assertion is made against the JSONB the
database then holds.
"""

from __future__ import annotations

import dataclasses
import uuid

import numpy as np
import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from forecast.conftest import EmittedRun, FitInput, StoredRun
from model.forecast.manifest import VENDOR_SHRINKAGE_HDI_PROBABILITY
from model.forecast.shrinkage import vendor_shrinkage
from model.forecast.write import insert_artifact_set

#: Module-level SQL, never assembled from values (Ruff S608).
STORED_WEIGHTS_SQL = text("SELECT vendor_shrinkage FROM forecast_run WHERE run_id = :run_id")
ROSTER_VENDORS_SQL = text("SELECT DISTINCT vendor_id FROM purchase_order_line")

#: The three published names, in the order migration `0300`'s helper lists them.
FIELDS = ("median", "hpdi_low", "hpdi_high")

#: The committed cohort's vendor count (`spec.md` § Published Constants). Stated
#: so the membership claim below is "all twelve" rather than "as many as the
#: object happens to carry", which any object satisfies against itself.
ROSTER_SIZE = 12

#: A stand-in posterior for the re-emitted run's weights. Seeded, because this
#: tier is derandomized and a membership claim that redrew its inputs would report
#: defects nobody can reproduce; lognormal because both parameters are scales.
STAND_IN_SEED = 20260728
STAND_IN_DRAWS = 400


def _stand_in_scales() -> tuple[np.ndarray, np.ndarray]:
    """Draws of `(τ, σ)` to compute a weight from, with no fit involved.

    Membership is a claim about which keys the object carries, not about the
    numbers under them, so running a sampler to obtain two positive sequences
    would make this test depend on the thing it is not asserting.
    """
    rng = np.random.default_rng(STAND_IN_SEED)
    noise = rng.standard_normal((2, STAND_IN_DRAWS))
    return np.exp(np.log(0.30) + 0.25 * noise[0]), np.exp(np.log(0.50) + 0.10 * noise[1])


def _stored_weights(db_session: Session, run_id: uuid.UUID) -> dict:
    """The run's shrinkage object, as the database returns it."""
    return db_session.execute(STORED_WEIGHTS_SQL, {"run_id": run_id}).scalar_one()


def test_the_stored_object_names_exactly_the_vendors_the_line_table_holds(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """DV-009's membership half against the roster the fit actually read.

    Compared against `purchase_order_line` rather than against E001's roster
    file: the claim is that every vendor *in the input* got a weight, and a
    vendor present in the file but absent from the loaded rows is a different
    defect with a different owner.
    """
    stored = _stored_weights(db_session, emitted_run.run_id)
    roster = set(db_session.execute(ROSTER_VENDORS_SQL).scalars())

    assert len(roster) == ROSTER_SIZE, (
        f"the loaded cohort carries {len(roster)} vendors rather than {ROSTER_SIZE}; the "
        f"membership claim below is about the committed roster"
    )
    assert set(stored) == roster, (
        f"{sorted(roster - set(stored))} have no stored weight and "
        f"{sorted(set(stored) - roster)} are named by no line"
    )


def test_every_stored_weight_is_a_triple_ordered_inside_the_unit_interval(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """The shape half, read back out of the column rather than out of the writer.

    The helper is what enforces this, so the assertion is that the delivered
    function is doing the job its name claims — a triple whose median sits outside
    its own interval is a published number a reader would act on and no
    downstream consumer would question.
    """
    for vendor, weight in _stored_weights(db_session, emitted_run.run_id).items():
        assert set(weight) == set(FIELDS), f"{vendor} published {sorted(weight)}"
        low, median, high = weight["hpdi_low"], weight["median"], weight["hpdi_high"]

        assert 0.0 <= low <= median <= high <= 1.0, f"{vendor} published {(low, median, high)}"


def test_a_vendor_with_no_training_line_is_stored_rather_than_dropped(
    db_session: Session, fit_input: FitInput, stored_run: StoredRun
) -> None:
    """SC-004's boundary, emitted because the committed cohort does not contain it.

    The roster is doctored so the smallest vendor is entirely held out, the
    delivered module computes the weights over it, and the run is re-emitted
    through the real writer. `ρ = n·τ²/(n·τ² + σ²)` is exactly 0 at `n = 0`, so
    the honest triple is degenerate and it is *published* rather than omitted: an
    absent vendor reads as an oversight while a zero reads as a measurement.
    """
    counts = dict(fit_input.training_line_counts)
    starved = min(counts, key=lambda vendor: counts[vendor])
    counts[starved] = 0

    assert min(fit_input.training_line_counts.values()) > 0, (
        "the committed cohort already contains a vendor with no training line, so this "
        "test should assert over the emitted run instead of doctoring the roster"
    )

    tau, sigma = _stand_in_scales()
    weights = vendor_shrinkage(tau, sigma, counts, VENDOR_SHRINKAGE_HDI_PROBABILITY)
    manifest = dataclasses.replace(
        stored_run.manifest, run_id=uuid.uuid4(), vendor_shrinkage=weights
    )
    insert_artifact_set(
        db_session, manifest, stored_run.assignments, stored_run.line_posteriors
    )

    stored = _stored_weights(db_session, manifest.run_id)

    assert set(stored) == set(counts), (
        f"the starved vendor {starved} must still be named: {sorted(set(counts) - set(stored))} "
        f"were dropped from the stored object"
    )
    assert stored[starved] == pytest.approx({"median": 0.0, "hpdi_low": 0.0, "hpdi_high": 0.0})
