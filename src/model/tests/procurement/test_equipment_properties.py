"""Property tests for the descriptive fields (FR-031, FR-032, FR-034, FR-037).

`equipment.py` is the seventh mandatory deterministic-computation module.
"""

from __future__ import annotations

from decimal import Decimal

import numpy as np
import pytest

from model.corpus.manufacturers import MANUFACTURERS, PART_NUMBER_PATTERN
from model.procurement.durations import TIER_OFFSETS
from model.procurement.equipment import (
    OVERLAP_QUANTITY_RANGE,
    OVERLAP_UNIT,
    QUANTITY_RANGE,
    UNITS_OF_MEASURE,
    EquipmentError,
    alias_spellings,
    catalog_overlap_share,
    draw_equipment,
    is_catalog_overlapping,
    is_corpus_overlapping,
)

SEED = 20260727
CATEGORIES = sorted(TIER_OFFSETS)


def _draw(overlapping: bool, count: int = 400, seed: int = SEED):
    rng = np.random.default_rng(seed)
    return [draw_equipment(rng, CATEGORIES[i % len(CATEGORIES)], overlapping) for i in range(count)]


class TestCatalogDraw:
    """FR-034 clauses 5–6 and FR-037: read the catalog, never invent."""

    def test_every_manufacturer_is_a_canonical_name(self) -> None:
        canonical = {entry.canonical_name for entry in MANUFACTURERS.values()}
        assert {line.manufacturer for line in _draw(True)} <= canonical

    def test_no_alias_is_ever_emitted(self) -> None:
        """The catalog publishes aliases for E006 to collapse. Emitting one here
        would make this dataset the source of the variation E006 removes."""
        aliases = {
            name
            for entry in MANUFACTURERS.values()
            for name in alias_spellings(entry.canonical_name)
            if name != entry.canonical_name
        }
        assert aliases
        assert not aliases & {line.manufacturer for line in _draw(True)}

    def test_the_manufacturer_makes_the_line_s_category(self) -> None:
        """Clause 5's category condition — what makes the pair informative
        rather than merely well-formed."""
        for line in _draw(True):
            key = next(
                k for k, entry in MANUFACTURERS.items() if entry.canonical_name == line.manufacturer
            )
            assert line.material_category in MANUFACTURERS[key].categories

    def test_the_part_number_prefix_is_bound_to_the_same_entry(self) -> None:
        """Clause 6. An unbound prefix passes the regex while naming a different
        manufacturer than the row does."""
        for line in _draw(True):
            key = next(
                k for k, entry in MANUFACTURERS.items() if entry.canonical_name == line.manufacturer
            )
            assert line.part_number.startswith(f"{key}-")

    def test_every_part_number_matches_e002_s_published_pattern(self) -> None:
        for line in _draw(True):
            assert PART_NUMBER_PATTERN.match(line.part_number)

    def test_the_complement_still_carries_both_fields(self) -> None:
        """NOT NULL in the delivered schema, and DV-004 requires all six present.
        The complement fails clause 5 by category mismatch, not by absence."""
        for line in _draw(False):
            assert line.manufacturer
            assert line.part_number

    def test_the_complement_s_manufacturer_does_not_make_its_category(self) -> None:
        for line in _draw(False):
            key = next(k for k, e in MANUFACTURERS.items() if e.canonical_name == line.manufacturer)
            assert line.material_category not in MANUFACTURERS[key].categories

    def test_no_catalog_content_is_restated_in_source(self) -> None:
        """The catalog is read, so a canonical name must not appear as a literal.

        A restated name would keep working after E002 renamed the manufacturer,
        which is the same failure mode as a hard-coded project id.
        """
        from pathlib import Path

        import model.procurement.equipment as module

        source = Path(module.__file__).read_text(encoding="utf-8")
        for entry in MANUFACTURERS.values():
            assert entry.canonical_name not in source


