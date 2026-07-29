"""T110 — NC-20: a unit short of a part must **fail** the reportable-unit check.

`test_reportable_unit.py` asserts that every measure with a decision criterion
is reported as FR-038's four-part unit and that a measure with no criterion
carries no verdict. Both halves are absences, and an absence is the easiest
claim in the world to satisfy by accident — a parser that finds no fields, a
section lookup that misses, a marker set that matches nothing. This file plants
the prohibited shapes and requires the predicates to find each one.

**Three plantings, and the first two are NC-20's own pair.**

- **A value with no criterion**: the ablation's `Decision criterion` bullet is
  removed, leaving a measure, a realized value and a verdict. That is the shape
  FR-033 records going wrong once already — "a delta and an interval with no bar
  next to them" — and `missing_parts` must name the part rather than the field.
- **A verdict against no criterion**: the wall clock is given one. FR-038 names
  it as a measure judged against nothing, so a verdict on it is a gate FR-026
  reserves for the evaluation harness, arriving without anyone publishing a bar.
- **A criterion with its direction stripped**: the third shape, because it is the
  one this epic's history says goes missing first — the refusal message kept the
  threshold and dropped its direction. The unit still renders four parts, which
  is exactly why the direction is checked separately from them.

**Every planting is made into a copy of the artifact the real job emitted**, so
everything except the mutated line is a document the renderer actually produced.
A control built from a hand-written report would test the parser against a shape
nothing emits.

**The stored half is planted at the store**, not at the parser: `forecast_
diagnostic`'s parts are columns, so the shape that has to be refused is a row
missing one, and the refusal is the database's.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from forecast.conftest import EmittedRun
from forecast.test_reportable_unit import (
    RUN_REPORT_UNITS,
    ReportableUnit,
    missing_parts,
    rendered_fields,
    stated_directions,
    verdict_markers,
)
from model.forecast.diagnostics import diagnostic_row
from model.forecast.paths import run_report_path
from model.forecast.write import DIAGNOSTIC_INSERT

#: The unit every report planting is made against: the censoring ablation, whose
#: four parts are four separate bullets. Chosen because it is the one unit where
#: removing a part leaves the other three visibly intact — the shape a reviewer
#: would have to notice rather than one the document falls apart without.
ABLATION = next(unit for unit in RUN_REPORT_UNITS if unit.section == "Censoring Ablation")

#: A realized R-hat that **passes** its published bar. Deliberately a passing
#: value: `ck_forecast_diagnostic__blocking_rows_passed` refuses a stored
#: blocking breach — a run that breached is refused and writes nothing — so a
#: breaching value would have the store reject the row for a reason unrelated to
#: the part this planting removes, and the control would evidence nothing.
PASSING_R_HAT = 1.0

#: FR-038's named criterion-free measure, and the section it lives in.
WALL_CLOCK_SECTION = "Sampling Shape"
WALL_CLOCK_FIELD = "Wall clock"

#: The verdict planted onto it. Rendered exactly as a legitimate verdict is,
#: which is the point: a gate arrives looking like every other field.
PLANTED_WALL_CLOCK_VERDICT = " **met** — comfortably inside the fitting budget."

#: The criterion rewritten with its number kept and its direction removed. Still
#: a decision criterion by every structural test — a labelled field carrying a
#: bar — and useless to a reader who does not already know which side passes.
DIRECTIONLESS_CRITERION = (
    "- **Decision criterion**: a derived floor of 0.0500. Interval [0.0100, 0.6700] at 0.94."
)


def _copy(source: Path, destination: Path) -> list[str]:
    """The emitted report's lines, read once for a planting to rewrite."""
    del destination  # the caller names the target; this only reads
    return source.read_text(encoding="utf-8").splitlines()


def _write(destination: Path, lines: list[str]) -> Path:
    destination.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return destination


def without_the_criterion(source: Path, destination: Path) -> Path:
    """The report with the ablation's `Decision criterion` bullet deleted.

    One line removed and nothing else touched, so the section still renders the
    comparator, the realized delta with its interval, the derivation and the
    verdict — a document that reads as complete unless the unit is checked.
    """
    kept = [
        line
        for line in _copy(source, destination)
        if not line.startswith(f"- **{ABLATION.criterion}**")
    ]
    return _write(destination, kept)


