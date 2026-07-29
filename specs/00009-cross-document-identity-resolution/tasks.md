# Tasks: Cross-Document Identity Resolution

**Input**: Design documents from `specs/00009-cross-document-identity-resolution/`
**Prerequisites**: `plan.md`, `spec.md`, `data-model.md`, `research.md`, `checklists/.checklists` (CHL001 data-integrity, CHL002 testing, CHL003 observability — recommended, not yet authored)

**Tests**: Included. `plan.md` §Testing Strategy makes tests part of the deliverable, and the Testing & Quality Policy binds every module under `model/compute/` to **both** mandates — strict red-green-refactor **and** property-based tests over its pure functions. Four modules take it here: `compute/pair_score.py`, `compute/decide.py`, `compute/calibrate.py`, and the `compute/metrics.py` additions. Each appears as an ordered pair whose test task precedes its implementation task **and names it**, and whose completion condition is an **observed failure** — a collection error against the absent module or the absent estimators, recorded on the task line — never a green suite. Every other module in this feature is test-after. **FR-047's weight calibration is folded into the existing T025→T026 pair rather than given a pair of its own**: weights and cutoffs are calibrated by one function against one frozen set, and splitting them would produce a second red-green pair over the same module.

**Size**: 84 tasks over 48 requirements (FR-001…FR-047 including FR-037a) and 43 success criteria.

**Organization**: Grouped by user story (`US#`) per `spec_type: product`. Requirement tags are `FR-###`. `plan.md` §Requirement Coverage Map is the authority for the requirement → component → file assignment below; `data-model.md` §Migration Sequence fixes the revision order and §Write Order fixes the per-run statement sequence the writer and CLI tasks implement.

This exceeds the skill's 5–10-per-phase target in Phases 2, 3 and 5; the epic is not split because every phase reads or writes through one transaction boundary over one migration chain, and a sub-feature cut would put that boundary in two workspaces. Precedent: E006, 92 tasks, same reason.

## Project Mode

`Brownfield`

E001 scaffolded the four entries, `import-linter` and the Compose `db` service; E002 committed the manufacturer catalogue this epic's alias table derives from; E003 owns `resolved_entity` and `resolved_entity_member` and the migration runner; E006 delivered the extraction output this epic reads and left the chain head at `0404`. No generic project-initialization tasks appear here. `~` paths in `plan.md` §Project Structure are extensions of files that already exist.

## Epic / Capability Map

- `[US1]` → Link the same material across specification, submittal and purchase order — alias and unit normalization, union-key blocking, the pair scorer, the band decision, clique-constrained clustering, the run manifest, the all-or-nothing writer (P1)
- `[US2]` → Withhold uncertain pairs instead of merging them — the collision guard, the review queue per run, append-only runs and the active-run pointer, both cutoffs withholding, the privilege revoke (P1)
- `[US3]` → Read the resolution's own quality evidence — the estimators and their intervals, the census/estimate classification, the exact-match baseline, the six published figures, the shortfall with its cause (P1)
- `[US4]` → Maintain the alias table as an auditable, versioned artifact — the committed table and its datasheet, unmatched strings as data, the catalogue digest, the version diff (P3)

**All three of US1, US2 and US3 are P1.** US3 was promoted at clarification because `specs/prd.md` gates P1 release on publishing merge precision and recall, so the MVP slice must carry the registered gate's evidence.

## Brownfield Notes

