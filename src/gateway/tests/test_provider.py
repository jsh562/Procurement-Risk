"""TR-001 / TR-003 / TR-004: the single permitted provider import site.

Rewritten by T009. E001's version tested `client_type()`, which TR-004 removes —
the placeholder existed to prove the import resolved before there was anything
to invoke, and leaving it beside the real entry point would be the "second
surface" that requirement forbids.

**This file deliberately never names the provider distribution.** E001's
version carried the same constraint and the reason still holds: the TR-001
source scan reads all of `/src`, tests included, and asserts exactly one file
names the client. Naming it here would make this the second. Consumers reach
the client through this module's surface, so the test does too — the constraint
improved the test then and still does.
"""

from __future__ import annotations

import pytest
from pydantic import BaseModel

import gateway.provider as provider
from gateway.errors import (
    GatewayConfigError,
    GatewayError,
    ProviderError,
    ProviderUnavailableError,
)
from gateway.provider import with_transport_budget


def test_the_placeholder_accessor_is_gone() -> None:
    """TR-004. The seam E001 left is replaced, not accompanied.

    Asserted on the module rather than on `__all__`, because an attribute that
    is merely undeclared is still importable and still a second surface.
    """
    assert not hasattr(provider, "client_type"), (
        "client_type still exists; TR-004 requires the placeholder be removed "
        "rather than left beside the invocation entry point"
    )


def test_the_module_loads_a_client_class() -> None:
    client = provider.load_client_class()
    assert isinstance(client, type), f"expected a class, got {client!r}"


def test_the_client_comes_from_the_distribution_this_boundary_declares() -> None:
    """Membership, not a literal comparison.

    Even an attribute access spelling the distribution out would make this the
    second file naming it. Comparing against the name the module itself records
    is both name-free here and a stronger claim: it fails if `load_client_class`
    ever starts returning something the gateway did not import.
    """
    client = provider.load_client_class()
    top_level = client.__module__.split(".")[0]
    assert top_level == provider._PROVIDER_DISTRIBUTION, (
        f"load_client_class returned a class from {top_level!r}, which is not the "
        f"distribution this boundary declares it imports"
    )


def test_the_import_is_not_performed_at_module_scope() -> None:
    """TR-003. The property ADR-0014 turns from a claim into a test.

    Read off the module's own namespace: a module-scope import binds the name
    there, a function-local one does not. Without this the lazy import could
    regress to module scope and every other test in this file would still pass,
    because they all call the function that performs it.
    """
    assert provider._PROVIDER_DISTRIBUTION not in set(vars(provider)), (
        "the provider SDK is bound at module scope; TR-003 requires the import "
        "to happen inside the invocation entry so the package imports without "
        "the `provider` extra installed"
    )


def test_loading_the_client_does_not_construct_one() -> None:
    """Constructing one reads a credential from the environment.

    The offline suite runs with none present (TR-023), so a boundary that
    constructed eagerly would be untestable there — and would hold a credential
    for longer than the one call that needs it.
    """
    result = provider.load_client_class()
    assert isinstance(result, type), "load_client_class returned an instance, not a type"


def test_the_missing_extra_error_is_gateway_owned() -> None:
    """ADR-0014's accepted cost, typed so a caller can act on it.

    `ProviderUnavailableError` is a configuration error, not a provider
    failure: the fault is in how the environment was resolved, and it is
    detectable before a request is built. A caller catching `GatewayError`
    catches it; one catching `ImportError` does not, which is deliberate —
    an SDK-shaped failure crossing this boundary is the coupling the boundary
    exists to prevent.
    """
    assert issubclass(ProviderUnavailableError, GatewayConfigError)
    assert issubclass(ProviderUnavailableError, GatewayError)
    assert not issubclass(ProviderUnavailableError, ImportError)


def test_the_default_model_is_pinned_at_the_boundary() -> None:
    """Pinned here rather than at each call site, so the model in use is a
    property of the boundary and readable without grepping callers."""
    assert provider.DEFAULT_MODEL == "claude-opus-5"


def test_the_module_exports_a_stable_surface() -> None:
    """`__all__` is the contract consumers read. TR-004 changes it, so it is
    pinned rather than left to drift with whatever happens to be defined."""
    assert set(provider.__all__) == {
        "CredentialHandle",
        "DEFAULT_MODEL",
        "MAX_TRANSPORT_ATTEMPTS",
        "MAX_TRANSPORT_RETRIES",
        "ProviderClient",
        "RemainingTime",
        "load_client_class",
        "native_output_schema",
        "with_transport_budget",
    }


# --- T025 / TR-010 / TR-034: the transport budget inside the deadline --------
#
# Every test below drives `with_transport_budget` with injected callables and no
# client at all. That is not a shortcut around the real thing — it is the
# arrangement TR-032 forces and TR-027 requires: the retry loop holds no clock
# and no SDK, so its budget is decidable without a credential, a network, or a
# fixture, and a test of it is a test of the rule rather than of the provider.


class _Retryable(Exception):
    """Stands in for a rate-limit or server error, without naming an SDK type."""


class _Fatal(Exception):
    """Stands in for a malformed-request error — real, and not worth retrying."""


def _always(_: BaseException) -> bool:
    return True


def _only_retryable(exc: BaseException) -> bool:
    return isinstance(exc, _Retryable)


def _seconds(*values: float) -> provider.RemainingTime:
    """A deadline that reports each value in turn, then repeats the last.

    Repeating rather than exhausting: a budget that ran out of clock readings
    would raise `StopIteration` and be indistinguishable from the loop
    finishing, which is the outcome under test.
    """
    remaining = list(values)

    def read() -> float:
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return read


