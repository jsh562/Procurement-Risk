"""T090 — NC-16: three planted completeness cases, for DV-006, DV-011 and DV-009.

Each of the three rules covers a gap no constraint can close, and all three fail
the same way: something is **absent**. A `CHECK` cannot see a sibling row or a
second table, and a deferred one is impossible, so the split can lose a line
(G-6), a monitored parameter can lose one of its three metrics (G-7), and the
shrinkage object can lose a vendor (G-9), and every declarative rule in the
schema stays satisfied.

That is the whole reason a control is needed here rather than desirable. A
completeness assertion over a complete database passes whether or not it is
looking at anything: the emitted run *is* complete, so "the predicate returned
true" carries no information about what it would do with an incomplete one. Each
test below plants exactly one absence and runs **the delivered predicate**,
imported from the module that owns it, so what is demonstrated is that the
positive test would have failed.

Every plant runs inside `begin_nested()` on this tier's rolled-back session, so
the doctored state exists for one assertion and is discarded. The emitted run is
committed and shared by the whole tier; a plant that escaped its savepoint would
take every other assertion in the tier down with it.
"""

from __future__ import annotations

import dataclasses
import uuid

from sqlalchemy import text
from sqlalchemy.orm import Session

from forecast.conftest import EmittedRun, FitInput, StoredRun
from forecast.test_diagnostics_completeness import parameter_coverage, stored_diagnostics
from forecast.test_shrinkage_membership import ROSTER_VENDORS_SQL, STORED_WEIGHTS_SQL
from forecast.test_split_completeness import COVERAGE_SQL, UNASSIGNED_LINES_SQL
from model.forecast.config import monitored_parameter_names
from model.forecast.diagnostics import PARAMETER_METRICS
from model.forecast.write import insert_artifact_set

#: Module-level SQL, never assembled from values (Ruff S608). Each deletes
#: exactly one row, because the interesting failure is the single quiet
#: omission rather than a wholesale absence a reader would notice.
DROP_ONE_ASSIGNMENT_SQL = text(
    """
    DELETE FROM forecast_split_assignment
    WHERE run_id = :run_id
      AND canonical_ordinal = (
        SELECT max(canonical_ordinal) FROM forecast_split_assignment WHERE run_id = :run_id
      )
    """
)
DROP_ONE_DIAGNOSTIC_SQL = text(
    """
    DELETE FROM forecast_diagnostic
    WHERE diagnostic_id = (
        SELECT diagnostic_id FROM forecast_diagnostic
        WHERE run_id = :run_id AND metric = :metric AND parameter_name = :parameter_name
    )
    """
)

#: The metric the DV-011 plant removes. `ess_tail` rather than `r_hat` because
#: it is the one a reader is least likely to miss by eye: a parameter that still
#: has an R-hat and a bulk ESS looks monitored.
OMITTED_METRIC = "ess_tail"


