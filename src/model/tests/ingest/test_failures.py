"""FR-034 / FR-035 / FR-036 / FR-037: the closed seven, the five, and absence.

T061 and T062. Three properties are checked that nothing else in the repository
checks:

1. The seven outcomes this module admits are the seven revision `0006` admits —
   read out of the revision's own `CHECK` rather than transcribed twice and
   hoped about. FR-034 says "MUST NOT introduce a new outcome value", and a
   restatement nobody compares is how a new one arrives.
2. A failure record carries none of the value or confidence columns, in both
   directions: not among the columns it is written to, and not among the fields
   it declares.
3. A field the document does not print produces exactly one record for the
   document, naming the lowest-ordinal chunk it was attempted on — and every
   way of producing *no* record for it is refused rather than defaulted.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from model.ingest.failures import (
    FAILURE_COLUMNS,
    FAILURE_OUTCOMES,
    FORBIDDEN_COLUMNS,
    OUTCOME_NO_VALUE_FOUND,
    REQUIRED_FIELDS,
    AttemptedChunk,
    ExtractionFailure,
    FailureError,
    absent_field_records,
    outcome_counts,
)

ENTRY_ROOT = Path(__file__).resolve().parents[2]
EXTRACTION_REVISION = ENTRY_ROOT / "src" / "model" / "schema" / "versions" / "0006_extraction.py"

DOCUMENT_ID = "prj-001-t0004-r1"
CHUNKS = (AttemptedChunk(ordinal=3, page_number=2), AttemptedChunk(ordinal=1, page_number=1))
ATTEMPTED = ("submittal_number", "manufacturer", "part_number", "quantity")


def admitted_outcomes() -> tuple[str, ...]:
    """The outcome values `ck_extraction_failure__outcome` admits.

    Parsed from the revision source rather than from a live database: the
    comparison is between two declarations, and requiring a migrated database
    would leave it unchecked on every run that has no server.
    """
    source = EXTRACTION_REVISION.read_text(encoding="utf-8")
    body = source.split("ck_extraction_failure__outcome", 1)[1].split("),", 1)[0]
    return tuple(re.findall(r"'([a-z_]+)'", body))


def failure(**overrides: object) -> ExtractionFailure:
    values: dict[str, object] = {
        "source_chunk": AttemptedChunk(ordinal=2, page_number=1),
        "field_name": "manufacturer",
        "outcome": "type_coercion_failed",
        "repair_attempt_count": 0,
        "detail": "'approx. 12' is not a number as printed",
    }
    values.update(overrides)
    return ExtractionFailure(**values)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# FR-034 — the closed set of seven
# ---------------------------------------------------------------------------


def test_the_seven_outcomes_are_the_ones_the_schema_admits() -> None:
    """Compared, not transcribed twice. Where the two disagree the schema
    governs ({SAD:ADR-0017}), so a difference is this module's defect."""
    assert set(FAILURE_OUTCOMES) == set(admitted_outcomes())
    assert len(FAILURE_OUTCOMES) == 7


def test_an_eighth_outcome_is_refused() -> None:
    """FR-034: no new outcome value. An eighth is a migration and an amendment."""
    with pytest.raises(FailureError, match="closed set of seven"):
        failure(outcome="model_declined")


def test_every_outcome_is_constructible_so_none_is_dead() -> None:
    """A member nothing can produce is a member in name only, and the report
    would publish its zero as a fact rather than as an absence of failures."""
    for outcome in FAILURE_OUTCOMES:
        assert failure(outcome=outcome).outcome == outcome


def test_the_outcome_breakdown_publishes_a_zero_for_an_untaken_outcome() -> None:
    """FR-034, SC-018: an outcome no failure took is published as a zero.

    An empty input yields seven zeros rather than an empty table — which is the
    difference between "no failure took this outcome" and "this outcome was
    forgotten".
    """
    assert outcome_counts(()) == dict.fromkeys(FAILURE_OUTCOMES, 0)
    counts = outcome_counts(
        (failure(), failure(), failure(outcome="schema_violation", repair_attempt_count=0))
    )
    assert counts["type_coercion_failed"] == 2
    assert counts["schema_violation"] == 1
    assert counts["missing_citation"] == 0
    assert set(counts) == set(FAILURE_OUTCOMES)
    assert sum(counts.values()) == 3


# ---------------------------------------------------------------------------
# FR-035 — the five required fields
# ---------------------------------------------------------------------------


def test_the_five_required_fields_are_all_present_on_a_record() -> None:
    """Named as the requirement names them, so the list can be asserted over
    rather than five separate spellings being trusted to agree."""
    record = failure()
    assert REQUIRED_FIELDS == (
        "source_chunk",
        "attempted_page",
        "field_name",
        "repair_attempt_count",
        "detail",
    )
    assert record.source_chunk.ordinal == 2
    assert record.attempted_page == 1
    assert record.field_name == "manufacturer"
    assert record.repair_attempt_count == 0
    assert record.detail


def test_the_attempted_page_is_inherited_from_the_chunk_and_not_supplied() -> None:
    """`fk_extraction_failure__chunk_page` would refuse a disagreeing pair; this
    makes the pair unconstructible, so the attempt is as traceable as a
    success."""
    record = failure(source_chunk=AttemptedChunk(ordinal=9, page_number=4))
    assert record.attempted_page == 4
    assert record.row_values("chunk-id")[1] == 4


def test_a_blank_detail_is_refused() -> None:
    """`ck_extraction_failure__detail_present`. A record explaining nothing
    defeats the point of recording it."""
    with pytest.raises(FailureError, match="blank diagnostic detail"):
        failure(detail="   \t ")


