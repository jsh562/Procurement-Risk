---
adr_id: ADR-0019
status: superseded
date: 2026-07-27
tags: [ingestion, provenance, data-lifecycle, schema, retrieval, traceability]
supersedes: []
superseded_by: "ADR-0020"
related_artifacts: ["specs/00006-document-ingestion-and-extraction/spec.md", "FR-043", "FR-055", "SC-025", "SC-043", "ADR-0008", "ADR-0012", "ADR-0017", "E006", "E008", "E009", "E012"]
deciders: ["Project owner", "Solution architect"]
---

# ADR-0019: Ingested Derived Data Carries an Active or Superseded Generation

## Status

Superseded by [ADR-0020](0020-superseded-generations-are-removed-at-promotion-not-retained.md), on the retention clause only. This record still supersedes nothing itself — it was additive when accepted and changed no earlier decision. The generation mechanism decided below stands: generation state is per document, carried on the run-to-document association rather than the run row, because FR-043's skip rule means a run touches some documents and not others; the partial unique index on the document predicated on `status = 'active'` still makes two live generations for one document unrepresentable rather than merely discouraged; the status column is still `NOT NULL` and `CHECK`-constrained, so a NULL third state cannot be arrived at by omission; promotion still cannot activate an incoming generation while another is active, because a partial unique index cannot back a `DEFERRABLE` constraint; the reader-filtering view still carries no `LIMIT` and is still the single place **E008**, **E009**, and **E012** discharge their filtering obligation; and removal is still strictly leaf-up under `ON DELETE RESTRICT`, which fires immediately regardless of any deferral setting. ADR-0020 replaces only the retention clause — that a superseded generation's rows stay in place, so "a bad promotion is reversible by flipping status back, because the superseded rows are still there," and so two chunkers can be compared over one corpus. That clause is withdrawn as unimplementable: E003's `uq_chunk__document_ordinal UNIQUE (document_id, ordinal)` (`src/model/src/model/schema/versions/0004_chunk.py:257`) scopes chunk ordinals to the document rather than to the generation, so two retained generations of one document both hold `(document_id, 0)` and the second cannot be stored. Under ADR-0020 promotion removes the prior generation for that document leaf-up, under the schema-owning role, before writing the new one.

The sections below are left as accepted and state what was true when this record was written. Where they assert retention — the second and third pros of Option A, the second and third positive consequences, the storage-growth negative and its rule that retirement is "**never** part of promotion," the Option C and Option D bullets that turn on retained rows, and the Decision Outcome paragraph ruling Option C out on privilege — read them against ADR-0020, which is authoritative on that clause.

The number is claimed rather than scanned. E006's **FR-051** claims ADR-0018 and ADR-0019 at epic start, alongside the migration block, precisely because number allocation works by scanning for the highest in use and a concurrent epic branching from the same baseline would allocate the same number and be equally right. ADR-0018 is being authored concurrently for the embedding runtime; this record does not depend on it.

## Context

E006 derives four kinds of row from a fixed 51-document corpus: chunks with their vectors, extracted values, the contributing-chunk records that keep a page-split value's provenance whole, and failure records. None of it is source data. Every one of those rows is a *function of its inputs* — the corpus manifest digests, the chunker version, the embedding model identity and revision, and the extraction prompt and schema digest. E006 names that tuple explicitly in **FR-043** and calls it the input tuple, and the definition is deliberately tight: "unchanged inputs" means that tuple and nothing looser, because a chunker upgrade tested only against an unchanged corpus would otherwise either silently skip or silently duplicate.

The consequence is that a re-run with a changed tuple *must* produce different rows. Different chunker, different boundaries; different embedding revision, different vectors. E006 already records this as a disclosed limitation — chunk identity is a function of the chunker, so any legitimate re-chunk moves chunk identifiers. There is no version of this system in which the second run's output can be reconciled row-by-row with the first.

But the previous rows cannot simply be replaced, for two independent reasons that are already in the delivered schema:

1. **The provenance tables are append-only by privilege.** Migration `0009` executes `REVOKE UPDATE, DELETE ON extracted_value, extracted_value_contributing_chunk, extraction_failure FROM procurement_app`. That revoke is load-bearing and E006 explicitly does not weaken it (FR-041). The application role cannot overwrite what it wrote.
2. **Citation edges restrict deletion.** The foreign keys from extracted values and failures to their source chunk, and from contributing-chunk rows to both parents, are `ON DELETE RESTRICT` (migration `0006`). A chunk with anything citing it cannot be deleted, in any order but leaf-up.

