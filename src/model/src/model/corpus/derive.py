"""Structural re-derivation from an emitted PDF — the oracle, not a restatement.

FR-031a / FR-001a. This module reads a committed document's own bytes and says
which of the **four structural** irregularity classes it carries. Nothing here
consults the generator, the plan, or the manifest: FR-031a requires the derived
set to be independent of what the generator recorded, because a comparison
against a set the generator also produced is a comparison of a thing with
itself.

**The word tolerances are pinned in one place and are part of the oracle.**
`WORD_EXTRACTION` below is passed to every `extract_words()` call; the library's
defaults are never used. pdfplumber's defaults are not a stable contract across
versions (research §Extraction for independent validation), and this derived set
is what VR-035 judges the *recorded* set against — so a tolerance change is a
change to the oracle and is treated as one, not as a library upgrade. `x_tolerance`
in particular is chosen **below the width of a space** at the smallest body size
any template uses, so words split where the layout put spaces and never inside a
word; a value above it would silently glue a label to its value and make every
blank field look populated.

**`SCAN_DEGRADATION` is not derived and is not derivable.** No structural
property of a PDF says a raster is *degraded* rather than merely present. Its
evidence path is the injector unit tests (VR-050); VR-036 adds a necessary
condition over the raster's existence and claims nothing more.

**The one ambiguity, and how it is broken.** A label ending a page with no value
beside it is, on the face of the artifact, either a field whose value was blanked
or a field whose value continues overleaf. The rule here is stated rather than
left to fall out of the code: *the page boundary wins* — a bare label that is the
last text object on page n, followed on page n+1 by a body line that is not
itself a label, is `PAGE_SPLIT_FIELD` and is excluded from the blank test.
`irregularity.py` keeps the two apart at the injection site as well, by never
blanking a field that can be last on a page, so the tie-break is a guard rather
than a load-bearing guess.

Untrusted input, deliberately (FR-001a): there is no sanitization pass between
the committed bytes and this reader, and any reader failure is raised as this
module's one error type for the caller to report against a rule — never
swallowed so a run can continue, which would silently exempt the document from
every rule asserted over it.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from types import MappingProxyType

from model.corpus.codes import VOCABULARY, FieldLabelVocabulary, fold_label
from model.corpus.irregularity import STRUCTURAL_CLASSES, IrregularityClass
from model.corpus.templates import LABEL_SUFFIX

__all__ = [
    "DERIVABLE_CLASSES",
    "LABEL_SEPARATOR",
    "VALUE_INDENT_FLOOR",
    "WORD_EXTRACTION",
    "DeriveError",
    "PageContent",
    "Rect",
    "TextLine",
    "Word",
    "derive_classes",
    "normalize_page_text",
    "page_text",
    "read_document",
]

#: What `derive_classes` can return: exactly the four structural classes. Stated
#: so the module's central claim is checkable rather than narrative, and so a
#: caller intersecting a recorded set against it names this rather than
#: rebuilding the set.
DERIVABLE_CLASSES: frozenset[str] = frozenset(member.value for member in STRUCTURAL_CLASSES)

#: **The one place the extraction tolerances live** (VR-035d, AD-004). Passed
#: explicitly on every call; the library defaults are never taken.
#:
#: - `x_tolerance` 1.0 pt — below the 2.5 pt advance of a space at the 9 pt body
#:   size of the smallest template, and far above the 0 pt gap between adjacent
#:   glyphs, so a word breaks exactly where the layout wrote a space.
#: - `y_tolerance` 2.0 pt — below the 12 pt leading of the tightest template, so
#:   two baselines are never merged into one line.
#: - `keep_blank_chars` False — a run of spaces is layout, not content; VR-039's
#:   comparison collapses whitespace for the same reason.
#: - `use_text_flow` False — reading order is decided by geometry rather than by
#:   the order the renderer happened to emit operators in, which is what makes
#:   "the last text object on page n" a statement about the page.
WORD_EXTRACTION: Mapping[str, object] = MappingProxyType(
    {
        "x_tolerance": 1.0,
        "y_tolerance": 2.0,
        "keep_blank_chars": False,
        "use_text_flow": False,
    }
)

#: The character a rendered label is terminated by. `templates.LABEL_SUFFIX`
#: writes it; this is the deriver's independent knowledge of the same
#: convention, and the two are compared immediately below rather than assumed
#: equal. A template that changed its separator would otherwise leave every
#: label unrecognised and every derivation empty, which reads as a clean corpus.
LABEL_SEPARATOR = ":"

if LABEL_SUFFIX != LABEL_SEPARATOR:
    raise ValueError(
        f"the templates terminate a label with {LABEL_SUFFIX!r} but the deriver recognises "
        f"{LABEL_SEPARATOR!r}; every label would go unrecognised"
    )

#: How far a value line must sit to the right of its label line to count as
#: that label's value region. The templates that place a value beneath its label
#: indent it by 12 pt and indent nothing else, so any positive floor below that
#: separates a value from the next field's label; 6 pt leaves room for a
#: rounding difference without admitting an unindented line.
VALUE_INDENT_FLOOR = 6.0


class DeriveError(ValueError):
    """Raised when a document cannot be opened, read, or structurally derived.

    One type for every failure, as `RosterError` and `ManifestError` are: a
    caller learns the same thing from each of them — this document's structural
    class set is unknown, and it must be reported rather than assumed empty.
    """


# ---------------------------------------------------------------------------
# What a page is, once it has been read
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Rect:
    """A bounding box in pdfplumber's top-down page coordinates."""

    x0: float
    top: float
    x1: float
    bottom: float

    @property
    def area(self) -> float:
        return max(0.0, self.x1 - self.x0) * max(0.0, self.bottom - self.top)

    def intersects(self, other: Rect) -> bool:
        """True when the two boxes share any area.

        Touching edges do not intersect: a text object whose baseline sits
        exactly on a raster's edge is outside it, and treating that as an
        overlap would make FR-032's guarantee depend on a rounding direction.
        """
        return (
            self.x0 < other.x1
            and other.x0 < self.x1
            and self.top < other.bottom
            and other.top < self.bottom
        )


