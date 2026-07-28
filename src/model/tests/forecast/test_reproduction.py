"""T103 — DV-018 / SC-018: a re-fit agrees per line, and the provenance is equal.

The claim FR-022 makes, asserted over a **real second fit** of the tier's shared
run at that run's own recorded shape. Four things have to hold together and each
would be satisfied by the wrong behaviour on its own:

* the population is **every stored line in both stores** — `line_posterior` and
  `held_out_prediction` — because the published tolerance is derived across both
  and scoping to one would leave the other's reproduction unclaimed while still
  borrowing a number derived from both;
* agreement is **per line** on the median and the 80th percentile, never an
  aggregate: an aggregate median can agree while individual lines move in
  compensating directions, which is the reading FR-022 exists to exclude;
* the manifest's provenance fields are **exactly** equal, measured on the re-run
  from the same functions a fit measures them from rather than copied off the run
  under comparison, which would be equal by construction;
* the comparison is never bitwise equality of draws. That claim is FR-032's, is
  optional, and is reported separately — `test_pin_scope.py` owns it.

The failing direction is `test_reproduction_controls.py`'s (NC-17); this file is
the passing one, and without a control it would be satisfied by a harness that
compared nothing.
"""

from __future__ import annotations

import uuid

import pytest

from forecast.conftest import EmittedRun, ReproducedRun
from model.forecast.compare import MEDIAN_PROBABILITY, P80_PROBABILITY, nearest_rank_percentile
from model.forecast.config import (
    REPRODUCTION_PREDICTIVE_ESS_FRACTION_MIN,
    REPRODUCTION_TOLERANCE_DAYS,
)
from model.forecast.reproduce import (
    HELD_OUT_STORE,
    LINE_POSTERIOR_STORE,
    OUTCOME_AGREES,
    OUTCOME_DISAGREES,
    OUTCOME_OUTSIDE_BASIS,
    PROVENANCE_FIELDS,
    ReproductionOutcome,
)

#: The seven fields FR-043 names, re-typed here rather than imported for the
#: comparison below. The module's tuple is what the job compares; this is the
#: review of it, and a field dropped from one and not the other fails.
REVIEWED_PROVENANCE_FIELDS = (
    "code_commit",
    "code_worktree_dirty",
    "library_versions",
    "model_version",
    "artifact_schema_version",
    "roster_hash",
    "split_seed_entropy",
)

#: The two quantities per line, and the two stores. Both restated so a
#: comparison that silently dropped a store or a quantile fails a count here
#: rather than passing on a smaller population.
QUANTITIES_PER_LINE = 2
BOTH_STORES = (LINE_POSTERIOR_STORE, HELD_OUT_STORE)


@pytest.fixture
def outcome(reproduced_run: ReproducedRun) -> ReproductionOutcome:
    """The shared reproduction's verdict, so each test names what it reads."""
    return reproduced_run.reproduction.outcome


def test_the_reproduction_agrees_within_the_published_tolerance(
    outcome: ReproductionOutcome,
) -> None:
    """SC-018's outcome, and the exit status class that carries it.

    Asserted as an equality against one of three named outcomes rather than as a
    truthiness, because the third — outside the tolerance's stated basis — is
    neither a pass nor a failure and a boolean verdict could not express it.
    """
    assert outcome.verdict == OUTCOME_AGREES, (
        f"the re-fit did not agree: {len(outcome.breaches)} comparison(s) outside "
        f"{REPRODUCTION_TOLERANCE_DAYS} days, {len(outcome.outside_basis)} outside the "
        f"basis, provenance differing on {list(outcome.differing_provenance_fields)}, "
        f"{len(outcome.unpaired)} unpaired line(s)"
    )
    assert outcome.exit_status == 0
    assert outcome.verdict in (OUTCOME_AGREES, OUTCOME_DISAGREES, OUTCOME_OUTSIDE_BASIS)


def test_every_single_line_is_inside_the_tolerance_rather_than_the_aggregate(
    outcome: ReproductionOutcome,
) -> None:
    """Per line and never an aggregate — the distinction FR-022 turns on.

    The mean delta is asserted to be *small* as well, and then explicitly not
    relied on: it is printed in the failure message so a reader can see that a
    passing aggregate would have said nothing about the lines beneath it.
    """
    worst = outcome.worst

    assert outcome.breaches == (), (
        f"{len(outcome.breaches)} of {len(outcome.comparisons)} comparisons breached while "
        f"the mean delta is "
        f"{sum(row.delta_days for row in outcome.comparisons) / len(outcome.comparisons):+.3f} "
        f"days — which is exactly the aggregate that would have reported agreement"
    )
    assert abs(worst.delta_days) <= REPRODUCTION_TOLERANCE_DAYS
    for row in outcome.comparisons:
        assert row.agrees


