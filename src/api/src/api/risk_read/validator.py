"""The response validator.

FR-020a, SC-027.

FR-020a claims a row's figures do not change except by a new active run, a
coordinator action, or the advance of the day boundary. That claim is over
*unobserved time* and no single response can falsify it — so the requirement
states the verification means rather than leaving it to inspection: every
response carries a validator computed over exactly the admitted inputs and
nothing else.

"Exactly, and nothing else" is what makes it evidence. A validator over the
whole serialised body would change whenever anything changed and prove nothing
about which inputs are permitted to move it. A validator over too little would
return `304` for a response that genuinely differs.

**The line set participates.** FR-020a's subject is a row's figures; this
response's subject is a *set* of rows. Without the set, a line opened since the
last request, one FR-022 has since made terminal, or one whose criticality moved
changes the worklist while every surviving row's figure stands still — and a
`304` would then withhold a response that differs. An unchanged validator is a
positive statement that the whole response is unchanged.

**`generated_at` deliberately takes no part.** It records when the response was
produced rather than what it contains. A validator moving with the clock would
report a change on every request and assert nothing at all.
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from datetime import date
from hashlib import sha256
from typing import Any, Final

from api.risk_read.query import OpenLine

__all__ = ["ADMITTED_INPUTS", "CONTRACT_VERSION", "compute_validator"]

#: This document's revision. Part of the validator because a contract change can
#: alter the response's shape without altering any input to it, and a `304`
#: served across that change would hand a client a body in the old shape.
CONTRACT_VERSION: Final[str] = "1.0.0"

#: FR-020a's admitted set, named so a reader can check the implementation
#: against the requirement without reconstructing it from the hash input.
ADMITTED_INPUTS: Final[tuple[str, ...]] = (
    "active run identity, or its absence",
    "today, in the configured time zone",
    "the project scope",
    "the sort key",
    "the applied override set",
    "the open-line set: identity, recorded need-by date, criticality, "
    "lifecycle state and roster hash of every line reported",
    "the contract version",
)


def _line_state(line: OpenLine) -> list[Any]:
    """Every stored field of a line the response derives from.

    Not the posterior: the draws and the survival array cannot change without
    the run changing, and the run's identity is already in the validator. Adding
    them would make the validator expensive to compute and no more sensitive.
    """
    return [
        str(line.po_line_id),
        line.project_id,
        line.po_number,
        line.line_number,
        line.need_by_date.isoformat(),
        line.criticality,
        line.lifecycle_state,
        line.roster_hash,
    ]


def compute_validator(
    *,
    run_id: str | None,
    today: date,
    project_id: str | None,
    sort_key: str,
    overrides: dict[str, str] | None,
    lines: Iterable[OpenLine],
) -> str:
    """A weak validator over exactly FR-020a's admitted inputs.

    Returns:
        The ``ETag`` value, weak-prefixed. Weak because two byte-different
        responses with the same inputs are semantically equivalent — the
        `generated_at` timestamp differs and nothing else does, which is
        precisely what a weak validator means.
    """
    payload = {
        "contract_version": CONTRACT_VERSION,
        "run_id": run_id,
        "today": today.isoformat(),
        "project_id": project_id,
        "sort_key": sort_key,
        "overrides": dict(sorted((overrides or {}).items())),
        # Sorted, so the validator is a function of the line *set* and not of
        # the order a query happened to return it in. An unsorted list would
        # make the ETag change when nothing about the data did.
        "lines": sorted(_line_state(line) for line in lines),
    }
    digest = sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
    ).hexdigest()
    return f'W/"sha256:{digest}"'
