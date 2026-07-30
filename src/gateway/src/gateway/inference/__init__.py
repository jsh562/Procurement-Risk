"""Local inference: the query/corpus encoder and the cross-encoder reranker.

Placed here by {SAD:ADR-0023}. Both Python boundaries already depend on this
package, and E008 needs a query embedding at request time — so the alternative
was either the serving entry declaring the modeling entry, which the layout rule
forbids, or a second pooling implementation kept in step with the first by
review. A query embedded by a different implementation than the corpus lands in
a different vector space, with no error and degraded ranking as the only symptom.

This package holds **arithmetic** — masked mean pooling, L2 normalization,
score computation — so `gateway.provider` must not reach it, on the same
reasoning that keeps it out of `gateway.compute`. The forbidden contract in
`src/gateway/pyproject.toml` names both. That contract named only
`gateway.compute` until E008: before {SAD:ADR-0023} no inference lived here, so
the gap did not exist to be found.
"""
