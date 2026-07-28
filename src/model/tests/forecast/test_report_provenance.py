"""T123 — DV-043 / SC-040: the layer and the datasheet reach the **reader**.

FR-045's sub-clause exists because the columns were never the gap. A run that
records `input_layer` and `input_datasheet_ref` on `forecast_run` has satisfied
every schema-level statement of the rule and has told nobody: the manifest is a
database row and the reader reads the emitted report. SC-040 says so outright,
and records that SC-020 asserted the manifest alone — reproducing at criterion
level the defect the sub-clause was added to close.

So this file asserts the pair in the **artifact**, and asserts it against the
row rather than against a literal: a report rendering `SYNTHETIC` from a constant
would agree with this test and disagree with the run it describes.

**The datasheet reference is followed, not just read.** A reference a reader
cannot resolve is a citation to nothing, and Data Provenance requires every
synthetic dataset to ship a datasheet — so the recorded path is resolved against
the checkout and the file is required to exist and to be non-empty. The reference
is stored repository-relative and POSIX-separated for exactly this reason: it
resolves against a clone rather than against the disk that produced it.

**Why the layer label is the one that matters.** It is what tells a reader of a
forecast that every number descending from it is synthetic-derived. A forecast
whose provenance says nothing is a forecast a reader will weigh as though it came
from real history, which is the failure Principle I exists to prevent.
"""

from __future__ import annotations

import re

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session

from forecast.conftest import EmittedRun
from forecast.test_no_verdict import report_fields
from model.corpus.manifest import LAYER_REAL, LAYER_SYNTHETIC
from model.forecast.paths import run_report_path
from model.forecast.report import SECTION_FIELDS
from model.procurement import paths as procurement_paths

#: The section FR-045's two fields are declared in.
PROVENANCE_SECTION = "Input Provenance"

#: The two fields the sub-clause is about. Named separately from the section's
#: other five, which carry digests and hashes and are E007's own provenance
#: rather than the input's.
LAYER_FIELD = "Input layer"
DATASHEET_FIELD = "Datasheet reference"

#: Module-level SQL, never assembled from values (Ruff S608).
RUN_PROVENANCE_SQL = text(
    "SELECT input_layer, input_datasheet_ref, input_data_hash, input_fixture_digest "
    "FROM forecast_run WHERE run_id = :run_id"
)

_FIELD = re.compile(r"^- \*\*(.+?)\*\*: (.*)$")
_CODE_SPAN = re.compile(r"`([^`]+)`")


def provenance_section(emitted_run: EmittedRun) -> dict[str, str]:
    """The emitted report's input-provenance entry as `field -> rendered text`.

    Parsed from the file the job wrote. The whole claim under assertion is that
    this document carries the pair, so reconstructing the section by calling the
    renderer would assert it about a string that never reached a reader.
    """
    report = run_report_path(emitted_run.run_id, emitted_run.report_root).read_text(
        encoding="utf-8"
    )
    inside = False
    fields: dict[str, str] = {}
    for line in report.splitlines():
        if line.startswith("## "):
            inside = line.endswith(PROVENANCE_SECTION)
        elif inside:
            matched = _FIELD.match(line)
            if matched is not None:
                fields.setdefault(matched.group(1), matched.group(2))
    assert fields, (
        f"the emitted report carries no `## {PROVENANCE_SECTION}` section; FR-045's two "
        f"fields are declared there and a missing section is the manifest-only failure the "
        f"sub-clause exists to close"
    )
    return fields


@pytest.fixture
def section(emitted_run: EmittedRun) -> dict[str, str]:
    """The shipped run's provenance entry, read once per test."""
    return provenance_section(emitted_run)


@pytest.fixture
def recorded(db_session: Session, emitted_run: EmittedRun):
    """The same run's manifest half, read back out of `forecast_run`."""
    return db_session.execute(RUN_PROVENANCE_SQL, {"run_id": emitted_run.run_id}).mappings().one()


