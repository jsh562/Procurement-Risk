"""FR-010 / FR-068: every chunk is on the page it names, over the whole corpus.

**Total, not sampled.** Every chunk of all 51 enumerated documents is checked,
and the population and its count are published by the assertions themselves
rather than described in prose. A sampled version of this check would leave the
one property every citation in the system rests on — that a chunk's recorded
page number addresses the page its text is actually on — established for some
chunks and assumed for the rest.

**What "independent extraction" means here, exactly.** The corpus is chunked in
full first; only then is each document re-read from its own bytes, and each
chunk is looked up **by the page number recorded on it** rather than by its
position in any list the chunking produced. So the second extraction consults
none of the first run's state: not its cached page text, not its page-to-chunk
mapping, not the order it happened to build. `parse.page_by_number` exists for
this and raises rather than falling back to an index.

**What it does not mean, stated rather than implied.** It is **not** independent
of the parser. FR-008 pins one reader for the whole repository and both sides of
this comparison come through it, so a page the reader reads wrongly is read
wrongly twice and this check cannot see it. What is established is the
*attribution* — the recorded page number addresses a page containing the chunk —
and nothing about whether that page was extracted correctly. That residual is
carried by FR-011's inspection bound and is disclosed in the ingestion report,
not closed here.

**Two actors discharge FR-010 and this file is the second.** The first is the
ingestion job, which runs the same containment check inside each document's
transaction (`model/ingest/writer.py`) so a mis-attributed chunk is never
committed. This suite re-asserts it corpus-wide after the fact, which is what
catches a guard that was skipped rather than a guard that failed.

**Cost, stated because it is real.** Chunking the corpus and re-reading it are
each roughly two minutes, so this module is the slowest in the entry. That is
the price of the word "every" in FR-010; sampling to make it cheap would change
what the requirement says.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from model.corpus.manifest import LAYER_REAL, LAYER_SYNTHETIC
from model.ingest.chunker import Chunk, DocumentChunking, chunk_document
from model.ingest.documents import DocumentRecord, build_documents
from model.ingest.manifest_reader import iter_entries
from model.ingest.parse import ParseError, read_pages
from model.ingest.writer import WriterError, verify_page_containment

#: E002 committed 26 real specifications and 25 synthetic transmittals. Written
#: down so a corpus that silently shrank is a failure here rather than a check
#: that quietly ranged over less.
EXPECTED_DOCUMENTS = 51


@dataclass(frozen=True)
class DocumentContainment:
    """One document's chunking and the verdict on it."""

    record: DocumentRecord
    chunking: DocumentChunking
    population: str
    count: int
    misses: tuple[str, ...]


@pytest.fixture(scope="module")
def corpus_containment() -> tuple[DocumentContainment, ...]:
    """Chunk the whole corpus, then check every chunk against a fresh read.

    Module-scoped because the work is the expensive part and every assertion
    below ranges over the same enumeration; splitting it per test would read the
    corpus once per test rather than once.

    The two phases are ordered deliberately: **all** chunking happens before
    **any** re-read, so the second extraction is post-run in the sense FR-010
    means and not merely a second call inside one document's processing.
    """
    records = build_documents(iter_entries())
    chunkings = [(record, chunk_document(record)) for record in records]

    checked: list[DocumentContainment] = []
    for record, chunking in chunkings:
        fresh = read_pages(record.path)
        result = verify_page_containment(record.document_id, chunking.chunks, fresh)
        checked.append(
            DocumentContainment(
                record=record,
                chunking=chunking,
                population=result.population,
                count=result.count,
                misses=tuple(str(miss) for miss in result.misses),
            )
        )
    return tuple(checked)


def test_the_enumerated_population_is_the_whole_corpus_and_is_not_empty(
    corpus_containment: tuple[DocumentContainment, ...],
) -> None:
    """FR-068: the check publishes what it ranged over, and zero is a failure.

    Both halves are asserted. A check that enumerated nothing reports no
    violations for the same reason a check over a correct corpus does, so the
    non-emptiness is the assertion that keeps the next test's pass meaningful.
    """
    assert len(corpus_containment) == EXPECTED_DOCUMENTS, (
        f"the containment check enumerated {len(corpus_containment)} documents; "
        f"E002 committed {EXPECTED_DOCUMENTS}"
    )
    layers = {entry.record.source_kind for entry in corpus_containment}
    assert layers == {LAYER_REAL, LAYER_SYNTHETIC}, "the population spans both layers"

    chunks = sum(entry.count for entry in corpus_containment)
    assert chunks > 0, "FR-068: an empty population fails rather than passes"
    assert all(entry.count > 0 for entry in corpus_containment), (
        "a document contributing zero chunks would be exempt from every rule asserted "
        "over chunks: "
        f"{[e.record.document_id for e in corpus_containment if e.count == 0]}"
    )
    assert all(entry.population for entry in corpus_containment)


