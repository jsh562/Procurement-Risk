"""Line-item grouping: run, document, item ordinal — and ordinal 0 is a group.

FR-059, AD-010, SC-046. A transmittal listing five items otherwise yields five
manufacturers and five part numbers with nothing joining them, so this is the
association identity resolution matches on.

**Keyed on the value alone.** `extracted_value_line_item`'s primary key is the
extracted value identifier, so a second membership for one value is
*unrepresentable* rather than merely wrong — which is what makes SC-046's
"exactly one line item" a property of the schema instead of a property of this
module's diligence. What this module owes is the other half: every stored value
gets a membership, and the ordinal it gets is the right one.

**The grouping key is the item ordinal, not the source chunk** (AD-010). Keying
on the chunk would make an over-long item entry that split across two chunks
silently become two line items — one item, no symptom, and the invisible
corruption Principle III targets. The ordinal survives the split because both
chunks' values carry the same printed item number, and `grouped_by_item` below
is what SC-046's second clause tests.

**Ordinal 0 is a declared group meaning "printed once for the whole document"**,
and it is declared per *field* rather than inferred per value. A transmittal
prints its submittal number, submittal date and approval date once for the
document, not once per item; those fields' scope is fixed in
`model.llm.schemas` before any extraction runs, so a value's group is decided by
what kind of field it is and never by pattern-matching a sentinel. That is what
lets "100% of extracted values belong to exactly one line item" stay literally
absolute over every row instead of being narrowed afterwards to the values that
happened to have an item number.

**Per-field cardinality within an item is left unasserted** and disclosed as
uncovered (spec Disclosed Limitations, gap G-5). "One manufacturer per line
item" would need `field_name` denormalized onto the association and held equal
by a composite foreign key against a unique key `extracted_value` does not have
and this epic may not add — and it is not universally true anyway, since an item
may legitimately cite two compliance standards.

This module writes no rows. It decides memberships; `ingest/writer.py` writes
them at §Write Order step 6, after step 5, because the association references
`ingestion_run_extracted_value` rather than `extracted_value` directly.
"""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Final

__all__ = [
    "DOCUMENT_SCOPED_ORDINAL",
    "FIRST_ITEM_ORDINAL",
    "GroupedValue",
    "LineItemError",
    "LineItemGrouping",
    "LineItemMembership",
    "RefusedGrouping",
    "group_line_items",
]


class LineItemError(ValueError):
    """A grouping cannot be decided, or must not be.

    Raised for the structural impossibilities — a negative ordinal, a run or
    document that disagrees with the value's own attribution. A value whose
    *reported* ordinal contradicts its field's declared scope is **not** one of
    these: it is a per-field refusal returned beside the memberships, because
    the caller records it as an extraction failure rather than aborting the
    document.
    """


#: FR-059's declared group for values a transmittal prints once for the whole
#: document. `ck_extracted_value_line_item__ordinal_non_negative` admits it, and
#: `data-model.md` is explicit that it is a membership like any other rather than
#: a sentinel to pattern-match: a reader selects `item_ordinal = 0` to ask for
#: document-scoped values and `>= 1` to iterate items.
DOCUMENT_SCOPED_ORDINAL: Final[int] = 0

#: Real items are one-based, matching the printed item numbering on the
#: transmittal. Numbering them from 0 would have made the document-scoped group
#: indistinguishable from the first printed item.
FIRST_ITEM_ORDINAL: Final[int] = 1


@dataclass(frozen=True)
class GroupedValue:
    """One extracted value, as grouping sees it.

    `position` identifies the value within the document's prepared value
    sequence rather than by identifier, for the same reason `writer.CitedChunk`
    names a chunk by ordinal: value identifiers are minted inside the document's
    transaction at §Write Order step 2, so nothing upstream of the write can
    carry one.

    `reported_ordinal` is what the model said, kept separate from the ordinal
    this module decides. Where the two differ the difference is a refusal rather
    than a silent correction — a value quietly moved into another group is a
    line item that gained a member nobody can trace.
    """

    position: int
    field_name: str
    reported_ordinal: int

    def __post_init__(self) -> None:
        if self.position < 0:
            raise LineItemError(f"a value position is non-negative; got {self.position}")
        if not self.field_name.strip():
            raise LineItemError("a grouped value names the field it carries")
        if self.reported_ordinal < 0:
            raise LineItemError(
                f"{self.field_name}: item ordinal {self.reported_ordinal} is negative, "
                f"which `ck_extracted_value_line_item__ordinal_non_negative` refuses. "
                f"Zero is the document-scoped group; there is nothing below it."
            )


@dataclass(frozen=True)
class LineItemMembership:
    """One `extracted_value_line_item` row, before the value identifier exists.

    `run_id` and `document_id` are carried because the association's foreign key
    targets `ingestion_run_extracted_value (extracted_value_id, run_id,
    document_id)` — all three in one referenced key — which is what makes the
    membership's run and document unable to disagree with the value's own
    attribution, and what makes the grouping generation-scoped so two
    generations never merge their item 3.
    """

    position: int
    run_id: str
    document_id: str
    item_ordinal: int


@dataclass(frozen=True)
class RefusedGrouping:
    """A value whose reported ordinal contradicts its field's declared scope.

    Returned rather than raised, and returned **per value**: the caller writes
    one extraction failure per refusal and keeps the rest of the document's
    values, exactly as `model.llm.schemas.bound_field_names` returns its refused
    names rather than raising on the first.
    """

    position: int
    field_name: str
    reported_ordinal: int
    reason: str