def test_a_first_attempt_that_succeeds_issues_exactly_one_request() -> None:
    """TR-010 states this as its own sentence, so it gets its own test.

    A loop that warmed up, probed, or retried speculatively would satisfy the
    ceiling of three while violating this.
    """
    calls: list[float] = []

    def attempt(timeout: float) -> str:
        calls.append(timeout)
        return "ok"

    value, attempts = with_transport_budget(attempt, _seconds(120.0), retryable=_always)
    assert value == "ok"
    assert attempts == 1
    assert len(calls) == 1, f"{len(calls)} requests issued for a first-attempt success"


def test_each_attempt_is_told_the_remaining_deadline() -> None:
    """TR-034 forbids delegating the deadline to the SDK's own default, and the
    way to not delegate it is to state it on every call — including the retries,
    which must inherit what is *left* rather than restart the clock."""
    seen: list[float] = []

    def attempt(timeout: float) -> str:
        seen.append(timeout)
        raise _Retryable

    with pytest.raises(ProviderError):
        with_transport_budget(attempt, _seconds(120.0, 80.0, 40.0), retryable=_always)

    assert seen == [120.0, 80.0, 40.0], f"attempts did not inherit the remaining deadline: {seen}"


def test_the_budget_is_two_retries_and_three_attempts() -> None:
    """TR-010's ceiling."""
    calls = 0

    def attempt(_: float) -> str:
        nonlocal calls
        calls += 1
        raise _Retryable

    with pytest.raises(ProviderError) as raised:
        with_transport_budget(attempt, _seconds(120.0), retryable=_always)

    assert calls == provider.MAX_TRANSPORT_ATTEMPTS == 3
    assert calls == provider.MAX_TRANSPORT_RETRIES + 1
    assert "3 transport attempt" in str(raised.value)


def test_a_non_retryable_failure_spends_one_attempt_and_stops() -> None:
    """Retrying a malformed request produces three identical rejections and
    three charges' worth of latency for one answer that was never going to
    change."""
    calls = 0

    def attempt(_: float) -> str:
        nonlocal calls
        calls += 1
        raise _Fatal

    with pytest.raises(ProviderError):
        with_transport_budget(attempt, _seconds(120.0), retryable=_only_retryable)

    assert calls == 1, f"a non-retryable failure was retried {calls} times"


def test_an_expired_deadline_is_a_transport_failure() -> None:
    """TR-034: expiry counts against TR-010's budget rather than forming its own
    category, so it surfaces as a provider error and never as `repaired`."""
    calls = 0

    def attempt(_: float) -> str:
        nonlocal calls
        calls += 1
        return "unreachable"

    with pytest.raises(ProviderError) as raised:
        with_transport_budget(attempt, _seconds(0.0), retryable=_always)

    assert calls == 0, "a request was issued after the deadline had already passed"
    assert raised.value.error_type == "deadline_exceeded"


def test_the_deadline_is_checked_before_each_retry_not_only_the_first() -> None:
    """The case that matters: time remains for the first attempt and is gone by
    the second. Checking only once would spend the whole budget past the
    deadline the configuration set."""
    calls = 0

    def attempt(_: float) -> str:
        nonlocal calls
        calls += 1
        raise _Retryable

    with pytest.raises(ProviderError) as raised:
        with_transport_budget(attempt, _seconds(10.0, -1.0), retryable=_always)

    assert calls == 1
    assert raised.value.error_type == "deadline_exceeded"
    assert "after 1 transport attempt" in str(raised.value)


def test_no_sdk_exception_escapes_the_boundary() -> None:
    """TR-025. What the caller catches is the gateway's error, and what it
    carries is three scalars."""

    class _SdkShaped(Exception):
        status_code = 429
        request_id = "req_abc123"

    def attempt(_: float) -> str:
        raise _SdkShaped

    with pytest.raises(ProviderError) as raised:
        with_transport_budget(attempt, _seconds(120.0), retryable=_only_retryable)

    assert not isinstance(raised.value, _SdkShaped)
    assert raised.value.status == 429
    assert raised.value.request_id == "req_abc123"
    assert raised.value.error_type == "transport_failed"


def test_the_original_exception_is_neither_chained_nor_contexted() -> None:
    """TR-064, and the property `raise ... from None` alone does not give.

    A provider error body can echo request headers, and a chained exception is
    rendered by the traceback the normalization exists to keep clean — so
    `__context__` matters as much as `__cause__`.
    """

    class _Echoing(Exception):
        pass

    def attempt(_: float) -> str:
        raise _Echoing("body echoing a credential header")

    with pytest.raises(ProviderError) as raised:
        with_transport_budget(attempt, _seconds(120.0), retryable=_only_retryable)

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None, (
        "the provider exception survives as __context__; TR-064 forbids retaining "
        "it as an implicit context as much as an explicit cause"
    )


def test_an_already_normalized_error_is_not_wrapped_again() -> None:
    """Two layers of normalization would report "failed after 3 attempts" over
    an error that already said what happened, and would count one failure
    twice."""
    inner = ProviderError("inner", status=400, error_type="transport_failed")

    def attempt(_: float) -> str:
        raise inner

    with pytest.raises(ProviderError) as raised:
        with_transport_budget(attempt, _seconds(120.0), retryable=_always)

    assert raised.value is inner


def test_the_native_output_transform_returns_a_schema() -> None:
    """TR-005's submission half. Asserted on shape rather than on contents —
    the contents are the SDK's business, and `test_validation_repair.py` is
    where what it drops is pinned."""

    class _Shape(BaseModel):
        name: str

    transformed = provider.native_output_schema(_Shape)
    assert transformed is not None
    assert transformed.get("type") == "object"
