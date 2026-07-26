---
adr_id: ADR-0015
status: accepted
date: 2026-07-25
tags: [llm, gateway, observability, durability, datastore]
supersedes: []
superseded_by: ""
related_artifacts: ["specs/prd.md", "specs/adrs/0002-postgres-as-the-single-datastore.md", "specs/adrs/0007-single-traced-language-model-invocation-boundary.md", "specs/adrs/0008-deterministic-provenance-and-computation-boundary.md", "specs/00004-traced-model-gateway/spec.md", "CAP-002", "CAP-008", "E004"]
deciders: ["Project owner", "Solution architect"]
---

# ADR-0015: Local Spool for Invocation Records Whose Database Write Fails

## Status

Accepted.

When the invocation record cannot be written to Postgres, the gateway spools it to a durable local SQLite file and reconciles it into Postgres on the next successful connection. The invocation still fails closed toward the caller.

## Context

The project's technology stack fixes a single PostgreSQL instance and states plainly that there is no second datastore (ADR-0002). E004's adversarial stress test surfaced a contradiction that rule cannot resolve on its own.

The gateway fails closed when the invocation record cannot be written — the correct behaviour, because an untraced call must not be allowed to look successful. But when the provider call has *already* succeeded and been billed, failing closed leaves a paid model call with no record anywhere. That falsifies two separately-published statements at once: the requirement that every invocation produces exactly one record, and the product's claim that one hundred percent of model calls are traced.

The failure is silent in the way this project's own principles single out as worst. The missing row looks exactly like a call that never happened. There is no counter that moves, no artifact left behind, and no way after the fact to distinguish "the provider was never called" from "the provider was called, charged us, and the evidence was lost". A tracing guarantee that quietly excludes its own worst failure mode is not a guarantee.

A decision is needed before E004 implements the record write, because the fail-closed path and the record-write path are the same code and cannot be sequenced separately.

## Decision Drivers

- Keeping the 100% tracing claim literally true rather than asterisked
- Not weakening fail-closed behaviour toward the caller
- Not introducing a second datastore of record
- Keeping the mechanism inside a library, with no service to lean on

## Considered Options

### Option A: Durable local spool with idempotent reconciliation

Spool the record to a local SQLite file with write-ahead logging and full synchronous durability, keyed on the invocation identifier, and reconcile into Postgres on the next successful connection with a conflict-ignoring insert, deleting the spool row only after the Postgres transaction commits. The invocation still fails closed toward the caller.

- **Pros**: A billed call is never left with no record anywhere; at-least-once delivery plus an idempotent sink keyed on a unique invocation id produces an exactly-once effect with no distributed transaction; SQLite supplies atomic commit, torn-write recovery, and cross-process locking that a flat file would have to reimplement badly; the spool is a transient buffer that drains to empty, with no query path reading it.
- **Cons**: A durable local file now exists alongside Postgres and must be justified against the single-datastore rule; the spool grows without bound while Postgres is down; SQLite's single-writer rule means a blocking reconcile can stall an invocation; a process death between the provider response and the spool commit still loses the record.

### Option B: Fail closed with no spool, and narrow the claim

Keep fail-closed behaviour unchanged, scope the 100% denominator to invocations whose record write succeeded, and publish the untraced-billed-call gap as a stated limitation.

- **Pros**: No new mechanism, no new file, no tension with the single-datastore rule; the smallest possible change.
- **Cons**: The headline claim acquires an asterisk on exactly the failure the claim exists to exclude, which is the one place a qualification costs the most credibility.

### Option C: Inline retry until the record write succeeds

Retry the record write in the invocation path until Postgres accepts it.

- **Pros**: No second store at all; no reconciliation logic; the record reaches its final home or the call never returns.
- **Cons**: Blocks the caller for the duration of a database outage with no bound, converting a trace failure into an availability failure — a strictly worse trade than the one being solved.

## Decision Outcome

Chosen option: **Durable local spool with idempotent reconciliation** — it is the only option that keeps a billed provider call from vanishing without also weakening what the caller is promised. Option B is honest but spends the credibility of the headline tracing claim on precisely the failure mode the claim exists to exclude. Option C avoids a second file only by converting a bounded observability failure into an unbounded availability failure. The chosen option's cost is a durable local file operators must know about and unbounded spool growth during a prolonged outage; in exchange the tracing claim stays true without qualification, fail-closed behaviour toward the caller is unchanged, and reconciliation is idempotent by construction rather than by retry discipline.

## Consequences

### Positive

- A provider call that has been billed always leaves a record somewhere, so the 100%-traced claim needs no qualification.
- Fail-closed behaviour toward the caller is unchanged: a call whose record cannot reach Postgres still fails.
- Reconciliation is idempotent by construction — at-least-once delivery into a sink keyed on a unique invocation id yields an exactly-once effect with no distributed transaction.
- SQLite supplies atomic commit, torn-write recovery, and cross-process locking that a hand-rolled append file would have to reimplement.

### Negative

- A durable local file exists alongside Postgres that operators must know about, back up or knowingly discard, and reason about during incidents.
- The spool grows without bound while Postgres is unavailable; nothing in this decision caps it.
- SQLite's single-writer rule means a blocking reconcile can stall an invocation behind spool drainage.
- A process death between the provider response and the spool commit still loses the record; the window is small but irreducible and should be disclosed rather than hidden.

### Neutral

- The spool is explicitly **not** a datastore of record: it holds only unreconciled invocation records, no consumer queries it, and its steady state is empty. The single-instance rule is thereby scoped to data of record rather than to every durable byte on disk — it is scoped, not relaxed.
- The reconcile depends on a unique constraint on the invocation identifier, created by a migration in E004's claimed `0100`–`0199` range.

## Links

- [specs/prd.md](../prd.md) — product requirements document
- [ADR-0002](../adrs/0002-postgres-as-the-single-datastore.md) — the single-datastore rule this decision scopes to data of record
- [ADR-0007](../adrs/0007-single-traced-language-model-invocation-boundary.md) — the traced invocation path whose completeness claim this protects
- [ADR-0008](../adrs/0008-deterministic-provenance-and-computation-boundary.md) — the provenance boundary that makes a missing record a correctness failure rather than a logging gap
- [specs/00004-traced-model-gateway/spec.md](../00004-traced-model-gateway/spec.md) — the adversarial stress test that surfaced the untraced-billed-call contradiction
- CAP-002 — Document Understanding & Extraction (offline invocations whose records spool)
- CAP-008 — Grounded Question Answering (request-serving invocations whose records spool)
- E004 — Traced Model Gateway, which owns the record write, the spool, and migrations `0100`–`0199`
