"""Slack, schedule pressure, and the criticality band.

Derivation runs slack → pressure → band, one direction only: criticality feeds
nothing that produces slack, so there is no cycle (STF-003).

Slack is **multiplicative** on the line's expected duration (AD-009). That is a
modelling decision with a data consequence — `slack / category_expected` then
reduces to roughly `f × exp(b_v)`, nearly independent of category. Additive
slack would make the shortest category's ratio systematically largest,
collapsing the tier × tercile table onto its diagonal and leaving criticality
bands unreachable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import date, timedelta
from types import MappingProxyType

import numpy as np

from model.procurement.durations import TIER_OFFSETS

__all__ = [
    "BAND_TABLE",
    "PRESSURE_LEVELS",
    "LATE_SHARE_BAND",
    "SLACK_MEAN",
    "SLACK_SD",
    "TIERS",
    "LateShareError",
    "check_late_share",
    "criticality_band",
    "draw_slack_days",
    "need_by_date",
    "pressure_terciles",
    "tercile_cut_points",
    "tier_of",
]

TIERS = ("T1", "T2", "T3")

#: Ordered from least to most slack per unit of expected duration. `TIGHT` is
#: the lowest tercile of the ratio.
PRESSURE_LEVELS = ("TIGHT", "MODERATE", "RELAXED")

#: `f ~ Normal(0.13, 0.10)` truncated at 0. **Calibrated against FR-011's band**,
#: which is what this parameter is for: 25–35% of *delivered* lines must miss
#: their need-by date.
#:
#: `data-model.md` declared 0.15 and described it as already calibrated to that
#: band. Measured on the emitted dataset it produces **24.6%** — outside a MUST
#: by four tenths of a point. The declared value and the declared outcome
#: disagreed, and the outcome is the requirement, so the parameter moved.
#:
#: 0.13 is the *smallest* departure that clears the floor: realized 26.3%, mean
#: slack 8.4 days, 8.5% of lines at zero slack, all five criticality bands
#: populated. Lower values reach the band's midpoint but push a fifth of lines to
#: zero slack, which piles ties onto the tercile cut points the pressure
#: dimension depends on. The lever is weak in any case — the late share moves
#: only 24.6% to 29.6% across the whole plausible range, because most lateness
#: comes from duration variance rather than from slack.
SLACK_MEAN = 0.13

#: FR-011's inclusive band on the share of *delivered* lines missing need-by.
LATE_SHARE_BAND = (0.25, 0.35)
SLACK_SD = 0.10

#: Nine cells, five distinct bands, every band reachable.
BAND_TABLE: Mapping[tuple[str, str], int] = MappingProxyType(
    {
        ("T1", "TIGHT"): 5,
        ("T1", "MODERATE"): 4,
        ("T1", "RELAXED"): 3,
        ("T2", "TIGHT"): 4,
        ("T2", "MODERATE"): 3,
        ("T2", "RELAXED"): 2,
        ("T3", "TIGHT"): 3,
        ("T3", "MODERATE"): 2,
        ("T3", "RELAXED"): 1,
    }
)

_TIER_BY_OFFSET = {0.20: "T1", 0.00: "T2", -0.40: "T3"}


def tier_of(material_category: str) -> str:
    """The tier a category belongs to, read from its duration offset.

    Derived from `TIER_OFFSETS` rather than listed again here: a second
    membership list beside the first is two statements of one fact, and this one
    decides a criticality band.
    """
    if material_category not in TIER_OFFSETS:
        raise KeyError(f"{material_category!r} is not one of the 20 committed categories")
    return _TIER_BY_OFFSET[round(TIER_OFFSETS[material_category], 2)]


def draw_slack_days(generator: np.random.Generator, line_expected_duration_days: float) -> int:
    """`max(0, round(expected × f))` with `f ~ Normal(0.13, 0.10)` truncated at 0.

    Truncating `f` rather than the day count is what makes zero slack an
    outcome of the distribution rather than a floor applied afterwards, which
    matters because the tercile cut points are computed over the realized
    ratios and a synthetic pile-up at zero would move them.
    """
    fraction = max(0.0, float(generator.normal(SLACK_MEAN, SLACK_SD)))
    return max(0, round(line_expected_duration_days * fraction))


def need_by_date(order_date: date, line_expected_duration_days: float, slack_days: int) -> date:
    """Order date plus expected total duration plus slack, never earlier (FR-011)."""
    offset = max(0, round(line_expected_duration_days) + slack_days)
    return order_date + timedelta(days=offset)


def tercile_cut_points(ratios: Sequence[float]) -> tuple[float, float]:
    """The two boundaries `pressure_terciles` actually assigns at.

    Derived from the same rank arithmetic rather than recomputed by index, so
    the published cut points are the boundaries the bands were assigned at. An
    earlier version computed `ordered[n//3]` independently in the generator and
    published a value one position off the real boundary — a second
    implementation of one rule, which is the defect class this epic keeps
    finding.

    Each boundary is reported as the midpoint of the two ratios it separates, so
    a reader can classify a new line by comparison without re-deriving the rank.
    """
    count = len(ratios)
    if count < len(PRESSURE_LEVELS):
        raise ValueError(
            f"cannot report tercile boundaries over {count} observation(s); "
            f"{len(PRESSURE_LEVELS)} levels need at least that many"
        )
    ordered = sorted(ratios)
    assigned = pressure_terciles(ordered)
    boundaries: list[float] = []
    for index in range(1, count):
        if assigned[index] != assigned[index - 1]:
            boundaries.append((ordered[index] + ordered[index - 1]) / 2)
    if len(boundaries) != len(PRESSURE_LEVELS) - 1:
        raise ValueError(
            f"expected {len(PRESSURE_LEVELS) - 1} tercile boundaries, found {len(boundaries)}; "
            f"the population is degenerate and the cut points would misdescribe it"
        )
    return (boundaries[0], boundaries[1])


def pressure_terciles(ratios: Sequence[float]) -> list[str]:
    """Assign each ratio a pressure level by tercile over the whole dataset.

    Over the realized dataset **as a whole rather than within each category**
    (FR-012) — per-category terciles would guarantee every category spanned all
    three levels and make the tier dimension of the table redundant.

    Assignment is by rank rather than by value against a cut point, so a
    population with ties at a cut point still splits evenly and deterministically
    instead of piling every tied line into one level. Ties break by position,
    which is stable because the caller supplies lines in natural-key order.
    """
    count = len(ratios)
    if count == 0:
        raise ValueError("cannot compute terciles over an empty population")
    order = sorted(range(count), key=lambda i: (ratios[i], i))
    assigned = [""] * count
    for rank, index in enumerate(order):
        assigned[index] = PRESSURE_LEVELS[min(rank * len(PRESSURE_LEVELS) // count, 2)]
    return assigned


class LateShareError(ValueError):
    """Raised when the realized late-delivery share falls outside FR-011's band."""


