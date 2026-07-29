# Checklist: Testing — Requirements Quality

**Feature**: E009 Cross-Document Identity Resolution | **Domain**: Testing | **Depth**: Standard | **Audience**: Reviewer (PR) | **Date**: 2026-07-29

> These items test whether the requirements and success criteria are stated so that a test could be written against them **and could fail**. They do not test implementation behavior. An item passes when the artifact is clear, complete, consistent and unambiguous on the point raised — not when the code works.

## Testability of the success criteria themselves

- [ ] CHK001 Is every escape branch in a success criterion given a triggering condition a test can select deterministically, rather than a condition an implementation chooses? [Testability, SC-017, SC-018, SC-019]
- [ ] CHK002 Can SC-013 ("all pairs withheld: run completes, zero merges, precision undefined") be distinguished in requirements from a run that generated no candidate pairs at all? If not, a system that blocks nothing passes it. [Unambiguity, SC-013, FR-024]
- [ ] CHK003 Does SC-034 ("a cluster containing several purchase-order lines is emitted without error") state a positive outcome that fails for an implementation producing no clusters? [Testability, SC-034, STF-006]
- [ ] CHK004 Is SC-005's failure condition — "a pair present in a cluster with no recorded score is a failure" — stated over the *induced* pair set rather than the scored set, so transitive joining is detectable? [Completeness, SC-005, FR-018]
- [ ] CHK005 Does each success criterion name the requirement it grades, so a criterion amended without its requirement (or the reverse) is visible? [Traceability, Success Criteria]
- [ ] CHK006 Are the criteria that certify a *refusal* (SC-015, SC-040, SC-043) specified with what must be absent as well as what must be reported, so a partial write cannot pass? [Completeness, SC-015, SC-040, SC-043, FR-035]
- [ ] CHK007 Is SC-041's rejection outcome ("naming the offending record") specified precisely enough to assert on, as opposed to any error? [Unambiguity, SC-041, FR-041]

## The strict test-first mandate

- [ ] CHK008 Is the boundary that decides which modules take both the test-first and property-based mandates stated as a **rule** — output is a number that is stored or published — rather than an enumeration, so a module added later can be classified without a ruling? [Clarity, plan AD-001, project-instructions § Testing & Quality Policy]
- [ ] CHK009 Is the completion condition for a test task specified as an **observed** failure — a collection error for the absent module, never a green suite — and recorded on the task line, so ordering survives a squash merge? [Testability, plan Testing Strategy, tasks T025/T033/T035/T059]
- [ ] CHK010 For `compute/metrics.py`, which already exists, is the observed-failure condition restated in a form that can actually occur (a collection error naming the absent estimators) rather than one that cannot? [Consistency, tasks T059, FR-027]
- [ ] CHK011 Does every `model/compute/` module in the plan have a test task that **precedes and names** its implementation task? [Completeness, plan AD-001, tasks T025→T026, T033→T034, T035→T036, T059→T060]
- [ ] CHK012 Is the FR-047 weight calibration covered by the existing `calibrate` test-first pair rather than added as an untested afterthought? [Completeness, FR-047, tasks T025, T026]

## Property-based coverage and relation classes

- [ ] CHK013 Is a **relation class** declared per module, rather than "property-based tests" named as a bare tool? [Clarity, plan Testing Strategy — Property tier]
- [ ] CHK014 Is the requirement that `decide` be enumerated **exhaustively** at and around both cutoffs — not sampled — stated as a requirement, given that a sampler will miss exact boundary values? [Completeness, SC-016, SC-035, FR-042]
- [ ] CHK015 Does the `metrics` relation class cover **estimator selection** as well as interval containment, so the three-way branch (rule of three / exact binomial / undefined) is exercised rather than assumed? [Completeness, FR-027, FR-028, SC-019]
- [ ] CHK016 Is `pair_score`'s monotonicity relation stated over the component agreements, so a weight sign error is detectable? [Testability, FR-014, plan Property tier]
- [ ] CHK017 Is `calibrate`'s frozen-set dependence stated as a property — a perturbed set yields a detectably different constant pair — rather than left as a test case? [Testability, FR-016, FR-047]

## Denominators and populations

