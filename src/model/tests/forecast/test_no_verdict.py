"""T075 — DV-021 / SC-026: no emitted artifact carries a verdict on forecast quality.

Checked as an **absence over the enumerated emitted set**, by the closed-schema
predicate rather than by a term search. The distinction is not stylistic and
`spec.md` records the specific failure that decided it: SC-025 and limitation L-3
**require** the run report to state the registered coverage band and whether the
realized event count supports its precision, so a deny-list of terms like
"coverage" fires on this epic's own mandated content and needs a carve-out — and
a carve-out is a standing hole a later verdict can be phrased into. Under a
closed schema a verdict has nowhere to appear at all.

The check has three parts, and the middle one is what makes the other two
evidence rather than assertion:

1. **The declared schema is the reviewed one.** `SECTION_FIELDS` is closed, so
   "no declared field is a coverage threshold or a calibration verdict" is a
   claim about a finite enumeration and is discharged by reading it. What a test
   can add is a guard: the enumeration is re-typed here, independently, so
   widening it — which is the only way a verdict could become a declared field —
   fails until somebody re-reads it. `test_no_verdict_controls.py` plants exactly
   that widening.
2. **Every field the emitted report renders belongs to that schema**, per
   section and as an equality. Without this the schema would constrain a
   document nobody compared against it.
3. **The stored rows are the reviewed columns.** DV-021 quantifies over "row,
   report or file", and `data-model.md` records that no column anywhere in this
   model holds a coverage threshold, a calibration verdict or a quality
   judgement. The five stores' columns are enumerated here for the same reason
   the report's fields are.

**The epic does store thresholds and pass/fail flags, and that is not a breach.**
`forecast_diagnostic` records a bar, a direction and a `passed` flag per metric —
over *sampler convergence*, which is a different failure mode from forecast
quality and the one FR-017 refuses on. The ablation carries a met-or-missed
verdict against a floor derived from the input's own censoring, and the shrinkage
table carries a per-vendor supported-or-not against an observation floor. Each is
named below with what it judges; none of them judges whether the forecast is any
good, which is E014's and is reserved by FR-026.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from sqlalchemy import bindparam, text
from sqlalchemy.orm import Session

from forecast.conftest import EmittedRun
from model.forecast.config import DIAGNOSTIC_THRESHOLDS
from model.forecast.paths import run_report_path
from model.forecast.report import SECTION_FIELDS, SECTION_TITLES

# ---------------------------------------------------------------------------
# Reading an emitted report
# ---------------------------------------------------------------------------

#: A rendered field: a bullet opening with a bolded label. Non-greedy, so a field
#: whose value carries its own bold run — the ablation's comparator label does —
#: yields the label rather than everything up to the last `**` on the line.
_FIELD = re.compile(r"^- \*\*(.+?)\*\*")

#: A Markdown table row. The per-vendor section renders five of its declared
#: fields as table columns rather than as bullets, so a parser reading only
#: bullets would report five declared fields missing and would not see a sixth
#: column if one appeared.
_TABLE_ROW = re.compile(r"^\|(.+)\|$")

#: The alignment row beneath a table header, which is punctuation rather than
#: field names.
_TABLE_RULE = re.compile(r"^\|[\s:|-]+\|$")

_SECTION_HEADING = "## "


def _section_title(heading: str) -> str:
    """`## 4. Split and Held-Out Evidence` -> `Split and Held-Out Evidence`.

    The ordinal is rendering and the title is the schema key, so it is stripped
    here rather than carried into every comparison below.
    """
    return heading.removeprefix(_SECTION_HEADING).split(". ", 1)[-1].strip()


def report_fields(report: Path) -> dict[str, tuple[str, ...]]:
    """The emitted report as `section title -> the field names it renders`.

    Parsed out of the file the job wrote, never re-rendered here: DV-021 is a
    claim about the artifact a reader receives. Repeats are folded, because the
    limitations section renders its six fields once per record. A table's field
    names are its **header** row, recognised by the alignment rule beneath it —
    reading every row would collect the vendor data as though each cell were a
    declared field.
    """
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    previous = ""
    for line in report.read_text(encoding="utf-8").splitlines():
        if line.startswith(_SECTION_HEADING):
            current = sections.setdefault(_section_title(line), [])
        elif current is not None:
            matched = _FIELD.match(line)
            if matched is not None:
                current.append(matched.group(1))
            elif _TABLE_RULE.match(line) and _TABLE_ROW.match(previous):
                current.extend(cell.strip() for cell in previous.strip("|").split("|"))
        previous = line
    return {
        title: tuple(dict.fromkeys(field for field in fields if field))
        for title, fields in sections.items()
    }


def undeclared_fields(
    rendered: dict[str, tuple[str, ...]], schema: dict[str, tuple[str, ...]]
) -> tuple[tuple[str, str], ...]:
    """Every `(section, field)` the artifact renders that the schema does not declare.

    The closed-schema predicate itself, returned as data rather than asserted
    here so `test_no_verdict_controls.py` can plant a coverage threshold into a
    report and observe this function find it. A section the schema does not
    declare at all reports every field it carries, which is the right answer: an
    undeclared section is a place a verdict could live unexamined.
    """
    return tuple(
        (title, field)
        for title, fields in rendered.items()
        for field in fields
        if field not in schema.get(title, ())
    )


# ---------------------------------------------------------------------------
# The reviewed schema (part 1)
# ---------------------------------------------------------------------------

#: `SECTION_FIELDS` re-typed, independently of the module, together with what
#: each section judges. **This is the review DV-021 rests on.** Every field below
#: was read against SC-026's three prohibitions — a coverage threshold, a
#: calibration verdict, a pass/fail judgement on forecast quality — and none of
#: them is one. A field added to the module and not to this mapping fails
#: `test_the_declared_schema_is_the_one_that_was_reviewed`, which is what stops
#: the absence from being a claim about a document nobody read again.
REVIEWED_SCHEMA: dict[str, tuple[str, ...]] = {
    # Identity and provenance: what this run is and what it read. Nothing judged.
    "Run Identity": (
        "Run identifier",
        "As-of date",
        "Code revision",
        "Worktree state",
        "Model version",
        "Artifact schema version",
        "Active pointer",
    ),
    "Input Provenance": (
        "Input layer",
        "Datasheet reference",
        "Input row hash",
        "Serialization convention",
        "Fixture file digest",
        "Fixture digest agreement",
        "Roster hash",
    ),
    # The realized frame. The chain count carries the one criterion here — a
    # published precondition on the *sampler*, cleared before sampling began.
    "Sampling Shape": (
        "Chains",
        "Draws per line",
        "Tuning draws per chain",
        "Grid horizon",
        "Draw serialization",
        "Artifact hash",
        "Wall clock",
        "Sampling seed entropy",
    ),
    # The split's composition and SC-025's event count. The count is stated
    # against a band `specs/prd.md` registers and this epic restates; the
    # statement is about the count's sufficiency for that band's *precision*,
    # never about whether any forecast was accurate.
    "Split and Held-Out Evidence": (
        "Declared held-out fraction",
        "Realized held-out fraction",
        "Split seed entropy",
        "Split assignment hash",
        "Training lines",
        "Open lines forecast",
        "Realized held-out uncensored event count",
    ),
    # FR-038's four-part unit over the censoring term's effect. The verdict here
    # judges one term of this run's own model against a floor derived from the
    # input's censoring — not the forecast.
    "Censoring Ablation": (
        "Comparator",
        "Realized delta",
        "Interval",
        "Seeds",
        "Per-seed deltas",
        "Decision criterion",
        "Criterion derivation",
        "Verdict",
    ),
    # FR-020's per-vendor unit. The verdict judges whether a *vendor-level claim*
    # is supported at the published observation floor — a statement about how
    # much of an estimate is borrowed from the population.
    "Per-Vendor Shrinkage": (
        "Credible level",
        "Vendor-claim observation floor",
        "Vendor",
        "Training lines",
        "Realized shrinkage weight",
        "Interval",
        "Vendor-level claim",
    ),
    # FR-031's disclosure. Two measurements and their difference; no criterion.
    "Horizon and Extrapolation": (
        "Grid horizon",
        "Maximum observed duration",
        "Extrapolation beyond the observed maximum",
    ),
    # FR-027's four-part records.
    "Limitations": (
        "Identifier",
        "Subject",
        "Scope decision",
        "Supporting evidence",
        "Reversal trigger",
        "Production-scale alternative",
    ),
    # FR-040's membership, stated in the artifact.
    "Emitted Report Set": ("Report kind", "This file"),
}

#: The declared fields that carry an explicit verdict, and what each judges.
#: FR-038 requires a verdict to sit beside a decision criterion, so a section
#: declaring one and not the other is the shape this pairing catches.
VERDICT_FIELDS: dict[tuple[str, str], str] = {
    ("Censoring Ablation", "Verdict"): (
        "the realized ablation delta against a floor derived from the training split's own "
        "censoring bias — the effect of one model term, never the forecast's accuracy"
    ),
    ("Per-Vendor Shrinkage", "Vendor-level claim"): (
        "whether a vendor's estimate is majority its own data at the published observation "
        "floor — how much was borrowed from the population, never whether it was right"
    ),
}

#: The field each verdict above must be declared beside. FR-038's unit is the
#: measure, its value, its criterion **with direction**, and the verdict; a
#: verdict declared with no criterion in the same section is the malformed shape.
VERDICT_CRITERION_FIELDS: dict[str, str] = {
    "Censoring Ablation": "Decision criterion",
    "Per-Vendor Shrinkage": "Vendor-claim observation floor",
}


# ---------------------------------------------------------------------------
# The reviewed stored columns (part 3)
# ---------------------------------------------------------------------------

#: Every column of the five stores this epic writes, enumerated. The same review
#: as the report's fields, applied where DV-021's "row" clause points: not one of
#: these holds a coverage threshold, a calibration verdict or a judgement of
#: forecast quality.
REVIEWED_COLUMNS: dict[str, tuple[str, ...]] = {
    "forecast_run": (
        "run_id",
        "code_commit",
        "code_worktree_dirty",
        "input_data_hash",
        "seed_entropy",
        "chain_count",
        "draw_count",
        "tuning_count",
        "library_versions",
        "artifact_hash",
        "draw_serialization",
        "artifact_schema_version",
        "model_version",
        "as_of_date",
        "horizon_days",
        "wall_clock_seconds",
        "roster_hash",
        "is_active",
        "created_at",
        "covariate_names",
        "open_line_draw_semantic",
        "input_fixture_digest",
        "input_layer",
        "input_datasheet_ref",
        "canonical_serialization",
        "split_seed_entropy",
        "split_assignment_hash",
        "held_out_fraction_declared",
        "held_out_fraction_realized",
        "held_out_uncensored_event_count",
        "vendor_shrinkage",
        "open_line_count",
        "training_line_count",
    ),
    "forecast_split_assignment": (
        "run_id",
        "po_line_id",
        "split_side",
        "is_censored",
        "canonical_ordinal",
    ),
    "line_posterior": (
        "run_id",
        "po_line_id",
        "draw_count",
        "horizon_days",
        "draws",
        "survival",
        "residual_tail_mass",
        "draw_digest",
    ),
    "held_out_prediction": (
        "run_id",
        "po_line_id",
        "draw_count",
        "horizon_days",
        "anchor_date",
        "line_is_closed",
        "anchor_convention",
        "duration_semantic",
        "draws",
        "survival",
        "residual_tail_mass",
        "draw_digest",
    ),
    "forecast_diagnostic": (
        "diagnostic_id",
        "run_id",
        "diagnostic_scope",
        "parameter_name",
        "metric",
        "observed_value",
        "threshold_value",
        "threshold_direction",
        "is_blocking",
        "passed",
    ),
}

#: The stored columns that carry a flag or a bar, and what each is about. Every
#: `boolean` column of the five stores appears here, plus the diagnostic bar and
#: its direction; the selection is checked against the database's own types below
#: rather than trusted, so a boolean column added anywhere lands in the gap
#: between the two and fails.
JUDGEMENT_BEARING_COLUMNS: dict[tuple[str, str], str] = {
    ("forecast_run", "code_worktree_dirty"): "whether the checkout had uncommitted changes",
    ("forecast_run", "is_active"): "which run the serving tier should read",
    ("forecast_split_assignment", "is_censored"): "whether the line had delivered at the anchor",
    ("held_out_prediction", "line_is_closed"): "whether the line had delivered at all",
    ("forecast_diagnostic", "is_blocking"): "whether this sampler metric refuses the run",
    ("forecast_diagnostic", "passed"): "whether this sampler metric met its published bar",
    ("forecast_diagnostic", "threshold_value"): "the published bar for a sampler metric",
    ("forecast_diagnostic", "threshold_direction"): "which side of that bar passes",
}

#: The six metrics `forecast_diagnostic` admits, re-typed from `config.py`. Every
#: one is a property of the **sampler**; not one is a property of the forecast.
#: This is what bounds the `passed` column above: the only pass/fail this epic
#: stores ranges over these and nothing else.
REVIEWED_DIAGNOSTIC_METRICS = (
    "r_hat",
    "ess_bulk",
    "ess_tail",
    "divergent_transitions",
    "ebfmi",
    "max_treedepth_hits",
)

#: Module-level SQL, never assembled from values (Ruff S608).
STORE_COLUMNS_SQL = text(
    """
    SELECT table_name, column_name, data_type
    FROM information_schema.columns
    WHERE table_schema = 'public' AND table_name IN :tables
    ORDER BY table_name, ordinal_position
    """
).bindparams(bindparam("tables", expanding=True))

BOOLEAN_TYPE = "boolean"


def stored_columns(db_session: Session) -> dict[str, dict[str, str]]:
    """The five stores as `table -> column -> data type`, read from the catalogue.

    Read rather than declared, which is the whole point: the enumeration above
    is the reviewed set and this is what the database actually has, and DV-021
    is the comparison between them.
    """
    rows = (
        db_session.execute(STORE_COLUMNS_SQL, {"tables": list(REVIEWED_COLUMNS)}).mappings().all()
    )
    observed: dict[str, dict[str, str]] = {table: {} for table in REVIEWED_COLUMNS}
    for row in rows:
        observed[row["table_name"]][row["column_name"]] = row["data_type"]
    return observed


@pytest.fixture
def emitted_report(emitted_run: EmittedRun) -> dict[str, tuple[str, ...]]:
    """The shipped run's report, parsed into `section -> rendered fields`."""
    return report_fields(run_report_path(emitted_run.run_id, emitted_run.report_root))


