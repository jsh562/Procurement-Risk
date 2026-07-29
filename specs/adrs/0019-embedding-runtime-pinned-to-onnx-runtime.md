---
adr_id: ADR-0019
status: accepted
date: 2026-07-27
tags: [retrieval, embeddings, runtime, compute-envelope, reproducibility]
supersedes: []
superseded_by: ""
related_artifacts: ["specs/prd.md", "CAP-003", "ADR-0003", "ADR-0006", "ADR-0009", "ADR-0012", "specs/00006-document-ingestion-and-extraction/spec.md", "specs/00006-document-ingestion-and-extraction/research.md", "E006", "E008", "E014"]
deciders: ["Project owner", "Solution architect"]
---

# ADR-0019: Embedding Runtime Pinned to ONNX Runtime for Corpus and Query Embedding

## Status

Accepted.

The pinned encoder is exported to ONNX and executed through ONNX Runtime in both places it runs: corpus embedding in E006 and query embedding in E008. Raised by E006, which claimed this number at epic start under its FR-051.

## Context

ADR-0012 fixed *what* is embedded and *into what*: an `all-MiniLM-L6-v2`-class encoder of roughly 23 million parameters, under 100 MB at full precision, producing 384-dimensional L2-normalized vectors, with `EMBEDDING_DIM = 384` written as a literal into the chunk column's type. It budgeted "roughly 80 MB of resident memory for the loaded session" and used that figure to eliminate the 768- and 1024-dimension classes on the request-time envelope.

That figure is model weights. It does not name an inference runtime, and a runtime carries its own resident cost — a framework's own allocator, its kernels, its threading — on top of whatever the weights occupy. ADR-0012's own arithmetic therefore has an unpriced term in it, and the term is not small relative to a budget that eliminated other options by tens of megabytes.

The choice is not local to E006. Two epics embed with the same model in two different processes:

- **E006 embeds the corpus offline**, in a console entry point under the modeling entry, batching several thousand chunks in one run where latency is nearly free and memory is nearly free.
- **E008 embeds the user's query at request time**, inside the API container, once per request, on the hot path.

ADR-0012 names that container's steady-state resident memory cap as 400 MB, carrying forward the request-time envelope ADR-0006 established, and that budget is already dominated by the INT8 ONNX cross-encoder reranker session which ADR-0006 put there. So the serving container is already running ONNX Runtime today, for the reranker, regardless of what this record decides. The only open question on the serving side is whether a *second* inference framework loads beside it.

And the two paths are not independent, because cosine distance between a corpus vector and a query vector is only meaningful if both came from the same weights. A query embedded by a slightly different computation does not error — it retrieves slightly worse, permanently, and no assertion anywhere in the system is in a position to notice.

One further fact makes the runtime question sharper than "which library loads the same weights". A `sentence-transformers` model is not only a transformer: it is a transformer *plus* a mean-pooling module that weights token vectors by the attention mask, *plus* an L2-normalization module. A raw ONNX export of the encoder emits token-level hidden states and stops there. Mean pooling and normalization then become repository code rather than library behaviour — which is precisely why an export cannot be assumed correct and has to be proven against the reference.

The decision is needed now because E006 authors the chunker and the embedding job in this epic, E006's FR-014 and SC-004 already require chunk length to be measured in the encoder's own tokenizer, and the tokenizer is an artifact this record has to pin alongside the weights.

## Decision Drivers

- The 400 MB request-time envelope, which already carries an ONNX session for the reranker
- One inference runtime in the serving container rather than two
- Corpus vectors and query vectors occupying one vector space exactly, by construction rather than by coincidence
- Reproducibility of published retrieval numbers from a clean checkout with no credential and no network (ADR-0009)
- Keeping query embedding in-process, so no separate service boundary is reintroduced (ADR-0003)

## Considered Options

### Option A: ONNX Runtime for both corpus and query embedding

The pinned encoder is exported to ONNX once, vendored with its tokenizer, and executed through ONNX Runtime in both E006's offline job and E008's request path. Pooling and normalization are repository code, shared by both callers. E006 owns producing the artifact and asserting its parity against the reference encoder.

- **Pros**:
  - The serving container loads exactly one inference runtime — the one ADR-0006 already put there for the INT8 reranker — so the unpriced runtime term in ADR-0012's memory arithmetic is paid once, not twice
  - Corpus and query vectors come from the same graph, the same weights, and the same pooling code, so the vector spaces are identical by construction and cannot drift apart silently
  - The export is a pinned build input reproducible from a clean checkout with no network, on the same terms ADR-0006 and ADR-0009 set for the reranker
  - Query embedding stays in-process, so no service boundary is reintroduced and the request path pays no network hop
  - The parity assertion is written once, in the epic that produces the artifact, rather than being owed by whichever epic notices first
