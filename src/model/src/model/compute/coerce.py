"""Deterministic numeric and date coercion. The model never supplies a type.

FR-049 / FR-062, Principle V. The model reports what a page *prints*; turning
`1,250` into a number and `3/14/26` into a date is arithmetic, and arithmetic is
code's. That split is not a style preference — a typed value the model produced
is a value nothing can recompute, so a stored quantity would rest on the same
evidence as the confidence score it was supposed to be independent of.

**Where each form is held** (FR-062). `extracted_value` carries canonical text on
every row and a typed numeric on exactly the number-kind rows:

| Kind | `value_text` | `value_number` |
|---|---|---|
| `text` | the printed text, character for character (FR-027) | NULL |
| `number` | the printed text, character for character | the parsed `Decimal` |
| `date` | ISO-8601 | NULL |

The date row is the only one where the stored text differs from what the page
shows, and STF-004 is the finding that names it: printed-text comparisons are
scoped to **text kinds** for exactly this reason, and the printed form of a date
stays recoverable from the cited chunk.

**The accepted forms are a closed list, and anything outside it raises.** Not
`None`, not zero, not "best effort" — FR-037's "absent, not inferred" reaches
this layer, and every plausible default here manufactures a value the document
does not print. The caller records an extraction failure with outcome
`type_coercion_failed` and stores nothing.

**Decimal, never float.** A price or an extended total parsed through binary
floating point is a stored number that does not equal the printed one, and
`extracted_value.value_number` is `numeric`. `Decimal` from the digit string is
exact; `float` is a rounding decision nobody declared.

This module imports nothing from `model.ingest`, `model.llm`, or `gateway`. It
is pure: same input, same output, no clock and no locale.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Final

__all__ = [
    "ACCEPTED_DATE_FORMS",
    "MONTH_ABBREVIATIONS",
    "TWO_DIGIT_YEAR_PIVOT",
    "VALUE_KINDS",
    "CoercedValue",
    "CoercionError",
    "coerce_value",
]


class CoercionError(ValueError):
    """A printed value is outside the accepted forms for its kind.

    One type for every rejection, because a caller does the same thing with all
    of them: record an extraction failure with outcome `type_coercion_failed`
    and store no value. Distinguishing "not a number" from "not a date" in the
    type would offer a branch nobody needs and invite one that recovers.
    """


#: `ck_field_vocabulary__value_kind`'s closed set. Restated so a kind outside it
#: is refused here rather than by a constraint after the row is built.
VALUE_KINDS: Final[tuple[str, ...]] = ("text", "number", "date")

#: The numeric date forms, tried in this order. `strptime` patterns, so they are
#: also what a test renders a known day through — the same string is both the
#: parser's grammar and the property's generator, which is what makes "every
#: accepted form of one day agrees" a statement about the declared list rather
#: than about a second list written beside it.
#:
#: ISO first, because it is unambiguous and is what the synthetic layer prints.
#: The two slash forms are month-first: the corpus approximates United States
#: federal submittal practice, and `3/14/26` there is 14 March. **The ambiguity
#: is resolved by declaration, not by inspection** — a parser that guessed
#: day-first when the first field exceeded 12 would read `3/14/26` and `14/3/26`
#: as the same day and `3/4/26` as whichever it felt like.
ACCEPTED_DATE_FORMS: Final[tuple[str, ...]] = ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y")

#: English month abbreviations, positionally indexed by month number minus one.
#: Held here rather than obtained from `strftime`/`strptime`'s `%b`, which
#: resolves through the C library's locale: a machine configured in another
#: language would parse `Mar` as unrecognised and `Mär` as valid, so a stored
#: date would depend on the machine that ingested it.
MONTH_ABBREVIATIONS: Final[tuple[str, ...]] = (
    "Jan",
    "Feb",
    "Mar",
    "Apr",
    "May",
    "Jun",
    "Jul",
    "Aug",
    "Sep",
    "Oct",
    "Nov",
    "Dec",
)

#: A two-digit year at or below this resolves into the 2000s; above it, the
#: 1900s. Stated rather than inherited from `strptime`'s `%y`, which applies the
#: same POSIX window — the constant is here so the rule is greppable, testable,
#: and stable if an interpreter ever changed its default. A *sliding* pivot
#: relative to the current year was rejected outright: it would re-date stored
#: values as the years passed, so the same document would coerce differently in
#: 2030 than it does today.
TWO_DIGIT_YEAR_PIVOT: Final[int] = 68

#: `[-+]?` then digits, optionally grouped in threes by commas, optionally with a
#: decimal fraction. Anchored at both ends, so `12 ea`, `approx. 12` and `$12.00`
#: are rejected rather than mined for the digits inside them.
#:
#: Grouping is validated rather than stripped: `1,2,3` is not a number printed
#: with separators, it is three numbers or a typo, and a parser that removed
#: every comma would read it as `123`.
_NUMBER = re.compile(
    r"""
    ^
    (?P<sign>[-+])?
    (?P<whole>
        [0-9]{1,3}(?:,[0-9]{3})+     # grouped: 1,250 / 1,234,567
        | [0-9]+                     # plain:   1250
    )
    (?:\.(?P<fraction>[0-9]+))?
    $
    """,
    re.VERBOSE,
)

#: `14 Mar 2026` and `Mar 14, 2026`. Two named forms rather than a permissive
#: scan, for the same reason the numeric list is closed.
_DAY_MONTH_YEAR = re.compile(
    r"^(?P<day>[0-9]{1,2})\s+(?P<month>[A-Za-z]{3,9})\s+(?P<year>[0-9]{4})$"
)
_MONTH_DAY_YEAR = re.compile(
    r"^(?P<month>[A-Za-z]{3,9})\s+(?P<day>[0-9]{1,2}),\s*(?P<year>[0-9]{4})$"
)


@dataclass(frozen=True)
class CoercedValue:
    """One value in the three forms a row needs: printed, canonical, typed.

    `printed` is kept **beside** the canonical text rather than instead of it,
    so a caller writing the row never has to decide which one the evidence is.
    For text and number kinds the two are equal by construction; for dates they
    differ, and that difference is the whole reason this type carries both.
    """

    printed: str
    value_kind: str
    value_text: str
    value_number: Decimal | None

    def __post_init__(self) -> None:
        # The database states this as a biconditional
        # (`ck_extracted_value__numeric_iff_number_kind`); stating it here too
        # means a row that the write would reject is unconstructible.
        if (self.value_kind == "number") != (self.value_number is not None):
            raise CoercionError(
                f"FR-062: value_number is populated exactly on number-kind values; "
                f"kind={self.value_kind!r} with value_number="
                f"{'set' if self.value_number is not None else 'unset'}"
            )
        if not self.value_text.strip():
            raise CoercionError(
                "`ck_extracted_value__value_text_present` refuses a blank canonical text"
            )


def _month_number(name: str) -> int:
    """The month a printed name denotes, or a refusal.

    Matches the three-letter abbreviation case-insensitively and also accepts the
    full English name, because both are printed. Anything else raises rather than
    resolving to the nearest match — `Smarch` has no nearest match, and a fuzzy
    one would silently store a date the page does not carry.
    """
    folded = name.strip().lower()
    for index, abbreviation in enumerate(MONTH_ABBREVIATIONS, start=1):
        if folded == abbreviation.lower():
            return index
    full = (
        "january",
        "february",
        "march",
        "april",
        "may",
        "june",
        "july",
        "august",
        "september",
        "october",
        "november",
        "december",
    )
    for index, spelled in enumerate(full, start=1):
        if folded == spelled:
            return index
    raise CoercionError(f"{name!r} is not an English month name or three-letter abbreviation")


def _coerce_number(printed: str) -> Decimal:
    """The typed numeric behind a printed quantity or price.

    Raises:
        CoercionError: the text is outside `_NUMBER`. There is no fallback: a
            coercion that returned zero for `TBD` would put a number in a column
            for a value the page does not state, and nothing downstream could
            tell it from a genuine zero.
    """
    matched = _NUMBER.match(printed.strip())
    if matched is None:
        raise CoercionError(
            f"{printed!r} is not a number as printed. The accepted form is an optional "
            f"sign, digits optionally grouped in threes by commas, and an optional "
            f"decimal fraction — anchored at both ends, so a number embedded in other "
            f"text is not mined out of it. FR-037: the value is recorded absent, never "
            f"inferred."
        )
    digits = matched.group("whole").replace(",", "")
    fraction = matched.group("fraction")
    sign = matched.group("sign") or ""
    try:
        return Decimal(f"{sign}{digits}" + (f".{fraction}" if fraction else ""))
    except InvalidOperation as error:  # pragma: no cover - unreachable via `_NUMBER`
        raise CoercionError(f"{printed!r} matched the numeric form but is not a Decimal") from error


def _coerce_date(printed: str) -> date:
    """The calendar day behind a printed date, or a refusal.

    Every accepted form is tried in the declared order and the **first exact
    match wins**; a form that parses with characters left over is not a match.
    `strptime` is strict about that, which is what rejects a timestamp
    (`2026-03-14T09:00:00`) and a month-and-year (`03/2026`) rather than
    truncating one and defaulting the other.

    Calendar validity comes free from `strptime`, so `2026-02-30` and
    `2026-13-01` raise instead of rolling over into March and January.
    """
    text = printed.strip()
    if not text:
        raise CoercionError("a blank string is not a date")

    for form in ACCEPTED_DATE_FORMS:
        try:
            return datetime.strptime(text, form).date()  # noqa: DTZ007 - a date, not an instant
        except ValueError:
            continue

    for pattern in (_DAY_MONTH_YEAR, _MONTH_DAY_YEAR):
        matched = pattern.match(text)
        if matched is None:
            continue
        month = _month_number(matched.group("month"))
        try:
            return date(int(matched.group("year")), month, int(matched.group("day")))
        except ValueError as error:
            raise CoercionError(f"{printed!r} names no day on the calendar") from error

    raise CoercionError(
        f"{printed!r} is not a date in any accepted form. The closed list is "
        f"{list(ACCEPTED_DATE_FORMS)} plus `D Mon YYYY` and `Mon D, YYYY`; the slash "
        f"forms are month-first by declaration, and a two-digit year at or below "
        f"{TWO_DIGIT_YEAR_PIVOT} resolves into the 2000s. FR-037: outside the list the "
        f"value is absent, never inferred."
    )


def coerce_value(printed: str, value_kind: str) -> CoercedValue:
    """Coerce one printed value to the forms its row stores (FR-049, FR-062).

    Args:
        printed: the value exactly as the page shows it, as the model reported
            it. Never pre-cleaned by the caller — FR-027 makes the printed text
            the evidence, and a caller that stripped it before calling would
            have destroyed the thing being stored.
        value_kind: `text`, `number`, or `date`, taken from the seeded
            vocabulary term rather than guessed from the value. Guessing is what
            makes a part number like `2026-03-14` become a date.

    Returns:
        The printed text, the canonical text the row stores, and the typed
        numeric where the kind has one.

    Raises:
        CoercionError: the kind is outside the closed three, the value is blank,
            or it is outside the accepted forms for its kind. Every one of them
            means the same thing to a caller — record an extraction failure with
            outcome `type_coercion_failed` and store no value.
    """
    if value_kind not in VALUE_KINDS:
        raise CoercionError(
            f"{value_kind!r} is outside the closed kind set {list(VALUE_KINDS)}, which "
            f"`ck_field_vocabulary__value_kind` fixes. A fourth kind is a migration, "
            f"not a new label."
        )
    if not printed.strip():
        raise CoercionError(
            f"a {value_kind}-kind value carries no printed text. "
            f"`ck_extracted_value__value_text_present` refuses the row, and a value "
            f"that is not printed is FR-037's `no_value_found` rather than a blank one."
        )

    if value_kind == "text":
        # Returned untouched — not stripped, not case-folded, not
        # Unicode-normalized. FR-027 forbids storing a cleaned form in place of
        # the printed one, and `manufacturer` and `part_number` are both here.
        return CoercedValue(
            printed=printed, value_kind=value_kind, value_text=printed, value_number=None
        )

    if value_kind == "number":
        return CoercedValue(
            printed=printed,
            value_kind=value_kind,
            # The printed text stays the canonical text: it is the evidence the
            # citation points at (FR-062), and the typed form travels beside it.
            value_text=printed,
            value_number=_coerce_number(printed),
        )

    return CoercedValue(
        printed=printed,
        value_kind=value_kind,
        value_text=_coerce_date(printed).isoformat(),
        value_number=None,
    )