# ---------------------------------------------------------------------------
# Part 1 — the declared schema is the reviewed one
# ---------------------------------------------------------------------------


def test_the_declared_schema_is_the_one_that_was_reviewed() -> None:
    """The guard on the review: a widened schema fails until it is read again.

    Field for field and section for section, because the ordering is part of
    what a reader receives. A verdict can only become a *declared* field by
    passing through this mapping, and the planted widening in the controls file
    is what shows the equality is load-bearing rather than reflexive.
    """
    assert SECTION_FIELDS == REVIEWED_SCHEMA
    assert tuple(SECTION_FIELDS) == SECTION_TITLES


def test_every_verdict_the_schema_declares_is_reviewed_and_sits_beside_its_criterion() -> None:
    """The two verdict-bearing fields, each judging something that is not the forecast.

    The pairing with a criterion field is FR-038's unit asserted structurally: a
    verdict declared in a section that declares no criterion would be a
    judgement against an unstated bar, which is the shape a calibration verdict
    would have to take to enter this schema at all.
    """
    for (title, field), subject in VERDICT_FIELDS.items():
        assert field in REVIEWED_SCHEMA[title]
        assert subject.strip()
        assert VERDICT_CRITERION_FIELDS[title] in REVIEWED_SCHEMA[title]

    assert set(VERDICT_CRITERION_FIELDS) == {title for title, _ in VERDICT_FIELDS}


