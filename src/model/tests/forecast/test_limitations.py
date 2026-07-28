"""T071 — DV-024 and DV-037 / SC-024 and SC-029, over the emitted report.

Four claims about the artifact a reader receives, and they are four rather than
one because each fails on its own:

- **every disclosed limitation carries all four parts** (DV-024, FR-027): scope
  decision, supporting evidence, reversal trigger, production-scale alternative.
  A record short of a part is not a shorter record — it is a limitation whose
  reversal condition nobody stated, and Principle VII names the four together for
  that reason.
- **every limitation `data-model.md` declares is present by identity** (DV-037).
  DV-024 quantifies over the records that are there, so a set of impeccable
  limitations that omits the horizon's extrapolation satisfies it completely.
  `NC-23` plants that set against the checker; this file asserts it over the
  emitted document. The declared set is read from the module rather than written
  here, which is what let it widen from four to five — `L-5` was declared under
  AD-013 and emitted by nothing — without this claim having to be restated.
- **the observation count below which no vendor-level claim stands is stated**
  (SC-024, FR-020) — in the reader-facing artifact, with each vendor's realized
  weight and training count paired against it and given an explicit verdict.
  Stating it once for the population leaves the reader to perform the comparison
  vendor by vendor, which is the step FR-038 says must not be delegated.
- **`L-2` names the horizon's extrapolation past the longest observed duration**
  (SC-029, FR-031), with the maximum **computed** rather than quoted — E005's
  datasheet publishes a median and a P80 and never a maximum, which is G-10.
  Recomputed here from `lifecycle_event` by SQL, so the published figure is
  compared against a second derivation rather than against itself.

The four-part *checker* is exercised by `test_limitation_controls.py`; the
presence-by-identity checker by `test_limitation_presence.py`. What this file
adds is that the checker was actually run against what shipped: a report emitted
without calling it would satisfy both control files and disclose nothing.
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
    DECISION_STATE,
    LIMITATION_IDENTIFIERS,
    SECTION_FIELDS,
    SHRINKAGE_SUPPORT_THRESHOLD,
)

#: The three sections this file reads.
LIMITATIONS_SECTION = "Limitations"
SHRINKAGE_SECTION = "Per-Vendor Shrinkage"
HORIZON_SECTION = "Horizon and Extrapolation"

#: The four parts, as the report renders their labels. The module's field names
#: are `scope_decision` and so on; these are what a reader sees, and DV-024 is a
#: claim about the reader's document.
RENDERED_PARTS = (
    "Scope decision",
    "Supporting evidence",
    "Reversal trigger",
    "Production-scale alternative",
)

#: The record whose subject SC-029 names.
HORIZON_LIMITATION = "L-2"

#: The record stating the vendor-claim observation floor's consequence.
VENDOR_LIMITATION = "L-4"

#: The record AD-013 opened, on the rework-loop bias. Named here for the same
#: reason `L-2` is: it carries a figure this run measured, and a record naming
#: the bias without the count is a sentence about lognormals in general.
REWORK_LIMITATION = "L-5"

#: Module-level SQL, never assembled from values (Ruff S608). The longest
#: duration the input observes at the run's own anchor, under `data-model.md`
#: § Conventions' whole-day convention — the same question `report.py` asks in
#: Python, reached by a different route.
MAXIMUM_OBSERVED_SQL = text(
    """
    SELECT max((t.occurred_at AT TIME ZONE 'UTC')::date - l.order_date)
    FROM purchase_order_line l
    JOIN lifecycle_event t ON t.po_line_id = l.po_line_id AND t.is_terminal
    JOIN forecast_run r ON r.run_id = :run_id
    WHERE (t.occurred_at AT TIME ZONE 'UTC')::date <= r.as_of_date
    """
)
RUN_HORIZON_SQL = text(
    "SELECT horizon_days, vendor_shrinkage, open_line_count "
    "FROM forecast_run WHERE run_id = :run_id"
)

#: L-5's figure, reached by SQL rather than by the module that publishes it. A
#: line is open at the anchor when no terminal event has occurred by then, and it
#: stands at the decision state when the last event it has walked by then landed
#: there — the two halves the Python helper applies in the same order.
OPEN_AT_DECISION_SQL = text(
    """
    SELECT count(*)
      FROM purchase_order_line l
      JOIN forecast_run r ON r.run_id = :run_id
     WHERE NOT EXISTS (
               SELECT 1 FROM lifecycle_event t
                WHERE t.po_line_id = l.po_line_id AND t.is_terminal
                  AND (t.occurred_at AT TIME ZONE 'UTC')::date <= r.as_of_date
           )
       AND (
               SELECT e.to_state FROM lifecycle_event e
                WHERE e.po_line_id = l.po_line_id
                  AND (e.occurred_at AT TIME ZONE 'UTC')::date <= r.as_of_date
                ORDER BY e.sequence_no DESC LIMIT 1
           ) = :decision_state
    """
)

_FIELD = re.compile(r"^- \*\*(.+?)\*\*: (.*)$")
_RECORD_HEADING = re.compile(r"^### (\S+) — (.+)$")
_RECORD_COUNT = re.compile(r"^([0-9]+) records, each in four parts", re.MULTILINE)
_TABLE_RULE = re.compile(r"^\|[\s:|-]+\|$")
_INTEGER = re.compile(r"([0-9]+)")


def emitted_report_text(emitted_run: EmittedRun) -> str:
    """The shipped run's report, read from the path the job resolved it under."""
    return run_report_path(emitted_run.run_id, emitted_run.report_root).read_text(encoding="utf-8")


