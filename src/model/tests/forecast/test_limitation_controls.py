"""T072 — NC-8: a deliberately three-part limitation record must **fail**.

`check_limitations` is the only thing standing between FR-027's four-part form
and a report that discloses a limitation without saying what would reverse it.
DV-024 asserts that every emitted record carries four parts, and that assertion
is satisfied identically by a checker which inspects nothing — every record in a
set of zero carries four parts, and so does every record when the loop body is a
comment. This file is the planted positive that makes the green one evidence.

**Every part is planted separately.** One three-part record would prove only
that *some* part is looked at, and the part a checker is most likely to drop is
the one nobody thinks about — the reversal trigger and the production-scale
alternative are the two Principle VII names last and the two an author is most
likely to leave for later. Parametrizing over `LIMITATION_PARTS` is what makes
the claim "all four are inspected" rather than "at least one is".

**Both spellings of missing are planted**, because they fail differently: an
empty string is a part nobody wrote, and a whitespace-only string is a part
somebody left a placeholder in. `check_limitations` strips before testing, so
the second is the one a naive truthiness test admits.

The presence half — a set of four well-formed records that omits `L-2` — is
**not** here. That is NC-23 and it lives in `test_limitation_presence.py`,
because form and presence are different claims and a file asserting both would
report either failure under one name.
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

#: The two spellings of "this part was not written". Whitespace is the
#: interesting one: it is what a placeholder leaves behind, and a checker testing
#: truthiness rather than `strip()` admits it.
ABSENT_VALUES = ("", "   \t  ")


def well_formed(identifier: str, **overrides: str) -> LimitationRecord:
    """One record carrying all four parts, with the named parts replaced.

    The text is deliberately unlike the delivered records' — nothing here should
    pass because it resembles `report.limitations()`'s output. What is under
    assertion is the checker, so the payload only has to be four non-empty parts
    and a subject, and the overrides are what turn a passing record into the
    planted failing one.
    """
    parts = dict.fromkeys(LIMITATION_PARTS, f"{identifier} part text")
    parts.update(overrides)
    return LimitationRecord(
        identifier=identifier,
        subject=f"{identifier}'s subject",
        **parts,
    )


def complete_set() -> tuple[LimitationRecord, ...]:
    """The four declared identifiers, each well formed. The positive control.

    Present so that every failing case below is a failure *against* a set the
    checker accepts: a checker that raised on everything would satisfy each
    planted case in this file and disclose nothing, which is the mirror image of
    the hole NC-8 exists to close.
    """
    return tuple(well_formed(identifier) for identifier in LIMITATION_IDENTIFIERS)


def test_a_set_of_four_well_formed_records_is_accepted() -> None:
    """The positive control: the checker accepts what it is meant to accept.

    The count it returns is what the emitted section's header states, so a
    checker that quietly dropped a record would be visible here rather than only
    in the document a reader receives.
    """
    records = complete_set()

    assert check_limitations(records) == len(records) == len(LIMITATION_IDENTIFIERS)


@pytest.mark.parametrize("part", LIMITATION_PARTS)
@pytest.mark.parametrize("absent", ABSENT_VALUES)
def test_a_record_missing_any_one_of_the_four_parts_fails(part: str, absent: str) -> None:
    """NC-8, planted once per part and once per spelling of missing.

    The set is otherwise complete and otherwise well formed — every declared
    identifier is present, every other part is written — so the only thing that
    can fail is the part this case removed. A checker inspecting three of the
    four parts passes seven of these eight cases and fails the two that matter.
    """
    records = (
        well_formed(LIMITATION_IDENTIFIERS[0], **{part: absent}),
        *(well_formed(identifier) for identifier in LIMITATION_IDENTIFIERS[1:]),
    )

    with pytest.raises(ReportError) as raised:
        check_limitations(records)

    assert part in str(raised.value)
    assert LIMITATION_IDENTIFIERS[0] in str(raised.value)


def test_the_failure_names_every_part_the_record_is_short_of() -> None:
    """A record short of two parts reports both, rather than the first one found.

    An author repairing the report reads this message and stops when it goes
    quiet. Reporting one part at a time turns a two-part repair into two rounds
    and, more to the point, makes the message a lower bound on the damage that
    reads like a description of it.
    """
    records = (
        well_formed(
            LIMITATION_IDENTIFIERS[0], reversal_trigger="", production_scale_alternative="  "
        ),
        *(well_formed(identifier) for identifier in LIMITATION_IDENTIFIERS[1:]),
    )

    with pytest.raises(ReportError) as raised:
        check_limitations(records)

    assert "reversal_trigger" in str(raised.value)
    assert "production_scale_alternative" in str(raised.value)


def test_an_empty_limitation_set_fails_rather_than_passing_vacuously() -> None:
    """The degenerate case a "100% carry four parts" claim is true of.

    Nothing in a universally quantified check objects to an empty set, so a
    report that emitted no limitation at all would satisfy DV-024 as stated. It
    is refused here instead, which is why `check_limitations` tests the set
    before it tests any record in it.
    """
    with pytest.raises(ReportError) as raised:
        check_limitations(())

    assert "discloses nothing" in str(raised.value)


def test_the_four_parts_the_checker_quantifies_over_are_the_four_principle_vii_names() -> None:
    """The parametrization's own oracle, so this file cannot shrink silently.

    Every case above ranges over `LIMITATION_PARTS`, so removing a part from
    that tuple would remove the case that plants it and leave a green suite
    asserting less than it did. The four names are written out here once,
    independently of the module, exactly to stop that.
    """
    assert LIMITATION_PARTS == (
        "scope_decision",
        "supporting_evidence",
        "reversal_trigger",
        "production_scale_alternative",
    )
