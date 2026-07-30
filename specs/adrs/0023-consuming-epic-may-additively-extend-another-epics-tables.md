---
adr_id: ADR-0023
status: accepted
date: 2026-07-29
tags: [governance, schema, migrations, boundaries, process, traceability]
supersedes: []
superseded_by: ""
related_artifacts: ["specs/00009-cross-document-identity-resolution/spec.md", "specs/00009-cross-document-identity-resolution/plan.md", "specs/00009-cross-document-identity-resolution/data-model.md", "specs/00003-core-data-schema/spec.md", "specs/00003-core-data-schema/data-model.md", "specs/00006-document-ingestion-and-extraction/spec.md", "specs/project-plan.md", "specs/sad.md", "ADR-0013", "ADR-0016", "ADR-0017", "ADR-0021", "TR-034", "TR-035", "TR-036", "TR-052", "TR-084", "FR-065", "E003", "E006", "E009", "E012", "E014", "E016"]
deciders: ["Project owner", "Solution architect"]
---

# ADR-0023: A Consuming Epic May Additively Extend Another Epic's Tables Under a Recorded Exception

## Status

Accepted. Supersedes nothing, and withdraws no clause of any earlier record.

It closes gap **G-9** in `specs/00009-cross-document-identity-resolution/data-model.md` — *"An epic after E009 alters those two tables citing E009's precedent, and nothing reports it."* Without this record E009 is an **instance** a later epic can cite as licence. With it, a later epic cites a **rule**, which either admits its case or refuses it.

Two scope disambiguations, because this record sits next to two accepted ones and the SAD catalog's coexistence rule requires the boundary to be stated rather than inferred:

- **Against [ADR-0021](0021-superseded-generations-are-removed-at-promotion-not-retained.md).** ADR-0021 refused a structurally similar change — widening `uq_chunk__document_ordinal` to key the chunk ordinal by run — and that refusal **stands, on its own facts**. It is not reopened, weakened, or granted an exception here. The facts that separate the two cases are named in the conditions below and in the Decision Outcome: `chunk` had a same-epic alternative that did not touch it, E006 was already stalled on one blocking cross-epic amendment, and the widening would have loosened a guarantee on a table three epics were planning reads against. A record that licensed E006's Option B retroactively would be a different decision from this one and would need to supersede ADR-0021; this record does not, and a reader who arrives here looking for permission to widen a constraint on a populated table is in the wrong record.
- **Against [ADR-0017](0017-plan-phase-artifact-normative-over-a-specify-phase-requirement.md).** ADR-0017 governs *which artifact* normatively states a schema, and its condition 3 holds that adding or removing a named object or constraint is a Specify-level change rather than a Plan-artifact correction. That condition is untouched and is in fact the mechanism this record depends on: it is precisely why E009's change cannot be absorbed as a `data-model.md` correction and must reach E003's `spec.md`. ADR-0021 read condition 3 as a *prohibition* on a consuming epic constraining a table it does not own. This record states the bounded circumstances in which that reading admits an exception, and states them as conditions rather than as a judgement call.

## Context

E009 (Cross-Document Identity Resolution) alters two tables E003 owns: `resolved_entity` and `resolved_entity_member`, created by `src/model/src/model/schema/versions/0010_resolved_entity.py` under TR-034, TR-035, and TR-045. The alteration adds project and run columns, re-scopes the three unique constraints those two tables carry, and then revokes `UPDATE` and `DELETE` on both from the application role. The three constraints are enumerable and there are exactly three: `uq_resolved_entity__normalized_identity UNIQUE (normalized_manufacturer, normalized_part_number)`, `uq_rem__extracted_value UNIQUE (extracted_value_id)`, and `uq_rem__po_line UNIQUE (po_line_id)`.

Ownership is registered. `specs/project-plan.md` § Shared Data Entities carries `ResolvedEntity | E003 (schema), E009 (populated) | E012, E014, E016`. That row is itself the product of E003's **TR-052**, which required E003 to *record the need to amend* the row and forbade it from performing the edit, because amendments serialize on the default branch. E009's change is a departure from the second half of that arrangement, not from the first: E009 is doing to E003's schema what the plan says only E003 does.

The decision was taken by the project owner during E009's clarification, and it is disclosed — in E009's `spec.md` § Risks and in its `plan.md` § Complexity Tracking. What did not exist until this record is a *rule*, and three facts make the absence of one expensive:

