"""Pure arithmetic: cost, content hashing, duration.

Everything in this subpackage is a deterministic function over its arguments,
with no provider client, no database handle, and no clock read beyond one
injected value. That is what makes it property-testable, and TR-028 requires
Hypothesis coverage over all three.

The computation-boundary contract in this entry's `pyproject.toml` forbids
`gateway.provider` from reaching anything here, directly or through an
intermediate module (TR-032). The orchestration module above both composes
them. Skeleton placed by T002 so that contract has a module to bind to;
`pricing`, `hashing`, and `timing` arrive with T035, T054, and T039.
"""

from __future__ import annotations

__all__: list[str] = []
