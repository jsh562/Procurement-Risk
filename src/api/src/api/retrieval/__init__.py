"""Hybrid retrieval: the fusion statement, the part-number route, reranking.

This package is the api entry's **third computation package**, alongside
`api.compute` and `api.risk_read`, and the import-linter contract in
`src/api/pyproject.toml` names it as such. E010 recorded the precedent when it
added the second: a boundary that guards one of two is a boundary in name.

What lives here is deterministic — the ranking statement, result projection,
the metrics, and the reporting surface. Model-facing code (`api.llm`) must not
reach any of it, which is what the contract asserts rather than leaves to
review.
"""
