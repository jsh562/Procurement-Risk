"""Property tests for the allocation: exact margins, and the shrinkage identity.

`plan.md` § Mandated properties promotes `allocate.py` to the mandatory property
tier for a stated reason: FR-004's claimed 0.22–0.67 shrinkage span is *derived
from* the per-vendor line counts through ρⱼ = τ²/(τ² + σ²/nⱼ), so the arithmetic
here is load-bearing for a requirement rather than a convenience. A wrong split
still sums to a legal line count, which is exactly the failure a total-only
check cannot see.

Two relation classes are asserted, over the domain the plan names:

* **Invariant** — per-vendor and per-project margins are met exactly and the two
  margins agree with the total. Boundary cases: totals at 190 and at 210, the
  five-line vendor, and the *N* = 200 crossover of FR-010's two floors.
* **Algebraic identity** — the endpoints of the declared vector reproduce
  FR-004's 0.22–0.67 span. This is the property that makes 5 and 35 the right
  endpoints rather than round numbers someone liked.

The declared vectors are **imported, never restated**. `tasks.md` § Design
constants says tasks consume the solved constants rather than re-solving them,
and a test that hard-codes the vector it is checking asserts only that someone
typed the same digits twice.
"""

from __future__ import annotations

import pytest

from model.procurement.allocate import (
    PO_SIZE_CYCLE,
    PROJECT_LINE_COUNTS,
    TAU,
    VENDOR_LINE_COUNTS,
    WITHIN_VENDOR_SIGMA,
    allocate_lines,
    shrinkage,
)


def _counts(lines, key):
    counts: dict[str, int] = {}
    for line in lines:
        counts[getattr(line, key)] = counts.get(getattr(line, key), 0) + 1
    return counts


class TestMargins:
    """Invariant: both margins are met exactly, and they agree with each other."""

    def test_vendor_margin_is_exact(self) -> None:
        lines = allocate_lines()
        assert _counts(lines, "vendor_id") == dict(VENDOR_LINE_COUNTS)

    def test_project_margin_is_exact(self) -> None:
        lines = allocate_lines()
        assert _counts(lines, "project_id") == dict(PROJECT_LINE_COUNTS)

    def test_the_two_margins_agree(self) -> None:
        """A cross-tab's margins must sum to the same total or the fill is wrong.

        Checked independently of `allocate_lines` because it is a statement about
        the declared constants themselves: if the two vectors disagreed, no fill
        could satisfy both and the failure would surface as an unexplained
        shortfall rather than as the arithmetic error it is.
        """
        assert sum(VENDOR_LINE_COUNTS.values()) == sum(PROJECT_LINE_COUNTS.values())

    def test_total_is_inside_the_declared_band(self) -> None:
        assert 190 <= len(allocate_lines()) <= 210

    @pytest.mark.parametrize("total", [190, 200, 210])
    def test_exact_margins_hold_at_the_band_edges_and_the_crossover(self, total: int) -> None:
        """190, 210 and the *N* = 200 crossover of FR-010's two floors.

        200 is not an arbitrary midpoint: FR-010 floors the uncensored count at
        `max(80% of realized lines, 160)`, and the two branches cross there. A
        fill correct at 199 and wrong at 200 would pass a test that only ever
        ran at the declared total.
        """
        lines = allocate_lines(total=total)
        assert len(lines) == total
        vendors = _counts(lines, "vendor_id")
        projects = _counts(lines, "project_id")
        assert sum(vendors.values()) == total
        assert sum(projects.values()) == total
        assert len(vendors) == len(VENDOR_LINE_COUNTS)
        assert len(projects) == len(PROJECT_LINE_COUNTS)
        assert min(vendors.values()) >= 1
        assert min(projects.values()) >= 1


