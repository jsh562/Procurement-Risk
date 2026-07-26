---
adr_id: ADR-0017
status: accepted
date: 2026-07-26
tags: [governance, process, artifacts, traceability, schema]
supersedes: []
superseded_by: ""
related_artifacts: ["specs/00002-core-data-schema/spec.md", "specs/00002-core-data-schema/data-model.md", "specs/00002-core-data-schema/plan.md", "ADR-0013", "specs/sad.md", "E003", "E004", "E005", "E006", "E007", "E009", "E010"]
deciders: ["Project owner", "Solution architect"]
---

# ADR-0017: A Plan-Phase Artifact May Be Declared Normative Over a Specify-Phase Requirement

## Status

Accepted. Supersedes nothing — this record is additive and changes no earlier decision.

It discharges **AR-4** in `specs/00002-core-data-schema/plan.md` § Amendment Requests, which recorded that the authority direction E003 adopted needs a decision record before it binds any epic beyond E003, and which could not be written from a feature branch.

## Context

The SDDP lifecycle is `Specify → Clarify → Plan → Checklist → Tasks → Analyze → Implement → QC`, and `AGENTS.md` calls that order strict. The authority direction normally follows it: `spec.md` states the obligations, and the Plan artifacts — `plan.md`, `data-model.md` — derive from them. A Plan artifact records *how*; it does not get to change *what*.

E003 inverts that direction in four requirements — **TR-056, TR-065, TR-076, TR-083** in `specs/00002-core-data-schema/spec.md`. TR-083 is the clearest: it requires every table and column to be defined in `data-model.md`, makes that document normative, and forbids creating an object absent from it. The document takes the mandate at full width — its §Conventions opens by declaring every table, column, named constraint, index, seeded row, and state transition normative — and it is where the migration sequence and the per-table detail actually live. TR-065 is the same shape narrower: it requires the closing-event mechanism actually taken to be recorded in the document's invariant-to-mechanism map, so which rung of the fallback ladder governs is a Plan-artifact fact. TR-076 makes the migration DDL literal authoritative over the published `schema_constants` row. TR-056 puts the reversal trigger and production-scale alternative for three constants in the document's declared-constants scope-decision record.

So a Plan re-run can invalidate a Specify requirement. That is the inversion, and it was raised as analysis finding **A-012** (MEDIUM, "lifecycle inversion") against E003. It was deferred through five phases, examined against the delivered code after QC had already passed, and accepted there; the write-up is in `spec.md` § Compliance Check. A related finding, **A-010**, is the same inversion caught in a form that *was* corrected: TR-041 and TR-077 both referred to "the declared format" for `document_id` while no requirement declared it — the format existed only in `data-model.md` — and the fix was to declare it in TR-041. The difference between A-010 and A-012 is scope, not kind: one identifier's format lifts into a requirement cheaply, and thirteen tables of DDL detail does not.

The decision is needed as a project-level record because E003's schema is the substrate for E004, E005, E006, E007, E009, and E010. Each of those epics reads `data-model.md` by name — E007 writes the draw and survival arrays under its declared array semantics, E010 reads them, E009 populates `resolved_entity` — and each will face the same question about whether a Plan artifact may bind it. An acceptance recorded inside one epic's Compliance Check does not bind a reviewer reading the lifecycle rule, which is exactly the failure mode ADR-0016 documented for a feature-local interpretation of ADR-0013.

## Decision Drivers

- One normative statement per fact, because two copies of a DDL declaration drift and E003 has the defects to prove it
- A bounded, named delegation rather than a blanket handover of Specify authority
- An agreement enforced by a test rather than by review
- Not letting a form declared during planning outrank what PostgreSQL 16 actually accepts
- Settling the direction once, before six downstream epics each re-argue it against the same lifecycle sentence

## Considered Options

### Option A: Accept the inversion under stated conditions

A Specify-phase requirement may name a Plan-phase artifact as normative for a bounded scope. Four conditions apply, all drawn from E003's precedent and all checkable:

