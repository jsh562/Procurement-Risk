---
adr_id: ADR-0018
status: accepted
date: 2026-07-27
tags: [modeling, storage, postgres, evaluation]
supersedes: []
superseded_by: ""
related_artifacts: ["specs/prd.md", "CAP-005", "CAP-006", "CAP-009", "CAP-014", "ADR-0002", "ADR-0004", "E007", "E010", "E012", "E014", "E019", "specs/00007-delivery-forecast-model/spec.md", "specs/00003-core-data-schema/data-model.md"]
deciders: ["Project owner", "Solution architect"]
---

# ADR-0018: Two Anchor-Distinguished Posterior Populations in Two Tables

## Status

Accepted. Extends [ADR-0004](0004-materialized-posterior-draws-with-sql-side-risk-computation.md), which remains accepted and unchanged: it governs the open-line population and the representation both populations share.

## Context

[ADR-0004](0004-materialized-posterior-draws-with-sql-side-risk-computation.md) fixed one materialized posterior store — a canonical sorted draw array per line plus a derived day-grid survival array — and E003 delivered it as `line_posterior`. That decision contemplated a single population: lines open at the run's as-of date, whose grid is anchored at `forecast_run.as_of_date`, documented in E003's data model as "the single anchor for every line's grid in this run".

E007 now needs a second population that ADR-0004 did not contemplate. To give the evaluation harness something to score, the fit must store gradeable predictions for **held-out lines that already delivered** — lines that finished *before* the run's as-of date. Where those predictions live is not a detail internal to E007, because it changes what every downstream reader must know about where forecasts are: E010 ranks the worklist from them, E012 plots them, E014 grades them, and E019 derives vendor summaries from them.

The delivered store cannot hold the second population, and not by preference — by three delivered constraints. `line_posterior.survival` is `NOT NULL`, so a row whose grid has no meaningful position under the run anchor cannot omit it. `ck_line_posterior__draws_non_negative` rejects the negative duration a pre-as-of delivery carries when measured from the run anchor. And `ck_schema_constants__anchor_convention CHECK (anchor_date_convention = 'run_as_of_date')` pins the convention on the singleton row that `/src/api` reads and serves, so widening what an anchor may be is a change to a published contract, not an internal one.

The two populations also carry genuinely different quantities, which is the deeper reason they are not interchangeable rows. An open line stores the **conditional remaining** duration given survival to the as-of date. A held-out delivered line stores the **total** duration from its own order date — the only quantity its observed outcome can be graded against.

That difference is invisible to the read path. E010 computes `1 - survival[d - as_of_date]` and has no column to consult that would tell an as-of-anchored row from an order-date-anchored one. A single mixed table would therefore mis-score every held-out row silently, with nothing anywhere reporting a problem.

A decision is needed now because E007 is claiming a migration block and the shape of the store is the thing every consuming epic reads against.

## Decision Drivers

- The evaluation harness must have gradeable stored predictions for lines whose outcome is already observed
- The existing read contract, `1 - survival[d - as_of_date]`, must remain literally true rather than true-by-convention, with no class of row that silently violates it
- Two different duration semantics must be distinguishable by a reader from the record itself, never inferred
- Posterior draws stay in the single Postgres instance, and the write-atomicity guarantee must have a mechanism that actually exists
- The delivered `line_posterior` contract and the published anchor convention that the serving boundary reads must not change
- Anchor correctness carried by a constraint rather than by a comment or a test

## Considered Options

### Option A: Two tables distinguished by anchor

Open-line posteriors stay in the delivered `line_posterior`, anchored at `forecast_run.as_of_date`. Held-out delivered-line predictions go in a new E007-owned table anchored at each line's own order date, with the anchor and the duration semantic named on each population.

- **Pros**:
  - The delivered read contract stays literally true — there is no order-date-anchored row in the table E010 reads, so no reader needs a discriminator it does not have
  - The anchor can be *proved* rather than asserted: a composite foreign key to `purchase_order_line (po_line_id, order_date, is_closed)` makes a mis-anchored prediction unrepresentable, and carries "this line actually delivered" into the referenced key at the same time
  - Each population records its own anchor and duration semantic, so neither is inferred
  - The delivered schema and the published anchor convention are untouched; no epic amends another's contract
  - The two populations are structurally disjoint on the held-out side, because a still-open line cannot receive a prediction row