@dataclass(frozen=True)
class Word:
    """One extracted word and its box."""

    text: str
    x0: float
    x1: float
    top: float
    bottom: float

    @property
    def rect(self) -> Rect:
        return Rect(self.x0, self.top, self.x1, self.bottom)


@dataclass(frozen=True)
class TextLine:
    """One baseline's worth of words, in reading order."""

    words: tuple[Word, ...]

    @property
    def text(self) -> str:
        return " ".join(word.text for word in self.words)

    @property
    def x0(self) -> float:
        return min(word.x0 for word in self.words)

    @property
    def top(self) -> float:
        return min(word.top for word in self.words)


@dataclass(frozen=True)
class PageContent:
    """One page after reading: its lines, its raster images, and its size."""

    number: int
    width: float
    height: float
    lines: tuple[TextLine, ...]
    images: tuple[Rect, ...]

    @property
    def text(self) -> str:
        return "\n".join(line.text for line in self.lines)

    @property
    def area(self) -> float:
        return self.width * self.height


def _group_lines(words: Sequence[Word], tolerance: float) -> tuple[TextLine, ...]:
    """Group words into baselines, top to bottom then left to right.

    Grouped here rather than taken from a higher-level extractor so the grouping
    uses the same pinned `y_tolerance` the words were split under: two
    tolerances, one from this constant and one from a library default, would be
    two answers to what a line is.
    """
    ordered = sorted(words, key=lambda word: (round(word.top, 2), round(word.x0, 2)))
    lines: list[TextLine] = []
    current: list[Word] = []
    for word in ordered:
        if current and abs(word.top - current[0].top) > tolerance:
            lines.append(TextLine(tuple(current)))
            current = []
        current.append(word)
    if current:
        lines.append(TextLine(tuple(current)))
    return tuple(lines)