def limitation_records(report: str) -> dict[str, dict[str, str]]:
    """Every limitation record, as `identifier -> rendered part -> text`.

    Keyed on the `###` heading's identifier rather than on position: DV-037 is a
    claim about identity, and a record read by ordinal would follow whichever
    limitation happened to be third. Collected in one pass so a record rendered
    outside the limitations section — which the closed schema forbids — is
    absent here rather than quietly picked up.
    """
    records: dict[str, dict[str, str]] = {}
    current: dict[str, str] | None = None
    inside = False
    for line in report.splitlines():
        if line.startswith("## "):
            inside = line.endswith(LIMITATIONS_SECTION)
            current = None
            continue
        if not inside:
            continue
        heading = _RECORD_HEADING.match(line)
        if heading is not None:
            current = records.setdefault(heading.group(1), {"Heading subject": heading.group(2)})
            continue
        matched = _FIELD.match(line) if current is not None else None
        if matched is not None:
            current[matched.group(1)] = matched.group(2)
    return records


def section_fields(report: str, section: str) -> dict[str, str]:
    """One section's rendered fields as `field -> text`, bounded by the next heading."""
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


def vendor_rows(report: str) -> dict[str, tuple[str, ...]]:
    """The per-vendor table as `vendor -> its remaining cells`.

    Read from the rendered table because that is where FR-020 requires the
    pairing to be: a weight, its own observation count and a verdict on one
    line. A reader who has to join two lists has been handed the comparison
    rather than the answer.
    """
    rows: dict[str, tuple[str, ...]] = {}
    inside = False
    for line in report.splitlines():
        if line.startswith("## "):
            inside = line.endswith(SHRINKAGE_SECTION)
        elif inside and line.startswith("| `") and not _TABLE_RULE.match(line):
            cells = tuple(cell.strip() for cell in line.strip("|").split("|"))
            rows[cells[0].strip("`")] = cells[1:]
    return rows


@pytest.fixture
def report(emitted_run: EmittedRun) -> str:
    """The emitted run report's text, read once per test."""
    return emitted_report_text(emitted_run)


@pytest.fixture
def records(report: str) -> dict[str, dict[str, str]]:
    """The emitted limitation records, keyed by identifier."""
    parsed = limitation_records(report)
    assert parsed, "the emitted report discloses no limitation at all"
    return parsed


# ---------------------------------------------------------------------------
# DV-024 — the four-part form
# ---------------------------------------------------------------------------


def test_every_emitted_limitation_carries_all_four_parts(
    records: dict[str, dict[str, str]],
) -> None:
    """DV-024 over the document rather than over the checker.

    Every part must be present *and* non-empty: a rendered label with nothing
    after it satisfies a presence test and discloses exactly as much as an
    absent one. The identifier and subject are checked alongside, because a
    record a reader cannot name is a record they cannot cite.
    """
    for identifier, record in records.items():
        for part in (*RENDERED_PARTS, "Identifier", "Subject"):
            assert part in record, f"limitation {identifier} renders no {part!r}"
            assert record[part].strip(), f"limitation {identifier}'s {part!r} is empty"


def test_each_records_identifier_field_agrees_with_the_heading_it_is_filed_under(
    records: dict[str, dict[str, str]],
) -> None:
    """The heading and the field are two renderings of one identity.

    They are parsed separately and must agree, because presence-by-identity is
    checked against one of them and a reader cites the other. A record headed
    `L-2` whose identifier field reads `L-3` would be found by DV-037 and read
    as something else.
    """
    for identifier, record in records.items():
        assert record["Identifier"].strip("`") == identifier
        assert record["Subject"] == record["Heading subject"]


