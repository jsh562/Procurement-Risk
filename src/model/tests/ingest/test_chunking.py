"""Unit tier for the ladder: tokenizer budget, segmentation, structure, chunks.

`plan.md` §Testing Strategy places the chunker ladder, the tokenizer budget and
segmentation in the **test-after unit tier** and says why: their output is a
boundary, a count and a digest — ingestion work — and their correctness over the
corpus is carried by SC-004, SC-007 and SC-038 as total assertions, not by
properties over a generated domain.

Fabricated pages are used wherever the rule under test is about the rule, so a
failure names the rule rather than a document. The two assertions that must hold
over real bytes — every corpus chunk fits the budget, every chunk lies on one
page — are made against the corpus itself.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest

from model.corpus.paths import REPO_ROOT
from model.ingest.chunker import (
    BOUNDARY_CLASSES,
    CHUNKER_VERSION,
    ChunkerError,
    chunk_pages,
)
from model.ingest.cli import oversized_sentence
from model.ingest.documents import DocumentRecord
from model.ingest.parse import ParsedPage, read_pages
from model.ingest.segment import SEGMENTER_VERSION, sentences
from model.ingest.structure import detect_document, detect_page
from model.ingest.tokens import (
    CONTENT_TOKEN_BUDGET,
    content_pieces,
    effective_sequence_cap,
    encoder_tokenizer,
    fits_budget,
    special_token_overhead,
)

SECTION = "SECTION 23 52 00"


def page(number: int, *lines: str) -> ParsedPage:
    return ParsedPage(number=number, lines=tuple(lines), text="\n".join(lines))


def record(document_id: str = "ufgs-23-52-00") -> DocumentRecord:
    return DocumentRecord(
        document_id=document_id,
        document_type="specification",
        project_id="PRJ-000",
        title="UFGS 23 52 00",
        source_kind="REAL",
        license_basis='{"basis_id":"us-gov-17usc105-ufgs"}',
        content_hash="sha256:" + "0" * 64,
        path=Path("unused.pdf"),
        source_ref="https://example.invalid/x.pdf",
        issuing_body="USACE",
        retrieval_date=date(2026, 7, 26),
    )


# ---------------------------------------------------------------------------
# FR-014 / AD-004 / HINT-001 — the budget
# ---------------------------------------------------------------------------


def test_the_effective_cap_is_256_and_not_model_max_length() -> None:
    """HINT-001: `model_max_length` is 512 and is the wrong field."""
    assert effective_sequence_cap() == 256
    assert special_token_overhead() == 2
    assert CONTENT_TOKEN_BUDGET == effective_sequence_cap() - special_token_overhead() == 254


def test_content_pieces_excludes_the_special_tokens() -> None:
    """The content budget is what the *content* consumes."""
    text = "Provide modulating combustion controls with gas pilot ignition."
    with_specials = len(encoder_tokenizer().encode(text).ids)
    assert content_pieces(text) == with_specials - 2
    assert content_pieces("") == 0


def test_the_counting_tokenizer_does_not_truncate() -> None:
    """A truncating instrument would report the cap as the length.

    That is the exact failure the budget exists to prevent, arriving through the
    thing that measures it, so it is asserted rather than assumed.
    """
    long_text = " ".join(["reinforced concrete masonry unit"] * 400)
    assert content_pieces(long_text) > effective_sequence_cap()
    assert not fits_budget(long_text)


# ---------------------------------------------------------------------------
# AD-003 — segmentation
# ---------------------------------------------------------------------------


def test_segmentation_keeps_the_text_and_its_offsets() -> None:
    """`clean=False`, `char_span=True`: spans address the parent verbatim."""
    text = (
        "Provide controls conforming to ASTM A653/A653M. Maintain the furnace draft "
        "within 0.25 mm of water column. Use No. 2 oil where indicated."
    )
    found = sentences(text)
    assert len(found) >= 2
    for sentence in found:
        assert text[sentence.start : sentence.end] == sentence.text
    assert "".join(sentence.text for sentence in found).strip() == text.strip()


def test_an_undivided_unit_comes_back_whole() -> None:
    """FR-014's fail-closed leaf is only reachable if this returns one sentence."""
    text = "ASTM A653/A653M reinforced structural steel designation table entry"
    assert [sentence.text for sentence in sentences(text)] == [text]
    assert sentences("   ") == ()