1. **The requirement names the artifact and the scope.** TR-083 names `data-model.md` and the class of facts it governs — every table and column created, and reader-facing semantics. A requirement that delegates "the technical detail" without naming the file and the class of facts does not qualify.
2. **Amendments to the normative section are labelled corrections carrying their evidence.** The note states the previously declared form, the delivered form, and the observation that shows the declared form was wrong.
3. **An amendment may not add or remove a named object or constraint.** The object and constraint inventory is identical before and after. A new table, view, index, constraint, or seeded row is a Specify-level change. Corrections narrow the gap between declaration and reality; they never extend scope.
4. **A test asserts the delivered schema against the named artifact.** The agreement fails a build, not a review.

- **Pros**:
  - The normative source stays single, so there is nothing for a second copy to disagree with
  - The mitigation is demonstrated rather than proposed — `data-model.md` was corrected four times during E003's implementation, each time toward what PostgreSQL accepts, each time with the evidence recorded and the object inventory unchanged
  - Condition 3 keeps object-level authority in Specify, which is where the scope questions live; a Plan re-run may sharpen a predicate, not invent a table
  - Condition 4 is already met in E003 by `src/model/tests/schema/test_table_ownership.py`, which enumerates relations, constraints, and functions from `pg_catalog` and requires each name to appear in the document, and by `test_extraction.py`, which asserts TR-081, TR-082, and TR-085 against the document because no constraint can carry them
  - Six downstream epics inherit a stated condition set instead of a sentence each would read against `AGENTS.md` on its own
- **Cons**:
  - The lifecycle order that `AGENTS.md` calls strict now has a sanctioned exception, and every future use of it is one more place a reader must check the conditions were honoured
  - Three of the four conditions are conventions a reviewer must verify; only the object-inventory condition is asserted by a test today
  - A reader of `spec.md` alone cannot see the obligation TR-083 delegates and must follow into `data-model.md` to find it

### Option B: Lift the DDL detail into `spec.md` so authority follows the lifecycle

Move the constraint definitions, referential actions, trim sets, generated-column expressions, and per-table detail for thirteen tables into requirements, leaving `data-model.md` a derived restatement.

- **Pros**:
  - The lifecycle order holds without exception, and no Plan re-run can touch an obligation
  - Every obligation is visible in the artifact a reviewer opens first
- **Cons**:
  - It does not remove the inversion, it duplicates the normative source — and two copies of a DDL declaration drift
  - E003 has direct evidence that they drift. Four separate defects in that epic were exactly a declared form disagreeing with the delivered one, and the QC pass found a fifth class of the same problem in stale counts: `plan.md` still claimed four helper functions and ten disclosed gaps against a delivered five and eleven, which `data-model.md` had right
  - The duplicate would need its own drift test, so the cost is the copy *plus* the enforcement that the copy agrees — against a single source that already has one test
  - It puts several hundred named constraints into a requirement set that the same analysis pass already faulted for 33 unintegrated appended requirements

### Option C: Demote `data-model.md` to non-normative and let the delivered schema be its own authority

The migrations and the database are the truth; the document becomes commentary.

- **Pros**:
  - No inversion, no duplication, and no artifact can ever be wrong about the schema
  - Nothing to amend when the delivered form differs from the planned one
- **Cons**:
  - It removes the traceability TR-083 exists to provide. `data-model.md`'s requirement-to-mechanism table is what maps 86 requirements onto named objects, and `test_table_ownership.py` would have no artifact to check the catalog against — an object nobody documented becomes an object nobody reviewed
  - It leaves no reviewable statement of intent, so a constraint dropped for being slow is indistinguishable from one that was never meant to exist. `ix_forecast_run__single_active` is the entire mechanism behind "at most one active run"; nothing in the DDL says so
  - Reader-facing semantics that no constraint can carry — TR-081's "confidence is self-reported, never calibrated", TR-082's agent granularity, TR-085's retention — would have nowhere normative to live and would become unenforceable rather than document-enforced
  - Later epics reference these names verbatim; with no normative inventory, E007 and E010 have no statement of the array contract to build against

## Decision Outcome

