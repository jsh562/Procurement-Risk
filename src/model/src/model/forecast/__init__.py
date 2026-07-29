"""E007's offline delivery-forecast package: read, split, fit, gate, write.

Two console entry points live here — `forecast-fit` and `forecast-reproduce`
(AD-003) — kept out of `model.procurement` so the generator's constants are not
one import away from the fit that is scored against them.

Nothing in this package may reach `model.llm` or `gateway`: the fit is offline
on every path, enforced by the `import-linter` contract in
`src/model/pyproject.toml` rather than by review (FR-024, FR-025, DV-022).
"""

from __future__ import annotations
