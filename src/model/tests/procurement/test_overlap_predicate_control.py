"""NC-7 — the overlap share can fall below 60%, so it is measured not assumed.

DV-014's second half is the one that matters: **every** line in the complement
must fail **all four** clauses. If the complement failed only one clause, the
share would be satisfied by construction — a line one edit away from
overlapping is not a control, and a predicate that cannot return False over the
realized dataset is not measuring anything.
"""

from __future__ import annotations

import numpy as np
import pytest

from model.procurement.durations import TIER_OFFSETS
from model.procurement.equipment import (
    OVERLAP_QUANTITY_RANGE,
    OVERLAP_SHARE_FLOOR,
    OVERLAP_UNIT,
    catalog_overlap_share,
    corpus_overlap_share,
    draw_equipment,
    is_catalog_overlapping,
    is_corpus_overlapping,
)

SEED = 20260727
CATEGORIES = sorted(TIER_OFFSETS)


def _mixed(overlapping_count: int, plain_count: int):
    rng = np.random.default_rng(SEED)
    lines = [
        draw_equipment(rng, CATEGORIES[i % len(CATEGORIES)], True) for i in range(overlapping_count)
    ]
    lines += [
        draw_equipment(rng, CATEGORIES[i % len(CATEGORIES)], False) for i in range(plain_count)
    ]
    return lines


class TestTheComplementFailsAllFourClauses:
    def test_no_complement_line_satisfies_the_predicate(self) -> None:
        for line in _mixed(0, 300):
            assert not is_corpus_overlapping(line, CATEGORIES, vendor_resolves=True)

    def test_the_complement_fails_the_description_clause(self) -> None:
        for line in _mixed(0, 200):
            assert "(Tag " not in line.description

    def test_the_complement_fails_the_quantity_or_unit_clause(self) -> None:
        """Clause 3 is a conjunction, so failing either leg fails the clause.
        Asserted per line rather than in aggregate: one line satisfying both
        legs would be a line one edit from overlapping."""
        low, high = OVERLAP_QUANTITY_RANGE
        for line in _mixed(0, 300):
            integral_in_range = (
                low <= line.quantity <= high and line.quantity == line.quantity.to_integral_value()
            )
            assert not (integral_in_range and line.unit_of_measure == OVERLAP_UNIT)

    def test_the_complement_fails_the_catalog_clauses_too(self) -> None:
        for line in _mixed(0, 200):
            assert not is_catalog_overlapping(line)


class TestTheShareCanFallBelowTheFloor:
    def test_an_all_complement_dataset_scores_zero(self) -> None:
        lines = _mixed(0, 200)
        assert corpus_overlap_share(lines, CATEGORIES, {}) == 0.0
        assert catalog_overlap_share(lines) == 0.0

    def test_a_deliberately_short_dataset_falls_below_sixty_percent(self) -> None:
        """The point of the control: the floor is a measurement that can fail,
        not an invariant the construction guarantees."""
        lines = _mixed(50, 150)
        assert corpus_overlap_share(lines, CATEGORIES, {}) == pytest.approx(0.25)
        assert corpus_overlap_share(lines, CATEGORIES, {}) < OVERLAP_SHARE_FLOOR

    def test_an_all_overlapping_dataset_scores_one(self) -> None:
        lines = _mixed(200, 0)
        assert corpus_overlap_share(lines, CATEGORIES, {}) == 1.0
        assert catalog_overlap_share(lines) == 1.0

    def test_the_share_tracks_the_mix_exactly(self) -> None:
        for overlapping, plain in ((120, 80), (150, 50), (60, 140)):
            lines = _mixed(overlapping, plain)
            expected = overlapping / (overlapping + plain)
            assert corpus_overlap_share(lines, CATEGORIES, {}) == pytest.approx(expected)


class TestTheTwoSharesAreMeasuredSeparately:
    """DV-028: folding them into one number would let a shortfall in either hide."""

    def test_the_two_predicates_can_disagree_on_a_line(self) -> None:
        """An overlapping line whose vendor does not resolve fails FR-032's
        clause 4 while still satisfying FR-034's clauses 5-6."""
        line = _mixed(1, 0)[0]
        assert not is_corpus_overlapping(line, CATEGORIES, vendor_resolves=False)
        assert is_catalog_overlapping(line)

    def test_the_shares_are_computed_by_separate_functions(self) -> None:
        lines = _mixed(120, 80)
        corpus = corpus_overlap_share(lines, CATEGORIES, dict.fromkeys(range(60), False))
        catalog = catalog_overlap_share(lines)
        assert corpus != catalog
        assert catalog == pytest.approx(0.60)
