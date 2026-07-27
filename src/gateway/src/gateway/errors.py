"""Gateway-owned error types — the only exceptions this package raises.

Skeleton placed by T002 so the public-surface import contract has a module to
bind to. `import-linter` resolves `source_modules` eagerly and errors on a name
that does not exist, so a contract written before its module is a contract that
cannot run — and HINT-001 requires the contract to land *first*, because one
added after the code it should have blocked cannot prove it would have blocked
it. The hierarchy itself arrives with T008.
"""

from __future__ import annotations

__all__: list[str] = []
