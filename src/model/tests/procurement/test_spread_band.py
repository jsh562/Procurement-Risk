"""NC-6 — the failure FR-036 mandates, over a constructed dataset.

FR-036 does not stop at reporting, and says so in its own words: *"Where the
unadjusted ratio falls inside the band and the category-adjusted ratio does not,
the run MUST fail rather than report the decomposition and pass."*

That distinction is the whole requirement. A decomposition that is computed,
printed and bounded nowhere leaves the bad outcome **visible and still
passing** — and the bad outcome is specific: FR-008's band met not because
vendors genuinely differ, but because some vendors happen to carry long-lead
categories and others carry commodity ones. Vendors do not supply identical
category mixes, so this is not a hypothetical.

The dataset below is constructed so that no vendor has any intrinsic effect at
all. Every apparent between-vendor difference is category mix. A generator that
reported the decomposition and carried on would emit that dataset; this test
requires it to refuse.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from model.procurement.durations import (
    SPREAD_RATIO_BAND,
    TAU,
    TIER_OFFSETS,
    SpreadBandError,
    check_spread_band,
    decompose_spread,
)

SEED = 20260727
LOW, HIGH = SPREAD_RATIO_BAND


def _confounded(vendor_count: int = 4, per_vendor: int = 200):
    """Vendors with no intrinsic effect and systematically different mixes.

    Residual noise is set at σ_r, the epic's own residual spread, rather than at
    something arbitrarily small. That is not cosmetic realism: with negligible
    noise the *unadjusted* ratio comes out above 0.49 and the dataset fails the
    band on both figures, which is a different — and much less interesting —
    failure than the one NC-6 exists to demonstrate. The control has to sit
    inside the band on the naive measure, or it is not showing that the naive
    measure can be fooled.
    """
    from model.procurement.durations import SIGMA_R

    rng = np.random.default_rng(SEED)
    tier_1 = next(c for c in sorted(TIER_OFFSETS) if TIER_OFFSETS[c] == 0.20)
    tier_3 = next(c for c in sorted(TIER_OFFSETS) if TIER_OFFSETS[c] == -0.40)
    logs, vendors, categories = [], [], []
    for v in range(vendor_count):
        long_lead = round(per_vendor * (vendor_count - 1 - v) / (vendor_count - 1))
        for i in range(per_vendor):
            category = tier_1 if i < long_lead else tier_3
            logs.append(TIER_OFFSETS[category] + float(rng.normal(0, SIGMA_R)))
            vendors.append(f"V{v:02d}")
            categories.append(category)
    return logs, vendors, categories


def _genuine(vendor_count: int = 12, per_vendor: int = 200):
    """Real between-vendor spread at τ, with a balanced category mix."""
    rng = np.random.default_rng(SEED)
    cats = sorted(TIER_OFFSETS)
    offsets = np.linspace(-1.6, 1.6, vendor_count)
    offsets = TAU * (offsets - offsets.mean()) / offsets.std(ddof=0)
    logs, vendors, categories = [], [], []
    for v in range(vendor_count):
        for i in range(per_vendor):
            category = cats[i % len(cats)]
            logs.append(float(offsets[v]) + TIER_OFFSETS[category] + float(rng.normal(0, 0.46)))
            vendors.append(f"V{v:02d}")
            categories.append(category)
    return logs, vendors, categories


class TestNC6:
    """The negative control: the check must be demonstrated failing."""

    def test_the_constructed_dataset_is_actually_confounded(self) -> None:
        """The control is only a control if it exhibits the condition.

        Asserted before the refusal is asserted, because a dataset that failed
        for some other reason would make the next test pass while proving
        nothing — a negative control that does not exhibit the negative is the
        most convincing kind of useless test.
        """
        result = decompose_spread(*_confounded())
        assert result.category_variance > result.vendor_variance * 10
        assert LOW <= result.unadjusted_ratio <= HIGH
        assert result.adjusted_ratio < LOW

    def test_the_run_fails_rather_than_reporting_and_passing(self) -> None:
        result = decompose_spread(*_confounded())
        with pytest.raises(SpreadBandError) as raised:
            check_spread_band(result)
        message = str(raised.value)
        assert "category heterogeneity" in message
        assert "0.12" in message and "0.49" in message

    def test_the_refusal_names_both_ratios(self) -> None:
        """The interesting case is the one where they disagree, so a reader
        needs to see the disagreement to act on it rather than re-derive it."""
        result = decompose_spread(*_confounded())
        with pytest.raises(SpreadBandError) as raised:
            check_spread_band(result)
        message = str(raised.value)
        assert f"{result.adjusted_ratio:.4f}" in message
        assert f"{result.unadjusted_ratio:.4f}" in message
        assert "vendor" in message and "category" in message and "residual" in message


class TestTheCheckPassesWhenItShould:
    """A check that refuses everything is not a check."""

    def test_genuine_vendor_spread_passes(self) -> None:
        result = decompose_spread(*_genuine())
        assert LOW <= result.adjusted_ratio <= HIGH
        check_spread_band(result)

    def test_the_declared_constants_pass(self) -> None:
        """τ/σ_r ≈ 0.2657 sits inside the band with margin at both ends.

        Recorded as an observation. The gap between 0.2657 and the 0.24 that
        SC-007 states as the target for this same adjusted quantity is analysis
        finding **A-020**, carried open — the band is not what the finding is
        about, and no run fails on it.
        """
        from model.procurement.durations import SIGMA_R

        adjusted = TAU / SIGMA_R
        assert LOW < adjusted < HIGH
        assert adjusted == pytest.approx(0.2657, abs=5e-4)
        assert min(adjusted - LOW, HIGH - adjusted) > 0.14

    @pytest.mark.parametrize("edge", [LOW, HIGH])
    def test_the_band_is_inclusive_at_both_edges(self, edge: float) -> None:
        """FR-008 says "between 0.12 and 0.49 **inclusive**", so the boundary
        must pass rather than fail by an off-by-one in the comparison."""
        from model.procurement.durations import SpreadDecomposition

        residual = 1.0
        at_edge = SpreadDecomposition(
            vendor_variance=(edge**2) * residual,
            category_variance=0.5,
            residual_variance=residual,
            unadjusted_vendor_variance=(edge**2) * residual,
        )
        assert at_edge.adjusted_ratio == pytest.approx(edge, rel=1e-12)
        check_spread_band(at_edge)

    def test_a_ratio_above_the_band_also_fails(self) -> None:
        """The band is two-sided. An implausibly large vendor effect is as much
        a calibration failure as an absent one."""
        from model.procurement.durations import SpreadDecomposition

        too_high = SpreadDecomposition(
            vendor_variance=4.0,
            category_variance=0.1,
            residual_variance=1.0,
            unadjusted_vendor_variance=4.0,
        )
        assert too_high.adjusted_ratio == pytest.approx(2.0, rel=1e-12)
        with pytest.raises(SpreadBandError, match="outside"):
            check_spread_band(too_high)


class TestDegenerateInputs:
    """Refusals, rather than a division nobody sees."""

    def test_an_empty_population_is_refused(self) -> None:
        with pytest.raises(ValueError, match="empty population"):
            decompose_spread([], [], [])

    def test_mismatched_lengths_are_refused(self) -> None:
        with pytest.raises(ValueError, match="one vendor and one category"):
            decompose_spread([1.0, 2.0], ["V01"], ["C", "C"])

    def test_zero_residual_yields_infinity_rather_than_a_crash(self) -> None:
        """A perfectly explained population is degenerate, not an exception.

        It must still fail the band — `inf` is outside `[0.12, 0.49]` — so the
        degenerate case cannot slip through as a pass.
        """
        from model.procurement.durations import SpreadDecomposition

        degenerate = SpreadDecomposition(1.0, 0.0, 0.0, 1.0)
        assert math.isinf(degenerate.adjusted_ratio)
        with pytest.raises(SpreadBandError):
            check_spread_band(degenerate)
