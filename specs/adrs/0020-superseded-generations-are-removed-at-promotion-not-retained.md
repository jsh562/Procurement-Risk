---
adr_id: ADR-0020
status: accepted
date: 2026-07-27
tags: [ingestion, provenance, data-lifecycle, schema, retrieval, traceability]
supersedes: ["ADR-0019"]
superseded_by: ""
related_artifacts: ["specs/00006-document-ingestion-and-extraction/spec.md", "specs/00003-core-data-schema/data-model.md", "FR-041", "FR-043", "FR-055", "SC-005", "SC-043", "TR-058", "ADR-0017", "ADR-0019", "E003", "E006", "E008", "E009", "E012"]
deciders: ["Project owner", "Solution architect"]
---

# ADR-0020: Superseded Generations Are Removed at Promotion, Not Retained

## Status

Accepted. **Supersedes ADR-0019, on its retention clause only.**

The replaced clause is the one holding that a superseded generation's derived rows stay in place — specifically, ADR-0019's Option A pros "a bad promotion is reversible by flipping status back, because the superseded rows were never deleted" and "superseded generations remain readable, so two chunkers can be compared on the same corpus"; the two positive consequences that restate them; the negative consequence that storage "grows by a full generation per re-chunk until retirement runs" together with its rule that retirement is a separate job and "**never** part of promotion"; and the corresponding half of the neutral bullet on retention. ADR-0019's third decision driver — a bad re-chunk must be reversible without a restore — survives only in the weakened form recorded below: reversal is a deterministic re-run, not a status update.

Everything else in ADR-0019 is carried forward unchanged and is **not** reopened by this record:

- Generation state is per document, carried on the run-to-document association rather than the run row, because FR-043's skip rule means a run touches some documents and not others.
- The partial unique index on the document predicated on `status = 'active'` still makes two live generations for one document unrepresentable rather than merely discouraged.
- The status column is still `NOT NULL` and `CHECK`-constrained, so a NULL third state cannot be arrived at by omission.
- Promotion still cannot activate an incoming generation while another active generation for that document exists; the check fires per statement and no deferral setting rescues a wrong order.
- The reader-filtering view is still the single place E008, E009, and E012 discharge their obligation, and it still carries no `LIMIT`, for the reason E003 established with `v_active_forecast_run`.
- Deletion is still strictly leaf-up under `ON DELETE RESTRICT`, and the application role still holds no `UPDATE` or `DELETE` on the three provenance tables migration `0009` revoked them from.

The number is claimed rather than scanned, on the same basis ADR-0019 records. E006's **FR-051** claimed ADR-0018 and ADR-0019 at epic start; this is a **third claim, made during the Checklist phase** rather than at epic start, and is recorded as such so that the discrepancy between FR-051's stated claim and the numbers actually allocated to this epic is visible rather than inferred. ADR-0020 was verified free against `specs/adrs/` at the time of writing: the highest allocated number on disk was `0019`.

## Context

ADR-0019 was accepted on 2026-07-27 and is superseded on one clause the same day. That is worth stating plainly rather than smoothing over, because the reason is specific and checkable.

ADR-0019 chose an explicit generation with `active`/`superseded` status and, in choosing it over deletion, recorded that superseded generations' rows stay in place: a bad promotion would be "reversible by flipping status back, because the superseded rows are still there," and two chunker versions could be compared over one corpus because the earlier generation survived its replacement.

A requirements-quality checklist pass over E006 then read the delivered E003 migration rather than the description of it. E003's `chunk` table carries:

```
CONSTRAINT uq_chunk__document_ordinal
    UNIQUE (document_id, ordinal)
```

at `src/model/src/model/schema/versions/0004_chunk.py:257`, with the stated intent "ordinal position is unique within a document, so the chunker cannot emit two chunks claiming the same position in one source." Chunk ordinals are zero-based and unique **within a document** — not within a run, and not within a generation, because when E003 wrote this constraint there was no generation concept for it to be scoped by.

The consequence is arithmetic. Two retained generations of the same document each contain a chunk at ordinal 0 for that `document_id`. The second generation's first insert violates `uq_chunk__document_ordinal` and the promotion transaction aborts. Retention is not merely expensive under the delivered schema; it is impossible under it. ADR-0019's retention clause was unimplementable from the moment it was written, and the defect is in the record rather than in any code, because implementation of E006 is separately blocked on FR-047's TR-081 amendment and no row has yet been written against the clause.

