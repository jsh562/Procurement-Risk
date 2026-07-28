"""The three boundary classes, the descent ladder, and the ordinal rule.

FR-012 / FR-013 / FR-014 / FR-015 / FR-016 / FR-017 / FR-053. Everything that
decides where a chunk starts and stops is here, and the four constraints it has
to satisfy simultaneously are worth stating together, because satisfying any
three of them is easy:

1. every boundary is one of **three named classes** — a structural boundary the
   parser identified, a page break, or a sentence boundary inside a leaf above
   the encoder window — and **no** boundary sits at a fixed character, word or
   token offset (FR-012);
2. a chunk lies on **exactly one page** (FR-013);
3. a chunk fits the encoder's **254-piece content budget**, measured in the
   encoder's own tokenizer, because the encoder truncates silently (FR-014);
4. ordinals are **zero-based, contiguous and in reading order** within a
   document (FR-015).

**The page split is applied before the ladder** (HINT-004), and it is applied by
`structure.detect_document`, which never sees two pages at once. So (2) holds by
construction here rather than by a check: this module only ever descends units
that already belong to one page.

**The ladder is article → paragraph → subparagraph → sentence** (FR-014). A unit
that fits is emitted whole. A unit that does not is replaced by its own lines
plus its children, each descended in turn; a **leaf** that does not fit is
segmented into sentences and its sentences are packed, in order, into chunks
that fit. A **single sentence** that still exceeds the budget fails the run
naming the unit — the one fail-closed case, and the only one, because AD-002
measured that fail-closed at the leaf level ingests nothing.

**A fragment keeps its parent's structural identifier** (FR-012). A page-break
fragment and a sentence fragment are both still `2.4.7`, which is what makes
them named boundaries rather than offsets: the identifier survives the cut, so
a reader can widen to the parent unit that was split.

**Text is verbatim** (FR-016). Chunk text is the lines as the committed reader
extracted them, joined by newline, including unresolved bracketed alternatives
(`[on-off] [high-low-off]`) exactly as an unedited UFGS master prints them.
Nothing here strips, resolves, or rewrites markup — the bracket is content.

**The chunker version is composed, not typed** (FR-017). It carries a declared
rule version *and* the pinned segmenter's identity and version, so a pySBD
upgrade changes the recorded version mechanically. FR-017 lists exactly what
obliges a bump — the boundary-class rules, the structural detection, the ordinal
rule, or the segmenter — and the encoder identity is deliberately not among
them.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from model.ingest.documents import DocumentRecord
from model.ingest.parse import ParsedPage, read_pages
from model.ingest.segment import SEGMENTER_ID, SEGMENTER_VERSION, sentences
from model.ingest.structure import PageStructure, StructuralUnit, detect_document
from model.ingest.tokens import CONTENT_TOKEN_BUDGET, content_pieces

__all__ = [
    "BOUNDARY_CLASSES",
    "CHUNKER_RULES_VERSION",
    "CHUNKER_VERSION",
    "Chunk",
    "ChunkerError",
    "DocumentChunking",
    "chunk_document",
    "chunk_pages",
]

#: FR-012's closed set. A chunk records which class closed it; there is no
#: fourth member and no "other".
BOUNDARY_CLASSES: tuple[str, ...] = ("structural", "page_break", "sentence")

#: The declared half of FR-017's version — bumped by hand when the boundary-class
#: rules, the structural detection, or the ordinal rule changes.
CHUNKER_RULES_VERSION = "e006-chunker/1"

#: The recorded value. The segmenter's identity and pinned version are part of
#: it, so an upgrade is a version change without anyone remembering the rule.
CHUNKER_VERSION = f"{CHUNKER_RULES_VERSION}+{SEGMENTER_ID}-{SEGMENTER_VERSION}"


class ChunkerError(ValueError):
    """Raised when a document cannot be cut into legal chunks.

    One type for every failure. The failure that actually occurs in practice is
    FR-014's: a single sentence above the encoder window, which cannot be split
    further by any named boundary class and must not be embedded truncated.
    """


@dataclass(frozen=True)
class Chunk:
    """One chunk, in E003's `chunk` column names plus the chunker's own record.

    `boundary_class` and `structural_identifier` are not E003 columns — they are
    what FR-012 and FR-053 require published, and they are carried on the chunk
    so the report counts them rather than re-deriving them from text.
    """

    document_id: str
    document_type: str
    project_id: str
    page_number: int
    ordinal: int
    body_text: str
    boundary_class: str
    structural_identifier: str
    spec_section: str | None = None
    heading: str | None = None
    content_pieces: int = 0

    def __post_init__(self) -> None:
        if self.boundary_class not in BOUNDARY_CLASSES:
            raise ChunkerError(
                f"{self.boundary_class!r} is outside FR-012's closed set {BOUNDARY_CLASSES}"
            )
        if self.ordinal < 0:
            raise ChunkerError(
                f"{self.document_id}: ordinal must be zero-based, found {self.ordinal}"
            )
        if self.page_number < 1:
            raise ChunkerError(f"{self.document_id}: page numbers are 1-based")
        if not self.body_text.strip():
            raise ChunkerError(f"{self.document_id}: a chunk carries searchable text")


@dataclass(frozen=True)
class DocumentChunking:
    """Every chunk of one document, with the version that produced them."""

    document_id: str
    chunker_version: str
    chunks: tuple[Chunk, ...]

    @property
    def boundary_class_counts(self) -> dict[str, int]:
        """FR-053's boundary-class counts, over the closed set including zeros."""
        counts = dict.fromkeys(BOUNDARY_CLASSES, 0)
        for chunk in self.chunks:
            counts[chunk.boundary_class] += 1
        return counts

    @property
    def leaf_lengths(self) -> tuple[int, ...]:
        """FR-053's leaf-length distribution, in content word pieces."""
        return tuple(chunk.content_pieces for chunk in self.chunks)


