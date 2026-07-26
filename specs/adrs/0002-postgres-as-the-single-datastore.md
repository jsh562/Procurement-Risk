---
adr_id: ADR-0002
status: accepted
date: 2026-07-25
tags: [storage, retrieval, postgres]
supersedes: []
superseded_by: ""
related_artifacts: ["specs/prd.md", "CAP-003", "CAP-004", "CAP-005", "CAP-006"]
deciders: ["Project owner", "Solution architect"]
---

# ADR-0002: Postgres as the Single Datastore

## Status

Accepted.

## Context

Procurement Risk Copilot needs sparse full-text retrieval, dense vector retrieval, relational storage for procurement lifecycle records and resolved cross-document entities, storage for per-line posterior predictive draws, and a durable record of every language-model invocation. A conventional design would reach for a dedicated vector database, a separate search engine, and an observability backend.

The corpus is small and fixed — roughly 5,000 to 15,000 chunks, 200 purchase-order lines, five projects, twelve vendors — with effectively one concurrent user. The request-time compute envelope is a small hosted instance.

The hybrid retrieval design fuses a sparse arm and a dense arm using Reciprocal Rank Fusion at k=60, and that fusion is part of the deterministic-computation boundary the product requires: ranking must be testable code, not model output.

A decision is needed now because it determines the schema, the deployment topology, and whether fusion is a database concern or an application concern.

## Decision Drivers

- Keeping ranking inside the deterministic-computation boundary as one testable artifact
- Operational simplicity within a small compute envelope and a single-developer capacity
- Scale genuinely served by one relational engine at this corpus size
- Ability to recompute risk figures at read time without re-running the model
- Minimizing the number of services a hosted deployment must provision

## Considered Options

### Option A: Single Postgres instance with vector and full-text extensions

Postgres holds everything: tsvector full-text, pgvector dense search, relational procurement data, posterior draws as arrays, and an invocation-trace table. Fusion is one SQL statement with two common table expressions joined on chunk identity.

- **Pros**:
  - Fusion is a single testable SQL artifact inside the deterministic boundary, and one round trip
  - One service to run locally and one to provision when hosted
  - Risk figures recompute at read time against stored draws with no refit
  - Traces live beside the data they describe, so surfacing them costs nothing extra
  - Comfortably sized for a corpus of this scale
- **Cons**:
  - Postgres full-text ranking has no corpus inverse-document-frequency term, so the sparse arm's internal ordering is weaker than a true BM25 implementation
  - Puts ranking logic in SQL, which needs its own test discipline
  - Would not be the right answer at a materially larger corpus

### Option B: Dedicated vector database alongside Postgres

A purpose-built vector store for dense retrieval; Postgres retains relational data.

- **Pros**:
  - Best-in-class vector search features and scaling headroom
  - Clean separation of concerns
- **Cons**:
  - Fusion moves into application code across two round trips, splitting the ranking artifact
  - A second stateful service to run locally and provision when hosted
  - Buys scaling headroom the project will never use

### Option C: Postgres plus a separate search engine for the sparse arm

A dedicated search engine supplies true BM25 ranking; Postgres supplies vectors and relational data.

- **Pros**:
  - Genuine BM25 with corpus statistics, strengthening the sparse arm
  - Mature relevance tuning surface
- **Cons**:
  - Third stateful service for one retrieval arm
  - Reciprocal Rank Fusion consumes rank rather than score, which already blunts the ranking weakness this would fix
  - Substantial operational cost for a small measurable gain at this scale

## Decision Outcome

Chosen option: **Single Postgres instance with vector and full-text extensions** — one Postgres instance holds document chunks, full-text indexes, dense vectors, resolved entities, posterior draws, and model-invocation traces; hybrid fusion runs as a single SQL statement.

At this corpus size a single relational engine genuinely serves every access pattern the product has, so the alternatives buy scaling headroom that will never be used at the cost of additional stateful services. Co-locating both retrieval arms lets Reciprocal Rank Fusion be expressed as one SQL statement — a single artifact that sits unambiguously inside the deterministic-computation boundary and can be unit-tested, rather than application glue spanning two round trips. The sparse arm's missing corpus inverse-document-frequency term is the real cost, and it is materially blunted by the fact that Reciprocal Rank Fusion consumes rank rather than score, and further offset by field weighting.

## Consequences

### Positive

- Reciprocal Rank Fusion is one SQL statement — a single artifact that can be unit-tested and that sits unambiguously inside the deterministic-computation boundary.
- Local development and hosted deployment each provision exactly one stateful service.
- Probability of lateness recomputes at read time when a need-by date or criticality value changes, with no model refit.
- The model-invocation trace is queryable beside the records it explains, which makes surfacing it in the interface a read rather than an integration.

### Negative

- The sparse arm lacks corpus inverse-document-frequency weighting, so it cannot distinguish a common term from a rare one on its own.
- Ranking and probability logic expressed in SQL requires deliberate test coverage that application-language code would get more naturally.

### Neutral

- Field weighting is applied so that headings, part numbers, and specification section text rank above body prose, partially compensating for the missing corpus statistics.
- This decision is scale-bound and would be revisited above roughly one hundred thousand chunks.

## Links

- [specs/prd.md](../prd.md)
- PRD capabilities: CAP-003, CAP-004, CAP-005, CAP-006