Chosen option: **Accept the inversion under stated conditions** — a Specify-phase requirement may declare a Plan-phase artifact normative for a bounded, named scope, provided amendments to that scope are labelled corrections carrying their evidence, add and remove no named object or constraint, and are enforced by a test rather than by review.

Option B is the intuitive fix and it is the wrong one, because the inversion is not the actual risk — divergence between what is declared and what is delivered is, and Option B creates a second place for that divergence to live. E003 already paid that cost four times over in a single source; two sources would have doubled the surface without removing the inversion, since a derived restatement still has to be kept honest. Option C removes the divergence by removing the declaration, which is cheaper only until someone needs to know whether a partial index was load-bearing.

What makes Option A defensible is that the correction discipline is demonstrated, not promised. `data-model.md` was amended four times during E003's implementation:

1. **`fk_lifecycle_event__chain`, `MATCH FULL` → `MATCH SIMPLE`** (implementation of `0007`). `MATCH FULL` does not skip a partially-null referencing triple, it *refuses* it — it permits all-null, requires all-matching, and rejects everything between. On a sequence-1 event `prev_sequence_no` and `from_state` are both NULL while `po_line_id` is NOT NULL, so the declared form made every purchase-order line's opening event unrepresentable, and with it the line's entire history. Verified against PostgreSQL 16: under `MATCH FULL` the sequence-1 insert is rejected with `ForeignKeyViolation` naming that constraint, SQLSTATE 23503; under `MATCH SIMPLE` the insert and its chained successor are accepted and a forged `from_state` at sequence 3 is still rejected.
2. **`ck_line_posterior__draws_length` and `ck_line_posterior__survival_length` strengthened to `coalesce(array_length(…, 1), 0) = N`** (implementation of `0008`). `array_length('{}', 1)` is NULL, not 0 — an empty array has no dimensions — and a `CHECK` rejects only on false, so the declared form *accepted* an artifact row with no draws at all.
3. **`ck_line_posterior__draws_1d` and `ck_line_posterior__survival_1d` extended with `array_lower(…, 1) = 1`** (implementation of `0008`). PostgreSQL array subscripts need not start at 1, and both documented read conventions subscript directly, so a legal lower-bound-0 array of the declared length puts its last element beyond subscript reach — `survival[horizon_days]` is then NULL, and the residual-tail check that depends on it is NULL and therefore satisfied.
4. **Migration `0009`'s traceability row gained a third named table.** The revoke of `UPDATE` and `DELETE` from the application role reached `extracted_value` and `extraction_failure` but not `extracted_value_contributing_chunk`; with that table left mutable, a citation set can be truncated without a statement touching either other table.

Every one of those is a correction *toward* the delivered reality, with the reason recorded. None adds a constraint name; none adds an object — the fourth extends a grant statement to a table migration `0006` had already created. That is the authority working as intended: the normative artifact was corrected, rather than the code being bent to a declared form that PostgreSQL does not support. Condition 3 is what keeps this narrow, and it is the condition that separates a correction from a scope change: the first amendment changed a match type, not the constraint's existence.

## Consequences

### Positive

- One normative source per fact, so the failure mode Option B would have created — two declarations of the same constraint, disagreeing — cannot arise. The four E003 corrections each had exactly one place to land.
- Corrections flow toward the delivered reality and carry their evidence, which makes the amendment record readable as a defect log. All four E003 amendments name the observed PostgreSQL 16 behaviour that invalidated the declared form.
- Object-level authority stays in Specify. A Plan re-run may sharpen a predicate that PostgreSQL evaluates wrongly; it may not create or delete a table, view, index, constraint, or seeded row, which is where the scope decisions and the epic-ownership boundaries live (TR-036).
- The reader-facing obligations that no constraint can carry — TR-081, TR-082, TR-085 — have a normative home and a test asserting they are stated there, instead of being unenforceable prose or an invented schema assertion.
- E004, E005, E006, E007, E009, and E010 inherit a settled direction with conditions attached, rather than each deciding for itself whether `data-model.md` binds it.

### Negative

