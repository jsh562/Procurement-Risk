---
adr_id: ADR-0005
status: accepted
date: 2026-07-25
tags: [retrieval, evaluation, reproducibility]
supersedes: []
superseded_by: ""
related_artifacts: ["specs/prd.md", "CAP-003", "CAP-009", "ADR-0002"]
deciders: ["Project owner", "Solution architect"]
---

# ADR-0005: Exact Vector Search for Evaluation, Approximate for Serving

## Status

Accepted.

## Context

The dense retrieval arm can run either as an exact scan over all chunk vectors or through an approximate nearest-neighbour graph index. Two facts make this a real decision rather than a default.

First, the graph index is explicitly non-deterministic: its structure depends on insertion order and parallel build workers, so rebuilding it can change results for an identical query. Second, its default search-breadth parameter is forty while the retrieval design fetches fifty candidates per arm — a candidate list smaller than the requested result count, which silently costs recall.

At five to fifteen thousand chunks an exact scan completes in roughly fifty to a hundred and fifty milliseconds with complete recall and stable ordering, so performance does not force the choice. What does force it is that reproducibility of published evaluation numbers is a release gate, and retrieval recall and mean reciprocal rank are published metrics with numeric targets.

## Decision Drivers

- Published retrieval metrics must measure the retrieval design, not an index build
- Reproducibility of published numbers is a release gate
- Demonstrating competent approximate-index tuning is expected of a project built on a vector-enabled database
- Avoiding drift between the evaluated path and the served path

## Considered Options

### Option A: Exact for evaluation, approximate for serving, delta published

A single configuration flag controls only whether the vector index is used. The evaluation harness runs exact; serving runs the tuned graph index; the recall difference between them becomes a fifth arm of the published ablation table.

- **Pros**:
  - Published recall and reciprocal-rank figures carry zero approximate-index variance, so the reproducibility gate is trivially satisfiable on the dense arm
  - The cost of approximation becomes a reported finding instead of an invisible confound
  - An index rebuild cannot move a published number
  - Still demonstrates index tuning, since the serving path is tuned and its parameters recorded
- **Cons**:
  - Two code paths, which can drift if the flag is allowed to control anything beyond index usage
  - One extra evaluation run to produce the delta

### Option B: Approximate everywhere, tuned

One path. Search breadth raised well above the fetch depth, build breadth raised, parallel build workers disabled for determinism, and index parameters recorded in the results manifest.

- **Pros**:
  - Single code path with no drift risk
  - Evaluated path is exactly the served path
  - Simplest mental model
- **Cons**:
  - Published retrieval numbers depend on a documented non-deterministic structure, which is what the reproducibility gate promises they do not
  - A rebuild that shifts a metric cannot be attributed to code or to the index
  - Approximation cost stays invisible rather than measured

### Option C: Exact everywhere

No approximate index anywhere; every query scans all vectors.

- **Pros**:
  - Fully deterministic end to end
  - One path, simplest possible reproducibility story
- **Cons**:
  - Never demonstrates approximate-index tuning, which a reader will expect from a project built on a vector-enabled database
  - Leaves an obvious scaling question unanswered

## Decision Outcome

Chosen option: **Exact for evaluation, approximate for serving, delta published** — the evaluation harness uses exact vector search so published retrieval numbers carry no approximate-index variance; the serving path uses a tuned graph index, and the recall difference between them is published as an additional ablation arm.

Reproducibility of published numbers is a release gate, and the graph index is documented as non-deterministic under rebuild — so evaluating through it would make recall and mean reciprocal rank depend on a structure the gate promises they do not depend on. Exact scan costs nothing meaningful at this corpus size, which removes the usual reason to accept that variance. Keeping a tuned index on the serving path preserves the demonstration of approximate-index competence that Option C forfeits, and turning the gap between the two paths into a published ablation arm converts the drift risk into a measured, reported quantity rather than an unmeasured one.

## Consequences

### Positive

- Recall and mean reciprocal rank measure the fusion and reranking design itself, with the dense arm contributing no variance.
- The ablation table gains a genuinely informative row quantifying what approximation costs at the chosen search breadth.
- Rebuilding the serving index cannot invalidate a published evaluation number.

### Negative

- Two retrieval configurations exist and must be prevented from diverging.
- Evaluation runs are slower than they would be through the index, though still fast at this corpus size.

### Neutral

- The configuration flag is constrained to control index usage only; filters, fusion, fetch depth, and reranking are shared by both paths.
- Serving index parameters — search breadth, build breadth, connectivity, and build worker count — are recorded in the results manifest alongside the delta.
- Filtered vector search requires an iterative-scan setting or per-filter partial indexes, since the index otherwise applies filters after selecting candidates and can return fewer rows than requested.

## Links

- [specs/prd.md](../prd.md)
- PRD capabilities: CAP-003, CAP-009
- [ADR-0002: Postgres as the Single Datastore](0002-postgres-as-the-single-datastore.md)
