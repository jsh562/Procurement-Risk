"""T084 — DV-011 / SC-016 / G-7: the diagnostics store, complete and named.

Nothing in the schema can say this. `uq_forecast_diagnostic__run_metric_
parameter` gives *at most one* row per metric per parameter, and every check on
the table is a statement about one row; completeness is cross-row, a `CHECK`
admits no sibling, and a deferred one is impossible. **G-7** records the gap and
this file is what covers it — nothing otherwise stops a run recording R-hat for
a parameter and omitting its ESS, or dropping the E-BFMI row entirely.

The omission is the interesting failure rather than a hypothetical one: a
parameter whose ESS row is missing is a parameter the gate did not judge, and
because a refused run stores nothing at all (G-8), the *presence* of a run's
rows is the only evidence anywhere that its gate ran over the set it claims.

SC-016's clauses ride here too, because they are claims about the same stored
rows: every monitored diagnostic sits beside the threshold it was judged against
**and that threshold's direction**, the monitored parameter set is *named* by
the enumerated `parameter_name` values (FR-016, AD-006), treedepth is recorded
as non-blocking, and every row joins to exactly one run.
"""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.orm import Session

from forecast.conftest import EmittedRun, FitInput
from model.forecast.config import monitored_parameter_names
from model.forecast.diagnostics import PARAMETER_METRICS, RUN_METRICS, threshold_for

#: Module-level SQL, never assembled from values (Ruff S608). Scoped to one
#: `run_id` throughout: E003's delivered fixtures live in `forecast_run`, and a
#: whole-table assertion would be a claim about rows this epic did not write.
STORED_DIAGNOSTICS_SQL = text(
    """
    SELECT diagnostic_id, diagnostic_scope, parameter_name, metric, observed_value,
           threshold_value, threshold_direction, is_blocking, passed
    FROM forecast_diagnostic
    WHERE run_id = :run_id
    """
)
ORPHANED_ROWS_SQL = text(
    """
    SELECT count(*) FROM forecast_diagnostic d
    WHERE d.run_id = :run_id
      AND NOT EXISTS (SELECT 1 FROM forecast_run r WHERE r.run_id = d.run_id)
    """
)


def stored_diagnostics(db_session: Session, run_id) -> list[dict]:
    """One run's diagnostic rows, as mappings, in whatever order they come back.

    Order is deliberately not imposed: DV-011 is a claim about a *set*, and a
    query that sorted would let a reader mistake the sort for part of the claim.
    """
    return [
        dict(row)
        for row in db_session.execute(STORED_DIAGNOSTICS_SQL, {"run_id": run_id})
        .mappings()
        .all()
    ]


def parameter_coverage(rows: list[dict]) -> dict[str, set[str]]:
    """Which parameter-scope metrics each named parameter actually carries.

    The predicate DV-011 is stated over, extracted so the negative control in
    `test_completeness_controls.py` runs *this* function rather than a second
    one written beside it — a control that re-authors its predicate proves the
    copy is falsifiable and says nothing about the original.
    """
    coverage: dict[str, set[str]] = {}
    for row in rows:
        if row["parameter_name"] is not None:
            coverage.setdefault(row["parameter_name"], set()).add(row["metric"])
    return coverage


def test_every_monitored_parameter_carries_all_three_parameter_metrics(
    db_session: Session, emitted_run: EmittedRun, fit_input: FitInput
) -> None:
    """DV-011's first half: three rows per parameter, and no partial coverage.

    The expected set is enumerated by `monitored_parameter_names` from the run's
    own vendor and material-category index, which is how AD-006 keeps the
    monitored set from being a hard-coded dataset fact — a thirteenth vendor is
    not a code change, and it is not a change to this test either.
    """
    rows = stored_diagnostics(db_session, emitted_run.run_id)
    expected = set(
        monitored_parameter_names(fit_input.vendor_ids, fit_input.material_categories)
    )
    coverage = parameter_coverage(rows)

    assert expected, "the monitored set is empty, so this test would pass vacuously"
    assert set(coverage) == expected, (
        f"{sorted(expected - set(coverage))} carry no diagnostic row and "
        f"{sorted(set(coverage) - expected)} are recorded for no monitored parameter; "
        f"FR-016 requires the monitored set to be named, and the enumerated "
        f"`parameter_name` values are what name it"
    )
    partial = {
        name: sorted(metrics)
        for name, metrics in coverage.items()
        if metrics != set(PARAMETER_METRICS)
    }

    assert not partial, (
        f"{len(partial)} parameter(s) are partially covered — first "
        f"{sorted(partial)[0]} carries {partial[sorted(partial)[0]]} rather than "
        f"{sorted(PARAMETER_METRICS)}. A parameter with an R-hat and no ESS is a "
        f"parameter the gate did not judge, and no `CHECK` can see it (G-7)"
    )