def read_document(path: Path) -> tuple[PageContent, ...]:
    """Read every page's words and raster boxes from a committed PDF.

    One open per document, so the four structural rules, the raster condition
    and the citation-anchor rule all read the same observation rather than five
    of them.
    """
    import pdfplumber

    target = Path(path)
    tolerance = float(WORD_EXTRACTION["y_tolerance"])  # type: ignore[arg-type]
    try:
        with pdfplumber.open(target) as document:
            pages: list[PageContent] = []
            for index, page in enumerate(document.pages, start=1):
                words = tuple(
                    Word(
                        text=str(raw["text"]),
                        x0=float(raw["x0"]),
                        x1=float(raw["x1"]),
                        top=float(raw["top"]),
                        bottom=float(raw["bottom"]),
                    )
                    for raw in page.extract_words(**WORD_EXTRACTION)
                )
                images = tuple(
                    Rect(
                        x0=float(raw["x0"]),
                        top=float(raw["top"]),
                        x1=float(raw["x1"]),
                        bottom=float(raw["bottom"]),
                    )
                    for raw in page.images
                )
                pages.append(
                    PageContent(
                        number=index,
                        width=float(page.width),
                        height=float(page.height),
                        lines=_group_lines(words, tolerance),
                        images=images,
                    )
                )
    except DeriveError:
        raise
    except Exception as exc:  # noqa: BLE001 - FR-001a: any reader failure is this module's
        raise DeriveError(f"cannot read {target}: {type(exc).__name__}: {exc}") from exc
    if not pages:
        raise DeriveError(f"{target} carries no page")
    return tuple(pages)


# ---------------------------------------------------------------------------
# Recognising a label
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LabelHit:
    """A line that opens with a label from the vocabulary."""

    key: str
    label: str
    canonical: bool
    inline_value: str


def _label_index(vocabulary: FieldLabelVocabulary) -> Mapping[str, tuple[str, bool]]:
    """Folded label text → (field key, is the canonical label).

    Built over both canonical and alternate labels because the deriver has to
    recognise a *mis*-labelled field in order to report it; the vocabulary
    reader has already refused any pair that would make this mapping ambiguous.
    """
    index: dict[str, tuple[str, bool]] = {}
    for key in vocabulary.field_keys:
        labels = vocabulary.labels(key)
        index[fold_label(labels.canonical_label)] = (key, True)
        for alternate in labels.alternate_labels:
            index[fold_label(alternate)] = (key, False)
    return MappingProxyType(index)


def _parse(line: TextLine, index: Mapping[str, tuple[str, bool]]) -> LabelHit | None:
    """The label a line opens with, if any, and whatever follows it on that line."""
    head, separator, rest = line.text.partition(LABEL_SEPARATOR)
    if not separator:
        return None
    found = index.get(fold_label(head))
    if found is None:
        return None
    key, canonical = found
    return LabelHit(key=key, label=head.strip(), canonical=canonical, inline_value=rest.strip())


# ---------------------------------------------------------------------------
# The derivation
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Reading:
    """The intermediate the four class rules are decided from."""

    hits: tuple[tuple[LabelHit | None, ...], ...]
    values: Mapping[str, str]
    split_positions: frozenset[tuple[int, int]]


def _value_region(page: PageContent, hits: Sequence[LabelHit | None], position: int) -> str:
    """The text of the value belonging to the label at `position`, or `""`.

    Two placements, one rule. A template that writes the value beside its label
    puts it on the same line, so the value region is the remainder of that line.
    A template that writes it beneath indents it, so the value region is the
    following line **only when that line is indented relative to the label and
    is not itself a label**. Without the indentation test the next field's label
    would count as this field's value on an inline template and no blank could
    ever be derived.
    """
    hit = hits[position]
    if hit is None:  # pragma: no cover - callers only ask about label lines
        return ""
    if hit.inline_value:
        return hit.inline_value
    following = position + 1
    if following >= len(page.lines) or hits[following] is not None:
        return ""
    if page.lines[following].x0 <= page.lines[position].x0 + VALUE_INDENT_FLOOR:
        return ""
    return page.lines[following].text


