"""FR-029 / SC-027: the seeded page-split value cites the page that prints it.

T068, against **real corpus data**. E002 committed four synthetic transmittals
carrying the `PAGE_SPLIT_FIELD` class — a field whose label is the last text
object on page one and whose value is the first body line of page two — so this
module does not construct a fixture of the shape it wants to see. It reads the
committed documents through the one pinned reader, chunks them with the shipped
chunker, and asserts the citation the writer would build from what is actually
on the pages.

**The anchor is the chunk carrying the printed value, and that is the whole
requirement.** The job a citation exists for is reaching the page showing the
number in one step; a citation landing on `Submittal Descriptor:` with no
descriptor under it fails that literally, however defensible "the field starts
here" sounds. So the assertions below are not about which of two chunks is
tidier — they check that the cited page **contains the printed value** and that
the label's page **does not**, on documents nobody wrote for this test.

**The anchor is contributor 1 and never appears among the contributing rows.**
`ck_evcc__ordinal_min CHECK (contributor_ordinal >= 2)` is E003's and cannot be
widened by this epic, so a value split across one page break has exactly **one**
contributing row, and `source_chunk_count` is one plus the recorded contributor
count. Both are asserted here as arithmetic over the real citations rather than
as a rule quoted from the schema.

**SC-027's comparison is in ascending page order.** The anchor is the *later*
page, so a reassembly ordered by contributor position would put the value before
its own label. The expected side is the generator's **pre-render** page text —
FR-067's reference — never this epic's own parse of the page, which would be a
derived oracle against itself.

**No database.** Everything here is the derivation. The storage half is
`fk_extracted_value__chunk_page`, `fk_evcc__value_count` and `fk_evcc__chunk_page`,
exercised by the schema tier.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import pytest

from model.compute.confidence import ParseSignals
from model.corpus.derive import normalize_page_text
from model.corpus.generate import compose_layer
from model.corpus.manifest import SyntheticEntry
from model.corpus.templates import LABEL_SUFFIX
from model.ingest.chunker import Chunk, chunk_pages
from model.ingest.documents import DocumentRecord, build_documents, mint_document_id
from model.ingest.manifest_reader import iter_entries
from model.ingest.parse import read_pages
from model.ingest.report import page_split_section
from model.ingest.writer import (
    CONTRIBUTING_CHUNK_COLUMNS,
    FIRST_CONTRIBUTOR_ORDINAL,
    CitedChunk,
    PreparedValue,
    ValueCitation,
    WriterError,
    cite_value,
    contributing_rows,
    multi_chunk_counts,
)

#: E002's irregularity class this module ranges over. Named rather than
#: hard-coding "the four documents": the assertion below is that the corpus
#: carries at least one and that **every** one of them is exercised, so a corpus
#: that gained a fifth is covered and one that lost them all fails (FR-068).
PAGE_SPLIT_CLASS = "PAGE_SPLIT_FIELD"

#: `.../src/model/tests/ingest/test_page_split.py` — three levels up is
#: `src/model`. Resolved from `__file__` so the read works from any cwd.
ENTRY_ROOT = Path(__file__).resolve().parents[2]
CONTRIBUTING_REVISION = ENTRY_ROOT / "src" / "model" / "schema" / "versions" / "0006_extraction.py"


def declared_contributing_columns() -> tuple[str, ...]:
    """The `extracted_value_contributing_chunk` columns E003's `0006` creates.

    Read from the revision's own `CREATE TABLE` rather than from a live catalog,
    for the reason `test_parse_signal_write.py` gives: the comparison is between
    the writer's statement and the schema's declaration, and requiring a migrated
    database to make it would leave the statement unchecked on every run that has
    no server — which is most of them.
    """
    source = CONTRIBUTING_REVISION.read_text(encoding="utf-8")
    body = source.split("CREATE TABLE extracted_value_contributing_chunk (", 1)[1]
    body = body.split("CONSTRAINT", 1)[0]
    return tuple(
        match.group(1)
        for line in body.splitlines()
        if (match := re.match(r"\s{12}([a-z_]+)\s+(uuid|smallint|integer)\b", line))
    )


@dataclass(frozen=True)
class SeededSplit:
    """One committed page-split document, as the chunker and the plan see it.

    Carries both sides of SC-027's comparison: the chunks this epic cut, and the
    generator's pre-render page text, which is the expected side and comes from
    no step being measured.
    """

    document_id: str
    split_field: str
    label_text: str
    value_text: str
    label_page: int
    value_page: int
    pre_render_pages: tuple[str, ...]
    chunks: tuple[Chunk, ...]

    def chunk_printing(self, text: str, page: int) -> Chunk:
        """The one chunk of `page` whose body contains `text`.

        Raises:
            AssertionError: no chunk contains it, or more than one does. Both
                are failures rather than a first-match: two chunks containing
                the printed value would make "the chunk carrying the value"
                ambiguous, and the citation would anchor on whichever the
                enumeration happened to reach first.
        """
        needle = normalize_page_text(text)
        found = [
            chunk
            for chunk in self.chunks
            if chunk.page_number == page and needle in normalize_page_text(chunk.body_text)
        ]
        assert len(found) == 1, (
            f"{self.document_id}: {len(found)} chunks of page {page} contain {text!r}; "
            f"the citation anchor must be a single chunk"
        )
        return found[0]

    @property
    def anchor_chunk(self) -> Chunk:
        """FR-029's anchor: the chunk carrying the **printed value**."""
        return self.chunk_printing(self.value_text, self.value_page)

    @property
    def label_chunk(self) -> Chunk:
        """The chunk carrying only the field's label, on the earlier page."""
        return self.chunk_printing(self.label_text, self.label_page)

    def citation(self) -> ValueCitation:
        """The citation the writer builds for this value."""
        return cite_value(
            CitedChunk(self.anchor_chunk.ordinal, self.anchor_chunk.page_number),
            [CitedChunk(self.label_chunk.ordinal, self.label_chunk.page_number)],
        )

    def pre_render(self, page: int) -> str:
        return self.pre_render_pages[page - 1]


