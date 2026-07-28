"""The terminal sentence split, below a paragraph, with character spans kept.

FR-014 / AD-003. pySBD is the last rung of the boundary ladder: when a
subparagraph still exceeds the encoder window, it is cut at sentence boundaries,
and only then. Everything about the configuration below is load-bearing:

- **`clean=False`** — `clean=True` rewrites the text. That would break page
  containment (FR-010 compares chunk text against the page's own extraction) and
  the offset arithmetic that maps a sentence back into its parent unit.
- **`char_span=True`** — every sentence carries its offsets in the parent, so a
  fragment can be located rather than merely produced.
- **`language="en"`** — declared rather than defaulted.

**Invoked only on units already over the cap.** pySBD is a regex cascade and is
slow relative to the structural detection above it (research §Deterministic
sentence segmentation), so calling it on every paragraph would cost far more
than it buys. The caller — `chunker.py` — descends to this rung only after the
structural rungs have failed to make a unit fit.

**A pySBD upgrade is a chunker-version bump** (FR-017). The version is exported
here rather than left implicit, and `chunker.CHUNKER_VERSION` incorporates it, so
an upgrade changes the recorded version mechanically instead of relying on
someone remembering the rule.

Purely rule-based: no model, no download, no training data, so identical input
yields identical output across runs and machines — which is what FR-017's
determinism claim rests on and why a statistical splitter was rejected.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import pysbd

__all__ = [
    "SEGMENTER_ID",
    "SEGMENTER_VERSION",
    "Sentence",
    "SegmentError",
    "sentences",
]

#: Identity and pinned version of the segmenter, both members of FR-017's
#: chunker-version rule.
SEGMENTER_ID = "pysbd"
SEGMENTER_VERSION = pysbd.__version__


class SegmentError(ValueError):
    """Raised when a unit cannot be segmented into sentences.

    One type for every failure. A segmentation that silently returns the whole
    unit as one sentence is not an error — it is a real outcome, and it is what
    makes FR-014's over-long-sentence failure reachable.
    """


@dataclass(frozen=True)
class Sentence:
    """One sentence and where it sits in the unit it came from.

    `start` and `end` are character offsets into the parent unit's text,
    half-open, so `parent[start:end]` is the sentence as printed — including any
    trailing space pySBD's span covers. The text is carried alongside rather
    than recomputed by the caller so the two cannot disagree.
    """

    text: str
    start: int
    end: int


@lru_cache(maxsize=1)
def _segmenter() -> pysbd.Segmenter:
    """One segmenter per process.

    Cached for cost, not for state: the segmenter is configured identically on
    every call and holds no per-document memory that could leak between units.
    """
    return pysbd.Segmenter(language="en", clean=False, char_span=True)


def sentences(text: str) -> tuple[Sentence, ...]:
    """Split a unit into sentences, keeping each one's span in the parent.

    Returns a single sentence covering the whole input when no interior boundary
    is found — designation-heavy specification strings are exactly the residual
    pySBD misses (research §Deterministic sentence segmentation), and that
    outcome must reach the caller as "this leaf did not divide" so FR-014's
    fail-closed rule can fire, rather than being smoothed into an arbitrary cut.
    """
    if not isinstance(text, str):
        raise SegmentError(f"a unit is segmented from a string, found {type(text).__name__}")
    if not text.strip():
        return ()
    try:
        spans = _segmenter().segment(text)
    except Exception as exc:  # noqa: BLE001 - any segmenter failure is this module's
        raise SegmentError(f"cannot segment a unit of {len(text)} characters: {exc}") from exc

    found: list[Sentence] = []
    for span in spans:
        sentence = Sentence(text=span.sent, start=int(span.start), end=int(span.end))
        if not sentence.text.strip():
            continue
        if text[sentence.start : sentence.end] != sentence.text:
            raise SegmentError(
                f"pySBD reported a span {sentence.start}:{sentence.end} that does not "
                "hold the sentence it returned; offsets into the parent unit would be wrong"
            )
        found.append(sentence)
    if not found:
        return (Sentence(text=text, start=0, end=len(text)),)
    return tuple(found)