- **E003's enforcement is name-level only, and this is a real limitation of the mitigation.** `src/model/tests/schema/test_table_ownership.py` enumerates relations, constraints, and functions from `pg_catalog` and requires each *identifier* to appear inside a `data-model.md` code span. It does not compare definitions. **A constraint whose definition drifted while keeping its name would not be caught** — a `CHECK` predicate weakened, a referential action changed from `RESTRICT` to `CASCADE`, or a `MATCH` type altered all pass so long as the name is still written down somewhere. Manual comparison during E003's QC found no such drift, but the axis is unguarded rather than clean. Closing it means comparing `pg_get_constraintdef` output against the definitions the document carries, which the document already writes in near-DDL form for exactly this reason.
- Three of the four conditions rest on convention. Condition 3 is testable today because the object inventory is enumerable from the catalog; conditions 1, 2, and 4 are verified by a reviewer reading the requirement text and the amendment notes.
- The lifecycle order now has a sanctioned exception, and a future epic can cite this record for a delegation far broader than TR-083's. The bound is the phrase "bounded, named scope", which is judgement, not a check.
- A reader of `spec.md` alone sees a delegation and not the obligation. TR-083 tells them where to look; it does not tell them what they will find, and the detail is thirteen tables away.

### Neutral

- The lifecycle order is otherwise unchanged, and nothing here lets a Plan artifact *originate* an obligation. `data-model.md` is normative because a requirement said so, for the scope that requirement named; a fact it states outside that scope binds nobody.
- The delegation is per-requirement, not global. A later epic that wants its own Plan artifact to be normative names it in its own requirement and meets the four conditions; this record sanctions the pattern, not any particular artifact.
- ADR-0013's schema ownership and ADR-0016's driver clarification are untouched. This record is about which artifact states the schema, not about which entry owns it or which entry may connect to it.
- A-012's own history is a process artifact worth keeping visible: the finding's definition was destroyed by a later analysis pass that renumbered to a B-series and overwrote the report in place, and had to be recovered with `git show 7138026:specs/00002-core-data-schema/analysis-report.md` while four artifacts — including a `.qc-passed` marker — still cited it by ID. `plan.md` records the rule change for that separately as AR-3; this record does not depend on it.

## Links

- [specs/00002-core-data-schema/spec.md](../00002-core-data-schema/spec.md) — TR-056, TR-065, TR-076, TR-083 are the four inverted requirements; § Compliance Check carries A-012's acceptance and its mitigation
- [specs/00002-core-data-schema/data-model.md](../00002-core-data-schema/data-model.md) — the normative artifact, its §Conventions declaration, and the four labelled corrections with their PostgreSQL 16 evidence
- [specs/00002-core-data-schema/plan.md](../00002-core-data-schema/plan.md) — **AR-4**, the amendment request this record discharges; AR-3 covers the analysis-report overwrite that lost A-012's definition
- [specs/00002-core-data-schema/analysis-report.md](../00002-core-data-schema/analysis-report.md) — A-012 as raised and as closed, and A-010 as the same inversion corrected at a scope where lifting was cheap
- `src/model/tests/schema/test_table_ownership.py` — the condition-4 enforcement for E003, and the file whose name-level matching is the disclosed limitation
- `src/model/tests/schema/test_extraction.py` — asserts TR-081, TR-082, and TR-085 against the document, since no constraint can carry them
- [ADR-0013](../adrs/0013-schema-ownership-in-the-modeling-entry.md) — puts the schema and its assets in `/src/model`; this record governs which artifact normatively states that schema
- [ADR-0016](../adrs/0016-database-client-access-is-not-restricted-by-schema-ownership.md) — the precedent that a feature-local interpretation does not bind a reviewer reading the record, which is why AR-4 required an ADR rather than a spec note
- `AGENTS.md` — the lifecycle order this record takes a bounded exception to
- [specs/sad.md](../sad.md) — ADR catalog; requires a new row
- E003 — the epic whose delivery is the evidence base for the conditions
- E004, E005, E006, E007, E009, E010 — the epics that consume this schema and inherit the authority direction
