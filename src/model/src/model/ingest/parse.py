"""Pages and page text — obtained by calling the one committed reader.

FR-007 / FR-008. This module is deliberately thin, and its thinness is the
requirement rather than a style preference. E002's `model.corpus.derive` already
pins the word-extraction tolerances, the line grouping, and the comparison
normalization, and `plan.md` records that a second normalization would be a
second answer. So the ingestion package obtains page text by **calling**
`read_document` and `PageContent.text` — it does not configure a second
assembly that resembles them. A caller needing the comparison form calls
`derive`'s `page_text` or `normalize_page_text` directly; this module wrapped
them until QC iteration 3, when the wrapper was found to have no caller.

What that rules out, concretely, is what `src/model/tests/ingest/test_single_page_reader.py`
asserts over this whole package (SC-037): no call to `extract_words`, no
tolerance mapping, no second normalization, no page-text assembly. Nothing here
joins words into lines or lines into a page; both joins are `derive`'s, reached
through its own dataclasses.

**Page numbers come from the parser** (FR-007). `PageContent.number` is
pdfplumber's 1-based page index, assigned while the document was open. No page
number is accepted from, requested of, or reconciled against a language model
anywhere in this package — the extraction path receives a chunk that already
carries its page and never returns one.

One error type, `ParseError`, wrapping `DeriveError` so a caller reports "this
document could not be read" without importing E002's error to catch it.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from model.corpus.derive import (
    DeriveError,
    PageContent,
    read_document,
)

__all__ = [
    "ParseError",
    "ParsedPage",
    "page_by_number",
    "read_pages",
]


class ParseError(ValueError):
    """Raised when a document cannot be read into pages.

    One type for every failure, as `DeriveError` is. A reader failure is never
    swallowed so the run can continue: a document silently reduced to zero pages
    is exempt from every rule asserted over its chunks.
    """


@dataclass(frozen=True)
class ParsedPage:
    """One page as the chunker sees it: its parser-assigned number and its lines.

    `lines` are `derive`'s own line groupings, in reading order, each already a
    string. They are carried rather than a single page string because FR-015
    assigns ordinals in reading order *within* a page and the structural
    detection of FR-012 reads line by line — collapsing to one string first and
    re-splitting it would be a second assembly of exactly the kind FR-008
    forbids.
    """

    number: int
    lines: tuple[str, ...]
    text: str

    @property
    def is_empty(self) -> bool:
        """True when the page yields no storable text.

        FR-015: such a page produces no chunk and consumes no ordinal, so the
        condition is named here rather than inferred from an empty join at the
        call site.
        """
        return not self.text.strip()


def _page(content: PageContent) -> ParsedPage:
    return ParsedPage(
        number=content.number,
        lines=tuple(line.text for line in content.lines),
        # `PageContent.text` is `derive`'s assembly. Restating the join here
        # would be the second page-text assembly SC-037 asserts the absence of.
        text=content.text,
    )


def read_pages(path: Path) -> tuple[ParsedPage, ...]:
    """Every page of a document, read through the committed reader (FR-007, FR-008).

    One open per document — `read_document` opens once and returns every page —
    so the chunker, the containment guard, and the report all read the same
    observation rather than three of them.
    """
    try:
        contents = read_document(Path(path))
    except DeriveError as exc:
        raise ParseError(f"cannot read pages from {path}: {exc}") from exc
    pages = tuple(_page(content) for content in contents)
    numbers = [page.number for page in pages]
    if numbers != list(range(1, len(pages) + 1)):
        # pdfplumber numbers from 1 and `read_document` enumerates from 1; an
        # off-by-one here is silent (research §Validating parser page
        # attribution), so it is asserted rather than assumed.
        raise ParseError(f"{path}: page numbers are not 1..{len(pages)}, found {numbers}")
    return pages


# `normalized_page_text` stood here and was deleted at QC iteration 3. It
# delegated to `derive`'s `page_text` / `normalize_page_text` and had no caller
# in production or in a test. An earlier round kept it on the argument that
# deleting it orphaned those two imports, and therefore that it was a
# load-bearing seam rather than dead code. That inverted cause and evidence:
# the two imports existed only to implement this function, so they are orphaned
# *by* the deletion rather than evidence against it, and a normalization route
# nothing takes normalizes nothing. SC-037 asserts the *absence* of a second
# normalization — `test_single_page_reader.py` — and removing a zero-caller
# wrapper cannot create one. Containment compares chunk text against the page
# text `derive` already assembled; whichever module next needs the comparison
# form should call `derive` directly, as this one did.
def page_by_number(pages: Sequence[ParsedPage], number: int) -> ParsedPage:
    """The page a chunk's recorded page number names.

    Addressed by the recorded number rather than by list position, which is what
    FR-010's fresh post-run extraction requires: the containment check must not
    consult the chunker's page-to-chunk mapping, and an index into the order the
    run happened to build is that mapping by another name.
    """
    for page in pages:
        if page.number == number:
            return page
    raise ParseError(f"no page numbered {number} among {[page.number for page in pages]}")
