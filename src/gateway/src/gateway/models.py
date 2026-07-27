"""Gateway-owned request, result, and record types.

Skeleton placed by T002 so the public-surface import contract has a module to
bind to; see `errors.py` for why the contract must precede the module. The
types themselves arrive with T007.

Nothing here may import the provider SDK, at module scope or under
`TYPE_CHECKING`: `import-linter`'s `exclude_type_checking_imports` defaults to
false, so a guarded import still violates the contract, and a leaked SDK type
in a signature couples every consumer to the provider (ADR-0014).
"""

from __future__ import annotations

__all__: list[str] = []
