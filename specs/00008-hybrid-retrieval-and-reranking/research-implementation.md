# Research: Hybrid Retrieval and Reranking — Implementation

> Feature: E008 | Date: 2026-07-29 | Purpose: engineering detail for the build. Complements
> `research.md` (domain practice: fusion theory, evaluation statistics, degradation convention) and
> does not restate it. Stack is fixed: PostgreSQL 16 + pgvector + `tsvector`, Python 3.12, ONNX
> Runtime ≥ 1.24, FastAPI at `/src/api`, encoder artifacts under `data/encoder/`.

## Reciprocal rank fusion as one statement

- **Practice**: pgvector's own hybrid example is the reference shape — one CTE per arm, each with
  its own `ORDER BY` and `LIMIT`, a window function producing per-arm rank, a full outer join on the
  row id, and a coalesced reciprocal-rank term summed so a candidate missing from one arm scores
  zero on that side. PostgreSQL 16 folds a non-recursive, volatile-free CTE into the parent when it
  is referenced once; folding is a planner transformation, and a sub-select carrying `LIMIT` is not
  flattened further, so the per-arm cut of 50 stands.
- **Implies**: Two CTEs, one join, one final ordering by fused score then chunk id. A row-number
  window over each arm's score and the tie-break key gives a total order per arm; a rank window
  gives tied candidates equal rank, which is truer to the formula but leaves the 50-row cut
  ambiguous when a tie straddles it. Either is defensible and FR-004 requires recording which.
- **Flag**: The documentation nowhere states that an inlined CTE's `LIMIT` is honoured; it follows
  from semantics rather than from text. Assert it with a plan-shape test rather than trusting the
  inference.
- **Sources**: <https://www.postgresql.org/docs/16/queries-with.html>,
  <https://github.com/pgvector/pgvector-python/blob/master/examples/hybrid_search/rrf.py>

## pgvector index behaviour under filters and at depth

- **Practice**: The graph index's search-breadth setting defaults to 40 — below the fetch depth of
  50, so the default silently under-serves the dense arm. Filtering is applied after the index is
  scanned; the project's own worked figure is a predicate matching 10% of rows returning roughly
  four rows at the default breadth. Version 0.8.0 added iterative scans in two modes, strict and
  relaxed, with bounds on tuples scanned and working memory. The relaxed mode improves recall but
  returns results slightly out of distance order. Partial indexes are the recommendation for a few
  distinct filter values, partitioning for many.
- **Implies**: Set the breadth at or above the fetch depth (FR-027). Strict-order iterative scan is
  the FR-028 answer that needs no schema change; partial indexes would be a new index, which this
  epic's scope excludes. Relaxed order conflicts with FR-020 and is unusable here.
- **Flag**: Verify the extension version of the digest-pinned image before designing on iterative
  scan. If it predates 0.8.0 the setting does not exist and the only in-scope remedy is a wider
  search breadth. That check is a task, not an assumption.
- **Sources**: <https://github.com/pgvector/pgvector/blob/master/README.md>

## ONNX Runtime cross-encoder inside a container

- **Practice**: The default intra-op thread count is one per physical core **the operating system
  reports**, which under a CPU quota is the host's count rather than the container's; the runtime
  also sets thread affinity when it picks the count itself, and that pinning is what oversubscribes
  in containers. Setting the counts explicitly suppresses affinity assignment. Inter-op threading
  matters only under a parallel execution mode; the default is sequential. Dynamic quantization is
  the documented recommendation for transformer models, computes activation scales at run time, and
  is explicitly not lossless; unsigned-signed quantization saturates on hardware without the
  relevant instruction extensions.
- **Implies**: Pin intra-op to the container's CPU limit and inter-op to one, both from
  configuration — the `NEW-CONFIG` signal. The saturation caveat is machine-dependent by
  construction, which is exactly why {SAD:ADR-0006} requires the quantized-versus-full-precision arm
  measured rather than assumed. Warm-up must run at the real maximum batch **and** sequence shape:
  memory-pattern optimisation is documented as effective only for static shapes, so under variable
  shapes warm-up buys arena growth, page-in and first-run graph initialisation, not buffer planning.
- **Flag**: The runtime publishes no container guidance; the quota behaviour is known from issue
  reports rather than documentation, and the arena defaults appear only in the API reference. Treat
  both as verified-locally rather than cited.
- **Sources**: <https://onnxruntime.ai/docs/performance/tune-performance/threading.html>,
  <https://onnxruntime.ai/docs/performance/model-optimizations/quantization.html>

## Query-side embedding over the corpus-side path

- **Practice**: The vendored encoder documents no query or passage prefix, mean pooling with the
  attention mask, L2 normalization, and truncation past 256 word pieces. Prefix conventions belong
  to asymmetric or prompt-declaring models; this one declares none. Under per-batch padding a
  one-item batch pads to its own length, so mask-weighted pooling is unaffected by batch shape.
- **Implies**: Call the existing corpus-side embedding function with a one-element sequence and take
  the first row — no new pooling code and no prefix. Adding a prefix would move the query off the
  chunks' vector space silently, with no error and only degraded ranking as the symptom. Assert the
  returned shape and unit norm, and enforce FR-007 by comparing the encoder identity against the
  identity recorded on the chunks before any search runs.
- **Flag**: The residual risk is not padding, it is tokenizer identity — this repository keeps a
  second, non-truncating tokenizer instance for counting, and feeding the session from that one
  would change nothing visible on short queries and everything on long ones. Separately, a symmetric
  encoder on a short-query, long-passage task is a known quality ceiling rather than a defect: it is
  the dense arm's measured number, not something to fix with a prefix.
