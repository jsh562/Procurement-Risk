---
adr_id: ADR-0001
status: superseded
date: 2026-07-25
tags: [layout, monorepo, governance]
supersedes: []
superseded_by: "ADR-0010"
related_artifacts: ["specs/prd.md", "CAP-002", "CAP-005", "CAP-013"]
deciders: ["Project owner", "Solution architect"]
---

# ADR-0001: Monorepo Source Layout Under /src

## Status

Superseded by [ADR-0010](../adrs/0010-source-layout-with-a-shared-gateway-package.md).

## Context

Procurement Risk Copilot spans three toolchains: a Next.js App Router TypeScript front end, a Python FastAPI request-serving backend, and a Python modeling package. The product brief called for a monorepo with clear `web/`, `api/`, and `model/` boundaries at the repository root. The project's source-layout convention requires all project source code to reside under `/src`. These two requirements conflict directly, and the conflict must be resolved before any scaffolding is written because it determines every Dockerfile build context, the deployment root-directory setting, and every import path in the repository. A layout violation is treated as a project-instructions violation at CRITICAL severity during quality control, so an unresolved conflict blocks release rather than merely creating churn.

## Decision Drivers

- Compliance with the mandatory `/src` source-layout convention (violations are CRITICAL)
- Preservation of the three explicit boundaries the product brief asks for
- Independent dependency graphs per boundary so the serving image never inherits the modeling toolchain
- Deployability without restructuring when the hosted demo is built
- Familiarity to a reader who has seen other monorepos

## Considered Options

### Option A: /src with three package directories

Three boundaries nested one level deeper: `/src/web`, `/src/api`, `/src/model`, each with its own dependency manifest and Dockerfile.

- **Pros**: Satisfies the `/src` convention and the three-boundary requirement simultaneously; each boundary keeps an independent dependency manifest, which is what actually enforces serving/modeling isolation; deployment platforms accept a root-directory setting, so the extra path segment costs nothing at deploy time; boundary names remain visible and self-documenting.
- **Cons**: One extra path segment on every import and build context; Next.js App Router lands at `/src/web/app` rather than the more familiar top-level `app/`; slightly unusual for readers expecting root-level workspace directories.

### Option B: Root-level web/, api/, model/

Three directories at the repository root exactly as the product brief describes.

- **Pros**: Most conventional monorepo shape; shortest paths; matches the brief verbatim.
- **Cons**: Violates the mandatory `/src` source-layout convention; escalates as a CRITICAL project-instructions violation during quality control; blocks release for a purely cosmetic gain.

### Option C: Flat /src tree with internal package prefixes

A single `/src` tree with package-level rather than directory-level separation between concerns.

- **Pros**: Complies with the `/src` convention; fewest top-level directories.
- **Cons**: Erases the explicit web/api/model separation the brief requires; mixes a Node and a Python toolchain in one directory, complicating tooling configuration; makes serving/modeling dependency isolation harder to enforce mechanically.

## Decision Outcome

Chosen option: **/src with three package directories** — it is the only option that satisfies both the mandatory `/src` source-layout convention and the product brief's three-boundary requirement without residual conflict. Option B trades a CRITICAL compliance violation for cosmetic path brevity, and Option C buys compliance by discarding the boundary separation that makes serving/modeling dependency isolation enforceable. The cost of the chosen option is one extra path segment, which deployment platforms absorb through a per-service root-directory setting.

## Consequences

### Positive

- Satisfies both the source-layout convention and the three-boundary requirement with no residual conflict.
- Per-boundary dependency manifests make the serving/modeling isolation rule mechanically testable rather than a convention someone must remember.
- Deployment requires only a root-directory setting per service; no restructuring.

### Negative

- Every import path and Docker build context carries an extra `/src` segment.
- The Next.js application root sits at `/src/web/app`, which is one level deeper than most readers expect.

### Neutral

- Container orchestration for local development sets its build contexts to the three package directories rather than the repository root.

## Links

- [specs/prd.md](../prd.md) — product requirements document
- CAP-002 — Document Understanding & Extraction (served from `/src/api`, modeled in `/src/model`)
- CAP-005 — Probabilistic Delivery Forecast (modeling boundary at `/src/model`)
- CAP-013 — Publicly Hosted Demonstration (per-service root-directory deployment setting)