def test_a_negative_repair_count_is_refused() -> None:
    with pytest.raises(FailureError, match="non_negative"):
        failure(repair_attempt_count=-1)


def test_a_blank_field_name_is_refused() -> None:
    with pytest.raises(FailureError, match="names the field"):
        failure(field_name="  ")


def test_chunk_ordinal_and_page_domains_are_checked_at_construction() -> None:
    with pytest.raises(FailureError, match="zero-based"):
        AttemptedChunk(ordinal=-1, page_number=1)
    with pytest.raises(FailureError, match="one-based"):
        AttemptedChunk(ordinal=0, page_number=0)


# ---------------------------------------------------------------------------
# FR-036 — no value and no confidence
# ---------------------------------------------------------------------------


def test_a_failure_row_carries_no_value_and_no_confidence_column() -> None:
    """Both directions. A failure with a value attached is a value stored while
    being reported as absent — the invisible corruption Principle III targets."""
    assert not set(FAILURE_COLUMNS) & FORBIDDEN_COLUMNS
    assert "confidence" in FORBIDDEN_COLUMNS
    assert "value_text" in FORBIDDEN_COLUMNS


def test_the_record_type_declares_no_value_or_confidence_field() -> None:
    declared = set(ExtractionFailure.__dataclass_fields__)
    assert not declared & FORBIDDEN_COLUMNS
    with pytest.raises(TypeError):
        ExtractionFailure(  # type: ignore[call-arg]
            source_chunk=AttemptedChunk(1, 1),
            field_name="manufacturer",
            outcome="no_value_found",
            repair_attempt_count=0,
            detail="absent",
            confidence=0.9,
        )


def test_the_written_row_is_exactly_the_declared_columns() -> None:
    values = failure().row_values("a-chunk-id")
    assert len(values) == len(FAILURE_COLUMNS)
    assert dict(zip(FAILURE_COLUMNS, values, strict=True))["outcome"] == "type_coercion_failed"


# ---------------------------------------------------------------------------
# FR-037 / FR-058 — absence, once per document, on the lowest-ordinal chunk
# ---------------------------------------------------------------------------


def test_an_unprinted_field_is_recorded_once_for_the_document() -> None:
    """FR-037: recorded rather than defaulted, inferred, or omitted silently."""
    records = absent_field_records(
        document_id=DOCUMENT_ID,
        attempted_fields=ATTEMPTED,
        attempted_chunks=CHUNKS,
        fields_with_values={"manufacturer", "part_number"},
    )
    assert [record.field_name for record in records] == ["submittal_number", "quantity"]
    assert {record.outcome for record in records} == {OUTCOME_NO_VALUE_FOUND}
    assert all(record.repair_attempt_count == 0 for record in records)


def test_the_record_names_the_lowest_ordinal_chunk_and_its_page() -> None:
    """FR-058's stated convention, and it is a convention rather than a claim.

    The chunks are supplied out of order here on purpose: the anchor is the
    lowest *ordinal*, not the first element, and FR-015's contiguous zero-based
    ordinals are what make it deterministic.
    """
    (record,) = absent_field_records(
        document_id=DOCUMENT_ID,
        attempted_fields=("quantity",),
        attempted_chunks=CHUNKS,
        fields_with_values=(),
    )
    assert record.source_chunk == AttemptedChunk(ordinal=1, page_number=1)
    assert record.attempted_page == 1
    assert "lowest-ordinal chunk" in record.detail


def test_a_field_found_anywhere_in_the_document_is_not_reported_absent() -> None:
    """Once per document means the whole document decides, not one chunk: a
    field printed on the last page is not absent from the first."""
    records = absent_field_records(
        document_id=DOCUMENT_ID,
        attempted_fields=ATTEMPTED,
        attempted_chunks=CHUNKS,
        fields_with_values=set(ATTEMPTED),
    )
    assert records == ()


def test_a_document_with_no_attempted_chunk_is_refused_not_defaulted() -> None:
    """`extraction_failure.source_chunk_id` is NOT NULL, so a record naming no
    chunk is unstorable. A document that produced no chunk is a run-level
    failure, not a document with four absent fields."""
    with pytest.raises(FailureError, match="no attempted chunk"):
        absent_field_records(
            document_id=DOCUMENT_ID,
            attempted_fields=ATTEMPTED,
            attempted_chunks=(),
            fields_with_values=(),
        )


def test_an_empty_attempted_set_is_refused() -> None:
    """Every field would be absent and none recorded — a vocabulary or
    configuration failure wearing the shape of a document with nothing on it."""
    with pytest.raises(FailureError, match="attempted zero fields"):
        absent_field_records(
            document_id=DOCUMENT_ID,
            attempted_fields=(),
            attempted_chunks=CHUNKS,
            fields_with_values=(),
        )


def test_a_repeated_attempted_term_is_refused() -> None:
    """FR-058 records an absent field once per document; a repeated term would
    record it twice and inflate the per-attempt denominator."""
    with pytest.raises(FailureError, match="repeats a term"):
        absent_field_records(
            document_id=DOCUMENT_ID,
            attempted_fields=("manufacturer", "manufacturer"),
            attempted_chunks=CHUNKS,
            fields_with_values=(),
        )


def test_a_value_for_a_field_nobody_attempted_is_refused() -> None:
    """FR-069: an attempt ledger with a value nothing attempted does not
    reconcile, and quietly ignoring it would close the hole by not looking."""
    with pytest.raises(FailureError, match="does not reconcile"):
        absent_field_records(
            document_id=DOCUMENT_ID,
            attempted_fields=ATTEMPTED,
            attempted_chunks=CHUNKS,
            fields_with_values={"unit_price"},
        )
