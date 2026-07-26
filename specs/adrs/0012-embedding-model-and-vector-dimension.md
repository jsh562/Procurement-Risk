---
adr_id: ADR-0012
status: accepted
date: 2026-07-25
tags: [retrieval, embeddings, schema, compute-envelope, reproducibility]
supersedes: []
superseded_by: ""
related_artifacts: ["specs/prd.md", "CAP-003", "ADR-0002", "ADR-0005", "ADR-0006", "ADR-0013", "specs/00003-core-data-schema/spec.md", "E003", "E006", "E008", "E014"]
deciders: ["Project owner", "Solution architect"]
---

# ADR-0012: Embedding Model and Vector Dimension

## Status

Accepted.

The corpus is embedded with a compact open sentence-embedding model producing 384-dimensional normalized vectors, generated locally and offline. `EMBEDDING_DIM` is **384**.

## Context

The chunk table needs a `vector(N)` column, and `N` must be a literal in the migration that declares it. A vector column's dimension is part of its type: it cannot be widened or narrowed in place without rewriting every row, so the number has to be right before the migration is authored rather than discovered afterwards.

No registered document names an embedding model. The schema epic surfaced this as a blocking gap and gated its own chunk migration behind this record — E003's spec carries it as TR-050, as the interface prerequisite IP-013, and as OBJ1 VC11, all of which say the same thing: an accepted decision record naming the model and fixing the dimension must exist in `specs/adrs/` before the chunk migration exists.

The choice is not local to one epic. It fixes the vector space for the entire corpus and binds three downstream epics simultaneously — E006 generates the embeddings, E008 retrieves against them, and E014 publishes ablation numbers measured on them. Every chunk ever written shares whatever is chosen here, because vectors from two different models are not comparable and a corpus containing both is a corpus whose distances mean nothing.

Four constraints narrow the field before quality is considered at all.

The first is a hard ceiling. pgvector's HNSW index refuses columns above 2000 dimensions — the `vector` type will store more, but the index will not build. ADR-0005 requires a tuned HNSW index on the serving path, so any model above 2000 dimensions does not trade quality for cost; it forecloses the serving index outright and leaves exact scan as the only option on both paths.

The second is the request-time compute envelope. Query embedding happens at request time in the API container, whose steady-state resident memory is capped at 400 MB — a budget the INT8 cross-encoder reranker session already dominates under ADR-0006. A second transformer session loaded beside it competes for what is left of the same 400 MB, so encoder size is a first-order constraint here, not a rounding error.

The third is reproducibility. Retrieval recall and mean reciprocal rank are published with numeric targets, and ADR-0006 already established that a published number must stay true for as long as the checkout stays unchanged. Whatever embeds the corpus must be a pinned artifact, reproducible from a clean checkout with no credential and no network — the same standard the reranker was held to, for the same reason.

The fourth is scale, which is permissive: at roughly 5,000 to 15,000 chunks, neither storage nor index build time meaningfully discriminates between the candidates. Scale does not decide this; memory and the index ceiling do.

## Decision Drivers

- Reproducibility of published retrieval numbers from a clean checkout, with no credential and no network access
- The 2000-dimension HNSW ceiling, which forecloses the serving index ADR-0005 requires for any model above it
- The request-time compute envelope — API container steady-state RSS at or below 400 MB, already carrying the INT8 reranker session
- Local, offline generation, so no request-time provider dependency is reintroduced into a path the architecture deliberately cleared
- Retrieval quality sufficient at a corpus of roughly 5,000 to 15,000 chunks
- Storage and index cost, which scale linearly in dimension but are not decisive at this corpus size

## Considered Options

### Option A: Compact open sentence-embedding model, 384 dimensions, generated locally

A small six-layer sentence-transformer of the `all-MiniLM-L6-v2` class — roughly 23 million parameters, under 100 MB at full precision — pinned by model identity and revision in the repository and run locally for both corpus embedding and query embedding.

- **Pros**:
  - Roughly 80 MB of resident memory for the loaded session, which fits beside the INT8 reranker inside the 400 MB envelope with headroom left for the request path itself
  - 384 dimensions sits far below the 2000-dimension HNSW ceiling, leaving room for a future model roughly five times wider without touching the index strategy
  - A pinned local artifact, so published recall and reciprocal-rank figures are reproducible from a clean checkout with no credential and no network
  - No request-time provider dependency, no per-query cost, and no network latency added to query embedding
  - Smallest vectors to store, index, and rebuild — under 25 MB of raw vector data at the top of the corpus range
