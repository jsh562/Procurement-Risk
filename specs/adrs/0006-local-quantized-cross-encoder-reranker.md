---
adr_id: ADR-0006
status: accepted
date: 2026-07-25
tags: [retrieval, reranking, reproducibility, compute-envelope]
supersedes: []
superseded_by: ""
related_artifacts: ["specs/prd.md", "CAP-003", "CAP-009", "ADR-0005"]
deciders: ["Project owner", "Solution architect"]
---

# ADR-0006: Local Quantized Cross-Encoder Reranker in the Serving Container

## Status

Accepted.

## Context

The retrieval design reranks the top fifty fused candidates with a cross-encoder. That reranker can run in one of three places: as a hosted reranking service, as a local model loaded inside the backend container, or as its own container behind a local interface.

The usual deciding factor does not apply here. The corpus is small and traffic is effectively single-user, so throughput is not a constraint and no option is eliminated on capacity grounds. Two other constraints decide it instead.

The first is the compute envelope, which caps request-time components at a small hosted instance's CPU and memory budget. Any local model competes directly with the rest of the serving container for that budget.

The second is more decisive. Reproducibility of published evaluation numbers is a release gate, and retrieval recall and mean reciprocal rank are published with numeric targets. A hosted reranker would make those numbers a function of a vendor's model version — which can change without notice and without leaving any signal in the repository. A published figure could then stop being true while every file in the checkout stayed byte-identical, and the evaluation harness would additionally require a credential and network access to reproduce anything at all.

## Decision Drivers

- Published evaluation numbers must not depend on a third party's model version
- The evaluation harness must run from a clean checkout without external credentials or network access
- Request-time memory and CPU must fit a small hosted instance
- Avoiding an additional stateful service for a single-user demo

## Considered Options

### Option A: Integer-quantized cross-encoder in the serving container

A small cross-encoder exported to an interchange format and dynamically quantized to integer precision, loaded once at application startup and warmed before the readiness gate opens.

- **Pros**:
  - The reranker is a pinned artifact in the repository, so a published number cannot move because a vendor shipped a new model
  - The evaluation harness needs no credentials and no network
  - No per-query cost and no external latency
  - Dynamic quantization needs no calibration dataset, so the artifact is a pure function of the source model and adds no reproducibility surface
  - Quantization reduces the artifact to roughly a quarter of its original size
- **Cons**:
  - The loaded session plus one in-flight batch is the dominant memory line item in the serving container
  - Reranking fifty candidates on one or two shared cores costs a few hundred milliseconds
  - Requires explicit thread configuration, because the runtime derives its default thread count from the host rather than the container's allocation

### Option B: Hosted reranking service

Reranking delegated to a vendor endpoint.

- **Pros**:
  - Near-zero memory in the serving container
  - Best available reranking quality
  - No local runtime tuning
- **Cons**:
  - Published evaluation numbers become non-reproducible the moment the vendor updates the model
  - The evaluation harness requires a credential and network access, contradicting the clean-checkout reproduction gate
  - Adds per-query cost and network latency
  - Introduces an external dependency for the demo's core retrieval path

### Option C: Separate reranker container

The reranker runs as its own service behind a local interface.

- **Pros**:
  - Clean boundary and independently scalable
  - Serving container stays lean
- **Cons**:
  - A third service for a single-user demo
  - The memory cost does not disappear, it relocates
  - More deployment surface for no reproducibility or latency gain

## Decision Outcome

Chosen option: **Integer-quantized cross-encoder in the serving container** — it is the only option under which a published retrieval number stays true for as long as the checkout stays unchanged. The reranker becomes a pinned artifact in the repository rather than a call to something a vendor controls, so recall and mean reciprocal rank can be reproduced from a clean checkout with no credentials and no network. Dynamic quantization strengthens rather than weakens that property: because it needs no calibration dataset, the quantized artifact is a pure function of the source model and introduces nothing new to version.

Option B is rejected on the release gate directly. It makes the published numbers depend on a model version that can change without notice and without any signal in the repository, and it requires the evaluation harness to hold a credential and reach the network — which contradicts the clean-checkout reproduction requirement outright. Its quality advantage is real but cannot be spent, because a number that cannot be reproduced is not publishable regardless of how good it is.

Option C is rejected as cost without benefit. It relocates the memory rather than removing it, adds a third service to run locally and provision when hosted, and buys no reproducibility or latency improvement over Option A for a workload that is single-user by assumption.

The accepted price is paid in the compute envelope: the model session becomes the dominant memory line item in the serving container, reranking adds a few hundred milliseconds per query on constrained CPU, and thread counts must be set explicitly because the runtime cannot see the container's CPU allocation. These are sizing and configuration obligations, not correctness risks, and they are recorded below so the hosted instance is sized against them deliberately.

## Consequences

### Positive

- Every published retrieval number is reproducible from a clean checkout with no credentials and no network access.
- No per-query cost and no external latency in the primary retrieval path.
- The quantized artifact derives deterministically from the source model, with no calibration dataset to version.

### Negative

- The model session dominates the serving container's memory budget and must be accounted for explicitly when sizing the hosted instance.
- Reranking adds a few hundred milliseconds per query on constrained CPU, mitigated by truncating candidate length and batching.
- Runtime thread settings must be configured explicitly, since container CPU allocation is not visible to the runtime's defaults.

### Neutral

- The session loads once at application startup with warm-up passes at the maximum batch shape, and readiness is gated on warm-up completion.
- A single worker process is used, because each worker would load its own copy of the model.
- The difference between full-precision and quantized reranking is measured on the frozen evaluation set and published as an additional arm rather than assumed to be negligible.
- If the model fails to load, the system degrades to fusion-only ordering and flags the degraded mode rather than silently serving worse results under published reranked numbers.

## Links

- [specs/prd.md](../prd.md) — CAP-003 (Evidence Retrieval), CAP-009 (Evaluation & Calibration Evidence)
- [ADR-0005](0005-exact-vector-search-for-evaluation-approximate-for-serving.md) — establishes the fused candidate set this ADR reranks
