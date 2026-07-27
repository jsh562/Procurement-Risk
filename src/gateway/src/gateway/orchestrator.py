"""Composes the call, the validation, the computation and the record.

TR-032. This module exists so that `gateway.provider` never has to reach
`gateway.compute`. The computation-boundary contract forbids that edge with
indirect detection on, and a boundary with nothing above it would have no way
to satisfy the rule except by putting the arithmetic where it must not go —
so the seam is the enforcement, not decoration around it.

Phase 2 lands the composition and the pieces OBJ1 owns. Validation with bounded
repair (Phase 3), the pure arithmetic (Phase 4), fixture resolution (Phase 5)
and the record write (Phase 4) attach at the points marked below. Each is a
named step rather than an inline call so the later phase extends a seam instead
of restructuring this function.

TR-075: the invocation record is the only telemetry this epic emits. Nothing
here starts a span, increments a metric, or installs an exporter or a context
propagator, and the package takes no dependency on an OpenTelemetry SDK. The
field *names* follow the OpenTelemetry generative-AI conventions so the record
stays exportable later (TR-013), which is a naming choice and not an
integration.
"""

from __future__ import annotations

from gateway import provider
from gateway.models import (
    InvocationRequest,
    InvocationResult,
    generate_trace_id,
    is_valid_trace_id,
)

__all__ = ["resolve_trace_id"]


def resolve_trace_id(request: InvocationRequest) -> str:
    """The identifier this invocation will be recorded under.

    TR-031: a caller-supplied identifier is used as given, and one is generated
    when the caller supplies none, so a record is never untraceable to the
    request that caused it.

    The revalidation here is not redundant with `InvocationRequest`'s field
    validator. That validator guards the *construction* path; this guards the
    *use* path, and they are reachable independently — `model_construct`
    bypasses validation entirely, and a later phase may rebuild a request from
    a stored payload. TR-047 places the domain check at the boundary before
    use, and this is that boundary.
    """
    supplied = request.trace_id
    if supplied is None:
        return generate_trace_id()
    if not is_valid_trace_id(supplied):
        raise ValueError(
            "trace_id must be 32 lowercase hexadecimal characters and not all zero"
        )
    return supplied


def invoke(request: InvocationRequest) -> InvocationResult:
    """Run one invocation end to end.

    "One invocation" is the unit throughout: exactly one record per invocation,
    never one per attempt (TR-011), with token counts summed and latency
    measured across every attempt it contains (TR-040).

    The steps below are ordered as they are because each later phase's failure
    mode depends on the earlier one having happened. Resolving the trace
    identifier first means even the earliest failure has something to be
    recorded under.
    """
    trace_id = resolve_trace_id(request)

    # Phase 5 (T051-T056): resolve from the fixture store in `replay` mode, or
    # reach the provider in `record` mode. Mode selection has no default and no
    # fallback (TR-021), so this becomes a branch rather than a conditional
    # around a single path.
    #
    # Phase 3 (T019-T023): submit the schema, validate, and repair at most once
    # before failing closed (TR-005 through TR-008).
    #
    # Phase 4 (T034-T049): price the invocation from stored token counts, and
    # write exactly one record before returning or raising (TR-011, TR-036).
    raise NotImplementedError(
        "the invocation path lands across Phases 3-5; OBJ1 establishes the "
        "composition seam, the public surface, and the provider boundary. "
        f"Trace identifier resolution is live: {trace_id[:8]}..."
    )


def _provider_boundary_is_reachable() -> bool:
    """Whether the provider boundary is importable from here.

    Exists so the composition seam has a real edge to `gateway.provider` rather
    than an imagined one: the computation-boundary contract constrains the
    graph, and a graph edge that no code creates proves nothing about the
    arrangement the contract is meant to enforce.
    """
    return hasattr(provider, "load_client_class")
