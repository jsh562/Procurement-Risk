"""T054 — DV-020 / SC-008 end to end, over what the job actually emitted.

Three claims, all read off the shared `forecast-fit` invocation rather than
reconstructed here: **the ablation ran**, **its floor was derived on the
training split alone**, and **the realized delta was published with its
interval** and an explicit met-or-missed verdict beside the criterion (FR-033,
FR-038).

This is the integration-level counterpart to `test_ablation_properties.py`.
That tier asserts the arithmetic over values `ablation.py` returns in memory;
this one asserts over the emitted report and the job's own diagnostics, which
is where a correctly implemented module that was never wired in, or wired in
against the whole cohort, would still look right everywhere else. `plan.md`
splits DV-020 exactly that way.

**No fit is performed here.** The shared run already pays for the ablation's
six fits — three committed seeds, two arms each — so every number below is one
the shipped run published, and this file costs a file read and two queries.

**The floor is recomputed by a second product-limit implementation** over rows
read straight from `purchase_order_line` and `lifecycle_event`, filtered by the
*stored* split side. `kaplan_meier_floor` is deliberately not called: it is the
function that produced the published figure, and SC-008 is relational — the
comparison has to range over two derivations, not over one expression twice.
"""

from __future__ import annotations

import math
import re
import uuid
from datetime import date

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from forecast.conftest import EmittedRun
from model.forecast.ablation import FLOOR_INTERVAL_PROBABILITY, MINIMUM_ABLATION_SEEDS
from model.forecast.fit import ABLATION_SEEDS
from model.forecast.paths import run_report_path
from model.forecast.report import SECTION_FIELDS
from model.forecast.split import TRAIN

#: The section this file reads, and the fields the closed schema declares for it.
ABLATION_SECTION = "Censoring Ablation"

#: Module-level SQL, never assembled from values (Ruff S608). One row per line
#: on the named split side, carrying the two dates a duration is measured
#: between. The terminal event is joined **at the run's own anchor**, so a line
#: whose delivery postdates it arrives here with no terminal date and is read as
#: censored — the same dated question `censoring.py` asks, reached by SQL.
SPLIT_SIDE_OBSERVATIONS_SQL = text(
    """
    SELECT l.order_date,
           r.as_of_date,
           (t.occurred_at AT TIME ZONE 'UTC')::date AS delivered_on
    FROM forecast_split_assignment a
    JOIN forecast_run r ON r.run_id = a.run_id
    JOIN purchase_order_line l ON l.po_line_id = a.po_line_id
    LEFT JOIN lifecycle_event t
           ON t.po_line_id = a.po_line_id
          AND t.is_terminal
          AND (t.occurred_at AT TIME ZONE 'UTC')::date <= r.as_of_date
    WHERE a.run_id = :run_id AND (:side = '' OR a.split_side = :side)
    """
)

RUN_COUNTS_SQL = text(
    "SELECT training_line_count, open_line_count FROM forecast_run WHERE run_id = :run_id"
)

#: Every side at once, for the discriminator below. The empty string rather than
#: `NULL`, so the bound parameter keeps one type on both calls.
EVERY_SIDE = ""

#: Tolerances against the report's own rendering: four decimals for every
#: fraction it publishes and one for the two day figures in the derivation.
FRACTION_TOLERANCE = 5e-5
DAY_TOLERANCE = 5e-2

#: The product-limit convention `ablation.py` publishes under: the median is the
#: first event time at which the curve has fallen to one half **or below**.
HALF = 0.5

_FIELD = re.compile(r"^- \*\*([^*]+)\*\*: (.*)$")
_LEADING_FRACTION = re.compile(r"^(-?[0-9]+\.[0-9]+)")
_INTERVAL = re.compile(r"\[(-?[0-9]+\.[0-9]+), (-?[0-9]+\.[0-9]+)\]")
_FLOOR = re.compile(r"at or above a floor of (-?[0-9]+\.[0-9]+)")
_TRAINING_LINES = re.compile(r"\(([0-9]+) lines\)")
_DERIVATION = re.compile(
    r"median of ([0-9]+\.[0-9]+) days against a naive completed-duration mean of "
    r"([0-9]+\.[0-9]+) days"
)
_SEEDS = re.compile(r"^([0-9]+(?:, [0-9]+)*)")


