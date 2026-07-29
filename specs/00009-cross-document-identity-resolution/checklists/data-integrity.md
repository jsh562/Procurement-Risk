# Checklist: Data Integrity — Requirements Quality

**Feature**: E009 Cross-Document Identity Resolution | **Domain**: Data Integrity | **Depth**: Standard | **Audience**: Reviewer (PR) | **Date**: 2026-07-29

> These items test whether the requirements governing stored data are clear, complete, consistent and unambiguous. They do not test implementation behavior. The design's stated method is to make a criterion *unrepresentable to violate* rather than checked afterward, so several items ask whether that claim is actually specified or merely asserted.

## Append-only runs and run scoping

- [ ] CHK001 Is "a later run MUST NOT alter or delete an earlier run's rows" specified precisely enough to exclude soft-delete, supersession, and flipping an active flag on old rows — all of which leave earlier rows technically present? [Unambiguity, FR-039, SC-033]
- [ ] CHK002 Is the active-run pointer's state defined when **no** run is active — before the first run, and after a run refuses? [Completeness, FR-039, SC-033]
- [ ] CHK003 Is it specified what the pointer does when a run fails **midway**, given FR-035 requires an all-or-nothing write? [Completeness, FR-035, FR-039]
- [ ] CHK004 Is the prohibition on selecting a run **by recency** stated as a requirement on consumers, so a downstream epic reading `MAX(run_id)` is a violation rather than a style choice? [Clarity, FR-039]
- [ ] CHK005 Is at most one active run per **project** specified, rather than one globally — given FR-020 scopes identity within a project? [Unambiguity, FR-020, FR-039]
- [ ] CHK006 Does the requirement set state that `UPDATE` and `DELETE` are revoked on the entity tables, and is that stated as a *requirement* rather than only as a migration detail? An append-only guarantee enforced by privilege needs to be findable from the requirements. [Completeness, FR-039, plan P-9]

## The E003 table extension

- [ ] CHK007 Does FR-045 enumerate **every** constraint the extension touches? It names `uq_rem__extracted_value` and `uq_rem__po_line`; `uq_resolved_entity__normalized_identity` is re-scoped on the same reasoning but is not named. [Completeness, FR-045, data-model A-2]
- [ ] CHK008 Is the consequence of `0509`'s revocation of `UPDATE`/`DELETE` specified for any **existing** consumer that E003's `0010` explicitly permitted to revise a resolved entity? [Completeness, FR-039, plan P-9]
- [ ] CHK009 Is the reversal of E003's recorded rationale — "a resolved entity is a revisable judgement about identity" — disclosed as a reversal of a documented decision, not merely as a privilege change? [Clarity, plan P-9, Complexity Tracking]
- [ ] CHK010 For the columns added to **already-populated** E003 tables, is nullability, default, and backfill specified, or left for the migration to decide? [Completeness, FR-045]
- [ ] CHK011 Is the re-scoped uniqueness specified so the **old** guarantee still holds within a single run — a record in two entities across runs is permitted, within one run is not? [Unambiguity, FR-045, FR-039, plan HINT-003]
- [ ] CHK012 Is the reliance on `NULLS DISTINCT` in the re-created unique constraints stated explicitly, given it determines whether a NULL member key is deduplicated or admitted repeatedly? [Clarity, data-model, FR-045]
- [ ] CHK013 Is it specified that E003's migrations are **not** edited — the extension arrives as a new revision in E009's own block — given Alembic is forward-only? [Completeness, plan AD-003, spec Implementation Signals]

## All-or-nothing writes

- [ ] CHK014 Is the **atomic unit** defined? FR-035 requires nothing partial; is that per run, per project, or per stage? [Unambiguity, FR-035]
- [ ] CHK015 Is the set of failure modes that must trigger the all-or-nothing outcome **enumerated**, or only exemplified? FR-035 names the collision guard and hash verification; FR-044 and FR-047 add threshold and weight divergence. [Completeness, FR-035, FR-044, FR-047]
- [ ] CHK016 Is it specified that a refused run writes **no manifest** — not a manifest marked failed — so a partial artifact cannot be mistaken for a result? [Unambiguity, FR-035, SC-015]
- [ ] CHK017 Are the three outputs a refused run must not write (resolved entities, review items, manifest) stated as an exhaustive list, so a fourth output added later is covered? [Completeness, FR-035]

## Uniqueness and identity