- **Cons**:
  - The export is a reimplementation of the thing every retrieval number depends on, and is worthless without a numeric parity assertion against the reference encoder
  - Pooling and normalization become repository code, so a mask-handling mistake is now a defect this project can author
  - Two vendored artifacts — weights and tokenizer — that must be pinned to the same revision, where a mismatch produces disagreement between the measured length and the consumed length rather than an error
  - The export toolchain does not disappear from the repository; it moves to a build-time-only position and still has to be pinned to produce the artifact reproducibly

### Option B: sentence-transformers offline, ONNX Runtime at request time

E006 embeds the corpus with `sentence-transformers` directly, because offline memory is cheap; E008 embeds the query through an ONNX export, because request-time memory is not.

- **Pros**:
  - E006 ships without owning an export, and uses the reference implementation whose pooling and normalization are known-good
  - The request-time envelope is satisfied exactly as under Option A, since the serving side is identical
  - Offline batch throughput is whatever the reference library gives, with no export step in the loop
- **Cons**:
  - Two runtimes produce vectors that must agree to several decimal places, and **any disagreement is invisible**: a query embedded slightly differently simply retrieves slightly worse, forever, with no error raised anywhere and no metric that isolates the cause
  - It defers the export work to E008 without recording it as owed, so the epic that has to build it inherits an unbudgeted obligation discovered late
  - The parity question is not removed, it is relocated to the boundary where it is hardest to test — comparing a stored corpus against a live query encoder rather than comparing two encoders on the same input
  - E014's published ablation numbers would be measured across a runtime seam that no artifact declares

### Option C: sentence-transformers everywhere

The reference library runs in both places, including inside the API container.

- **Pros**:
  - One implementation, one code path, no export, and no parity question to answer at all
  - Pooling and normalization stay library behaviour, so there is no mask-handling code to get wrong
- **Cons**:
  - torch plus a transformer session does not fit beside the INT8 reranker in 400 MB; the framework alone is a multiple of the ~80 MB ADR-0012 budgeted for weights
  - It breaches ADR-0006's envelope, which is the constraint that eliminated the 768- and 1024-dimension encoders in the first place — accepting it here would retroactively invalidate ADR-0012's reasoning
  - The only way to hold the envelope while keeping this library is to move query embedding into a separate service, which reintroduces exactly the boundary ADR-0003 removed
  - It puts a second inference runtime in a container that is already running ONNX Runtime for the reranker, for no benefit the reranker's runtime does not already provide

## Decision Outcome

Chosen option: **ONNX Runtime for both corpus and query embedding** — the pinned encoder is exported to ONNX, vendored with its tokenizer at the same revision, and executed through ONNX Runtime in E006's offline job and E008's request path alike.

The envelope decides the serving side and leaves no room to argue. Option C's framework does not fit beside the INT8 reranker in 400 MB, and the two ways out of that are both foreclosed: renegotiating ADR-0006's budget would invalidate the reasoning that eliminated the 768- and 1024-dimension encoders under ADR-0012, and exiling query embedding into its own service would undo ADR-0003. Whatever else is true, the request path runs ONNX.

That reduces the real choice to Option A against Option B — whether the offline path joins it. Option B is the tempting one, because it lets E006 use the reference implementation and defer the export. It is rejected on the failure mode. Two runtimes embedding into one vector space is a correctness claim enforced by nothing: the vectors are the same shape, the distances are well-formed, the queries return results, and a small systematic difference between the corpus encoder and the query encoder shows up only as retrieval that is quietly worse than the published numbers say. There is no exception to catch and no invariant to assert, because the artifact that would reveal the problem — the two encoders' outputs on the same input — is exactly what Option B never computes. Option A makes that comparison a build-time assertion instead of a permanent latent risk.

The honest cost of Option A is that the export is a reimplementation. A raw ONNX graph emits token-level hidden states; the attention-masked mean pooling and the L2 normalization that `sentence-transformers` supplies as modules become code in this repository, and a mask-handling mistake there is a defect this project can author and every retrieval number would inherit. That is why the parity assertion is not a nice-to-have and is recorded below as an obligation on E006 rather than a suggestion: without it, the export is an unverified reimplementation of the thing all of CAP-003 rests on.

## Consequences

### Positive