# ---------------------------------------------------------------------------
# Reading the emitted report
# ---------------------------------------------------------------------------


def emitted_section(run_id: uuid.UUID, report_root) -> dict[str, str]:
    """The ablation section of the run report, as `field -> rendered text`.

    Parsed out of the file the job wrote rather than re-rendered here, because
    DV-020's report half is a claim about the artifact a reader receives. The
    section is bounded by the next `##` heading, so a field that leaked into a
    neighbouring section would be absent here rather than silently collected.
    """
    report = run_report_path(run_id, report_root).read_text(encoding="utf-8")
    inside = False
    fields: dict[str, str] = {}
    for line in report.splitlines():
        if line.startswith("## "):
            inside = line.endswith(ABLATION_SECTION)
            continue
        matched = _FIELD.match(line) if inside else None
        if matched is not None:
            fields[matched.group(1)] = matched.group(2)
    assert fields, (
        f"the run report carries no `## {ABLATION_SECTION}` section with fields in it; "
        f"FR-033's measure is a declared section of the closed schema and cannot be skipped"
    )
    return fields


def fractions(text_value: str) -> list[float]:
    """Every four-decimal fraction in a rendered field, in order."""
    return [float(value) for value in re.findall(r"-?[0-9]+\.[0-9]+", text_value)]


# ---------------------------------------------------------------------------
# The floor, recomputed by a second implementation
# ---------------------------------------------------------------------------


def observations(db_session: Session, run_id: uuid.UUID, side: str) -> list[tuple[int, bool]]:
    """`(days, censored)` for every line on one stored split side.

    A delivered line contributes the duration to its terminal event; a line
    still open at the anchor contributes its elapsed time as a censoring time.
    Both land on the same axis, which is the whole of what lets a product-limit
    estimate use a censored row rather than discard it.
    """
    rows = db_session.execute(
        SPLIT_SIDE_OBSERVATIONS_SQL, {"run_id": run_id, "side": side}
    ).mappings().all()
    return [
        (
            (row["delivered_on"] - row["order_date"]).days
            if row["delivered_on"] is not None
            else (row["as_of_date"] - row["order_date"]).days,
            row["delivered_on"] is None,
        )
        for row in rows
    ]


def product_limit_median(rows: list[tuple[int, bool]]) -> float | None:
    """The Kaplan–Meier median: the first event time at which `S(t) <= ½`.

    Written as a plain loop over the distinct completed durations, sharing no
    expression with `ablation.py`'s vectorless-but-different accumulation. A row
    censored at an event time is at risk for that event, which is the standard
    convention and the one the published figure has to have used to agree here.
    """
    survival = 1.0
    for moment in sorted({days for days, censored in rows if not censored}):
        at_risk = sum(1 for days, _ in rows if days >= moment)
        occurred = sum(1 for days, censored in rows if days == moment and not censored)
        survival *= 1.0 - occurred / at_risk
        if survival <= HALF:
            return float(moment)
    return None


def derived_floor(rows: list[tuple[int, bool]]) -> tuple[float, float, float]:
    """`((median − mean) / median, median, mean)` over one cohort.

    The censoring-aware estimate against the one a reader gets by averaging the
    deliveries that have happened and discarding the rest. The gap between them
    is the input's own censoring bias, which is what SC-008 judges the fit's
    realized delta against.
    """
    completed = [days for days, censored in rows if not censored]
    median = product_limit_median(rows)
    assert median is not None and median > 0.0, (
        "the recomputed survival curve never reaches one half, so this file has no floor to "
        "compare the published one against"
    )
    mean = sum(completed) / len(completed)
    return (median - mean) / median, median, mean


@pytest.fixture
def section(emitted_run: EmittedRun) -> dict[str, str]:
    """The shipped run's ablation entry, read once per test."""
    return emitted_section(emitted_run.run_id, emitted_run.report_root)


# ---------------------------------------------------------------------------
# The ablation ran
# ---------------------------------------------------------------------------


def test_the_report_carries_the_ablation_entry_with_every_declared_field(
    section: dict[str, str],
) -> None:
    """DV-020's report half: the entry exists, complete, in the declared order.

    An equality against the closed schema rather than a membership test — a
    section carrying seven of its eight fields is the shape FR-038 names, where
    the delta survives and the verdict beside it does not.
    """
    assert tuple(section) == SECTION_FIELDS[ABLATION_SECTION]
    assert all(value.strip() for value in section.values())


