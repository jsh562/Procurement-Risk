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

from typing import NoReturn, Protocol

from gateway import provider
from gateway.errors import GatewayError
from gateway.models import (
    InvocationRequest,
    InvocationResult,
    Outcome,
    generate_trace_id,
    is_valid_trace_id,
)
from gateway.validation import MAX_REPAIR_ATTEMPTS

__all__ = [
    "RecordWriter",
    "classify_outcome",
    "record_then_raise",
    "resolve_trace_id",
]


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
    # Phase 3 supplies the pieces this will compose — `validate_or_repair` for
    # the bounded repair, `with_transport_budget` for the attempt budget,
    # `classify_outcome` and `record_then_raise` below — but not the request
    # construction that joins them, which needs the mode branch above.
    #
    # Phase 4 (T034-T049): price the invocation from stored token counts, and
    # write exactly one record before returning or raising (TR-011, TR-036).
    raise NotImplementedError(
        "the invocation path lands across Phases 3-5; OBJ1 establishes the "
        "composition seam, the public surface, and the provider boundary. "
        f"Trace identifier resolution is live: {trace_id[:8]}..."
    )


def classify_outcome(*, reached_valid_value: bool, repair_attempt_count: int) -> Outcome:
    """The invocation's single outcome value (TR-009, TR-042, TR-078).

    **Total by construction.** TR-078 requires the mapping be total and
    exhaustive rather than stated as the negative rule "a transport failure is
    never `repaired`". Every reachable combination of the two inputs lands on
    exactly one of the three values, and the combination that the negative rule
    alone leaves unclassified — an invocation that consumed transport retries
    *and then* repaired successfully — is `repaired` here, because the repair is
    what produced the returned value.

    **The transport attempt count is not a parameter, and its absence is the
    point.** TR-078 says the classification holds "whatever the transport
    attempt count", so taking one would offer a knob that must never be turned.
    A transport failure cannot be `repaired` because it reaches no valid value
    at all, which makes `reached_valid_value` false — the rule falls out of the
    inputs rather than being enforced by a guard someone could later drop.

    **The unit is the invocation** (TR-042). One value per invocation, never one
    per attempt; nothing here returns a per-attempt classification and no caller
    can assemble one, because the attempt counts it would need are not inputs.

    Args:
        reached_valid_value: Whether a schema-valid value was obtained at all.
            False covers all three failure causes TR-078 lists — repair budget
            exhausted, transport budget exhausted, deadline expired — which are
            distinguished on the record by `error_type` (TR-064), not here.
        repair_attempt_count: 0 or 1. TR-007 bounds the repair budget at one, so
            a higher value means a caller has miscounted and is rejected rather
            than silently classified.

    Returns:
        `valid`, `repaired`, or `failed`.

    Raises:
        ValueError: `repair_attempt_count` is outside the budget TR-007 fixes.
    """
    if not 0 <= repair_attempt_count <= MAX_REPAIR_ATTEMPTS:
        raise ValueError(
            f"repair_attempt_count must be between 0 and {MAX_REPAIR_ATTEMPTS} "
            f"(TR-007); got {repair_attempt_count}"
        )
    if not reached_valid_value:
        return "failed"
    return "repaired" if repair_attempt_count > 0 else "valid"


class RecordWriter(Protocol):
    """Writes exactly one invocation record.

    A protocol rather than an import because the writer is Phase 4's, and
    because the ordering TR-008 fixes is testable now against any writer at all
    — which is the whole of what Phase 3 owes. A protocol also keeps the
    orchestrator from depending on the storage boundary in order to state the
    order in which it must be reached.
    """

    def __call__(self, *, trace_id: str, outcome: Outcome, error_type: str | None) -> None: ...


def record_then_raise(
    error: GatewayError,
    *,
    write: RecordWriter,
    trace_id: str,
    outcome: Outcome,
    error_type: str | None,
) -> NoReturn:
    """Write the invocation record, then raise (TR-008, TR-036).

    The order is the requirement, and it is the order a natural implementation
    gets wrong: raising where the failure happens and recording in an `except`
    further out reads fine and leaves the record unwritten on every path that
    re-raises before reaching it.

    A record-write failure does not swallow the original. The gateway fails
    closed either way — the caller receives an error and no value — but the
    error they receive is the one that describes what actually went wrong with
    their invocation, not the storage failure that followed it. The write
    failure is attached as `__notes__`, which a traceback renders and which
    carries no reference to the write's own exception object.

    Phase 4 replaces the bare re-raise on write failure with the spool append
    of TR-041; the ordering here does not change when it does, which is why the
    ordering is being fixed now rather than then.
    """
    write_failure: BaseException | None = None
    try:
        write(trace_id=trace_id, outcome=outcome, error_type=error_type)
    except Exception as exc:  # noqa: BLE001 - the original error must still win
        write_failure = exc

    if write_failure is not None:
        # The note carries the failure's text, not the exception. Attaching the
        # object would put it in reach of a `repr` on an error whose field set
        # TR-025 closes at three scalars.
        error.add_note(
            f"the invocation record could not be written: {write_failure!s}. "
            "Phase 4 (TR-041) appends to the local spool here instead of losing it."
        )
    raise error


def _provider_boundary_is_reachable() -> bool:
    """Whether the provider boundary is importable from here.

    Exists so the composition seam has a real edge to `gateway.provider` rather
    than an imagined one: the computation-boundary contract constrains the
    graph, and a graph edge that no code creates proves nothing about the
    arrangement the contract is meant to enforce.
    """
    return hasattr(provider, "load_client_class")