There is exactly one way to make retention work, and it is closed. Widening the constraint to key the ordinal by run means altering a constraint on `chunk`, a table E006 does not own. `specs/00003-core-data-schema/data-model.md` is normative over `chunk` under {SAD:ADR-0017}, and that record's conditions explicitly forbid a Plan-phase artifact adding or removing a named object or constraint on a table it does not own. E006 may add no constraint to `chunk`. So the decision is between removing superseded rows and abandoning the generation concept, and it has to be made now, in the same phase that found it, because three downstream epics — E008 over chunks, E009 over extracted line items, E012 over source-page traceability — plan against what ADR-0019 says is in the database.

This is verified against the migration file, not inferred from the data model's description of it.

## Decision Drivers

- The invariant must be enforceable against the schema **as delivered**; a constraint that exists in a migration on `main` outranks a mechanism described in a record accepted after it
- The ownership boundary holds: E006 may not add, remove, or widen a named constraint on E003's `chunk` table, per {SAD:ADR-0017}'s conditions on normativity
- Everything in ADR-0019 that the defect does not touch must survive the correction, so this is a clause replacement rather than a re-decision of the generation mechanism
- A capability that has to be given up should be given up in writing, in the record that removes it, rather than left implied by a superseded document

## Considered Options

### Option A: Promotion removes the prior generation for that document

Re-ingesting a document deletes its previous generation's rows leaf-up — contributing-chunk rows, then extracted values and extraction failures, then chunks, then the run-to-document association — under the schema-owning role, and then writes the new generation. Exactly one generation's rows exist per document at any time.

- **Pros**:
  - `uq_chunk__document_ordinal` is satisfied without touching it: with one generation's rows resident per document, `(document_id, ordinal)` cannot collide
  - No table E006 does not own is altered, so the ownership boundary and {SAD:ADR-0017}'s conditions are both respected
  - The generation mechanism ADR-0019 chose survives whole — per-document state, the partial unique index, the `CHECK`ed status vocabulary, the filtering view
  - Removal precedes the write inside the per-document transaction (FR-054), so no transient state exists in which two generations' chunks are resident and the constraint is under strain
  - Storage stops growing per re-chunk, and FR-055's retention bound becomes a bound the schema enforces rather than a number a purge job is trusted to honour
- **Cons**:
  - Rollback by status flip is withdrawn; recovering from a bad promotion means re-running the previous chunker version from the corpus
  - Two generations can no longer coexist for comparison, so a chunker ablation needs two databases or two sequential runs
  - Promotion now performs deletion, which the ingestion job's role cannot do, so promotion becomes an operator procedure rather than an unattended step
  - The delete is on the promotion path and must be leaf-up under `ON DELETE RESTRICT`, so a mis-ordered delete fails the promotion itself rather than a background job

### Option B: Amend E003 to key the chunk ordinal by run

Widen `uq_chunk__document_ordinal` to include the run, so two generations of one document can each hold ordinal 0, and keep ADR-0019's retention clause exactly as written.

- **Pros**:
  - ADR-0019 stands entire; no clause is withdrawn and no capability is lost
  - Rollback stays a status update and side-by-side chunker comparison stays possible in one database
- **Cons**:
  - It widens a constraint on a table three downstream epics read, weakening a guarantee E003 stated deliberately: that the chunker cannot emit two chunks claiming the same position in one source
  - {SAD:ADR-0017}'s conditions explicitly forbid a Plan-phase artifact adding or removing a named object or constraint on a table it does not own; `chunk` belongs to E003's normative data model
  - It would be a **second** blocking cross-epic amendment for this epic, alongside the TR-081 amendment E006 already waits on under FR-047 — doubling the schedule cost of a defect that has a same-epic fix
  - `chunk_id` is the citation FK target; loosening the ordinal's scope invites the same widening on the citation edges, which is the one place in the schema that must not become ambiguous

### Option C: Withdraw the generation concept entirely

Drop `active`/`superseded`, the per-document state, the index, and the view; store one set of derived rows and overwrite it.

- **Pros**:
  - Nothing to promote, nothing to activate, no filtering obligation, no vocabulary
  - Trivially satisfies `uq_chunk__document_ordinal`