- **Sources**: <https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2>,
  <https://sbert.net/examples/sentence_transformer/applications/semantic-search/README.html>

## Readiness gating in the serving framework

- **Practice**: The framework's lifespan hook runs everything before its yield point before the
  application begins receiving requests — a stronger gate than a readiness endpoint, because no
  window exists in which traffic arrives unwarmed. Orchestrator probes are ternary with no partial
  state: readiness removes the instance from service, liveness restarts it, and a startup probe
  exists for slow-loading containers and blocks the other two until it passes.
- **Implies**: Load and warm inside lifespan, but catch the failure there and still yield, setting a
  degraded flag — raising would produce not-ready, which FR-021 forbids. Ready-degraded is therefore
  a success response carrying a state field, never a status code, and the same flag feeds every
  response body (FR-022). Deployment uses a startup probe with a generous threshold plus a tight
  liveness probe, rather than a long liveness period.
- **Flag**: No standard exists for a machine-readable degraded signal, confirming `research.md`'s
  finding. The closest precedent is an expired Internet-Draft whose warn status returns a success
  code with detail fields — worth mirroring in shape, but it is not a standard and must not be cited
  as one.
- **Sources**: <https://fastapi.tiangolo.com/advanced/events/>,
  <https://kubernetes.io/docs/concepts/configuration/liveness-readiness-startup-probes/>

## Testing a single-statement ranking query

- **Practice**: The database documents that with a row limit you must use an ordering that
  constrains rows into a **unique** order or you get an unpredictable subset, and that the optimizer
  plans differently for different limit values, yielding different row orders. The project's own
  regression suite is golden-output comparison, and its documented caveat is that unordered queries
  legitimately vary by plan.
- **Implies**: Golden orderings are valid here precisely because the tie-break key makes the
  ordering total; it must appear in each arm's ordering as well as the final one, or the candidate
  set itself is unstable. Assert determinism three ways: the same statement twice, again with plan
  settings flipped, and again after a rebuild — an ordering that survives a plan change is defined
  by the query rather than the plan. Property-based tests survive as an oracle: recompute the fusion
  arithmetic in Python from the same per-arm rank vectors and assert it matches the emitted order,
  which tests the formula without moving ranking out of SQL. That is the compensating surface
  `specs/sad.md` prescribes for SQL-resident logic.
- **Flag**: The rebuild assertion holds only on the exact path. Graph construction is randomized, so
  a rebuild can legitimately change which approximate candidates are returned — the spec's own edge
  case. FR-020's identical-ordering guarantee is testable across rebuilds only on the exact path;
  the approximate path gets an overlap and recall-delta assertion instead. No authoritative source
  covers testing SQL-resident ranking; the plan-flip technique is inferred from the limit caveat,
  not published practice.
- **Sources**: <https://www.postgresql.org/docs/16/queries-limit.html>,
  <https://www.postgresql.org/docs/16/regress-evaluation.html>

## Four findings that cut against the obvious design

1. **The per-arm limit is not deterministic on its own.** A row limit requires an ordering that
   constrains rows into a unique order; without a tie-break **inside each arm's CTE**, ties at the
   fiftieth position mean the candidate *set* varies between runs, not merely its ordering. FR-004's
   tie-break key therefore applies per arm, not only to the fused result.
2. **Setting the search breadth per query is a second statement.** FR-002 (one statement) and FR-027
   (breadth at least the fetch depth) collide unless the setting is carried on the connection rather
   than issued per query. `specs/00003-core-data-schema/data-model.md` already prescribes setting it
   "at query time", which as written would be a second statement.
3. **Relaxed-order iterative scan is unusable.** It is the setting that most improves filtered
   recall (FR-028) and it returns results slightly out of distance order, directly against FR-020.
   Strict order is the only compatible mode, and iterative scan exists only from extension version
   0.8.0, which must be verified against the pinned image digest before anything depends on it.
4. **FR-020's identical ordering is not assertable across rebuilds on the approximate path.** Graph
   construction is randomized. The spec's edge cases anticipate this; the requirement text does not
   qualify it, and the plan must scope the guarantee to the exact path.

## Sources Index

| URL | Topic | Fetched |
|-----|-------|---------|
| <https://www.postgresql.org/docs/16/queries-with.html> | RRF in one statement | 2026-07-29 |
| <https://github.com/pgvector/pgvector-python/blob/master/examples/hybrid_search/rrf.py> | RRF in one statement | 2026-07-29 |
| <https://github.com/pgvector/pgvector/blob/master/README.md> | pgvector filters and depth | 2026-07-29 |
| <https://onnxruntime.ai/docs/performance/tune-performance/threading.html> | runtime in a container | 2026-07-29 |
| <https://onnxruntime.ai/docs/performance/model-optimizations/quantization.html> | runtime in a container | 2026-07-29 |
| <https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2> | query-side embedding | 2026-07-29 |
| <https://sbert.net/examples/sentence_transformer/applications/semantic-search/README.html> | query-side embedding | 2026-07-29 |
| <https://fastapi.tiangolo.com/advanced/events/> | readiness gating | 2026-07-29 |
| <https://kubernetes.io/docs/concepts/configuration/liveness-readiness-startup-probes/> | readiness gating | 2026-07-29 |
| <https://www.postgresql.org/docs/16/queries-limit.html> | testing the ranking query | 2026-07-29 |
| <https://www.postgresql.org/docs/16/regress-evaluation.html> | testing the ranking query | 2026-07-29 |
