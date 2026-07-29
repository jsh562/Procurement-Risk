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


def _sort_key(line: RankableLine) -> tuple[float, int, int, str]:
    """FR-010, in order.

    ``po_line_id`` terminates it and is what makes the order *total*: the key is
    unique by construction, so no two lines can tie through the whole sequence
    and the same input set always produces the same order.

    The identifier is the stable generated key, never the human-readable
    identity of project, purchase order and line number. The two produce
    different orders and only the generated key is unique — which is the whole
    reason it can terminate the sequence. It is compared as a string so the
    ordering matches what a database `ORDER BY po_line_id` and a JSON consumer
    both see, rather than UUID integer order, which differs.
    """
    return (
        -expected_harm(line),
        line.need_by_offset,
        -line.criticality,
        str(line.po_line_id),
    )


def order_lines(lines: list[RankableLine]) -> list[UUID]:
    """Order lines worst-first and return their identifiers.

    Returns identifiers rather than the lines themselves so a caller cannot
    accidentally treat the result as a re-derived set of rows; the ordering is
    the output, and the rows are already held elsewhere.
    """
    return [line.po_line_id for line in sorted(lines, key=_sort_key)]
