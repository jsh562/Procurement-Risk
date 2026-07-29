"""When the worklist could not be read at all.

FR-043. Three conditions, and none of them is a ninth degraded state:

- the datastore is unreachable;
- the request fails before a worklist can be assembled;
- the active run's artifact schema version is one this build does not read.

FR-018's eight are states of what the system *knows* — each reached by a
successful read, each carried inside a `200`. These three are the system saying
it could not look. Rendering them alike presents an outage as an honest empty
state, which is the same defect in the opposite direction to a missing figure
rendered as a zero.

**The schema-version refusal is deliberate.** A reader meeting an unfamiliar
artifact schema version fails loudly rather than misreading array offsets — and
a misread offset yields a figure wrong in a way no coordinator could see. It is
reported with its own cause rather than as a generic fault, because "this run
was written by a schema this build does not know" names what would change it and
a bare failure does not.

Every failure carries a **correlation identifier**, so the thing a coordinator
can quote off the screen is the thing an engineer can find in the logs. Without
it the report is "the worklist was broken this morning", and the record it
correlates to has to be found by timestamp.
"""

from __future__ import annotations

import secrets
from typing import Any, Final

__all__ = [
    "PROBLEM_BASE",
    "SUPPORTED_ARTIFACT_SCHEMA_VERSIONS",
    "UnsupportedArtifactSchema",
    "correlation_id",
    "problem",
]

PROBLEM_BASE: Final[str] = "https://procurement-risk.local/problems"

#: The artifact schema versions this build's array-offset reasoning is written
#: against. Widening this set is a decision about whether the offsets still mean
#: the same thing, not a configuration change — which is why it lives in code
#: beside the reader rather than in an environment variable.
SUPPORTED_ARTIFACT_SCHEMA_VERSIONS: Final[frozenset[int]] = frozenset({1})


class UnsupportedArtifactSchema(Exception):
    """The active run was written by a schema version this build does not read."""

    def __init__(self, run_id: str, found: int) -> None:
        self.run_id = run_id
        self.found = found
        supported = ", ".join(str(item) for item in sorted(SUPPORTED_ARTIFACT_SCHEMA_VERSIONS))
        super().__init__(
            f"Active run {run_id} carries artifact_schema_version {found}; "
            f"this build reads {supported}."
        )


def correlation_id() -> str:
    """A short opaque identifier for one failure.

    Random rather than derived from the request, because two identical requests
    failing an hour apart are two incidents and must not share an identifier —
    the whole point is to find *this* occurrence in the record.
    """
    return secrets.token_hex(13).upper()


def problem(
    *,
    kind: str,
    title: str,
    detail: str,
    correlation: str,
    **extra: Any,
) -> dict[str, Any]:
    """An RFC 9457 problem document.

    ``type`` is a URI naming the *kind* of failure so a client can branch on it
    without parsing prose; ``detail`` is the sentence a human reads. Both are
    present because a client that branched on the sentence would break the first
    time the wording improved.
    """
    return {
        "type": f"{PROBLEM_BASE}/{kind}",
        "title": title,
        "detail": detail,
        "correlation_id": correlation,
        **extra,
    }
