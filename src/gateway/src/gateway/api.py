"""The gateway's public surface — the entry point both Python boundaries call.

TR-002. Everything nameable from here is gateway-owned: the request type, the
result type, and the error hierarchy. No provider SDK type is accepted,
returned, or re-exported, which is what lets a consumer be built and
type-checked in an environment where no provider package is installed at all
(ADR-0014, OBJ1 VC1).

**Exactly one invocation entry point** (TR-004, OBJ1 VC5). E001's `client_type`
placeholder existed to prove the provider import resolved before there was
anything to invoke; it is removed rather than left beside `invoke`, because two
ways in is the ambiguity the requirement names.

This module composes through `gateway.orchestrator` and never imports
`gateway.provider` itself. The manifest's public-entry contract enforces the
direct half of that; the type leak the contract cannot see — an SDK object
obtained by the orchestrator and re-exposed in a signature here — is what
`tests/test_public_surface.py` checks by name.
"""

from __future__ import annotations

from gateway import orchestrator
from gateway.errors import (
    GatewayConfigError,
    GatewayError,
    ProviderError,
    ProviderUnavailableError,
)
from gateway.models import (
    InvocationRequest,
    InvocationResult,
    Outcome,
    ResolutionMode,
    generate_trace_id,
)

__all__ = [
    "GatewayConfigError",
    "GatewayError",
    "InvocationRequest",
    "InvocationResult",
    "Outcome",
    "ProviderError",
    "ProviderUnavailableError",
    "ResolutionMode",
    "invoke",
    "new_trace_id",
]


def invoke(request: InvocationRequest) -> InvocationResult:
    """Run one traced invocation and return its validated result.

    The single entry point. Both Python boundaries reach the provider through
    this call and through no other, which is what makes "every model call is
    traced" a property of the code rather than of reviewer diligence.

    Args:
        request: What to ask, which model to ask, and — optionally — the trace
            identifier to record the invocation under. The identifier is an
            explicit field rather than ambient state (TR-080): the gateway
            reads no thread-local, no context variable and no inbound header,
            so where an identifier came from is visible at the call site. One
            is generated when the field is absent (TR-031).

    Returns:
        The validated result, carrying the invocation and trace identifiers so
        the caller can join what it received to the row the gateway wrote
        without querying for it. Only a validated value is ever returned
        (TR-006) — a failure raises instead of returning a result with an error
        field, so a caller cannot mistake one for the other by forgetting to
        check.

    Raises:
        GatewayError: Every failure this package can produce is one of these or
            a subclass. Deliberately not a provider SDK exception: an SDK's
            exception types are as much a part of its public surface as its
            return types, and letting one escape here would couple every
            consumer to the provider (TR-025).
    """
    return orchestrator.invoke(request)


def new_trace_id() -> str:
    """Mint a trace identifier a caller can supply on a later request.

    Exposed because TR-080 makes the identifier explicit: a caller that wants
    two invocations correlated needs some way to obtain a conforming identifier
    without either reimplementing TR-047's domain or importing an internal
    module. Without this, the explicit-field design would push callers into
    generating their own, and a caller-generated identifier that misses the
    domain is rejected at the boundary — a stricter API producing worse
    identifiers.

    Returns:
        Thirty-two lowercase hexadecimal characters, never all zero (TR-047).
    """
    return generate_trace_id()
