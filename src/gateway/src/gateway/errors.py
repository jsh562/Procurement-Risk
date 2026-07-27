"""Gateway-owned error types — the only exceptions this package raises.

TR-002 and TR-025. The hierarchy exists so a caller can distinguish *why* an
invocation failed without catching a provider SDK exception, which would couple
it to the provider as surely as accepting an SDK type would. `provider.py`
normalizes every SDK exception into one of these before it escapes.

The shape is deliberately wider than Phase 2 needs. Later phases raise on
validation failure, on a fail-closed record write, and on a replay miss, and
designing the hierarchy once is cheaper than reshaping it three times — a
caller that has already written ``except GatewayError`` keeps working as the
leaves arrive.

Nothing here imports the provider SDK, at module scope or under
``TYPE_CHECKING``: ``import-linter``'s ``exclude_type_checking_imports``
defaults to false, so a guarded import violates the contract just as a real one
does.
"""

from __future__ import annotations

__all__ = [
    "GatewayConfigError",
    "GatewayError",
    "GatewayValidationError",
    "ProviderError",
    "ProviderUnavailableError",
]


class GatewayError(Exception):
    """Base for every error this package raises.

    A caller that catches this catches everything the gateway can raise, and
    nothing the provider SDK can. That is the point: an SDK's exception types
    are as much a part of its public surface as its return types, and letting
    one escape would make every consumer depend on the provider.
    """


class GatewayConfigError(GatewayError):
    """Configuration is missing, malformed, or contradictory.

    Raised before any request is constructed, so it never costs a provider
    call. TR-065 constrains the message: it may name the configuration key at
    fault and never the value, because the values in question include a
    credential.
    """


class ProviderError(GatewayError):
    """A normalized provider failure.

    Carries only what TR-025 permits to cross the boundary — a status, an error
    type, and the provider's request identifier where it supplied one. The
    original SDK exception is deliberately **not** chained: TR-064 forbids
    retaining it as ``__cause__`` or ``__context__``, because a traceback
    renders the chained exception's arguments and a provider error body can
    echo request headers.
    """

    def __init__(
        self,
        message: str,
        *,
        status: int | None = None,
        error_type: str | None = None,
        request_id: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status = status
        self.error_type = error_type
        self.request_id = request_id


class GatewayValidationError(GatewayError):
    """The model produced no schema-valid value, and the repair budget is spent.

    TR-008. Raised after the *second* failure, never the first — the first is
    what the single repair attempt exists to answer. By the time this is
    raised the invocation record has already been written with outcome
    `failed`, which is the ordering TR-008 fixes: a caller that catches this
    can rely on the row existing, and a paid call is never left with no trace
    of itself.

    Carries the failing field paths rather than the model's output. The output
    is what failed validation, so returning it would be handing back the
    unvalidated value TR-006 forbids — through the error rather than through
    the return, which is the same value arriving by a quieter route.
    """

    def __init__(
        self,
        message: str,
        *,
        field_paths: tuple[str, ...] = (),
        repair_attempt_count: int = 0,
    ) -> None:
        super().__init__(message)
        self.field_paths = field_paths
        self.repair_attempt_count = repair_attempt_count


class ProviderUnavailableError(GatewayConfigError):
    """The provider SDK is not installed.

    Its own type rather than a bare ``ImportError`` so a caller can tell "you
    did not install the extra" from "the provider rejected the call". It
    inherits from the configuration error because that is what it is: the fault
    is in how the environment was resolved, not in anything the provider did,
    and it is detectable before a request is built. ADR-0014 accepts this as
    the cost of making the SDK optional — a consumer that omits
    ``gateway[provider]`` learns at first invocation rather than at dependency
    resolution.
    """
