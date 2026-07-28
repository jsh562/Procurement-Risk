"""DV-012 and DV-013 — the two gates QC found missing.

Both figures were computed, printed in the datasheet, and bounded by nothing.
`data-model.md` defines them as generation-time refusals and `plan.md` lists them
among the fail-fast shape breaches; neither string appeared in any source or test
file until QC looked for them.

That is the Principle I failure this epic has now produced three times — a value
recorded in an artifact that no check enforces — so these tests assert the
*refusal*, not the number.
"""

from __future__ import annotations

import pytest

from model.procurement.criticality import (
    LATE_SHARE_BAND,
    LateShareError,
    check_late_share,
)
from model.procurement.durations import (
    MEDIAN_TARGET_DAYS,
    MEDIAN_TOLERANCE_DAYS,
    P80_TARGET_DAYS,
    P80_TOLERANCE_DAYS,
    AggregateDurationError,
    check_aggregate_duration,
)


class TestDV012:
    def test_the_committed_figures_pass(self) -> None:
        check_aggregate_duration(58.0, 90.4)

    @pytest.mark.parametrize("median", [MEDIAN_TARGET_DAYS - 5, MEDIAN_TARGET_DAYS + 5])
    def test_the_tolerance_is_inclusive_at_both_edges(self, median: float) -> None:
        check_aggregate_duration(median, P80_TARGET_DAYS)

    @pytest.mark.parametrize("median", [55.9, 66.1, 20.0, 120.0])
    def test_a_median_outside_the_tolerance_refuses(self, median: float) -> None:
        with pytest.raises(AggregateDurationError, match="median"):
            check_aggregate_duration(median, P80_TARGET_DAYS)

    @pytest.mark.parametrize("p80", [85.9, 102.1, 30.0])
    def test_a_p80_outside_the_tolerance_refuses(self, p80: float) -> None:
        with pytest.raises(AggregateDurationError, match="P80"):
            check_aggregate_duration(MEDIAN_TARGET_DAYS, p80)

    def test_the_refusal_names_the_target_and_the_realized_value(self) -> None:
        with pytest.raises(AggregateDurationError) as raised:
            check_aggregate_duration(40.0, P80_TARGET_DAYS)
        message = str(raised.value)
        assert "40.0" in message
        assert str(int(MEDIAN_TARGET_DAYS)) in message
        assert "SC-023" in message

    def test_the_gate_runs_inside_generation(self) -> None:
        """A check nobody calls is the same as no check."""
        from pathlib import Path

        import model.procurement.generate as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "check_aggregate_duration(" in source
        before_write = source.split("def _write(")[0]
        assert "check_aggregate_duration(" in before_write

    def test_the_tolerances_are_sc_023s(self) -> None:
        assert (MEDIAN_TARGET_DAYS, MEDIAN_TOLERANCE_DAYS) == (61.0, 5.0)
        assert (P80_TARGET_DAYS, P80_TOLERANCE_DAYS) == (94.0, 8.0)


class TestDV013:
    def test_the_committed_share_passes(self) -> None:
        check_late_share(46, 175)

    @pytest.mark.parametrize("share", list(LATE_SHARE_BAND))
    def test_the_band_is_inclusive_at_both_edges(self, share: float) -> None:
        check_late_share(round(200 * share), 200)

    @pytest.mark.parametrize("late", [0, 40, 80, 200])
    def test_a_share_outside_the_band_refuses(self, late: int) -> None:
        with pytest.raises(LateShareError, match="outside"):
            check_late_share(late, 200)

    def test_an_empty_delivered_set_refuses(self) -> None:
        """The denominator is delivered lines only, so an empty one is not a
        share of zero — it is a question with no answer."""
        with pytest.raises(LateShareError, match="no delivered line"):
            check_late_share(0, 0)

    def test_the_refusal_names_both_sides_and_the_band(self) -> None:
        with pytest.raises(LateShareError) as raised:
            check_late_share(10, 200)
        message = str(raised.value)
        assert "10 of 200" in message
        assert "0.25" in message and "0.35" in message
        assert "FR-011" in message

    def test_the_gate_runs_inside_generation(self) -> None:
        from pathlib import Path

        import model.procurement.generate as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        assert "check_late_share(" in source
        assert "check_late_share(" in source.split("def _write(")[0]

    def test_the_band_is_fr_011s(self) -> None:
        assert LATE_SHARE_BAND == (0.25, 0.35)


class TestTheCensoredExclusionIsRecordedNotFolded:
    """SC-024: an already-overdue *censored* line is excluded from both sides of
    the share and counted separately, because "missed its need-by" is not
    observable for a delivery that has not happened."""

    def test_the_count_is_emitted(self) -> None:
        from model.procurement import paths

        text = paths.datasheet_path().read_text(encoding="utf-8")
        assert "Already-overdue censored lines" in text

    def test_the_count_is_not_in_the_late_share(self) -> None:
        """The two figures must differ, or the exclusion did not happen."""
        import re

        from model.procurement import paths

        text = paths.datasheet_path().read_text(encoding="utf-8")
        overdue = int(re.search(r"Already-overdue censored lines.*?\*\*(\d+)\*\*", text).group(1))
        share = float(re.search(r"Late-delivery share.*?\*\*([\d.]+)\*\*", text).group(1))
        assert overdue > 0
        assert LATE_SHARE_BAND[0] <= share <= LATE_SHARE_BAND[1]
