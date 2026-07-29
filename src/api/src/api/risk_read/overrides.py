"""Session-scoped need-by what-ifs.

FR-011, FR-031, FR-055.

An adjusted need-by date is a *question*, never a record. Nothing here writes:
the query parameter is the whole mechanism, so reloading without it restores the
recorded dates and no reset endpoint is required. That is why the worklist
exposes no write path at all — a property of the route table rather than a
convention someone maintains.

**Why the round trip happens on the server.** Re-ranking under a changed date
needs the mean over the line's stored draws of the overrun past the new date
(FR-001) and a fresh survival lookup at the new offset (FR-020). Doing that in
the browser would mean shipping four thousand draws and three hundred and
sixty-five survival values per line — and would hand the client the arrays from
which a single delivery date is one aggregation away, defeating FR-007 to save
a request that costs no model call and touches the same rows.

**Refusal and non-application are different things**, and conflating them is the
failure FR-055 names. A malformed date is a `422`: the coordinator asked
something meaningless. An override naming a line that is absent, terminal or out
of scope is *not* an error — it is reported as unapplied with which of the three
it was, because silently dropping it would leave the coordinator believing an
adjustment took effect while reading an ordering computed without it.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Final
from uuid import UUID

__all__ = [
    "MAX_OVERRIDES",
    "OVERRIDE_WINDOW_YEARS",
    "OverrideRejection",
    "UnappliedReason",
    "parse_overrides",
    "partition_overrides",
]

#: Bounds the URL length and the substitution set. A coordinator interrogating
#: an ordering adjusts one line at a time; twenty-five is generous for that and
#: still small enough to keep the recomputation inside the latency budget.
MAX_OVERRIDES: Final[int] = 25

#: A date further out than this is a typo rather than a question — a
#: four-digit-year slip puts a need-by date centuries away, and re-ranking
#: against it would place the line at the bottom and look like a correct answer.
OVERRIDE_WINDOW_YEARS: Final[int] = 10

#: FR-055's three non-application causes. Reported, never silent.
UnappliedReason = str
LINE_NOT_FOUND: Final[UnappliedReason] = "line_not_found"
LINE_TERMINAL: Final[UnappliedReason] = "line_terminal"
LINE_OUT_OF_SCOPE: Final[UnappliedReason] = "line_out_of_scope"


class OverrideRejection(ValueError):
    """An adjustment that is not a well-formed question.

    Distinct from a *non-application*: this one becomes a `422` naming the
    parameter and the reason, because there is nothing to compute an answer
    from. A non-application has a perfectly good date attached to a line this
    response does not contain.
    """

    def __init__(self, reason: str, value: str) -> None:
        super().__init__(f"{reason}: {value!r}")
        self.reason = reason
        self.value = value


@dataclass(frozen=True)
class ParsedOverrides:
    """The admitted adjustment set, keyed by line."""

    by_line: dict[UUID, date]


def parse_overrides(raw: list[str] | None, *, today: date) -> dict[UUID, date]:
    """Parse and admit `<po_line_id>:<YYYY-MM-DD>` entries.

    Raises:
        OverrideRejection: On a malformed entry, a duplicate line, a date
            outside the ten-year window, or more than ``MAX_OVERRIDES`` entries.

    A set exceeding the cap is *refused with the cap stated* rather than
    silently truncated: a truncated set re-ranks the list against dates the
    coordinator did not ask for, and the list would look like an answer.

    The stored constraint ``need_by_date >= order_date`` is deliberately not
    applied. It guards the stored record; this value is never stored, and
    enforcing it would refuse a question the ranking answers correctly — "what
    if this had been needed before we ordered it" is a legitimate thing to ask
    of a line already running late.
    """
    if not raw:
        return {}

    if len(raw) > MAX_OVERRIDES:
        raise OverrideRejection(
            f"at most {MAX_OVERRIDES} adjustments may be applied at once, and a set over the "
            "cap is refused rather than truncated — a truncated set re-ranks the list against "
            "dates that were never asked for",
            f"{len(raw)} entries",
        )

    by_line: dict[UUID, date] = {}
    horizon = timedelta(days=OVERRIDE_WINDOW_YEARS * 366)

    for entry in raw:
        line_part, separator, date_part = entry.partition(":")
        if not separator:
            raise OverrideRejection("expected <po_line_id>:<YYYY-MM-DD>", entry)

        try:
            po_line_id = UUID(line_part)
        except ValueError as exc:
            raise OverrideRejection("the line identifier is not a UUID", entry) from exc

        try:
            adjusted = date.fromisoformat(date_part)
        except ValueError as exc:
            raise OverrideRejection("the date is not a valid calendar date", entry) from exc

        if abs(adjusted - today) > horizon:
            raise OverrideRejection(
                f"the date is more than {OVERRIDE_WINDOW_YEARS} years from today, which is a "
                "typo rather than a question — and re-ranking against it would put the line at "
                "one end of the list and look like a correct answer",
                entry,
            )

        if po_line_id in by_line:
            raise OverrideRejection(
                "the same line is adjusted twice and there is no rule for which wins", entry
            )

        by_line[po_line_id] = adjusted

    return by_line


def partition_overrides(
    overrides: dict[UUID, date],
    *,
    reported: set[UUID],
    terminal: set[UUID],
    out_of_scope: set[UUID],
) -> tuple[dict[UUID, date], list[dict[str, str]]]:
    """Split an adjustment set into what applied and what did not, with causes.

    Args:
        overrides: The admitted set.
        reported: Lines this response actually contains.
        terminal: Lines excluded by FR-022.
        out_of_scope: Lines outside the active project scope.

    Returns:
        The applicable subset, and FR-055's report for the rest. The three
        causes are distinguished rather than collapsed to "not applied" because
        they call for different actions: a terminal line needs no chasing, an
        out-of-scope one needs the filter cleared, and an absent one is a
        stale reference the coordinator is holding.
    """
    applied: dict[UUID, date] = {}
    unapplied: list[dict[str, str]] = []

    for po_line_id, adjusted in overrides.items():
        if po_line_id in reported:
            applied[po_line_id] = adjusted
            continue

        if po_line_id in terminal:
            reason = LINE_TERMINAL
        elif po_line_id in out_of_scope:
            reason = LINE_OUT_OF_SCOPE
        else:
            reason = LINE_NOT_FOUND

        unapplied.append(
            {
                "po_line_id": str(po_line_id),
                "need_by_date": adjusted.isoformat(),
                "reason": reason,
            }
        )

    return applied, unapplied
