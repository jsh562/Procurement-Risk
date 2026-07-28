"""T070 — DV-025 / SC-025: the event count published **with** its statement.

FR-028 has two halves and only one of them is a number. The count is the easy
half: `forecast_run.held_out_uncensored_event_count` is a column, and a run that
wrote it has recorded something. The half that matters is the sentence beside it
— **whether that count supports the precision the registered coverage band
claims** — because a count published without it is a figure a reader has no way
to weigh, and the reader who most needs the weighing is the one who will quote
the band.

**On this input the answer is no, and that is the point.** A 0.25 split of the
committed dataset realizes far fewer gradeable events than the ~120 `specs/prd.md`
derives its registered band from. Principle VII says a target that is not met is
published with its cause and never retroactively adjusted, so this file asserts
the miss is stated as a miss, in the section and in limitation **L-3**, with the
band left exactly where the registered document put it.

**The band is not this epic's and must not read as though it were.** FR-026
forbids E007 publishing a coverage threshold of its own; the band is restated
here under attribution, which is what keeps SC-025 and FR-026 compatible. The
report's own field says so, and so does the assertion below.

**The count is recomputed by a second route.** The published figure is compared
against the run row, and both against a `SELECT` over the stored split
assignments joined to `purchase_order_line.is_closed` — the same population
`held_out_prediction` holds, reached by SQL rather than by the Python that
produced the recorded value.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from forecast.conftest import EmittedRun
from forecast.test_no_verdict import report_fields
from model.forecast.paths import run_report_path
from model.forecast.report import (
    REGISTERED_COVERAGE_BAND,
    REGISTERED_UNCENSORED_EVENT_ASSUMPTION,
    SECTION_FIELDS,
)

#: The two sections this file reads. SC-025's statement lives in the first and
#: its four-part disclosure in the second; a report carrying the count in one and
#: nothing in the other discharges half of FR-028.
SPLIT_SECTION = "Split and Held-Out Evidence"
LIMITATIONS_SECTION = "Limitations"

#: The field carrying SC-025's count and statement.
EVENT_COUNT_FIELD = "Realized held-out uncensored event count"

#: The limitation FR-028's shortfall is disclosed as.
SHORTFALL_LIMITATION = "L-3"

#: Module-level SQL, never assembled from values (Ruff S608). The count is taken
#: over the **stored** assignments joined to the line's own delivered flag, which
#: is the population `fk_held_out_prediction__line_anchor` resolves against — not
#: over the dated censoring indicator, which answers a different question.
STORED_UNCENSORED_HELD_OUT_SQL = text(
    """
    SELECT count(*)
    FROM forecast_split_assignment a
    JOIN purchase_order_line l ON l.po_line_id = a.po_line_id
    WHERE a.run_id = :run_id AND a.split_side = 'held_out' AND l.is_closed
    """
)
RUN_EVENT_COUNT_SQL = text(
    "SELECT held_out_uncensored_event_count, held_out_fraction_declared "
    "FROM forecast_run WHERE run_id = :run_id"
)
HELD_OUT_PREDICTION_COUNT_SQL = text(
    "SELECT count(*) FROM held_out_prediction WHERE run_id = :run_id"
)

_FIELD = re.compile(r"^- \*\*(.+?)\*\*: (.*)$")
_LEADING_INTEGER = re.compile(r"^([0-9]+)")


def rendered_fields(run_id, report_root, section: str) -> dict[str, str]:
    """One section of the emitted report as `field -> rendered text`.

    The field *names* are checked against the closed schema by
    `test_no_verdict.py`; what this needs is the rendered value beside each of
    them, so the section is parsed again here for its text rather than for its
    keys. Bounded by the next heading, so a field that leaked into a
    neighbouring section is absent here rather than silently collected.
    """
    report = run_report_path(run_id, report_root).read_text(encoding="utf-8")
    inside = False
    fields: dict[str, str] = {}
    for line in report.splitlines():
        if line.startswith("## "):
            inside = line.endswith(section)
        elif inside:
            matched = _FIELD.match(line)
            if matched is not None:
                fields.setdefault(matched.group(1), matched.group(2))
    assert fields, f"the emitted report carries no `## {section}` section with fields in it"
    return fields


def limitation_text(run_id, report_root, identifier: str) -> dict[str, str]:
    """One limitation record from the emitted report, as `part -> rendered text`.

    Keyed on the `### L-x` heading rather than on position, because DV-037's
    claim is about identity: a record read by ordinal would silently follow
    whichever limitation happened to be third.
    """
    report = run_report_path(run_id, report_root).read_text(encoding="utf-8")
    inside = False
    parts: dict[str, str] = {}
    for line in report.splitlines():
        if line.startswith("### "):
            inside = line.startswith(f"### {identifier} ")
        elif line.startswith("## "):
            inside = False
        elif inside:
            matched = _FIELD.match(line)
            if matched is not None:
                parts[matched.group(1)] = matched.group(2)
    assert parts, f"the emitted report carries no `### {identifier}` limitation record"
    return parts


@pytest.fixture
def split_section(emitted_run: EmittedRun) -> dict[str, str]:
    """The shipped run's split-and-held-out entry, read once per test."""
    return rendered_fields(emitted_run.run_id, emitted_run.report_root, SPLIT_SECTION)


