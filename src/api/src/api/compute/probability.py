"""Turning a stored probability into something honest to display.

FR-005, FR-006, FR-007, FR-008, FR-013, FR-017, FR-030, FR-053.

Three rules, each closing a specific way a percentage lies:

**Whole percent.** Several thousand draws support one percent and no more. A
single draw out of four thousand is 0.025%, so a figure like `34.7%` asserts
resolution the artifact does not have.

**Bounded forms at the extremes.** `<1%` and `>99%`, never `0%` or `100%` — and
this holds for a stored probability of *exactly* zero or one as much as for one
that merely rounds there. Several thousand draws cannot evidence a certainty;
an exact zero in the array is itself an estimate at the resolution the draw
count supports. There is no endpoint at which the bounded form is skipped.

**The complement is subtracted from the displayed integer**, never rounded a
second time from the stored value. Independent rounding gives pairs summing to
99 or 101, which reads as an arithmetic error and destroys the credibility of
the dual framing FR-006 exists to provide.

The pair sums to one hundred only where **both** directions are unbounded
integers. At a bounded form there is no integer to subtract from — `<1%` pairs
with `>99%` — and a bounded value paired with a flat certainty would reintroduce
through the complement exactly what the bound removes.

Every figure carries its **reference class**. A bare percentage is read as a
confidence — "how sure are you?" — rather than as a frequency, which is the
misreading the research on non-expert probability display names most directly.
The class travels with the figure rather than being applied by whichever
renderer remembers to.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from typing import Final

__all__ = [
    "LOWER_BOUND_DISPLAY",
    "REFERENCE_CLASS",
    "UPPER_BOUND_DISPLAY",
    "PercentFigure",
    "complement",
    "percent_figure",
]

LOWER_BOUND_DISPLAY: Final[str] = "<1%"
UPPER_BOUND_DISPLAY: Final[str] = ">99%"

#: FR-005. Frequency framing with an explicit denominator, because "35%" and
#: "35 out of 100" are read as different kinds of claim by the people this
#: product is for — the second as a rate, the first as a degree of belief.
REFERENCE_CLASS: Final[str] = "out of 100 lines like this one"


@dataclass(frozen=True)
class PercentFigure:
    """A probability in the only forms this product will display it.

    ``percent`` is ``None`` exactly when ``bounded`` is true. That is structural
    rather than conventional: a bounded figure has no integer, and a field
    holding ``0`` alongside ``bounded=True`` is the placeholder FR-054 forbids —
    one renderer away from a screen reading `0%`.
    """

    display: str
    bounded: bool
    percent: int | None
    reference_class: str = REFERENCE_CLASS

    def __post_init__(self) -> None:
        if self.bounded != (self.percent is None):
            raise ValueError(
                "A bounded figure carries no integer and an unbounded one must carry its "
                f"integer; got bounded={self.bounded} with percent={self.percent}. Admitting "
                "the pair would let a bounded figure render its placeholder."
            )


def percent_figure(stored: float) -> PercentFigure:
    """Render a stored probability at whole percent, bounding the extremes.

    Args:
        stored: A probability in [0, 1], as `ck_line_posterior__survival_unit_interval`
            stores it.

    Raises:
        ValueError: If ``stored`` is outside the unit interval. Unreachable from
            storage, so reaching it means a caller computed it — and clamping
            would turn a computation defect into a plausible-looking figure.
    """
    if not 0.0 <= stored <= 1.0:
        raise ValueError(
            f"{stored!r} is outside the unit interval. Storage cannot produce it, so this is a "
            "computation defect; clamping it would hide the defect behind a figure that looks "
            "entirely reasonable on screen."
        )

    # Decimal, not float. `round()` is half-to-even, so a stored 0.125 would
    # render 12% where FR-008 states 13% — and every value not exactly on a half
    # would agree, so the defect would be invisible outside the two cases the
    # requirement names.
    percent = int((Decimal(repr(stored)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))

    if percent <= 0:
        return PercentFigure(display=LOWER_BOUND_DISPLAY, bounded=True, percent=None)
    if percent >= 100:
        return PercentFigure(display=UPPER_BOUND_DISPLAY, bounded=True, percent=None)
    return PercentFigure(display=f"{percent}%", bounded=False, percent=percent)


def complement(figure: PercentFigure) -> PercentFigure:
    """The other direction of FR-006's dual framing.

    Subtracted from the displayed integer rather than re-rounded from the stored
    value, so the pair sums to one hundred exactly. A bounded figure's
    complement is the opposite bound, never `>99%`'s arithmetic partner `1%` —
    pairing a bound with a flat certainty is the failure the bound exists to
    prevent.
    """
    if figure.bounded:
        opposite = (
            UPPER_BOUND_DISPLAY if figure.display == LOWER_BOUND_DISPLAY else LOWER_BOUND_DISPLAY
        )
        return PercentFigure(
            display=opposite,
            bounded=True,
            percent=None,
            reference_class=figure.reference_class,
        )

    assert figure.percent is not None  # guaranteed by __post_init__
    other = 100 - figure.percent
    return PercentFigure(
        display=f"{other}%",
        bounded=False,
        percent=other,
        reference_class=figure.reference_class,
    )