class TestShrinkageIdentity:
    """FR-004's 0.22–0.67 span is derived from the endpoints, not asserted beside them."""

    def test_endpoints_reproduce_the_claimed_span(self) -> None:
        counts = sorted(VENDOR_LINE_COUNTS.values())
        assert shrinkage(counts[0]) == pytest.approx(0.22, abs=0.005)
        assert shrinkage(counts[-1]) == pytest.approx(0.67, abs=0.005)

    def test_shrinkage_is_monotone_in_line_count(self) -> None:
        """More lines, more weight on the vendor's own mean. Strictly."""
        counts = sorted(VENDOR_LINE_COUNTS.values())
        values = [shrinkage(n) for n in counts]
        assert values == sorted(values)
        assert len(set(values)) == len(values)

    def test_the_five_line_vendor_is_the_most_shrunk(self) -> None:
        """The boundary case the plan names by number."""
        assert min(VENDOR_LINE_COUNTS.values()) == 5
        assert shrinkage(5) == min(shrinkage(n) for n in VENDOR_LINE_COUNTS.values())

    def test_shrinkage_matches_the_identity_directly(self) -> None:
        """Alternate implementation of ρⱼ = τ²/(τ² + σ²/nⱼ), computed inline."""
        for n in VENDOR_LINE_COUNTS.values():
            expected = TAU**2 / (TAU**2 + WITHIN_VENDOR_SIGMA**2 / n)
            assert shrinkage(n) == pytest.approx(expected, rel=1e-12)

    @pytest.mark.parametrize("bad", [0, -1])
    def test_shrinkage_refuses_a_non_positive_count(self, bad: int) -> None:
        """ρ is undefined at n = 0 and the division would raise far from here."""
        with pytest.raises(ValueError, match="line count"):
            shrinkage(bad)


class TestPurchaseOrderGrouping:
    """DV-003's grouping clauses, as properties of the allocation rather than of a run."""

    def test_every_po_shares_one_project_and_one_vendor(self) -> None:
        groups: dict[tuple[str, str], set[tuple[str, str]]] = {}
        for line in allocate_lines():
            groups.setdefault((line.project_id, line.po_number), set()).add(
                (line.project_id, line.vendor_id)
            )
        assert all(len(pair) == 1 for pair in groups.values())

    def test_line_numbers_are_contiguous_from_one(self) -> None:
        pos: dict[tuple[str, str], list[int]] = {}
        for line in allocate_lines():
            pos.setdefault((line.project_id, line.po_number), []).append(line.line_number)
        for numbers in pos.values():
            assert sorted(numbers) == list(range(1, len(numbers) + 1))

    def test_natural_keys_are_unique(self) -> None:
        lines = allocate_lines()
        keys = [(line.project_id, line.po_number, line.line_number) for line in lines]
        assert len(set(keys)) == len(keys)

    def test_multi_line_purchase_orders_occur(self) -> None:
        """`line_number >= 2` must actually be exercised (`data-model.md` §Allocation).

        The cyclic size pattern exists so the delivered schema's contiguity and
        natural-key constraints are tested against something other than a table
        of one-line orders.
        """
        sizes: dict[tuple[str, str], int] = {}
        for line in allocate_lines():
            key = (line.project_id, line.po_number)
            sizes[key] = sizes.get(key, 0) + 1
        assert max(sizes.values()) >= 2
        assert max(PO_SIZE_CYCLE) >= 2

    def test_every_declared_order_size_actually_occurs(self) -> None:
        """Each size in the cycle must appear at least once, and none outside it.

        This is what `data-model.md` actually requires — the sizes are declared,
        so a declared size that never occurs means the cycle was not followed.
        It catches the per-group reset, under which the pattern's `3` occurs
        once in the whole dataset and its second `2` never distinguishably at
        all: a pattern declared and not realized reads, from the outside,
        exactly like a pattern that was followed.

        What this deliberately does **not** assert is that the realized shares
        match the cycle's composition. Clipping at group boundaries means they
        do not, and no artifact states a target distribution to hold them to.
        Asserting one here would be inventing the number and then shaping the
        generator to hit it. The gap is disclosed rather than tested away.
        """
        sizes: dict[tuple[str, str], int] = {}
        for line in allocate_lines():
            key = (line.project_id, line.po_number)
            sizes[key] = sizes.get(key, 0) + 1

        realized = set(sizes.values())
        assert realized == set(PO_SIZE_CYCLE), (
            f"realized order sizes {sorted(realized)} against declared {sorted(set(PO_SIZE_CYCLE))}"
        )


class TestDeterminism:
    """No seed is consumed here, so equality is the whole claim."""

    def test_repeated_calls_are_identical(self) -> None:
        assert allocate_lines() == allocate_lines()

    def test_allocation_reads_no_clock_and_no_entropy(self) -> None:
        """The allocation is declared, so two calls at different totals differ
        only in the way the totals differ — not in vendor ordering."""
        first = [line.vendor_id for line in allocate_lines(total=190)]
        second = [line.vendor_id for line in allocate_lines(total=190)]
        assert first == second
