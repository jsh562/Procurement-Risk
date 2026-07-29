"""T109, T119 — DV-039 / SC-034: FR-038's reportable unit, over reports and rows.

Every measure this epic reports that has a decision criterion is one unit — the
**measure**, its **realized value**, the **criterion including its direction**,
and an **explicit verdict** — and a measure reported without a criterion carries
no verdict. SC-034 records where the failure lives: "the stored half is already
columns; the emitted half is where the parts have gone missing one at a time."

So the units are enumerated below rather than searched for, each with what it
judges and with the phrase its criterion states the direction by. A phrase per
unit and never a keyword sweep: `Maximum observed duration` is a quantity's name
and `Vendor-claim observation floor` is a bar, and a scan for "maximum" or
"floor" cannot tell them apart. `test_reportable_unit_controls.py` plants a unit
short of each part and requires the predicate to find it.

**The stored half is asserted over the rows, not over the writer.** `forecast_
diagnostic` carries the four parts as columns — `metric` with `parameter_name`,
`observed_value`, `threshold_value` with `threshold_direction`, and `passed` —
and the direction's prose is taken from `diagnostics.direction_prose`, so the
column and the sentence the refusal report renders cannot drift apart.

**T119 — the P1 side.** SC-034 is tagged `[US1]`. The run report and both
refusal reports are emitted by a P1 cut and their units are asserted from
P1 fixtures alone; only the reproduction report's two units need Phase 7, and
they are kept in their own section below so a P1-only cut still evidences the
criterion rather than skipping it entirely (A-016).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from forecast.conftest import EmittedRun, RefusedInvocation, ReproducedRun
from forecast.test_no_verdict import report_fields
from model.forecast.diagnostics import direction_prose
from model.forecast.paths import (
    REFUSAL_REPORT_PREFIX,
    REPRODUCTION_REPORT_PREFIX,
    RUN_REPORT_PREFIX,
    run_report_path,
)

# ---------------------------------------------------------------------------
# The vocabulary: what a direction is, and what a verdict looks like
# ---------------------------------------------------------------------------

#: Every phrase this epic states a criterion's direction by, and what each one
#: tells a reader. **This is the review DV-039 rests on**: a criterion is
#: reported "with its direction" exactly when its rendered line carries one of
#: these, and each unit below declares which. A phrase added here without a unit
#: needing it fails `test_the_direction_vocabulary_is_used_rather_than_carried`.
DIRECTIONS: dict[str, str] = {
    "at or above": "a floor — a realized value at or above it is the passing side",
    "at or below": "a ceiling — a realized value at or below it is the passing side",
    "published minimum": "a floor on a precondition — fewer than the minimum refuses",
    "observation floor": "a floor on a count — a vendor at or above it is claimable",
    "extends past": "the realized horizon lies beyond the criterion",
    "does not reach": "the realized horizon lies short of the criterion",
    "digest equal": "an equality — neither side of it is slack",
}

#: Words this epic renders a verdict as. Used only where FR-038 says there must
#: be **none** — the wall clock and the realized sampling shape — so the set is
#: read as "anything that would read as a judgement", and a false positive here
#: is a failing test rather than a silent pass. Matched on word boundaries: a
#: bare `met` must not fire on `measurement`.
VERDICT_MARKERS: tuple[str, ...] = (
    "met",
    "unmet",
    "missed",
    "breached",
    "supported",
    "agrees",
    "disagrees",
    "failed",
    "equal",
    "passes",
    "conforms",
    # Added when the draw-digest claim lost its failing disposition. The claim now
    # resolves to `equal` or to a scope limit and never to a failure, so "scope
    # limit" is a verdict in the corrected vocabulary rather than an absence of one
    # — and the word-boundary match above means "equality" inside the surrounding
    # prose does not stand in for it. Found by running this suite on Linux, where
    # the claim degrades; on Windows it resolves to `equal` and this row was never
    # exercised.
    "scope limit",
)


@dataclass(frozen=True, slots=True)
class ReportableUnit:
    """One measure with a criterion, and the four fields that carry its parts.

    Several parts share a field where the report renders them on one line — the
    chain count against its published minimum is a precondition and a verdict in
    eight words, and splitting it across four bullets would be a schema serving
    this file rather than a reader. What the parts may never do is go missing,
    which is what `missing_parts` checks.
    """

    section: str
    measure: str
    value: str
    criterion: str
    directions: tuple[str, ...]
    verdict: str
    judges: str

    @property
    def parts(self) -> dict[str, str]:
        """The four parts of FR-038's unit, keyed by name for the failure message."""
        return {
            "measure": self.measure,
            "realized value": self.value,
            "criterion": self.criterion,
            "verdict": self.verdict,
        }