@pytest.fixture
def published_count(split_section: dict[str, str]) -> int:
    """The event count as the reader receives it: parsed out of the field's text."""
    return int(_LEADING_INTEGER.match(split_section[EVENT_COUNT_FIELD]).group(1))


# ---------------------------------------------------------------------------
# The count
# ---------------------------------------------------------------------------


def test_the_split_section_renders_every_field_its_schema_declares(
    emitted_run: EmittedRun,
) -> None:
    """The count cannot be discharged by a section that dropped it.

    An equality against the closed schema rather than a membership test for the
    one field this file is about: a section rendering six of its seven fields is
    the shape FR-038 names, where a measure survives and the statement beside it
    does not.
    """
    rendered = report_fields(run_report_path(emitted_run.run_id, emitted_run.report_root))

    assert set(rendered[SPLIT_SECTION]) == set(SECTION_FIELDS[SPLIT_SECTION])
    assert EVENT_COUNT_FIELD in rendered[SPLIT_SECTION]


def test_the_published_count_is_the_run_rows_and_the_stored_populations(
    db_session: Session, emitted_run: EmittedRun, published_count: int
) -> None:
    """DV-025's figure, checked against the row and against a second derivation.

    Three values, not two: the report, the column, and a count over the stored
    split assignments joined to the line's delivered flag. The third is what
    makes the first two evidence — a report and a column that agree because one
    was rendered from the other agree about nothing.
    """
    parameters = {"run_id": emitted_run.run_id}
    recorded = db_session.execute(RUN_EVENT_COUNT_SQL, parameters).mappings().one()
    recounted = int(db_session.execute(STORED_UNCENSORED_HELD_OUT_SQL, parameters).scalar_one())
    predictions = int(db_session.execute(HELD_OUT_PREDICTION_COUNT_SQL, parameters).scalar_one())

    assert published_count == recorded["held_out_uncensored_event_count"] == recounted
    assert published_count == predictions, (
        f"the report publishes {published_count} gradeable events while the run stored "
        f"{predictions} `held_out_prediction` rows; the count is what a grader has to work "
        f"with, so a figure larger than the population is a promise nothing can keep"
    )
    assert published_count > 0


# ---------------------------------------------------------------------------
# The statement beside it (SC-025)
# ---------------------------------------------------------------------------