def test_the_section_states_the_record_count_it_actually_renders(report: str) -> None:
    """The header's count against the records beneath it.

    A count rendered from anything other than the records — a constant, a
    configured expectation — would let a dropped record pass unnoticed by the
    one reader-visible signal that a record is missing.
    """
    stated = _RECORD_COUNT.search(report)

    assert stated is not None
    assert int(stated.group(1)) == len(limitation_records(report))


# ---------------------------------------------------------------------------
# DV-037 — presence by identity
# ---------------------------------------------------------------------------


def test_every_declared_limitation_is_present_by_identity(
    records: dict[str, dict[str, str]],
) -> None:
    """DV-037: the limitations that were *owed* were written.

    A containment rather than an equality — disclosing one more than the
    declared set is Principle VII working rather than failing — and the declared
    ones are named from the module so this cannot pass by agreeing with whatever
    the report happens to carry.
    """
    assert set(LIMITATION_IDENTIFIERS) <= set(records), (
        f"the emitted report omits {sorted(set(LIMITATION_IDENTIFIERS) - set(records))}; "
        f"`data-model.md` declares {len(LIMITATION_IDENTIFIERS)} limitations by identity, "
        f"and a set of well-formed records that happens not to include one of them "
        f"discloses nothing about it"
    )


def test_the_limitations_section_renders_only_its_declared_fields(
    emitted_run: EmittedRun,
) -> None:
    """The closed schema over this section, so a fifth part cannot appear here.

    The four parts are what Principle VII names; a record carrying a fifth
    labelled field would be outside the declared schema and is caught by the
    same predicate DV-021 rests on.
    """
    rendered = report_fields(run_report_path(emitted_run.run_id, emitted_run.report_root))

    assert set(rendered[LIMITATIONS_SECTION]) == set(SECTION_FIELDS[LIMITATIONS_SECTION])


# ---------------------------------------------------------------------------
# SC-029 — L-2 and the computed maximum observed duration
# ---------------------------------------------------------------------------


def test_the_horizon_section_publishes_a_computed_maximum_observed_duration(
    report: str, db_session: Session, emitted_run: EmittedRun
) -> None:
    """G-10's missing number, measured here by SQL and compared against the report.

    The datasheet publishes a median and a P80 and never a maximum, so the
    figure has no source to be quoted from and the report computes it. This
    recomputation walks `lifecycle_event` at the run's own anchor under the same
    whole-day convention, which is what makes the published number checkable
    rather than merely stated.
    """
    parameters = {"run_id": emitted_run.run_id}
    observed = db_session.execute(MAXIMUM_OBSERVED_SQL, parameters).scalar_one()
    run = db_session.execute(RUN_HORIZON_SQL, parameters).mappings().one()
    fields = section_fields(report, HORIZON_SECTION)

    assert observed is not None, "no line has delivered at the anchor, so L-2 reads differently"
    assert int(_INTEGER.search(fields["Maximum observed duration"]).group(1)) == int(observed)
    assert int(_INTEGER.search(fields["Grid horizon"]).group(1)) == run["horizon_days"]
    assert int(_INTEGER.search(fields["Extrapolation beyond the observed maximum"]).group(1)) == (
        run["horizon_days"] - int(observed)
    )


def test_limitation_l2_names_the_extrapolation_with_both_numbers(
    records: dict[str, dict[str, str]], db_session: Session, emitted_run: EmittedRun
) -> None:
    """SC-029: the horizon's extrapolation, in the four-part form, with figures.

    The record has to carry the same two numbers the horizon section publishes —
    the observed maximum and the span past it — or the limitation is a sentence
    about extrapolation in general rather than a disclosure about this run.
    """
    parameters = {"run_id": emitted_run.run_id}
    observed = int(db_session.execute(MAXIMUM_OBSERVED_SQL, parameters).scalar_one())
    horizon = db_session.execute(RUN_HORIZON_SQL, parameters).mappings().one()["horizon_days"]
    record = records[HORIZON_LIMITATION]

    assert "extrapolation" in record["Subject"].lower()
    assert str(observed) in record["Supporting evidence"]
    assert str(horizon - observed) in record["Supporting evidence"]
    assert str(horizon) in record["Scope decision"]