@dataclass(frozen=True)
class LineItemGrouping:
    """Every membership this document's values earned, and every refusal."""

    memberships: tuple[LineItemMembership, ...]
    refusals: tuple[RefusedGrouping, ...]

    # `item_ordinals` stood here and was deleted at QC iteration 3: no caller.
    # `grouped_by_item` below already exposes the ordinals as its keys, which
    # is the form SC-046's second clause is read in.

    def grouped_by_item(self) -> Mapping[int, tuple[int, ...]]:
        """Value positions per item ordinal — SC-046's second clause, readable.

        "A line item split across two chunks remains one" is exactly the
        statement that two values read from different chunks with the same
        printed item number land in one entry here. Nothing about a chunk
        appears in the key, which is the point.
        """
        groups: dict[int, list[int]] = {}
        for member in self.memberships:
            groups.setdefault(member.item_ordinal, []).append(member.position)
        return {ordinal: tuple(sorted(positions)) for ordinal, positions in sorted(groups.items())}


def group_line_items(
    values: Iterable[GroupedValue],
    *,
    run_id: str,
    document_id: str,
    document_scoped_fields: Collection[str],
) -> LineItemGrouping:
    """Decide one membership per value, by the field's declared scope (FR-059).

    Args:
        values: this document's extracted values, in the order they will be
            written. Position *i* here is position *i* in the writer's prepared
            value sequence, which is how the membership finds its row.
        run_id: the run this generation belongs to. Held equal to the value's
            own attribution by the association's composite foreign key; passed
            here so the membership carries it rather than a later step
            reconstructing it.
        document_id: likewise.
        document_scoped_fields: the field names declared as printed once for the
            whole document — `model.llm.schemas`' `DOCUMENT_SCOPE` terms. Passed
            in rather than imported so this module does not depend on the
            package that talks to the provider, and so a run that narrowed its
            vocabulary groups by the subset it actually attempted.

    Returns:
        The memberships and the refusals. Every value appears in exactly one of
        the two — which is what makes "zero values sit outside a group and zero
        sit in two" checkable by counting rather than by inspection.

    Raises:
        LineItemError: `run_id` or `document_id` is blank, or two values share a
            position. A duplicate position would produce two memberships for one
            row, which the association's primary key would then reject at the
            write — after the transaction had already done its other work.

    **The declared scope wins over the reported ordinal, in one direction only.**
    A document-scoped field is placed in group 0 whatever the model reported,
    because the group is a property of the field and the model is not being
    asked to decide it. An item-scoped field reporting ordinal 0 is *refused*
    rather than placed in group 0: it is the model saying "this manufacturer
    belongs to no printed item", which is either a mis-read or a document this
    epic cannot group, and quietly filing it under the document-scoped group
    would put a per-item value in the group reserved for values that have no
    item.
    """
    if not run_id.strip() or not document_id.strip():
        raise LineItemError(
            "FR-059: a line-item membership is scoped to a run and a document; both are "
            "part of the foreign key that holds it equal to the value's own attribution"
        )

    scoped = set(document_scoped_fields)
    memberships: list[LineItemMembership] = []
    refusals: list[RefusedGrouping] = []
    seen: set[int] = set()

    for value in values:
        if value.position in seen:
            raise LineItemError(
                f"FR-059: value position {value.position} is grouped twice. The "
                f"association's primary key is the value alone, so a second membership "
                f"is unrepresentable — this would fail at the write, after the "
                f"transaction had done its other work."
            )
        seen.add(value.position)

        if value.field_name in scoped:
            memberships.append(
                LineItemMembership(
                    position=value.position,
                    run_id=run_id,
                    document_id=document_id,
                    item_ordinal=DOCUMENT_SCOPED_ORDINAL,
                )
            )
            continue

        if value.reported_ordinal < FIRST_ITEM_ORDINAL:
            refusals.append(
                RefusedGrouping(
                    position=value.position,
                    field_name=value.field_name,
                    reported_ordinal=value.reported_ordinal,
                    reason=(
                        f"{value.field_name} is printed against a numbered item, so it "
                        f"belongs to an item from {FIRST_ITEM_ORDINAL} upwards. Ordinal "
                        f"{DOCUMENT_SCOPED_ORDINAL} is the declared group for values a "
                        f"document prints once for the whole document, and filing a "
                        f"per-item value there would put it among values that have no "
                        f"item at all (FR-059)."
                    ),
                )
            )
            continue

        memberships.append(
            LineItemMembership(
                position=value.position,
                run_id=run_id,
                document_id=document_id,
                item_ordinal=value.reported_ordinal,
            )
        )

    return LineItemGrouping(memberships=tuple(memberships), refusals=tuple(refusals))


def unassigned_positions(
    grouping: LineItemGrouping, values: Sequence[GroupedValue]
) -> tuple[int, ...]:
    """Positions that earned neither a membership nor a refusal (SC-046).

    Always empty for a grouping this module produced, and computed anyway: the
    criterion is "zero values sit outside a group", and a total check that
    cannot fail is not a check. A caller publishing SC-046 calls this and
    publishes the count with the population, per FR-068.
    """
    accounted = {member.position for member in grouping.memberships} | {
        refusal.position for refusal in grouping.refusals
    }
    return tuple(sorted(value.position for value in values if value.position not in accounted))