def test_exactly_three_run_scope_rows_exist(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """DV-011's second half: the three run-level metrics, each exactly once.

    Exactly three and exactly *these* three. `uq_forecast_diagnostic__run_metric_
    parameter` is `NULLS NOT DISTINCT` precisely so a run cannot record its
    divergence count twice with two different values; what it cannot say is that
    the E-BFMI row is there at all.
    """
    rows = stored_diagnostics(db_session, emitted_run.run_id)
    run_scoped = sorted(row["metric"] for row in rows if row["diagnostic_scope"] == "run")

    assert run_scoped == sorted(RUN_METRICS), (
        f"the run-scope rows are {run_scoped} rather than {sorted(RUN_METRICS)}; a "
        f"missing E-BFMI row is a blocking metric nobody recorded a verdict for"
    )
    assert all(row["parameter_name"] is None for row in rows if row["diagnostic_scope"] == "run")


def test_the_stored_row_count_is_the_set_the_run_claims_to_have_monitored(
    db_session: Session, emitted_run: EmittedRun, fit_input: FitInput
) -> None:
    """Three per monitored parameter plus three, as one arithmetic statement.

    A count beside the two set assertions above, because they quantify over
    *distinct* values and a duplicated row would satisfy both while making the
    store hold a measurement twice. The unique key forbids it; this is what
    shows the key is doing that job.
    """
    monitored = monitored_parameter_names(
        fit_input.vendor_ids, fit_input.material_categories
    )
    rows = stored_diagnostics(db_session, emitted_run.run_id)

    assert len(rows) == len(monitored) * len(PARAMETER_METRICS) + len(RUN_METRICS)
    assert len({row["diagnostic_id"] for row in rows}) == len(rows)


def test_every_row_records_the_threshold_and_the_direction_it_was_judged_against(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """SC-016 over the **stored** rows, not over a log line or the report.

    "Records" alone is satisfied by output that does not survive the run, which
    is why the criterion names the durable store. The threshold and the
    direction are compared against `config.py`'s published tuple, so a row
    recording a bar nobody published — or a floor recorded as a ceiling — fails
    here rather than being read as a verdict against an unstated number.
    """
    rows = stored_diagnostics(db_session, emitted_run.run_id)

    assert rows, "the emitted run stored no diagnostic row at all"
    for row in rows:
        published = threshold_for(row["metric"])

        assert row["threshold_value"] == published.threshold_value, (
            f"{row['metric']} was judged against {row['threshold_value']} rather than the "
            f"published {published.threshold_value}"
        )
        assert row["threshold_direction"] == published.threshold_direction
        assert row["diagnostic_scope"] == published.diagnostic_scope
        assert row["is_blocking"] == published.is_blocking


def test_every_row_is_joinable_to_exactly_one_run(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """SC-016's binding clause: `fk_forecast_diagnostic__run` and the natural key.

    Evidence belongs to its run. The anti-join is redundant against the foreign
    key by design — as the criterion's own wording is redundant against the
    coverage map — and it is what shows the key reaches these rows rather than
    being declared on a table nothing writes to.
    """
    rows = stored_diagnostics(db_session, emitted_run.run_id)
    orphaned = db_session.execute(
        ORPHANED_ROWS_SQL, {"run_id": emitted_run.run_id}
    ).scalar_one()
    natural_keys = [(row["metric"], row["parameter_name"]) for row in rows]

    assert orphaned == 0
    assert len(set(natural_keys)) == len(natural_keys), (
        "two rows share a metric and parameter under one run; "
        "`uq_forecast_diagnostic__run_metric_parameter` is `NULLS NOT DISTINCT` so that "
        "a run cannot record its divergence count twice with two different values"
    )