def test_the_count_is_published_with_a_statement_about_the_bands_precision(
    split_section: dict[str, str], published_count: int
) -> None:
    """SC-025 itself: the number and the sentence, in one field.

    The disposition is checked against the comparison it claims to be, so a
    report rendering "supports" from anything other than the count against the
    registered assumption fails here rather than reading as measured. Both
    operands are required to be present — the realized count and the ~120 the
    band was derived from — because a verdict that names one operand is a
    caption.
    """
    published = split_section[EVENT_COUNT_FIELD]
    low, high = REGISTERED_COVERAGE_BAND
    supports = published_count >= REGISTERED_UNCENSORED_EVENT_ASSUMPTION

    assert str(REGISTERED_UNCENSORED_EVENT_ASSUMPTION) in published
    assert f"{low:.0%}" in published and f"{high:.0%}" in published
    assert ("does not support" in published) == (not supports)
    assert ("supports the precision" in published) == supports


def test_the_band_is_restated_under_attribution_and_never_asserted_as_this_epics(
    split_section: dict[str, str],
) -> None:
    """FR-026 and SC-025 held together, which is the whole difficulty of this field.

    The report has to state a coverage band and must not publish one. What makes
    that consistent is attribution: the field names the registered document the
    band belongs to and says outright that this run asserts no coverage
    threshold and no calibration verdict of its own.
    """
    published = split_section[EVENT_COUNT_FIELD]

    assert "specs/prd.md" in published
    assert "asserts no coverage threshold and no calibration verdict" in published
    assert SHORTFALL_LIMITATION in published


# ---------------------------------------------------------------------------
# The miss, published as a miss (L-3, Principle VII)
# ---------------------------------------------------------------------------


def test_the_realized_count_falls_short_of_the_registered_assumption_on_this_input(
    published_count: int,
) -> None:
    """The honest miss, asserted rather than left to whichever branch renders.

    The committed dataset does not carry enough held-out gradeable events for
    the registered band to be measurable at this split — that is L-3, and it is
    the reason SC-025 asks for a statement rather than a number. Asserted here
    so that a change making the shortfall disappear is a deliberate revision of
    this file and of L-3, never a silent one.
    """
    assert published_count < REGISTERED_UNCENSORED_EVENT_ASSUMPTION, (
        f"the split now realizes {published_count} gradeable events against the "
        f"~{REGISTERED_UNCENSORED_EVENT_ASSUMPTION} `specs/prd.md` derives its band from, so "
        f"L-3's shortfall no longer exists as written. That is a change to what this epic "
        f"publishes, not a test failure to route around: the limitation, this file and the "
        f"registered document have to move together"
    )


def test_limitation_l3_carries_the_same_count_and_states_the_shortfall(
    emitted_run: EmittedRun, published_count: int, db_session: Session
) -> None:
    """The disclosure half: L-3 measured on this run, not restated from a document.

    The same realized count appears in the record, beside the declared fraction
    the split was drawn at, so the limitation describes *this* run rather than
    the general shape of the problem. Its supporting evidence names the column
    the figure is recorded in, which is what makes it checkable at all.
    """
    record = limitation_text(emitted_run.run_id, emitted_run.report_root, SHORTFALL_LIMITATION)
    declared = float(
        db_session.execute(RUN_EVENT_COUNT_SQL, {"run_id": emitted_run.run_id})
        .mappings()
        .one()["held_out_fraction_declared"]
    )

    assert str(published_count) in record["Scope decision"]
    assert f"{declared:.2f}" in record["Scope decision"]
    assert str(REGISTERED_UNCENSORED_EVENT_ASSUMPTION) in record["Supporting evidence"]
    assert "held_out_uncensored_event_count" in record["Supporting evidence"]


def test_l3_adjusts_no_band_and_asserts_no_threshold_of_its_own(
    emitted_run: EmittedRun,
) -> None:
    """Principle VII's second clause: the target is not moved to meet the result.

    A shortfall published beside a quietly widened band is not a published
    shortfall. The record says which band it missed, that it adjusts none, and
    what would reverse the limitation — the reversal trigger being the part that
    distinguishes a disclosure from an apology.
    """
    record = limitation_text(emitted_run.run_id, emitted_run.report_root, SHORTFALL_LIMITATION)

    assert "adjusts no band" in record["Scope decision"]
    assert "no coverage threshold of its own" in record["Scope decision"]
    assert record["Reversal trigger"].strip()
    assert record["Production-scale alternative"].strip()