def _split_halves(pre_render_pages: tuple[str, ...]) -> tuple[str, str]:
    """The label ending page one and the value opening page two, as printed.

    Read off the **pre-render** model rather than off a parse: the generator
    appends the label after every other line of page one and inserts the value
    immediately after page two's citation anchor, which is what makes those two
    positions the definition of the class rather than a heuristic about it.
    """
    first = [line for line in pre_render_pages[0].splitlines() if line.strip()]
    second = [line for line in pre_render_pages[1].splitlines() if line.strip()]
    return first[-1].strip(), second[1].strip()


@pytest.fixture(scope="module")
def seeded_splits() -> tuple[SeededSplit, ...]:
    """Every committed `PAGE_SPLIT_FIELD` document, chunked.

    Module-scoped: reproducing the synthetic layer and reading the PDFs is the
    expensive part, and every assertion below ranges over the same enumeration.

    The corpus is reached twice on purpose. `compose_layer` reproduces the
    pre-render models the manifests are digest-pinned to — the expected side —
    and `read_pages` reads the committed bytes through the one pinned reader —
    the observed side. Neither is derived from the other.
    """
    records: dict[str, DocumentRecord] = {
        record.document_id: record for record in build_documents(iter_entries())
    }
    collected: list[SeededSplit] = []
    for generated in compose_layer():
        if not generated.plan.split_field:
            continue
        document_id = mint_document_id(generated.plan.document_id)
        record = records[document_id]
        pre_render = tuple(page.text for page in generated.model.pages)
        label, value = _split_halves(pre_render)
        pages = read_pages(record.path)
        collected.append(
            SeededSplit(
                document_id=document_id,
                split_field=generated.plan.split_field,
                label_text=label,
                value_text=value,
                label_page=1,
                value_page=2,
                pre_render_pages=pre_render,
                chunks=chunk_pages(record, pages).chunks,
            )
        )
    return tuple(collected)


# ---------------------------------------------------------------------------
# FR-068 — the population this module ranges over, published and non-empty
# ---------------------------------------------------------------------------


def test_the_seeded_population_is_enumerated_and_is_not_empty(
    seeded_splits: tuple[SeededSplit, ...],
) -> None:
    """A corpus with no page-split document would pass every check below by
    having nothing to check, which is the shape FR-068 refuses."""
    assert seeded_splits, (
        "FR-068: no committed document carries a page-split field, so every assertion "
        "in this module would hold vacuously"
    )
    assert len({split.document_id for split in seeded_splits}) == len(seeded_splits)


def test_every_exercised_document_is_one_the_manifest_records_as_page_split(
    seeded_splits: tuple[SeededSplit, ...],
) -> None:
    """The population is the corpus's own, not one this module selected.

    The class is recorded on the committed manifest entry and derived from the
    document by `corpus.derive`; taking the population from the reproduced plan
    and checking it against the manifest is what keeps this module ranging over
    the *seeded* documents rather than over whatever the reproduction produced.
    """
    recorded = {
        mint_document_id(document.entry.location.removesuffix(".pdf"))
        for document in iter_entries()
        if isinstance(document.entry, SyntheticEntry)
        and PAGE_SPLIT_CLASS in document.entry.irregularity_classes
    }
    assert recorded, f"no committed manifest entry records {PAGE_SPLIT_CLASS}"
    assert {split.document_id for split in seeded_splits} == recorded