**Nothing in the repository guards these two tables.** The ownership guard is `test_e006_adds_no_column_constraint_or_index_to_a_table_e003_owns` in `src/model/tests/schema/test_table_ownership.py`. Its `E003_OWNED_TABLES` constant holds six names — `chunk`, `document`, `extracted_value`, `extracted_value_contributing_chunk`, `extraction_failure`, `field_vocabulary` — and neither identity table is among them. The guard is therefore blind to exactly this change. It is also worth stating what that guard actually asserts, because it is easy to over-read: it is scoped to **E006's** obligation, **FR-065**, which binds E006 to add zero columns, constraints, and indexes to *the six E003-owned tables E006 populates*. FR-065 says nothing about a later epic and nothing about `resolved_entity`. There is no requirement anywhere that forbids a post-E003 epic from altering an E003 table in general; the boundary has been held by three named requirements pointed at particular epics — TR-036, FR-065, TR-052 — and by convention.

**The guard's window drifts in the other direction.** `OWNERSHIP_BOUNDARY_REVISION = "0303"` and the comparison runs from that revision to chain head. Every epic that lands revisions after E006 widens the window without widening the table list, so the test increasingly measures "everything after E007's head" while still reporting its findings as E006's. A change E009 made to one of the six would be caught and **attributed to E006**.

**The privilege change reverses a recorded decision, not just a column list.** Migration `0010` grants all four verbs on both tables and records why: *"a resolved entity is a revisable judgement about identity, and E009 must be able to withdraw a merge it later finds unsupported — Principle III's 'withhold rather than merge' is worth nothing if a merge already made cannot be taken back."* Revoking `UPDATE` and `DELETE` does not extend that reasoning, it contradicts it, and it falsifies TR-084's enumeration of which tables are append-only. That is a different kind of change from adding a column, and it is why E009 carries it as a separate propagation obligation (**P-9**) rather than folding it into the column list (**P-6**).

A decision is needed now, at ADR level, for the reason ADR-0016 established: an acceptance recorded inside one epic's Risks section does not bind a reviewer reading the ownership rule. E012, E014, and E016 all read this join surface. Any of them can reach the same argument E009 reached.

## Decision Drivers

- A precedent that exists only as an instance is licence; a precedent that exists as a rule with disqualifying conditions is a boundary — so the record must be able to *refuse*
- The conditions must be checkable, and the one that matters most — that the diff is what was intended — must be asserted by a test rather than verified by review, because the existing ownership guard is demonstrably blind here
- The owning epic's normative documents must not be left silently wrong; if they will be wrong for a window, the window must be named and bounded by a recorded obligation
- Not duplicating an entity in order to avoid touching it, because the duplicate splits a join surface three epics read
- Not stalling an epic behind a governance queue it cannot enter, since amendments serialize on the default branch and a feature branch may only record the need
- Emptiness of the target tables is the fact doing the real work, and it must be stated as a condition rather than left as background

## Considered Options

### Option A: Additively extend the owning epic's tables under recorded conditions

E009 alters `resolved_entity` and `resolved_entity_member` in its own migration block, subject to admitting conditions that a later epic must also meet, and disqualifying conditions that refuse cases the admitting conditions would otherwise seem to cover.

- **Pros**:
  - The join surface stays single. `resolved_entity_member` remains the only sanctioned join between an extracted value and a purchase-order line, which is what TR-045 exists to guarantee and what E012, E014, and E016 will each query
  - The change is enumerable and can be asserted against a hardcoded expected set, which turns an unintended alteration into a test failure instead of a review miss — and gives the two tables the guard they never had
  - The tables are empty, so no written row can be invalidated by a re-scoped constraint; the guarantee being re-scoped has never been relied upon
  - The ownership shift becomes a recorded, dated obligation against named documents rather than an undocumented divergence
  - It states the conditions once, so E012, E014, and E016 inherit a boundary instead of inheriting E009's example
- **Cons**:
  - E003's `data-model.md` and `spec.md` are false about their own schema for the window between the migration landing and the amendment landing
  - It is a precedent, and a reader who skims will take the headline and not the conditions
  - The admitting conditions include judgement — "additive" is not fully mechanical once a unique key gains a column, which weakens the guarantee at the old scope even as it adds an object
  - It puts a second class of exception next to ADR-0017's, and a reviewer must now check two condition sets rather than one lifecycle sentence

### Option B: Parallel E009-owned identity tables

