---
adr_id: ADR-0007
status: accepted
date: 2026-07-25
tags: [llm, observability, validation, reproducibility]
supersedes: []
superseded_by: ""
related_artifacts: ["specs/prd.md", "CAP-002", "CAP-008", "CAP-009", "ADR-0006"]
deciders: ["Project owner", "Solution architect"]
---

# ADR-0007: Single Traced Language-Model Invocation Boundary

## Status

Accepted.

## Context

Two product requirements govern every language-model call in this system. The first is that each call must be traced with its request, its response, token counts, latency, and cost. The second is that every model output must be validated against a schema with repair-or-fail semantics, so that no unvalidated value ever reaches storage or the interface.

Stated as prose, both requirements are aspirations. They erode the first time someone adds a call in a hurry, and they erode silently — an untraced call leaves no trace by definition, and an unvalidated field looks exactly like a validated one once it is written to a row. Requirements of this shape need a structural form, or the coverage figures reported against them are claims rather than measurements.

A related requirement complicates the picture. Published evaluation numbers must reproduce from a clean checkout, but hosted model decoding is not deterministic. A zero-temperature setting is not a determinism guarantee — it constrains the sampling distribution without constraining the serving stack behind it — and the auxiliary sampling parameters that once approximated determinism are no longer accepted on current models. So the reproducibility gate cannot be satisfied by configuring the provider. It has to be satisfied somewhere else.

Model selection sits inside the same decision. Extraction over specification documents is an offline batch over a fixed corpus, not an interactive per-request cost. Accuracy therefore dominates and total spend is small enough not to be the binding constraint — and structured extraction against a strict schema with per-field confidence is precisely the task where capability differences between model tiers are visible rather than marginal.

The decision is needed before any provider code is written. Retrofitting a single invocation path onto call sites that are already scattered is far harder than starting with one, and the enforcement mechanism has to exist before there is anything to enforce it against.

## Decision Drivers

- Making complete tracing and complete schema validation structurally guaranteed rather than conventional
- Reproducing published extraction numbers from a clean checkout despite non-deterministic decoding
- Extraction accuracy on strict schemas with per-field confidence, at a corpus size where cost is not the binding constraint
- Keeping the invocation record queryable, so it can be surfaced in the product rather than buried in logs

## Considered Options

### Option A: One traced module enforced by an import contract, with hash-keyed response fixtures

A single module wraps the provider client and is the only module in the repository permitted to import it. That restriction is expressed as an import-linter contract that fails the build when any other module reaches the provider directly. Every call through the module is recorded with model identity, token counts, latency, computed cost, and a validation outcome of valid, repaired, or failed. Responses are cached under a key derived from a hash of prompt, model, and parameters, and the resulting fixtures are committed so the evaluation harness can replay them; both replayed and live extraction numbers are published.

- **Pros**:
  - Tracing and validation coverage are enforced by the build, so a violating change cannot merge — the coverage target becomes a measured property rather than an assertion
  - The evaluation harness reproduces extraction results with no network access and no credential
  - Publishing replayed and live numbers side by side discloses the decoding variance rather than concealing it
  - The invocation record is a queryable table, so surfacing it in the interface is a read rather than an integration
  - Adopting standard generative-AI telemetry attribute names costs nothing at authoring time and keeps the record exportable to a tracing backend later
- **Cons**:
  - Fixtures must be regenerated when a prompt changes, or the cache silently serves stale responses
  - Committed fixtures add repository weight
  - One module becomes a coordination point for all model-facing work

### Option B: Convention-only wrapper

A shared helper exists and contributors are expected to use it, with code review as the enforcement mechanism.

- **Pros**:
  - No build tooling to configure
  - No import restrictions to work around
- **Cons**:
  - Coverage is a claim rather than a measurement — exactly the kind of assertion an evaluator discounts
  - The first direct provider import silently creates an untraced, unvalidated path, and nothing signals its existence
  - No mechanism prevents regression once the convention slips

