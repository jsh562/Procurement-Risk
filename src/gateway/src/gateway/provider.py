"""The single module in this repository permitted to import the model provider.

Every traced call in every later epic reaches the provider through here. The
``protected`` import-linter contract in this package's ``pyproject.toml`` names
the provider distribution protected and this module its only allowed importer,
so a second import site fails the build rather than passing review.

Two properties this module exists to hold, both of which are asserted elsewhere
rather than merely intended here:

**The import is lazy** (TR-003, ADR-0014). The provider SDK is an optional
extra, and the import happens inside the call below rather than at module
scope, so the package imports and type-checks in an environment resolved
without it. A module-scope import would defeat that — and so would a
``TYPE_CHECKING``-guarded one, since ``exclude_type_checking_imports`` defaults
to false and the contract counts it.

**No SDK type escapes** (TR-002). The client is typed against `ProviderClient`,
a protocol defined here, not against the SDK's own class. Nothing this module
returns carries an SDK type into a signature a consumer can see.

This module also does no arithmetic. Cost, content hashing and duration live in
``gateway.compute``, which the computation-boundary contract forbids this
module from reaching (TR-032) — the orchestration module above both composes
them instead.
"""

from __future__ import annotations

from typing import Any, Final, Protocol, runtime_checkable

from gateway.errors import ProviderUnavailableError

# Pinned here rather than at each call site so the model in use is a property
# of the boundary, readable without grepping the callers.
DEFAULT_MODEL: Final[str] = "claude-opus-5"

#: The distribution that provides the client. Held as a name so this module can
#: report a useful install hint without a second file in the repository
#: spelling it out — `tests/checks/test_single_import_site.py` scans all of
#: `/src`, tests included, and asserts exactly one file names it.
_PROVIDER_DISTRIBUTION: Final[str] = "anthropic"

__all__ = ["DEFAULT_MODEL", "ProviderClient", "load_client_class"]


@runtime_checkable
class ProviderClient(Protocol):
    """The shape this boundary needs from a provider client.

    A locally defined protocol rather than the SDK's class, and rather than a
    ``TYPE_CHECKING`` import of it, for two reasons that point the same way:
    the contract counts a guarded import as an import, and a signature naming
    the SDK's class would put an SDK type on a surface that must not carry one.

    Structural, so the real client satisfies it without being told to.
    """

    def __init__(self, *args: Any, **kwargs: Any) -> None: ...


def load_client_class() -> type[Any]:
    """Return the provider client class, importing the SDK on first use.

    The import is function-local by design (TR-003). Returning the class rather
    than an instance keeps this callable in an environment with no credential:
    constructing a client reads one from the environment, and the offline suite
    runs with none present (TR-023).

    Raises:
        ProviderUnavailableError: the ``provider`` extra is not installed.
            Raised as a gateway-owned error rather than letting
            ``ModuleNotFoundError`` escape, because an SDK-shaped failure
            crossing this boundary is the coupling the boundary exists to
            prevent. ADR-0014 records this runtime failure as the accepted cost
            of making the SDK optional.
    """
    # Raised *outside* the handler, and that placement is the whole point.
    # TR-064 forbids retaining the original as `__cause__` **or** as
    # `__context__`. `raise ... from None` inside the `except` block satisfies
    # only the first: it sets `__suppress_context__`, which stops the default
    # traceback renderer from printing the original, while `__context__` still
    # holds it and `exc.__context__` still hands it back. Leaving the handler
    # before raising is what actually clears it, so the property holds against
    # inspection and not only against rendering.
    client_class: type[Any] | None = None
    try:
        import anthropic
    except ModuleNotFoundError:  # pragma: no cover - exercised by T015
        pass
    else:
        client_class = anthropic.Anthropic

    if client_class is None:
        raise ProviderUnavailableError(
            "the provider SDK is not installed; add the extra with "
            f"`uv add 'gateway[provider]'` (missing distribution: {_PROVIDER_DISTRIBUTION})"
        )

    return client_class
