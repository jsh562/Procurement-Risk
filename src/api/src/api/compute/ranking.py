"""The harm score and the ordering it produces.

FR-001, FR-010, FR-013a.

**Expected schedule harm** is the mean over the stored draws of the delivery
overrun past the need-by date, counting zero where a draw delivers on time,
multiplied by the line's criticality:

    E[max(0, delivery − need_by)] × criticality

Read it as "expected days late, weighted by how much being late costs". It is
deliberately *not* the miss probability: a line almost certain to slip by one
day matters less than one likely to slip by six weeks, and a probability alone
cannot tell those apart. Multiplying by criticality is what makes the ranking
answer "what should I chase first" rather than "what is most likely to be late".

The mean is taken over the full draw array rather than over a summary of it.
Reducing the draws to a point first — a mean delivery date, a median — and then
computing overrun from that point would collapse the distribution exactly where
its shape is the information: a symmetric distribution and a long-tailed one
with the same median carry very different expected overruns.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from uuid import UUID

from api.compute.ordering import SORT_DIRECTIONS, SORT_KEYS

__all__ = ["RankableLine", "expected_harm", "order_lines"]

#: FR-010's sequence, for the error message and for anyone reading the sort key
#: below without wanting to decode a tuple.
TIEBREAK_DESCRIPTION: Final[str] = (
    "expected harm descending, then need-by ascending, then criticality descending, "
    "then line identifier ascending"
)


@dataclass(frozen=True)
class RankableLine:
    """The inputs the ranking reads, and nothing else.

    A narrow record rather than the full ``OpenLine``: the ordering must not be
    able to depend on a field nobody intended it to, and the only reliable way
    to guarantee that is for the field not to be reachable from here.
    """

    po_line_id: UUID
    draws: tuple[float, ...]
    #: Days from the run's as-of date to the effective need-by date. Negative
    #: for a line already late when the run was fitted.
    need_by_offset: int
    criticality: int


def expected_harm(line: RankableLine) -> float:
    """``E[max(0, delivery − need_by)] × criticality``.

    Zero — not negative — for a line whose every draw lands on or before its
    need-by date. That is what creates FR-010's zero-harm block: at a full
    horizon a large set of comfortable lines all score exactly zero, and the
    tiebreak is the *only* ordering within it rather than a rare fallback.
    """
    if not line.draws:
        raise ValueError(
            "A line with no draws has no expected harm. It is a not-covered line and belongs "
            "in the unranked group (FR-016), not in the ranking with a score of zero — which "
            "would place it among the safest lines on the strength of having no forecast."
        )
    overrun = sum(max(0.0, draw - line.need_by_offset) for draw in line.draws) / len(line.draws)
    return overrun * line.criticality


def _primary(line: RankableLine, sort_key: str) -> float:
    """The active key's value, negated where the key runs descending.

    Each of FR-026's four carries one fixed direction, so the negation belongs
    here rather than in a caller-supplied flag: `expected_harm ascending` would
    order the safest line first and call it a worklist.

    `calendar_margin` is the need-by offset itself — days from the run's anchor
    to the need-by date — and it ascends, so the tightest margin leads. It takes
    no forecast input, which is what keeps it from reconstructing a delivery
    date by subtraction (FR-009).
    """
    if sort_key == "expected_harm":
        return -expected_harm(line)
    if sort_key == "need_by_date":
        return float(line.need_by_offset)
    if sort_key == "criticality":
        return float(-line.criticality)
    if sort_key == "calendar_margin":
        # The same value as `need_by_date`, and that is arithmetic rather than
        # an oversight: every line in a response shares one active run, so the
        # margin is the need-by date minus a constant and the two keys induce
        # the same order. They are kept separate because they answer different
        # questions — "what is due soonest" and "what has least room" — and
        # because they diverge the moment a response spans two anchors, which
        # FR-002's single-active-run rule currently forbids and a later epic
        # could permit.
        return float(line.need_by_offset)
    raise AssertionError(  # pragma: no cover - `order_lines` validates before sorting
        f"{sort_key!r} reached the comparator. `order_lines` rejects an unknown key before "
        "sorting, so this is unreachable unless something calls `_sort_key` directly."
    )


def _sort_key(line: RankableLine, sort_key: str) -> tuple[float, float, int, int, str]:
    """The active key, then FR-010's tiebreak in order.

    ``po_line_id`` terminates it and is what makes the order *total*: the key is
    unique by construction, so no two lines can tie through the whole sequence
    and the same input set always produces the same order.

    The identifier is the stable generated key, never the human-readable
    identity of project, purchase order and line number. The two produce
    different orders and only the generated key is unique — which is the whole
    reason it can terminate the sequence. It is compared as a string so the
    ordering matches what a database `ORDER BY po_line_id` and a JSON consumer
    both see, rather than UUID integer order, which differs.

    The tiebreak follows *every* key, not only the default. Under
    `need_by_date`, a whole day's lines tie on the key alone, and without the
    tiebreak their order would be whatever the query returned — which is the
    same defect FR-010 names for the zero-harm block, in a different place.
    """
    return (
        _primary(line, sort_key),
        -expected_harm(line),
        line.need_by_offset,
        -line.criticality,
        str(line.po_line_id),
    )


def order_lines(lines: list[RankableLine], *, sort_key: str = "expected_harm") -> list[UUID]:
    """Order lines under ``sort_key`` and return their identifiers.

    Returns identifiers rather than the lines themselves so a caller cannot
    accidentally treat the result as a re-derived set of rows; the ordering is
    the output, and the rows are already held elsewhere.

    Raises:
        ValueError: If ``sort_key`` is not one of FR-026's four.

    The key is validated *before* the sort rather than inside the comparator.
    ``sorted`` never calls the comparator on an empty list, so a validation that
    lived only there would accept an unknown key on any empty ranked group —
    every no-active-run page, every empty filter — and reject it only once rows
    appeared. A parameter that is valid until the data arrives is worse than one
    that is never validated, because the failure surfaces far from its cause.
    """
    if sort_key not in SORT_DIRECTIONS:
        raise ValueError(
            f"{sort_key!r} is not one of FR-026's four keys: {', '.join(SORT_KEYS)}. The "
            "enumeration is closed: a fifth key would have to satisfy FR-005 through FR-008 "
            "first, and one ordering lines by a single delivery date or one quantile alone is "
            "the point estimate re-entering through the sort control."
        )
    return [line.po_line_id for line in sorted(lines, key=lambda item: _sort_key(item, sort_key))]
