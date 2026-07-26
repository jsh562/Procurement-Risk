---
adr_id: ADR-0010
status: accepted
date: 2026-07-25
tags: [layout, monorepo, llm, governance]
supersedes: ["ADR-0001"]
superseded_by: ""
related_artifacts: ["specs/prd.md", "specs/adrs/0001-monorepo-source-layout-under-src.md", "specs/adrs/0007-single-traced-language-model-invocation-boundary.md", "specs/adrs/0003-offline-modeling-package-instead-of-a-model-service.md", "CAP-002", "CAP-008", "E001", "E004"]
deciders: ["Project owner", "Solution architect"]
---

# ADR-0010: Source Layout with a Shared Gateway Package

## Status

Accepted. Supersedes [ADR-0001](../adrs/0001-monorepo-source-layout-under-src.md).

`/src` holds four entries — the three original boundaries plus a minimal shared gateway package that both Python boundaries depend on, so exactly one module in the repository imports the model-provider client.

## Context

ADR-0001 established three boundaries under `/src` — `web`, `api`, `model` — each with an independent dependency manifest, and that independence is what makes serving/modeling isolation mechanically assertable. Specifying the foundation epic surfaced a conflict ADR-0001 could not have anticipated.

Three separately-recorded facts cannot all hold at once. The architecture has both the request-serving boundary and the offline jobs making traced model-provider calls: chat answers requests, and document extraction runs as an offline job. ADR-0007 permits exactly one module *in the repository* to import the provider client. And the three-boundary layout, read strictly, forbids either Python boundary depending on the other, because a dependency edge would pull one resolution graph into the other.

With no shared location, the only ways to satisfy two of the three were to let one Python boundary depend on the other, or to duplicate the gateway. The first leaves an offline environment carrying request-serving dependencies it never imports and makes the boundaries' independence directional rather than real. The second puts the traced path in two places, which is precisely the outcome the single-invocation-path decision exists to prevent — a field added to the invocation record in one copy and not the other produces silently divergent trace data discovered long after the divergence.

A decision is needed now because the foundation epic cannot scaffold a layout that contradicts an accepted record.

## Decision Drivers

- Preserving exactly one model-provider import site in the repository, without duplication
- Keeping the request-serving image free of the modeling stack — the constraint the original layout existed to protect
- Keeping each boundary's dependency resolution genuinely independent rather than directionally coupled
- Changing the accepted layout as little as the conflict requires

## Considered Options

### Option A: Four entries — three boundaries plus a shared gateway package

Add a minimal fourth entry under `/src` containing only the provider client and its validation and tracing wrapper. Both Python boundaries declare it as a dependency; neither depends on the other.

- **Pros**: Exactly one module in the repository imports the provider client, so the single-path decision survives verbatim; the gateway package carries only the provider SDK and validation — no web framework, no modeling stack — so both environments stay minimal and the serving image constraint is untouched; neither Python boundary depends on the other, so their resolutions remain genuinely independent rather than directionally coupled; the traced path, its cost accounting, and its schema validation exist once and change once.
- **Cons**: `/src` now holds four entries rather than three, so the layout is no longer describable as exactly three boundaries; a third Python dependency manifest to maintain; the gateway becomes a shared component whose interface changes affect both consumers.

### Option B: One-directional dependency from the modeling boundary to the request-serving boundary

The gateway stays inside the request-serving boundary; the modeling boundary declares that boundary as a dependency.

- **Pros**: No change to the entry count under `/src`; preserves one gateway module; the direction is the safe one — the serving image never acquires modeling packages.
- **Cons**: The offline jobs environment acquires the entire request-serving dependency graph, including a web framework it never imports; boundary independence becomes directional rather than actual, weakening the property the original decision was written to establish; couples the offline pipeline to a boundary whose reason for existing is serving requests.

### Option C: One gateway per Python boundary

Each Python boundary holds its own provider wrapper, with the import contract scoped per boundary instead of repository-wide.

- **Pros**: No cross-boundary dependency at all; no change to the entry count under `/src`; each boundary stays fully self-contained.
- **Cons**: Two copies of the traced path, contradicting the single-invocation-path decision and requiring it to be superseded as well; cost accounting, schema validation, and redaction must be kept identical by discipline, which is the enforcement model this project rejects everywhere else; a field added to one copy and not the other yields divergent trace data discovered long after the divergence.

## Decision Outcome

Chosen option: **Four entries — three boundaries plus a shared gateway package** — it is the only option that satisfies all three previously-recorded constraints at once, and it does so with the smallest change to the accepted layout. Option B keeps the entry count but buys it by giving the offline environment an entire web-serving dependency graph it never imports, converting boundary independence into a directional property and weakening exactly what ADR-0001 was written to establish. Option C keeps both the entry count and boundary self-containment, but only by duplicating the traced invocation path — which would require superseding ADR-0007 as well and would leave trace-data consistency to discipline rather than enforcement. The chosen option's cost is one additional entry under `/src` and a third Python manifest; in exchange the single-provider-import contract remains enforceable verbatim and the serving image constraint is untouched.

## Consequences

### Positive

- Exactly one module in the repository imports the model-provider client, so the single-invocation-path decision needs no change.
- Tracing, cost accounting, schema validation, and credential redaction are implemented once and cannot drift between consumers.
- Both Python boundaries keep genuinely independent resolutions; neither depends on the other.
- The gateway package excludes both the web framework and the modeling stack, so the request-serving image constraint is unaffected.

### Negative

- The layout can no longer be described as exactly three boundaries; any rule or test asserting a three-entry count under `/src` must be restated.
- A third Python dependency manifest and lockfile to maintain.
- The gateway is now a shared component: an interface change affects the request-serving boundary and the offline pipeline together.

### Neutral

- The gateway package is deliberately minimal — provider client, schema validation, invocation recording — and acquires no framework or modeling dependency.
- The import contract remains repository-wide rather than per-boundary, since the single permitted importer now lives somewhere both consumers can reach.
- The three original boundaries, their independent manifests, and the `/src` root rule are otherwise unchanged from ADR-0001.

## Links

- [specs/prd.md](../prd.md) — product requirements document
- [ADR-0001](../adrs/0001-monorepo-source-layout-under-src.md) — the superseded source-layout record; its three boundaries, per-boundary manifests, and `/src` root rule carry forward unchanged
- [ADR-0007](../adrs/0007-single-traced-language-model-invocation-boundary.md) — the single traced invocation boundary this layout preserves without amendment
- [ADR-0003](../adrs/0003-offline-modeling-package-instead-of-a-model-service.md) — establishes the offline modeling package that is the gateway's second consumer
- CAP-002 — Document Understanding & Extraction (offline extraction jobs invoke the provider through the shared gateway)
- CAP-008 — Grounded Question Answering (request-serving chat invokes the provider through the same gateway)
- E001 — foundation epic that scaffolds the four-entry layout
- E004 — epic consuming the shared gateway from the offline pipeline