# ---------------------------------------------------------------------------
# The run report's units — P1 (T119)
# ---------------------------------------------------------------------------

RUN_REPORT_UNITS: tuple[ReportableUnit, ...] = (
    ReportableUnit(
        section="Sampling Shape",
        measure="Chains",
        value="Chains",
        criterion="Chains",
        directions=("published minimum",),
        verdict="Chains",
        judges=(
            "the realized chain count against the published sampler minimum — a "
            "precondition cleared before sampling, never a judgement on the forecast"
        ),
    ),
    ReportableUnit(
        section="Split and Held-Out Evidence",
        measure="Realized held-out uncensored event count",
        value="Realized held-out uncensored event count",
        criterion="Realized held-out uncensored event count",
        directions=("at or above",),
        verdict="Realized held-out uncensored event count",
        judges=(
            "the realized gradeable-event count against the event assumption `specs/prd.md` "
            "derives its registered coverage band from (FR-028, SC-025)"
        ),
    ),
    ReportableUnit(
        section="Censoring Ablation",
        measure="Realized delta",
        value="Realized delta",
        criterion="Decision criterion",
        directions=("at or above",),
        verdict="Verdict",
        judges=(
            "the censoring term's realized effect against a floor derived from the training "
            "split alone, before and independently of the fit (FR-033, SC-008)"
        ),
    ),
    ReportableUnit(
        section="Per-Vendor Shrinkage",
        measure="Vendor",
        value="Realized shrinkage weight",
        criterion="Vendor-claim observation floor",
        directions=("observation floor",),
        verdict="Vendor-level claim",
        judges=(
            "each vendor's realized shrinkage weight against the published observation "
            "floor — how much of the estimate is borrowed, never whether it was right "
            "(FR-019, FR-020)"
        ),
    ),
    ReportableUnit(
        section="Horizon and Extrapolation",
        measure="Grid horizon",
        value="Extrapolation beyond the observed maximum",
        criterion="Extrapolation beyond the observed maximum",
        directions=("extends past", "does not reach"),
        verdict="Extrapolation beyond the observed maximum",
        judges=(
            "the forward grid against the computed maximum observed duration, which is a "
            "disclosure of how far the curves extrapolate rather than a gate (FR-031)"
        ),
    ),
)

# ---------------------------------------------------------------------------
# The refusal report's units — also P1 (US4)
# ---------------------------------------------------------------------------

REFUSAL_UNITS: tuple[ReportableUnit, ...] = (
    ReportableUnit(
        section="Breached Blocking Diagnostics",
        measure="Metric",
        value="Realized value",
        criterion="Threshold direction",
        directions=("at or above", "at or below"),
        verdict="Verdict",
        judges=(
            "one breached sampler diagnostic against its published bar — FR-017's "
            "five-field set, with the refusal itself as the verdict"
        ),
    ),
)

#: The **two**-field set a pre-sampling refusal carries, which is deliberately
#: not a four-part unit: FR-017 states it as the precondition and its realized
#: value with **no** threshold direction, because a precondition is not a
#: measured metric and has no floor or ceiling for a direction to disambiguate.
#: Asserted in its own right below, so the absence is evidenced as the shape
#: FR-017 asks for rather than read as a unit missing a part.
PRECONDITION_FIELDS: tuple[str, ...] = ("Precondition", "Realized value", "Verdict")