- **Cons**:
  - The array invariants — one-dimensional, lower bound 1, declared length, sorted, non-increasing, unit interval, residual agreement — must now be asserted in two places rather than one
  - The residual tolerance literal appears in the schema a third time, and the existing drift test names only the one constraint it was written for
  - Any reader gaining access to forecasts must now learn which population it is reading, where previously there was one place to look
  - The artifact hash must define an order across two populations rather than one

### Option B: One table with a per-row anchor column and a widened anchor convention

Keep a single store, add an anchor date and an anchor-convention column per row, and relax `ck_schema_constants__anchor_convention` to admit both values.

- **Pros**:
  - One place for every forecast, one set of array invariants, one set of constraint declarations
  - The artifact hash orders one population
  - A future third anchor would be a row value rather than a table
- **Cons**:
  - Every existing reader becomes wrong on the day the second population lands. E010's `1 - survival[d - as_of_date]` would silently mis-score order-date-anchored rows, and correctness would depend on every current and future consumer remembering to filter — the failure mode with no detector
  - Relaxing `ck_schema_constants__anchor_convention` changes a published constant that `/src/api` serves, so a serving-boundary contract is widened to accommodate an offline job
  - `survival NOT NULL` and `ck_line_posterior__draws_non_negative` would both have to be weakened to admit the new population, removing checks from the delivered rows they currently protect
  - The two duration semantics would still differ per row, so a per-row semantic column would be needed anyway — the merge saves a table, not the discriminator

### Option C: A committed file outside the database

Write held-out predictions to a versioned artifact file rather than to a table.

- **Pros**:
  - No schema change at all, and no constraint on a delivered table
  - The artifact is trivially diffable in review
- **Cons**:
  - Puts posterior draws outside the single datastore of record, contradicting [ADR-0002](0002-postgres-as-the-single-datastore.md), which placed them in Postgres deliberately
  - Strands the write-atomicity guarantee: a fit that must write draws and their derived survival arrays indivisibly has no cross-store transaction, so a refused run could leave a file behind after the database rolled back
  - The anchor could not be a foreign key, so a mis-anchored prediction would be caught by a test at best and by nothing at worst
  - The evaluation harness would join a file to a database to grade, for no gain

### Option D: Draws only, with no survival array for the held-out population

Store the sorted draw array for held-out lines and omit the derived day-grid survival array.

- **Pros**:
  - Smallest new table; grading reads draws, so the survival array is not strictly required by the immediate consumer
  - Sidesteps the question of what a survival grid means for a line that already delivered
- **Cons**:
  - Two artifact populations with different column sets, so the shared invariants diverge and one population's checks stop being a template for the other's
  - The residual tail mass would have nothing to be checked against, losing the independent-agreement test that makes it a real check rather than a copy
  - A held-out total duration that overruns the horizon is the reachable case where the grid cannot express an observed outcome; dropping the grid hides that case instead of recording it
  - Forecloses any later consumer that wants a curve for a graded line, for a saving of one array on roughly forty rows

## Decision Outcome

Chosen option: **Two tables distinguished by anchor** — the two populations are two different quantities anchored at two different dates, and separating them by table is what keeps the existing read contract literally true rather than conventionally true.

Option B is the one worth rejecting explicitly, because it is the conventional answer. It fails on the specific ground that this project treats as decisive: the failure it admits is silent. E010 has no discriminator, so a mixed table does not produce an error, a warning, or a wrong-looking number — it produces a plausible risk score computed against the wrong origin, for exactly the rows an evaluation would then grade. Correctness would rest on every present and future reader remembering a filter, and nothing would detect the first one that forgot. It also asks a serving-boundary constant to widen so an offline job can store a second population, and requires weakening two delivered checks that currently hold on every row.

Option C is refused by [ADR-0002](0002-postgres-as-the-single-datastore.md) on its own terms — posterior draws live in the single Postgres instance — and independently by atomicity, which has no cross-store mechanism to offer. Option D saves one array per row and gives up the residual-agreement check, the representation of the reachable horizon-overrun case, and the symmetry that lets one population's invariants be the other's template.