E009 creates its own `resolved_entity`-shaped tables in its own block, carrying the project and run scoping natively, and leaves E003's two tables untouched and permanently empty.

- **Pros**:
  - No ownership boundary is crossed; TR-036, FR-065, and TR-052 are all satisfied without an exception
  - No amendment to E003 is needed, so nothing enters the governance queue and no document is ever wrong
  - E009 owns its own schema outright and can re-scope anything without consulting another epic's record
- **Cons**:
  - **It splits the join surface**, which is the coupling this schema was shaped to avoid. `extracted_value` deliberately carries no foreign key to `purchase_order_line` (TR-045, asserted continuously by `test_extraction.py`) precisely so that `resolved_entity_member` is the single dated, reviewable place an identity claim lives. Two membership tables mean two places, and a reader of E012's detail view has to know which
  - E003's two tables become dead schema that migration `0010` still creates, still grants on, and `test_resolved_entity.py` still tests — an object nobody populates is worse than an object somebody altered, because nothing marks it as abandoned
  - It duplicates the XOR, the `NULLS DISTINCT` mechanism, and the normalization checks, all of which `0010` records as subtle and one-keyword-from-broken. A copy is a second place for that subtlety to be got wrong
  - A parallel table is worse coupling than a recorded extension, and it hides the coupling instead of removing it
  - `specs/project-plan.md`'s ResolvedEntity row would still need correcting, so the amendment is not actually avoided — only its content changes

### Option C: Block E009 until the E003 amendment lands on `main`

E009 records the need to amend, stops, and resumes once Governance has landed the amendment to E003's `spec.md` and `data-model.md` on the default branch.

- **Pros**:
  - No document is ever false. The normative statement of the schema changes before the schema does, which is the correct order
  - No exception is needed at all — E009 would be extending tables whose owning epic had already conceded the columns
  - It is the option a reader who dislikes this record will name, and it deserves to be refused on the record rather than passed over
- **Cons**:
  - `specs/project-plan.md` § Shared Artifact Surface makes Governance documents a **single writer**: one amendment in flight at a time, performed on the default branch and landed before the next begins, with a feature branch recording the need and not performing it. E009 cannot perform its own unblock
  - So the epic waits on a queue it does not control and cannot estimate. E006 is the worked example: it has been blocked on the TR-081 amendment through multiple phases, and ADR-0021 refused its own Option B partly on the ground that a **second** blocking cross-epic amendment would double the schedule cost of a defect with a local fix
  - The wait buys the elimination of a window that a recorded obligation already bounds, at the price of stalling a P1 epic that three later epics depend on
  - It also does not remove the need for this record. Whether E009 waits or not, the *rule* about when a consuming epic may extend another's tables is still unwritten, and G-9 is still open

### Option D: Keep the tables exactly as delivered and drop project and run scoping from E009

E009 accepts `uq_resolved_entity__normalized_identity` at its delivered scope and resolves identity globally rather than per project and run.

- **Pros**:
  - Zero schema change, zero amendment, zero exception — the option ADR-0021 chose in its own analogous situation, where the local fix existed
  - The delivered guarantee stays at its stronger scope: one entity per normalized manufacturer-and-part pair, full stop
- **Cons**:
  - It makes identity resolution non-re-runnable. With no run column, a second resolution pass over the same corpus collides with the first on `uq_resolved_entity__normalized_identity`, so the only way to re-resolve is to delete — which is the capability the privilege revocation is separately taking away
  - It makes merges non-comparable across projects, which is a product requirement rather than a schema preference
  - Unlike ADR-0021's Option A, there is no same-epic alternative available: the columns that would carry the scoping do not exist on tables E009 may not touch, so "fix it locally" has nothing to fix

## Decision Outcome

Chosen option: **Additively extend the owning epic's tables under recorded conditions** — a consuming epic may alter a table another epic owns, but only when every admitting condition below holds and no disqualifying condition holds.

This is the whole content of the record, and both halves are load-bearing. A record that said only "this is allowed" would make G-9 worse rather than closing it, because it would convert a single disclosed departure into an open-ended permission.

### Admitting conditions — all four must hold

