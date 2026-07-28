"""T076 — NC-7: a planted coverage threshold must **fail** the absence check.

`test_no_verdict.py` asserts that nothing this epic emits carries a coverage
threshold, a calibration verdict or a pass/fail judgement on forecast quality.
An absence is the easiest claim in the world to satisfy by accident: a parser
that finds no fields, a schema comparison against itself, a column query naming
no table — each of them reports the same green as a genuinely clean artifact.
This file plants the prohibited thing in each of the three places the check
looks and requires the check to find it.

**Three plantings, one per part of DV-021's predicate.**

- Into the **emitted report**, as a field inside a declared section. This is the
  form a verdict would actually take: a bullet among the bullets, in a document
  whose every other field is legitimate.
- Into the **declared schema**, as a widened `SECTION_FIELDS`. This is the route
  by which the first planting becomes legal — a field is undeclared only until
  somebody declares it — and it is why the schema equality guard exists. Both
  halves are asserted together: the widened schema *admits* the planted report,
  and the guard refuses the widened schema.
- Into the **stored columns**, as a `boolean` on `forecast_run` added inside the
  rolled-back transaction. A calibration verdict reaching a column is the
  failure `data-model.md` records as impossible, and "impossible" is worth
  exactly as much as the check that would notice.

**Deliberately not a term search.** Nothing here asserts that the string
"coverage" is absent — the emitted report is *required* to state the registered
coverage band (SC-025, L-3), and a deny-list would fire on the epic's own
mandated content. What each planting violates is structure: a field outside the
closed schema, a schema outside the reviewed one, a column outside the
enumerated set.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from forecast.conftest import EmittedRun
from forecast.test_no_verdict import (
    JUDGEMENT_BEARING_COLUMNS,
    REVIEWED_COLUMNS,
    REVIEWED_SCHEMA,
    report_fields,
    stored_columns,
    undeclared_fields,
)
from model.forecast.paths import run_report_path
from model.forecast.report import SECTION_FIELDS

#: The section the report field is planted into. A declared one, deliberately:
#: planting into an undeclared section would be caught by the section comparison
#: and would say nothing about whether fields are examined at all.
PLANTED_SECTION = "Split and Held-Out Evidence"

#: What a coverage threshold would look like if one were ever published here —
#: rendered exactly like every legitimate field beside it, which is the point.
PLANTED_FIELD = "Coverage threshold"
PLANTED_LINE = (
    f"- **{PLANTED_FIELD}**: 0.80 — the run is judged calibrated at or above this "
    f"held-out coverage."
)

#: The verdict planted into the schema. FR-026 reserves this judgement for the
#: evaluation harness, so a field of this name is the one thing the run report's
#: schema must never be able to declare.
PLANTED_SCHEMA_FIELD = "Calibration verdict"

#: The column planted into `forecast_run`. Added inside `db_session`'s
#: transaction and discarded with it, so nothing survives the test. Module-level
#: SQL, never assembled from values (Ruff S608).
PLANTED_COLUMN = "coverage_band_met"
PLANT_COLUMN_SQL = text("ALTER TABLE forecast_run ADD COLUMN coverage_band_met boolean")


def planted_report(source: Path, destination: Path) -> Path:
    """The emitted report with one coverage-threshold field added to a section.

    Copied and modified rather than written from scratch, so everything except
    the planted line is a real artifact the real job produced: a control built
    from a hand-written document would test the parser against a shape the
    renderer never emits.
    """
    lines = source.read_text(encoding="utf-8").splitlines()
    planted: list[str] = []
    for line in lines:
        planted.append(line)
        if line.startswith("## ") and line.endswith(PLANTED_SECTION):
            planted += ["", PLANTED_LINE]
    destination.write_text("\n".join(planted) + "\n", encoding="utf-8")
    return destination


def widened_schema() -> dict[str, tuple[str, ...]]:
    """`SECTION_FIELDS` with a calibration verdict declared in the same section.

    The schema an implementation would arrive at by "just adding the field" —
    which is the only way the planted report above stops being a violation, and
    therefore the thing the reviewed-schema equality exists to catch.
    """
    widened = {title: tuple(fields) for title, fields in SECTION_FIELDS.items()}
    widened[PLANTED_SECTION] = (*widened[PLANTED_SECTION], PLANTED_FIELD, PLANTED_SCHEMA_FIELD)
    return widened


@pytest.fixture
def emitted_report_path(emitted_run: EmittedRun) -> Path:
    """Where the shipped run's report is, as the job's own path resolution gives it."""
    return run_report_path(emitted_run.run_id, emitted_run.report_root)


# ---------------------------------------------------------------------------
# Planting 1 — a coverage threshold in the emitted report
# ---------------------------------------------------------------------------


def test_the_unplanted_report_passes_the_predicate(emitted_report_path: Path) -> None:
    """The positive control: the checker accepts the artifact the job emitted.

    Without it every planting below is satisfied by a predicate that rejects
    everything, which discloses as little as one that inspects nothing.
    """
    assert undeclared_fields(report_fields(emitted_report_path), SECTION_FIELDS) == ()


def test_a_planted_coverage_threshold_field_fails_the_closed_schema_predicate(
    emitted_report_path: Path, tmp_path: Path
) -> None:
    """NC-7's first direction: the field is found, and found by name and section.

    The planted line is rendered exactly as its neighbours are, so a parser that
    read only the first bullet of a section, or only sections it recognised,
    would miss it and report the same clean result as the control above.
    """
    planted = planted_report(emitted_report_path, tmp_path / "planted-run-report.md")
    found = undeclared_fields(report_fields(planted), SECTION_FIELDS)

    assert found == ((PLANTED_SECTION, PLANTED_FIELD),)


def test_a_planted_field_in_an_undeclared_section_is_also_found(
    emitted_report_path: Path, tmp_path: Path
) -> None:
    """The second route in: a whole section the schema never declared.

    A verdict does not have to arrive inside a legitimate section — appending a
    `## Calibration` section is simpler and would escape any check that walked
    the declared titles and ignored what else the document contained.
    """
    target = tmp_path / "planted-section-report.md"
    original = emitted_report_path.read_text(encoding="utf-8")
    target.write_text(
        f"{original}\n## 10. Calibration\n\n- **{PLANTED_SCHEMA_FIELD}**: calibrated.\n",
        encoding="utf-8",
    )
    parsed = report_fields(target)

    assert ("Calibration", PLANTED_SCHEMA_FIELD) in undeclared_fields(parsed, SECTION_FIELDS)
    assert set(parsed) != set(SECTION_FIELDS)


# ---------------------------------------------------------------------------
# Planting 2 — the widened schema that would make planting 1 legal
# ---------------------------------------------------------------------------


def test_a_widened_schema_admits_the_planted_report_and_is_itself_refused(
    emitted_report_path: Path, tmp_path: Path
) -> None:
    """Both halves, because either alone is half an argument.

    Declaring the field makes the planted report conform — that is what "closed
    schema" means, and it is why the schema cannot be the only line of defence.
    The reviewed-schema equality is the second line, and it refuses the widening
    that made the first one pass.
    """
    planted = planted_report(emitted_report_path, tmp_path / "planted-run-report.md")
    widened = widened_schema()

    assert undeclared_fields(report_fields(planted), widened) == ()
    assert widened != REVIEWED_SCHEMA
    assert PLANTED_SCHEMA_FIELD not in REVIEWED_SCHEMA[PLANTED_SECTION]


# ---------------------------------------------------------------------------
# Planting 3 — a calibration verdict as a stored column
# ---------------------------------------------------------------------------


def test_a_planted_boolean_column_fails_the_reviewed_column_set(db_session: Session) -> None:
    """NC-7 over DV-021's "row" clause, planted and rolled back with the session.

    Two ways it must be caught, because a later column could evade either one:
    it is not in the enumerated set for its table, and it is a `boolean` the
    judgement-bearing mapping does not account for. The transaction is discarded
    in teardown, so the column never exists outside this test.
    """
    db_session.execute(PLANT_COLUMN_SQL)
    observed = stored_columns(db_session)
    boolean_columns = {
        (table, column)
        for table, columns in observed.items()
        for column, data_type in columns.items()
        if data_type == "boolean"
    }

    assert PLANTED_COLUMN in observed["forecast_run"]
    assert tuple(observed["forecast_run"]) != REVIEWED_COLUMNS["forecast_run"]
    assert ("forecast_run", PLANTED_COLUMN) in boolean_columns
    assert not boolean_columns <= set(JUDGEMENT_BEARING_COLUMNS)


def test_the_unplanted_stores_carry_only_reviewed_columns(db_session: Session) -> None:
    """The positive control for planting 3, in the same session shape.

    Asserted here as well as in `test_no_verdict.py` on purpose: if this failed
    while the planted case passed, the planted case would be evidence about the
    database's baseline rather than about the column somebody added.
    """
    observed = stored_columns(db_session)

    assert PLANTED_COLUMN not in observed["forecast_run"]
    for table, reviewed in REVIEWED_COLUMNS.items():
        assert tuple(observed[table]) == reviewed
