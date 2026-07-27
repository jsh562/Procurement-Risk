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

import gateway.provider as provider
from gateway.errors import GatewayConfigError, GatewayError, ProviderUnavailableError


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
    assert set(provider.__all__) == {"DEFAULT_MODEL", "ProviderClient", "load_client_class"}