def test_every_chunk_is_contained_in_a_fresh_extraction_of_the_page_it_names(
    corpus_containment: tuple[DocumentContainment, ...],
) -> None:
    """FR-010, over every chunk of all 51 documents.

    The failure message carries the population and the count alongside the
    misses, because a containment failure is only actionable next to the size of
    what was checked.
    """
    misses = [miss for entry in corpus_containment for miss in entry.misses]
    checked = sum(entry.count for entry in corpus_containment)
    assert not misses, (
        f"FR-010: {len(misses)} of {checked} chunks across {len(corpus_containment)} "
        f"documents are absent from a fresh extraction of the page they name. "
        f"First five: {misses[:5]}"
    )


def test_the_page_is_addressed_by_its_recorded_number_not_by_list_position(
    corpus_containment: tuple[DocumentContainment, ...],
) -> None:
    """The failing direction, demonstrated rather than asserted.

    A containment check that always passed would be indistinguishable from this
    one on a correct corpus, so a chunk is deliberately re-labelled with a page
    it is not on and the check must object. The re-label also shows the lookup
    is by *recorded number*: the substituted number addresses a different page
    of the same document, which a positional index would never notice.
    """
    entry = next(e for e in corpus_containment if len(e.chunking.chunks) > 1)
    pages = read_pages(entry.record.path)
    assert len(pages) > 1, "the demonstration needs a document with two pages"

    original = entry.chunking.chunks[0]
    elsewhere = next(page.number for page in pages if page.number != original.page_number)
    relabelled = Chunk(
        document_id=original.document_id,
        document_type=original.document_type,
        project_id=original.project_id,
        page_number=elsewhere,
        ordinal=original.ordinal,
        body_text=original.body_text,
        boundary_class=original.boundary_class,
        structural_identifier=original.structural_identifier,
        spec_section=original.spec_section,
        heading=original.heading,
        content_pieces=original.content_pieces,
    )
    result = verify_page_containment(entry.record.document_id, [relabelled], pages)
    assert not result.holds, (
        f"{entry.record.document_id} ordinal {original.ordinal} was re-labelled onto page "
        f"{elsewhere} and the check accepted it"
    )
    assert result.count == 1
    assert result.misses[0].page_number == elsewhere


def test_a_page_number_no_page_carries_is_refused_rather_than_defaulted(
    corpus_containment: tuple[DocumentContainment, ...],
) -> None:
    """A recorded page outside the document raises instead of silently passing.

    `page_by_number` has no fallback to a positional index, and this is what
    says so: an out-of-range page number is a `ParseError`, not a lookup that
    quietly lands on the last page and finds the text there.
    """
    entry = corpus_containment[0]
    pages = read_pages(entry.record.path)
    original = entry.chunking.chunks[0]
    beyond = Chunk(
        document_id=original.document_id,
        document_type=original.document_type,
        project_id=original.project_id,
        page_number=len(pages) + 99,
        ordinal=original.ordinal,
        body_text=original.body_text,
        boundary_class=original.boundary_class,
        structural_identifier=original.structural_identifier,
        spec_section=original.spec_section,
        heading=original.heading,
        content_pieces=original.content_pieces,
    )
    with pytest.raises(ParseError):
        verify_page_containment(entry.record.document_id, [beyond], pages)


def test_an_empty_population_fails_rather_than_passes(
    corpus_containment: tuple[DocumentContainment, ...],
) -> None:
    """FR-068 at the one place it can still abort a write.

    A document that produced no chunk would otherwise pass this check
    vacuously — nothing to contradict — and then be committed as a document
    with nothing in it. The guard the writer runs inside the transaction raises
    instead.
    """
    entry = corpus_containment[0]
    pages = read_pages(entry.record.path)
    with pytest.raises(WriterError, match="FR-068"):
        verify_page_containment(entry.record.document_id, [], pages)
