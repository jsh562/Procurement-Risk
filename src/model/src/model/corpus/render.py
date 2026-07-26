"""The deterministic ReportLab canvas.

FR-021a / FR-021b / FR-032. This module turns a `DocumentModel` into a PDF and
is the only place in the epic that writes one. Three properties are load-bearing
and each is a decision rather than a default:

**Nothing here reads a clock.** The canvas is opened with `invariant=True`,
which feeds one fixed timestamp into `CreationDate`, `ModDate`, and the md5
that becomes the `/ID` trailer, so the file identifier is content-derived
rather than clock- or path-seeded (AD-001). The producer, creator, title, and
author strings are set explicitly, because ReportLab's defaults carry its own
version and would turn a routine dependency bump into a corpus-wide byte
change without any content having moved. Byte-identity rests on the version
pinned in the modeling boundary's lockfile; a differing pin is a regeneration
event, not a validation failure (FR-021a, VR-041).

`SOURCE_DATE_EPOCH` is **neutralised for the duration of the render**, not
merely ignored. ReportLab honours it even under `invariant`, so a machine that
happened to export it would emit different bytes for identical content — a
byte-identity claim that depends on an ambient environment variable is not a
claim about the corpus. The prior value is restored, so a caller's environment
is unchanged on the way out.

**Only base-14 fonts are used.** Font subset prefixes are assigned by subset
order, so a glyph-set change reshuffles tags across the whole file and moves
every byte-level hash (HINT-005). Helvetica and its bold and oblique faces are
not embedded and not subset, so no such tag exists to move.

**The citation anchor is drawn in the header band, outside `BODY_RECT`.**
FR-032 requires the page number and document identifier to remain an
undegraded text object on every page, and `BODY_RECT` is the one region the
Phase-6 degradation injector may cover. Keeping the anchor above it makes the
guarantee a property of the layout rather than a masking trick that could
regress silently the first time a raster grew (AD-003).

**The degradation seam is present and empty.** A page whose render directive
names a profile other than `UNDEGRADED_PROFILE` is not rendered here: it is
handed to the caller's `degrader` together with the canvas, the body rectangle,
and the same `Line` objects the undegraded path would draw, so the injector can
raster the body and re-lay the identical text beneath it at render mode 3. With
no degrader supplied such a page raises rather than rendering clean — a
silently undegraded page would satisfy every text rule while carrying none of
the property `SCAN_DEGRADATION` records.
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from reportlab.lib.pagesizes import LETTER
from reportlab.pdfgen import canvas as pdfcanvas

from model.corpus.model import DocumentModel
from model.corpus.templates import LayoutTemplate, Line, TemplateError, page_text, template

__all__ = [
    "BODY_RECT",
    "HEADER_BAND",
    "PAGE_SIZE",
    "PRODUCER",
    "TEXT_RENDER_INVISIBLE",
    "TEXT_RENDER_VISIBLE",
    "UNDEGRADED_PROFILE",
    "DocumentLayout",
    "PageRender",
    "PlacedText",
    "RenderError",
    "body_line_layout",
    "draw_body_lines",
    "render_document",
]

PAGE_SIZE: tuple[float, float] = (float(LETTER[0]), float(LETTER[1]))
_WIDTH, _HEIGHT = PAGE_SIZE

MARGIN = 54.0
HEADER_BAND_HEIGHT = 54.0

# The band the citation anchor lives in: the top of the page, above every
# rectangle a raster may occupy. Exported so the degradation injector asserts
# against the same numbers rather than a second copy of them.
HEADER_BAND: tuple[float, float, float, float] = (
    MARGIN,
    _HEIGHT - HEADER_BAND_HEIGHT,
    _WIDTH - MARGIN,
    _HEIGHT,
)
# The body region: everything a degraded page may replace with a raster.
BODY_RECT: tuple[float, float, float, float] = (
    MARGIN,
    MARGIN,
    _WIDTH - MARGIN,
    _HEIGHT - HEADER_BAND_HEIGHT,
)

ANCHOR_BASELINE = _HEIGHT - 36.0
ANCHOR_FONT_SIZE = 8

# PDF text render modes. 3 is "neither fill nor stroke" — the invisible text
# layer that sits beneath a scanned raster and keeps the page extractable
# without recognition (AD-003).
TEXT_RENDER_VISIBLE = 0
TEXT_RENDER_INVISIBLE = 3

# The reserved profile name meaning "this page is not degraded". Held here
# rather than in the generation configuration because it is a property of the
# renderer's contract with the model, not a tunable: the configuration
# enumerates the profiles that *may be injected*, and the generator asserts
# that none of them is called this.
UNDEGRADED_PROFILE = "NONE"

PRODUCER = "model.corpus.render (E002 synthetic corpus generator)"
CREATOR = "model.corpus.generate"

_FONT_REGULAR = "Helvetica"
_FONT_BOLD = "Helvetica-Bold"
_FONT_OBLIQUE = "Helvetica-Oblique"

# Per style: font, a size resolved against the template's body size, and the
# left indent from the body rectangle's left edge. A closed table, so a style
# the templates admit but this module does not know fails loudly.
_STYLE_FONTS: Mapping[str, str] = {
    "title": _FONT_BOLD,
    "subtitle": _FONT_OBLIQUE,
    "heading": _FONT_BOLD,
    "field_inline": _FONT_BOLD,
    "field_label": _FONT_BOLD,
    "field_value": _FONT_REGULAR,
    "text": _FONT_REGULAR,
}
_STYLE_SIZE_DELTA: Mapping[str, int] = {
    "title": 3,
    "subtitle": -1,
    "heading": 1,
    "field_inline": 0,
    "field_label": 0,
    "field_value": 0,
    "text": 0,
}
_STYLE_INDENT: Mapping[str, float] = {
    "title": 0.0,
    "subtitle": 0.0,
    "heading": 0.0,
    "field_inline": 0.0,
    "field_label": 0.0,
    "field_value": 12.0,
    "text": 0.0,
}
# Extra vertical space *before* a line of this style, in multiples of leading.
_STYLE_SPACE_BEFORE: Mapping[str, float] = {
    "title": 0.5,
    "subtitle": 0.0,
    "heading": 0.75,
    "field_inline": 0.0,
    "field_label": 0.25,
    "field_value": 0.0,
    "text": 0.25,
}


class RenderError(ValueError):
    """Raised when a document cannot be rendered deterministically or at all.

    One type for every failure, as `RosterError` and `ManifestError` are: a
    caller learns the same thing from each of them — this document must not be
    written, and no manifest entry may be built for it.
    """


@dataclass(frozen=True)
class PageRender:
    """Everything the degradation injector needs about one page.

    Passed to the caller's `degrader` instead of the injector reaching back
    into this module's internals. It carries the same `Line` objects the
    undegraded path draws, so the text layer the injector re-lays beneath its
    raster is the identical text — which is what VR-050's "extracted text layer
    identical to the control" is asserted against.
    """

    canvas: Any
    template: LayoutTemplate
    lines: tuple[Line, ...]
    profile: str
    parameters: Mapping[str, str | int]
    body_rect: tuple[float, float, float, float]
    page_number: int
    page_count: int
    anchor: str


@contextmanager
def _without_source_date_epoch():
    """Render with `SOURCE_DATE_EPOCH` unset, then restore it.

    ReportLab reads this variable even when `invariant` is on, so leaving it in
    place would make identical content render to different bytes on two
    machines. Popped rather than overwritten, and restored exactly, so a caller
    that relies on it elsewhere is unaffected.
    """
    sentinel = object()
    previous = os.environ.pop("SOURCE_DATE_EPOCH", sentinel)
    try:
        yield
    finally:
        if previous is not sentinel:
            os.environ["SOURCE_DATE_EPOCH"] = previous  # type: ignore[assignment]


def _draw_text(
    target: Any,
    x: float,
    y: float,
    text: str,
    font: str,
    size: float,
    mode: int,
) -> None:
    """One string, one baseline, at a stated render mode.

    A text object rather than `drawString` because only a text object exposes
    the render mode, and the invisible layer of AD-003 is exactly a render-mode
    change over otherwise identical drawing calls.
    """
    obj = target.beginText()
    obj.setTextRenderMode(mode)
    obj.setFont(font, size)
    obj.setTextOrigin(x, y)
    obj.textOut(text)
    target.drawText(obj)


def _line_height(layout: LayoutTemplate, line: Line) -> float:
    return layout.leading * (1.0 + _STYLE_SPACE_BEFORE.get(line.style, 0.0))


@dataclass(frozen=True)
class PlacedText:
    """One string, at the position and size the body layout puts it.

    The unit both consumers of the body layout work in: the canvas draws these
    at a render mode, and the degradation injector rasters the same list at the
    same coordinates. Two independent layouts would be two answers to "where
    does this line sit", and a raster that disagreed with the text beneath it
    would look like a scan of a *different* document.
    """

    text: str
    x: float
    baseline: float
    font: str
    size: float


def body_line_layout(
    layout: LayoutTemplate,
    lines: Sequence[Line],
    *,
    body_rect: tuple[float, float, float, float] = BODY_RECT,
) -> tuple[PlacedText, ...]:
    """Where every body string goes, top-to-bottom inside `body_rect`.

    Anchor lines are skipped here — the anchor is drawn once, in the header
    band, by `render_document`, and drawing it again inside the body would put
    a copy of it under the raster and defeat FR-032's guarantee.

    Overflow raises rather than spilling or truncating. A silently dropped line
    would break VR-039, which compares extracted page text against the model's,
    and the model is fixed before rendering begins.
    """
    left, bottom, _right, top = body_rect
    cursor = top
    placed: list[PlacedText] = []
    for line in lines:
        if line.style == "anchor":
            continue
        font = _STYLE_FONTS.get(line.style)
        if font is None:
            raise RenderError(f"no drawing rule for line style {line.style!r}")
        size = float(layout.body_font_size + _STYLE_SIZE_DELTA[line.style])
        cursor -= _line_height(layout, line)
        if cursor < bottom:
            raise RenderError(
                f"page content overflows the body rectangle at line {line.text!r}; "
                "the generator must split the page rather than the renderer truncate it"
            )
        indent = _STYLE_INDENT[line.style]
        if line.style == "field_inline":
            placed.append(PlacedText(line.label, left + indent, cursor, _FONT_BOLD, size))
            if line.value:
                placed.append(
                    PlacedText(
                        line.value,
                        left + indent + layout.label_width,
                        cursor,
                        _FONT_REGULAR,
                        size,
                    )
                )
        else:
            placed.append(PlacedText(line.text, left + indent, cursor, font, size))
    return tuple(placed)


def draw_body_lines(
    target: Any,
    layout: LayoutTemplate,
    lines: Sequence[Line],
    *,
    body_rect: tuple[float, float, float, float] = BODY_RECT,
    invisible: bool = False,
) -> None:
    """Draw a page's body lines top-to-bottom inside `body_rect`.

    Public because the degradation injector must lay the identical text beneath
    its raster: calling this with `invisible=True` produces the same glyph
    positions at render mode 3, so the extracted text layer of a degraded page
    is byte-for-byte the extracted text of its undegraded control.
    """
    mode = TEXT_RENDER_INVISIBLE if invisible else TEXT_RENDER_VISIBLE
    for placed in body_line_layout(layout, lines, body_rect=body_rect):
        _draw_text(target, placed.x, placed.baseline, placed.text, placed.font, placed.size, mode)


@dataclass(frozen=True)
class DocumentLayout:
    """A document model paired with the lines each of its pages was composed of.

    The pairing is **asserted, not assumed**: every page's `page_text(lines)`
    must equal the text the model records for that page, checked here, once,
    before anything is drawn. That is what makes VR-039 an assertion about
    rendering rather than about two independent transcriptions of one document
    — the renderer draws these `Line` objects and the model hashes their text,
    and a divergence between them is impossible to commit rather than merely
    unlikely.

    Carrying the lines beside the model is deliberate in the other direction
    too: the model holds text and render directives because that is what the
    reproducibility hash covers (DM-1), and styling is not in the hash. Two
    documents differing only in which style a line was drawn in would be one
    document to FR-021, which is correct, so the styling has to travel outside
    the model.
    """

    model: DocumentModel
    pages: tuple[tuple[Line, ...], ...]

    def __post_init__(self) -> None:
        pages = tuple(tuple(lines) for lines in self.pages)
        if len(pages) != len(self.model.pages):
            raise RenderError(
                f"the layout carries {len(pages)} page(s) and the model {len(self.model.pages)}"
            )
        for index, (lines, page) in enumerate(zip(pages, self.model.pages, strict=True), start=1):
            if not lines:
                raise RenderError(f"page {index} carries no lines")
            for line in lines:
                if not isinstance(line, Line):
                    raise RenderError(
                        f"page {index} carries a {type(line).__name__}, expected a Line"
                    )
            if lines[0].style != "anchor":
                raise RenderError(
                    f"page {index} does not begin with its citation anchor; FR-032 requires "
                    "the document identifier and page number outside the body region"
                )
            if sum(1 for line in lines if line.style == "anchor") != 1:
                raise RenderError(f"page {index} carries more than one anchor line")
            composed = page_text(lines)
            if composed != page.text:
                raise RenderError(
                    f"page {index} text does not match the model it was hashed from:\n"
                    f"  model:  {page.text!r}\n"
                    f"  layout: {composed!r}"
                )
        object.__setattr__(self, "pages", pages)


def render_document(
    layout: DocumentLayout,
    target: Path,
    *,
    degrader: Callable[[PageRender], None] | None = None,
) -> Path:
    """Render one laid-out document to a PDF at `target` (FR-021b).

    PDF is the only emitted format. The whole file is written under one
    `invariant` canvas with an explicit producer, so two runs of one model on
    one pinned renderer produce identical bytes.
    """
    if not isinstance(layout, DocumentLayout):
        raise RenderError(f"expected a DocumentLayout, found {type(layout).__name__}")
    model = layout.model
    path = Path(target)
    identifier = model.identity.get("document_id")
    if not identifier:
        raise RenderError("a document model must carry a document_id identity field")

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise RenderError(f"cannot create {path.parent}: {exc}") from exc

    page_count = len(model.pages)
    with _without_source_date_epoch():
        try:
            surface = pdfcanvas.Canvas(str(path), pagesize=PAGE_SIZE, invariant=True)
            surface.setProducer(PRODUCER)
            surface.setCreator(CREATOR)
            surface.setAuthor(CREATOR)
            surface.setTitle(identifier)
            surface.setSubject(model.identity.get("submittal_number", identifier))

            for index, (page, lines) in enumerate(
                zip(model.pages, layout.pages, strict=True), start=1
            ):
                page_template = template(page.directive.template_id)
                anchor = lines[0].text

                # FR-032: the citation anchor, in the header band, outside
                # every rectangle a raster may occupy. Drawn before the body so
                # that it exists on the page whatever the body path does.
                _draw_text(
                    surface,
                    HEADER_BAND[0],
                    ANCHOR_BASELINE,
                    anchor,
                    _FONT_REGULAR,
                    ANCHOR_FONT_SIZE,
                    TEXT_RENDER_VISIBLE,
                )

                if page.directive.degradation_profile == UNDEGRADED_PROFILE:
                    draw_body_lines(surface, page_template, lines)
                elif degrader is None:
                    raise RenderError(
                        f"page {index} of {identifier} requests degradation profile "
                        f"{page.directive.degradation_profile!r} but no degrader was supplied; "
                        "rendering it clean would record a class the document does not carry"
                    )
                else:
                    degrader(
                        PageRender(
                            canvas=surface,
                            template=page_template,
                            lines=lines,
                            profile=page.directive.degradation_profile,
                            parameters=page.directive.parameters,
                            body_rect=BODY_RECT,
                            page_number=index,
                            page_count=page_count,
                            anchor=anchor,
                        )
                    )
                surface.showPage()
            surface.save()
        except TemplateError as exc:
            raise RenderError(f"cannot render {identifier}: {exc}") from exc
        except OSError as exc:
            raise RenderError(f"cannot write {path}: {exc}") from exc
    return path
