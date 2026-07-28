"""Precision, recall, and the continuity-corrected Wilson interval. No F1.

FR-060, AD-011. Three decisions are fixed here and none of them is left to the
caller:

**The interval variant is the continuity-corrected Wilson score interval**, and
it is *named with every figure* rather than in a footnote — "Wilson 95%" alone
does not say which of the two was computed, and the two differ by exactly the
amount this epic's small denominators care about. Per-field denominators are
frequently under 20: Wald degenerates to `[0,0]` and `[1,1]` at the boundaries,
so "100% precision" from 7 of 7 would read as certainty. Wilson keeps both
bounds inside `[0,1]` and makes the small denominator visible.

**The correction is applied rather than its absence disclosed.** The research
records under-coverage at extreme proportions for very small *n* without it,
which is exactly this regime — denominators under 20 with precision expected
near 1 — and the corrected form errs toward over-coverage, which is the honest
direction under Principle II. The property that distinguishes the two
implementations is asserted in `src/model/tests/compute/test_metrics.py`: the
corrected interval is never narrower than the uncorrected one, and strictly
wider wherever neither bound is clamped.

**Two denominators, both printed, and they are different populations** (FR-060).
Precision is denominated on *the values the run stored* for that field and
layer; recall on *the fields the generator recorded as printed*. Denominating
recall on stored values would make it structurally unable to see a value that
was never stored, which is the only thing recall is for.

**No F1, and the omission is published with its reason.** A Wilson interval
inverts the score test for a binomial proportion. F1 is a harmonic mean of two
proportions with different denominators, so no interval for it exists — and
SC-029 admits no figure without one. Publishing F1 without an interval would
break the rule; publishing it with an interval borrowed from one of its two
inputs would be worse.

**There is no pooling function, and the absence is deliberate.** Pooling two
fields to manufacture a larger *n* is what the research rejects: it answers a
question nobody asked with a denominator nobody can interpret. A helper for it
would be used.

Pure: no clock, no locale, no database, no `model.ingest`, no `gateway`.
"""

from __future__ import annotations

import math
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final

__all__ = [
    "CONFIDENCE_LEVEL",
    "F1_OMISSION_REASON",
    "INTERVAL_METHOD",
    "PRECISION_DENOMINATOR",
    "RECALL_DENOMINATOR",
    "Z_95",
    "FieldCounts",
    "FieldFigures",
    "MetricsError",
    "Proportion",
    "per_field_figures",
    "wilson_interval",
]


class MetricsError(ValueError):
    """A figure cannot be computed, or must not be.

    Chief among the "must not": an empty denominator. SC-047 requires zero
    figures resting on one and zero layer rows blank or `0/0`, so a `0/0` is
    made unconstructible rather than rendered as a dash — the real layer is
    published as *not measured with its reason*, which is a different statement
    and has to look different.
    """


#: The two-sided normal quantile at 95%. Written to the precision the figures
#: are published at rather than as `1.96`: the rounded value shifts the fourth
#: decimal of a bound, which is inside the range these intervals are printed to.
Z_95: Final[float] = 1.959963984540054

CONFIDENCE_LEVEL: Final[str] = "95%"

#: Named with every figure (FR-060). A constant rather than a literal at each
#: call site so the report and the module cannot disagree about which variant
#: produced the numbers.
INTERVAL_METHOD: Final[str] = "continuity-corrected Wilson 95%"

#: FR-060's two denominators, stated once. Carried onto every `Proportion` so a
#: published figure prints *what* it was denominated on and not only how many.
PRECISION_DENOMINATOR: Final[str] = "values the run stored for this field and layer"
RECALL_DENOMINATOR: Final[str] = "fields the generator recorded as printed for this field and layer"

#: FR-060 requires the omission itself to be published, with this reason.
F1_OMISSION_REASON: Final[str] = (
    "F1 is not published. A Wilson interval inverts the score test for a binomial "
    "proportion; F1 is a harmonic mean of two proportions with different denominators "
    "— precision is denominated on stored values and recall on printed fields — so no "
    "interval for it exists, and SC-029 admits no figure without one. The omission is "
    "published here rather than left as an absence a reader has to notice."
)


