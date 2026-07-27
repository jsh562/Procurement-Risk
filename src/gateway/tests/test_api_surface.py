"""TR-002 / TR-004 / OBJ1 VC5: the public surface, enumerated.

`test_public_surface.py` asks whether the surface leaks a foreign *type*. This
file asks a different question — what is *on* the surface at all — and OBJ1 VC5
states it precisely: "E001's placeholder client-type accessor is absent and
exactly one invocation entry point is present."

Both halves need asserting, and the second is the one that would rot quietly.
Removing `client_type` is a single visible edit; growing a second way to invoke
happens one convenience wrapper at a time, each defensible on its own, until
"every model call goes through the gateway" is true of one path and not the
others.
"""

from __future__ import annotations

import inspect

from gateway import api, provider

#: The exported callables that take a request and run an invocation. Held as a
#: set rather than a count so a failure names what it found rather than
#: reporting that two is not one.
INVOCATION_ENTRY_POINTS = frozenset({"invoke"})

#: E001's seam. It proved the provider import resolved before there was
#: anything to invoke; TR-004 removes it rather than leaving it beside `invoke`.
PLACEHOLDER_ACCESSOR = "client_type"


def _exported_callables() -> dict[str, object]:
    return {
        name: obj
        for name in api.__all__
        if callable(obj := getattr(api, name)) and not isinstance(obj, type)
    }


def test_the_placeholder_accessor_is_absent_from_the_public_surface() -> None:
    """OBJ1 VC5, first half.

    Asserted with `hasattr` rather than against `__all__`: an attribute that is
    merely undeclared is still importable and still a second way in, so
    dropping it from `__all__` would satisfy a weaker version of this test
    while leaving the accessor exactly where it was.
    """
    assert not hasattr(api, PLACEHOLDER_ACCESSOR), (
        f"gateway.api still exposes {PLACEHOLDER_ACCESSOR!r}; TR-004 requires the "
        f"placeholder be replaced by the invocation entry point, not accompanied by it"
    )
    assert not hasattr(provider, PLACEHOLDER_ACCESSOR), (
        f"gateway.provider still exposes {PLACEHOLDER_ACCESSOR!r}; removing it from "
        f"the public module while leaving it on the wrapper moves the seam rather "
        f"than closing it"
    )


def test_exactly_one_invocation_entry_point_is_present() -> None:
    """OBJ1 VC5, second half.

    An invocation entry point is identified by what it *consumes* — the
    gateway-owned request type — rather than by its name. Matching on a name
    would let a second entry point escape by being called something else, which
    is exactly how a second one would arrive.
    """
    entry_points = {
        name
        for name, obj in _exported_callables().items()
        if any(
            parameter.annotation in {"InvocationRequest", api.InvocationRequest}
            for parameter in inspect.signature(obj).parameters.values()
        )
    }
    assert entry_points == INVOCATION_ENTRY_POINTS, (
        f"the public surface offers {sorted(entry_points)} as invocation entry points, "
        f"expected exactly {sorted(INVOCATION_ENTRY_POINTS)}. Every traced call in "
        f"every epic reaches the provider through one of these; a second one is a "
        f"second answer to 'is this call traced?'"
    )


def test_the_entry_point_returns_the_gateway_owned_result_type() -> None:
    """Only a validated, gateway-owned value reaches a caller (TR-006, TR-002).

    Checked on the annotation rather than by calling: the return type is part
    of the contract a consumer type-checks against, and TR-002's claim is about
    what a consumer can *name*, not only about what arrives at runtime.
    """
    returns = inspect.signature(api.invoke).return_annotation
    assert returns in {"InvocationResult", api.InvocationResult}, (
        f"gateway.api.invoke returns {returns!r}, not the gateway-owned result type"
    )


def test_every_exported_name_resolves() -> None:
    """`__all__` is what `from gateway.api import *` reads and what a reader
    treats as the surface. An entry naming something that does not exist turns
    both into a lie, and nothing else in this suite would notice."""
    missing = [name for name in api.__all__ if not hasattr(api, name)]
    assert not missing, f"gateway.api.__all__ names attributes that do not exist: {missing}"


def test_the_surface_carries_the_error_hierarchy() -> None:
    """A caller that cannot name the errors cannot handle them without reaching
    into an internal module — and reaching in is how internals become surface.

    `GatewayError` specifically: it is the one name a caller needs to catch
    everything this package raises and nothing the provider SDK raises, which
    is the property TR-025 exists to give them.
    """
    assert "GatewayError" in api.__all__, (
        "gateway.api does not export GatewayError; a caller would have to import "
        "gateway.errors directly to write a correct except clause"
    )