- **Cons**:
  - Loses run attribution for derived rows — "which run produced this chunk, under which chunker version and which embedding revision" becomes unanswerable, and that is FR-055's requirement rather than an optional nicety
  - Discards a mechanism whose defect is confined to one clause, which is a re-decision rather than a correction
  - Removes the database guarantee that a document has one current answer, putting the invariant back into whichever code path wrote last — the failure ADR-0019 exists to prevent, and it is untouched by the constraint that forced this record

## Decision Outcome

Chosen option: **Option A — promotion removes the prior generation for that document** — because it is the only option that makes ADR-0019's invariant enforceable against the schema that actually exists, without altering a table E006 does not own and without discarding a mechanism that is correct everywhere except its retention clause.

Option B is the option that preserves the most and is nonetheless the wrong one. It buys ADR-0019's text intact at the price of widening a delivered constraint on a shared table, which {SAD:ADR-0017} forbids at this phase and which would be a second blocking amendment for an epic already stalled on the first. Option C over-corrects: the retention clause is one clause, and throwing out run attribution to fix it would trade a real FR-055 obligation for a problem that has a local solution.

Two points about the chosen mechanism are load-bearing and are recorded here because ADR-0019 rejected its own Option C — "delete the previous generation at promotion" — partly on grounds that do not apply to this one.

**The privilege objection that defeated ADR-0019's Option C does not apply, because the actor is different.** ADR-0019 rejected deletion in part because it "requires the ingestion path to hold `DELETE` on `extracted_value`, `extracted_value_contributing_chunk`, and `extraction_failure` — privileges migration `0009` deliberately revoked." That is still true and migration `0009` is still untouched. The removal here is performed under the **schema-owning role**, not the application role, exactly as ADR-0019 already specified for its retirement job: "the retirement job runs under the schema-owning role, because the application role holds no `DELETE` on the three provenance tables (FR-041)." What changes is *when* that operator step runs — immediately before the replacing write, rather than as a separate job at an unspecified later time — not *who* runs it. The ingestion job gains no privilege it was denied.

**The removal precedes the write, inside the per-document transaction.** Deleting after writing would put the new generation's ordinal 0 in the table alongside the old one for the length of a statement, which is precisely the collision this record exists to avoid. The order within one document's transaction is: delete the prior generation leaf-up, write the new rows, activate the new generation. A crash at any point rolls the document back to its prior generation intact, which remains the correct state to fail into.

## Consequences

### Positive

- `uq_chunk__document_ordinal` is satisfied without altering a table E006 does not own. The invariant is now enforceable against the schema as delivered, and it is enforced twice over: the partial unique index forbids two active generations, and one resident generation per document means the chunk constraint cannot be reached.
- Everything else ADR-0019 decided survives. Generation state is still per document; the partial unique index still makes two live generations unrepresentable; the `CHECK`ed, `NOT NULL` status still closes off a third state by omission; and the reader-filtering view still gives E008, E009, and E012 one place to meet their obligation. Superseding this clause does not reopen any of them.
- Storage stops growing by a full generation per re-chunk. On E006's own estimate (SC-005) a generation is 5,000–15,000 chunks with vectors plus everything derived from them, and under this record that is the steady-state size rather than the per-revision increment. There is no retirement backlog to schedule and no purge job that can fall behind.
- ADR-0019's worst failure mode — an unqualified query silently unioning two generations and returning near-identical duplicates of all 51 documents — is now prevented by the data as well as by the predicate. A reader who forgets the filter gets the right rows anyway.

### Negative

- **Rollback is no longer a flag flip.** ADR-0019's stated positive consequence is withdrawn. Reverting a bad promotion means re-running the previous chunker version from the corpus. That is possible — ingestion is deterministic given its input tuple (FR-043), so the earlier generation is reproducible rather than lost — but it is a re-run costing a full ingestion pass, not a status update inside a transaction. Recovery time goes from seconds to the length of a run.
- **Two chunker generations can no longer be compared in one database.** A side-by-side chunker or embedding-revision ablation now needs two databases, or two sequential runs with the evaluation figures captured between them. This is stated explicitly because it is a real capability ADR-0019 implied and this record removes: the comparison is still possible, but it is no longer possible by querying.
- **"What did the previous run produce" stops being answerable.** The run history survives in `ingestion_run` — every run's identity, input tuple, timings, and model identifiers persist whether or not its rows do — so "which run produced the rows that are here now" stays answerable through the generation record. The rows that run produced and a later run replaced are gone, and no query recovers them.
- **Promotion becomes an operator procedure.** Deletion requires privileges the ingestion job does not hold and will not be granted (FR-041, migration `0009`), so a re-ingestion that replaces an existing generation cannot run unattended under the application role. This is consistent with the correction path already established for this epic, where in-place correction of provenance rows is likewise an operator procedure rather than an application capability — but it does mean re-ingestion and first ingestion are not the same operation.
- **A leaf-up delete now sits on the promotion path.** ADR-0019 placed the `ON DELETE RESTRICT` ordering burden on a background retirement job, where a mis-ordered delete failed a job. It now fails the promotion. The ordering — contributing-chunk rows, then extracted values and failures, then chunks, then the run-to-document association — is not adjustable by marking constraints deferrable, because `RESTRICT` fires immediately regardless of deferral setting.