- **Cons**:
  - Lowest retrieval quality of the open candidates on general benchmarks; the compact model gives up measurable ground to the 768-dimension class
  - Short input window — 256 word pieces, after which input is silently truncated — which constrains E006's chunk sizing rather than merely advising it
  - 384 dimensions is a real information bottleneck for long, densely technical specification passages

### Option B: Mid-size open model, 768 dimensions

A base-size encoder of the `bge-base-en-v1.5` class — roughly 110 million parameters, on the order of 440 MB at full precision.

- **Pros**:
  - Clearly better retrieval quality than the compact class on general and domain benchmarks
  - Still local, offline, and pinnable, so the reproducibility gate is satisfiable
  - 768 dimensions remains well under the HNSW ceiling
  - Longer input window than the compact class, easing chunk-sizing pressure on E006
- **Cons**:
  - The loaded session alone approaches or exceeds the entire 400 MB request-time envelope, before the reranker session, the framework, and any in-flight batch are counted — it does not fit without either quantizing it too or renegotiating ADR-0006's budget
  - Doubles vector storage and index size against Option A for a corpus where retrieval is already not storage-bound
  - Slower query encoding on the one or two shared cores the envelope assumes, on a path already paying a few hundred milliseconds for reranking

### Option C: Large open model, 1024 dimensions

A large encoder of the `bge-large-en-v1.5` class — roughly 335 million parameters, on the order of 1.3 GB at full precision.

- **Pros**:
  - Best retrieval quality among the open candidates
  - Local and pinnable, so reproducibility is preserved in principle
  - 1024 dimensions is still inside the HNSW ceiling

- **Cons**:
  - Three times the entire request-time memory envelope for the session alone; there is no configuration in which this loads beside the reranker in a 400 MB container
  - Would force query embedding out of the request path into a separate service, reintroducing exactly the service boundary ADR-0003 removed
  - Query encoding latency on constrained CPU is prohibitive for an interactive path
  - Quality gain is largest on tasks and corpora far bigger than 15,000 chunks, so the marginal benefit here is much smaller than the benchmark delta suggests

### Option D: Hosted provider embedding API

Embeddings obtained from a vendor endpoint — the small tier at 1536 dimensions, the large tier at 3072.

- **Pros**:
  - No local memory cost at all; the request-time envelope is untouched
  - Strong retrieval quality with no local runtime tuning
  - No model artifact to host, pin, or export

- **Cons**:
  - Published retrieval numbers become a function of a vendor's model version, which can change without notice and without leaving any signal in the repository — the precise failure ADR-0006 rejected for the reranker, and rejecting it there while accepting it here would be incoherent
  - The evaluation harness would need a credential and network access to reproduce anything, contradicting the clean-checkout reproduction gate
  - Reintroduces a request-time, credentialed external dependency on the query path, which the architecture deliberately cleared
  - The large tier at 3072 dimensions exceeds the 2000-dimension HNSW ceiling outright and cannot be served through the index ADR-0005 requires; the small tier at 1536 fits but leaves almost no headroom
  - Re-embedding the corpus after any vendor deprecation becomes a billed, rate-limited, network-bound operation rather than a local batch job

## Decision Outcome

Chosen option: **Compact open sentence-embedding model, 384 dimensions, generated locally** — a pinned `all-MiniLM-L6-v2`-class encoder producing 384-dimensional L2-normalized vectors, run locally for corpus embedding in E006 and for query embedding in E008. `EMBEDDING_DIM` is **384**, and that is the literal the chunk column is declared with: `vector(384)`.

Two constraints eliminate three of the four options before quality is weighed. Option D fails the reproducibility gate on the same argument that decided ADR-0006 — a published number that a vendor can invalidate without any change to the checkout is not publishable — and it puts a credentialed network call back on the request path and on the evaluation harness. Options B and C fail the compute envelope: the base-size session approaches the whole 400 MB budget on its own and the large session triples it, and the reranker is already the dominant line item inside that budget. Neither fits without renegotiating ADR-0006 or exiling query embedding into a separate service, which would undo ADR-0003.

That leaves Option A, and it wins on merit rather than by elimination. Its ~80 MB session coexists with the INT8 reranker inside the envelope with headroom to spare; it is a pinned local artifact so the reproducibility gate is trivially satisfiable on the dense arm, matching the standard the reranker is held to; and 384 dimensions sits so far under the 2000-dimension ceiling that a future model up to roughly five times wider could be adopted without the index strategy changing at all.