# ---------------------------------------------------------------------------
# Part 2 — the emitted artifact renders that schema and nothing else
# ---------------------------------------------------------------------------


def test_every_field_the_emitted_report_renders_belongs_to_the_declared_schema(
    emitted_report: dict[str, tuple[str, ...]],
) -> None:
    """The closed-schema predicate over the artifact a reader actually receives.

    An equality per section rather than a containment: a field outside the
    schema is where a verdict would appear, and a declared field that never
    renders is a section the schema describes and the document does not have.
    """
    assert undeclared_fields(emitted_report, SECTION_FIELDS) == ()
    assert set(emitted_report) == set(SECTION_FIELDS)
    for title, declared in SECTION_FIELDS.items():
        assert set(emitted_report[title]) == set(declared), (
            f"the emitted `## {title}` section renders "
            f"{sorted(set(emitted_report[title]) ^ set(declared))} on one side only; the "
            f"schema is closed, so a field present in the document and absent from the "
            f"schema is a place a verdict can live unexamined"
        )


def test_the_report_renders_the_declared_sections_in_the_declared_order(
    emitted_report: dict[str, tuple[str, ...]],
) -> None:
    """Order, so a section cannot be smuggled in between two declared ones.

    The parse keys on `## ` headings, so an undeclared section would appear as
    an extra key here and its fields would be reported by
    `undeclared_fields` — but only if the sequence is compared rather than the
    set, which is what this asserts.
    """
    assert tuple(emitted_report) == SECTION_TITLES