class TestDescriptionGrammars:
    def test_overlapping_descriptions_use_e002_s_composition(self) -> None:
        for line in _draw(True):
            assert line.description.startswith(
                " ".join(w.capitalize() for w in line.material_category.split("_"))
            )
            assert "(Tag " in line.description

    def test_the_two_grammars_are_mutually_exclusive(self) -> None:
        """The em dash in the complement's grammar makes this true by inspection
        rather than by counting."""
        overlapping = {line.description for line in _draw(True)}
        plain = {line.description for line in _draw(False)}
        assert not overlapping & plain
        assert all("(Tag " not in d for d in plain)
        assert all(" — " not in d for d in overlapping)

    def test_every_description_is_non_blank_after_trimming(self) -> None:
        for overlapping in (True, False):
            for line in _draw(overlapping):
                assert line.description.strip(" \t\n\r\f")


class TestQuantityAndUnit:
    def test_quantity_is_at_a_fixed_scale_of_one(self) -> None:
        """DV-005. `numeric` equality ignores trailing zeros, so the fixed scale
        is generator-only and cannot be delegated to the database."""
        for overlapping in (True, False):
            for line in _draw(overlapping):
                assert line.quantity == line.quantity.quantize(Decimal("0.1"))
                assert "." in str(line.quantity)
                assert len(str(line.quantity).split(".")[1]) == 1

    def test_quantity_is_inside_its_declared_range(self) -> None:
        low, high = QUANTITY_RANGE
        for overlapping in (True, False):
            for line in _draw(overlapping):
                assert low <= line.quantity <= high

    def test_units_come_from_the_five_permitted_values(self) -> None:
        for overlapping in (True, False):
            assert {line.unit_of_measure for line in _draw(overlapping)} <= set(UNITS_OF_MEASURE)

    def test_overlapping_lines_are_integer_ea(self) -> None:
        low, high = OVERLAP_QUANTITY_RANGE
        for line in _draw(True):
            assert line.unit_of_measure == OVERLAP_UNIT
            assert low <= line.quantity <= high
            assert line.quantity == line.quantity.to_integral_value()


class TestPredicates:
    def test_drawn_overlapping_lines_satisfy_all_four_clauses(self) -> None:
        for line in _draw(True):
            assert is_corpus_overlapping(line, CATEGORIES, vendor_resolves=True)

    def test_drawn_overlapping_lines_satisfy_clauses_five_and_six(self) -> None:
        for line in _draw(True):
            assert is_catalog_overlapping(line)

    def test_the_complement_fails_the_catalog_predicate(self) -> None:
        for line in _draw(False):
            assert not is_catalog_overlapping(line)

    def test_an_unresolved_vendor_fails_clause_four(self) -> None:
        line = _draw(True, count=1)[0]
        assert not is_corpus_overlapping(line, CATEGORIES, vendor_resolves=False)

    def test_an_unknown_category_fails_clause_one(self) -> None:
        line = _draw(True, count=1)[0]
        assert not is_corpus_overlapping(line, ["SOMETHING_ELSE"], vendor_resolves=True)


class TestDeterminism:
    def test_one_seed_gives_one_result(self) -> None:
        assert _draw(True, seed=99) == _draw(True, seed=99)

    def test_two_seeds_differ(self) -> None:
        assert _draw(True, seed=99) != _draw(True, seed=100)


class TestRefusals:
    def test_an_uncommitted_category_is_refused(self) -> None:
        rng = np.random.default_rng(SEED)
        with pytest.raises(EquipmentError, match="committed category"):
            draw_equipment(rng, "NOT_A_CATEGORY", True)

    def test_an_empty_dataset_share_is_refused(self) -> None:
        with pytest.raises(EquipmentError, match="empty dataset"):
            catalog_overlap_share([])

    def test_an_unknown_canonical_name_is_refused(self) -> None:
        with pytest.raises(EquipmentError, match="canonical name"):
            alias_spellings("Not A Manufacturer")
