"""FR-049 / FR-062: coercion is deterministic code, and it is property-tested.

T044, and it is the **red** half of a strict red-green pair (`plan.md` §The
test-first boundary). This file was authored and run against an absent
`model.compute.coerce` and observed to fail with a collection error before a
line of that module existed; T045 is what makes it pass. A test task marked
complete beside a green suite is the defect the ordering condition exists to
name, so the observed failure is recorded on T044's task line.

**Why this module takes the strict mandate and the chunker does not.** The
boundary is package placement — everything under `model/compute/` — and the rule
behind the placement is that these are the *scoring functions*, the ones whose
output is a number that is stored or published. A coerced quantity is written to
`extracted_value.value_number` and a coerced date to `value_text`; both are
stored, so both are here.

**The relation class is round-trip and metamorphic** (`plan.md`):

*Round-trip* — printed text → typed value → canonical text reproduces the stored
canonical form. Stated as idempotence, which is the checkable form: coercing a
value's own canonical text must land on that same canonical text, or the stored
form depends on how many times it has been through the function.

*Metamorphic* — whitespace and separator variants of one printed date or
quantity coerce to the same typed value, and a string outside the accepted forms
**raises rather than defaulting**. That last one is the property that keeps
FR-037's "absent, not inferred" true at the coercion layer: a coercion that fell
back to zero, or to today, would manufacture a value the document does not
print.

**Month names are rendered from the module's own English table rather than from
`strftime`.** `%b` resolves through the C library's locale, so a machine
configured in another language would render `Mär` and the property would fail
for a reason that has nothing to do with the code under test. Using the table as
*input data* is not testing the module against itself — the table is data and
the parse is the behaviour being asserted.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from model.compute.coerce import (
    ACCEPTED_DATE_FORMS,
    MONTH_ABBREVIATIONS,
    TWO_DIGIT_YEAR_PIVOT,
    CoercionError,
    coerce_value,
)

#: Days whose year a two-digit form can recover. The metamorphic property over
#: *every* accepted form has to stay inside this window, because `%y` is lossy by
#: construction and no implementation can recover a year outside the one it
#: declares — 2000 to 2068 under `TWO_DIGIT_YEAR_PIVOT`.
#:
#: Bounded as a **strategy** rather than by `assume`, deliberately: filtering
#: `st.dates()` down to a 69-year window out of ten millennia discards almost
#: every generated example and Hypothesis reports it as a health-check failure —
#: correctly, since the surviving examples would be neither many nor varied.
two_digit_safe_dates = st.dates(min_value=date(2000, 1, 1), max_value=date(2068, 12, 31))

#: Days a four-digit printed year can express. The month-name forms require
#: exactly four year digits, and `strftime` will not render a year below 1000 on
#: every platform.
four_digit_year_dates = st.dates(min_value=date(1000, 1, 1), max_value=date(9999, 12, 31))


def month_name_forms(day: date) -> tuple[str, ...]:
    """`14 Mar 2026` and `Mar 14, 2026`, built from the module's own table."""
    abbreviation = MONTH_ABBREVIATIONS[day.month - 1]
    return (
        f"{day.day} {abbreviation} {day.year}",
        f"{abbreviation} {day.day}, {day.year}",
    )


def grouped(value: int) -> str:
    """`1234567` as `1,234,567` — the same number, printed with separators."""
    return f"{value:,}"


# ---------------------------------------------------------------------------
# Round-trip: the canonical form is a fixed point
# ---------------------------------------------------------------------------


@given(st.integers(min_value=0, max_value=9_999_999).map(str))
def test_a_coerced_number_recoerces_to_itself(printed: str) -> None:
    """Round-trip. The stored canonical text must not move on a second pass."""
    once = coerce_value(printed, "number")
    twice = coerce_value(once.value_text, "number")
    assert twice.value_text == once.value_text
    assert twice.value_number == once.value_number


@given(four_digit_year_dates)
def test_a_coerced_date_recoerces_to_itself(day: date) -> None:
    """Round-trip on the kind whose canonical text differs from the printed one.

    A date is the only kind this epic stores in a form that is not what the page
    shows (ISO-8601 in `value_text`), so it is the kind where a non-idempotent
    canonicalization would actually change a stored value.
    """
    once = coerce_value(day.isoformat(), "date")
    twice = coerce_value(once.value_text, "date")
    assert once.value_text == day.isoformat()
    assert twice.value_text == once.value_text


@given(st.text(min_size=1).filter(lambda value: value.strip()))
def test_text_is_kept_exactly_as_printed(printed: str) -> None:
    """FR-027: a text-kind value is stored as printed, with no normalized twin.

    Character for character — no case folding, no whitespace collapsing, no
    Unicode normalization. `manufacturer` and `part_number` are text-kind, and
    this is the property SC-013 and SC-027 rest on.
    """
    coerced = coerce_value(printed, "text")
    assert coerced.value_text == printed
    assert coerced.value_number is None
    assert coerced.printed == printed


# ---------------------------------------------------------------------------
# Metamorphic: variants of one printed value agree
# ---------------------------------------------------------------------------


@given(st.integers(min_value=0, max_value=9_999_999), st.sampled_from(["", " ", "  ", "\t", "\n"]))
def test_surrounding_whitespace_does_not_change_the_typed_value(value: int, padding: str) -> None:
    """Metamorphic. Leading and trailing whitespace is layout, not content."""
    assert coerce_value(f"{padding}{value}{padding}", "number").value_number == Decimal(value)