# ---------------------------------------------------------------------------
# FR-029 — the anchor is the chunk printing the value
# ---------------------------------------------------------------------------


def test_the_cited_page_is_the_page_that_prints_the_value(
    seeded_splits: tuple[SeededSplit, ...],
) -> None:
    """FR-029, stated as the job the citation exists to do.

    Not "the anchor is the second chunk" — that is an implementation detail that
    would still hold if the pages were the other way round. What is asserted is
    that the page the citation names **contains the printed value** and that the
    label's page **does not**, so a reader following the citation lands on the
    number rather than on the word before it.
    """
    for split in seeded_splits:
        citation = split.citation()
        assert citation.cited_page == split.value_page, (
            f"{split.document_id}: the {split.split_field!r} value is printed on page "
            f"{split.value_page} and the citation names page {citation.cited_page}"
        )
        printed = normalize_page_text(split.value_text)
        assert printed in normalize_page_text(split.pre_render(citation.cited_page))
        assert printed not in normalize_page_text(split.pre_render(split.label_page)), (
            f"{split.document_id}: the label's page also prints the value, so this "
            f"document does not exercise the anchor rule"
        )


def test_the_label_page_is_kept_as_a_contributing_chunk_and_is_not_the_anchor(
    seeded_splits: tuple[SeededSplit, ...],
) -> None:
    """Keeps both pages — the earlier page is recorded, just not cited."""
    for split in seeded_splits:
        citation = split.citation()
        pages = [chunk.page_number for chunk in citation.contributors]
        assert pages == [split.label_page]
        contributors = [chunk.ordinal for chunk in citation.contributors]
        assert citation.anchor.ordinal not in contributors, (
            "the anchor is contributor 1 by definition and never appears again"
        )


def test_anchoring_on_the_label_chunk_is_refused_by_the_citation_itself(
    seeded_splits: tuple[SeededSplit, ...],
) -> None:
    """The failing direction, on real geometry rather than on invented ordinals.

    Building the citation the other way round — anchor on the label's chunk,
    the value's chunk as a contributor — puts a contributor on a page *after*
    the anchor's, which `ValueCitation` refuses. So the wrong reading of FR-029
    is unconstructible for these documents rather than merely discouraged.
    """
    for split in seeded_splits:
        anchor, label = split.anchor_chunk, split.label_chunk
        with pytest.raises(WriterError, match="FR-029"):
            cite_value(
                CitedChunk(label.ordinal, label.page_number),
                [CitedChunk(anchor.ordinal, anchor.page_number)],
            )


# ---------------------------------------------------------------------------
# FR-029 — one contributing row per additional page, and the count that follows
# ---------------------------------------------------------------------------


def test_the_written_columns_are_the_columns_the_revision_declares() -> None:
    """A column the table has and the statement omits is a NOT NULL violation on
    the first real insert; here it is a failing test with a diff.

    `extracted_value_contributing_chunk` has **no default on any column** — not
    even its key, which is `(extracted_value_id, contributor_ordinal)` — so the
    written list is the whole row rather than a selection from it. That equality
    is the assertion.
    """
    assert set(CONTRIBUTING_CHUNK_COLUMNS) == set(declared_contributing_columns())
    assert len(CONTRIBUTING_CHUNK_COLUMNS) == 5


def test_a_two_page_value_records_exactly_one_contributing_row(
    seeded_splits: tuple[SeededSplit, ...],
) -> None:
    """`ck_evcc__ordinal_min` fixes the floor at 2, so the anchor is not a row.

    A two-page value therefore has one contributing row, not two. Written as an
    equality against the ordinal floor rather than against the literal `2`, so
    the assertion moves with the constraint it restates.
    """
    for split in seeded_splits:
        citation = split.citation()
        rows = contributing_rows(citation)
        assert len(rows) == 1
        row = rows[0]
        assert row.contributor_ordinal == FIRST_CONTRIBUTOR_ORDINAL
        assert row.chunk.page_number == split.label_page
        assert row.chunk.ordinal == split.label_chunk.ordinal
        assert row.source_chunk_count == citation.source_chunk_count


def test_the_source_chunk_count_is_one_plus_the_recorded_contributor_count(
    seeded_splits: tuple[SeededSplit, ...],
) -> None:
    """The arithmetic `extracted_value.source_chunk_count` has to satisfy.

    `fk_evcc__value_count` holds the child's copy equal to the parent's and
    `ck_evcc__ordinal_within_declared_count` bounds the ordinals by it, so a
    count that did not include the anchor would make the highest ordinal
    unstorable. Asserted here as a number rather than inferred from the
    constraint names.
    """
    for split in seeded_splits:
        citation = split.citation()
        rows = contributing_rows(citation)
        assert citation.source_chunk_count == 1 + len(rows)
        assert citation.provenance_kind == "multi_chunk"
        assert max(row.contributor_ordinal for row in rows) <= citation.source_chunk_count