def test_the_population_is_every_stored_line_in_both_stores(
    outcome: ReproductionOutcome, reproduced_run: ReproducedRun
) -> None:
    """The comparison covers the population the tolerance was derived across.

    An equality against the stores' own contents, in both directions: a line the
    harness did not pair is reported rather than skipped, and a store it did not
    read at all would show up here as a missing key rather than as a smaller
    number nobody noticed.
    """
    recorded = reproduced_run.reproduction.recorded

    assert outcome.unpaired == ()
    for store in BOTH_STORES:
        stored = set(recorded.artifacts[store])
        compared = {row.po_line_id for row in outcome.comparisons if row.store == store}

        assert stored, f"`{store}` holds no row for the run, so this store is unasserted"
        assert compared == stored
    assert len(outcome.comparisons) == QUANTITIES_PER_LINE * sum(
        len(recorded.artifacts[store]) for store in BOTH_STORES
    )


def test_both_quantities_are_compared_on_every_line(outcome: ReproductionOutcome) -> None:
    """FR-022's two quantities: the median **and** the 80th percentile.

    A harness comparing only the median would satisfy every tolerance assertion
    above while leaving the tail — the quantity a reader plans against, and the
    one AD-004's arithmetic is binding at — unchecked.
    """
    per_line: dict[tuple[str, uuid.UUID], set[float]] = {}
    for row in outcome.comparisons:
        per_line.setdefault((row.store, row.po_line_id), set()).add(row.probability)

    assert per_line
    for key, probabilities in per_line.items():
        assert probabilities == {MEDIAN_PROBABILITY, P80_PROBABILITY}, (
            f"{key} was compared at {sorted(probabilities)} rather than at both quantities"
        )


def test_each_compared_value_is_the_delivered_percentile_of_the_stored_draws(
    outcome: ReproductionOutcome, reproduced_run: ReproducedRun
) -> None:
    """The recorded side of every comparison is recomputed from the stored array.

    Without this the deltas would be a claim about two numbers the harness
    produced; with it, one side is checked against the draws the database
    actually holds, under the same nearest-rank convention `/src/api` serves.
    """
    recorded = reproduced_run.reproduction.recorded

    for row in outcome.comparisons:
        stored = recorded.artifacts[row.store][row.po_line_id].draws

        assert row.recorded == nearest_rank_percentile(stored, row.probability)
        assert row.delta_days == pytest.approx(row.reproduced - row.recorded)


def test_the_manifests_provenance_fields_are_exactly_equal(
    outcome: ReproductionOutcome,
) -> None:
    """FR-043's set, compared for **exact** equality rather than within anything.

    Field for field, with the reviewed tuple asserted against the module's own,
    so a field quietly dropped from the compared set fails here instead of making
    the equality easier to satisfy.
    """
    recorded, reproduced = outcome.recorded_provenance, outcome.reproduced_provenance

    assert PROVENANCE_FIELDS == REVIEWED_PROVENANCE_FIELDS
    assert outcome.differing_provenance_fields == ()
    for name in REVIEWED_PROVENANCE_FIELDS:
        assert getattr(recorded, name) == getattr(reproduced, name), (
            f"the re-run's `{name}` is {getattr(reproduced, name)!r} against a recorded "
            f"{getattr(recorded, name)!r}; FR-022 requires exact equality, and two runs on "
            f"different code or different libraries are not two runs of one thing"
        )
    assert recorded.library_versions and reproduced.library_versions


def test_the_verdict_names_both_runs_it_compared(outcome: ReproductionOutcome) -> None:
    """FR-022: a verdict that does not name its two operands resolves to nothing.

    The recorded run by `run_id`, and the re-run by its manifest provenance
    fields — this job writes no run row, so it has no identifier of its own and
    the fields it *would* have recorded are what identify it.
    """
    assert outcome.run_id
    assert outcome.reproduced_provenance.code_commit
    assert outcome.reproduced_provenance.roster_hash


def test_the_basis_condition_is_measured_rather_than_assumed(
    outcome: ReproductionOutcome,
) -> None:
    """AD-004's published basis condition, realized per line.

    The predictive effective sample size is **not** the parameter ESS the gate
    floors at 400: each stored draw carries independent residual and inverse-CDF
    randomness. Asserted as a measurement above the floor rather than as a
    constant, so a harness that reported the draw count instead of measuring one
    would have to produce exactly the draw count on every line.
    """
    floor = REPRODUCTION_PREDICTIVE_ESS_FRACTION_MIN * outcome.draw_count
    realized = {row.po_line_id: row.predictive_ess for row in outcome.comparisons}

    assert outcome.outside_basis == ()
    assert realized
    assert min(realized.values()) >= floor
    assert len({round(value, 6) for value in realized.values()}) > 1, (
        "every line reports the same predictive effective sample size, which is what a "
        "constant substituted for a measurement would look like"
    )


def test_the_reproduction_wrote_no_run_and_left_the_stores_alone(
    reproduced_run: ReproducedRun, emitted_run: EmittedRun
) -> None:
    """The job reads. Its only output is the report FR-040 enumerates.

    Asserted over the report root the job was given: exactly one file, and it is
    the reproduction report for the run that was reproduced. A job that had
    written a second run would also have written a run report beside this one.
    """
    emitted = sorted(reproduced_run.report_root.iterdir())

    assert emitted == [reproduced_run.reproduction.report]
    assert str(emitted_run.run_id) in emitted[0].name
    assert emitted[0].read_text(encoding="utf-8").startswith("# Forecast Reproduction Report")
