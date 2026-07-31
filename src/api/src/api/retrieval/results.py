"""Results are projected from the chunk row, and constructed no other way.

Spec FR-008, FR-009, FR-013 and AD-004. Principle I: every figure traces to the
record it came from, and a page number is the figure most likely to be invented
— it is a plausible small integer, so a wrong one looks exactly like a right one
and a reader following a citation lands on the wrong page with no error anywhere.

**The guarantee is structural, not procedural.** `RetrievalResult` has no public
constructor that accepts a page. The only way to build one is `_from_chunk_row`,
which reads every provenance field from one database row, so a caller *cannot*
supply a page from a model, a heuristic, or an off-by-one. `test_page_provenance.py`
asserts the factory is the only construction site rather than trusting review to
notice a second one.

**Empty is empty.** FR-009: a search matching nothing returns nothing. It is
worth stating because the tempting behaviour — relax the query, pad from a
weaker arm, return the nearest vectors regardless of match — is exactly what
turns "no evidence" into "here is some evidence", which Principle III forbids
where the mistake is invisible.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

__all__ = [
    "MatchKind",
    "RetrievalResult",
    "results_from_rows",
]


class MatchKind(StrEnum):
    """How a result came to be in the set.

    A closed vocabulary rather than a free string: FR-013 requires a
    deterministic identifier hit be distinguishable from a ranked relevance hit
    *by a machine*, and any string a caller can invent is distinguishable only
    by a reader who already knows the convention.
    """

    #: The part-number route matched this chunk's identifier exactly.
    DETERMINISTIC_IDENTIFIER = "deterministic_identifier"
    #: Fusion ranked it. The ordinary case.
    RANKED_RELEVANCE = "ranked_relevance"


#: The columns `_from_chunk_row` reads, in order. Named so the projection and
#: the query cannot drift apart silently — a mismatch raises here rather than
#: producing a result whose page came from whatever column happened to be third.
PROJECTED_COLUMNS: Final = (
    "chunk_id",
    "document_id",
    "document_type",
    "project_id",
    "page_number",
    "body_text",
)


@dataclass(frozen=True)
class RetrievalResult:
    """One retrieved passage and the record it came from.

    Frozen: a result whose page could be reassigned after construction would
    defeat the point of controlling construction.
    """

    chunk_id: str
    document_id: str
    document_type: str
    project_id: str
    page_number: int
    body_text: str
    match_kind: MatchKind
    fused_rank: int | None

    def __post_init__(self) -> None:
        # Pages are one-based; the schema enforces it on write and this enforces
        # it on read, because a zero here would mean the projection lost a row's
        # value somewhere between the two.
        if self.page_number < 1:
            msg = f"page_number must be one-based, found {self.page_number}"
            raise ValueError(msg)


def _from_chunk_row(
    row: Sequence[Any],
    *,
    match_kind: MatchKind,
    fused_rank: int | None,
) -> RetrievalResult:
    """Project one database row into a result.

    **The only construction site.** Every provenance field is read positionally
    from `row`, in `PROJECTED_COLUMNS` order, so nothing here can originate
    anywhere but the chunk record. `match_kind` and `fused_rank` are properties
    of *how the row was found* rather than of the row, which is why they are the
    only two arguments and why neither is provenance.
    """
    if len(row) != len(PROJECTED_COLUMNS):
        msg = (
            f"expected {len(PROJECTED_COLUMNS)} projected columns "
            f"{PROJECTED_COLUMNS}, found {len(row)}"
        )
        raise ValueError(msg)
    chunk_id, document_id, document_type, project_id, page_number, body_text = row
    return RetrievalResult(
        chunk_id=str(chunk_id),
        document_id=str(document_id),
        document_type=str(document_type),
        project_id=str(project_id),
        page_number=int(page_number),
        body_text=str(body_text),
        match_kind=match_kind,
        fused_rank=fused_rank,
    )


def results_from_rows(
    rows: Sequence[Sequence[Any]],
    *,
    match_kind: MatchKind = MatchKind.RANKED_RELEVANCE,
    ranked: bool = True,
) -> list[RetrievalResult]:
    """Project rows into results, preserving order.

    An empty input produces an empty list. It is **not** padded, back-filled or
    widened to reach a target count: FR-009 makes an empty result set empty, and
    a short set short. The alternative reads as evidence and is not.

    `fused_rank` is the row's one-based position when the rows are ranked, and
    `None` when they are not — a deterministic route match has no fused rank,
    and giving it one would let it sort among ranked results as though it had
    been scored.
    """
    return [
        _from_chunk_row(
            row,
            match_kind=match_kind,
            fused_rank=(position if ranked else None),
        )
        for position, row in enumerate(rows, start=1)
    ]