So a second run has only bad options unless a rule exists. Either it refuses to write anything — leaving the database describing a chunker that no longer exists, which is a silent staleness no reader can detect — or it writes a second full set beside the first. The second failure is the worse one, because it is invisible at the write and only shows up at read time: a retrieval query, an identity-resolution scan, or the traceability view unions two generations and returns near-identical duplicates of all 51 documents. E006's own estimate puts one generation at 5,000–15,000 chunks with vectors (SC-005), so the corpus grows by that much per chunker revision with no ceiling, and the duplicates are the hardest possible kind to spot — same document, same page, nearly the same text, different identifiers.

A decision is needed now rather than at first re-chunk, because the shape of the run-output records is being designed in this epic's claimed migration block and three downstream epics build directly on these tables: E008 retrieves over the chunk table, E009 resolves identity over extracted line items, and E012 navigates from a forecast back to a cited source page.

There is a precedent in the repository to build on and to differ from deliberately. E003 solved the same "exactly one current answer" problem for forecasts with `ix_forecast_run__single_active`, a unique index on `forecast_run (is_active) WHERE is_active`, plus a `v_active_forecast_run` view that deliberately carries no `LIMIT` — a `LIMIT` would hide a second active row rather than the index preventing it. That mechanism is global: at most one active forecast run in the database. The ingestion invariant is per-document (**SC-043**), which is a different index and, as it turns out, a different placement for the status column.

## Decision Drivers

- Downstream readers must see exactly one answer per document; retrieval, identity resolution, and the traceability view all break differently on two
- Append-only provenance forbids in-place replacement, so "the new set replaces the old set" is not available as a mechanism
- A bad re-chunk must be reversible without a restore, because the failure mode is only visible after downstream reads
- The invariant must hold as a database guarantee rather than depending on every reader remembering to filter

## Considered Options

### Option A: An explicit generation with active/superseded status, enforced by a partial unique index

Each ingestion run is a generation carrying its input tuple — corpus manifest digests, chunker version, embedding model identity and revision, extraction prompt and schema digest — and a status of `active` or `superseded`. Every chunk, extracted value, and failure associates to its run (SC-021). "At most one active generation per document" is enforced by a partial unique index on the document, predicated on `status = 'active'`, so a second activation fails on write rather than producing two live generations. Promotion is one transaction: write the new generation, supersede the old, activate the new. Readers filter on the active generation through a view rather than by convention.

- **Pros**:
  - The invariant is enforced by the index, so two live generations for one document are unrepresentable rather than merely discouraged
  - A bad promotion is reversible by flipping status back, because the superseded rows were never deleted
  - Superseded generations remain readable, so two chunkers can be compared on the same corpus instead of one being destroyed to evaluate the other
  - It needs no privilege the ingestion path was deliberately denied: the status lives on a record this epic owns in its own migration block, and the three revoked tables are never updated or deleted
  - It matches the shape E003 already proved with `ix_forecast_run__single_active` and `v_active_forecast_run`, so a reviewer meets a familiar mechanism
- **Cons**:
  - Every downstream reader acquires a filtering obligation, and an unqualified query against the chunk table silently returns superseded rows
  - Storage grows by a full generation per re-chunk until a retirement job runs
  - It adds a lifecycle concept, a status column, an index, and a view to a schema that would otherwise just have rows

### Option B: A bare boolean flag with no partial index

Add an `is_current` boolean to the run and set it by application logic.

- **Pros**:
  - Cheapest to write; no index, no view, no status vocabulary
  - Reads look identical to Option A for anyone who remembers the predicate
- **Cons**:
  - Two concurrent promotions produce two current generations and nothing catches it — the check is a read-then-write race with no unique index to lose on
  - The invariant lives in whichever code path remembered it, which is the failure mode this decision exists to remove
  - A boolean cannot distinguish "superseded" from "never activated", so a half-written generation is indistinguishable from a retired one

### Option C: Delete the previous generation at promotion

The new run removes the old rows as it writes, leaving exactly one set at all times.

- **Pros**:
  - No storage growth, no filtering obligation on readers, no lifecycle concept
  - The table always means what an unqualified query says it means