@given(st.integers(min_value=1000, max_value=9_999_999))
def test_thousands_separators_do_not_change_the_typed_value(value: int) -> None:
    """Metamorphic. `1,250` and `1250` are one quantity printed two ways."""
    assert coerce_value(grouped(value), "number").value_number == Decimal(value)
    assert coerce_value(str(value), "number").value_number == Decimal(value)


@given(two_digit_safe_dates)
def test_every_accepted_numeric_date_form_of_one_day_agrees(day: date) -> None:
    """Metamorphic, and the case STF-004 names by hand.

    `3/14/26` and `2026-03-14` are one day printed two ways, and both must store
    the same ISO-8601 text — otherwise a value's stored form would depend on
    which vendor's layout happened to print it.
    """
    stored = {coerce_value(day.strftime(form), "date").value_text for form in ACCEPTED_DATE_FORMS}
    assert stored == {day.isoformat()}


@given(four_digit_year_dates)
def test_every_accepted_month_name_form_of_one_day_agrees(day: date) -> None:
    """The same property over the two forms that print a month name."""
    stored = {coerce_value(printed, "date").value_text for printed in month_name_forms(day)}
    assert stored == {day.isoformat()}


@given(two_digit_safe_dates)
def test_a_two_digit_year_resolves_by_the_declared_pivot(day: date) -> None:
    """The ambiguity is resolved by a **declared** rule, not by the platform's.

    The pivot is a fixed constant the module states, so `26` is 2026 on every
    machine and in every year the code runs. A rule that drifted with the
    current date would silently re-date stored values as the years passed.
    """
    assert coerce_value(day.strftime("%m/%d/%y"), "date").value_text == day.isoformat()


def test_the_pivot_is_stated_rather_than_inherited() -> None:
    """A pivot nobody wrote down is a pivot that can move under an interpreter
    upgrade, taking every stored date with it."""
    assert TWO_DIGIT_YEAR_PIVOT == 68
    assert coerce_value("1/1/68", "date").value_text == "2068-01-01"
    assert coerce_value("1/1/69", "date").value_text == "1969-01-01"


# ---------------------------------------------------------------------------
# The reject branch: absent, never inferred
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "printed",
    [
        "twelve",
        "12 ea",
        "approx. 12",
        "1.2.3",
        "",
        "   ",
        "-",
        "TBD",
        "N/A",
        "1,2,3",
        "12%",
        "$12.00",
    ],
)
def test_a_number_outside_the_accepted_forms_raises(printed: str) -> None:
    """FR-037 at the coercion layer: absent, not inferred.

    Every one of these has a tempting default — zero, `None`, the digits found
    inside it — and each default would manufacture a value the document does not
    print. The function raises instead, and the caller records an extraction
    failure with outcome `type_coercion_failed`.
    """
    with pytest.raises(CoercionError):
        coerce_value(printed, "number")


@pytest.mark.parametrize(
    "printed",
    [
        "sometime in March",
        "2026-13-01",
        "2026-02-30",
        "03/2026",
        "",
        "next Tuesday",
        "2026-03-14T09:00:00",
        "14 Smarch 2026",
    ],
)
def test_a_date_outside_the_accepted_forms_raises(printed: str) -> None:
    """Including the two that parse as *dates* and are not accepted **forms**.

    `2026-02-30` is a calendar impossibility and `2026-03-14T09:00:00` is a
    timestamp; a permissive parser accepts one by rolling over and the other by
    discarding the time, and both silently store something the page does not say.
    """
    with pytest.raises(CoercionError):
        coerce_value(printed, "date")


def test_a_blank_text_value_raises() -> None:
    """`ck_extracted_value__value_text_present` refuses it at the storage
    boundary; refusing it here means the row is never built."""
    with pytest.raises(CoercionError):
        coerce_value("   \t\n ", "text")


def test_an_unknown_kind_raises() -> None:
    """The three kinds are `ck_field_vocabulary__value_kind`'s closed set."""
    with pytest.raises(CoercionError):
        coerce_value("12", "integer")


# ---------------------------------------------------------------------------
# FR-062 — where each form is held
# ---------------------------------------------------------------------------


def test_a_number_keeps_its_printed_text_as_the_evidence() -> None:
    """FR-062: the printed text is what the citation points at.

    So `1,250` is stored as `1,250` in `value_text` and as `1250` in
    `value_number`. Storing the normalized digits in both would lose the
    evidence; storing nothing in `value_number` would leave
    `ck_extracted_value__numeric_iff_number_kind` unsatisfiable.
    """
    coerced = coerce_value("1,250", "number")
    assert coerced.value_text == "1,250"
    assert coerced.value_number == Decimal(1250)
    assert coerced.printed == "1,250"


def test_a_date_stores_iso_text_and_no_number() -> None:
    """`date` terms store ISO-8601 in `value_text` and leave `value_number` NULL —
    revision `0005`'s own words, and `0006`'s biconditional check."""
    coerced = coerce_value("3/14/26", "date")
    assert coerced.value_text == "2026-03-14"
    assert coerced.value_number is None
    assert coerced.printed == "3/14/26"


def test_the_numeric_column_is_populated_exactly_on_number_kinds() -> None:
    """`ck_extracted_value__numeric_iff_number_kind` is a biconditional, so a
    coercion that populated the column on a text kind would produce a row the
    database refuses."""
    assert coerce_value("Nordway", "text").value_number is None
    assert coerce_value("2026-03-14", "date").value_number is None
    assert coerce_value("12", "number").value_number is not None