- **One runtime and one set of weights across the offline and request-time paths.** Corpus vectors and query vectors are identical by construction rather than by coincidence, and the pooling and normalization code is shared by both callers rather than duplicated per path.
- The request-time envelope is satisfied without a second framework: the API container loads exactly one inference runtime, which it already ships for the INT8 reranker under ADR-0006.
- The exported model is a pinned build input, so published recall and mean-reciprocal-rank figures reproduce from a clean checkout with no credential and no network — the same standard ADR-0006 set for the reranker and ADR-0009 generalized.
- Query embedding stays in-process on the request path, so no per-query network hop and no service boundary are reintroduced.
- The parity obligation lands in the epic that produces the artifact, so E008 and E014 consume a proven export rather than discovering they owe one.

### Negative

- **E006 owns producing the ONNX artifact and proving it.** The export is a build input pinned by model identity and revision, and E006 must assert that the exported model's output matches the reference encoder within a stated numeric tolerance, on a fixed set of inputs. Without that assertion the export is an unverified reimplementation of the thing every retrieval number depends on. The tolerance is a published number in the sense of ADR-0009 and has to be defended, not merely chosen.
- **Pooling and normalization move from library behaviour into repository code.** A raw export emits token-level hidden states; attention-masked mean pooling and L2 normalization are ours to implement. A mask-handling error would produce well-formed 384-vectors that are systematically wrong, which is exactly the class of defect the parity assertion exists to catch.
- **The tokenizer is a separate vendored artifact, pinned to the same revision as the weights.** Its `model_max_length` field is `512` and that is **not** the effective sequence cap, which is `256`; the cap counts special tokens, so the content budget is **254** pieces. Reading the tokenizer's own field is the likeliest way to ship silent truncation, and a tokenizer pinned to a different revision than the weights makes the measured length and the consumed length disagree without erroring.
- **An encoder upgrade is now a two-artifact change** — weights and tokenizer, exported and re-proven together — on top of everything ADR-0012 already recorded: a change of dimension is a schema change, and any model change invalidates every stored vector and every published retrieval number.
- The export toolchain remains a pinned build-time dependency of the repository. Option A removes `sentence-transformers` and torch from the serving image, not from the project.

### Neutral

- This record fixes *how* vectors are computed. It does not touch *what* they are: ADR-0012's model class, its 384 dimensions, its L2 normalization, and the `vector(384)` column literal are unchanged, and nothing here alters the 2000-dimension HNSW ceiling or the per-chunk model identity and revision that E003 records.
- ADR-0006's explicit thread-configuration obligation now applies to two sessions in the same process, since ONNX Runtime derives its default thread count from the host rather than the container's CPU allocation. The reranker's existing configuration is the precedent, not a separate mechanism.
- E006's FR-014 and SC-004 are unaffected in substance — chunk length is still measured in the encoder's own tokenizer — but the tokenizer they name is now pinned by this record as a vendored artifact rather than assumed available.
- ADR-0003 is superseded by ADR-0011 on its invocation clause only; the boundary decision this record relies on — that there is no separate modeling service and the serving image never installs the modeling stack — stands unchanged.
- E014 measures the dense arm's contribution as an ablation. Because both arms of that measurement now run on one runtime, the ablation reports the model's contribution rather than a runtime seam.

## Links

- [specs/prd.md](../prd.md) — CAP-003 (Evidence Retrieval)
- [ADR-0012](0012-embedding-model-and-vector-dimension.md) — fixes the encoder class, `EMBEDDING_DIM = 384`, and the ~80 MB weight budget this record prices a runtime against
- [ADR-0006](0006-local-quantized-cross-encoder-reranker.md) — puts the INT8 ONNX reranker session in the serving container, establishes the request-time envelope ADR-0012 states as 400 MB, and sets the explicit-thread-configuration obligation
- [ADR-0003](0003-offline-modeling-package-instead-of-a-model-service.md) — no separate modeling service and no modeling stack in the serving image; superseded by ADR-0011 on its invocation clause only, and relied on here for the boundary clause that stands
- [ADR-0009](0009-reproducibility-gate-as-a-published-tolerance.md) — reproduction from a clean checkout as a published tolerance, which the export's parity tolerance is stated under
- [specs/00006-document-ingestion-and-extraction/spec.md](../00006-document-ingestion-and-extraction/spec.md) — E006; FR-051 claims this number, FR-014 and SC-004 require tokenizer-measured chunk length, FR-053 measures the leaf-length distribution
- [specs/00006-document-ingestion-and-extraction/research.md](../00006-document-ingestion-and-extraction/research.md) — the `model_max_length` 512 versus `max_seq_length` 256 trap and the 254-piece content budget
- E006 — produces the ONNX artifact, vendors the tokenizer, and owns the parity assertion
- E008 — embeds the query at request time through the same artifact
- E014 — publishes retrieval and ablation numbers measured on vectors this runtime produced
- [specs/sad.md](../sad.md) — ADR catalog; requires a new row
