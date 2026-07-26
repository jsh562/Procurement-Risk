"""Page-image degradation with the retained text layer — the searchable scan.

FR-032 / AD-003. A degraded page here is a *searchable* scan, which is three
things at once and fails if any of them is missing:

1. the body region carries a raster image that has been blurred, contrast-
   reduced, speckled and rotated by a bounded amount;
2. the identical body text is re-laid beneath that raster at PDF text render
   mode 3, so extraction recovers the whole page **without recognition**; and
3. the citation anchor — the document identifier and the page number — is drawn
   as ordinary visible text in the header band, outside every raster rectangle.

The third is a property of the *layout*, not of this module: `render.py` draws
the anchor into `HEADER_BAND` before handing the page over, and this module is
only ever given `BODY_RECT` to cover. Keeping the guarantee there rather than
here is what stops it regressing silently the first time a raster grows
(AD-003), and `degrade_page` refuses a body rectangle that reaches into the
header band rather than trusting its caller.

**The raster is an image of the same layout, not an approximation of it.** Both
the raster and the invisible text come from `render.body_line_layout`, so the
pixels and the text layer cannot disagree about where a line sits — a raster
laid out independently would look like a scan of a different document.

**Nothing here is random at run time.** The speckle stream is seeded from the
profile name and its parameters, so two runs of one model produce identical
pixels and therefore identical PDF bytes. A degradation that drew from the
default random stream would make FR-021's reproducibility claim false for a
reason no reader could see.

Pillow is pinned as tightly as ReportLab in the modeling boundary's lockfile
(HINT-005): a filter implementation change moves every pixel and therefore
every byte-level hash, which is a regeneration event rather than a corpus
defect (FR-021a).
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from functools import lru_cache

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from reportlab.lib.utils import ImageReader

from model.corpus.render import (
    HEADER_BAND,
    UNDEGRADED_PROFILE,
    PageRender,
    body_line_layout,
    draw_body_lines,
)

__all__ = [
    "PARAMETER_NAMES",
    "PAPER_LEVEL",
    "RASTER_DPI",
    "RASTER_MODE",
    "DegradeError",
    "body_raster",
    "degrade_page",
    "degraded_raster",
]

#: Points per inch. Fixed here rather than read from the generation
#: configuration: the configuration enumerates what may be *injected* — the
#: profiles and their parameter ranges — while the sampling resolution is a
#: property of this construction, and moving it would move every emitted byte
#: without any recorded value changing.
RASTER_DPI = 100

#: Greyscale. A filed scan is not colour, and one channel is a third of the
#: bytes of three for a corpus that is committed rather than fetched (SC-014).
RASTER_MODE = "L"

#: The paper background and the ink, in `RASTER_MODE` levels.
PAPER_LEVEL = 255
INK_LEVEL = 20

#: The four parameters every declared profile ranges over. Named here so a
#: profile carrying a parameter this module does not apply is a loud failure
#: rather than a silently ignored key — a parameter inside the reproducibility
#: hash (DM-2) that changed no pixel would make VR-040b pass over nothing.
PARAMETER_NAMES: tuple[str, ...] = (
    "blur_radius_tenths",
    "contrast_percent",
    "noise_percent",
    "rotation_millidegrees",
)


class DegradeError(ValueError):
    """Raised when a page cannot be degraded as the searchable-scan construction.

    One type for every failure, as `RenderError` and `ManifestError` are: a
    caller learns the same thing from each of them — this page must not be
    written, and no entry may record `SCAN_DEGRADATION` for it.
    """


@lru_cache(maxsize=32)
def _font(pixel_size: int) -> ImageFont.ImageFont:
    """Pillow's bundled default face at a pixel size.

    Bundled rather than a system font on purpose: a system font is a property
    of the machine, and a raster whose glyphs depend on which fonts happen to
    be installed is not byte-reproducible across the development machine and
    the verification runner.
    """
    try:
        return ImageFont.load_default(size=max(1, pixel_size))
    except TypeError:  # pragma: no cover - Pillow below 10.1; the pin is far above it
        return ImageFont.load_default()


def _scale(points: float) -> float:
    return points * RASTER_DPI / 72.0


def body_raster(render: PageRender) -> Image.Image:
    """A clean image of the page's body region, before any degradation.

    Exported because it is the **control** VR-050 judges a degraded raster
    against: "the body raster differs from the control" is only an assertion if
    the undegraded raster of the same body can be produced and compared. It also
    keeps the degradation pipeline separable — the layout is tested by
    comparing this against the vector render, and the filters by comparing this
    against their own output.
    """
    left, bottom, right, top = render.body_rect
    width = max(1, int(round(_scale(right - left))))
    height = max(1, int(round(_scale(top - bottom))))
    image = Image.new(RASTER_MODE, (width, height), PAPER_LEVEL)
    surface = ImageDraw.Draw(image)
    for placed in body_line_layout(render.template, render.lines, body_rect=render.body_rect):
        size = max(1, int(round(_scale(placed.size))))
        # PIL's default anchor is the ascender, and the layout works in
        # baselines, so the ascent is subtracted rather than the two being
        # silently one pixel-row apart.
        surface.text(
            (_scale(placed.x - left), _scale(top - placed.baseline) - size),
            placed.text,
            font=_font(size),
            fill=INK_LEVEL,
        )
    return image


def _speckle(image: Image.Image, percent: int, rng: random.Random) -> Image.Image:
    """Scatter `percent` of the pixels, deterministically.

    Point sampling rather than a full-frame noise field: the share of touched
    pixels is the declared parameter, and generating one draw per pixel would
    make the cost of the smallest admissible noise level equal to the largest.
    """
    if percent <= 0:
        return image
    width, height = image.size
    speckled = image.copy()
    pixels = speckled.load()
    for _ in range(int(width * height * percent / 100)):
        pixels[rng.randrange(width), rng.randrange(height)] = rng.randrange(256)
    return speckled


def degraded_raster(
    image: Image.Image, profile: str, parameters: Mapping[str, str | int]
) -> Image.Image:
    """Apply one declared profile's parameters to a clean body raster.

    Every one of `PARAMETER_NAMES` must be supplied. A missing parameter is a
    failure rather than a default: the parameters are inside
    `document_model_hash`, so a defaulted value would be a degradation decision
    the reproducibility comparison never sees (DM-2).
    """
    if not isinstance(profile, str) or not profile.strip():
        raise DegradeError(f"a degradation profile must be named, found {profile!r}")
    if profile == UNDEGRADED_PROFILE:
        raise DegradeError(
            f"{UNDEGRADED_PROFILE!r} is the renderer's reserved name for a page carrying no "
            "degradation and must never reach this module"
        )
    missing = [name for name in PARAMETER_NAMES if name not in parameters]
    if missing:
        raise DegradeError(
            f"degradation profile {profile!r} supplied no value for {missing}; every declared "
            "parameter is inside the reproducibility hash and none may be defaulted"
        )
    values: dict[str, int] = {}
    for name in PARAMETER_NAMES:
        raw = parameters[name]
        if not isinstance(raw, int) or isinstance(raw, bool):
            raise DegradeError(
                f"degradation parameter {name!r} must be an integer, found {type(raw).__name__}; "
                "a float's shortest representation is a property of the platform"
            )
        values[name] = raw

    # Seeded from what the model records and from nothing else, so the pixels
    # are a function of the recorded directive rather than of the run.
    # Suppression rationale: seeded from the recorded directive so the pixels are a function of
    # what the manifest states, not of when the run happened. A cryptographic
    # generator would make the same document render differently every time and
    # break VR-041's byte-identity comparison.
    rng = random.Random(  # noqa: S311
        f"{profile}:" + ":".join(f"{name}={values[name]}" for name in PARAMETER_NAMES)
    )

    result = image
    blur = values["blur_radius_tenths"] / 10.0
    if blur > 0:
        result = result.filter(ImageFilter.GaussianBlur(radius=blur))
    contrast = values["contrast_percent"] / 100.0
    if contrast != 1.0:
        result = ImageEnhance.Contrast(result).enhance(contrast)
    result = _speckle(result, values["noise_percent"], rng)
    rotation = values["rotation_millidegrees"] / 1000.0
    if rotation != 0.0:
        result = result.rotate(
            rotation, resample=Image.Resampling.BILINEAR, expand=False, fillcolor=PAPER_LEVEL
        )
    return result


def degrade_page(render: PageRender) -> None:
    """Render one page as a searchable scan (FR-032).

    The `degrader` seam `render.render_document` calls for any page whose
    directive names a profile other than `UNDEGRADED_PROFILE`.

    Order is load-bearing and stated rather than incidental: the text layer is
    drawn **first**, at render mode 3, and the raster is drawn over it. That is
    the construction a genuinely filed searchable scan has, and it keeps the
    body text beneath the image rather than beside it.
    """
    if not isinstance(render, PageRender):
        raise DegradeError(f"expected a PageRender, found {type(render).__name__}")

    left, bottom, right, top = render.body_rect
    if right <= left or top <= bottom:
        raise DegradeError(f"the body rectangle is empty or inverted: {render.body_rect}")
    # FR-032, asserted here as well as guaranteed by the layout: the raster may
    # never reach the band the citation anchor lives in. A caller passing a
    # taller rectangle would silently cover the one text object the whole
    # construction exists to keep readable.
    if top > HEADER_BAND[1]:
        raise DegradeError(
            f"the body rectangle reaches {top} but the citation anchor's band begins at "
            f"{HEADER_BAND[1]}; a raster must not cover the page number or document identifier"
        )

    draw_body_lines(
        render.canvas,
        render.template,
        render.lines,
        body_rect=render.body_rect,
        invisible=True,
    )
    raster = degraded_raster(body_raster(render), render.profile, render.parameters)
    render.canvas.drawImage(
        ImageReader(raster),
        left,
        bottom,
        width=right - left,
        height=top - bottom,
        preserveAspectRatio=False,
        anchor="sw",
        mask=None,
    )
