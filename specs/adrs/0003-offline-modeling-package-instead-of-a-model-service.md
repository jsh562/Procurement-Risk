---
adr_id: ADR-0003
status: superseded
date: 2026-07-25
tags: [modeling, boundaries, reproducibility]
supersedes: []
superseded_by: "ADR-0011"
related_artifacts: ["specs/prd.md", "CAP-001", "CAP-005", "CAP-009"]
deciders: ["Project owner", "Solution architect"]
---

# ADR-0003: Offline Modeling Package Instead of a Model Service

## Status

Superseded by [ADR-0011](../adrs/0011-model-owned-one-shot-jobs-invoked-as-console-entry-points.md), on the invocation clause only. The offline-package decision below stands: the modeling boundary is a package of discrete commands, never a service; no posterior is sampled at request time; the serving image never installs the modeling stack; and the contract to the serving boundary is the database. ADR-0011 replaces only "invoked as one-shot container jobs under a non-default profile" with invocation as console entry points through the modeling entry's own environment.

## Context

The forecast is a hierarchical Bayesian model over vendor and category with partial pooling, right-censoring for still-open orders, and covariates for lifecycle state, days in state, and approval-cycle count. Fitting it requires Markov chain Monte Carlo sampling, which is slow and memory-hungry, and drags in a numerical stack including a compiler toolchain and linear-algebra libraries.

The product requirements fix two constraints as release-blocking: posteriors are fitted offline and served from stored results, and request-time components must fit a small hosted instance's compute budget.

A decision is needed before any scaffolding because retrofitting an offline boundary onto a design that sampled on demand is a rearchitecture rather than a refactor, and because the choice determines whether the modeling stack ever enters the serving image.

## Decision Drivers

- Making the no-request-time-sampling constraint structural rather than a rule someone must remember
- Keeping the modeling toolchain out of the request-serving image to hold the compute envelope
- Reproducibility — a job that runs to completion and emits a versioned artifact is auditable in a way a live endpoint is not
- Single-developer operational capacity

## Considered Options

### Option A: Offline package with command-line entrypoints

A Python package exposing discrete commands (generate data, fit, evaluate, seed) invoked as one-shot container jobs under a non-default profile. Output is written to the database as a versioned run. No process runs between invocations.

- **Pros**:
  - The constraint becomes structural: there is no endpoint that could sample on demand
  - The serving image never installs the modeling stack, which is the dominant lever on the compute envelope
  - Each run emits a manifest recording code revision, input hash, seeds, library versions, and artifact hash — exactly what the reproducibility gate needs
  - Jobs are re-runnable and independently testable
- **Cons**:
  - Refreshing a forecast is an operator action rather than an API call
  - Requires a documented job-invocation path so a newcomer knows how to regenerate results

### Option B: Third HTTP service

The modeling boundary runs as its own long-lived service that the backend calls.

- **Pros**:
  - Clean network interface between boundaries
  - Independently deployable and scalable
- **Cons**:
  - A live endpoint that can sample is precisely the temptation the constraint exists to remove
  - The service idles at request time doing nothing, since nothing may be sampled on demand
  - A third container to run locally and provision when hosted, for no request-time work

### Option C: Library imported by the backend

The backend imports the modeling code directly and invokes fitting in-process.

- **Pros**:
  - Fewest moving parts
  - No inter-process contract to maintain
- **Cons**:
  - Pulls the sampling stack, tensor library, and linear-algebra dependencies into the request-serving image, breaking the compute-envelope constraint
  - Makes serving/modeling dependency isolation impossible to assert mechanically
  - Places sampling one function call away from a request handler

## Decision Outcome

Chosen option: **Offline package with command-line entrypoints** — it is the only option that turns the release-blocking "no request-time sampling" constraint into a property of the topology rather than a rule engineers must remember. Because no modeling process is running when a request arrives, no request path can reach a sampler; because the serving image never installs the modeling stack, the compute envelope holds by construction and can be asserted mechanically. The one-shot job model also produces exactly the artifact the reproducibility gate requires: a run that terminates and emits a versioned, manifest-bearing result. Option B pays for a third container that does no request-time work while retaining a live endpoint capable of the exact behavior the constraint forbids; Option C violates the compute-envelope constraint outright by pulling the sampling stack into the request-serving image.

## Consequences

### Positive

- No request path can reach a sampler, because no sampler is running — the constraint is enforced by topology, not discipline.
- The serving image's dependency list can be asserted to contain no modeling packages, turning an architectural rule into a test.
- Every fit emits a run manifest with code revision, input hash, seeds, and library versions, which is the substrate the reproducibility gate checks against.

### Negative

- Producing a fresh forecast requires running a job; there is no self-service refresh through the interface.
- The contract between the modeling boundary and the serving boundary is a database schema, so a schema version must be carried and checked or a stale reader will misread a new artifact.

### Neutral

- Job containers are declared under a non-default profile so ordinary local startup brings up only the persistent services.
- The backend must never declare a startup dependency on a job container.

## Links

- [specs/prd.md](../prd.md) — CAP-001 (Auditable Data Foundation), CAP-005 (Probabilistic Delivery Forecast), CAP-009 (Evaluation & Calibration Evidence)
