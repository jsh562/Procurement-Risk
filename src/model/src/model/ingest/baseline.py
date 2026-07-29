"""The deterministic per-vendor template baseline (FR-050, Principle VIII).

**The terms this file is authored under, stated before there was anything to
author.** The baseline reads the **rendered** documents only. It may not import
`model.corpus.templates` (the per-vendor generation source), `model.corpus.render`
(what turns a document model into the PDF), or `model.corpus.model` (the
pre-render document model). Those three are the answer key, and an opponent
reading the answer key cannot lose — which would make every quality figure
published beside it flattery rather than evidence.

`src/model/pyproject.toml` declares that as an import-linter `forbidden`
contract with this module as its source and `allow_indirect_imports = false`, so
reaching the templates through `corpus.generate` — or through `ingest.parse`,
which imports `corpus.derive`, which imports `corpus.templates` — fails the
build exactly as a direct import would. The contract landed with T008, before
this extractor was written, because a rule introduced after the code it governs
ratifies rather than audits. That ordering is what FR-050's **declared** label
rests on.

**What was actually read to write the rules below.** The rendered text of the
committed synthetic transmittals, through the ingestion job's own parser, and
the committed field-label vocabulary — the one shared input FR-050 permits. Two
layouts are visible in that text and both are handled:

    inline   `Manufacturer: Norhelm Transformer Wks.`
    stacked  `Manufacturer:` on one line, the value on the next

and one further shape, the page-split field, where the label ends a page and the
value opens the next. Nothing here was copied from a template definition; the
rules are what a reader with the PDFs and no source access would write, which is
the only kind of opponent whose defeat carries information (AD-012).

**Why the vocabulary is a permitted input and the templates are not.** The
vocabulary is committed *data* naming what a field may be called; the templates
are the generator's *decisions* about what each vendor prints and where. An
extractor given the first still has to find the value, decide which item it
belongs to, and get the text right. An extractor given the second is reading the
answer.

**This module deliberately does not import `model.ingest.reference`.** That is
the answer key by another name, and it reaches `corpus.generate` anyway. The
label-to-term mapping below is therefore this module's own rather than shared
with it — an independent opponent writes its own — and
`src/model/tests/ingest/test_baseline.py` asserts the two agree, so a divergence
is a failing test rather than a baseline that silently stops finding a field.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from types import MappingProxyType
from typing import Final

from model.corpus.codes import VOCABULARY, fold_label

__all__ = [
    "BASELINE_ID",
    "BASELINE_INDEPENDENCE",
    "DOCUMENT_SCOPED_TERMS",
    "ITEM_DELIMITER_KEY",
    "TERM_BY_LABEL_KEY",
    "BaselinePage",
    "BaselineValue",
    "extract_document",
    "label_index",
]

#: Names the rule set, so a figure can say which opponent it was measured
#: against. Bumped when a rule changes, which is a re-measurement rather than a
#: refinement — an opponent improved after seeing the scoreboard is not the
#: opponent the declared label was fixed against.
BASELINE_ID: Final[str] = "e006-template-baseline/1"

#: FR-050's declared independence, in the words the report publishes. Here
#: rather than in `report.py` because the claim is about *this* module, and a
#: claim stated somewhere else is a claim nobody editing the code has to keep
#: true.
BASELINE_INDEPENDENCE: Final[str] = (
    "Authored from the rendered documents' text and the committed field-label "
    "vocabulary alone. Enforced by the import-linter contract 'The baseline extractor "
    "does not reach the corpus generator' — `model.corpus.templates`, "
    "`model.corpus.render` and `model.corpus.model` are forbidden, directly and "
    "indirectly (`allow_indirect_imports = false`) — declared before this extractor "
    "was written."
)

#: The printed label keys this baseline knows, mapped onto the seeded vocabulary
#: terms a value is stored under. The key space is the committed field-label
#: vocabulary's; the term space is `field_vocabulary`'s. Keys absent from this
#: mapping are labels the transmittal prints that the schema has no term for —
#: the contract number, project identifier, vendor name, descriptor code,
#: approving authority, revision suffix and date received — and they are skipped
#: rather than mapped onto the nearest term.
TERM_BY_LABEL_KEY: Mapping[str, str] = MappingProxyType(
    {
        "manufacturer": "manufacturer",
        "part_number": "part_number",
        "material_item": "product_description",
        "equipment_category": "material_category",
        "quantity": "quantity",
        "specification_section": "specification_section",
        "transmittal_number": "submittal_number",
        "action_stamp": "submittal_status",
        "date_submitted": "submittal_date",
        "date_returned": "approval_date",
    }
)

#: Terms this baseline files under item ordinal 0 — the declared group for
#: values a transmittal prints once for the whole document (FR-059). Read off
#: the rendered documents: these appear once, in the register block, never
#: against a numbered item.
DOCUMENT_SCOPED_TERMS: Final[frozenset[str]] = frozenset(
    {
        "submittal_number",
        "submittal_status",
        "submittal_date",
        "approval_date",
        "specification_section",
    }
)

#: The label that starts a new printed item. Every rendered transmittal opens
#: each item with it, so it is the item boundary a reader of the PDFs would
#: use — and using it means an item that split across two chunks is still one
#: item here, because the boundary is a printed label rather than a chunk edge.
ITEM_DELIMITER_KEY: Final[str] = "material_item"

#: The per-page citation anchor the renderer puts in the header band of every
#: page. Recognised so a page-split value is not mistaken for the anchor line:
#: when a label ends a page, the value is the first line of the next page that
#: is not this. Derived from the rendered text — it is on every page of every
#: document — not from the renderer's source.
_PAGE_ANCHOR = re.compile(r"\|\s*Page\s+\d+\s+of\s+\d+\s*$")

#: `Label:` optionally followed by the value on the same line. The colon is the
#: separator every observed layout uses; a layout that dropped it would not be
#: extractable by this baseline, and that is a fair loss rather than a defect —
#: an honest opponent is allowed to be beaten.
_LABELLED = re.compile(r"^(?P<label>[^:]{1,60}):(?P<value>.*)$")


@dataclass(frozen=True)
class BaselinePage:
    """One rendered page, as plain text lines.

    A local structure rather than `model.ingest.parse.ParsedPage`, and not for
    convenience: `parse` imports `corpus.derive`, which imports
    `corpus.templates`, so importing it here would break the independence
    contract through two hops. The caller reads the pages and hands over
    strings, which is also the honest shape — this baseline is given the
    rendered text and nothing else.
    """

    number: int
    lines: tuple[str, ...]

    def __post_init__(self) -> None:
        if self.number < 1:
            raise ValueError(f"page numbers are one-based; got {self.number}")


@dataclass(frozen=True)
class BaselineValue:
    """One value the baseline read, with where it read it.

    `page_number` is the page carrying the **printed value**, which is the same
    anchor rule FR-029 fixes for the model path. Scoring two extractors whose
    citations followed different rules would compare more than the extraction.
    """

    field_name: str
    value_text: str
    item_ordinal: int
    page_number: int


def label_index() -> Mapping[str, str]:
    """Folded printed label -> field-label-vocabulary key.

    Built over canonical **and** alternate labels, because
    `INCONSISTENT_FIELD_LABEL` is one of the five irregularity classes the
    synthetic layer carries: a baseline that recognised only canonical labels
    would score zero on every document carrying that class, and would be losing
    for a reason that has nothing to do with extraction.
    """
    index: dict[str, str] = {}
    for key in VOCABULARY.field_keys:
        labels = VOCABULARY.labels(key)
        index[fold_label(labels.canonical_label)] = key
        for alternate in labels.alternate_labels:
            index[fold_label(alternate)] = key
    return MappingProxyType(index)


def _body_lines(pages: Iterable[BaselinePage]) -> tuple[tuple[int, str], ...]:
    """Every page's lines with its page number, anchors removed.

    Flattened across pages on purpose. A page-split field is a label at the end
    of one page and its value at the start of the next, so a scan that stopped
    at each page boundary would read the label as a blank field — which is
    exactly the ambiguity E002's generator refuses to create and this baseline
    should not re-introduce.
    """
    return tuple(
        (page.number, line)
        for page in pages
        for line in page.lines
        if line.strip() and not _PAGE_ANCHOR.search(line)
    )


def extract_document(pages: Sequence[BaselinePage]) -> tuple[BaselineValue, ...]:
    """Read every value this baseline can find, in printed order (FR-050).

    Args:
        pages: the document's rendered pages, in order, as text lines.

    Returns:
        The values found, in the order they are printed. Item ordinals count
        from 1 in printed order; document-scoped terms take ordinal 0, which is
        the declared group rather than a sentinel.

    Raises:
        ValueError: `pages` is empty. An extractor handed nothing would report
            nothing, and "the baseline found no values" is a figure of 0/0 —
            which SC-047 admits nowhere.

    **Three layouts, one scan.** A label with text after the colon is an inline
    value. A label with nothing after it takes the next body line as its value,
    unless that line is itself a label — in which case the field is printed
    blank, which is `MISSING_OR_BLANK_FIELD` and not a value. The same rule
    resolves the page-split case for free, because the scan runs over the
    document's body lines rather than over each page separately.
    """
    if not pages:
        raise ValueError(
            "FR-068: the baseline was handed zero pages, so it would report zero values "
            "and every figure against it would rest on an empty denominator"
        )

    index = label_index()
    lines = _body_lines(pages)
    found: list[BaselineValue] = []
    item_ordinal = 0
    position = 0

    def key_at(offset: int) -> str | None:
        """The vocabulary key the line at `offset` labels, if it labels one."""
        if offset >= len(lines):
            return None
        matched = _LABELLED.match(lines[offset][1])
        if matched is None:
            return None
        return index.get(fold_label(matched.group("label").strip()))

    while position < len(lines):
        page_number, line = lines[position]
        matched = _LABELLED.match(line)
        if matched is None:
            position += 1
            continue

        key = index.get(fold_label(matched.group("label").strip()))
        if key is None:
            position += 1
            continue

        if key == ITEM_DELIMITER_KEY:
            item_ordinal += 1

        value = matched.group("value").strip()
        value_page = page_number
        consumed = 1
        # Stacked layout, or a page-split field. The following body line is the
        # value unless it is itself a label, which is how a blank field is told
        # apart from one whose value is on the next line.
        if not value and position + 1 < len(lines) and key_at(position + 1) is None:
            value_page, value = lines[position + 1]
            value = value.strip()
            consumed = 2

        term = TERM_BY_LABEL_KEY.get(key)
        if term is not None and value:
            found.append(
                BaselineValue(
                    field_name=term,
                    value_text=value,
                    item_ordinal=(0 if term in DOCUMENT_SCOPED_TERMS else max(item_ordinal, 1)),
                    page_number=value_page,
                )
            )
        position += consumed

    return tuple(found)
