"""The deterministic part-number route: additive by construction.

Spec FR-010 to FR-014. A coordinator who types `NRH-80347` wants that item, and
neither arm of hybrid retrieval is good at finding it. The lexical arm tokenizes
it badly — `ts_rank` has no corpus-wide term statistics, so a rare designation
gets no more weight than a common word — and the dense arm embeds it into a
space where alphanumeric identifiers cluster with each other rather than with
the thing they name.

**The route never removes a result.** FR-012 makes it a union, not a filter, and
that is the whole design. A route that replaced hybrid retrieval would be a
lookup that silently loses the passage explaining the part; a route that ranks
alongside would let a deterministic match compete on a score it never earned.
Instead: matches are added, carry a null fused rank, and are counted outside
`limit`.

**Fall-through is not an error path.** FR-011: a recognised token that matches
nothing means hybrid retrieval answers alone. Returning empty because the route
found nothing would make a well-formed part number *worse* than a vague
question, which is the opposite of what recognising it is for.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any, Final

import psycopg

__all__ = [
    "PART_NUMBER_PATTERN",
    "RouteOutcome",
    "recognise_part_numbers",
    "resolve_part_numbers",
]

#: The declared part-number shape. Two to four uppercase letters, a hyphen, then
#: four to six digits — `NRH-80347`, `AC-1120`.
#:
#: Anchored on word boundaries rather than on the whole string, because FR-010
#: recognises a token **anywhere in the query**: "what is the lead time on
#: NRH-80347" is the question a coordinator actually types, and a whole-string
#: match would recognise the designation only when typed alone.
#:
#: Case-sensitive on the letters. The corpus prints designations uppercase, and
#: matching lowercase would recognise ordinary words in hyphenated compounds —
#: the pattern is narrow because a false positive here adds a result that was
#: never asked for and carries `deterministic_identifier` while doing it.
PART_NUMBER_PATTERN: Final = re.compile(r"\b[A-Z]{2,4}-\d{4,6}\b")


class RouteOutcome:
    """What the route did, so a response can say so rather than imply it."""

    __slots__ = ("added_chunk_ids", "matched_tokens", "recognised_tokens")

    def __init__(
        self,
        recognised_tokens: tuple[str, ...],
        matched_tokens: tuple[str, ...],
        added_chunk_ids: tuple[str, ...],
    ) -> None:
        self.recognised_tokens = recognised_tokens
        self.matched_tokens = matched_tokens
        self.added_chunk_ids = added_chunk_ids

    @property
    def fired(self) -> bool:
        """Whether any recognised token resolved to a chunk."""
        return bool(self.matched_tokens)

    @property
    def fell_through(self) -> bool:
        """Whether tokens were recognised but none resolved (FR-011).

        Distinct from "no token was recognised": both leave hybrid retrieval
        answering alone, but only this one means the query *looked* like a part
        number and the corpus does not hold it. A response that conflated them
        could not tell a coordinator their part is absent.
        """
        return bool(self.recognised_tokens) and not self.matched_tokens

    def as_dict(self) -> dict[str, Any]:
        return {
            "recognised_tokens": list(self.recognised_tokens),
            "matched_tokens": list(self.matched_tokens),
            "added_count": len(self.added_chunk_ids),
            "fell_through": self.fell_through,
        }


def recognise_part_numbers(query: str) -> tuple[str, ...]:
    """Every part-number token in `query`, in order, de-duplicated.

    Order is preserved so a response listing recognised tokens reads in the
    order they were typed; duplicates collapse because resolving the same
    designation twice would add the same chunk twice.
    """
    seen: dict[str, None] = {}
    for match in PART_NUMBER_PATTERN.finditer(query):
        seen.setdefault(match.group(0), None)
    return tuple(seen)


def resolve_part_numbers(
    connection: psycopg.Connection,
    tokens: Sequence[str],
    *,
    exclude_chunk_ids: Sequence[str] = (),
) -> RouteOutcome:
    """Resolve `tokens` to chunks by exact designation, excluding what fusion found.

    **The exclusion is what makes the union additive rather than duplicating.**
    A chunk fusion already returned keeps its fused rank and its
    `ranked_relevance` kind; adding it again as a deterministic match would put
    one chunk in the response twice, and the two copies would disagree about how
    it was found.

    Matched against `part_numbers` — the column E006 populates from designations
    extracted *as printed*. That column is NULL on every synthetic row today,
    which is the defect FR-005 publishes: on the corpus as it stands this route
    resolves nothing on the synthetic layer, and the fall-through is what keeps
    that from being a worse answer than not recognising the token at all.
    """
    if not tokens:
        return RouteOutcome((), (), ())
    excluded = set(exclude_chunk_ids)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT DISTINCT ON (t.token, c.chunk_id) t.token, c.chunk_id::text
            FROM unnest(%(tokens)s::text[]) AS t(token)
            JOIN chunk c
              ON c.part_numbers IS NOT NULL
             AND c.part_numbers ~ ('\\m' || t.token || '\\M')
            ORDER BY t.token, c.chunk_id
            """,
            {"tokens": list(tokens)},
        )
        rows = cursor.fetchall()
    matched = tuple(dict.fromkeys(str(row[0]) for row in rows))
    added = tuple(
        chunk_id
        for chunk_id in dict.fromkeys(str(row[1]) for row in rows)
        if chunk_id not in excluded
    )
    return RouteOutcome(tuple(tokens), matched, added)
