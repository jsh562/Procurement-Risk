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
    "SLACK_MEAN",
    "SLACK_SD",
    "TIERS",
    "criticality_band",
    "draw_slack_days",
    "need_by_date",
    "pressure_terciles",
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
    """`max(0, round(expected × f))` with `f ~ Normal(0.15, 0.10)` truncated at 0.

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


def criticality_band(material_category: str, pressure_level: str) -> int:
    """The table cell for this line. Derived, never drawn independently."""
    if pressure_level not in PRESSURE_LEVELS:
        raise KeyError(f"{pressure_level!r} is not one of {PRESSURE_LEVELS}")
    return BAND_TABLE[(tier_of(material_category), pressure_level)]