# ---------------------------------------------------------------------------
# SC-024 — the observation count, stated and paired per vendor
# ---------------------------------------------------------------------------


def test_the_report_states_the_observation_count_no_vendor_claim_stands_below(
    report: str,
) -> None:
    """SC-024's second half, in the reader-facing artifact (FR-020).

    The floor is published as a **derivation rule** applied to this run's
    realized weights, so the field must carry the rule — the smallest
    training-line count at which realized shrinkage reaches the published
    threshold — as well as its outcome. A number with no rule beside it is a
    number chosen after the weights were seen, for anything the reader can tell.
    """
    field = section_fields(report, SHRINKAGE_SECTION)["Vendor-claim observation floor"]

    assert field.strip()
    assert f"{SHRINKAGE_SUPPORT_THRESHOLD:.2f}" in field
    assert "smallest training-line count" in field
    assert "not chosen after seeing them" in field


def test_every_vendor_is_paired_with_its_own_count_weight_and_verdict(
    report: str, db_session: Session, emitted_run: EmittedRun
) -> None:
    """FR-038's unit at the vendor level, where FR-020 requires it.

    Every vendor the run stored a weight for appears in the table with a
    training count, an interval and an explicit supported-or-not verdict, and
    the verdict is checked against the published weight and the published
    threshold — so a table rendering a verdict from anything other than that
    comparison fails here rather than reading as measured.
    """
    stored = (
        db_session.execute(RUN_HORIZON_SQL, {"run_id": emitted_run.run_id})
        .mappings()
        .one()["vendor_shrinkage"]
    )
    rows = vendor_rows(report)

    assert set(rows) == set(stored)
    for vendor, cells in rows.items():
        count, weight, interval, verdict = cells
        assert int(count) >= 0
        assert float(weight) == pytest.approx(float(stored[vendor]["median"]), abs=1e-3)
        assert interval.startswith("[") and interval.endswith("]")
        assert ("**not supported**" in verdict) == (float(weight) < SHRINKAGE_SUPPORT_THRESHOLD)
        assert "at the floor above" in verdict


def test_limitation_l4_states_the_consequence_measured_on_this_run(
    records: dict[str, dict[str, str]], report: str
) -> None:
    """L-4 carries a figure this run produced, not one quoted from the datasheet.

    The smallest vendor's realized training count is what makes the limitation
    about this fit; E005's published 0.22-at-n=5 is a property of that dataset's
    generative constants and the record says so explicitly.
    """
    smallest = min(int(cells[0]) for cells in vendor_rows(report).values())
    record = records[VENDOR_LIMITATION]

    assert str(smallest) in record["Supporting evidence"]
    assert f"{SHRINKAGE_SUPPORT_THRESHOLD:.2f}" in record["Scope decision"]
    assert "observation floor published in § Per-Vendor Shrinkage" in record["Scope decision"]


def test_limitation_l5_states_the_rework_bias_with_the_count_it_exposes(
    records: dict[str, dict[str, str]], db_session: Session, emitted_run: EmittedRun
) -> None:
    """AD-013's stated cost, in the report rather than in a source comment.

    L-5 says an open line standing at the two-way decision state is forecast
    **short**, because the conditional draw walks the forward legs only and a
    future rework loop adds legs the parent never included. The exposure is a
    count, and the count is recomputed here by SQL — a second derivation, as
    with L-2's maximum — so a record quoting a plausible number it did not
    measure fails rather than reads as measured.

    The direction is asserted too. A record disclosing the omission without
    saying which way it biases the forecast leaves the reader unable to act on
    it, which is the half Principle VII's "cause" clause is about.
    """
    parameters = {"run_id": emitted_run.run_id, "decision_state": DECISION_STATE}
    at_decision = int(db_session.execute(OPEN_AT_DECISION_SQL, parameters).scalar_one())
    open_lines = int(
        db_session.execute(RUN_HORIZON_SQL, {"run_id": emitted_run.run_id})
        .mappings()
        .one()["open_line_count"]
    )
    record = records[REWORK_LIMITATION]

    assert "rework" in record["Subject"].lower()
    assert "short" in record["Scope decision"]
    assert f"**{at_decision}**" in record["Supporting evidence"], (
        f"L-5 does not publish the measured count; {at_decision} of {open_lines} open lines "
        f"stand at {DECISION_STATE!r} on this run. Evidence reads: "
        f"{record['Supporting evidence']}"
    )
    assert str(open_lines) in record["Supporting evidence"]
    assert DECISION_STATE in record["Supporting evidence"]
