"""Property tests for slack, schedule pressure and criticality (FR-011, FR-012).

Every wrong band is still an integer 1–5 that the delivered CHECK accepts, so
the mapping is asserted here or nowhere.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pytest

from model.procurement.criticality import (
    BAND_TABLE,
    PRESSURE_LEVELS,
    SLACK_MEAN,
    SLACK_SD,
    TIERS,
    criticality_band,
    draw_slack_days,
    need_by_date,
    pressure_terciles,
    tier_of,
)
from model.procurement.durations import TIER_OFFSETS, category_expected_duration_days

SEED = 20260727


class TestBandTable:
    """Invariant: nine cells, five distinct bands, every band reachable."""

    def test_the_table_is_three_by_three(self) -> None:
        assert len(TIERS) == 3
        assert len(PRESSURE_LEVELS) == 3
        assert len(BAND_TABLE) == 9

    def test_all_nine_cells_are_defined(self) -> None:
        for tier in TIERS:
            for level in PRESSURE_LEVELS:
                assert (tier, level) in BAND_TABLE

    def test_five_distinct_bands_all_in_range(self) -> None:
        bands = set(BAND_TABLE.values())
        assert bands == {1, 2, 3, 4, 5}
        assert all(isinstance(b, int) for b in BAND_TABLE.values())

    def test_the_table_matches_the_published_grid(self) -> None:
        """T1 5/4/3, T2 4/3/2, T3 3/2/1 — `data-model.md` prints it."""
        expected = {
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
        assert dict(BAND_TABLE) == expected

    def test_tighter_pressure_never_lowers_the_band(self) -> None:
        for tier in TIERS:
            row = [BAND_TABLE[(tier, level)] for level in ("TIGHT", "MODERATE", "RELAXED")]
            assert row == sorted(row, reverse=True)

    def test_every_category_maps_to_a_tier(self) -> None:
        assert {tier_of(c) for c in TIER_OFFSETS} == set(TIERS)

    def test_tier_assignment_follows_the_duration_offset(self) -> None:
        for category, offset in TIER_OFFSETS.items():
            assert tier_of(category) == {0.20: "T1", 0.00: "T2", -0.40: "T3"}[offset]


class TestSlack:
    """`f ~ Normal(0.15, 0.10)` truncated at 0, multiplicative on expected duration."""

    def test_the_declared_parameters(self) -> None:
        """0.13, not the 0.15 `data-model.md` first declared.

        The parameter exists to be calibrated against FR-011's 25-35% late band,
        and 0.15 produced 24.6% on the emitted dataset — outside a MUST. The
        declared value and the declared outcome disagreed; the outcome is the
        requirement.
        """
        assert (SLACK_MEAN, SLACK_SD) == (0.13, 0.10)

    def test_slack_is_never_negative(self) -> None:
        rng = np.random.default_rng(SEED)
        assert min(draw_slack_days(rng, 70.0) for _ in range(5_000)) >= 0

    def test_zero_slack_occurs_at_the_truncation(self) -> None:
        """`slack_days = 0` — the boundary case the plan names by name."""
        rng = np.random.default_rng(SEED)
        assert 0 in {draw_slack_days(rng, 70.0) for _ in range(20_000)}

    def test_mean_slack_is_near_the_declared_figure(self) -> None:
        """≈9.0 days at a tier-2 expected duration, following the recalibration
        from 0.15 to 0.13. Asserted as a consequence of SLACK_MEAN rather than
        as a second constant, so the two cannot drift apart."""
        rng = np.random.default_rng(SEED)
        drawn = [draw_slack_days(rng, 69.5) for _ in range(20_000)]
        assert float(np.mean(drawn)) == pytest.approx(69.5 * SLACK_MEAN, abs=1.0)

    def test_slack_scales_with_expected_duration(self) -> None:
        """Multiplicative, not additive — AD-009."""
        rng = np.random.default_rng(SEED)
        short = [draw_slack_days(np.random.default_rng(SEED + i), 46.6) for i in range(2_000)]
        long = [draw_slack_days(np.random.default_rng(SEED + i), 84.9) for i in range(2_000)]
        assert float(np.mean(long)) > float(np.mean(short)) * 1.5
        del rng


class TestNeedBy:
    def test_need_by_is_order_plus_expected_plus_slack(self) -> None:
        order = date(2025, 9, 1)
        assert need_by_date(order, 70.0, 10) == date(2025, 9, 1) + __import__("datetime").timedelta(
            days=80
        )

    def test_need_by_is_never_earlier_than_the_order_date(self) -> None:
        """FR-011 states it explicitly, so a zero-or-negative expectation must
        still produce a same-day-or-later need-by rather than a past date."""
        order = date(2025, 9, 1)
        for expected in (0.0, 0.4, 1.0):
            assert need_by_date(order, expected, 0) >= order


class TestTerciles:
    """Cut points over the realized dataset as a whole, not within each category."""

    def test_three_levels_partition_the_population(self) -> None:
        ratios = [i / 300 for i in range(300)]
        assigned = pressure_terciles(ratios)
        assert set(assigned) == set(PRESSURE_LEVELS)
        assert len(assigned) == len(ratios)

    def test_tight_is_the_lowest_tercile(self) -> None:
        """`TIGHT` = least slack per unit of expected duration."""
        ratios = [i / 300 for i in range(300)]
        assigned = pressure_terciles(ratios)
        assert assigned[0] == "TIGHT"
        assert assigned[-1] == "RELAXED"

    def test_the_split_is_even_to_within_one(self) -> None:
        assigned = pressure_terciles([i / 300 for i in range(300)])
        counts = [assigned.count(level) for level in PRESSURE_LEVELS]
        assert max(counts) - min(counts) <= 1

    def test_ties_at_a_cut_point_are_resolved_deterministically(self) -> None:
        """The boundary case the plan names. A population that is one repeated
        value has no meaningful terciles, and must not raise or produce a
        different answer between runs."""
        tied = [0.25] * 90
        first = pressure_terciles(tied)
        assert first == pressure_terciles(tied)
        assert len(first) == 90
        assert set(first) <= set(PRESSURE_LEVELS)

    def test_a_partially_tied_population_is_stable(self) -> None:
        ratios = [0.1] * 40 + [0.25] * 40 + [0.9] * 40
        assert pressure_terciles(ratios) == pressure_terciles(ratios)

    def test_an_empty_population_is_refused(self) -> None:
        with pytest.raises(ValueError, match="empty"):
            pressure_terciles([])


class TestMultiplicativeSlackKeepsTheTablePopulated:
    """Metamorphic — AD-009's stated reason for multiplicative slack."""

    def test_scaling_a_category_leaves_the_continuous_ratio_unchanged(self) -> None:
        """`ratio = slack / category_expected` reduces exactly to `f`, which is
        what makes it independent of category — AD-009's whole argument."""
        rng = np.random.default_rng(SEED)
        fractions = [float(f) for f in rng.normal(SLACK_MEAN, SLACK_SD, 300).clip(min=0)]

        for expected in (46.6, 70.0, 84.9):
            ratios = [(expected * f) / expected for f in fractions]
            assert pressure_terciles(ratios) == pressure_terciles(fractions)

    def test_whole_day_rounding_perturbs_the_assignment_only_at_the_margins(self) -> None:
        """The exact invariance above survives integer days only approximately.

        `slack_days` is a whole number, so the realized ratio is quantized at
        `1/expected` — coarser for a short category than a long one. Lines
        sitting within one day's worth of a cut point can therefore land in
        different terciles at different category scales. Measured, that is
        ~6% of lines across the T3-to-T1 span.

        Asserted as a bound rather than as exact equality because the effect is
        real and disclosing it is more useful than a test tuned to hide it. A
        *large* divergence would mean the ratio had stopped being
        category-independent, which is the failure this guards against.
        """
        rng = np.random.default_rng(SEED)
        fractions = [float(f) for f in rng.normal(SLACK_MEAN, SLACK_SD, 300).clip(min=0)]

        short = pressure_terciles([round(46.6 * f) / 46.6 for f in fractions])
        long = pressure_terciles([round(84.9 * f) / 84.9 for f in fractions])
        agreement = sum(a == b for a, b in zip(short, long, strict=True)) / len(fractions)
        assert agreement > 0.90

    def test_additive_slack_would_collapse_the_table_onto_its_diagonal(self) -> None:
        """The failure AD-009 avoids, demonstrated rather than asserted in prose.

        With a *fixed* slack in days, the ratio is largest for the shortest
        category, so T3 lines occupy `RELAXED` almost exclusively and cells go
        unpopulated — leaving criticality bands unreachable.
        """
        cats = sorted(TIER_OFFSETS)
        expected = [category_expected_duration_days(cats[i % len(cats)]) for i in range(300)]

        additive = [10.0 / e for e in expected]
        multiplicative = [0.15 for _ in expected]

        additive_cells = {
            (tier_of(cats[i % len(cats)]), level)
            for i, level in enumerate(pressure_terciles(additive))
        }
        multiplicative_cells = {
            (tier_of(cats[i % len(cats)]), level)
            for i, level in enumerate(pressure_terciles(multiplicative))
        }
        assert len(additive_cells) < 9
        assert len(multiplicative_cells) >= len(additive_cells)


class TestBandDerivation:
    """DV-006: every band an integer 1–5, and all five occur."""

    def test_the_band_equals_its_table_cell(self) -> None:
        for category in sorted(TIER_OFFSETS):
            for level in PRESSURE_LEVELS:
                assert criticality_band(category, level) == BAND_TABLE[(tier_of(category), level)]

    def test_all_five_bands_occur_across_the_grid(self) -> None:
        """Generator-only under DV-006 — the delivered CHECK bounds the range
        but cannot require the range be exercised."""
        produced = {
            criticality_band(c, level) for c in sorted(TIER_OFFSETS) for level in PRESSURE_LEVELS
        }
        assert produced == {1, 2, 3, 4, 5}

    def test_an_unknown_pressure_level_is_refused(self) -> None:
        with pytest.raises(KeyError):
            criticality_band(next(iter(TIER_OFFSETS)), "URGENT")

    def test_an_unknown_category_is_refused(self) -> None:
        with pytest.raises(KeyError):
            criticality_band("NOT_A_CATEGORY", "TIGHT")