def test_the_provenance_section_renders_every_field_its_schema_declares(
    emitted_run: EmittedRun,
) -> None:
    """The two fields cannot be discharged by a section that dropped one.

    An equality against the closed schema rather than a membership test: a
    section rendering six of its seven fields is exactly the shape this rule is
    about, where the digests survive and the label a reader needs does not.
    """
    rendered = report_fields(run_report_path(emitted_run.run_id, emitted_run.report_root))

    assert set(rendered[PROVENANCE_SECTION]) == set(SECTION_FIELDS[PROVENANCE_SECTION])


def test_the_reader_facing_artifact_carries_the_layer_the_run_row_recorded(
    section: dict[str, str], recorded
) -> None:
    """DV-043's first half: the label, in the document, equal to the row's.

    Compared against the row rather than against `SYNTHETIC`, so a report
    rendering the label from a constant fails. The admitted values are named too
    — `ck_forecast_run__input_layer` takes two, and a third would mean the
    fixture declared something the schema does not recognise.
    """
    rendered = _CODE_SPAN.search(section[LAYER_FIELD]).group(1)

    assert rendered == recorded["input_layer"]
    assert rendered in (LAYER_REAL, LAYER_SYNTHETIC)


def test_the_layer_field_says_what_the_label_means_for_every_number_below_it(
    section: dict[str, str],
) -> None:
    """A label with no consequence stated is a label a reader can skip.

    The point of recording the layer in the reader's artifact is that every
    figure descending from the run inherits it; the field says so rather than
    leaving the inference to somebody who has not read FR-045.
    """
    assert "every number descending from this run inherits that label" in section[LAYER_FIELD]


def test_the_reader_facing_artifact_carries_the_datasheet_reference_the_row_recorded(
    section: dict[str, str], recorded
) -> None:
    """DV-043's second half, again against the row rather than against a literal."""
    rendered = _CODE_SPAN.search(section[DATASHEET_FIELD]).group(1)

    assert rendered == recorded["input_datasheet_ref"]
    assert rendered.strip()


def test_the_published_datasheet_reference_resolves_to_a_datasheet(
    section: dict[str, str],
) -> None:
    """The reference is followed. A citation to nothing is not provenance.

    Resolved repository-relative, which is how it is stored and why: the value
    has to mean the same thing in a clone as it did on the machine that wrote
    it. The target is checked against `model.procurement.paths`' own answer, so
    a reference pointing at some *other* readable file fails rather than passing
    for existing.
    """
    rendered = _CODE_SPAN.search(section[DATASHEET_FIELD]).group(1)
    root = procurement_paths.REPO_ROOT
    resolved = root / rendered

    assert not rendered.startswith("/") and ":" not in rendered, (
        f"{rendered!r} is not a repository-relative POSIX path, so it resolves against the "
        f"disk that produced it rather than against a clone"
    )
    assert resolved.is_file(), (
        f"the run cites {rendered!r} as its input's datasheet and no such file exists in "
        f"this checkout; Data Provenance requires every synthetic dataset to ship one, and a "
        f"reference a reader cannot follow discharges nothing"
    )
    assert resolved == procurement_paths.datasheet_path(root)
    assert resolved.read_text(encoding="utf-8").strip()


def test_the_pair_is_in_the_row_and_in_the_report_rather_than_in_either_alone(
    section: dict[str, str], recorded
) -> None:
    """SC-040 stated as the conjunction it is, so neither half can stand in.

    The manifest half is FR-014's and was already asserted; this is the half
    SC-020 could not reach. Both are required here in one test so that a
    regression removing the fields from the report — while leaving the columns
    populated — fails on the criterion that owns it.
    """
    assert recorded["input_layer"] and recorded["input_datasheet_ref"]
    assert recorded["input_layer"] in section[LAYER_FIELD]
    assert recorded["input_datasheet_ref"] in section[DATASHEET_FIELD]
