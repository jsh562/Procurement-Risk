"""A deliberately violating package.

FR-035. The real contract bars `api.llm` from `api.risk_read` — the read path
holds date arithmetic and the degraded-state precedence, which model-facing code
must not reach any more than it may reach `api.compute`.

This fixture violates it two ways, because the real contract has
`allow_indirect_imports = False` and a fixture that only violated it directly
would leave the indirect half unevidenced.
"""