The cost is honest and is accepted deliberately. The compact model gives up measurable retrieval quality against the 768-dimension class, and its 256-word-piece input window is a hard constraint on E006's chunker rather than a suggestion. The first is mitigated structurally: the dense arm is one of two fused arms and is followed by cross-encoder reranking, so the design is not asking the embedding model to carry retrieval quality alone — and E014's ablation table will measure the dense arm's actual contribution rather than assume it. The second is an explicit obligation on E006, recorded below.

## Consequences

### Positive

- The chunk migration can be authored: `EMBEDDING_DIM` is 384 and the column is `vector(384)`, unblocking E003's TR-050, IP-013, and OBJ1 VC11.
- Published retrieval numbers are reproducible from a clean checkout with no credential and no network, on the same terms ADR-0006 established for the reranker.
- The embedding session fits beside the INT8 reranker inside the 400 MB request-time envelope with headroom for the request path itself.
- 384 dimensions leaves roughly a fivefold margin under pgvector's 2000-dimension HNSW ceiling, so the serving index ADR-0005 requires is available now and would survive a substantially wider replacement model.
- Query embedding stays in-process on the request path: no per-query cost, no network latency, no credential.
- Raw vector storage stays under 25 MB at the top of the corpus range, keeping index builds and rebuilds cheap during development.

### Negative

- **The 2000-dimension HNSW ceiling is a standing constraint on every future model change, not a one-time check.** Any replacement model above 2000 dimensions cannot be served through an HNSW index at all and would force ADR-0005's serving path back to exact scan. Dimension must be verified against this ceiling before any model swap is proposed, and this constraint outlives the specific model chosen here.
- **Changing the embedding model requires re-embedding the entire corpus plus a forward migration.** A vector column's dimension is part of its type, so a change of dimension is a schema change, not a configuration change: a forward Alembic migration altering the column, a full regeneration of every chunk vector, an index rebuild, and a re-run of every published retrieval number. There is no incremental path and no in-place edit.
- The compact model gives up measurable retrieval quality against the 768-dimension class; the dense arm is the weakest it would be under any open option considered.
- The 256-word-piece input window truncates silently. E006's chunker must keep chunks inside it, and must fail or split rather than emit a chunk whose tail is never embedded — a truncated chunk is indistinguishable from a complete one at query time.

### Neutral

- **Model identity and revision are recorded per chunk, and a mismatch refuses to serve rather than mixing vector spaces.** The chunk table carries the embedding model identity and model revision on every row (E003 TR-012). Vectors from two models are not comparable, so a corpus containing both is silently wrong in a way no query error would reveal; the system therefore refuses to serve a corpus whose rows disagree instead of returning distances computed across two spaces.
- Vectors are L2-normalized at generation, so cosine and inner-product ordering coincide; the HNSW index uses the cosine operator class, and the exact evaluation path uses the same distance.
- 384 is published in the `schema_constants` row per ADR-0013 and also appears as the literal in the chunk migration's DDL; E003 TR-048 requires a test asserting the two agree.
- The model artifact is pinned by repository identity and revision and vendored or cached as a build input, so neither corpus embedding nor evaluation reaches the network at run time.
- E014 measures the dense arm's contribution as an ablation rather than assuming it, so the quality given up by choosing the compact class becomes a reported number instead of an unexamined assumption.

## Links

- [specs/prd.md](../prd.md) — CAP-003 (Evidence Retrieval)
- [ADR-0002](0002-postgres-as-the-single-datastore.md) — the single datastore whose vector column this record dimensions
- [ADR-0005](0005-exact-vector-search-for-evaluation-approximate-for-serving.md) — requires the HNSW serving index whose 2000-dimension ceiling constrains this choice
- [ADR-0006](0006-local-quantized-cross-encoder-reranker.md) — establishes the 400 MB request-time envelope this session shares, and the reproducibility standard applied here
- [ADR-0013](0013-schema-ownership-in-the-modeling-entry.md) — publishes `EMBEDDING_DIM` through the `schema_constants` table
- [specs/00003-core-data-schema/spec.md](../00003-core-data-schema/spec.md) — E003; TR-050, TR-012, TR-048, IP-013, OBJ1 VC11, SC-020
- E006 — generates chunk embeddings with the model fixed here
- E008 — retrieves against the vector space fixed here
- E014 — publishes evaluation and ablation numbers measured on it