- [ ] CHK018 Is the unordered-pair identity specified so it is **stable across runs**, not merely unique within one — the property FR-043's grouping depends on? [Completeness, FR-009, FR-043, SC-039]
- [ ] CHK019 Is a pair matching on **more than one** blocking key specified as exactly one candidate pair, and is the deduplication required at generation rather than at reporting? Deferring it double-counts every denominator. [Unambiguity, FR-009, SC-011, plan HINT-004]
- [ ] CHK020 Is a self-pair (a record paired with itself) excluded by specification rather than by implementation accident? [Completeness, FR-009]
- [ ] CHK021 For "at most one specification section per cluster", is the disposition of the **rejected** record specified — rejected and named, but is it dropped, queued, or left unclustered? [Completeness, FR-041, SC-041]
- [ ] CHK022 Is the cross-project prohibition specified at **both** the blocking and clustering stages, so a single-stage bug cannot produce a cross-project entity? [Completeness, FR-020, SC-006]
- [ ] CHK023 Is "purchase-order lines are unbounded within a cluster" stated positively, so an implementation capping them is a violation? [Clarity, FR-041, SC-034]

## Digests, hashes and versioning

- [ ] CHK024 Is the **canonical serialization** each digest is computed over declared — for the alias artifact and for the labeled set both? A digest over an unspecified serialization is not reproducible and FR-004's version identifier is not verifiable. [Completeness, FR-004, FR-017, FR-040]
- [ ] CHK025 Are the two strata's **separate hashes** distinguishable everywhere a hash is referenced, so verifying one is not mistaken for verifying both? [Unambiguity, FR-017, FR-037, SC-037]
- [ ] CHK026 Is the alias-table version required on **every merge**, not only on the run, so a merge remains interpretable after the table is edited? [Completeness, FR-004, SC-029]
- [ ] CHK027 Is the E002 catalogue digest recorded **separately** from the alias-table version, so editing the table without reseeding is detectable? [Clarity, FR-040, SC-036]
- [ ] CHK028 Is the weight vector bound to the **same** labeled-set hash as the thresholds, with divergence in either causing refusal? [Consistency, FR-047, FR-044, SC-043]
- [ ] CHK029 Is the widening of the referenced unique key from four columns to eight specified as preceding the referencing foreign key, given forward-only migration makes the ordering irreversible? [Completeness, FR-047, plan AD-005]

## Alias table integrity

- [ ] CHK030 Is alias-to-canonical stated as a **function**, with a duplicate alias failing **table load** — explicitly not resolved by a runtime tie-break, first-match rule, or score? [Unambiguity, FR-002, SC-028]
- [ ] CHK031 Is "exactly one preferred label per canonical manufacturer" specified structurally rather than as a validation rule that could be skipped? [Completeness, FR-001, plan AD-004]
- [ ] CHK032 Are the three alias classes specified as **disjoint**, so a string cannot be both display-worthy and match-only? [Unambiguity, FR-001, plan AD-004]
- [ ] CHK033 Is additive normalization specified as retaining the raw string **in the same record** as the normalized form, so no stage can discard it? [Completeness, FR-003, SC-010]
- [ ] CHK034 Is the collision guard's test population specified — the labeled **negative** pairs, from the hard-negative stratum — rather than left as "known distinct records"? [Unambiguity, FR-007, FR-037]

## Units

- [ ] CHK035 Are non-dimensional units specified as carrying **no** base and **no** factor, so "never converted" has nothing to multiply by rather than relying on a code path? [Completeness, FR-005, SC-009]
- [ ] CHK036 Is unit agreement specified as a scored attribute that can never act as a hard filter, stated as a prohibition rather than a preference? [Unambiguity, FR-006, SC-008]

## Referential integrity against upstream

- [ ] CHK037 Are the assumptions about E006's extraction output — which columns are read and their guarantees — **stated** rather than assumed, so an upstream change breaks visibly? [Completeness, spec Assumptions, plan Integration Points]
- [ ] CHK038 Is the per-attribute score's field reference required to resolve against E003's `field_vocabulary` rather than a free-text field name? [Completeness, FR-014]

---

**Traceability**: 38 of 38 items carry a reference (100%).
**Authoring note**: written from the plan and the data model's structural summary rather than a line-by-line read of `data-model.md`, after five consecutive API failures took out the delegated planner. Items CHK007, CHK012, CHK024 and CHK029 are the ones most dependent on constraint-level detail; evaluation should verify them against `data-model.md` directly rather than accepting them as framed.