def _read(pages: Sequence[PageContent], index: Mapping[str, tuple[str, bool]]) -> _Reading:
    hits = tuple(tuple(_parse(line, index) for line in page.lines) for page in pages)

    # The page boundary is resolved first, because it is what removes the one
    # label that would otherwise read as blank.
    split_positions: set[tuple[int, int]] = set()
    split_values: dict[int, str] = {}
    for number in range(len(pages) - 1):
        page, following = pages[number], pages[number + 1]
        if not page.lines or len(following.lines) < 2:
            continue
        last = hits[number][-1]
        if last is None or last.inline_value:
            continue
        # `lines[0]` of the next page is its citation anchor, which every page
        # carries; the continuation is the first body line after it, and it must
        # not itself be a label or the field's value never appears at all.
        if hits[number + 1][1] is not None:
            continue
        split_positions.add((number, len(page.lines) - 1))
        split_values[number] = following.lines[1].text

    values: dict[str, str] = {}
    for number, page in enumerate(pages):
        for position, hit in enumerate(hits[number]):
            if hit is None:
                continue
            if (number, position) in split_positions:
                value = split_values.get(number, "")
            else:
                value = _value_region(page, hits[number], position)
            if value and hit.key not in values:
                values[hit.key] = value
    return _Reading(
        hits=hits, values=MappingProxyType(values), split_positions=frozenset(split_positions)
    )


def _missing_or_blank(
    pages: Sequence[PageContent], reading: _Reading, vocabulary: FieldLabelVocabulary
) -> bool:
    """VR-035a, in both its halves."""
    for number, page in enumerate(pages):
        for position, hit in enumerate(reading.hits[number]):
            if hit is None or (number, position) in reading.split_positions:
                continue
            if not _value_region(page, reading.hits[number], position):
                return True
    seen = {
        hit.key
        for page_hits in reading.hits
        for hit in page_hits
        if hit is not None and hit.canonical
    }
    return any(key not in seen for key in vocabulary.structural_field_keys)


def _out_of_order(reading: _Reading, vocabulary: FieldLabelVocabulary) -> bool:
    """VR-035c: the parsed date fields violate the committed chronological order."""
    parsed: list[date] = []
    for key in vocabulary.date_field_order:
        raw = reading.values.get(key)
        if not raw:
            continue
        try:
            parsed.append(date.fromisoformat(raw.strip()))
        except ValueError:
            continue
    if len(parsed) < 2:
        # Undecidable rather than satisfied: a document whose dates cannot be
        # read carries no evidence either way, and reporting the class would
        # attribute an ordering defect to a parsing one.
        return False
    return any(later < earlier for earlier, later in zip(parsed, parsed[1:], strict=False))


def derive_classes(
    source: Path | Iterable[PageContent],
    *,
    vocabulary: FieldLabelVocabulary | None = None,
) -> frozenset[str]:
    """The structural irregularity classes an emitted document carries.

    `source` is a path, or pages already read by `read_document` — the validator
    reads each document once and hands the same observation to every rule.

    `vocabulary` defaults to the committed one. It is a parameter because the
    deriver has to be checkable against an expectation the injector did not
    produce: the fixture tests supply a vocabulary of their own, so a deriver
    that merely echoed the committed labels would fail there while passing set
    equality over the committed layer (VR-035, `data-model.md`).

    The result is always a subset of the four structural classes. `SCAN_DEGRADATION`
    is never returned, which is what makes FR-031a's comparison
    `derived == recorded ∩ {the four}` rather than `derived == recorded`.
    """
    vocab = vocabulary if vocabulary is not None else VOCABULARY
    pages = tuple(read_document(source) if isinstance(source, str | Path) else source)
    if not pages:
        raise DeriveError("a document must carry at least one page to derive from")

    index = _label_index(vocab)
    reading = _read(pages, index)

    derived: set[str] = set()
    if any(
        hit is not None and not hit.canonical for page_hits in reading.hits for hit in page_hits
    ):
        derived.add(IrregularityClass.INCONSISTENT_FIELD_LABEL.value)
    if reading.split_positions:
        derived.add(IrregularityClass.PAGE_SPLIT_FIELD.value)
    if _missing_or_blank(pages, reading, vocab):
        derived.add(IrregularityClass.MISSING_OR_BLANK_FIELD.value)
    if _out_of_order(reading, vocab):
        derived.add(IrregularityClass.OUT_OF_ORDER_DATE.value)
    return frozenset(derived)


def _normalized(text: str) -> str:
    """NFC, whitespace-collapsed — VR-039's comparison form."""
    return " ".join(unicodedata.normalize("NFC", text).split())


def page_text(page: PageContent) -> str:
    """One page's extracted text in VR-039's comparison form."""
    return _normalized(page.text)


def normalize_page_text(text: str) -> str:
    """The same normalization, applied to a document model's page text."""
    return _normalized(text)
