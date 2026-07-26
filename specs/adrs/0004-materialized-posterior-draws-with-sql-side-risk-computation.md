---
adr_id: ADR-0004
status: accepted
date: 2026-07-25
tags: [modeling, storage, postgres]
supersedes: []
superseded_by: ""
related_artifacts: ["specs/prd.md", "CAP-005", "CAP-006", "CAP-007", "CAP-012", "ADR-0002", "ADR-0003"]
deciders: ["Project owner", "Solution architect"]
---

# ADR-0004: Materialized Posterior Draws with SQL-Side Risk Computation

## Status

Accepted.

## Context

The coordinator-facing interface needs three things from each open purchase-order line: median and eightieth-percentile delivery durations, the probability of missing a need-by date, and a plotted posterior distribution in the detail view. Forecasts are fitted offline and served from stored results (see [ADR-0003](0003-offline-modeling-package-instead-of-a-model-service.md)), so the question is what exactly gets stored and where the risk arithmetic happens.

Two downstream behaviours constrain the answer. Coordinators may override a line's criticality, and need-by dates can change; both must alter the ranked worklist without refitting the model. Separately, the reproducibility gate requires the stored artifact to be hashable so a published number can be tied to a specific fit.

At 200 lines and roughly four thousand draws each, every candidate representation is small, so the choice turns on contract clarity and read-path behaviour rather than storage cost.

## Decision Drivers

- Recomputing probability of lateness on a changed need-by date or criticality without a refit
- A canonical stored artifact that can be hashed and tied to a fit run
- Supporting a distribution plot in the detail view, not just summary statistics
- Read-path predictability — no per-request sort over unnested rows
- Keeping all probability arithmetic inside the deterministic-computation boundary

## Considered Options

### Option A: Sorted draw array per line plus a derived day-grid survival array

Each line stores one sorted array of posterior predictive draws as the canonical, checksummable artifact. The same offline job derives a per-line day-grid survival array used as the read path, so probability of lateness is an array index and the percentiles are inverse-cumulative lookups.

- **Pros**:
  - One row per line is a natural hashable unit that ties cleanly to a fit run
  - Probability of lateness is a constant-time array index at the offset between need-by and the anchor date, so overrides recompute instantly
  - Percentiles are inverse-cumulative lookups with no sort at read time
  - The draw array feeds the detail-view distribution plot directly
  - Expected days late is a bounded sum over the array tail
- **Cons**:
  - Two representations of the same posterior must be kept consistent by the job that writes them
  - Array-based arithmetic in SQL is less obvious to a reader than row-based aggregation

### Option B: Normalized draws table

One row per line-and-draw pair, with percentiles computed by continuous-percentile aggregation at read time.

- **Pros**:
  - Conventional relational shape that any reader will recognize immediately
  - Standard aggregate functions do the work with no custom array handling
- **Cons**:
  - Forces a sort on every read
  - The continuous-percentile aggregate has no partial mode, so it never parallelizes
  - Eight hundred thousand rows to represent two hundred forecasts is a poor hashable unit
  - Probability of lateness becomes a filtered count over the full set on every request

### Option C: Summary quantiles only

Store just the median, the eightieth percentile, and a precomputed probability of lateness.

- **Pros**:
  - Smallest possible footprint
  - Simplest schema
- **Cons**:
  - Removes the posterior distribution plot from the detail view, dropping a required capability
  - Probability of lateness cannot be recomputed against a changed need-by date without a refit, breaking the override behaviour
  - Discards the information that makes the forecast defensible

## Decision Outcome

Chosen option: **Sorted draw array per line plus a derived day-grid survival array** — it is the only option that satisfies the override behaviour and the reproducibility gate at the same time. Keeping the full posterior means a changed need-by date or criticality value is answered by re-reading the stored artifact rather than by re-running the model, and keeping it as one row per line means that artifact is a natural checksummable unit that a fit run's manifest can point at. Option C fails outright on both counts: it cannot answer a changed need-by date without a refit, and it discards the distribution the detail view is required to plot.

Option B keeps the same information but chooses the wrong shape for it. Every read pays a sort, the continuous-percentile aggregate has no partial mode so it never parallelizes, and probability of lateness degrades into a filtered count over the full set on each request — all to represent two hundred forecasts as eight hundred thousand rows that hash as a set rather than as a record. The derived day-grid survival array removes that cost entirely: probability of lateness becomes an index at the offset between the need-by date and the anchor date, and the percentiles become inverse-cumulative lookups. Both stay in SQL, which keeps the arithmetic inside the deterministic-computation boundary established by [ADR-0002](0002-postgres-as-the-single-datastore.md). The accepted cost is that one posterior now has two on-disk representations that a single job must keep in step.

## Consequences

### Positive

- Criticality overrides and need-by changes reorder the worklist immediately, with no model run in the loop.
- Each line's posterior is one checksummable row that ties directly to a fit run's manifest.
- The detail-view distribution plot reads the same artifact the risk figures are computed from, so the two can never disagree.
- Read cost is bounded and predictable — no sort, no full-set scan.

### Negative

- The canonical draw array and the derived survival array must be written by the same job in the same transaction, or they can drift.
- Array-indexed probability arithmetic in SQL requires deliberate test coverage and a clearly documented anchor-date convention.

### Neutral

- One percentile convention is chosen and stated once; published quantiles are rounded to whole days so floating-point drift cannot move a published number.
- Forecast selection uses an explicit active-run pointer rather than most-recent-timestamp ordering.

## Links

- [specs/prd.md](../prd.md) — CAP-005 (Probabilistic Delivery Forecast), CAP-006 (Risk-Ranked Coordinator Worklist), CAP-007 (Forecast Explanation & Source Traceability), CAP-012 (Criticality Override)
- [ADR-0002](0002-postgres-as-the-single-datastore.md) — Postgres as the Single Datastore
- [ADR-0003](0003-offline-modeling-package-instead-of-a-model-service.md) — Offline Modeling Package Instead of a Model Service