- **Cons**:
  - No rollback — a bad re-chunk is unrecoverable without a database restore, and it is detected only after downstream reads
  - No ability to compare two chunkers or two embedding revisions on the same corpus, which is exactly what a re-chunk needs to be justified by
  - It requires the ingestion path to hold `DELETE` on `extracted_value`, `extracted_value_contributing_chunk`, and `extraction_failure` — privileges migration `0009` deliberately revoked and FR-041 keeps revoked
  - The deletion still has to be leaf-up under `ON DELETE RESTRICT`, so it carries Option A's ordering cost without Option A's reversibility

### Option D: No generation concept; readers take the newest run

Store every run's rows and have each reader select the maximum run for the document.

- **Pros**:
  - Nothing to promote, nothing to activate, no transaction to get right
  - Superseded data is retained, so comparison and rollback are both possible in principle
- **Cons**:
  - It re-implements a `max(version)` subquery in every reader — the same invariant enforced N times, and wrong the first time someone forgets
  - "Newest" is not the same predicate as "active": it cannot express a rollback, because the run being rolled back is still the newest one
  - A partially written or abandoned run becomes the answer for every document it touched, with no state that says otherwise

## Decision Outcome

Chosen option: **Option A — an explicit generation with active/superseded status, enforced by a partial unique index** — because it is the only option that makes the invariant a property of the database rather than of the code that happened to write last, and the only one that keeps a bad re-chunk reversible.

Option B and Option D fail the same way from opposite ends: B has a single place for the invariant and no enforcement, D has enforcement in every reader and therefore no single place. D's deeper defect is that "newest" cannot express rollback — the generation you want to abandon is by definition the newest one, so the only recovery is to run a third ingestion, which is not a recovery, it is a hope. Option C is the tempting one because it makes the tables mean what an unqualified query says they mean, and it is ruled out mechanically rather than on preference: it needs `DELETE` on three tables from which that privilege was revoked on purpose, and buying it back would undo `0009` to save a filter.

Three details of the chosen mechanism are load-bearing and are recorded here because getting any of them wrong reproduces the failure the decision is meant to prevent.

**The status lives on the per-document generation record, not on the run row.** The invariant is per document (SC-043), and FR-043 requires a run to skip documents whose input tuple is unchanged, creating no rows for them. So a run touches some documents and not others, and a run-level status would either supersede generations the run never replaced or leave replaced ones active. The record carrying `status` is therefore the run-to-document association, and the index is unique on the document, predicated on `status = 'active'`. This is where the mechanism differs from `ix_forecast_run__single_active`, which is global because a forecast run has no per-document scope.

**The promotion order inside the transaction is forced, because the check cannot be deferred.** A partial unique index cannot back a `DEFERRABLE` unique constraint, so the uniqueness check fires per statement. Promotion must mark the outgoing generation `superseded` *before* activating the incoming one; the reverse order fails on the index, and no deferral setting rescues it. That ordering is a property of the transaction, not a convention — both statements are in the same transaction, so a crash between them rolls back to the old generation still active, which is the correct state to fail into.

**The status column is `NOT NULL` and `CHECK`-constrained.** A `CHECK` rejects only on false, so a NULL status passes it; and `status = 'active'` evaluates to NULL for a NULL status, so the row falls out of the index predicate too. A NULL-status generation would therefore be neither active nor superseded, invisible to the invariant and invisible to every reader — a third state nobody filters on, arrived at by omission. Both constraints together are what make the state space actually two.

Readers filter through a view. The view is not a convenience; it is the single place the filtering obligation is discharged, and it is the reason E008, E009, and E012 do not each have to be right about a predicate. Following E003's precedent, that view carries no `LIMIT` — a `LIMIT` would conceal a second active generation rather than the index preventing one.

## Consequences

### Positive

- The invariant is a database guarantee rather than application discipline. Two live generations for one document are unrepresentable, and a second activation fails on write with a named index rather than succeeding and being discovered at read time.
- A bad promotion is reversible by flipping status back, because the superseded rows are still there. Recovery is a status update inside a transaction, not a restore.
- Two chunkers or two embedding revisions can be compared over the same corpus, because the earlier generation survives its replacement. Under Option C the only way to evaluate a chunker change would be to destroy the thing being compared against.
- The mechanism costs the ingestion path no privilege it was denied. The three revoked tables are still never updated and never deleted from by the application role; the status transitions touch a table E006 owns in its claimed migration block.
- The shape is the one E003 already proved and tested for forecasts, so `v_active_forecast_run` and the ingestion view are the same pattern read twice rather than two mechanisms to learn.

### Negative

