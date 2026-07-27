"""NC-8 — a three-part limitation record must fail the checker.

A record missing a part is not a shorter record. It is a limitation whose
reversal condition nobody stated, which is the thing FR-016 exists to prevent —
and it reads identically to a complete one unless something counts the parts.
"""

from __future__ import annotations

import dataclasses

import pytest

from model.procurement.datasheet import (
    ACTIVE_LIMITATIONS,
    LIMITATION_PARTS,
    DatasheetError,
    LimitationRecord,
    check_limitations,
)

COMPLETE = LimitationRecord(
    "L-99",
    "A control record",
    "scope decision text",
    "supporting evidence text",
    "reversal trigger text",
    "production-scale alternative text",
)


def test_the_complete_control_passes() -> None:
    """Otherwise every failure below could be for the wrong reason."""
    assert check_limitations([COMPLETE]) == 1


@pytest.mark.parametrize("omitted", LIMITATION_PARTS)
def test_each_missing_part_fails(omitted: str) -> None:
    """Every one of the four, not just a representative — a checker covering
    three of four passes the record that omits the fourth."""
    broken = dataclasses.replace(COMPLETE, **{omitted: ""})
    with pytest.raises(DatasheetError, match=omitted):
        check_limitations([broken])


@pytest.mark.parametrize("omitted", LIMITATION_PARTS)
def test_whitespace_does_not_count_as_a_part(omitted: str) -> None:
    """A part filled with spaces satisfies a presence check and discloses
    nothing, which is the cheapest way to defeat this rule."""
    broken = dataclasses.replace(COMPLETE, **{omitted: "   \t\n"})
    with pytest.raises(DatasheetError, match=omitted):
        check_limitations([broken])


def test_a_record_missing_several_parts_names_all_of_them() -> None:
    broken = dataclasses.replace(COMPLETE, reversal_trigger="", supporting_evidence="")
    with pytest.raises(DatasheetError) as raised:
        check_limitations([broken])
    assert "reversal_trigger" in str(raised.value)
    assert "supporting_evidence" in str(raised.value)


def test_an_empty_record_set_fails() -> None:
    """A datasheet with no limitation discloses nothing, and would otherwise
    satisfy '100% of records carry all four parts' vacuously."""
    with pytest.raises(DatasheetError, match="discloses nothing"):
        check_limitations([])


def test_one_broken_record_among_complete_ones_still_fails() -> None:
    """The realistic case: nine good records and one that rotted."""
    broken = dataclasses.replace(COMPLETE, production_scale_alternative="")
    with pytest.raises(DatasheetError, match="L-99"):
        check_limitations([*ACTIVE_LIMITATIONS, broken])


def test_every_shipped_record_is_complete() -> None:
    assert check_limitations(ACTIVE_LIMITATIONS) == len(ACTIVE_LIMITATIONS)


def test_the_four_parts_are_the_declared_ones() -> None:
    assert LIMITATION_PARTS == (
        "scope_decision",
        "supporting_evidence",
        "reversal_trigger",
        "production_scale_alternative",
    )