What makes Option A more than a partition is that the separation buys an enforcement the merged shape cannot have. Because the held-out table is new, its anchor can be a composite foreign key to `purchase_order_line (po_line_id, order_date, is_closed)` rather than a column with a comment beside it — the same idiom E003 already uses to make a citation whose page differs from its chunk's page have no referent. The foreign key proves the anchor date *is* the line's order date and, through the delivered biconditional between closure and delivery, that the line actually delivered. A mis-anchored prediction becomes unrepresentable rather than merely untested.

The accepted cost is duplication of the array invariants across two tables, and the discipline that goes with it: E003's `IMMUTABLE` helper functions are **reused, not re-declared**, so sortedness, monotonicity, and unit-interval membership cannot drift into two definitions of the same property.

## Consequences

### Positive

- E010's `1 - survival[d - as_of_date]` remains correct for every row in the table it reads, with no filter and no discriminator required of it.
- The evaluation harness has stored, gradeable predictions for lines with observed outcomes, joinable to exactly one run.
- Each population carries its own anchor and its own duration semantic on the record, so no downstream reader infers either.
- The held-out anchor is enforced by a foreign key, so grading against the wrong origin is unrepresentable rather than merely tested.
- A still-open line cannot receive a held-out prediction, because the delivered closure-implies-delivered check is carried into the referenced key.
- The delivered `line_posterior` contract, its constraints, and the published anchor convention that `/src/api` serves are all untouched.

### Negative

- The array invariants are now asserted in two places. Every future strengthening — the kind E003 already had to apply twice, where a check evaluated to NULL on the input it existed to refuse — must be applied to both tables or they diverge.
- The residual-agreement tolerance literal now appears in the schema a third time, and the existing drift test reads exactly one constraint by name, so the new occurrence is undrifted against nothing until that test is generalized. E003's own statement that only two constants are duplicated as DDL literals is now false and is E003's to correct.
- Structural disjointness holds on one side only. Nothing prevents an order-date-anchored row from being written into `line_posterior`, because that table carries no anchor column and E007 may not add one; that direction is covered by validation rather than by a constraint.
- Every downstream reader now has to know which population it wants. The rule that the worklist reader must never read the held-out table is a documented contract, not something the schema can enforce.
- The run's artifact hash must define an order *across* two populations rather than within one, so the hash definition now carries a population rank.
- The refusal guarantee — a non-converged fit leaves no row anywhere — must enumerate every store the run writes to, because splitting storage created a second place a refused artifact could survive.
- A new table added under one epic's migration block requires a unique key on another epic's delivered table to serve as the foreign-key target. It is additive and rejects no previously legal row, but it is still a cross-epic change that only the migration prefix records.

### Neutral

- E003's helper constraint functions are reused rather than re-declared, which is what keeps the shared invariants from acquiring two definitions.
- The percentile convention is unchanged and applies identically to both populations; only the anchor and the duration semantic differ.
- The observed outcome of a held-out line is deliberately not stored beside its prediction — grading joins the lifecycle events — which keeps the graded answer out of the row the model wrote.

## Links

- [ADR-0004](0004-materialized-posterior-draws-with-sql-side-risk-computation.md) — Materialized Posterior Draws with SQL-Side Risk Computation, the decision this extends
- [ADR-0002](0002-postgres-as-the-single-datastore.md) — Postgres as the Single Datastore, which refuses the committed-file option
- [specs/00003-core-data-schema/data-model.md](../00003-core-data-schema/data-model.md) — `line_posterior`, `schema_constants`, `forecast_run.as_of_date`, and the normative array-semantics table
- [specs/00007-delivery-forecast-model/spec.md](../00007-delivery-forecast-model/spec.md) — FR-008, FR-010, FR-012, FR-029, and the Clarifications session of 2026-07-27
- [specs/00007-delivery-forecast-model/data-model.md](../00007-delivery-forecast-model/data-model.md) — the `held_out_prediction` design and its disclosed gaps
- [specs/prd.md](../prd.md) — CAP-005 (Probabilistic Delivery Forecast), CAP-006 (Risk-Ranked Coordinator Worklist), CAP-009 (Evaluation Harness), CAP-014 (Vendor Lead-Time Scorecards)
- [specs/project-plan.md](../project-plan.md) — E007 (writer), E010, E012, E014, E019 (readers)
