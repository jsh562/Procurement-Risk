"""FR-059 / SC-046: every value in exactly one group, ordinal 0 a named one.

The two claims worth testing separately, because only one of them is carried by
the schema. That a value has *at most* one membership is a primary key on the
value — unrepresentable rather than merely wrong. That a value has *at least*
one, and the right one, is this module's, and it is what these assertions cover.
"""

from __future__ import annotations

import pytest

from model.ingest.lineitems import (
    DOCUMENT_SCOPED_ORDINAL,
    FIRST_ITEM_ORDINAL,
    GroupedValue,
    LineItemError,
    group_line_items,
    unassigned_positions,
)
from model.llm.schemas import DOCUMENT_SCOPE, TRANSMITTAL_FIELD_SUBSET

DOCUMENT_SCOPED = frozenset(
    entry.name for entry in TRANSMITTAL_FIELD_SUBSET if entry.scope == DOCUMENT_SCOPE
)
RUN = "6f1c0d5e-0000-4000-8000-000000000001"
DOCUMENT = "prj-001-t0001-r0"


def grouped(values: list[GroupedValue]):
    return group_line_items(
        values, run_id=RUN, document_id=DOCUMENT, document_scoped_fields=DOCUMENT_SCOPED
    )


def test_a_document_scoped_field_lands_in_the_declared_group() -> None:
    """FR-059: ordinal 0 means "printed once for the whole document"."""
    result = grouped([GroupedValue(0, "submittal_number", 0)])
    (member,) = result.memberships
    assert member.item_ordinal == DOCUMENT_SCOPED_ORDINAL
    assert member.run_id == RUN
    assert member.document_id == DOCUMENT


def test_the_declared_scope_wins_over_a_reported_ordinal() -> None:
    """The group is a property of the field, not something the model decides.

    A submittal date the model reported against item 2 is still printed once for
    the whole document, and filing it under item 2 would give that item a member
    the page does not put there.
    """
    result = grouped([GroupedValue(0, "submittal_date", 2)])
    assert result.memberships[0].item_ordinal == DOCUMENT_SCOPED_ORDINAL
    assert result.refusals == ()


def test_an_item_scoped_field_keeps_its_printed_item_number() -> None:
    result = grouped([GroupedValue(0, "manufacturer", 1), GroupedValue(1, "part_number", 2)])
    assert [member.item_ordinal for member in result.memberships] == [1, 2]


def test_an_item_scoped_field_reported_at_zero_is_refused_not_relabelled() -> None:
    """Quietly filing it under group 0 would put a per-item value among the
    values that have no item at all."""
    result = grouped([GroupedValue(0, "manufacturer", 0)])
    assert result.memberships == ()
    (refusal,) = result.refusals
    assert refusal.field_name == "manufacturer"
    assert str(FIRST_ITEM_ORDINAL) in refusal.reason


def test_a_line_item_split_across_two_chunks_remains_one() -> None:
    """SC-046's second clause, and the reason AD-010 rejected the source chunk
    as the key: an over-long item entry that split into two chunks would
    silently become two line items with no symptom."""
    result = grouped(
        [
            GroupedValue(0, "manufacturer", 3),
            GroupedValue(1, "part_number", 3),
            GroupedValue(2, "quantity", 3),
        ]
    )
    assert result.grouped_by_item() == {3: (0, 1, 2)}


def test_every_value_is_accounted_for_exactly_once() -> None:
    """ "Zero values sit outside a group and zero sit in two" — by counting."""
    values = [
        GroupedValue(0, "submittal_number", 0),
        GroupedValue(1, "manufacturer", 1),
        GroupedValue(2, "manufacturer", 0),
    ]
    result = grouped(values)
    assert len(result.memberships) + len(result.refusals) == len(values)
    assert unassigned_positions(result, values) == ()


def test_two_values_at_one_position_are_refused() -> None:
    """The association's primary key would reject the second at the write, after
    the transaction had already done its other work."""
    with pytest.raises(LineItemError, match="grouped twice"):
        grouped([GroupedValue(0, "manufacturer", 1), GroupedValue(0, "part_number", 1)])


def test_a_negative_reported_ordinal_is_refused_at_construction() -> None:
    """`ck_extracted_value_line_item__ordinal_non_negative`. Zero is the
    document-scoped group; there is nothing below it."""
    with pytest.raises(LineItemError):
        GroupedValue(0, "manufacturer", -1)


def test_a_membership_is_scoped_to_a_run_and_a_document() -> None:
    with pytest.raises(LineItemError, match="FR-059"):
        group_line_items(
            [GroupedValue(0, "manufacturer", 1)],
            run_id="",
            document_id=DOCUMENT,
            document_scoped_fields=DOCUMENT_SCOPED,
        )


def test_the_document_scoped_set_comes_from_the_declared_subset() -> None:
    """The grouping and the extraction schema must agree about which fields a
    transmittal prints once. Two lists would drift, and the drift would show up
    as a submittal date filed against item 1."""
    assert "submittal_number" in DOCUMENT_SCOPED
    assert "manufacturer" not in DOCUMENT_SCOPED
