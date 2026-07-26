"""TR-008: the single permitted provider import site.

The most architecturally load-bearing module in the repository had no test at
all — its correctness rested on import-linter happening to import it while
building a graph, which catches a syntax error and nothing else.

This file deliberately never names the provider distribution. An earlier draft
did, and the TR-010 source scan failed it: naming the client here would make
this the second file in the repository to do so, which is exactly the property
the scan exists to prevent. Consumers reach the client through this module's
surface, so the test does too — the constraint improved the test.
"""

from __future__ import annotations

import gateway.provider as provider


def test_the_module_exposes_a_client_class() -> None:
    client = provider.client_type()
    assert isinstance(client, type), f"expected a class, got {client!r}"


def test_the_client_comes_from_a_module_this_boundary_imported() -> None:
    """Membership, not a literal comparison.

    Even an attribute access spelling the distribution out would make this the
    second file naming it. Checking that the returned class's top-level module
    appears in this module's own namespace is both name-free and a stronger
    claim: it fails if `client_type` ever starts returning something the
    gateway did not import.
    """
    client = provider.client_type()
    top_level = client.__module__.split(".")[0]
    assert top_level in vars(provider), (
        f"client_type returned a class from {top_level!r}, which this boundary never imported"
    )


def test_client_type_does_not_construct_a_client() -> None:
    """Constructing one reads credentials from the environment.

    E001 supplies none and TR-025 forbids introducing any, so returning the
    type rather than an instance is deliberate. This pins that behaviour so a
    later refactor cannot quietly turn it into a constructor call.
    """
    result = provider.client_type()
    assert isinstance(result, type), "client_type returned an instance, not a type"


def test_the_default_model_is_pinned_at_the_boundary() -> None:
    """Pinned here rather than at each call site, so the model in use is a
    property of the boundary and readable without grepping callers."""
    assert provider.DEFAULT_MODEL == "claude-opus-5"


def test_the_module_exports_a_stable_surface() -> None:
    """E004 builds tracing, schema validation, and cost accounting on this
    surface; a silent rename would break both consuming boundaries."""
    assert set(provider.__all__) == {"DEFAULT_MODEL", "client_type"}
