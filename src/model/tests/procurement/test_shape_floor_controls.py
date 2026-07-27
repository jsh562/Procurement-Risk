"""NC-5 — all three DV-010 breaches fail loudly, before anything is written.

FR-010 requires the generator to *fail* rather than emit a dataset below the
floors. The "before anything is written" half matters as much as the failure:
a partially-written artifact tree that also reports an error is the state a
later run can mistake for a completed one.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from model.procurement.censor import (
    CENSORED_SHARE_FLOOR,
    DELIVERED_EVENT_FLOOR,
    ShapeFloorError,
    check_shape_floors,
    delivered_share_window,
)

NON_TERMINAL = (
    "submitted",
    "under_review",
    "approved",
    "revise_and_resubmit",
    "released_for_fabrication",
    "shipped",
)
HEALTHY = dict.fromkeys(NON_TERMINAL, 3)


@pytest.mark.parametrize(
    ("label", "line_count", "delivered", "occupancy", "expected"),
    [
        ("event floor", 199, 150, HEALTHY, "delivered"),
        ("censoring floor", 199, 199, HEALTHY, "censored"),
        ("empty non-terminal state", 199, 170, HEALTHY | {"approved": 0}, "approved"),
    ],
)
def test_each_breach_raises(
    label: str, line_count: int, delivered: int, occupancy: dict[str, int], expected: str
) -> None:
    with pytest.raises(ShapeFloorError, match=expected):
        check_shape_floors(line_count, delivered, occupancy)


def test_no_artifact_is_written_when_a_floor_is_breached(tmp_path: Path) -> None:
    """The check runs before the write path, so a breach leaves the tree empty.

    Asserted over a directory the check never receives, which is the point: it
    has no filesystem access at all, so it *cannot* write a partial artifact
    and there is no ordering bug to introduce later.
    """
    target = tmp_path / "procurement"
    target.mkdir()
    with pytest.raises(ShapeFloorError):
        check_shape_floors(199, 150, HEALTHY)
    assert list(target.iterdir()) == []


def test_the_healthy_shape_passes(tmp_path: Path) -> None:
    """A control that refuses everything demonstrates nothing."""
    check_shape_floors(199, 170, HEALTHY)


class TestBothBindingRegimes:
    """DV-010 requires both branches of the event floor be exercised."""

    def test_below_the_crossover_the_absolute_floor_binds(self) -> None:
        low, _ = delivered_share_window(190)
        assert low > 0.80
        assert low * 190 == pytest.approx(DELIVERED_EVENT_FLOOR)
        check_shape_floors(190, 160, HEALTHY)
        with pytest.raises(ShapeFloorError, match="delivered"):
            check_shape_floors(190, 159, HEALTHY)

    def test_at_or_above_the_crossover_the_share_binds(self) -> None:
        low, _ = delivered_share_window(210)
        assert low == pytest.approx(0.80)
        check_shape_floors(210, 168, HEALTHY)
        with pytest.raises(ShapeFloorError, match="delivered"):
            check_shape_floors(210, 167, HEALTHY)

    def test_the_crossover_is_at_two_hundred(self) -> None:
        assert delivered_share_window(199)[0] > 0.80
        assert delivered_share_window(200)[0] == pytest.approx(0.80)


class TestTheCeiling:
    def test_the_censored_floor_is_the_delivered_ceiling(self) -> None:
        assert delivered_share_window(199)[1] == pytest.approx(1.0 - CENSORED_SHARE_FLOOR)

    def test_exactly_ten_percent_censored_passes(self) -> None:
        """FR-010's floor is inclusive, so the boundary must not fail."""
        check_shape_floors(200, 180, HEALTHY)

    def test_just_under_ten_percent_censored_fails(self) -> None:
        with pytest.raises(ShapeFloorError, match="censored"):
            check_shape_floors(200, 181, HEALTHY)


def test_every_empty_state_is_named_in_the_message() -> None:
    """A message naming one of three empty states sends the reader back for
    two more runs to discover the rest."""
    occupancy = HEALTHY | {"approved": 0, "revise_and_resubmit": 0}
    with pytest.raises(ShapeFloorError) as raised:
        check_shape_floors(199, 170, occupancy)
    assert "approved" in str(raised.value)
    assert "revise_and_resubmit" in str(raised.value)