# ---------------------------------------------------------------------------
# The reproduction report's units — P2 (Phase 7)
# ---------------------------------------------------------------------------

REPRODUCTION_UNITS: tuple[ReportableUnit, ...] = (
    ReportableUnit(
        section="Reproduction Outcome",
        measure="Measure",
        value="Realized value",
        criterion="Decision criterion",
        directions=("at or below",),
        verdict="Verdict",
        judges=(
            "the largest per-line delta against the pre-registered day tolerance, together "
            "with exact provenance equality (FR-022, SC-018)"
        ),
    ),
    ReportableUnit(
        section="Draw-Digest Claim",
        measure="Digest agreement",
        value="Digest agreement",
        criterion="Decision criterion",
        directions=("digest equal",),
        verdict="Verdict",
        judges=(
            "every stored draw digest against bitwise equality, scoped to the whole "
            "recorded library pin (FR-032, SC-030)"
        ),
    ),
)

# ---------------------------------------------------------------------------
# The measures FR-038 names as carrying no criterion at all
# ---------------------------------------------------------------------------

#: `(kind, section, field)` for every measure FR-038 says is "recorded and judged
#: against nothing". Attaching a verdict to one would manufacture a gate FR-026
#: forbids, so the claim is an absence and the planted control is what makes it
#: evidence.
CRITERION_FREE: tuple[tuple[str, str, str], ...] = (
    ("run report", "Sampling Shape", "Wall clock"),
    ("run report", "Sampling Shape", "Draws per line"),
    ("run report", "Sampling Shape", "Tuning draws per chain"),
    ("reproduction report", "Compared Runs", "Wall clock"),
    ("refusal report", "Sampling", "Wall clock"),
    ("refusal report", "Sampling", "Realized sampling shape"),
)

#: What a criterion-free field is required to say instead of a verdict. Only the
#: wall clocks carry it: the realized shape's absence is stated by FR-026 and by
#: `_shape_section`'s docstring rather than in the artifact.
NO_CRITERION_SENTENCE = "with no criterion and therefore no verdict"


# ---------------------------------------------------------------------------
# Reading an emitted report as `section -> field -> the lines it renders`
# ---------------------------------------------------------------------------

#: A rendered bullet, captured with its text. Non-greedy on the label for the
#: reason `test_no_verdict.py` records — a value carrying its own bold run would
#: otherwise swallow the line — and the whole line is kept, because the direction
#: a reader receives is sometimes in the label (`Vendor-claim observation floor`)
#: and sometimes after the colon.
_BULLET = re.compile(r"^- \*\*(.+?)\*\*(.*)$")
_TABLE_ROW = re.compile(r"^\|(.+)\|$")
_TABLE_RULE = re.compile(r"^\|[\s:|-]+\|$")
_SECTION_HEADING = "## "


def _section_title(heading: str) -> str:
    """`## 4. Split and Held-Out Evidence` -> the schema key."""
    return heading.removeprefix(_SECTION_HEADING).split(". ", 1)[-1].strip()


