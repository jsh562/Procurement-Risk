"""Structural markers: the UFGS ladder and the transmittal field blocks.

FR-012 / FR-013 / HINT-004. Two corpus layers, two structures, one detector —
because the chunker descends the same ladder in both and a second detector would
be a second answer to "what is a leaf".

**The page boundary is applied before the structural ladder, and that ordering
is the module's shape rather than a step inside it** (HINT-004, FR-013).
Detection runs *per page*: a structural unit never spans two pages, because the
detector is never shown two pages at once. Applying the ladder first and cutting
on the page afterwards produces a structurally clean unit that straddles a page,
which the scalar `chunk.page_number` cannot represent — and the failure is
silent, because a straddling unit still has exactly one plausible page number to
record. Ordering it this way makes the violation unconstructible instead of
checked for.

**A continuation keeps its parent's structural identifier** (FR-012). When a
page ends inside `2.4.7`, the next page's opening body lines are emitted as a
unit whose identifier is still `2.4.7`, marked `continued`. That is what makes a
page break a *named boundary class* rather than a fixed offset: the fragment is
still `2.4.7`, on a different page.

**The UFGS ladder.** SpecsIntact fixes the hierarchy — section, then the three
parts, then Article (`1.1`), Paragraph (`1.1.1`), and numbered subparagraphs to
level 6, with lettered items (`a.`, `b.`) below whatever numbered unit they sit
in. Levels are lexically detectable, so no inference is involved; the depth of a
dotted number *is* its level.

**The transmittal field block.** A generated transmittal prints `Label: value`
lines, and a run of consecutive such lines is one block. Labels are recognised
against E002's **committed** field-label vocabulary — canonical and alternate
alike, since an alternate label is still a label — through the same
`fold_label` the deriver uses. Nothing here judges whether a label is the
canonical one; that is a confidence signal, computed later and elsewhere.

Detection is a pure function of the lines a page yielded, so two runs over the
same bytes produce identical structure (FR-017).
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from model.corpus.codes import VOCABULARY, FieldLabelVocabulary, fold_label
from model.ingest.parse import ParsedPage

__all__ = [
    "LEVEL_ARTICLE",
    "LEVEL_PARAGRAPH",
    "LEVEL_PART",
    "LEVEL_SECTION",
    "LEVEL_SUBPARAGRAPH",
    "MARKER_KINDS",
    "PageStructure",
    "StructuralUnit",
    "StructureError",
    "detect_document",
    "detect_page",
]

#: Depth in the ladder. The numbers are the *ladder's* levels, not a dotted
#: number's component count: an article `2.4` is level 2 and carries two
#: components, and every deeper numbered unit is a subparagraph whatever its
#: depth, which is what makes "descend to the next level" total.
LEVEL_SECTION = 0
LEVEL_PART = 1
LEVEL_ARTICLE = 2
LEVEL_PARAGRAPH = 3
LEVEL_SUBPARAGRAPH = 4

#: The closed set of things a unit can be. `body` is the residual — a run of
#: lines under no marker at all — and it is a named member rather than an
#: absence so a page with no structure still yields units the ladder can descend.
MARKER_KINDS: tuple[str, ...] = (
    "section",
    "part",
    "article",
    "paragraph",
    "subparagraph",
    "lettered_item",
    "field_block",
    "body",
)

#: `SECTION 23 52 00` **and nothing else on the line**. The anchor at the end is
#: load-bearing: every UFGS page carries a running footer reading
#: `SECTION 23 52 00 Page 30`, and a prefix match treats each of those as a new
#: section marker — which resets the ladder once per page and silently discards
#: the article a page break was in the middle of.
_SECTION = re.compile(r"^SECTION\s+(?P<number>[0-9]{2} [0-9]{2} [0-9]{2}(?: [0-9]{2} [0-9]{2})?)$")
_PART = re.compile(r"^PART\s+(?P<number>[0-9]+)\s+(?P<title>[A-Z].*)$")
#: A dotted number opening a line, two to six components, followed by its title
#: or by nothing. Anchored at the line start so `para. 1.1.` inside prose is not
#: a marker.
_NUMBERED = re.compile(r"^(?P<number>[0-9]+(?:\.[0-9]+){1,5})\s+(?P<title>\S.*)$")
#: `a.`, `b.` — a lettered item, one level below whatever numbered unit holds it.
_LETTERED = re.compile(r"^(?P<letter>[a-z])\.\s+(?P<title>\S.*)$")

_LABEL_SEPARATOR = ":"


class StructureError(ValueError):
    """Raised when a page cannot be structurally read.

    One type for every failure: a caller learns the same thing from each of them
    — this page's structure is unknown, and chunking it would place boundaries
    the ladder did not identify.
    """


@dataclass(frozen=True)
class StructuralUnit:
    """One structural unit on one page, with its own lines and its children.

    `identifier` is what a fragment keeps (FR-012): `2.4.7` for a numbered unit,
    `PART 2` for a part, the folded field key for a field block, and the
    enclosing unit's identifier for a body run. `continued` marks a unit whose
    parent opened on an earlier page — the page-break boundary class.

    `lines` are the unit's **own** lines, excluding those belonging to its
    children, so a caller assembling text descends rather than double-counting.
    """

    kind: str
    identifier: str
    heading: str | None
    level: int
    page_number: int
    lines: tuple[str, ...]
    continued: bool = False
    children: tuple[StructuralUnit, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        if self.kind not in MARKER_KINDS:
            raise StructureError(f"{self.kind!r} is outside the closed marker set")
        if self.level < 0:
            raise StructureError(f"{self.identifier!r}: level must not be negative")

    @property
    def own_text(self) -> str:
        """This unit's own lines, excluding its children's."""
        return "\n".join(self.lines)

    @property
    def text(self) -> str:
        """This unit and everything beneath it, in reading order."""
        parts = [self.own_text] if self.lines else []
        parts.extend(child.text for child in self.children if child.text)
        return "\n".join(part for part in parts if part)

    @property
    def is_leaf(self) -> bool:
        return not self.children


@dataclass(frozen=True)
class PageStructure:
    """One page's units, in reading order, and the path left open at its foot.

    `open_path` is what the next page's continuation inherits. It is returned
    rather than held in the detector so `detect_page` stays a pure function of
    its arguments — two runs enumerating pages in different orders would
    otherwise disagree, which FR-017 does not permit.
    """

    page_number: int
    units: tuple[StructuralUnit, ...]
    open_path: tuple[tuple[str, str, int], ...]


#: SpecsIntact fixes exactly three parts, so an article's leading component is
#: `1`, `2` or `3` and nothing else. The bound is what separates a heading from a
#: body line that happens to open with a decimal number — `104.4 degrees C ...`
#: is not article 104.4, and admitting it resets the ladder to a unit the
#: document never printed.
_PART_NUMBERS = frozenset({"1", "2", "3"})


def _opens_a_heading(title: str) -> bool:
    """A heading's title opens with a capital, a digit, or bracketed markup.

    The second half of the same filter: `1.5 times the design pressure` clears
    the leading-component test and fails here, while `2.4.2 Electrical controls`
    and `1.2 [SUBMITTALS]` pass. Lower-cased prose after a number is a sentence,
    not a heading.
    """
    head = title.lstrip()[:1]
    return bool(head) and (head.isupper() or head.isdigit() or head == "[")


def _marker(
    line: str,
    vocabulary: FieldLabelVocabulary,
    part_number: str | None = None,
) -> tuple[str, str, str | None, int] | None:
    """`(kind, identifier, heading, level)` for a line that opens a unit.

    `part_number` is the open `PART n`, when one is open. A numbered heading
    beneath a part must lead with that part's own number — UFGS numbering is
    anchored to it — which is a stronger filter than any lexical test and costs
    nothing where the anchor is known.
    """
    stripped = line.strip()
    if not stripped:
        return None

    section = _SECTION.match(stripped)
    if section:
        number = section.group("number")
        return "section", f"SECTION {number}", stripped, LEVEL_SECTION

    part = _PART.match(stripped)
    if part:
        return "part", f"PART {part.group('number')}", part.group("title"), LEVEL_PART

    numbered = _NUMBERED.match(stripped)
    if numbered and _is_heading_number(numbered, part_number):
        number = numbered.group("number")
        depth = number.count(".") + 1
        if depth == 2:
            kind, level = "article", LEVEL_ARTICLE
        elif depth == 3:
            kind, level = "paragraph", LEVEL_PARAGRAPH
        else:
            # Levels 4, 5 and 6 are all subparagraphs; the ladder descends by
            # dotted depth, so the level is the depth and the kind is the name
            # the requirement uses for every rung below paragraph.
            kind, level = "subparagraph", LEVEL_SUBPARAGRAPH + depth - 4
        return kind, number, numbered.group("title"), level

    if _is_field_line(stripped, vocabulary):
        return "field_block", _field_key(stripped, vocabulary), None, LEVEL_ARTICLE

    lettered = _LETTERED.match(stripped)
    if lettered:
        return (
            "lettered_item",
            lettered.group("letter"),
            lettered.group("title"),
            # Placed relative to whatever numbered unit encloses it; the
            # enclosing level is added by the caller, which is the only place
            # that knows it.
            -1,
        )
    return None


def _is_heading_number(match: re.Match[str], part_number: str | None) -> bool:
    """Whether a line opening with a dotted number is a heading at all."""
    leading = match.group("number").split(".", 1)[0]
    expected = _PART_NUMBERS if part_number is None else {part_number}
    return leading in expected and _opens_a_heading(match.group("title"))


def _field_key(line: str, vocabulary: FieldLabelVocabulary) -> str:
    head, _, _ = line.partition(_LABEL_SEPARATOR)
    return _label_index(vocabulary).get(fold_label(head), fold_label(head))


def _is_field_line(line: str, vocabulary: FieldLabelVocabulary) -> bool:
    head, separator, _ = line.partition(_LABEL_SEPARATOR)
    if not separator:
        return False
    return fold_label(head) in _label_index(vocabulary)


_INDEX_CACHE: dict[int, dict[str, str]] = {}


def _label_index(vocabulary: FieldLabelVocabulary) -> dict[str, str]:
    """Folded label text → field key, over canonical **and** alternate labels.

    Alternates are included because a mis-labelled field is still a field, and
    excluding them would leave every alternate-labelled line unrecognised — a
    transmittal carrying one would lose its whole block, which reads as a clean
    document rather than an irregular one.
    """
    cached = _INDEX_CACHE.get(id(vocabulary))
    if cached is not None:
        return cached
    index: dict[str, str] = {}
    for key in vocabulary.field_keys:
        labels = vocabulary.labels(key)
        index[fold_label(labels.canonical_label)] = key
        for alternate in labels.alternate_labels:
            index[fold_label(alternate)] = key
    _INDEX_CACHE[id(vocabulary)] = index
    return index


@dataclass
class _Open:
    """A unit under construction, before its children are known."""

    kind: str
    identifier: str
    heading: str | None
    level: int
    continued: bool
    lines: list[str] = field(default_factory=list)
    children: list[StructuralUnit] = field(default_factory=list)

    def close(self, page_number: int) -> StructuralUnit:
        return StructuralUnit(
            kind=self.kind,
            identifier=self.identifier,
            heading=self.heading,
            level=self.level,
            page_number=page_number,
            lines=tuple(self.lines),
            continued=self.continued,
            children=tuple(self.children),
        )


def detect_page(
    page: ParsedPage,
    *,
    carried: Sequence[tuple[str, str, int]] = (),
    vocabulary: FieldLabelVocabulary | None = None,
) -> PageStructure:
    """One page's structural units, and the path its foot leaves open.

    `carried` is the previous page's `open_path` — `(kind, identifier, level)`
    per open ancestor. Its units are re-opened here as `continued`, which is how
    a fragment keeps the structural identifier of the unit it came from while
    still being confined to one page.
    """
    vocab = vocabulary if vocabulary is not None else VOCABULARY
    if not isinstance(page, ParsedPage):
        raise StructureError(f"expected a ParsedPage, found {type(page).__name__}")

    stack: list[_Open] = [
        _Open(kind=kind, identifier=identifier, heading=None, level=level, continued=True)
        for kind, identifier, level in carried
    ]
    roots: list[StructuralUnit] = []

    def close_to(level: int) -> None:
        while stack and stack[-1].level >= level:
            finished = stack.pop().close(page.number)
            if stack:
                stack[-1].children.append(finished)
            else:
                roots.append(finished)

    for line in page.lines:
        if not line.strip():
            continue
        found = _marker(line, vocab, _open_part(stack))
        if found is None:
            _current(stack, page, roots).lines.append(line)
            continue
        kind, identifier, heading, level = found
        if level < 0:
            # A lettered item sits one rung below whatever encloses it — unless
            # what encloses it is another lettered item, in which case `b.`
            # follows `a.` as its sibling. Without that second case every item
            # in a list nests inside the one before it, so `e.` ends up five
            # rungs deep and the ladder descends through units the document
            # never printed.
            if stack and stack[-1].kind == "lettered_item":
                level = stack[-1].level
            else:
                level = (stack[-1].level if stack else LEVEL_ARTICLE) + 1
        if kind == "field_block" and stack and stack[-1].kind == "field_block":
            # A run of consecutive label lines is **one** block, not one unit
            # per label: they are printed as a group and read as a group.
            stack[-1].lines.append(line)
            continue
        close_to(level)
        stack.append(
            _Open(kind=kind, identifier=identifier, heading=heading, level=level, continued=False)
        )
        stack[-1].lines.append(line)

    close_to(0)
    while stack:
        finished = stack.pop().close(page.number)
        if stack:
            stack[-1].children.append(finished)
        else:
            roots.append(finished)

    return PageStructure(
        page_number=page.number,
        units=tuple(roots),
        open_path=_open_path(roots),
    )


def _open_part(stack: Sequence[_Open]) -> str | None:
    """The number of the `PART n` currently open, if any."""
    for unit in stack:
        if unit.kind == "part":
            return unit.identifier.split(" ", 1)[-1]
    return None


def _current(stack: list[_Open], page: ParsedPage, roots: list[StructuralUnit]) -> _Open:
    """The unit a body line belongs to, opening a `body` residual if there is none.

    A page whose first lines sit under no marker — a continuation with nothing
    carried, a cover page, a table — still produces a unit, so no text is
    silently dropped between the parser and the chunker.
    """
    if stack:
        return stack[-1]
    residual = _Open(
        kind="body",
        identifier=f"p{page.number}-body{len(roots)}",
        heading=None,
        level=LEVEL_ARTICLE,
        continued=False,
    )
    stack.append(residual)
    return residual


def _open_path(roots: Sequence[StructuralUnit]) -> tuple[tuple[str, str, int], ...]:
    """The rightmost root-to-leaf path — the ancestors a page break leaves open.

    A `body` residual is deliberately **not** carried: it is this module's own
    invention for text under no marker, and carrying it onto the next page would
    attach a structural identifier that nothing in the document printed.
    """
    path: list[tuple[str, str, int]] = []
    unit: StructuralUnit | None = roots[-1] if roots else None
    while unit is not None:
        if unit.kind == "body":
            break
        path.append((unit.kind, unit.identifier, unit.level))
        unit = unit.children[-1] if unit.children else None
    return tuple(path)


def detect_document(
    pages: Sequence[ParsedPage],
    *,
    vocabulary: FieldLabelVocabulary | None = None,
) -> tuple[PageStructure, ...]:
    """Every page's structure, each page detected in isolation (HINT-004).

    Pages are visited in ascending page number and the open path is threaded
    forward, so the only thing that crosses a page boundary is a *name* — never
    a unit. Empty pages produce an empty structure and, per FR-015, no chunk and
    no ordinal; the open path passes through them unchanged, so a unit split by a
    blank page keeps its identifier on the far side.
    """
    ordered = sorted(pages, key=lambda page: page.number)
    detected: list[PageStructure] = []
    carried: tuple[tuple[str, str, int], ...] = ()
    for page in ordered:
        if page.is_empty:
            detected.append(PageStructure(page.number, (), carried))
            continue
        structure = detect_page(page, carried=carried, vocabulary=vocabulary)
        detected.append(structure)
        carried = structure.open_path
    return tuple(detected)