# ---------------------------------------------------------------------------
# Part 3 — the stored rows
# ---------------------------------------------------------------------------


def test_the_five_stores_carry_exactly_the_reviewed_columns(db_session: Session) -> None:
    """DV-021's "row" clause: no column holds a threshold or a quality verdict.

    Read from `information_schema` and compared against the enumeration above,
    so a column added by a later migration fails here rather than being reviewed
    by nobody. The controls file plants one and observes this fail.
    """
    observed = stored_columns(db_session)
    for table, reviewed in REVIEWED_COLUMNS.items():
        assert tuple(observed[table]) == reviewed, (
            f"`{table}` carries {sorted(set(observed[table]) ^ set(reviewed))} on one side "
            f"only; every column of the five stores is enumerated in this file precisely so "
            f"that a coverage threshold or a calibration verdict cannot arrive as a column "
            f"nobody looked at"
        )


def test_every_flag_or_bar_the_stores_hold_judges_something_other_than_the_forecast(
    db_session: Session,
) -> None:
    """The judgement-bearing columns, enumerated and each accounted for.

    Selected from the catalogue by type rather than by name — every `boolean`
    column must appear in the reviewed mapping — so a `coverage_band_met`
    arriving later is caught by its shape and not by whether anyone guessed its
    spelling. The two threshold columns are named explicitly beside them.
    """
    observed = stored_columns(db_session)
    boolean_columns = {
        (table, column)
        for table, columns in observed.items()
        for column, data_type in columns.items()
        if data_type == BOOLEAN_TYPE
    }

    assert boolean_columns <= set(JUDGEMENT_BEARING_COLUMNS)
    for (table, column), subject in JUDGEMENT_BEARING_COLUMNS.items():
        assert column in observed[table]
        assert subject.strip()


def test_the_only_pass_fail_this_epic_stores_ranges_over_sampler_metrics(
    db_session: Session,
) -> None:
    """What bounds `forecast_diagnostic.passed`: the metric set it is taken over.

    A `passed` column is a pass/fail judgement, and the question SC-026 asks is
    *of what*. The answer is the six metrics `config.py` publishes, every one a
    property of the sampler; the flag cannot reach forecast quality because no
    row exists to carry it. Compared against the module and against the
    database's own admitted values.
    """
    published = tuple(threshold.metric for threshold in DIAGNOSTIC_THRESHOLDS)

    assert published == REVIEWED_DIAGNOSTIC_METRICS
    assert "passed" in stored_columns(db_session)["forecast_diagnostic"]