1. **The owning epic's tables are empty, and the consuming epic is their first writer.** True for E009: both tables are the P2 half of E003's schema, created by `0010` and populated by nobody. Nothing in E006 can write them either — `src/model/tests/ingest/test_no_identity_claims.py` scans the ingestion packages and fails if any module so much as names `resolved_entity` or `resolved_entity_member`. **This condition is asserted by the altering migration itself**, which refuses to run if either table holds a row. That is a precondition assertion, not a "have I already run?" guard, and it does not conflict with TR-003: the migration author cannot see the contents of the database the migration will meet, and emptiness is the fact the whole exception rests on.
2. **The extension is additive at the object level: no column dropped, no type narrowed, no constraint removed without an equivalent or stronger replacement.** Re-creating a constraint at wider scope is permitted; removing a guarantee is not. The tension in that sentence is real and is resolved as follows, because widening a unique key *does* weaken what it guarantees — `UNIQUE (a)` implies `UNIQUE (b, a)` and not the converse. A re-scope is admissible only when (i) condition 1 holds, so no written row ever depended on the narrower scope, (ii) the added key column is `NOT NULL`, so the re-created constraint cannot be satisfied vacuously, and (iii) the consuming epic states the replacement guarantee at the new scope in its own requirements and asserts it. Absent (iii) the change is a removal wearing an addition's clothes.
3. **The diff is enumerated and asserted against a hardcoded expected set.** Not "no unexpected change" derived from a rule, but a literal expected set of added columns, dropped constraints, added constraints, and revoked privileges, compared against `pg_catalog` and `information_schema`. E009 adds **VR-028** and **VR-029** for this. The reason the assertion cannot be delegated to the existing guard is stated in Context: `E003_OWNED_TABLES` does not list either table, so the guard passes on this change without having looked at it.
4. **The ownership change is recorded as a propagation obligation against the owning epic's normative documents and the project plan, and the obligation is performed on the default branch.** E009 carries **P-6** (the column and constraint inventory) and **P-9** (the privilege revocation, separate because it reverses a recorded decision rather than extending one). The split is deliberate: P-6 is a list, P-9 is a reversal, and folding a reversal into a list is how a reversal goes unreviewed.

### Disqualifying conditions — any one refuses the case

1. **The owning epic's tables hold data.** This is the condition that separates this record from ADR-0021's Option B and it is not negotiable, because a re-scoped constraint on a populated table either fails on existing rows or silently admits rows the owning epic declared impossible.
2. **The change removes or narrows an existing guarantee, or reverses a recorded decision of the owning epic, without that reversal being separately recorded.** E009's privilege revocation reverses `0010`'s stated rationale that *"a resolved entity is a revisable judgement about identity"*, and it falsifies TR-084's enumeration of the append-only tables. It is admitted because it is recorded as its own obligation, P-9, and refused if it were not.
3. **No test asserts the diff.** A change verified by review is not admitted, however small. The guard being blind is what created G-9.
4. **The obligation is recorded but the extension lands on a feature branch and the obligation never lands.** A recorded obligation that is never discharged is indistinguishable at rest from an undisclosed alteration — and worse, it leaves a normative document confidently wrong. There is a specific way this can become permanent: `specs/project-plan.md`'s Epic Checklist notes that a **ticked epic is immutable** and that E001–E004 are deliberately left unticked so they remain adjustable. If E003's row is ticked before P-6 and P-9 land, the amendment becomes forbidden by the immutability rule and E003's documents stay false with no legal path to correction. The obligation and the tick are in a race, and the tick must lose.

Option B is the one worth refusing explicitly, because avoidance looks like discipline. Creating a parallel identity table would satisfy every ownership requirement in the repository while producing the exact coupling the schema was shaped to prevent: two places where an identity claim can live, with `extracted_value`'s deliberate absence of a foreign key to `purchase_order_line` no longer meaning what TR-045 says it means. The extension is visible and asserted; the duplicate is invisible and permanent.

Option C is refused on schedule mechanics rather than on principle — its principle is correct, and the cost of not following it is stated as a negative consequence below rather than argued away.

## Consequences

### Positive

- The join surface stays single. `resolved_entity_member` remains the one sanctioned, dated, reviewable place an identity claim lives, which is what E012's traceability view, E014's published merge precision, and E016's review queue each read. A parallel table is worse coupling than a recorded extension, because it moves the coupling out of the schema and into the reader's knowledge of which table is current.
- The two tables acquire the assertion they never had. VR-028 and VR-029 give `resolved_entity` and `resolved_entity_member` a hardcoded expected-diff check, where the existing ownership guard covers six other tables and would have passed silently on any change to these two.
- A later epic now cites a rule. G-9's failure mode — *"an epic after E009 alters those two tables citing E009's precedent, and nothing reports it"* — requires the citing epic to demonstrate emptiness, additivity, an asserted diff, and a landed obligation. Three of those four are false for any epic arriving after E009 has populated the tables, so the precedent narrows as soon as it is used.
- The privilege reversal is visible as a reversal. Splitting P-9 from P-6 means the sentence being contradicted — that a resolved entity is a revisable judgement — is quoted in the obligation that contradicts it, rather than being discovered later by a reader of `0010`.