- **Every downstream reader must filter on the active generation.** A query against the chunk table unqualified now returns superseded rows, and it returns them silently — same document, same page, near-identical text. This is a real obligation on **E008** (retrieval and reranking over chunks), **E009** (identity resolution over extracted line items), and **E012** (source-page traceability), and the view exists so that obligation has exactly one place to be met. An epic that joins the base table directly has taken the obligation on itself.
- Storage grows by a full generation per re-chunk until retirement runs — 5,000–15,000 chunks with their vectors per generation, on E006's own estimate, plus the values and failures derived from them. Retirement is a separate operator job with a stated retention bound (FR-055) and is **never** part of promotion; folding it in would put deletion back on the path that promotes, which is the privilege boundary Option C failed on.
- **`ON DELETE RESTRICT` cannot be deferred**, unlike `NO ACTION`, so a retirement job must delete strictly leaf-up: contributing-chunk rows, then extracted values and failures, then chunks, then the generation. A parent-first deletion inside one transaction cannot be made to work by marking constraints deferrable — `RESTRICT` fires immediately regardless of the deferral setting, and discovering this at retirement time rather than here would look like a schema bug.
- The retirement job runs under the schema-owning role, because the application role holds no `DELETE` on the three provenance tables (FR-041). Retirement is therefore an operator procedure with its own access path, not an automated background task the ingestion job can start.
- A third state cannot be added cheaply later. The `CHECK` is what stops a typo from creating one, and it is also what makes any genuine future state — `pending`, say, for a generation being written — a migration plus an audit of every reader's predicate.

### Neutral

- ADR-0008's provenance boundary is untouched. Page citations remain parser-derived deterministic facts; this record governs which *set* of those facts is current, not how any one of them is produced.
- ADR-0012's embedding model and vector dimension are unchanged. The model identity and revision appear here only as two elements of the input tuple whose change makes a generation stale.
- The decision says nothing about how many generations to retain. FR-055 requires a stated retention bound and a purge procedure; this record requires only that retirement be separate from promotion and leaf-up in order.
- Under ADR-0017, the per-table detail — column names, the index name, the view definition, the `CHECK` predicate — is stated normatively in E006's Plan-phase data model within its claimed migration block. This record fixes the mechanism and the invariant, not the identifiers.
- Zero active generations for a document is a legal and meaningful state, meaning "this document has not been ingested under the current inputs". It is the same reading E003 gave zero active forecast runs, and readers should distinguish it from "ingested, possibly stale" rather than falling back to the newest generation — falling back would re-introduce Option D inside the view.

## Links

- [specs/00006-document-ingestion-and-extraction/spec.md](../00006-document-ingestion-and-extraction/spec.md) — **FR-055** (active/superseded, one active per document, reader filtering, retention bound), **FR-043** (the input tuple and the skip rule), **FR-041** (no in-place update; removal is an operator procedure), **FR-054** (per-document transaction), **SC-025**, **SC-043**, **SC-021**; FR-051 is the claim under which this number was allocated
- [ADR-0008](0008-deterministic-provenance-and-computation-boundary.md) — deterministic parser-derived provenance; this record governs which generation of those facts is current
- [ADR-0012](0012-embedding-model-and-vector-dimension.md) — the embedding model identity and revision, two elements of the input tuple whose change supersedes a generation
- [ADR-0017](0017-plan-phase-artifact-normative-over-a-specify-phase-requirement.md) — why the per-table detail for this mechanism is normative in E006's Plan-phase data model rather than restated here
- ADR-0018 — claimed by the same FR-051 for the embedding runtime, authored concurrently; no dependency in either direction
- `src/model/src/model/schema/versions/0009_provenance_privileges.py` — the `REVOKE UPDATE, DELETE` on `extracted_value`, `extracted_value_contributing_chunk`, and `extraction_failure` that makes in-place replacement unavailable
- `src/model/src/model/schema/versions/0006_extraction.py` — the `ON DELETE RESTRICT` citation edges that force leaf-up retirement, and `v_extracted_value_provenance`
- `src/model/src/model/schema/versions/0008_forecast.py` — `ix_forecast_run__single_active` and `v_active_forecast_run`, the global-scope precedent this per-document mechanism follows
- E006 — the epic that raises this decision and owns the generation records in its claimed `0300`–`0399` migration block
- E008, E009, E012 — the epics bound by the filtering obligation; the view is where they discharge it
- [specs/sad.md](../sad.md) — ADR catalog; requires a new row
