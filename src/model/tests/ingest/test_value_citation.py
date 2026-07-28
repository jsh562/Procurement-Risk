"""FR-029: the citation is inherited from the chunk, and anchored on the value.

T046's half of the requirement — the derivation. The storage half is
`fk_extracted_value__chunk_page`, a composite foreign key against `chunk
(chunk_id, page_number)`, which is what makes a disagreeing citation *unstorable*
rather than merely detectable; the contributing-chunk rows are T066's and the
seeded page-split document is T068's.

What is asserted here is that a disagreeing citation cannot be **constructed**:
there is no parameter through which a caller supplies a page, so the cited page
is the anchor chunk's or it is nothing.
"""

from __future__ import annotations

import pytest

from model.compute.confidence import ParseSignals
from model.ingest.writer import CitedChunk, PreparedValue, ValueCitation, WriterError, cite_value


def test_a_single_chunk_value_cites_its_own_chunks_page() -> None:
    citation = cite_value(CitedChunk(ordinal=7, page_number=2))
    assert citation.cited_page == 2
    assert citation.source_chunk_count == 1
    assert citation.provenance_kind == "single_chunk"
    assert citation.contributors == ()


def test_a_page_split_value_anchors_on_the_chunk_printing_the_value() -> None:
    """FR-029: never the chunk carrying only the label.

    The label ends page 1 and the value begins page 2, so the anchor — and
    therefore the cited page — is the *later* page.
    """
    citation = cite_value(
        CitedChunk(ordinal=9, page_number=2), [CitedChunk(ordinal=8, page_number=1)]
    )
    assert citation.cited_page == 2
    assert citation.source_chunk_count == 2
    assert citation.provenance_kind == "multi_chunk"


def test_a_reassembly_orders_by_page_not_by_contributor_position() -> None:
    """SC-027. The anchor is the later page on a page-split value, so
    reassembling by contributor position would put the value before its label."""
    citation = cite_value(
        CitedChunk(ordinal=9, page_number=3),
        [CitedChunk(ordinal=8, page_number=2), CitedChunk(ordinal=7, page_number=1)],
    )
    assert citation.pages_in_reading_order() == (1, 2, 3)
    assert [chunk.page_number for chunk in citation.contributors] == [1, 2]


def test_the_provenance_kind_is_derived_and_cannot_disagree_with_the_count() -> None:
    """`ck_extracted_value__provenance_agrees_with_count` is a biconditional in
    the database. Deriving one from the other makes disagreement
    unrepresentable rather than rejected."""
    assert cite_value(CitedChunk(0, 1)).provenance_kind == "single_chunk"
    assert cite_value(CitedChunk(1, 2), [CitedChunk(0, 1)]).provenance_kind == "multi_chunk"


def test_the_anchor_never_appears_among_its_own_contributors() -> None:
    """The anchor is contributor 1 by definition; a second row for it would make
    `source_chunk_count` disagree with the rows that explain it."""
    with pytest.raises(WriterError, match="FR-029"):
        ValueCitation(anchor=CitedChunk(5, 2), contributors=(CitedChunk(5, 2),))


def test_a_contributor_named_twice_is_refused() -> None:
    with pytest.raises(WriterError, match="FR-029"):
        ValueCitation(anchor=CitedChunk(5, 3), contributors=(CitedChunk(4, 2), CitedChunk(4, 2)))


def test_a_contributor_after_the_anchors_page_is_refused() -> None:
    """Either the anchor is the label's chunk rather than the value's, or the
    value continues past the chunk cited for it. Both are FR-029 violations and
    neither is a citation."""
    with pytest.raises(WriterError, match="FR-029"):
        ValueCitation(anchor=CitedChunk(4, 1), contributors=(CitedChunk(5, 2),))


def test_page_and_ordinal_domains_are_checked_at_construction() -> None:
    with pytest.raises(WriterError):
        CitedChunk(ordinal=-1, page_number=1)
    with pytest.raises(WriterError):
        CitedChunk(ordinal=0, page_number=0)


# ---------------------------------------------------------------------------
# The row the citation ends up on
# ---------------------------------------------------------------------------


def prepared(**overrides: object) -> PreparedValue:
    fields: dict[str, object] = {
        "field_name": "manufacturer",
        "value_kind": "text",
        "value_text": "Norhelm Transformer Wks.",
        "value_number": None,
        "confidence": 1.0,
        "citation": cite_value(CitedChunk(3, 2)),
        "signals": ParseSignals("canonical", 1, False),
    }
    fields.update(overrides)
    return PreparedValue(**fields)  # type: ignore[arg-type]


def test_the_signal_row_cannot_disagree_with_the_citations_chunk_count() -> None:
    """FR-063: the page-split signal *is* the value's own `source_chunk_count`.

    `fk_extracted_value_parse_signal__value_count` holds the two equal in the
    database; this makes the disagreeing pair unconstructible, so the deduction
    can never be computed from a copy that drifted from the provenance.
    """
    with pytest.raises(WriterError, match="parse signal records"):
        prepared(
            citation=cite_value(CitedChunk(3, 2), [CitedChunk(2, 1)]),
            signals=ParseSignals("canonical", 1, False),
        )
    # The agreeing pair is accepted, so the refusal above is about the
    # disagreement rather than about multi-chunk values in general.
    assert (
        prepared(
            citation=cite_value(CitedChunk(3, 2), [CitedChunk(2, 1)]),
            signals=ParseSignals("canonical", 2, False),
        ).signals.page_split
        is True
    )


def test_a_blank_value_cannot_be_prepared() -> None:
    """`ck_extracted_value__value_text_present`, and FR-037: a field that is not
    printed is `no_value_found` rather than a blank value."""
    with pytest.raises(WriterError, match="blank value"):
        prepared(value_text="   ")


def test_the_typed_numeric_is_populated_exactly_on_number_kinds() -> None:
    with pytest.raises(WriterError, match="biconditional"):
        prepared(value_kind="number", value_number=None)
    with pytest.raises(WriterError, match="biconditional"):
        prepared(value_kind="text", value_number=12)


@pytest.mark.parametrize("confidence", [-0.01, 1.01])
def test_a_confidence_outside_the_stored_range_is_refused(confidence: float) -> None:
    """`ck_extracted_value__confidence_range` admits [0.0, 1.0] inclusive at both
    ends — a genuinely certain extraction reports 1.0 and a genuinely worthless
    one reports 0.0."""
    with pytest.raises(WriterError, match="confidence_range"):
        prepared(confidence=confidence)
