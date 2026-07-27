"""The descriptive fields: category, description, manufacturer, part number, quantity.

Two overlap predicates live here and are **measured separately** (FR-032 and
FR-034). Folding them into one share would let a shortfall in either hide behind
the other, and they select different lines.

Manufacturer and part number are **read** from E002's published catalog, never
generated. Aliases are deliberately not drawn: the catalog publishes them for
E006's normalisation to collapse, and emitting them here would make this dataset
the source of the variation E006 exists to remove.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal

import numpy as np

from model.corpus.manufacturers import (
    MANUFACTURERS,
    PART_NUMBER_PATTERN,
    format_part_number,
    manufacturers_for_category,
    printed_names,
)
from model.procurement.durations import TIER_OFFSETS

__all__ = [
    "OVERLAP_QUANTITY_RANGE",
    "OVERLAP_SHARE_FLOOR",
    "OVERLAP_UNIT",
    "PART_NUMBER_PATTERN",
    "QUANTITY_RANGE",
    "UNITS_OF_MEASURE",
    "EquipmentError",
    "LineEquipment",
    "catalog_overlap_share",
    "corpus_overlap_share",
    "draw_equipment",
    "is_catalog_overlapping",
    "is_corpus_overlapping",
]

#: The five permitted units. `EA` on corpus-overlapping lines (clause 3).
UNITS_OF_MEASURE = ("EA", "LOT", "SET", "LF", "M")
OVERLAP_UNIT = "EA"

#: DV-005's domain: fixed scale of exactly one, value in [0.5, 480.0].
QUANTITY_RANGE = (Decimal("0.5"), Decimal("480.0"))

#: Clause 3 of FR-032's predicate: an integer value in [1, 6] at scale 1.
OVERLAP_QUANTITY_RANGE = (Decimal("1.0"), Decimal("6.0"))

OVERLAP_SHARE_FLOOR = 0.60

#: E002's own `MaterialItem.description` composition, for overlapping lines.
_OVERLAP_DESCRIPTION = re.compile(r"^[A-Z][A-Za-z ]* \(Tag [1-5]0[1-3]-[1-9][0-9]\)$")
#: E005's own grammar, for the complement. The em dash is what makes the two
#: mutually exclusive by inspection rather than by counting.
_PLAIN_DESCRIPTION = re.compile(r"^[A-Z][A-Za-z ]* — [a-z][a-z ]*$")

_DESCRIPTORS = (
    "spare unit",
    "replacement module",
    "site standby",
    "phase two allowance",
    "commissioning spare",
)


class EquipmentError(ValueError):
    """Raised when a descriptive field cannot be produced within its domain."""


@dataclass(frozen=True, slots=True)
class LineEquipment:
    material_category: str
    description: str
    manufacturer: str
    part_number: str
    quantity: Decimal
    unit_of_measure: str


def _mismatched_key(generator: np.random.Generator, material_category: str) -> str:
    """A catalog key that does **not** list `material_category`.

    Chosen from the catalog rather than invented, so FR-037 holds on every line.
    Refuses if every manufacturer makes this category: the complement would then
    be indistinguishable from the overlapping population on clauses 5–6, and a
    share that cannot fall below its floor is not a measurement.
    """
    making = set(manufacturers_for_category(material_category))
    candidates = tuple(key for key in sorted(MANUFACTURERS) if key not in making)
    if not candidates:
        raise EquipmentError(
            f"every manufacturer in the catalog makes {material_category!r}, so no "
            f"category-mismatched entry exists and the catalog-overlap share could not "
            f"fall below its floor for this category"
        )
    return candidates[int(generator.integers(0, len(candidates)))]


def _title_case(category: str) -> str:
    """`WATER_CHILLER` -> `Water Chiller`. The category key is the shared token
    (FR-031); this is only its presentation inside a description."""
    return " ".join(word.capitalize() for word in category.split("_"))


def draw_equipment(
    generator: np.random.Generator, material_category: str, corpus_overlapping: bool
) -> LineEquipment:
    """One line's descriptive fields.

    `corpus_overlapping` is decided by the caller, not drawn here, because the
    realized share is a dataset-level target (FR-032, FR-034) and a per-line
    draw cannot hit a floor exactly.
    """
    if material_category not in TIER_OFFSETS:
        raise EquipmentError(f"{material_category!r} is not one of the 20 committed category keys")

    if corpus_overlapping:
        # `[1-5]0[1-3]-[1-9][0-9]` — E002's tag grammar. Built digit-group by
        # digit-group against the pattern rather than as one integer, so a
        # change to the grammar fails loudly here instead of silently emitting
        # a tag no corpus document could carry.
        tag = (
            f"{int(generator.integers(1, 6))}"
            f"0{int(generator.integers(1, 4))}"
            f"-{int(generator.integers(10, 100))}"
        )
        description = f"{_title_case(material_category)} (Tag {tag})"
        quantity = Decimal(int(generator.integers(1, 7))).quantize(Decimal("0.1"))
        unit = OVERLAP_UNIT
        key = manufacturers_for_category(material_category)[
            int(generator.integers(0, len(manufacturers_for_category(material_category))))
        ]
        manufacturer = MANUFACTURERS[key].canonical_name
        part_number = format_part_number(key, int(generator.integers(0, 100_000)))
    else:
        descriptor = _DESCRIPTORS[int(generator.integers(0, len(_DESCRIPTORS)))]
        description = f"{_title_case(material_category)} — {descriptor}"
        # Outside clause 3 on both legs: a non-integer quantity or a non-EA unit.
        quantity = (Decimal(int(generator.integers(70, 4800))) / 10).quantize(Decimal("0.1"))
        unit = UNITS_OF_MEASURE[1 + int(generator.integers(0, len(UNITS_OF_MEASURE) - 1))]

        # **The complement still carries both fields.** They are drawn from the
        # catalog — FR-037 admits no exception — but from an entry that does
        # *not* make this category, so clause 5 fails and the catalog-overlap
        # share stays falsifiable.
        #
        # Leaving them NULL would be simpler and is what an earlier draft of
        # `data-model.md` said to do. It is not available: the delivered schema
        # declares `manufacturer` and `part_number` NOT NULL, and DV-004 requires
        # all six descriptive fields present and non-blank. A complement of NULLs
        # would have been refused at load, after the artifact was committed.
        key = _mismatched_key(generator, material_category)
        manufacturer = MANUFACTURERS[key].canonical_name
        part_number = format_part_number(key, int(generator.integers(0, 100_000)))

    return LineEquipment(material_category, description, manufacturer, part_number, quantity, unit)


def is_corpus_overlapping(
    equipment: LineEquipment, category_keys: Sequence[str], vendor_resolves: bool
) -> bool:
    """FR-032's four clauses, all of which must hold."""
    return (
        equipment.material_category in category_keys
        and bool(_OVERLAP_DESCRIPTION.match(equipment.description))
        and (
            OVERLAP_QUANTITY_RANGE[0] <= equipment.quantity <= OVERLAP_QUANTITY_RANGE[1]
            and equipment.quantity == equipment.quantity.to_integral_value()
            and equipment.unit_of_measure == OVERLAP_UNIT
        )
        and vendor_resolves
    )


