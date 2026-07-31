"""A deliberately violating package.

FR-048 / {SAD:ADR-0023}. The real contract bars `gateway.inference` from
`gateway.provider`, and it is what makes the modeling entry's import of the
shared encoder safe: `test_model_facing_placement.py` admits `gateway.inference`
outside `model.llm` *only* because the admitted package cannot itself reach the
provider. If this contract lapses, that exception silently becomes the hole the
placement rule existed to close.

Violated two ways, because the real contract has `allow_indirect_imports = False`
and a fixture violating it only directly would leave the laundered route
unevidenced — which is the route that matters here, since inference reaching the
provider through a helper is exactly how it would happen in practice.
"""