### Neutral

- FR-055's retention bound is satisfied structurally rather than by policy. The bound is one generation per document and the purge procedure is the removal step of promotion; ADR-0019's requirement that a bound be *stated* is met by this record stating it.
- The `superseded` status remains part of the vocabulary and is not withdrawn — it is the state an outgoing generation occupies between being marked and being removed, and it remains distinguishable from "never activated," which is why ADR-0019 chose a `CHECK`ed vocabulary over a boolean. Whether that state is durable or transient in the delivered schema is per-table detail, normative in E006's Plan-phase data model under {SAD:ADR-0017}, not fixed here.
- The reader-filtering view's role shifts without its obligation lapsing. Its predicate is no longer the only thing standing between a reader and duplicate rows, but it remains the declared reading contract and the place run attribution is obtained. E008, E009, and E012 are not released from it, and no epic should start joining base tables directly on the strength of this record.
- Migration `0009`'s `REVOKE UPDATE, DELETE` on `extracted_value`, `extracted_value_contributing_chunk`, and `extraction_failure` is untouched, as is FR-041's commitment not to weaken it. The application role's relationship to the provenance tables is exactly what ADR-0019 left it.
- ADR-0008's provenance boundary and ADR-0012's embedding model and vector dimension are unaffected, as they were under ADR-0019. This record governs how a stale generation leaves, not how any fact in it is produced.
- Zero active generations for a document remains legal and meaningful — "this document has not been ingested under the current inputs." Under this record it is also the state a document occupies between the removal and the write inside a promotion transaction, invisible outside it.

## Links

- [ADR-0019](0019-ingested-derived-data-carries-an-active-or-superseded-generation.md) — superseded on its retention clause only; the generation mechanism, the per-document partial unique index, the `CHECK`ed status, the promotion ordering, and the reader-filtering view are carried forward from it unchanged
- [ADR-0017](0017-plan-phase-artifact-normative-over-a-specify-phase-requirement.md) — why `specs/00003-core-data-schema/data-model.md` is normative over `chunk`, and why its conditions forbid E006 adding or widening a constraint on that table
- `src/model/src/model/schema/versions/0004_chunk.py` — `uq_chunk__document_ordinal UNIQUE (document_id, ordinal)` at line 257, the delivered constraint that makes retention unimplementable; verified in the migration, not inferred from its description
- [specs/00003-core-data-schema/data-model.md](../00003-core-data-schema/data-model.md) — the normative statement of that constraint, "ordinal position is unique within a document"
- [specs/00006-document-ingestion-and-extraction/spec.md](../00006-document-ingestion-and-extraction/spec.md) — **FR-055** (generation lifecycle and retention bound), **FR-043** (the input tuple, the skip rule, and the determinism that makes a re-run a recovery), **FR-041** (no in-place update; removal is an operator procedure), **FR-054** (per-document transaction), **FR-047** (the TR-081 amendment blocking implementation), **SC-005**, **SC-043**; FR-051 is the claim under which ADR-0018 and ADR-0019 were allocated, and which this third claim extends
- `src/model/src/model/schema/versions/0009_provenance_privileges.py` — the `REVOKE UPDATE, DELETE` that keeps removal an operator procedure under the schema-owning role
- `src/model/src/model/schema/versions/0006_extraction.py` — the `ON DELETE RESTRICT` citation edges that force the leaf-up order now on the promotion path
- E003 — the epic that owns `chunk` and its constraints; E006 — the epic that raises this correction during its Checklist phase
- E008, E009, E012 — still bound by the view; this record narrows what their unqualified queries could return but does not release the obligation
- [specs/sad.md](../sad.md) — ADR catalog; requires a new row and a status correction on ADR-0019's row