def test_the_job_ran_the_ablation_over_the_committed_seeds(
    section: dict[str, str], emitted_run: EmittedRun
) -> None:
    """The seeds are the committed ones, and the job is observed using each.

    A per-run seed set would let a re-fit resample until the delta cleared the
    floor, so the published seeds are compared against `fit.py`'s constants and
    each is additionally found in the job's own diagnostics — the report alone
    could not distinguish three fits from three labels.
    """
    published = tuple(
        int(seed) for seed in _SEEDS.match(section["Seeds"]).group(1).split(", ")
    )

    assert published == ABLATION_SEEDS
    assert len(set(published)) >= MINIMUM_ABLATION_SEEDS
    for seed in ABLATION_SEEDS:
        assert f"ablation seed {seed}" in emitted_run.stderr
    assert "realized ablation delta" in emitted_run.stderr
    assert "censoring floor" in emitted_run.stderr


def test_the_comparator_is_published_as_an_ablation_and_not_as_a_baseline(
    section: dict[str, str],
) -> None:
    """Principle VIII, in the artifact rather than only in the module docstring.

    The censoring-ignoring arm is this run's own model with one term removed, so
    beating it is evidence about that term alone. A reader who took it for a
    baseline would read a far stronger claim than the one this run makes.
    """
    assert "not a baseline" in section["Comparator"]


# ---------------------------------------------------------------------------
# The delta, with its interval
# ---------------------------------------------------------------------------


def test_the_delta_is_summarised_from_the_per_seed_values_published_beside_it(
    section: dict[str, str],
) -> None:
    """FR-033's "never a single-seed pass", checked against the seeds themselves.

    The interval must be the range those seeds actually produced and the
    reported delta their nearest-rank median, so a figure computed from one seed
    and dressed with a band fails here rather than reading as measured.
    """
    published = section["Per-seed deltas"].split(", aligned")[0]
    per_seed = [float(value) for value in published.split(", ")]
    delta = float(_LEADING_FRACTION.match(section["Realized delta"]).group(1))
    low, high = (float(value) for value in _INTERVAL.search(section["Interval"]).groups())
    seeds = _SEEDS.match(section["Seeds"]).group(1).split(", ")

    assert len(per_seed) == len(seeds) >= MINIMUM_ABLATION_SEEDS
    assert low == pytest.approx(min(per_seed), abs=FRACTION_TOLERANCE)
    assert high == pytest.approx(max(per_seed), abs=FRACTION_TOLERANCE)
    assert delta == pytest.approx(
        sorted(per_seed)[max(math.ceil(HALF * len(per_seed)), 1) - 1], abs=FRACTION_TOLERANCE
    )


# ---------------------------------------------------------------------------
# The floor, derived on the training split alone
# ---------------------------------------------------------------------------


def test_the_published_floor_is_the_training_splits_own_censoring_bias(
    section: dict[str, str], db_session: Session, emitted_run: EmittedRun
) -> None:
    """AD-008's derivation, recomputed from the rows by a second implementation.

    All three published operands, not only the ratio: a floor that agreed by
    arithmetic accident while resting on a different median or a different mean
    would be a number the report's own derivation does not support.
    """
    floor = float(_FLOOR.search(section["Decision criterion"]).group(1))
    median, mean = (
        float(value) for value in _DERIVATION.search(section["Criterion derivation"]).groups()
    )
    expected, expected_median, expected_mean = derived_floor(
        observations(db_session, emitted_run.run_id, TRAIN)
    )

    assert median == pytest.approx(expected_median, abs=DAY_TOLERANCE)
    assert mean == pytest.approx(expected_mean, abs=DAY_TOLERANCE)
    assert floor == pytest.approx(expected, abs=FRACTION_TOLERANCE)


def test_the_floor_counts_only_the_training_side_and_says_how_many_lines(
    section: dict[str, str], db_session: Session, emitted_run: EmittedRun
) -> None:
    """"The training split alone" published as a number a reader can check.

    FR-007 is the whole content of the difference between the two counts, and a
    reader has no other way to tell which cohort the floor came off. Compared
    against the stored assignments and against the run row's own total.
    """
    published = int(_TRAINING_LINES.search(section["Criterion derivation"]).group(1))
    counts = db_session.execute(RUN_COUNTS_SQL, {"run_id": emitted_run.run_id}).mappings().one()
    training = len(observations(db_session, emitted_run.run_id, TRAIN))
    everything = len(observations(db_session, emitted_run.run_id, EVERY_SIDE))

    assert published == training == counts["training_line_count"]
    assert 0 < training < everything