### Negative

- **E003's normative `data-model.md` and its `spec.md` are false about their own schema for the window between the migration landing and the obligation landing.** This is the cost, and naming the window is the point of stating it. The change cannot be absorbed as a Plan-artifact correction: ADR-0017's condition 3 holds that adding or removing a named object or constraint is a Specify-level change, and E009's migration performs three `DROP CONSTRAINT`s and on the order of fourteen `ADD`s per its Complexity Tracking, so it reaches `spec.md` and not only `data-model.md`. During that window `data-model.md` § `resolved_entity` / `resolved_entity_member` describes columns and constraint scopes the database does not have, and it is normative while being wrong. Any epic that reads it in that window reads a false statement of the schema with an ADR-0017 authority claim attached to it.
- **It creates a precedent, and the disqualifying conditions are the only thing bounding it.** They are load-bearing rather than decorative for that reason. Condition 2's "additive" test still contains judgement at the margin, and a determined reader can argue that almost any re-scope is a widening.
- **E003's stated droppability of migration `0010` is withdrawn.** `0010`'s own docstring records that `data-model.md` puts these two tables last in the chain by design — P2 is droppable, every P1 objective has completed by `0009`, "so this revision can be removed from the chain without taking an objective with it." Once an E009 revision alters those tables, removing `0010` breaks the chain. A stated property of E003's design is lost, and it is lost quietly unless recorded here.
- **The revoke is not durable against a later blanket grant.** PostgreSQL records no negative grant. `0010` makes exactly this point about `0009`: a later `GRANT ... ON ALL TABLES IN SCHEMA public` silently re-grants everything an earlier revoke took away. P-9's revocation inherits that fragility, and nothing but a test on the delivered privilege set will catch its undoing.
- **A second exception class now sits beside ADR-0017's.** A reviewer checking an ownership question must check ADR-0017's four conditions, this record's four-plus-four, and ADR-0021's refusal, and decide which applies. That is more surface than one lifecycle sentence, and the compensation is only that each surface is written down.
- **The ownership guard's misattribution is now material.** With E009 revisions in the chain, `test_e006_adds_no_column_constraint_or_index_to_a_table_e003_owns` compares `0303` to head across two epics' revisions while reporting any finding as E006's. Its table list should grow to cover the identity tables and its window should be pinned per epic; neither is done by this record.

### Neutral

- **ADR-0021's refusal is untouched.** No clause of it is withdrawn and `chunk` gains no permission. The distinguishing facts are that `chunk` had a same-epic alternative, that E006 was already blocked on one amendment, and that `chunk` is a table later epics plan reads against — none of which hold for two empty P2 tables whose only planned writer is the epic altering them.
- **ADR-0017's conditions are unchanged**, and this record depends on condition 3 rather than excepting it: condition 3 is why the change must reach `spec.md`. What this record adds is the circumstance under which the Specify-level change may be *made by the consuming epic* and reconciled afterwards, instead of being made by the owning epic first.
- **ADR-0013's schema ownership and ADR-0016's client-access clarification are unaffected.** The schema and its assets stay in `/src/model`; who may connect is untouched. This record is about who may alter a table, not where the schema lives or which entry may read it.
- **TR-036 is unaffected.** It bars E003 from *creating* tables that E004, E009, and E017 own, and E009 creates no E003 table. The direction this record governs is the opposite one and was never covered by TR-036.
- **E009 must claim a migration block, and the claim moves a control.** `tests/checks/test_migration_ranges.py` currently uses `0500` as its just-past-the-last-block negative control, and its docstring records that the probe has already moved twice for exactly this reason. A block claim at `0500`-`0599` makes `0500` a real number and the probe must move again; left behind, it asserts that a declared number is undeclared, which is the off-by-one it exists to catch pointed at the test instead of at the table.
- **The epic-to-decision mapping for this record is recorded.** `specs/project-plan.md` § Architecture Decisions is the only place this project checks whether a decision reached an epic, and that section's own Uncovered Items note records that it has silently omitted records twice. The `ADR-0023 | accepted | E003, E009` row is present there, and the `specs/sad.md` catalog row is present too — the catalog table carries no mapping column, which is why the two are recorded in different documents. *(This item read "not yet recorded" when the record was authored, which was true then: the project-plan row is a separate amendment in the same serialized queue as P-6 and P-9, and it landed after this one. Corrected here rather than left standing, because a consequence that says a thing is missing when it is present is the shape of stale annotation this project has repeatedly had to chase.)*
- **Nothing here licenses a consuming epic to alter a table it merely reads.** Condition 1 requires the consuming epic to be the table's first writer, and registered ownership — `E003 (schema), E009 (populated)` — is what makes E009 that writer. An epic with no populate claim on a table has no case under this record at all.