def rendered_fields(report: Path) -> dict[str, dict[str, tuple[str, ...]]]:
    """`section -> field -> every line the artifact renders it as`.

    Every line and not the last: the refusal report renders one five-field set
    per breached diagnostic, so a mapping keyed by label alone would assert the
    unit over whichever breach happened to be rendered last. Table columns are
    read from the header row and their cells collected per column, which is how
    the per-vendor unit — whose measure, value and verdict are columns — is
    reachable by the same predicate as a bulleted one.
    """
    sections: dict[str, dict[str, list[str]]] = {}
    current: dict[str, list[str]] | None = None
    header: tuple[str, ...] = ()
    previous = ""
    for line in report.read_text(encoding="utf-8").splitlines():
        if line.startswith(_SECTION_HEADING):
            current = sections.setdefault(_section_title(line), {})
            header = ()
        elif current is None:
            pass
        elif (bullet := _BULLET.match(line)) is not None:
            current.setdefault(bullet.group(1), []).append(line)
        elif _TABLE_RULE.match(line) and _TABLE_ROW.match(previous):
            header = tuple(cell.strip() for cell in previous.strip("|").split("|"))
            for name in header:
                current.setdefault(name, [])
        elif header and _TABLE_ROW.match(line):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            for name, cell in zip(header, cells, strict=False):
                current.setdefault(name, []).append(cell)
        previous = line
    return {
        title: {name: tuple(lines) for name, lines in fields.items()}
        for title, fields in sections.items()
    }


def missing_parts(
    rendered: dict[str, dict[str, tuple[str, ...]]], unit: ReportableUnit
) -> tuple[str, ...]:
    """Which of FR-038's four parts the artifact does not render for `unit`.

    The predicate itself, returned as data rather than asserted here so the
    controls file can plant a unit short of a part and watch this find it. A part
    is present when its field is rendered **with content**: a declared column
    whose every cell is blank is a field a reader is owed and does not get.
    """
    fields = rendered.get(unit.section, {})
    return tuple(
        part
        for part, name in unit.parts.items()
        if not any(line.strip() for line in fields.get(name, ()))
    )


def stated_directions(
    rendered: dict[str, dict[str, tuple[str, ...]]], unit: ReportableUnit
) -> tuple[str, ...]:
    """Every declared direction phrase the unit's criterion actually states.

    Read off the criterion's rendered lines, label included. Empty is the
    failure SC-034 names: a value and a bar that do not resolve to a verdict for
    a reader who does not already know which is which.
    """
    lines = rendered.get(unit.section, {}).get(unit.criterion, ())
    joined = " ".join(lines).lower()
    return tuple(phrase for phrase in unit.directions if phrase in joined)