def without_the_direction(source: Path, destination: Path) -> Path:
    """The report with the ablation's criterion kept and its direction removed."""
    rewritten = [
        DIRECTIONLESS_CRITERION if line.startswith(f"- **{ABLATION.criterion}**") else line
        for line in _copy(source, destination)
    ]
    return _write(destination, rewritten)


def with_a_verdict_on_the_wall_clock(source: Path, destination: Path) -> Path:
    """The report with a verdict attached to a measure that has no criterion."""
    rewritten = [
        line + PLANTED_WALL_CLOCK_VERDICT if line.startswith(f"- **{WALL_CLOCK_FIELD}**") else line
        for line in _copy(source, destination)
    ]
    return _write(destination, rewritten)


@pytest.fixture(scope="module")
def emitted_report(emitted_run: EmittedRun) -> Path:
    """Where the shipped run's report is, as the job's own path resolution gives it."""
    return run_report_path(emitted_run.run_id, emitted_run.report_root)


# ---------------------------------------------------------------------------
# The positive controls
# ---------------------------------------------------------------------------


def test_the_unplanted_report_passes_every_predicate(emitted_report: Path) -> None:
    """Without this, each planting below is satisfied by a predicate that
    rejects everything — which discloses as little as one that inspects nothing.
    """
    rendered = rendered_fields(emitted_report)

    for unit in RUN_REPORT_UNITS:
        assert missing_parts(rendered, unit) == ()
        assert stated_directions(rendered, unit)
    for line in rendered[WALL_CLOCK_SECTION][WALL_CLOCK_FIELD]:
        assert not verdict_markers(line)


# ---------------------------------------------------------------------------
# Planting 1 — a value with no criterion
# ---------------------------------------------------------------------------


def test_a_measure_whose_criterion_was_removed_fails_the_unit_check(
    emitted_report: Path, tmp_path: Path
) -> None:
    """NC-20's first entry: three parts present, and that is a failure.

    The part is named rather than counted, because "one part missing" sends a
    reader back to the document to work out which — and the whole of FR-038 is
    that a unit short of a part is not a shorter unit.
    """
    planted = without_the_criterion(emitted_report, tmp_path / "no-criterion.md")
    rendered = rendered_fields(planted)

    assert missing_parts(rendered, ABLATION) == ("criterion",)
    assert rendered[ABLATION.section][ABLATION.value], "the value survived, as the plant intends"
    assert rendered[ABLATION.section][ABLATION.verdict], "the verdict survived too"
    assert stated_directions(rendered, ABLATION) == ()


def test_the_removal_is_invisible_to_a_reader_who_only_counts_fields(
    emitted_report: Path, tmp_path: Path
) -> None:
    """Why the check is per-unit rather than per-section.

    The section still renders seven of its eight declared fields and every one
    of them is legitimate, so nothing about its shape says a bar went missing.
    Only the unit — this measure, this criterion — reports it.
    """
    planted = rendered_fields(without_the_criterion(emitted_report, tmp_path / "counted.md"))
    original = rendered_fields(emitted_report)

    assert len(planted[ABLATION.section]) == len(original[ABLATION.section]) - 1
    assert set(planted[ABLATION.section]) < set(original[ABLATION.section])


# ---------------------------------------------------------------------------
# Planting 2 — a verdict against no criterion
# ---------------------------------------------------------------------------


def test_a_verdict_planted_on_the_wall_clock_is_found(emitted_report: Path, tmp_path: Path) -> None:
    """NC-20's second entry: FR-038's own named criterion-free measure, judged.

    The wall clock is recorded and judged against nothing. A verdict on it is a
    gate nobody published, and the field even states its own absence of one — so
    the planted document contradicts itself on a single line, which is what
    makes it the right control rather than a contrived one.
    """
    planted = with_a_verdict_on_the_wall_clock(emitted_report, tmp_path / "judged-clock.md")
    lines = rendered_fields(planted)[WALL_CLOCK_SECTION][WALL_CLOCK_FIELD]

    assert lines, "the wall clock stopped rendering, so the planting missed"
    assert all(verdict_markers(line) == ("met",) for line in lines)
    assert all("no criterion and therefore no verdict" in line for line in lines), (
        "the plant is appended to the real line, so the document now records both that "
        "there is no verdict and a verdict"
    )