# ---------------------------------------------------------------------------
# FR-012 / FR-013 / HINT-004 — structure, page first
# ---------------------------------------------------------------------------


def test_a_running_footer_is_not_a_section_marker() -> None:
    """`SECTION 23 52 00 Page 30` is furniture, and treating it as a marker
    resets the ladder once per page."""
    structure = detect_page(
        page(30, "2.4.7 Combustion Safety Controls", "Body line.", f"{SECTION} Page 30")
    )
    kinds = {unit.kind for unit in structure.units}
    assert kinds == {"paragraph"}


def test_lettered_items_are_siblings_not_a_nest() -> None:
    """`b.` follows `a.`; it does not sit inside it."""
    structure = detect_page(
        page(
            1,
            "2.4.6 Boiler Combustion Controls",
            "a. First item.",
            "b. Second item.",
            "c. Third item.",
        )
    )
    paragraph = structure.units[0]
    letters = [child.identifier for child in paragraph.children]
    assert letters == ["a", "b", "c"]
    assert all(child.level == paragraph.level + 1 for child in paragraph.children)


def test_a_body_line_opening_with_a_decimal_number_is_not_an_article() -> None:
    """`104.4 degrees C` is prose. Admitting it invents a unit."""
    structure = detect_page(
        page(1, "PART 2 PRODUCTS", "2.4 COMBUSTION CONTROL", "104.4 degrees C at the outlet.")
    )
    part = structure.units[0]
    assert [child.identifier for child in part.children] == ["2.4"]
    assert "104.4 degrees C at the outlet." in part.children[0].own_text


def test_a_unit_split_by_a_page_break_keeps_its_identifier() -> None:
    """FR-012: the fragment is still `2.4.7`, on the next page."""
    structures = detect_document(
        [
            page(
                1,
                "PART 2 PRODUCTS",
                "2.4 COMBUSTION CONTROL",
                "2.4.7 Safety Controls",
                "First half of the paragraph.",
            ),
            page(2, "Second half of the paragraph."),
        ]
    )
    carried = structures[1].units[0]
    identifiers = []
    unit = carried
    while unit is not None:
        identifiers.append(unit.identifier)
        unit = unit.children[0] if unit.children else None
    assert "2.4.7" in identifiers
    assert carried.continued is True


def test_a_run_of_label_lines_is_one_field_block() -> None:
    """The transmittal half of the detector."""
    structure = detect_page(
        page(
            1,
            "TRANSMITTAL OF SUBMITTAL DATA",
            "Project: PRJ-001 Thundervale",
            "Contract No.: DACA11-2026-C-1000",
            "Transmittal No.: PRJ-001-T0002",
        )
    )
    blocks = [unit for unit in structure.units if unit.kind == "field_block"]
    assert len(blocks) == 1
    assert len(blocks[0].lines) == 3


# ---------------------------------------------------------------------------
# FR-012 / FR-014 / FR-015 / FR-016 — the chunker
# ---------------------------------------------------------------------------


def test_ordinals_are_zero_based_contiguous_and_in_reading_order() -> None:
    """FR-015, including the page that yields nothing consuming no ordinal."""
    chunking = chunk_pages(
        record(),
        [
            page(1, "PART 1 GENERAL", "1.1 REFERENCES", "First body line."),
            page(2, "   "),
            page(3, "1.2 SUBMITTALS", "Second body line."),
        ],
    )
    assert [chunk.ordinal for chunk in chunking.chunks] == list(range(len(chunking.chunks)))
    assert [chunk.page_number for chunk in chunking.chunks] == sorted(
        chunk.page_number for chunk in chunking.chunks
    )
    assert 2 not in {chunk.page_number for chunk in chunking.chunks}


def test_bracketed_markup_is_stored_verbatim() -> None:
    """FR-016: an unresolved alternative is content, not markup to resolve."""
    line = "Provide [on-off] [high-low-off] [modulating] combustion controls."
    chunking = chunk_pages(record(), [page(1, "PART 2 PRODUCTS", "2.1 BOILERS", line)])
    assert any(line in chunk.body_text for chunk in chunking.chunks)


def test_every_boundary_class_is_from_the_closed_set() -> None:
    chunking = chunk_pages(record(), [page(1, "PART 1 GENERAL", "1.1 REFERENCES", "Body.")])
    assert all(chunk.boundary_class in BOUNDARY_CLASSES for chunk in chunking.chunks)
    assert set(chunking.boundary_class_counts) == set(BOUNDARY_CLASSES)