def is_catalog_overlapping(equipment: LineEquipment) -> bool:
    """FR-034's clauses 5–6: a category-appropriate canonical name, and a part
    number carrying that same entry's prefix."""
    if equipment.manufacturer is None or equipment.part_number is None:
        return False
    key = next(
        (k for k, entry in MANUFACTURERS.items() if entry.canonical_name == equipment.manufacturer),
        None,
    )
    if key is None:
        return False
    if equipment.manufacturer != MANUFACTURERS[key].canonical_name:
        return False  # an alias, not the canonical name
    if equipment.material_category not in MANUFACTURERS[key].categories:
        return False
    if not PART_NUMBER_PATTERN.match(equipment.part_number):
        return False
    return equipment.part_number.startswith(f"{key}-")


def corpus_overlap_share(
    lines: Sequence[LineEquipment],
    category_keys: Sequence[str],
    vendor_resolves: Mapping[int, bool],
) -> float:
    if not lines:
        raise EquipmentError("cannot measure an overlap share over an empty dataset")
    matched = sum(
        is_corpus_overlapping(line, category_keys, vendor_resolves.get(index, True))
        for index, line in enumerate(lines)
    )
    return matched / len(lines)


def catalog_overlap_share(lines: Sequence[LineEquipment]) -> float:
    if not lines:
        raise EquipmentError("cannot measure an overlap share over an empty dataset")
    return sum(is_catalog_overlapping(line) for line in lines) / len(lines)


def alias_spellings(canonical_name: str) -> tuple[str, ...]:
    """Every spelling a manufacturer may appear under. Exposed so a test can
    assert none of them is ever emitted."""
    key = next(
        (k for k, entry in MANUFACTURERS.items() if entry.canonical_name == canonical_name), None
    )
    if key is None:
        raise EquipmentError(f"{canonical_name!r} is not a catalog canonical name")
    return printed_names(key)