def test_a_single_chunk_value_of_the_same_document_records_no_row(
    seeded_splits: tuple[SeededSplit, ...],
) -> None:
    """The contrast that keeps the count above from being trivially one.

    Every other value on these documents is read from one chunk and records no
    contributing row at all, so "one row per **additional** page" is a rule
    about the additional pages rather than a row written for every value.
    """
    for split in seeded_splits:
        ordinary = cite_value(CitedChunk(split.anchor_chunk.ordinal, split.value_page))
        assert contributing_rows(ordinary) == ()
        assert ordinary.source_chunk_count == 1
        assert ordinary.provenance_kind == "single_chunk"


# ---------------------------------------------------------------------------
# SC-027 — the reassembly, in ascending page order
# ---------------------------------------------------------------------------


def test_the_value_reassembles_against_the_pre_render_text_in_page_order(
    seeded_splits: tuple[SeededSplit, ...],
) -> None:
    """SC-027, against the generator's pre-render text and not this epic's parse.

    The label ends one page and the value opens the next, so the printed pair
    exists only in the concatenation — it is on neither page alone, which is
    what makes "keeps both pages" load-bearing rather than tidy.
    """
    for split in seeded_splits:
        citation = split.citation()
        pages = citation.pages_in_reading_order()
        assert pages == (split.label_page, split.value_page)
        assert split.label_text.endswith(LABEL_SUFFIX), (
            f"{split.document_id}: page {split.label_page} does not end on a field label, "
            f"so this document is not a page-split document"
        )

        label = normalize_page_text(split.label_text)
        value = normalize_page_text(split.value_text)
        earlier = normalize_page_text(split.pre_render(split.label_page))
        later = normalize_page_text(split.pre_render(split.value_page))

        # Neither page carries both halves. This is the assertion "keeps both
        # pages" rests on: drop either chunk and the field is a label with no
        # value or a value with no field.
        assert label in earlier and value not in earlier
        assert value in later and label not in later

        joined = normalize_page_text(" ".join(split.pre_render(page) for page in pages))
        assert label in joined and value in joined
        assert joined.index(label) < joined.index(value), (
            f"{split.document_id}: reassembled in page order, the {split.split_field!r} "
            f"value precedes its own label"
        )


def test_contributor_order_does_not_reassemble_and_page_order_does(
    seeded_splits: tuple[SeededSplit, ...],
) -> None:
    """Why SC-027 names the order at all.

    The anchor is contributor 1 and sits on the *later* page, so reassembling by
    contributor position puts the value before its own label. Both orders are
    built and compared, so the requirement's clause is evidenced rather than
    restated: the two reassemblies disagree about which half comes first, and
    only one of them reads as the document does.
    """
    for split in seeded_splits:
        citation = split.citation()
        by_page = normalize_page_text(
            " ".join(split.pre_render(page) for page in citation.pages_in_reading_order())
        )
        by_contributor = normalize_page_text(
            " ".join(
                split.pre_render(chunk.page_number)
                for chunk in (citation.anchor, *citation.contributors)
            )
        )
        label = normalize_page_text(split.label_text)
        value = normalize_page_text(split.value_text)
        assert by_page.index(label) < by_page.index(value)
        assert by_contributor.index(value) < by_contributor.index(label), (
            f"{split.document_id}: contributor order happens to agree with page order "
            f"here, so this document does not distinguish the two"
        )


# ---------------------------------------------------------------------------
# The counts the report publishes for these values (FR-029, item 10)
# ---------------------------------------------------------------------------


def test_the_published_counts_follow_from_the_seeded_citations(
    seeded_splits: tuple[SeededSplit, ...],
) -> None:
    """T067's figures, denominated on the real citations above.

    One multi-chunk value per seeded document and one contributing row each, so
    the row count equals the multi-chunk value count exactly — every one of
    these splits spans two pages.
    """
    values = [
        PreparedValue(
            field_name="manufacturer",
            value_kind="text",
            value_text=split.value_text,
            value_number=None,
            confidence=0.9,
            citation=split.citation(),
            signals=ParseSignals("canonical", 2, False),
        )
        for split in seeded_splits
    ]
    counts = multi_chunk_counts(values)
    assert counts.values == len(seeded_splits)
    assert counts.multi_chunk_values == len(seeded_splits)
    assert counts.contributing_rows == len(seeded_splits)
    assert counts.single_chunk_values == 0

    section = page_split_section(run_id="page-split-check", counts=counts)
    assert section.item == 10
    assert str(counts.contributing_rows) in section.render()
    assert section.total_checks[0].count == counts.values
