"""T121 — NC-23: four well-formed records that omit `L-2` must **fail**.

DV-037 is the presence half of FR-027 and it is a different claim from DV-024's
form half. `data-model.md` § Disclosed Limitations declares four limitations **by
identity**, and the failure it is aimed at is not a malformed record: it is a
report that discloses four impeccable limitations and never mentions that the far
tail of every survival curve is extrapolation.

**NC-8 cannot reach that failure and this file exists because of it.** NC-8
plants a three-part record, so it exercises *form* — the checker it constrains
walks the records that are there. A set of four four-part records that happens
not to include `L-2` satisfies every assertion NC-8 makes, and a DV-037
implementation that never looks for `L-2` would pass both. The discriminator is
asserted here rather than argued: each planted set is shown to satisfy the form
predicate before it is shown to fail the checker.

**The omitted identifier is parametrized rather than fixed on `L-2`.** L-2 is the
one FR-031 and SC-029 name, but a checker looking for exactly `L-2` and nothing
else would pass three quarters of the rule and fail nothing — the declared set is
four, and each of them is owed for its own reason.

The set is kept at four records throughout, so the failure cannot be attributed
to a set that is merely short. What changes is *which* limitations were written,
never how many.
"""

from __future__ import annotations

import pytest

from model.forecast.report import (
    LIMITATION_IDENTIFIERS,
    LIMITATION_PARTS,
    LimitationRecord,
    ReportError,
    check_limitations,
)

#: A well-formed record naming a limitation this epic does not declare. It is the
#: filler that keeps a planted set at four records: without it the set is three
#: and a checker counting records would refuse for the wrong reason.
UNDECLARED_IDENTIFIER = "L-9"


def well_formed(identifier: str) -> LimitationRecord:
    """One record carrying all four parts, in the form the checker admits.

    Deliberately terse text: nothing here should pass because it resembles the
    delivered records, and what is under assertion is which identifiers the
    checker demands rather than what any of them says.
    """
    return LimitationRecord(
        identifier=identifier,
        subject=f"{identifier}'s subject",
        **dict.fromkeys(LIMITATION_PARTS, f"{identifier} part text"),
    )


def carries_four_parts(records: tuple[LimitationRecord, ...]) -> bool:
    """DV-024's form predicate, re-authored here so the discriminator is checkable.

    Written out rather than imported from `check_limitations`, which tests form
    and presence together: the whole point below is that a planted set passes
    *this* and fails *that*, and calling the combined checker to establish the
    first half would be asking one function whether it agrees with itself.
    """
    return bool(records) and all(
        str(value).strip() for record in records for value in record.parts().values()
    )


def set_omitting(identifier: str) -> tuple[LimitationRecord, ...]:
    """Four well-formed records, one declared identifier swapped for a filler.

    The result is exactly as long as the declared set and every record in it is
    exactly as complete, so the only observable difference is that one
    limitation this epic owes its reader was never written.
    """
    return tuple(
        well_formed(UNDECLARED_IDENTIFIER if declared == identifier else declared)
        for declared in LIMITATION_IDENTIFIERS
    )


@pytest.mark.parametrize("omitted", LIMITATION_IDENTIFIERS)
def test_four_well_formed_records_that_omit_a_declared_limitation_fail(omitted: str) -> None:
    """NC-23, planted once per declared identifier, `L-2` among them.

    The form predicate is asserted first and asserted to *pass*: that is the
    whole content of the claim that this file reaches a failure NC-8 does not.
    The checker must then refuse anyway, and its message must name the
    limitation that is missing rather than reporting a count.
    """
    records = set_omitting(omitted)

    assert len(records) == len(LIMITATION_IDENTIFIERS)
    assert carries_four_parts(records), (
        "the planted set is malformed, so its refusal below would be DV-024's form failure "
        "wearing DV-037's name and this file would assert nothing about presence"
    )
    with pytest.raises(ReportError) as raised:
        check_limitations(records)

    assert omitted in str(raised.value)


def test_the_complete_set_of_declared_identifiers_is_accepted() -> None:
    """The positive control, without which every case above is satisfied by refusing.

    A checker that raised on any set at all would pass the four planted cases
    and disclose nothing, so the accepted case is what makes the refusals
    evidence.
    """
    records = tuple(well_formed(identifier) for identifier in LIMITATION_IDENTIFIERS)

    assert carries_four_parts(records)
    assert check_limitations(records) == len(LIMITATION_IDENTIFIERS)


def test_a_superset_carrying_every_declared_identifier_is_accepted() -> None:
    """Presence is a containment, not an equality — a fifth limitation is legal.

    `data-model.md` declares the four a run **owes**; disclosing a fifth is
    Principle VII working rather than failing. An implementation that compared
    the emitted identifiers against the declared set for equality would refuse a
    report that told the reader more, which is the wrong direction to be strict
    in.
    """
    records = (
        *(well_formed(identifier) for identifier in LIMITATION_IDENTIFIERS),
        well_formed(UNDECLARED_IDENTIFIER),
    )

    assert check_limitations(records) == len(LIMITATION_IDENTIFIERS) + 1


def test_the_declared_identifiers_are_the_four_the_data_model_names() -> None:
    """The parametrization's oracle, written independently of the module.

    Every case above ranges over `LIMITATION_IDENTIFIERS`, so shrinking that
    tuple would silently shrink this file. `L-2` is named here because FR-031
    and SC-029 rest on it specifically: it is the horizon's extrapolation past
    the longest observed duration, and it is the one whose absence DV-037 was
    written for.
    """
    assert LIMITATION_IDENTIFIERS == ("L-1", "L-2", "L-3", "L-4")