def wilson_interval(successes: int, trials: int, *, z: float = Z_95) -> tuple[float, float]:
    """The continuity-corrected Wilson score interval (FR-060, AD-011).

    Args:
        successes: the numerator. `0 <= successes <= trials`.
        trials: the denominator, which must be positive — see the raise below.
        z: the two-sided normal quantile. Defaults to 95%, which is the only
            level this epic publishes; the parameter exists so the level is
            visible at the call site rather than compiled in.

    Returns:
        `(lower, upper)`, both inside `[0, 1]`, containing `successes / trials`,
        and of non-zero width even at 0 or *n* successes.

    Raises:
        MetricsError: a negative count, more successes than trials, or an empty
            denominator. The last is the one that matters: an interval on 0 of 0
            is not wide, it is undefined, and rendering it as `[0, 1]` would
            publish a figure that ranged over nothing (FR-068, SC-047).

    The form is Newcombe's: with `p = successes / trials` and `q = 1 - p`,

        lower = (2np + z² − 1 − z·sqrt(z² − 2 − 1/n + 4p(nq + 1))) / (2(n + z²))
        upper = (2np + z² + 1 + z·sqrt(z² + 2 − 1/n + 4p(nq − 1))) / (2(n + z²))

    with `lower` pinned to 0 when `p = 0` and `upper` pinned to 1 when `p = 1`.
    The pinning is part of the definition rather than a clamp bolted on: without
    it the corrected bound overshoots past the boundary it is bounding, which is
    not a wider interval but a wrong one.

    The radicands are floored at zero. Both are positive throughout the domain —
    the lower one is at least `z² − 2 − 1/n`, which exceeds zero for every
    `n >= 1` at this `z` — so the floor never fires on real input; it is there so
    a caller passing a smaller `z` gets a degenerate-but-valid interval rather
    than a domain error from `sqrt`.
    """
    if trials <= 0:
        raise MetricsError(
            f"FR-068 / SC-047: an interval on {successes} of {trials} has an empty "
            f"denominator. A figure that ranged over nothing is not published as a wide "
            f"interval — the population is published as not measured, with its reason."
        )
    if successes < 0:
        raise MetricsError(f"a count is non-negative; got successes={successes}")
    if successes > trials:
        raise MetricsError(
            f"successes must not exceed trials; got {successes} of {trials}. A "
            f"numerator larger than its denominator is a counting defect upstream, not "
            f"a proportion above one."
        )

    n = float(trials)
    p = successes / n
    q = 1.0 - p
    denominator = 2.0 * (n + z * z)

    lower_radicand = max(0.0, z * z - 2.0 - 1.0 / n + 4.0 * p * (n * q + 1.0))
    upper_radicand = max(0.0, z * z + 2.0 - 1.0 / n + 4.0 * p * (n * q - 1.0))

    lower = (2.0 * n * p + z * z - 1.0 - z * math.sqrt(lower_radicand)) / denominator
    upper = (2.0 * n * p + z * z + 1.0 + z * math.sqrt(upper_radicand)) / denominator

    if successes == 0:
        lower = 0.0
    if successes == trials:
        upper = 1.0
    return max(0.0, min(1.0, lower)), max(0.0, min(1.0, upper))


@dataclass(frozen=True)
class Proportion:
    """One published proportion with its denominator and its interval.

    There is no constructor that omits the denominator's *description*. FR-060
    requires both denominators stated and printed beside their figures, and a
    bare `6/7` states how many without saying what of — which is exactly the
    ambiguity between "7 values were stored" and "7 fields were printed" that
    the two denominators exist to keep apart.
    """

    name: str
    numerator: int
    denominator: int
    denominator_names: str

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise MetricsError("a published proportion carries a name")
        if not self.denominator_names.strip():
            raise MetricsError(
                f"FR-060: {self.name!r} publishes a denominator of {self.denominator} "
                f"without saying what it counts. Precision and recall are denominated "
                f"on different populations, and a bare count cannot say which."
            )
        # Delegated rather than restated: the interval refuses an empty
        # denominator, a negative count and a numerator above its denominator,
        # and a second copy of those rules here could disagree with it.
        wilson_interval(self.numerator, self.denominator)

    @property
    def point(self) -> float:
        return self.numerator / self.denominator

    @property
    def interval(self) -> tuple[float, float]:
        return wilson_interval(self.numerator, self.denominator)

    def rendered(self) -> str:
        """The figure as the report prints it: point, interval, and denominator.

        All three in one string, so a row cannot carry the number without the
        interval or the interval without what it was computed over.
        """
        low, high = self.interval
        return (
            f"{self.point:.3f} [{low:.3f}, {high:.3f}] "
            f"({self.numerator}/{self.denominator} {self.denominator_names}; "
            f"{INTERVAL_METHOD})"
        )


@dataclass(frozen=True)
class FieldCounts:
    """The four counts one field-and-layer cell is computed from.

    Kept as counts rather than as ready-made proportions because the two
    denominators are different populations and a single `(matched, total)` pair
    could only carry one of them — which is how recall silently acquires
    precision's denominator.
    """

    field: str
    layer: str
    stored: int
    stored_matching: int
    printed: int
    printed_recovered: int

    def __post_init__(self) -> None:
        if not self.field.strip() or not self.layer.strip():
            raise MetricsError("a per-field figure names both its field and its layer")


@dataclass(frozen=True)
class FieldFigures:
    """Precision and recall for one field on one layer, each with its interval."""

    field: str
    layer: str
    precision: Proportion
    recall: Proportion


def per_field_figures(observations: Iterable[FieldCounts]) -> tuple[FieldFigures, ...]:
    """Per-field precision and recall, in a deterministic order (FR-060).

    Sorted by `(layer, field)`, which is what makes the metamorphic relation
    hold: permuting the input permutes nothing in the output, so the published
    table cannot depend on the order the run happened to enumerate its fields.

    Raises:
        MetricsError: a field-and-layer cell appears twice, or a cell has an
            empty denominator on either side. The duplicate is refused because
            two rows for one cell is the shape a silent pooling would take; the
            empty denominator is refused because SC-047 admits no `0/0` row.
    """
    seen: set[tuple[str, str]] = set()
    figures: list[FieldFigures] = []
    for entry in observations:
        cell = (entry.layer, entry.field)
        if cell in seen:
            raise MetricsError(
                f"FR-060: {entry.field!r} on layer {entry.layer!r} appears twice. One "
                f"cell holds one figure; two would have to be pooled to be published, "
                f"and pooling to manufacture a larger denominator is what the method "
                f"rejects."
            )
        seen.add(cell)
        figures.append(
            FieldFigures(
                field=entry.field,
                layer=entry.layer,
                precision=Proportion(
                    name=f"precision — {entry.field} ({entry.layer})",
                    numerator=entry.stored_matching,
                    denominator=entry.stored,
                    denominator_names=PRECISION_DENOMINATOR,
                ),
                recall=Proportion(
                    name=f"recall — {entry.field} ({entry.layer})",
                    numerator=entry.printed_recovered,
                    denominator=entry.printed,
                    denominator_names=RECALL_DENOMINATOR,
                ),
            )
        )
    return tuple(sorted(figures, key=lambda figure: (figure.layer, figure.field)))
