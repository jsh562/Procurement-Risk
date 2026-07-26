---
adr_id: ADR-0008
status: accepted
date: 2026-07-25
tags: [traceability, determinism, extraction, governance]
supersedes: []
superseded_by: ""
related_artifacts: ["specs/prd.md", "CAP-002", "CAP-006", "CAP-007", "ADR-0004", "ADR-0007"]
deciders: ["Project owner", "Solution architect"]
---

# ADR-0008: Deterministic Provenance and Computation Boundary

## Status

Accepted.

## Context

The product requires that every extracted value carry a page citation and a per-field confidence, and that language models extract and structure only while deterministic code performs all date arithmetic, ranking, and probability computation.

An obvious implementation would ask the model to emit a page number alongside each value, or use the provider's native document-citation feature. Both are unsatisfactory. A model-emitted page number is a value the model can get wrong, and the product's own traceability principle treats an unattributable number as a defect. The provider's native citation feature is additionally incompatible with constrained structured output — requesting both in the same call is rejected — so a design depending on it would have to abandon schema-enforced extraction.

Meanwhile the layout-aware parser already knows exactly which page each chunk came from, which makes provenance a deterministic property of ingestion rather than something to be inferred.

A decision is needed before the extraction schema is designed, because it determines whether page numbers are model output or parser output.

## Decision Drivers

- Traceability that cannot be hallucinated, since an unattributable value is defined as a defect
- Compatibility with schema-constrained extraction, which native document citations preclude
- Enforcing the determinism rule structurally rather than by review
- Keeping every published risk figure derived from testable code

## Considered Options

### Option A: Parser-derived provenance with storage-level and architecture-test enforcement

Chunks carry project, document type, specification section, and page as metadata written by the parser. Extracted values inherit the page from the chunk they came from. Citation and confidence columns are non-nullable at the storage boundary, so an uncited value cannot be persisted. Date, ranking, and probability logic live in modules an architecture test asserts are free of model-facing imports, with property-based tests over the pure scoring functions.

- **Pros**:
  - Page provenance is deterministic and cannot be hallucinated
  - Compatible with schema-constrained extraction, so both requirements are met at once
  - Non-nullable columns make an uncited value impossible to store rather than merely detectable
  - Architecture tests turn the determinism rule into a build failure instead of a review comment
  - Property-based tests over pure functions give real coverage of the scoring logic
- **Cons**:
  - Provenance is only as good as the parser's page attribution, which becomes a component to validate in its own right
  - Values synthesised across multiple chunks need an explicit multi-source provenance representation

### Option B: Model-emitted page citations

The extraction schema includes a page field the model populates alongside each value.

- **Pros**:
  - Single extraction pass with no parser metadata plumbing
  - Handles values spanning several pages naturally
- **Cons**:
  - The citation becomes a hallucinable value, contradicting the traceability principle
  - Cannot be verified without re-reading the source, so the guarantee is unenforceable
  - Wrong page numbers are the most damaging possible failure in a product whose premise is traceability

### Option C: Provider-native document citations

Use the model provider's built-in citation feature, which returns page locations for cited spans.

- **Pros**:
  - Citations produced by the provider with span-level granularity
  - No parser metadata plumbing
- **Cons**:
  - Rejected when combined with constrained structured output, so schema-enforced extraction would have to be abandoned
  - Introduces a provider-specific dependency in the extraction contract
  - Still yields model-determined rather than deterministic provenance

## Decision Outcome

Chosen option: **Parser-derived provenance with storage-level and architecture-test enforcement** — provenance is treated as a fact recorded during ingestion rather than an assertion recovered from the model afterwards. The parser already knows the page each chunk came from, so attaching project, document type, specification section, and page to the chunk turns the citation into something the model never has the opportunity to get wrong. Extracted values inherit that page from the chunk they were read out of.

Option B fails on the product's own terms. A model-emitted page number is a hallucinable value, and it is the single worst value to allow the model to hallucinate in a product whose premise is that a coordinator can follow any number back to the page it came from. Worse, it cannot be checked without re-reading the source document, which means the traceability guarantee would be an unenforceable claim rather than a property of the system.

Option C is ruled out mechanically, not on preference: requesting native document citations together with constrained structured output is rejected in the same call, so adopting it would mean giving up schema-enforced extraction — trading one required property for another. It would also bind the extraction contract to a specific provider and would still leave provenance model-determined.

Enforcement is placed where it cannot be skipped. Citation and confidence columns are non-nullable, so an uncited value is not merely detectable after the fact but impossible to persist. The determinism rule is likewise structural: date arithmetic, ranking, and probability logic live in modules an architecture test asserts contain no model-facing imports, and the pure scoring functions carry property-based tests. Both rules fail the build rather than depending on a reviewer noticing. The accepted cost is that the parser's page attribution becomes correctness-critical, and that values synthesised from more than one chunk need an explicit multi-source provenance representation rather than a single page reference.

## Consequences

### Positive

- Page citations are deterministic ingestion facts, so the traceability guarantee is genuinely enforceable.
- Schema-constrained extraction and complete citation coverage coexist, where the provider-native approach would have forced a choice.
- An extracted value without a citation and a confidence cannot be persisted at all.
- Determinism and computation-boundary rules fail the build when violated rather than surviving until review.

### Negative

- Parser page attribution becomes a correctness-critical component with its own validation burden.
- Values derived from more than one chunk require an explicit multi-source provenance representation rather than a single page reference.

### Neutral

- Extraction outputs that fail validation after one repair attempt are routed to a failure table so the field is absent rather than wrong, consistent with preferring silence over a silent mistake.
- The interface tier never queries the datastore directly, so probability and ranking logic cannot be reimplemented on the client when the hosted deployment splits the tiers.

## Links

- [specs/prd.md](../prd.md) — CAP-002 (Document Understanding & Extraction), CAP-006 (Risk-Ranked Coordinator Worklist), CAP-007 (Forecast Explanation & Source Traceability)
- [ADR-0004](0004-materialized-posterior-draws-with-sql-side-risk-computation.md) — Materialized Posterior Draws with SQL-Side Risk Computation
- [ADR-0007](0007-single-traced-language-model-invocation-boundary.md) — the traced, schema-validated invocation path whose outputs this boundary constrains
