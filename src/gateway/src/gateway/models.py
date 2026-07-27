"""Gateway-owned request, result, and record types.

TR-002. Every value crossing the gateway's public surface is defined here, so a
consumer can be built and type-checked with no provider package installed. That
is what makes the provider-type-free claim testable rather than asserted: while
the SDK was a hard dependency, anything able to import the gateway also had it,
and the harness had nothing to assert (ADR-0014).

Nothing here may import the provider SDK, at module scope or under
``TYPE_CHECKING``: ``import-linter``'s ``exclude_type_checking_imports``
defaults to false, so a guarded import violates the contract just as a real one
does, and a leaked SDK type in an annotation couples every consumer to the
provider.
"""

from __future__ import annotations

import re
import uuid
from typing import Annotated, Final

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = [
    "TRACE_ID_PATTERN",
    "InvocationRequest",
    "InvocationResult",
    "Outcome",
    "ResolutionMode",
    "generate_trace_id",
    "is_valid_trace_id",
]

#: TR-047. Thirty-two lowercase hexadecimal characters. Matches the W3C Trace
#: Context trace-id and, conveniently, ``uuid4().hex`` — but the domain is
#: specified independently of how one is generated, because a caller may supply
#: an identifier this gateway did not mint.
TRACE_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{32}$")

#: The one value the pattern admits and the domain does not. W3C reserves
#: all-zero as "invalid", and a column full of zeroes would satisfy TR-031's
#: NOT NULL constraint while carrying no information at all — the failure the
#: constraint exists to prevent, wearing a valid-looking value.
_ALL_ZERO_TRACE_ID: Final[str] = "0" * 32


def is_valid_trace_id(value: str) -> bool:
    """Whether ``value`` is inside TR-047's domain.

    Exposed rather than inlined into the validator so the record writer and the
    checks can ask the same question of a stored value without restating the
    rule. Two copies of a domain rule is one copy too many.
    """
    return bool(TRACE_ID_PATTERN.match(value)) and value != _ALL_ZERO_TRACE_ID


def generate_trace_id() -> str:
    """Mint an identifier inside TR-047's domain.

    ``uuid4().hex`` is 32 lowercase hex by construction. The all-zero case is
    excluded explicitly rather than dismissed as improbable: TR-047 states the
    exclusion, so the generator honours it rather than relying on the odds.
    """
    while True:
        candidate = uuid.uuid4().hex
        if candidate != _ALL_ZERO_TRACE_ID:
            return candidate


type ResolutionMode = str
"""How an invocation was resolved: ``record`` reaches the provider, ``replay``
resolves from a committed fixture. Widened to a closed enumeration in Phase 5,
where mode selection is implemented (TR-021)."""

type Outcome = str
"""``valid``, ``repaired`` or ``failed`` — the classification of an invocation,
never of an attempt (TR-009, TR-042). Widened to a closed enumeration in Phase
3, where the terminal-state mapping is implemented (TR-078)."""


class InvocationRequest(BaseModel):
    """What a caller asks the gateway to do.

    ``trace_id`` is an **explicit optional field**, never ambient (TR-080). The
    gateway reads no thread-local, no context variable and no propagator: a
    caller that wants its request correlated says so in the request, and one
    that does not gets a generated identifier. Ambient propagation would make
    the identifier's provenance invisible at the call site, and it is the one
    field a reader most needs to trace back.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    prompt: str = Field(min_length=1)
    model: str | None = None
    trace_id: Annotated[str | None, Field(default=None)] = None

    @field_validator("trace_id")
    @classmethod
    def _trace_id_is_in_domain(cls, value: str | None) -> str | None:
        """TR-047: validate a caller-supplied identifier at the boundary.

        Before use, not at write time. A malformed identifier caught here costs
        nothing; caught at the storage boundary it costs a provider call whose
        record cannot be written, which is the billed-but-untraced case
        ADR-0015 exists to prevent.
        """
        if value is None:
            return None
        if not is_valid_trace_id(value):
            raise ValueError(
                "trace_id must be 32 lowercase hexadecimal characters and not all zero; "
                f"got {len(value)} characters"
            )
        return value


class InvocationResult(BaseModel):
    """What the gateway returns when an invocation succeeds.

    Only a validated value reaches a caller (TR-006). A failure raises rather
    than returning a result carrying an error, so a caller cannot mistake one
    for the other by forgetting to check a field.

    ``invocation_id`` and ``trace_id`` are here so the caller can join what it
    received to the row the gateway wrote, without querying for it.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    invocation_id: str
    trace_id: str
    content: str
    outcome: Outcome
    resolution_mode: ResolutionMode