def test_an_over_long_leaf_is_cut_at_sentence_boundaries() -> None:
    """FR-014's third boundary class, and only inside a leaf above the window."""
    sentence = "Provide modulating combustion controls with a gas pilot ignition system. "
    chunking = chunk_pages(record(), [page(1, "PART 2 PRODUCTS", "2.1 BOILERS", sentence * 40)])
    assert any(chunk.boundary_class == "sentence" for chunk in chunking.chunks)
    assert all(chunk.content_pieces <= CONTENT_TOKEN_BUDGET for chunk in chunking.chunks)


def test_a_single_over_long_sentence_fails_the_run_naming_the_unit() -> None:
    """FR-014's one fail-closed case."""
    undivided = " ".join(["designation"] * 900)
    with pytest.raises(ChunkerError, match="FR-014") as raised:
        chunk_pages(record(), [page(1, "PART 2 PRODUCTS", "2.1 BOILERS", undivided)])
    assert "2.1" in str(raised.value)


def test_the_oversized_sentence_carries_its_three_subjects_as_attributes() -> None:
    """FR-056: the failure is classifiable, not only readable.

    `oversized_sentence` is one of the five kinds
    `ck_ingestion_run__failure_kind_domain` admits, and `cli.oversized_sentence`
    requires the document, the page and the structural unit to construct one.
    Interpolating them into prose and nothing else left the orchestrator holding
    a sentence it could not take those three values back out of, so it recorded
    the abort as unclassified and wrote no `run_failure_kind` — a classified
    abort reading as `in_flight` forever.

    Asserted here, at the raise site, rather than only where the run records it:
    the routing is driven by these attributes, and a raise site that stopped
    setting them would leave the routing correct and unreachable.
    """
    undivided = " ".join(["designation"] * 900)
    with pytest.raises(ChunkerError) as raised:
        chunk_pages(record("ufgs-26-05-13"), [page(4, "PART 2 PRODUCTS", "2.1 BOILERS", undivided)])

    error = raised.value
    assert error.is_oversized_sentence
    assert error.document_id == "ufgs-26-05-13"
    assert error.page_number == 4
    assert error.structural_unit == "2.1"

    failure = oversized_sentence(
        document_id=error.document_id,
        page_number=error.page_number,
        structural_unit=error.structural_unit,
    )
    assert failure.kind == "oversized_sentence"
    assert failure.recorded_detail.startswith("document in flight ufgs-26-05-13:")
    assert "page 4" in failure.recorded_detail and "'2.1'" in failure.recorded_detail


def test_a_chunker_failure_that_is_not_the_oversized_sentence_carries_no_subjects() -> None:
    """All three attributes or none — two of them cannot construct the failure.

    `ChunkerError` covers more than FR-014's case, and the others have no member
    among FR-056's five. A caller must be able to tell them apart without
    reading the message, which is exactly what the old code could not do.
    """
    error = ChunkerError("FR-015: ordinals are not contiguous from zero")
    assert not error.is_oversized_sentence
    assert (error.document_id, error.page_number, error.structural_unit) == (None, None, None)


def test_the_chunker_version_carries_the_segmenter_version() -> None:
    """FR-017: a pySBD upgrade is a chunker-version bump, mechanically."""
    assert SEGMENTER_VERSION in CHUNKER_VERSION
    assert CHUNKER_VERSION.startswith("e006-chunker/")


# ---------------------------------------------------------------------------
# The two claims that must hold over real bytes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "relative",
    [
        "data/corpus/real/ufgs/ufgs-23-52-43-00-20.pdf",
        "data/corpus/synthetic/PRJ-001/PRJ-001-T0002-R0.pdf",
    ],
)
def test_every_chunk_of_a_real_document_fits_one_page_and_the_budget(relative: str) -> None:
    """SC-004 in miniature: measured on bytes, not on a fabricated page."""
    path = REPO_ROOT / relative
    pages = read_pages(path)
    chunking = chunk_pages(record(document_id="probe-document"), pages)
    numbers = {p.number for p in pages}
    assert chunking.chunks
    for chunk in chunking.chunks:
        assert chunk.content_pieces <= CONTENT_TOKEN_BUDGET
        assert chunk.page_number in numbers
        assert chunk.body_text.strip()