def test_a_split_row_removed_fails_dv_006(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """NC-16's first case: one line loses its assignment and the count notices.

    `pk_forecast_split_assignment` gives *at most once per run* and
    `uq_forecast_split_assignment__run_ordinal` gives at most one line per
    position; neither can see `purchase_order_line`, so a line with no
    assignment is a legal database state. It is also a line the fit neither
    trained on nor held out, and one whose absence makes `split_assignment_hash`
    a digest of a sequence with a hole in it.
    """
    parameters = {"run_id": emitted_run.run_id}
    with db_session.begin_nested() as plant:
        removed = db_session.execute(DROP_ONE_ASSIGNMENT_SQL, parameters).rowcount

        assert removed == 1, "the plant removed no assignment, so it demonstrates nothing"

        counts = db_session.execute(COVERAGE_SQL, parameters).mappings().one()
        unassigned = list(db_session.execute(UNASSIGNED_LINES_SQL, parameters).scalars())

        assert counts["assigned"] != counts["lines_present"], (
            "the coverage count still agrees after a row was deleted, so DV-006's "
            "predicate is not comparing against `purchase_order_line`"
        )
        assert len(unassigned) == 1
        plant.rollback()


def test_an_ess_tail_row_omitted_fails_dv_011(
    db_session: Session, emitted_run: EmittedRun, fit_input: FitInput
) -> None:
    """NC-16's second case: a monitored parameter keeps two metrics of three.

    The parameter is still named, still carries an R-hat, and still carries a
    bulk ESS — so a reader scanning the store sees a monitored parameter and a
    row count that is very slightly wrong. What is actually gone is a blocking
    metric nobody recorded a verdict for, on a parameter the gate claims to have
    judged (G-7).
    """
    monitored = monitored_parameter_names(
        fit_input.vendor_ids, fit_input.material_categories
    )
    victim = monitored[0]
    with db_session.begin_nested() as plant:
        removed = db_session.execute(
            DROP_ONE_DIAGNOSTIC_SQL,
            {"run_id": emitted_run.run_id, "metric": OMITTED_METRIC, "parameter_name": victim},
        ).rowcount

        assert removed == 1, f"no {OMITTED_METRIC} row existed for {victim} to remove"

        coverage = parameter_coverage(stored_diagnostics(db_session, emitted_run.run_id))

        assert victim in coverage, "the plant removed the parameter entirely, not one metric"
        assert coverage[victim] != set(PARAMETER_METRICS), (
            f"{victim} still reads as fully covered after its {OMITTED_METRIC} row was "
            f"deleted, so DV-011's predicate is not checking coverage per parameter"
        )
        assert set(coverage) == set(monitored), (
            "the parameter set itself changed, so this control would fail for the wrong "
            "reason — the plant is a partial covering, not a missing parameter"
        )
        plant.rollback()


def test_a_vendor_absent_from_the_shrinkage_object_fails_dv_009(
    db_session: Session, fit_input: FitInput, stored_run: StoredRun
) -> None:
    """NC-16's third case: eleven weights where the roster holds twelve.

    Planted through the **real writer** rather than by doctoring a column, so
    the row is one the database accepts: `fn_vendor_shrinkage_wellformed`
    validates shape and cannot validate membership — a `CHECK` admits no
    subquery against `purchase_order_line`, and E007 may not hard-code E001's
    twelve identifiers into DDL. That is G-9, and this is what covers it.
    """
    weights = dict(stored_run.manifest.vendor_shrinkage)
    dropped = sorted(weights)[0]
    del weights[dropped]
    manifest = dataclasses.replace(
        stored_run.manifest, run_id=uuid.uuid4(), vendor_shrinkage=weights
    )
    with db_session.begin_nested() as plant:
        insert_artifact_set(
            db_session, manifest, stored_run.assignments, stored_run.line_posteriors
        )
        stored = db_session.execute(
            STORED_WEIGHTS_SQL, {"run_id": manifest.run_id}
        ).scalar_one()
        roster = set(db_session.execute(ROSTER_VENDORS_SQL).scalars())

        assert dropped in roster, "the dropped vendor is not in the roster, so nothing is missing"
        assert set(stored) != roster, (
            f"the stored object still names every roster vendor after {dropped} was "
            f"removed, so DV-009's predicate is comparing the object against itself"
        )
        assert roster - set(stored) == {dropped}
        assert dropped in fit_input.training_line_counts, (
            "the dropped vendor carries no training count either, so its absence from the "
            "weights would be explicable rather than a defect"
        )
        plant.rollback()


def test_the_three_predicates_pass_on_the_undoctored_run(
    db_session: Session, emitted_run: EmittedRun, fit_input: FitInput
) -> None:
    """The setup assertion the three plants above depend on.

    Each control asserts that a predicate *fails* on a doctored state. That says
    nothing unless the same predicate passes on the real one — otherwise the
    controls would be demonstrating a predicate that rejects everything.
    """
    parameters = {"run_id": emitted_run.run_id}
    counts = db_session.execute(COVERAGE_SQL, parameters).mappings().one()
    coverage = parameter_coverage(stored_diagnostics(db_session, emitted_run.run_id))
    monitored = monitored_parameter_names(
        fit_input.vendor_ids, fit_input.material_categories
    )

    assert counts["assigned"] == counts["lines_present"]
    assert set(coverage) == set(monitored)
    assert all(metrics == set(PARAMETER_METRICS) for metrics in coverage.values())