def test_the_planting_leaves_every_other_criterion_free_measure_clean(
    emitted_report: Path, tmp_path: Path
) -> None:
    """The predicate discriminates rather than firing on the whole section.

    `Draws per line` and `Tuning draws per chain` sit in the same section as the
    planted wall clock and are equally criterion-free; a check that reported the
    section rather than the field would name all three and evidence none.
    """
    planted = rendered_fields(
        with_a_verdict_on_the_wall_clock(emitted_report, tmp_path / "one-field.md")
    )

    for field in ("Draws per line", "Tuning draws per chain"):
        for line in planted[WALL_CLOCK_SECTION][field]:
            assert not verdict_markers(line)


# ---------------------------------------------------------------------------
# Planting 3 — a criterion with its direction stripped
# ---------------------------------------------------------------------------


def test_a_criterion_without_its_direction_fails_while_all_four_parts_render(
    emitted_report: Path, tmp_path: Path
) -> None:
    """The part that goes missing first, and why it is checked on its own.

    Every structural test passes on the planted document: the field is declared,
    it is rendered, it carries a number, and a verdict sits beside it. What it
    does not carry is which side of 0.0500 passes — and the verdict two lines
    below then reads as an assertion rather than as a comparison a reader can
    perform.
    """
    planted = rendered_fields(without_the_direction(emitted_report, tmp_path / "no-direction.md"))

    assert missing_parts(planted, ABLATION) == ()
    assert stated_directions(planted, ABLATION) == ()
    assert any("0.0500" in line for line in planted[ABLATION.section][ABLATION.criterion])


@pytest.mark.parametrize("unit", RUN_REPORT_UNITS, ids=lambda unit: unit.measure)
def test_the_direction_check_is_not_satisfied_by_the_rest_of_the_document(
    unit: ReportableUnit, emitted_report: Path, tmp_path: Path
) -> None:
    """The phrase has to be in *this* unit's criterion, not somewhere in the file.

    The ablation's direction is removed and every other unit is asserted still
    to state its own — so a predicate that had searched the whole report, or the
    whole section, would report the planted document as clean.
    """
    planted = rendered_fields(without_the_direction(emitted_report, tmp_path / "scoped.md"))

    if unit.section == ABLATION.section:
        assert stated_directions(planted, unit) == ()
    else:
        assert stated_directions(planted, unit), (
            f"`## {unit.section}` lost its direction to a planting made in `## {ABLATION.section}`"
        )


# ---------------------------------------------------------------------------
# Planting 4 — the stored half, refused by the store
# ---------------------------------------------------------------------------


def test_a_stored_row_missing_its_parameter_is_refused_by_the_store(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """The stored unit's parts are columns, so the planting is a row short of one.

    `r_hat` is keyed by parameter — a bare metric name does not say which
    parameter breached — and the store refuses the row rather than accepting a
    measure nobody can attribute. Issued inside the tier's rolled-back
    transaction, so the row never exists outside this test.
    """
    parameters = diagnostic_row("r_hat", PASSING_R_HAT, "mu_sojourn[a__b]").row_parameters(
        emitted_run.run_id
    )
    parameters["parameter_name"] = None

    with pytest.raises(IntegrityError):
        db_session.execute(DIAGNOSTIC_INSERT, parameters)
    db_session.rollback()


def test_the_same_row_with_its_parameter_is_accepted(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """The positive control for the planting above, in the same session shape.

    Without it the refusal is evidence about the insert statement rather than
    about the missing part — a row the store rejects for some other reason
    fails the test above just as convincingly.
    """
    parameters = diagnostic_row("r_hat", PASSING_R_HAT, "mu_sojourn[a__b]").row_parameters(
        emitted_run.run_id
    )

    db_session.execute(DIAGNOSTIC_INSERT, parameters)
    db_session.rollback()