@dataclass(frozen=True)
class _Context:
    """What a unit inherits from the units above it on the same page."""

    spec_section: str | None = None
    heading: str | None = None

    def below(self, unit: StructuralUnit) -> _Context:
        if unit.kind == "section":
            # `SECTION 23 52 00` → `23 52 00`, the form `chunk.spec_section`
            # and the manifest's `masterformat_section` both carry.
            return _Context(spec_section=unit.identifier.split(" ", 1)[-1], heading=self.heading)
        if unit.heading:
            return _Context(spec_section=self.spec_section, heading=unit.heading)
        return self


def _boundary_class(unit: StructuralUnit, page: PageStructure) -> str:
    """Which named class closed a chunk emitted whole from `unit`.

    A unit re-opened on this page was cut by the previous page's foot; a unit on
    this page's open path will be cut by this page's foot. Either way the
    boundary is the page break, and it is recorded as such rather than as a
    structural boundary that happens to coincide with one.
    """
    if unit.continued:
        return "page_break"
    if (unit.kind, unit.identifier, unit.level) in page.open_path:
        return "page_break"
    return "structural"


def _own_fragment(unit: StructuralUnit) -> StructuralUnit:
    """The unit's own lines as a childless unit, keeping its identifier.

    Produced when a unit is too long and the ladder descends into it: the unit's
    own text is a fragment of that unit, so it keeps the unit's identifier and
    heading exactly as FR-012 requires.
    """
    return StructuralUnit(
        kind=unit.kind,
        identifier=unit.identifier,
        heading=unit.heading,
        level=unit.level,
        page_number=unit.page_number,
        lines=unit.lines,
        continued=unit.continued,
        children=(),
    )


