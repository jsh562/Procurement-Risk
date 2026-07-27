"""DV-001 and DV-003 against the allocation, as unit assertions.

The property tier next door asserts relations that must hold across the input
domain — margins under a swept total, the shrinkage identity. This file asserts
the two named validation rules against the delivered allocation, which is the
narrower and more literal claim: DV-001's counts and roster membership, DV-003's
key uniqueness, contiguity and one-project-one-vendor grouping.

`data-model.md` marks DV-003's grouping clause **generator-only (G-2)** — the
delivered schema does not enforce that a purchase order belongs to one vendor —
so this file is the only place it is checked before the data is committed.
"""

from __future__ import annotations

import pytest

from model.procurement.allocate import (
    DECLARED_TOTAL,
    AllocationError,
    allocate_lines,
)
from model.roster.reader import EXPECTED_PROJECTS, EXPECTED_VENDORS, read_roster


@pytest.fixture(scope="module")
def lines():
    return allocate_lines()


class TestDV001:
    """190 ≤ len(lines) ≤ 210; all 5 projects and all 12 vendors; ids from the roster."""

    def test_line_count_is_inside_the_band(self, lines) -> None:
        assert 190 <= len(lines) <= 210
        assert len(lines) == DECLARED_TOTAL

    def test_every_project_appears(self, lines) -> None:
        assert len({line.project_id for line in lines}) == EXPECTED_PROJECTS

    def test_every_vendor_appears(self, lines) -> None:
        assert len({line.vendor_id for line in lines}) == EXPECTED_VENDORS

    def test_every_identifier_comes_from_the_roster(self, lines) -> None:
        """FR-001: identities are read, never invented.

        Checked against `identifiers()` — the flat set — because membership is
        exactly what that method is for, and a line's project and vendor are
        both drawn from the same roster document.
        """
        known = read_roster().identifiers()
        for line in lines:
            assert line.project_id in known
            assert line.vendor_id in known

    def test_no_identifier_literal_in_the_module_source(self) -> None:
        """The scan T032 applies to the package, applied early to this module.

        A hard-coded `PRJ-001` would pass every count assertion above and still
        breach FR-001, because it would keep working against a roster that had
        renamed the entity.
        """
        from pathlib import Path

        import model.procurement.allocate as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        code = "\n".join(line for line in source.splitlines() if not line.lstrip().startswith("#:"))
        body = code.split('"""', 2)[-1]
        assert "PRJ-" not in body
        assert "VND-" not in body


class TestDV003:
    """Natural-key uniqueness, contiguity, and one project and vendor per order."""

    def test_natural_keys_are_unique(self, lines) -> None:
        keys = [line.natural_key for line in lines]
        assert len(set(keys)) == len(keys)

    def test_line_numbers_are_contiguous_from_one_within_each_order(self, lines) -> None:
        orders: dict[tuple[str, str], list[int]] = {}
        for line in lines:
            orders.setdefault((line.project_id, line.po_number), []).append(line.line_number)
        for key, numbers in orders.items():
            assert sorted(numbers) == list(range(1, len(numbers) + 1)), key

    def test_each_order_carries_one_project_and_one_vendor(self, lines) -> None:
        """Generator-only under G-2 — the delivered schema does not enforce it."""
        seen: dict[tuple[str, str], tuple[str, str]] = {}
        for line in lines:
            key = (line.project_id, line.po_number)
            identity = (line.project_id, line.vendor_id)
            assert seen.setdefault(key, identity) == identity

    def test_purchase_order_numbers_are_unique_within_a_project(self, lines) -> None:
        """A PO number identifies an order inside its project without encoding the vendor."""
        pairs = {(line.project_id, line.po_number) for line in lines}
        by_project: dict[str, int] = {}
        for project_id, _ in pairs:
            by_project[project_id] = by_project.get(project_id, 0) + 1
        assert sum(by_project.values()) == len(pairs)

    def test_lines_are_returned_in_natural_key_order(self, lines) -> None:
        keys = [line.natural_key for line in lines]
        assert keys == sorted(keys)


class TestRefusals:
    """The allocation refuses rather than emitting a shape that breaches DV-001."""

    def test_a_total_below_the_stratum_count_is_refused(self) -> None:
        """Twelve vendors cannot be non-empty across fewer than twelve lines.

        Refusing beats emitting eleven vendors and a silent gap: DV-001 requires
        all twelve present, and a missing stratum is only caught much later, by
        a coverage assertion over the finished dataset.
        """
        with pytest.raises(AllocationError, match="non-empty"):
            allocate_lines(total=8)

    def test_every_stratum_stays_non_empty_at_the_low_edge(self) -> None:
        lines = allocate_lines(total=190)
        assert len({line.vendor_id for line in lines}) == EXPECTED_VENDORS
        assert len({line.project_id for line in lines}) == EXPECTED_PROJECTS