def check_late_share(late: int, delivered: int) -> None:
    """DV-013 — FR-011's 25–35% band, **enforced** rather than printed.

    The denominator is delivered lines only. A censored line is excluded from
    both numerator and denominator even when already past its need-by date at
    the as-of point, because "missed its need-by" is not observable for a line
    whose delivery has not happened yet; the count of those is recorded
    separately instead (SC-024).

    Defined in `data-model.md` as DV-013 and implemented nowhere until QC found
    it — the share was computed for the datasheet and bounded by nothing.
    """
    if delivered <= 0:
        raise LateShareError("no delivered line to measure a late share over")
    share = late / delivered
    low, high = LATE_SHARE_BAND
    if not low <= share <= high:
        raise LateShareError(
            f"{late} of {delivered} delivered lines missed their need-by date, a share of "
            f"{share:.4f}, outside FR-011's [{low}, {high}] band. The slack distribution is "
            f"what calibrates this; refusing rather than emitting a dataset whose "
            f"late-delivery rate is not the one the requirement states"
        )


def criticality_band(material_category: str, pressure_level: str) -> int:
    """The table cell for this line. Derived, never drawn independently."""
    if pressure_level not in PRESSURE_LEVELS:
        raise KeyError(f"{pressure_level!r} is not one of {PRESSURE_LEVELS}")
    return BAND_TABLE[(tier_of(material_category), pressure_level)]