- **Existing flows touched**: `tests/checks/test_migration_ranges.py`, `src/gateway/tests/test_migrations.py`, `src/model/tests/schema/test_resolved_entity.py`, `src/model/tests/schema/test_table_ownership.py`, `src/model/pyproject.toml`, the root `pyproject.toml`, `.github/workflows/verify.yml`, E003's `src/model/src/model/schema/versions/`, `src/model/src/model/compute/metrics.py`
- **Four existing tests these changes falsify, each with its own task**: T001 (the three-part `test_migration_ranges.py` edit, and the third part is the trap), T019 (`test_resolved_entity.py`'s eight assertions), T020–T021 (`test_table_ownership.py`'s blind ownership guard, VR-028/VR-029), T022 (`test_migrations.py`'s block membership — extended, never re-pinned to a new head)
- **Coverage enumerations override rather than merge (HINT-002)**: `verify.yml`'s `--source` list (T002) and the root manifest's `[tool.coverage.run] source` plus `[tool.coverage.paths]` (T003). Both edits, or every line this epic writes sits outside the denominator while the gate reports green
- **Declared artifact paths** — fixed in `plan.md` §Project Structure so no task has to invent one. The rendered run report is `specs/00009-cross-document-identity-resolution/resolution-report.md`, following E006's `ingestion-report.md` convention: reports live in the feature workspace, not under `data/`. **T064, T065, T066 and T067 all render into that one file.** The committed alias table is `data/identity/manufacturer-aliases.csv`, **CSV**, its content digest computed over a declared canonical serialization — sorted by `(canonical_key, alias_class, normalized_alias)`, LF endings, no BOM (T073). Without that rule FR-004's version identifier is not reproducible, because a digest over an editor-dependent byte stream identifies the editor as much as the contents
- **`normalize.py` holds syntactic transforms only** (T029) — case folding, whitespace, part-number segmentation — with every alias-dependent step in `alias.py`. AD-012 was corrected during task decomposition: `baseline.py` **shares** `normalize.py` and must, or FR-046's two figures are not over the same normalized values. The contract therefore forbids two modules, not three (T006). The hazard this creates is quiet: if an alias lookup leaks into `normalize.py`, `lint-imports` stays green and the baseline is contaminated through a module it is allowed to import, so the split is stated on T029 rather than left to the contract to catch
- **Ordering constraints that shape the phases**:
  - **HINT-001 is load-bearing and is visible in the list, not implied.** T023 builds both strata, T024 freezes, hashes and **commits** the artifact, and only then do T025 → T026 calibrate and T027 write the threshold constants. A constant committed before the set is frozen cannot be shown to predate it
  - **Migration chain**: `0500` → `0501` → `0502` → `0503` → `0504` → `0505` → `0506` → `0507` → `0508` → `0509` is a hard order. `0505` must follow `0503`; `0506` cannot precede `0505`. Alembic is forward-only — no delivered revision is edited
  - **FR-047 touches `0501` as well as `0502`.** The plan's coverage-map row named only `0502`, and that is where the referencing side lives — but the refusal is a foreign key, so `0501` must widen `uq_threshold_calibration__strata_thresholds` from four columns to eight *before* `0502` can declare the eight-column edge. Authoring the weights into `0502` alone leaves the key undeclarable, and forward-only migration means the correction would cost a second revision. Both task lines carry it (T009, T010)
  - **Strict test-first pairs**: T025→T026 (`calibrate.py`), T033→T034 (`pair_score.py`), T035→T036 (`decide.py`), T059→T060 (`metrics.py` additions). Neither member of a pair is ever `[P]`
  - **HINT-004** — deduplicate on the unordered identity **at generation** (T031, T032), never at reporting, or every published share carries a double-counted denominator
  - **HINT-003** — `0505` drops and recreates rather than widening in place, and T019 restates the eight falsified assertions rather than deleting them
- **Governance boundary**: no task edits `specs/prd.md`, `specs/sad.md`, `specs/project-plan.md`, `project-instructions.md`, or anything under `specs/adrs/`. `plan.md` records eleven obligations P-1…P-11 as recorded-not-performed, and ADR-0022 is claimed and deliberately left unwritten. T082 asserts that boundary held
- **Regression focus**: `tests/checks/test_migration_ranges.py` stays green with a sixth block declared and its negative control still controlling; E006's FR-065 ownership guard stays green **and gains sight of the two tables it was blind to**; the existing computation-boundary and single-provider-import contracts stay KEPT as `model.identity` grows

---

## Phase 1: Setup (Repository / Workspace Delta)

**Lands the block claim, the coverage wiring, the console entry and the two import contracts before any migration or module they constrain. T001 in particular precedes T008, the first `05xx` revision — see §Dependencies.**

- [ ] T001 Amend tests/checks/test_migration_ranges.py 3 ways (VR-026): DECLARED_BLOCKS += (500,599,E009); OWNERS_EXPECTED_TO_HAVE_REVISIONS += E009; negative-control probe 0500 -> 0600
- [ ] T002 [P] Append identity to the coverage --source enumeration and add a per-package 80% floor for model.identity in .github/workflows/verify.yml (HINT-002)
- [ ] T003 [P] Append model.identity to [tool.coverage.run] source and [tool.coverage.paths] in the root pyproject.toml, where both enumerations actually live (HINT-002)
- [ ] T004 Add the resolve-identity console entry point to [project.scripts] in src/model/pyproject.toml, one verb per job (AD-010)
- [ ] T005 Add the import contract forbidding model.identity from reaching model.llm or gateway, allow_indirect_imports=false, in src/model/pyproject.toml (AD-009)
- [ ] T006 {FR-046} Add the contract forbidding model.identity.baseline from identity.alias and compute.pair_score — two modules, normalize.py is shared — in src/model/pyproject.toml (AD-012)
- [ ] T007 Plant a model.identity import of gateway and a baseline import of identity.alias and observe lint-imports reject each, then remove both after:T006

---

## Phase 2: Foundational (Cross-Work-Item Blockers)

**The fifteen owned tables, the two views, the FR-045 extension, and the frozen-set-then-thresholds sequence. Every delivery phase writes through the chain, and no run can decide anything before thresholds exist, so both are lifted here rather than left in the stories whose criteria label them.**

- [ ] T008 {FR-002} Author revision 0500_alias_artifact in src/model/src/model/schema/versions/ — the four alias tables chained from 0404, with uq_manufacturer_alias__version_alias after:T001
- [ ] T009 {FR-037,FR-047} Author revision 0501_labeled_set — labeled_pair_set, labeled_pair, threshold_calibration with its four weight columns, and the 8-column strata_thresholds key after:T008
- [ ] T010 {FR-039,FR-044,FR-047} Author revision 0502_resolution_run — the manifest with its weight vector, the 8-column calibration FK, three unique keys and the active-run index after:T009
- [ ] T011 {FR-001,FR-013} Author revision 0503_run_records — resolution_run_record, unmatched_manufacturer_string and the two independent blocking indexes after:T010
- [ ] T012 {FR-009,FR-015} Author revision 0504_candidate_pair — candidate_pair with ck_candidate_pair__decision_matches_band, and candidate_pair_attribute_score after:T011
- [ ] T013 {FR-045,FR-020,FR-041} Author revision 0505_resolved_entity_extension — run and project columns, three DROP CONSTRAINTs, run-scoped replacements, the single-spec index after:T012
- [ ] T014 {FR-018} Author revision 0506_induced_pair — resolved_entity_induced_pair with its pinned decision='merge' FK and both member FKs after:T013
- [ ] T015 {FR-021,FR-023,FR-043} Author revision 0507_review_queue — review_queue_item, its eight-column FK and v_open_review_item after:T014
- [ ] T016 {FR-025,FR-038} Author revision 0508_resolution_figure — resolution_figure with its interval, stratum, census and shortfall CHECKs after:T015
- [ ] T017 {FR-039} Author revision 0509_resolution_privileges — grants on 15 tables and 2 views, revoke UPDATE/DELETE on the append-only twelve and on E003's two after:T016
- [ ] T018 Verify apply-from-empty, re-apply no-op, single head, 05xx prefixes, empty downgrades and the object inventory in src/model/tests/schema/test_identity_migrations.py after:T017
- [ ] T019 {FR-045} Restate the eight assertions 0505 falsifies — seven constraint names and the exactly-three-FK count — in src/model/tests/schema/test_resolved_entity.py after:T013
- [ ] T020 {FR-045} Add resolved_entity and resolved_entity_member to the ownership snapshot over an 0404-to-head window in src/model/tests/schema/test_table_ownership.py after:T017
- [ ] T021 {FR-045} [COMPLETES FR-045] Assert 0505's diff against a hardcoded expected object set and that no other E003 table changed, in test_table_ownership.py after:T020
- [ ] T022 Extend src/gateway/tests/test_migrations.py with E009's 0500-0509 block membership, keeping its positive control over an undamaged revision directory after:T017
- [ ] T023 {FR-010,FR-036} Build the two-strata labeled set — separate frames, annotation provenance, per-stratum canonical serialization — in identity/labeled.py after:T009 → exports: freeze_stratum
- [ ] T024 {FR-017,FR-037} [COMPLETES FR-037] Freeze, hash and COMMIT both strata under data/identity/ and load them into labeled_pair_set — HINT-001, precedes T025-T027 after:T023
- [ ] T025 {FR-016,FR-047} Failing tests for determinism and frozen-set dependence, cutoffs and weights, in tests/compute/test_calibrate.py — precedes T026, on an observed collection error after:T024
- [ ] T026 {FR-016,FR-047} Calibrate the cutoffs and four attribute weights over the frozen estimation stratum in compute/calibrate.py after:T025 → exports: calibrate_thresholds, calibrate_weights
- [ ] T027 {FR-016,FR-044,FR-047} [COMPLETES FR-016] Write the committed cutoffs and weight vector, bound to both stratum hashes, in identity/thresholds.py after:T026 → exports: WEIGHTS

---

## Phase 3: US1 - Link The Same Material Across Specification, Submittal And Purchase Order (Priority: P1) 🎯 MVP

- [ ] T028 [US1] {FR-001,FR-002,FR-004} Load the versioned alias artifact, fail on a duplicate alias, and record the rule that fired in identity/alias.py after:T008 → exports: resolve_manufacturer
- [ ] T029 [P] [US1] {FR-003} Retain raw strings beside normalized forms and segment part numbers — syntactic transforms only, no alias lookup — in identity/normalize.py → exports: normalize_record
- [ ] T030 [P] [US1] {FR-005} Canonicalize dimensional units to the declared base and classify arbitrary units as non-convertible in identity/units.py → exports: classify_unit, to_base_quantity
- [ ] T031 [P] [US1] {FR-009} Mint the stable unordered-pair identity and deduplicate at generation, before any denominator (HINT-004), in identity/pairs.py → exports: pair_identity
- [ ] T032 [US1] {FR-008,FR-020} Generate candidates on canonical manufacturer OR part-number prefix as independent keys, within one project, in identity/block.py after:T028 ← T031:pair_identity
- [ ] T033 [US1] {FR-006,FR-014} Failing property tests for boundedness and monotonicity in tests/compute/test_pair_score.py — precedes T034, on an observed collection error
- [ ] T034 [US1] {FR-006,FR-014,FR-047} Implement the scorer over the committed weights, absent state contributing zero, in compute/pair_score.py after:T033 ← T027:WEIGHTS → exports: score_pair
- [ ] T035 [US1] {FR-015} Failing tests for totality and disjointness enumerated exhaustively at both cutoffs in tests/compute/test_decide.py — precedes T036, on an observed collection error
- [ ] T036 [US1] {FR-015,FR-042} Implement the two-threshold band decision with strict comparisons so both cutoffs withhold, in compute/decide.py after:T035 → exports: decide_band
- [ ] T037 [US1] {FR-018,FR-041,FR-020} Agglomerate merged pairs under the clique constraint and reject a second specification section naming the record, in identity/cluster.py after:T036
- [ ] T038 [US1] {FR-034,FR-040,FR-004,FR-047} Assemble the manifest — alias version and digest, catalogue digest, cutoffs, weight vector, both hashes, input counts — in identity/runs.py after:T027
- [ ] T039 [US1] {FR-003,FR-012,FR-014,FR-033} Persist run records, every candidate pair, its attribute scores, entities, members and induced pairs in write order, in identity/writer.py after:T038
- [ ] T040 [US1] {FR-020} Add the resolve-identity entry driving normalize, block, score, decide, cluster and write for one project, in identity/cli.py after:T039
- [ ] T041 [US1] {FR-008} Assert a record with no part number but a shared manufacturer pairs, and the mirror case, in tests/identity/test_blocking.py after:T032 (VR-024)
- [ ] T042 [US1] {FR-009} [COMPLETES FR-009] Assert a pair agreeing on both blocking keys is one row carrying both key kinds, in tests/schema/test_candidate_pair.py after:T039 (VR-023)
- [ ] T043 [US1] {FR-014} [COMPLETES FR-014] Assert attribute contributions sum to total_score bit-exactly in ascending attribute order, in tests/schema/test_candidate_pair.py after:T039 (VR-009)
- [ ] T044 [US1] {FR-018} [COMPLETES FR-018] Assert every entity holds exactly n(n-1)/2 induced pairs, each merge-decided, and a singleton holds zero, in tests/schema/test_induced_pair.py after:T039
- [ ] T045 [US1] {FR-041,FR-033} [COMPLETES FR-041] Assert a second spec section is refused, three PO lines accepted, and each member resolves to its page, in tests/schema/test_members.py after:T039
- [ ] T046 [US1] {FR-005,FR-006} [COMPLETES FR-006] Assert VR-003's unit combinations and that a unit disagreement lowers a score rather than dropping a pair, in tests/identity/test_units.py
- [ ] T047 [US1] {FR-020} [COMPLETES FR-020] Assert no entity spans projects and a known spec/submittal/PO triple resolves to one entity, in tests/identity/test_run_end_to_end.py after:T040

---

## Phase 4: US2 - Withhold Uncertain Pairs Instead Of Merging Them (Priority: P1) 🎯 MVP

- [ ] T048 [US2] {FR-007} Assert no normalization rule maps two hard-negative-stratum records onto one key, before the transaction opens, in identity/guard.py after:T024 → exports: assert_no_collision
- [ ] T049 [US2] {FR-021,FR-022,FR-043} Write one review item per withheld pair per run, carrying the run id and the stable pair identity, in identity/review.py after:T039
- [ ] T050 [P] [US2] {FR-023} Assert review_queue_item carries no adjudication column and that E016's join surface is the pair identity, in tests/schema/test_review_queue.py after:T015
- [ ] T051 [US2] {FR-039,FR-044} Publish a run by deactivating the project's active run then activating the new one, in a second transaction, in identity/runs.py after:T038 (AD-007)
- [ ] T052 [US2] {FR-035,FR-017} Run both guards before the transaction opens and commit the run as one unit, aborting to zero rows, in identity/cli.py and writer.py after:T048
- [ ] T053 [US2] {FR-035,FR-007} Assert a breached collision guard and a mutated labeled row each leave zero rows in all fifteen tables, counted per table, in tests/identity/test_abort.py after:T052
- [ ] T054 [P] [US2] {FR-015,FR-042} [COMPLETES FR-015] Assert both cutoffs withhold and nine score positions each map to one decision, in both directions, in tests/schema/test_bands.py after:T012
- [ ] T055 [US2] {FR-024} Treat zero merges as success and assert an all-withheld run commits with one item per pair and a zero-denominator precision row, in identity/cli.py after:T052 (VR-014)
- [ ] T056 [US2] {FR-039,FR-043} Assert a second run leaves the first byte-identical and the pointer names exactly one run, in tests/identity/test_append_only.py after:T051 (VR-016, VR-017)
- [ ] T057 [US2] {FR-043} [COMPLETES FR-043] Assert v_open_review_item shows only the active run's items while the first run's remain in the table, in tests/schema/test_views.py after:T051 (VR-018)
- [ ] T058 [P] [US2] {FR-039} [COMPLETES FR-039] Assert the 29 privilege refusals under SET LOCAL ROLE procurement_app, naming E003's two tables, in tests/schema/test_privileges.py after:T017

---

## Phase 5: US3 - Read The Resolution's Own Quality Evidence (Priority: P1) 🎯 MVP

- [ ] T059 [US3] {FR-026,FR-027} Failing tests for interval containment and estimator selection in tests/compute/test_metrics.py — precedes T060, on a collection error naming the absent estimators
- [ ] T060 [US3] {FR-026,FR-027,FR-028} Add rule-of-three, one-sided exact binomial, the undefined branch and the n<30 disclosure to compute/metrics.py after:T059 → exports: precision_interval
- [ ] T061 [US3] {FR-011,FR-030,FR-031,FR-038} Add pair completeness, recall, coverage, reduction ratio and withheld share with their census/estimate classification to compute/metrics.py after:T060
- [ ] T062 [US3] {FR-019,FR-037a} Take the stratum as an estimator argument so no figure is computed across the union of the two strata, in compute/metrics.py after:T061
- [ ] T063 [P] [US3] {FR-046} Implement the exact-match baseline over normalize.py's shared output — same strings as the resolver — reading no alias table or scorer, in identity/baseline.py after:T029
- [ ] T064 [US3] {FR-025,FR-029,FR-038} [COMPLETES FR-038] Write all six figure rows with an interval or a declared no-interval reason, coverage beside precision, in identity/report.py after:T062
- [ ] T065 [US3] {FR-032} Publish a shortfall with its cause and read the registered target rather than restating it, in identity/report.py after:T064 (G-6)
- [ ] T066 [US3] {FR-046} [COMPLETES FR-046] Publish the baseline's precision and recall beside the resolver's, same stratum and estimator, labeled strong, in identity/report.py after:T063
- [ ] T067 [US3] {FR-010,FR-011} Publish blocking pair completeness with its sampling frame and reduction ratio as figures distinct from merge precision, in identity/report.py after:T064
- [ ] T068 [US3] {FR-030} Assert a withheld, rejected or never-blocked true pair each counts as a recall miss attributed to its stage, in tests/identity/test_recall.py after:T064 (SC-024)
- [ ] T069 [US3] {FR-025,FR-028,FR-037a} [COMPLETES FR-025] Assert six figure rows exist, none names hard_negative, each with an interval or a declaration, in tests/schema/test_figure.py after:T064
- [ ] T070 [P] [US3] {FR-044} [COMPLETES FR-044] Assert a run with an uncalibrated threshold pair is refused at first insert, perturbing each alone, in tests/schema/test_calibration.py after:T010
- [ ] T071 [P] [US3] {FR-017} [COMPLETES FR-017] Assert each stratum's hash recomputes, its counts reconcile, and frozen_at precedes calibrated_at, in tests/schema/test_labeled_set.py after:T024
- [ ] T072 [P] [US3] {FR-010,FR-036} [COMPLETES FR-010] Assert each labeled pair records its annotator and basis and its frame is not blocking-derived, in tests/schema/test_labeled_pair.py after:T024
- [ ] T084 [P] [US3] {FR-047} [COMPLETES FR-047] Assert a run refuses when one weight is perturbed, each of the four alone, in tests/schema/test_calibration.py after:T038 (VR-030, SC-043)

**T084 is appended rather than inserted**, following E006's T092-at-the-tail-of-US2 precedent, so the task IDs `plan.md` and the decomposition record already cite do not move. It sits at the end of Phase 5 because SC-043 is a US3 criterion and because it needs T038's manifest to have something to refuse.

---

## Phase 6: US4 - Maintain The Alias Table As An Auditable, Versioned Artifact (Priority: P3)

- [ ] T073 [US4] {FR-040} Derive data/identity/manufacturer-aliases.csv from E002's catalogue, digest it over the declared canonical serialization, and record the catalogue digest after:T028 (AD-004)
- [ ] T074 [US4] Write data/identity/datasheet.md disclosing the alias table's generative assumptions, following data/procurement/datasheet.md after:T073
- [ ] T075 [US4] {FR-013} Record every manufacturer string that matched no alias, once per run record, in identity/alias.py and identity/writer.py after:T039
- [ ] T076 [US4] {FR-002} [COMPLETES FR-002] Assert a duplicate normalized alias fails load naming uq_manufacturer_alias__version_alias, in tests/identity/test_alias_table.py after:T073 (VR-001)
- [ ] T077 [US4] {FR-013} [COMPLETES FR-013] Assert every merge names its alias rule and every unmatched string is retrievable, in tests/schema/test_alias_evidence.py after:T075 (VR-002, VR-008)
- [ ] T078 [US4] {FR-040,FR-034} [COMPLETES FR-040] Assert the manifest records E002's catalogue digest and a run naming a different one has no referent, in tests/schema/test_manifest.py after:T038
- [ ] T079 [US4] {FR-004} [COMPLETES FR-004] Assert two alias-table versions yield runs recording different versions and affected merges are identifiable, in tests/identity/test_versions.py after:T077

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T080 [P] Verify model.identity reaches the 80% per-package coverage floor in the combined report, asserted alone after:T002
- [ ] T081 [P] Verify the four architecture contracts stay KEPT — computation boundary, single provider import, model.identity isolation, baseline independence after:T007
- [ ] T082 [P] Confirm no task edited specs/prd.md, specs/sad.md, specs/project-plan.md, project-instructions.md or specs/adrs/, and that ADR-0022 remains claimed and unwritten
- [ ] T083 [P] Document the resolve-identity job, its two pre-transaction guards and the separate publication flip in src/model/README.md after:T040

---

## Dependencies

Setup → Foundational → Delivery Work Items (US1 → US2 → US3, all P1, then US4 at P3) → Polish

- **The block claim precedes the first revision it authorises.** T001 declares `0500`–`0599` in `tests/checks/test_migration_ranges.py`, and T008 names `after:T001` rather than relying on the phase gate alone. A `05xx` revision merged before T001 turns CI red three ways, and the third of those ways — the negative-control probe at `"0500"` becoming a real revision number — turns nothing red at all and silently stops controlling for anything. That is why the probe move is part of T001 and not a footnote.
- **Migration chain**: T008 → T009 → T010 → T011 → T012 → T013 → T014 → T015 → T016 → T017 is a hard order, each edge carried by a declared `after:`. Two of them are structural rather than stylistic: **`0505` (T013) must follow `0503` (T011)** because `fk_rem__run_record` targets `resolution_run_record`, and **`0506` (T014) cannot precede `0505` (T013)** because both member foreign keys target `uq_rem__entity_kind_record`, which `0505` creates. Alembic is forward-only; no delivered revision is edited by any task here, and every `downgrade()` raises `NotImplementedError` (T018, VR-027).
- **HINT-001 is a sequence of tasks, not a note.** T023 builds both strata; **T024 freezes, hashes and commits the artifact**; T025 → T026 calibrate against it; T027 writes the threshold constants. The order is readable in the list and its execution order is visible in checkbox state, which is the point — a threshold constant committed before the set is frozen cannot be shown to predate it, and no later artifact can reconstruct the ordering after a squash merge. T027 declares `after:T026` and T026 declares `after:T025`, so a scheduler honouring only declared edges cannot invert it.
- **Foundational is not optional and is not US3.** The fifteen tables and two views block all four stories, and the frozen set plus the calibrated thresholds block every run that decides anything — US1 cannot produce a merge without them. Leaving them in US3 where their success criteria are labelled would make US1 and US2 unbuildable, so they are lifted. What stays in US3 is the estimator arithmetic, the baseline, and the published figures.
- **FR-047's weights are frozen on the same discipline as the cutoffs, and the sequence is the same one.** T009 and T010 carry the schema, T025 → T026 calibrate both together, T027 commits both, T034 *consumes* the committed weights rather than choosing its own (`← T027:WEIGHTS`), T038 records the vector in the manifest, and T084 perturbs each weight alone and asserts the run refuses. The edge that matters is T027 → T034: a scorer that hardcodes its own weights would satisfy every other task here and leave the scale tunable after observing precision, which is the hole FR-047 was written to close.
- **Mandatory red-green pairs**: T025 before T026 (`compute/calibrate.py`, cutoffs **and** weights), T033 before T034 (`compute/pair_score.py`), T035 before T036 (`compute/decide.py`), T059 before T060 (`compute/metrics.py`). Each test task **names the implementation task it precedes** and is complete only when its suite has been run against the absent module — or, for `metrics.py`, against the absent estimators — and **observed to fail**, with that observed failure recorded on the task line. A test task marked complete beside a passing suite is the defect this condition exists to catch. Neither member of a pair is ever `[P]`.
- **Relation class per pair**, carried from `plan.md` §Testing Strategy Property tier: `pair_score` — boundedness and monotonicity (T033); `decide` — totality and disjointness, **enumerated exhaustively at and around both cutoffs rather than sampled**, because the criterion is about exact boundary values and a sampler will miss them (T035); `metrics` — interval containment and estimator selection, over every published figure including pair completeness, coverage, the withheld share and the stratum argument (T059); `calibrate` — determinism and frozen-set dependence (T025).
- **Four existing tests are falsified and each has its own task.** T001 — `tests/checks/test_migration_ranges.py`, three edits including the probe move. T019 — `src/model/tests/schema/test_resolved_entity.py`, eight assertions **restated against the run-scoped constraints, never deleted**; a name-matched assertion simply removed leaves the re-scoping unasserted. T020 and T021 — `test_table_ownership.py`, whose `E003_OWNED_TABLES` names six tables and includes neither `resolved_entity` nor `resolved_entity_member`, so E006's FR-065 guard is blind to exactly the alteration FR-045 performs (VR-028, VR-029, G-9). T022 — `src/gateway/tests/test_migrations.py`, **extended with E009's block rather than re-pinned to a new head**, keeping its positive control over an undamaged revision directory so a broken fixture cannot make the negative controls pass for the wrong reason.
- **Write order is a task, not a convention**: T039 implements `data-model.md` §Write Order steps 1–9 exactly, and T052 places the two guards **before** the transaction opens and the publication flip **after** it commits, in its own transaction. T049, T051 and T055 attach to named steps and must not reorder them. SC-015's three absences are one property of that ordering — nothing was opened, rather than something rolled back, which matters because a rollback would need a `DELETE` privilege `procurement_app` does not hold.
- **Cross-phase edges into Foundational**: T028→T008, T023→T009, T038→T027, T048→T024, T050→T015, T054→T012, T058→T017, T070→T010, T071→T024, T072→T024. Every delivery phase reaches the chain through a declared edge; no row is written before T017 is `[X]`.
- **Declared edges**: T007→T006, T008→T001, T009→T008, T010→T009, T011→T010, T012→T011, T013→T012, T014→T013, T015→T014, T016→T015, T017→T016, T018→T017, T019→T013, T020→T017, T021→T020, T022→T017, T023→T009, T024→T023, T025→T024, T026→T025, T027→T026, T028→T008, T032→T028, T034→T033, T036→T035, T037→T036, T038→T027, T039→T038, T040→T039, T041→T032, T042→T039, T043→T039, T044→T039, T045→T039, T047→T040, T048→T024, T049→T039, T050→T015, T051→T038, T052→T048, T053→T052, T054→T012, T055→T052, T056→T051, T057→T051, T058→T017, T060→T059, T061→T060, T062→T061, T063→T029, T064→T062, T065→T064, T066→T063, T067→T064, T068→T064, T069→T064, T070→T010, T071→T024, T072→T024, T073→T028, T074→T073, T075→T039, T076→T073, T077→T075, T078→T038, T079→T077, T080→T002, T081→T007, T083→T040, T084→T038.
- **Symbol-import edges gate execution exactly as `after:` does.** T032 ← T031:`pair_identity` and T034 ← T027:`WEIGHTS`. A consumer reading only `after:` under-constrains both; both forms must be honoured.
- **The publish tasks share one output file.** T064, T065, T066 and T067 all render into `specs/00009-cross-document-identity-resolution/resolution-report.md` (declared in `plan.md` §Project Structure and repeated in §Brownfield Notes). The path is carried here rather than on each task line because four copies of it would push every one of those lines past the 200-character limit the task grammar sets. They are sequenced T064 → T065/T066/T067 so the six figure rows exist before any section that reads them.
- **P1 boundary**: Phases 1–5 (T001–T072 plus T084) are the viable deliverable — identities resolved per project, uncertain pairs withheld to a review queue, and the run's own quality evidence published with its intervals and its honest opponent. Phase 6 (US4, P3) and Phase 7 are omittable without breaking any P1 criterion, at the cost of VR-008's unmatched-string reconciliation and SC-028/SC-029/SC-030/SC-036.
- **US independence**: US1 is testable by running resolution over one project and inspecting a triple; US2 by engineering a pair into the withhold band and observing an item and no merge; US3 by running against the frozen labeled set and reading the six figures with their strata and intervals; US4 by editing one alias and diffing two runs. Each later story depends on earlier artifacts through declared edges, which is dependency, not loss of independent testability.
- Tasks marked `[P]` can run in parallel within their phase — they touch distinct files and carry no `after:T###` or `← T###:` edge to another task in the same batch.
- A task with `after:T###` or `← T###:Symbol` must not be `[P]`-batched with the referenced task; the implementing agent must verify the referenced task is `[X]` before executing.
