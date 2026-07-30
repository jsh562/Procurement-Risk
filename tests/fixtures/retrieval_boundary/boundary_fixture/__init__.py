"""A deliberately violating package.

FR-002. The real contract bars `api.llm` from `api.retrieval` — the third
computation package, holding the ranking statement, result projection, the
metrics and the reporting surface. Model-facing code must not reach it any more
than it may reach `api.compute` or `api.risk_read`.

This fixture violates it two ways, because the real contract has
`allow_indirect_imports = False` and a fixture that only violated it directly
would leave the indirect half unevidenced.
"""