def verdict_markers(line: str) -> tuple[str, ...]:
    """Every verdict word a line carries, on word boundaries.

    Boundaries rather than substrings: `met` inside `measurement` would report a
    verdict on every field this epic renders, and a check that fires everywhere
    is turned off rather than fixed.
    """
    lowered = line.lower()
    return tuple(
        marker
        for marker in VERDICT_MARKERS
        if re.search(rf"\b{re.escape(marker)}\b", lowered) is not None
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def run_report(emitted_run: EmittedRun) -> dict[str, dict[str, tuple[str, ...]]]:
    """The shipped run's report, parsed. A P1 fixture, deliberately (T119)."""
    return rendered_fields(run_report_path(emitted_run.run_id, emitted_run.report_root))


def _only_report(invocation: RefusedInvocation, prefix: str) -> Path:
    """The one file of `prefix`'s kind the invocation emitted."""
    emitted = [path for path in invocation.emitted_reports if path.name.startswith(prefix)]
    assert len(emitted) == 1, f"{invocation.report_root} holds {[p.name for p in emitted]}"
    return emitted[0]


@pytest.fixture(scope="module")
def post_sampling_refusal(
    refused_after_sampling: RefusedInvocation,
) -> dict[str, dict[str, tuple[str, ...]]]:
    """NC-1's refusal report — the one carrying breached blocking diagnostics."""
    return rendered_fields(_only_report(refused_after_sampling, REFUSAL_REPORT_PREFIX))


@pytest.fixture(scope="module")
def pre_sampling_refusal(
    refused_below_the_chain_minimum: RefusedInvocation,
) -> dict[str, dict[str, tuple[str, ...]]]:
    """NC-14's refusal report — the one carrying an unmet precondition."""
    return rendered_fields(_only_report(refused_below_the_chain_minimum, REFUSAL_REPORT_PREFIX))


@pytest.fixture(scope="module")
def reproduction_report(
    reproduced_run: ReproducedRun,
) -> dict[str, dict[str, tuple[str, ...]]]:
    """The shared reproduction's report. **The one P2 fixture in this file.**"""
    emitted = [
        path
        for path in sorted(reproduced_run.report_root.iterdir())
        if path.name.startswith(REPRODUCTION_REPORT_PREFIX)
    ]
    assert len(emitted) == 1, f"{reproduced_run.report_root} holds {[p.name for p in emitted]}"
    return rendered_fields(emitted[0])


# ---------------------------------------------------------------------------
# The declared table is reviewed and is used
# ---------------------------------------------------------------------------


def test_every_declared_unit_names_what_it_judges_and_a_known_direction() -> None:
    """The guard on the review: a unit added without either fails here.

    `judges` is what makes this table a review rather than a list, and the
    direction phrases are checked against the vocabulary so a unit cannot
    introduce a private spelling that no other unit is held to.
    """
    for unit in (*RUN_REPORT_UNITS, *REFUSAL_UNITS, *REPRODUCTION_UNITS):
        assert unit.judges.strip(), f"{unit.section}/{unit.measure} judges nothing"
        assert unit.directions, f"{unit.section}/{unit.measure} declares no direction"
        for phrase in unit.directions:
            assert phrase in DIRECTIONS, (
                f"{unit.section}/{unit.measure} states its direction by {phrase!r}, which the "
                f"vocabulary does not name; a private spelling is a direction nobody reviewed"
            )


def test_the_direction_vocabulary_is_used_rather_than_carried() -> None:
    """Every phrase in the vocabulary belongs to a unit, and says something.

    A vocabulary wider than its use is a place a weaker phrase can be added and
    then relied on later, which is exactly how "the criterion including its
    direction" degrades into a keyword sweep.
    """
    used = {
        phrase
        for unit in (*RUN_REPORT_UNITS, *REFUSAL_UNITS, *REPRODUCTION_UNITS)
        for phrase in unit.directions
    }

    assert used == set(DIRECTIONS)
    assert all(meaning.strip() for meaning in DIRECTIONS.values())


def test_the_parser_reads_the_same_fields_the_shared_reader_finds(
    emitted_run: EmittedRun, run_report: dict[str, dict[str, tuple[str, ...]]]
) -> None:
    """This file's parser against `test_no_verdict.py`'s, so there is one opinion.

    The shared reader returns labels and this one returns labels with their
    text, so a disagreement about *which* fields exist would make every
    assertion below a claim about a document neither file is reading.
    """
    shared = report_fields(run_report_path(emitted_run.run_id, emitted_run.report_root))

    assert set(run_report) == set(shared)
    for title, fields in shared.items():
        assert set(run_report[title]) == set(fields), (
            f"`## {title}` reads as {sorted(set(run_report[title]) ^ set(fields))} on one "
            f"side only; the two parsers must agree about what a rendered field is"
        )


# ---------------------------------------------------------------------------
# P1 — the run report (T119)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("unit", RUN_REPORT_UNITS, ids=lambda unit: unit.measure)
def test_every_run_report_unit_renders_all_four_parts(
    unit: ReportableUnit, run_report: dict[str, dict[str, tuple[str, ...]]]
) -> None:
    """SC-034 over the artifact a P1 cut emits: measure, value, criterion, verdict.

    Over the emitted file rather than over `SECTION_FIELDS`, because a declared
    field that never renders is exactly how a part goes missing — the schema
    keeps describing a document the reader does not receive.
    """
    absent = missing_parts(run_report, unit)

    assert absent == (), (
        f"the run report's `## {unit.section}` unit is missing {list(absent)}. It judges "
        f"{unit.judges}, and a measure with a criterion is reported as one unit or it is not "
        f"reported (FR-038)"
    )


@pytest.mark.parametrize("unit", RUN_REPORT_UNITS, ids=lambda unit: unit.measure)
def test_every_run_report_criterion_states_its_direction(
    unit: ReportableUnit, run_report: dict[str, dict[str, tuple[str, ...]]]
) -> None:
    """The part this epic's own history records going missing first.

    A value and a bar do not resolve to a verdict for a reader who does not
    already know which metrics are floors and which are ceilings — which is why
    FR-038 states the unit rather than the verb "reporting".
    """
    stated = stated_directions(run_report, unit)

    assert stated, (
        f"the run report's `## {unit.section}` criterion field {unit.criterion!r} states no "
        f"direction. Expected one of {list(unit.directions)}, each of which tells a reader "
        f"which side of the bar the realized value has to fall on. Rendered as: "
        f"{run_report.get(unit.section, {}).get(unit.criterion, ())}"
    )


@pytest.mark.parametrize("unit", RUN_REPORT_UNITS, ids=lambda unit: unit.measure)
def test_every_run_report_unit_carries_a_realized_number(
    unit: ReportableUnit, run_report: dict[str, dict[str, tuple[str, ...]]]
) -> None:
    """The realized value is a measurement, not a label promising one.

    Weak on its own and load-bearing beside the part check: a renderer emitting
    the four fields with an empty value satisfies "all four parts render" and
    publishes nothing.
    """
    values = run_report[unit.section][unit.value]

    assert all(re.search(r"\d", line) for line in values), (
        f"`## {unit.section}` renders {unit.value!r} as {values} with no realized figure in "
        f"one of them"
    )


def test_the_per_vendor_table_pairs_every_weight_with_a_verdict(
    run_report: dict[str, dict[str, tuple[str, ...]]],
) -> None:
    """FR-020's per-vendor unit, asserted per row rather than per section.

    The floor is stated once and the verdict is stated twelve times, which is
    the arrangement FR-020 requires: a single population-level sentence leaves
    the reader to perform the comparison vendor by vendor, and that is the step
    a reader of a sparse-vendor estimate is least likely to perform.
    """
    section = run_report["Per-Vendor Shrinkage"]
    vendors = section["Vendor"]

    assert len(vendors) > 1, f"the table carries {vendors}, so the per-row claim is untested"
    assert len(section["Realized shrinkage weight"]) == len(vendors)
    assert len(section["Training lines"]) == len(vendors)
    assert len(section["Vendor-level claim"]) == len(vendors)
    for claim in section["Vendor-level claim"]:
        assert verdict_markers(claim), (
            f"a vendor row's claim reads {claim!r} and carries no verdict; the floor is "
            f"stated beside each vendor precisely so the comparison is performed here"
        )


def test_the_run_reports_criterion_free_measures_carry_no_verdict(
    run_report: dict[str, dict[str, tuple[str, ...]]],
) -> None:
    """SC-034's second half on the P1 artifact: no verdict without a criterion.

    The wall clock is the named instance. It is a measure this epic reports and
    judges against nothing, and attaching a verdict to it would manufacture a
    gate FR-026 reserves for the evaluation harness.
    """
    for kind, section, field in CRITERION_FREE:
        if kind != "run report":
            continue
        for line in run_report[section][field]:
            assert not verdict_markers(line), (
                f"the run report's {field!r} reads {line!r}, which carries "
                f"{list(verdict_markers(line))}; it has no criterion, so a verdict on it is "
                f"a gate nobody published"
            )
    assert all(NO_CRITERION_SENTENCE in line for line in run_report["Sampling Shape"]["Wall clock"])


# ---------------------------------------------------------------------------
# P1 — the two refusal reports (US4)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("unit", REFUSAL_UNITS, ids=lambda unit: unit.measure)
def test_every_breached_diagnostic_renders_the_whole_unit(
    unit: ReportableUnit, post_sampling_refusal: dict[str, dict[str, tuple[str, ...]]]
) -> None:
    """FR-017's five-field set, on **every** breach rather than on the first.

    Several rows breach in one run and an operator handed one of them returns
    for a second run to discover the next, so the parser keeps every rendered
    line and the count is compared across the four parts.
    """
    section = post_sampling_refusal[unit.section]

    assert missing_parts(post_sampling_refusal, unit) == ()
    assert len(section["Metric"]) > 1, "one breach only, so 'every breach' is untested"
    assert len(section["Realized value"]) == len(section["Metric"])
    assert len(section["Threshold"]) == len(section["Metric"])
    assert len(section["Threshold direction"]) == len(section["Metric"])
    assert len(section["Verdict"]) == len(section["Metric"])


def test_every_breached_diagnostic_states_its_thresholds_direction(
    post_sampling_refusal: dict[str, dict[str, tuple[str, ...]]],
) -> None:
    """Each rendered direction resolves to a side, in the words `diagnostics.py` owns.

    Compared against `direction_prose` rather than against a copy of its
    sentences, so the refusal message on standard error and the durable file say
    the same thing about the same bar (DV-038).
    """
    section = post_sampling_refusal["Breached Blocking Diagnostics"]
    prose = {direction_prose("max"), direction_prose("min")}

    for line in section["Threshold direction"]:
        assert any(sentence in line for sentence in prose), (
            f"{line!r} does not carry either direction sentence `diagnostics.py` publishes"
        )
        assert any(phrase in line.lower() for phrase in ("at or above", "at or below"))
    for line in section["Verdict"]:
        assert verdict_markers(line)


def test_the_pre_sampling_refusal_carries_the_two_field_set_and_no_direction(
    pre_sampling_refusal: dict[str, dict[str, tuple[str, ...]]],
) -> None:
    """FR-017's trailing clause, evidenced as a shape rather than as an omission.

    A precondition is not a measured metric: there is no floor or ceiling for a
    direction to disambiguate, so the set is the precondition, its realized
    value and the verdict. Asserted positively so the missing fifth field reads
    as the form FR-035 asks for and not as a unit short of a part.
    """
    section = pre_sampling_refusal["Unmet Preconditions"]

    for field in PRECONDITION_FIELDS:
        assert section.get(field), f"the unmet precondition renders no {field!r}"
    assert "Threshold direction" not in section
    assert "Threshold" not in section
    for line in section["Verdict"]:
        assert verdict_markers(line)


@pytest.mark.parametrize(
    ("section", "field"),
    [(section, field) for kind, section, field in CRITERION_FREE if kind == "refusal report"],
)
def test_the_refusal_reports_criterion_free_measures_carry_no_verdict(
    section: str, field: str, pre_sampling_refusal: dict[str, dict[str, tuple[str, ...]]]
) -> None:
    """The wall clock and the realized shape, on the artifact a refusal leaves.

    The refusal report is the document most likely to acquire a stray verdict —
    every other field in it is one — which is why the two that carry no
    criterion are asserted here rather than left to the run report's copy.
    """
    for line in pre_sampling_refusal[section][field]:
        assert not verdict_markers(line), (
            f"the refusal report's {field!r} reads {line!r} and carries "
            f"{list(verdict_markers(line))}"
        )


# ---------------------------------------------------------------------------
# The stored half — `forecast_diagnostic`'s four columns (SC-016, SC-034)
# ---------------------------------------------------------------------------

#: Module-level SQL, never assembled from values (Ruff S608). The four parts as
#: the store holds them: the metric with its parameter, the realized value, the
#: bar with its direction, and `passed`.
STORED_UNITS_SQL = text(
    """
    SELECT metric, parameter_name, diagnostic_scope, observed_value,
           threshold_value, threshold_direction, passed
    FROM forecast_diagnostic WHERE run_id = :run_id
    """
)


def test_every_stored_diagnostic_row_carries_the_whole_unit(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """DV-039's stored half: the columns are the unit, on every row of the run.

    Over the rows the shipped run actually wrote rather than over the table
    definition — a `NOT NULL` column proves a row cannot be written empty and
    says nothing about whether any row was written at all.
    """
    rows = db_session.execute(STORED_UNITS_SQL, {"run_id": emitted_run.run_id}).mappings().all()

    assert rows, "the run stored no diagnostic, so the stored half ranges over nothing"
    for row in rows:
        assert row["metric"], "a stored measure with no name"
        assert row["observed_value"] is not None
        assert row["threshold_value"] is not None
        assert row["passed"] is not None
        assert (row["parameter_name"] is not None) == (row["diagnostic_scope"] == "parameter"), (
            f"{dict(row)} names a parameter at run scope or omits one at parameter scope; a "
            f"bare metric name does not say which parameter was measured"
        )


def test_every_stored_direction_resolves_to_a_side_the_vocabulary_names(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """The stored direction and the rendered one are the same claim.

    `direction_prose` is what the refusal report renders, so putting the stored
    value through it and requiring the result to carry a declared phrase is what
    keeps the column, the stream and the file from drifting apart.
    """
    rows = db_session.execute(STORED_UNITS_SQL, {"run_id": emitted_run.run_id}).mappings().all()
    stored = {row["threshold_direction"] for row in rows}

    assert stored <= {"max", "min"}
    assert stored, "no direction was stored at all"
    for direction in stored:
        assert any(phrase in direction_prose(direction) for phrase in DIRECTIONS), (
            f"{direction!r} reads as {direction_prose(direction)!r}, which states no "
            f"direction this epic's reports use"
        )


# ---------------------------------------------------------------------------
# P2 — the reproduction report (Phase 7)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("unit", REPRODUCTION_UNITS, ids=lambda unit: unit.section)
def test_every_reproduction_unit_renders_all_four_parts_with_its_direction(
    unit: ReportableUnit, reproduction_report: dict[str, dict[str, tuple[str, ...]]]
) -> None:
    """The kind a P1 cut does not emit, asserted where Phase 7 is present.

    Kept apart from the run report's assertions rather than folded in with them:
    SC-034 is a `[US1]` criterion, and an assertion that needs a P2 fixture to
    run is not evidence for a P1 cut (A-016).
    """
    assert missing_parts(reproduction_report, unit) == (), (
        f"the reproduction report's `## {unit.section}` unit is missing "
        f"{list(missing_parts(reproduction_report, unit))}; it judges {unit.judges}"
    )
    assert stated_directions(reproduction_report, unit), (
        f"`## {unit.section}`'s criterion states no direction; expected one of "
        f"{list(unit.directions)}"
    )
    for line in reproduction_report[unit.section][unit.verdict]:
        assert verdict_markers(line), f"`## {unit.section}` renders {line!r} as its verdict"


def test_the_reproduction_reports_wall_clock_carries_no_verdict(
    reproduction_report: dict[str, dict[str, tuple[str, ...]]],
) -> None:
    """The third artifact's criterion-free measure, for completeness of the claim."""
    for line in reproduction_report["Compared Runs"]["Wall clock"]:
        assert not verdict_markers(line)
        assert NO_CRITERION_SENTENCE in line


def test_the_units_cover_every_kind_this_epic_emits(
    emitted_run: EmittedRun,
    post_sampling_refusal: dict[str, dict[str, tuple[str, ...]]],
    reproduction_report: dict[str, dict[str, tuple[str, ...]]],
) -> None:
    """All three kinds carry at least one unit, so none is asserted over nothing.

    FR-040 enumerates three report kinds and FR-038 reaches every measure with a
    criterion in any of them; a kind with no declared unit would pass this file
    silently while publishing whatever it liked.
    """
    del post_sampling_refusal, reproduction_report  # requested so all three were parsed

    assert run_report_path(emitted_run.run_id, emitted_run.report_root).name.startswith(
        RUN_REPORT_PREFIX
    )
    assert RUN_REPORT_UNITS and REFUSAL_UNITS and REPRODUCTION_UNITS
    assert {kind for kind, _, _ in CRITERION_FREE} == {
        "run report",
        "refusal report",
        "reproduction report",
    }
