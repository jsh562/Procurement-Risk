"""The single module in this repository permitted to import the model provider.

Every traced call in every later epic reaches the provider through here. The
`protected` import-linter contract in this package's ``pyproject.toml`` names
``anthropic`` protected and this module its only allowed importer, so a second
import site fails the build rather than passing review.

E001 establishes the boundary and nothing more: no request is issued here, and
the tracing, schema validation, and cost accounting the wrapper owes arrive
with E004. What exists now is the seam those obligations attach to.
"""

from __future__ import annotations

from typing import Final

import anthropic

# Pinned here rather than at each call site so the model in use is a property
# of the boundary, readable without grepping the callers.
DEFAULT_MODEL: Final[str] = "claude-opus-5"

__all__ = ["DEFAULT_MODEL", "client_type"]


def client_type() -> type[anthropic.Anthropic]:
    """Return the provider client class without constructing one.

    Constructing a client reads credentials from the environment. E001 supplies
    none and TR-025 forbids introducing any, so this returns the type — enough
    to prove the import resolves and the contract permits it, without requiring
    a credential the epic deliberately does not have.
    """
    return anthropic.Anthropic