### Option C: Traced wrapper without response caching

Enforce the single traced path through an import contract, but do not cache or commit model responses.

- **Pros**:
  - Simpler; no fixture lifecycle to manage
  - Always exercises the live provider, so drift in provider behavior is visible immediately
- **Cons**:
  - Extraction numbers cannot be reproduced from a clean checkout, directly contradicting the reproducibility release gate
  - Leaves a known reproducibility loophole open immediately after the reranking decision deliberately closed the equivalent one

## Decision Outcome

Chosen option: **One traced module enforced by an import contract, with hash-keyed response fixtures** — it is the only option that converts the two coverage requirements from promises into properties of the build. Because exactly one module may import the provider client, and because that module traces and validates unconditionally, complete coverage is not something the team maintains through discipline; it is something the repository cannot be configured to violate. A contributor who adds a direct provider call does not create an untraced path — they fail the build.

The response cache carries the reproducibility requirement, which the provider itself cannot. Since decoding cannot be pinned by configuration, the only available point of determinism is the record of what the model actually returned. Keying that record by a hash of prompt, model, and parameters makes replay exact and makes the invalidation condition explicit: change the prompt and the key changes with it. Publishing both the replayed numbers and a live run alongside them means the decoding variance is disclosed as a measurement rather than hidden behind a single figure.

Option B is rejected on the same ground that its only advantage rests on. It saves the cost of configuring one build check and pays for that saving with the credibility of every coverage number the project reports. A stated one-hundred-percent tracing rate with no enforcing mechanism is indistinguishable from an unverified guess, and the failure mode is invisible by construction.

Option C is rejected because it accepts the enforcement discipline and then declines the benefit that motivated it. It leaves published extraction numbers irreproducible from a clean checkout while the reranking decision has already paid real memory and latency costs to close exactly that loophole for retrieval. Holding one half of the pipeline to a reproducibility gate and exempting the other is not a simplification; it is an inconsistency that an evaluator would find first.

Model selection follows from the workload shape. Extraction runs offline over a fixed corpus, so the higher-capability tier is chosen: accuracy on strict schemas with per-field confidence is where tier differences actually show, and total spend at this corpus size is not the binding constraint. The traced record stores model identity and a price-table version alongside token counts, so the cost of that choice remains recomputable if the selection is revisited.

The accepted price is a fixture lifecycle and a single point of coordination. Both are managed rather than avoided — regeneration is tied to the prompt hash so staleness cannot pass unnoticed, and the coordination cost of one module is the direct consequence of the property that makes the decision work.

## Consequences

### Positive

- Tracing and validation coverage are enforced at build time, so the stated one-hundred-percent targets are structural facts rather than claims.
- Extraction results reproduce from a clean checkout, closing the decoding loophole that the local reranking decision left open.
- Publishing replayed and live numbers side by side discloses decoding variance instead of concealing it.
- Costs are recomputable after the fact, because the record stores token counts, model identity, and a price-table version rather than only a derived figure.

### Negative

- Prompt changes invalidate fixtures, and a stale fixture would silently serve an old response — regeneration must be tied to the prompt hash.
- Committed fixtures increase repository size.
- All model-facing work funnels through one module, which becomes a merge coordination point.

### Neutral

- The provider credential is redacted by the traced path itself, making redaction a property of the boundary rather than a caller's responsibility.
- Repair is attempted at most once before failing closed; failures are recorded so the affected field is absent rather than wrong.
- The repaired-response rate is published as a quality signal in its own right.

## Links

- [specs/prd.md](../prd.md) — CAP-002 (Document Understanding & Extraction), CAP-008 (Grounded Question Answering), CAP-009 (Evaluation & Calibration Evidence)
- ADR-0006 — closes the equivalent reproducibility loophole for reranking; this ADR applies the same gate to model invocation