def test_a_floor_taken_over_the_whole_cohort_would_not_be_this_number(
    section: dict[str, str], db_session: Session, emitted_run: EmittedRun
) -> None:
    """The discriminator that makes the agreement above evidence rather than luck.

    A floor derived over every line — held-out rows included — is computable and
    perfectly plausible, and no delivered constraint would reject it. It is a
    different number on this input, so the published figure could not have come
    from it.
    """
    floor = float(_FLOOR.search(section["Decision criterion"]).group(1))
    whole_cohort, _, _ = derived_floor(
        observations(db_session, emitted_run.run_id, EVERY_SIDE)
    )

    assert abs(floor - whole_cohort) > FRACTION_TOLERANCE, (
        f"the floor derived over the whole cohort ({whole_cohort:.4f}) is indistinguishable "
        f"from the published one ({floor:.4f}) on this input, so agreement with the "
        f"training-side recomputation is no longer evidence that the split was respected"
    )


def test_the_floor_carries_the_interval_its_own_estimate_supports(
    section: dict[str, str],
) -> None:
    """Principle II over the criterion, not only over the measurement.

    The Kaplan–Meier median is an estimate off a few hundred lines, so a bare
    number for the floor would be a claim of exactness it does not support. The
    band brackets the point estimate by construction, and the mass it carries is
    stated rather than left for a reader to assume.
    """
    floor = float(_FLOOR.search(section["Decision criterion"]).group(1))
    low, high = (
        float(value) for value in _INTERVAL.search(section["Decision criterion"]).groups()
    )

    assert low <= floor <= high
    assert f"{FLOOR_INTERVAL_PROBABILITY:.2f}" in section["Decision criterion"]


# ---------------------------------------------------------------------------
# The verdict (FR-038)
# ---------------------------------------------------------------------------


def test_the_verdict_is_the_comparison_between_the_published_delta_and_floor(
    section: dict[str, str],
) -> None:
    """FR-038's fourth part: measure, value, criterion with direction, verdict.

    The verdict is checked against the two numbers published beside it, so a
    section that rendered "met" from something other than the comparison fails
    here. The direction is asserted too — a value and a bar do not resolve to a
    verdict for a reader who does not already know which way the bar points.
    """
    delta = float(_LEADING_FRACTION.match(section["Realized delta"]).group(1))
    floor = float(_FLOOR.search(section["Decision criterion"]).group(1))
    verdict = section["Verdict"]

    assert "the passing direction is upward" in section["Decision criterion"]
    assert verdict.startswith("**met**") == (delta >= floor)
    assert verdict.startswith("**met**") or verdict.startswith("**missed**")


def test_a_missed_floor_is_published_as_a_shortfall_rather_than_suppressed(
    section: dict[str, str],
) -> None:
    """SC-008's stated disposition, asserted on whichever branch this run took.

    A shortfall is published with its cause and the floor is not adjusted to
    meet it (Principle VII); a met floor states the comparison that was made.
    Either way the verdict names both operands, which is what stops it from
    being a caption.
    """
    verdict = section["Verdict"]
    floor = float(_FLOOR.search(section["Decision criterion"]).group(1))

    if verdict.startswith("**missed**"):
        assert "shortfall rather than treated as a defect" in verdict
        assert "not adjusted" in verdict
    assert f"{floor:.4f}" in verdict


def test_the_anchor_the_ablation_was_measured_at_is_the_runs_own(
    db_session: Session, emitted_run: EmittedRun
) -> None:
    """The floor and the delta describe the same run, which nothing else states.

    Every observation above is joined to `forecast_run.as_of_date` through
    `run_id`, so this file would agree with itself at a wrong anchor. The one
    comparison that pins it is against the date the invocation asked for.
    """
    rows = db_session.execute(
        SPLIT_SIDE_OBSERVATIONS_SQL, {"run_id": emitted_run.run_id, "side": TRAIN}
    ).mappings().all()

    assert rows
    assert {row["as_of_date"] for row in rows} == {emitted_run.as_of_date}
    assert isinstance(emitted_run.as_of_date, date)