class _Emitter:
    """Ordinal assignment and the descent, kept together.

    The ordinal counter lives here rather than being passed down the recursion
    because FR-015's contiguity is a property of the *emission sequence*: a
    counter incremented only where a chunk is actually appended cannot skip a
    number for a page or unit that produced nothing.
    """

    def __init__(self, record: DocumentRecord) -> None:
        self._record = record
        self._chunks: list[Chunk] = []

    @property
    def chunks(self) -> tuple[Chunk, ...]:
        return tuple(self._chunks)

    def append(
        self,
        *,
        text: str,
        page_number: int,
        boundary_class: str,
        identifier: str,
        context: _Context,
        pieces: int,
    ) -> None:
        if not text.strip():
            # FR-015: a unit yielding no storable text produces no chunk and
            # consumes no ordinal, so the counter is not touched.
            return
        self._chunks.append(
            Chunk(
                document_id=self._record.document_id,
                document_type=self._record.document_type,
                project_id=self._record.project_id,
                page_number=page_number,
                ordinal=len(self._chunks),
                body_text=text,
                boundary_class=boundary_class,
                structural_identifier=identifier,
                spec_section=context.spec_section,
                heading=context.heading,
                content_pieces=pieces,
            )
        )

    def descend(self, unit: StructuralUnit, page: PageStructure, context: _Context) -> None:
        """Emit `unit`, descending the ladder only as far as the budget forces."""
        below = context.below(unit)
        text = unit.text
        if not text.strip():
            return
        pieces = content_pieces(text)
        if pieces <= CONTENT_TOKEN_BUDGET:
            self.append(
                text=text,
                page_number=unit.page_number,
                boundary_class=_boundary_class(unit, page),
                identifier=unit.identifier,
                context=below,
                pieces=pieces,
            )
            return
        if unit.children:
            # One rung down: the unit's own lines, then each child in reading
            # order. Both keep their own structural identifiers.
            self.descend(_own_fragment(unit), page, below)
            for child in unit.children:
                self.descend(child, page, below)
            return
        self._split_leaf(unit, page, below)

    def _split_leaf(self, unit: StructuralUnit, page: PageStructure, context: _Context) -> None:
        """The last rung: sentence boundaries inside an over-long leaf (FR-014).

        pySBD is reached only here, which is the whole of research's "invoke it
        only on units that already exceed the cap".
        """
        text = unit.text
        found = sentences(text)
        packed: list[str] = []
        packed_pieces = 0

        def flush() -> None:
            nonlocal packed, packed_pieces
            if packed:
                self.append(
                    text="".join(packed).strip(),
                    page_number=unit.page_number,
                    boundary_class="sentence",
                    identifier=unit.identifier,
                    context=context,
                    pieces=packed_pieces,
                )
            packed = []
            packed_pieces = 0

        for sentence in found:
            pieces = content_pieces(sentence.text)
            if pieces > CONTENT_TOKEN_BUDGET:
                flush()
                raise ChunkerError(
                    f"FR-014: {self._record.document_id} page {unit.page_number} unit "
                    f"{unit.identifier!r} holds a single sentence of {pieces} content "
                    f"word pieces, above the {CONTENT_TOKEN_BUDGET} budget. It cannot be "
                    "divided by any named boundary class and is not embedded truncated: "
                    f"{sentence.text[:120]!r}"
                )
            if packed and packed_pieces + pieces > CONTENT_TOKEN_BUDGET:
                flush()
            packed.append(sentence.text)
            # Re-measured on the joined text rather than summed: word pieces do
            # not add across a join, and a sum would drift above the budget.
            packed_pieces = content_pieces("".join(packed))
            while packed_pieces > CONTENT_TOKEN_BUDGET and len(packed) > 1:
                tail = packed.pop()
                packed_pieces = content_pieces("".join(packed))
                flush()
                packed = [tail]
                packed_pieces = content_pieces(tail)
        flush()


def chunk_pages(
    record: DocumentRecord,
    pages: Sequence[ParsedPage],
) -> DocumentChunking:
    """Chunk a document whose pages have already been read.

    Separate from `chunk_document` so the determinism check and the containment
    check can supply the same pages twice rather than reading the file twice and
    comparing two reads.
    """
    structures = detect_document(pages)
    emitter = _Emitter(record)
    for structure in structures:
        for unit in structure.units:
            emitter.descend(unit, structure, _Context())
    chunks = emitter.chunks
    ordinals = [chunk.ordinal for chunk in chunks]
    if ordinals != list(range(len(chunks))):
        raise ChunkerError(
            f"FR-015: {record.document_id} produced ordinals {ordinals[:8]}…, which are not "
            "contiguous from zero"
        )
    return DocumentChunking(
        document_id=record.document_id,
        chunker_version=CHUNKER_VERSION,
        chunks=chunks,
    )


def chunk_document(record: DocumentRecord) -> DocumentChunking:
    """Read a document through the committed reader and chunk it."""
    return chunk_pages(record, read_pages(record.path))
