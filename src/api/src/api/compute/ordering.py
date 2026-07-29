"""The offered sort keys, and the digest over a ranking's order.

FR-012, FR-026, FR-046.

Two things live here because both are properties of an *ordering* rather than
of any line in it.

**The offered keys are enumerated by the server**, so FR-032's assertion that
they are exactly FR-026's four is testable against a response rather than
against a component's source. Each carries one fixed direction: a key whose
direction the caller chose would let `expected_harm ascending` order the safest
line first and call it a worklist.

**The digest answers "did the order change?" in one comparison.** FR-012
requires an adjustment that changes no ordering to be acknowledged as applied
with the order unchanged, and FR-046 the converse. Without a value the server
publishes, the interface re-derives an equality the server already knows — and
two consumers would each derive it slightly differently.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from hashlib import sha256
from typing import Final
from uuid import UUID

__all__ = [
    "DEFAULT_SORT_KEY",
    "EMPTY_ORDERING_DIGEST",
    "SORT_DIRECTIONS",
    "SORT_KEYS",
    "TIEBREAK",
    "SortOption",
    "ordering_digest",
    "sort_options",
]

#: FR-026's four keys and no others. `criticality` and `calendar_margin` are
#: FR-009 explanatory context admitted by FR-027's carve-out, because both are
#: inputs a coordinator may reasonably triage by. Notably absent: any key that
#: would order lines by a single delivery date or one quantile alone — that is
#: the point estimate re-entering through the sort control, which FR-026 names
#: directly.
SORT_KEYS: Final[tuple[str, ...]] = (
    "expected_harm",
    "need_by_date",
    "criticality",
    "calendar_margin",
)

#: One fixed direction per key, so "worst first" cannot be inverted into
#: "safest first" by a query parameter.
SORT_DIRECTIONS: Final[dict[str, str]] = {
    "expected_harm": "desc",
    "need_by_date": "asc",
    "criticality": "desc",
    "calendar_margin": "asc",
}

DEFAULT_SORT_KEY: Final[str] = "expected_harm"

#: FR-013a. Applied in order after the active key, and total by construction:
#: `po_line_id` is unique, so no two lines can tie through the whole sequence
#: and the ordering is deterministic across reloads (FR-010).
TIEBREAK: Final[tuple[str, ...]] = (
    "need_by_date asc",
    "criticality desc",
    "po_line_id asc",
)

#: FR-012's stated domain. The digest of the empty sequence, which is what a
#: response ranking nothing carries. Two such responses are digest-identical
#: however much else differs between them, so equality of this value alone MUST
#: NOT be read as sameness of state — the state travels in `page_states`.
EMPTY_ORDERING_DIGEST: Final[str] = "sha256:" + sha256(b"").hexdigest()


@dataclass(frozen=True)
class SortOption:
    """One offered key, its fixed direction, and whether it is default or active."""

    key: str
    direction: str
    is_default: bool
    is_active: bool


def sort_options(active_key: str) -> tuple[SortOption, ...]:
    """Every offered key, marked against ``active_key``."""
    if active_key not in SORT_DIRECTIONS:
        raise ValueError(
            f"{active_key!r} is not one of FR-026's four sort keys: {', '.join(SORT_KEYS)}"
        )
    return tuple(
        SortOption(
            key=key,
            direction=SORT_DIRECTIONS[key],
            is_default=key == DEFAULT_SORT_KEY,
            is_active=key == active_key,
        )
        for key in SORT_KEYS
    )


def ordering_digest(po_line_ids: Iterable[UUID] | Sequence[UUID]) -> str:
    """Digest the ranked group's ordered identifier sequence.

    The separator is not decorative. Joining bare hex would let two different
    orderings of differently-split identifiers collide, and a digest that can
    collide answers "did the order change?" with a confident no.
    """
    joined = "\n".join(str(po_line_id) for po_line_id in po_line_ids)
    return "sha256:" + sha256(joined.encode("utf-8")).hexdigest()