- [ ] CHK018 Is merge precision's denominator stated unambiguously as **merges among labeled pairs** — not the labeled-set size, not the run's total merge count — everywhere precision is named? [Unambiguity, FR-027, FR-019, SC-021]
- [ ] CHK019 Is recall's denominator stated as the true pairs in the frozen labeled set, and is every way a true pair can be missed (withheld, rejected, never blocked) enumerated as a miss? [Completeness, FR-030, SC-024]
- [ ] CHK020 Does every published figure name the **stratum** it draws on, and is computing across the union of the two strata prohibited rather than merely discouraged? [Unambiguity, FR-037, FR-037a, SC-037]
- [ ] CHK021 Is the hard-negative stratum's exclusion from *every* published denominator stated once in a way that binds all figures, rather than repeated per figure with the risk of omission? [Consistency, FR-037, SC-037]
- [ ] CHK022 Is the census/estimate classification (FR-038) applied to every figure the feature publishes, with none left unclassified? [Completeness, FR-025, FR-038, SC-031]
- [ ] CHK023 Where a figure's interval is undefined at the realized sample size, is the required output specified as "undefined **with its denominator**" rather than omission or zero? [Completeness, FR-028, SC-013]
- [ ] CHK024 Is the n < 30 disclosure obligation tied to the realized denominator rather than to the labeled-set size? [Unambiguity, FR-027, SC-020]

## Frozen-set discipline

- [ ] CHK025 Is "frozen and hashed **before** calibration" expressed as a checkable ordering with an artifact that evidences it, rather than as an intention? [Testability, FR-016, FR-017, plan HINT-001, tasks T023→T024→T025]
- [ ] CHK026 Is the refusal-on-divergence behavior stated **symmetrically** for thresholds and weights, so neither is frozen while the other floats? [Consistency, FR-044, FR-047, SC-040, SC-043]
- [ ] CHK027 Are the two strata's **separate hashes** distinguishable everywhere a hash is referenced, so verifying one cannot be mistaken for verifying both? [Unambiguity, FR-017, FR-037, SC-037]
- [ ] CHK028 Is the canonical serialization each hash is computed over declared, for the labeled set **and** the alias artifact? An unspecified serialization makes the hash unreproducible and the verification untestable. [Completeness, FR-004, FR-017, FR-040]
- [ ] CHK029 Does SC-043's verification method (perturb one weight, assert refusal) generalize across all four weights rather than certifying one? [Completeness, SC-043, FR-047]

## The baseline

- [ ] CHK030 Is "same stratum, same estimator" specified precisely enough that the baseline's figure and the resolver's are comparable rather than merely adjacent? [Unambiguity, FR-046, SC-042]
- [ ] CHK031 Is the baseline's normalization specified — shared with the resolver, not private — so both figures are computed over the same values? [Consistency, FR-046, plan AD-012]
- [ ] CHK032 Is the constraint that `normalize.py` hold **only** syntactic transforms stated as a requirement, so an alias lookup leaking into it would be a violation rather than a design drift the import contract cannot see? [Completeness, plan AD-012, tasks T029]
- [ ] CHK033 Is the baseline's strength label (strong / weak) required to be *declared*, and declared before the figures exist rather than chosen after? [Testability, FR-046, project-instructions § Principle VIII]

## Existing tests this feature falsifies

- [ ] CHK034 Are the changes to `test_migration_ranges.py` specified as three distinct edits, including **moving the negative-control probe off `0500`** — which becomes a real revision number and silently stops controlling for anything? [Completeness, plan HINT-005, tasks T001]
- [ ] CHK035 Are the eight assertions in `test_resolved_entity.py` that `0505` falsifies required to be **restated** against the run-scoped constraints rather than deleted, so the re-scoping does not go unasserted? [Completeness, plan HINT-003, tasks T019]
- [ ] CHK036 Is the gap in `test_table_ownership.py` — `E003_OWNED_TABLES` names neither `resolved_entity` nor `resolved_entity_member`, so the FR-065 guard is blind to exactly the alteration FR-045 performs — recorded as a required change rather than left for discovery? [Completeness, plan Propagation Obligations note, tasks T020, T021]
- [ ] CHK037 Is the gateway migration test required to be **extended with E009's block** rather than re-pinned to a new head, and its positive control over an undamaged revision directory preserved? [Consistency, plan HINT-005, tasks T022]

## Coverage denominator

- [ ] CHK038 Is it stated that the coverage `--source` lists **override rather than merge**, so omitting `identity` leaves every line this epic writes outside the denominator while the gate still reports green? [Clarity, plan HINT-002]
- [ ] CHK039 Are **both** locations named — `.github/workflows/verify.yml` and the **root** `pyproject.toml` (`[tool.coverage.run] source` and `[tool.coverage.paths]`) — rather than one? [Completeness, plan Project Structure, tasks T003]
- [ ] CHK040 Is a per-package floor on `model.identity` required in addition to the combined floor, so an already-covered package cannot carry a newly added one across the threshold? [Completeness, plan Testing Strategy — Coverage tier]

---

**Traceability**: 40 of 40 items carry a reference (100%).
**Not covered by design**: integration-tier database behavior and CI wiring are graded by the Data Integrity checklist and by QC respectively; this checklist stops at whether the requirements admit a failing test.
