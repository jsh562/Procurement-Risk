"""FR-022 / FR-070: the recorded exclusion, and the invocation reconciliation.

The two things `ingest/cli.py` decides before any row is written: which
documents extraction reaches, and under which trace identifier it reaches them.
Both are published rather than inferred, so both are asserted here as
*publication* rather than as behaviour — the requirement in each case is that a
reader can see the fact, not merely that the code did the right thing.

FR-070's comparison is asserted against `report.reconciliation_section`, which
is the path the run takes (`publish.py` calls it). `ingest/cli.py` briefly
carried a second, unreachable copy of the same invariant; it was deleted, and
the assertions that were only ever made against it — the signed difference, and
the refusal of a negative count — are made here against the live path instead.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from model.ingest.cli import (
    EXCLUSION_REASON,
    ExtractionScope,
    OrchestrationError,
    RunTrace,
    attempted_invocation_count,
    count_recorded_invocations,
    documents_by_layer,
    exclusion_section,
    select_extraction_documents,
)
from model.ingest.documents import DocumentRecord
from model.ingest.report import ReportError, reconciliation_section

TRACE_ID = "0123456789abcdef0123456789abcdef"


def transmittal(document_id: str) -> DocumentRecord:
    return DocumentRecord(
        document_id=document_id,
        document_type="transmittal",
        project_id="PRJ-001",
        title=document_id,
        source_kind="SYNTHETIC",
        license_basis="{}",
        content_hash="sha256:" + "0" * 64,
        path=Path(f"{document_id}.pdf"),
        generator_id="e002-generator/1",
        generation_seed="20260101",
        generated_at=date(2026, 1, 1),
        fixture_hashes=("sha256:" + "1" * 64,),
        roster_hash="sha256:" + "2" * 64,
    )


def specification(document_id: str) -> DocumentRecord:
    return DocumentRecord(
        document_id=document_id,
        document_type="specification",
        project_id="PRJ-000",
        title=document_id,
        source_kind="REAL",
        license_basis="{}",
        content_hash="sha256:" + "3" * 64,
        path=Path(f"{document_id}.pdf"),
        source_ref="https://example.invalid/ufgs",
        issuing_body="UFGS",
        retrieval_date=date(2026, 1, 1),
    )


# ---------------------------------------------------------------------------
# FR-022 — the partition, and the exclusion as a record
# ---------------------------------------------------------------------------


def test_extraction_reaches_transmittals_and_no_specification() -> None:
    scope = select_extraction_documents(
        [transmittal("prj-001-t0001-r0"), specification("ufgs-23-52-00"), transmittal("doc-t2")]
    )
    assert scope.attempted_ids == ("prj-001-t0001-r0", "doc-t2")
    assert scope.excluded_ids == ("ufgs-23-52-00",)
    assert scope.population == 3


def test_the_partition_covers_every_enumerated_document() -> None:
    """A document in neither branch is one nobody extracted and nobody noticed."""
    records = [transmittal("doc-t1"), specification("doc-s1"), specification("doc-s2")]
    scope = select_extraction_documents(records)
    assert len(scope.attempted) + len(scope.excluded) == len(records)


def test_a_type_and_layer_that_disagree_stop_the_run() -> None:
    """The reference set exists only on the synthetic layer (FR-067), so a real
    transmittal would be extracted and then measured against nothing."""
    record = DocumentRecord(
        document_id="odd-one",
        document_type="transmittal",
        project_id="PRJ-000",
        title="odd",
        source_kind="REAL",
        license_basis="{}",
        content_hash="sha256:" + "4" * 64,
        path=Path("odd.pdf"),
        source_ref="https://example.invalid/x",
        issuing_body="UFGS",
        retrieval_date=date(2026, 1, 1),
    )
    with pytest.raises(OrchestrationError, match="FR-022"):
        select_extraction_documents([record])


def test_the_exclusion_section_publishes_the_reason_and_both_counts() -> None:
    """FR-022: recorded explicitly rather than left to be interpreted.

    An empty extraction table is also what a broken extractor produces. The
    section is what makes the two distinguishable, so it must carry the reason
    and the enumerated identifiers rather than only the count.
    """
    scope = select_extraction_documents([transmittal("doc-t1"), specification("ufgs-23-52-00")])
    section = exclusion_section(run_id="run-1", scope=scope)
    assert section.item == 5
    assert EXCLUSION_REASON in section.body
    assert "ufgs-23-52-00" in section.body
    assert [figure.value for figure in section.figures] == [1, 1]
    (check,) = section.total_checks
    assert check.count == 2


def test_the_exclusion_section_refuses_an_empty_corpus() -> None:
    """FR-068: a total check that enumerated nothing has not passed."""
    with pytest.raises(ReportError):
        exclusion_section(run_id="run-1", scope=ExtractionScope(attempted=(), excluded=()))


def test_documents_by_layer_counts_both_layers() -> None:
    counts = documents_by_layer(
        [transmittal("doc-t1"), transmittal("doc-t2"), specification("doc-s1")]
    )
    assert counts == {"REAL": 1, "SYNTHETIC": 2}


# ---------------------------------------------------------------------------
# FR-070 — one trace identifier, and the reconciliation
# ---------------------------------------------------------------------------


def test_a_minted_trace_id_is_inside_the_gateway_domain() -> None:
    """TR-047: 32 lowercase hexadecimal characters, never all zero."""
    trace = RunTrace.mint()
    assert len(trace.trace_id) == 32
    assert trace.trace_id == trace.trace_id.lower()
    assert int(trace.trace_id, 16) >= 0
    assert trace.trace_id != "0" * 32


@pytest.mark.parametrize("value", ["0" * 32, "abc", "0123456789ABCDEF0123456789abcdef", ""])
def test_a_trace_id_outside_the_domain_is_refused(value: str) -> None:
    with pytest.raises(OrchestrationError, match="FR-070"):
        RunTrace(trace_id=value)


def test_the_attempted_count_is_derived_from_the_partition_not_from_a_counter() -> None:
    """A counter incremented by the loop that issues the calls would be the same
    number twice: a loop that skipped a chunk would decrement its own
    expectation along with the work it skipped."""
    scope = select_extraction_documents(
        [transmittal("doc-t1"), transmittal("doc-t2"), specification("doc-s")]
    )
    assert attempted_invocation_count(scope, {"doc-t1": 5, "doc-t2": 3, "doc-s": 40}) == 8


def test_a_document_with_no_chunk_count_stops_the_ledger() -> None:
    scope = select_extraction_documents([transmittal("doc-t1")])
    with pytest.raises(OrchestrationError, match="FR-069"):
        attempted_invocation_count(scope, {})


def test_the_reconciliation_section_publishes_both_counts_and_the_verdict() -> None:
    section = reconciliation_section(run_id="run-1", trace_id=TRACE_ID, attempted=120, recorded=120)
    assert section.item == 15
    assert [figure.value for figure in section.figures] == [120, 120]
    assert TRACE_ID in section.body
    assert section.total_checks[0].outcome == "held"


def test_a_failing_reconciliation_still_publishes_both_counts() -> None:
    """The failure is published, not raised: SC-011 requires both counts
    published *and* required equal, and raising would prevent the counts from
    ever being published — the one outcome the requirement rules out. A report
    that omitted them when they disagreed would only ever show reconciliations
    that worked.

    The signed difference is asserted rather than only the word "disagree": a
    reader has to be able to tell "more recorded than attempted" — a request
    issued outside the run's ledger — from "fewer" — an attempted invocation
    that left no row. The two are different defects and the sign is what
    separates them.
    """
    section = reconciliation_section(run_id="run-1", trace_id=TRACE_ID, attempted=120, recorded=118)
    assert [figure.value for figure in section.figures] == [120, 118]
    assert "disagree by -2" in section.body
    assert section.total_checks[0].outcome == "FAILED"

    surplus = reconciliation_section(run_id="run-1", trace_id=TRACE_ID, attempted=120, recorded=127)
    assert "disagree by +7" in surplus.body


def test_the_reconciliation_section_refuses_a_zero_attempt_run() -> None:
    """It agrees with zero recorded for no reason at all."""
    with pytest.raises(ReportError, match="FR-070"):
        reconciliation_section(run_id="run-1", trace_id=TRACE_ID, attempted=0, recorded=0)


def test_the_reconciliation_section_refuses_a_negative_count() -> None:
    """The other half of the same guard. A negative `recorded` is not a
    reconciliation that failed, it is a count that was computed wrongly, and
    publishing it as a figure would put a nonsense number in the report."""
    with pytest.raises(ReportError, match="non-negative"):
        reconciliation_section(run_id="run-1", trace_id=TRACE_ID, attempted=120, recorded=-1)
    with pytest.raises(ReportError, match="non-negative"):
        reconciliation_section(run_id="run-1", trace_id=TRACE_ID, attempted=-1, recorded=0)


# ---------------------------------------------------------------------------
# The recorded side of the reconciliation
# ---------------------------------------------------------------------------


class _Cursor:
    def __init__(self, row: tuple[int] | None) -> None:
        self.row = row
        self.executed: list[tuple[str, tuple[str, ...]]] = []

    def __enter__(self) -> _Cursor:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def execute(self, statement: str, parameters: tuple[str, ...]) -> None:
        self.executed.append((statement, parameters))

    def fetchone(self) -> tuple[int] | None:
        return self.row


class _Connection:
    def __init__(self, cursor: _Cursor) -> None:
        self._cursor = cursor

    def cursor(self) -> _Cursor:
        return self._cursor


def test_recorded_invocations_are_counted_on_the_trace_id_alone() -> None:
    """The run record's `run_trace_id` is what joins the two sides. Filtering on
    anything else would reconcile against a set the run record cannot name."""
    cursor = _Cursor((42,))
    assert count_recorded_invocations(_Connection(cursor), TRACE_ID) == 42
    statement, parameters = cursor.executed[0]
    assert "llm_invocation" in statement
    assert parameters == (TRACE_ID,)


def test_a_count_query_returning_no_row_is_refused() -> None:
    """`count(*)` cannot return no row, so this means the connection is not
    addressing a migrated database."""
    with pytest.raises(OrchestrationError, match="FR-070"):
        count_recorded_invocations(_Connection(_Cursor(None)), TRACE_ID)