## Links

- [specs/00009-cross-document-identity-resolution/data-model.md](../00009-cross-document-identity-resolution/data-model.md) — **G-9**, the gap this record closes, and the enumerated column, constraint, and privilege diff
- [specs/00009-cross-document-identity-resolution/spec.md](../00009-cross-document-identity-resolution/spec.md) — § Risks, where the project-owner decision is disclosed; **VR-028** and **VR-029**, the condition-3 assertions
- [specs/00009-cross-document-identity-resolution/plan.md](../00009-cross-document-identity-resolution/plan.md) — § Complexity Tracking, and the propagation obligations **P-6** (column and constraint inventory) and **P-9** (privilege revocation, recorded separately because it reverses a decision)
- `src/model/src/model/schema/versions/0010_resolved_entity.py` — the delivered tables, the three unique constraints, the `NULLS DISTINCT` mechanism, the all-four-verbs grant and its *"revisable judgement about identity"* rationale, and the recorded droppability of the revision
- `src/model/tests/schema/test_table_ownership.py` — `E003_OWNED_TABLES` (six names, neither identity table) and `OWNERSHIP_BOUNDARY_REVISION = "0303"`: the guard that is blind to this change in its table list and over-broad in its window
- `src/model/tests/ingest/test_no_identity_claims.py` — the source scan that keeps E006 from writing either table, and part of the evidence for condition 1
- `src/model/tests/schema/test_resolved_entity.py` — what is asserted about these tables today, and the file an extension must keep passing
- [specs/00003-core-data-schema/spec.md](../00003-core-data-schema/spec.md) — **TR-034**, **TR-035**, **TR-045** (the sanctioned join surface), **TR-036** (which tables E003 must not create), **TR-052** (record the amendment, do not perform it), **TR-084** (the append-only enumeration P-9 falsifies)
- [specs/00003-core-data-schema/data-model.md](../00003-core-data-schema/data-model.md) — the normative statement that is false for the window named above
- [specs/00006-document-ingestion-and-extraction/spec.md](../00006-document-ingestion-and-extraction/spec.md) — **FR-065**, which binds E006 to the six tables it populates and does not reach `resolved_entity`
- [ADR-0017](0017-plan-phase-artifact-normative-over-a-specify-phase-requirement.md) — condition 3 is why this change reaches `spec.md` rather than stopping at `data-model.md`; unchanged by this record
- [ADR-0021](0021-superseded-generations-are-removed-at-promotion-not-retained.md) — the refusal of a structurally similar widening on `chunk`, which stands; the disqualifying conditions are what keep the two apart
- [ADR-0016](0016-database-client-access-is-not-restricted-by-schema-ownership.md) — the precedent that a feature-local acceptance does not bind a reviewer reading the record, which is why a Risks-section disclosure was not enough
- [specs/project-plan.md](../project-plan.md) — § Shared Data Entities (`ResolvedEntity | E003 (schema), E009 (populated) | E012, E014, E016`), § Shared Artifact Surface (single-writer amendments on the default branch), § Epic Checklist (ticked epics are immutable), § Architecture Decisions (needs an `ADR-0023 | accepted | E003, E009` row)
- `tests/checks/test_migration_ranges.py` — `DECLARED_BLOCKS` and the `0500` negative control that E009's block claim moves
- [specs/sad.md](../sad.md) — ADR catalog; requires a new row
- E003 — the owning epic; E009 — the consuming epic and the evidence base for the conditions
- E012, E014, E016 — the readers of this join surface, and the epics most able to cite this record next
