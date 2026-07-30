# Tasks: Line Detail and Traceability

**Input**: Design documents from `specs/00012-line-detail-and-traceability/`
**Prerequisites**: `plan.md` (required), `spec.md` (required), `research.md`, `contracts/openapi.yaml`

**Tests**: Included. FR-046 makes test-first with property-based tests mandatory for
`src/api/src/api/compute/distribution.py`; the remaining tiers are test-after per `plan.md` § Testing Strategy.

**Organization**: Four P1 user stories, each independently testable. There is no `data-model.md` — E012 adds
no table, no column and no migration, so no schema task appears below.

## Project Mode

`Brownfield`

## Epic / Capability Map

- `[US1]` → Inspect the whole distribution behind a line — fifty marks, cumulative view, textual equivalent
- `[US2]` → Read the source page behind a linked record — citations, source streaming, the resolution census
- `[US3]` → Be told when a line's identity has not been resolved — three identity states and the absent line
- `[US4]` → See which covariates the forecast conditioned on — reconstructed, reconciled, withheld

## Brownfield Notes

- Existing flows touched: `src/api/src/api/main.py` (router registration), `src/api/tests/conformance.py`
  (validator extension), `src/api/tests/fixtures/frozen_run/` (fixture extension),
  `src/web/app/worklist/Row.tsx` (E010's delivered surface).
- Reused rather than re-chosen: `api.compute.probability` (`PercentFigure`, `complement`, `REFERENCE_CLASS`),
  `api.risk_read.failures` (problems and correlation identifiers), `worklist/stateCopy.ts` and
  `worklist/useWorklist.ts` conventions.
- Import contracts: every new server module lands in `api.risk_read`, `api.routes` or `api.compute`, none of
  which may reach `gateway` — indirect imports included.
- Compatibility: FR-035 converts the *existing* worklist identity into a link and adds no row element. E010
  closes row content in three classes (HINT-005).
- Unresolved identity is the common path, not an edge case: `resolved_entity_member` is empty until E009 runs,
  and E009 stands at 0 of 86 tasks. T008/T009 build and test that path in Foundational, and every traversal
  test runs against frozen fixtures (HINT-003).
- Paths outside `plan.md` § Project Structure, and why they are needed:
  `tests/checks/test_detail_checks_run_in_the_gate.py` (root-level cross-entry check, following the delivered
  `test_worklist_checks_run_in_the_gate.py`) and `src/model/tests/test_corpus_span_acceptance.py` (AD-010's
  corpus-page half needs `pdfplumber`, which only `/src/model` declares).
- Regression focus: the worklist must render unchanged after T024, and the delivered
  `src/api/tests/test_contract_conformance.py` must keep passing after the T003 validator extension.

---

## Phase 1: Setup (Repository / Workspace Delta)

- [X] T001 Merge branch `claim-adr-0025` (commit `7846bae`) into `main` — the record is **already authored** at specs/adrs/0025-stored-posterior-arrays-do-not-cross-the-serving-boundary.md and pushed, together with its catalog rows in specs/sad.md and specs/project-plan.md as one serialized amendment. Do not re-author it: a second record under a different slug would claim the number twice. Then re-run the plan's Instructions Check gate, which is FAIL solely on this — **done**: merged at `f6e363f`; ADR-0025 and both catalog rows are on `main`, gate re-run to PASS
- [ ] T002 {FR-023} Pass the corpus root to the api process in scripts/dev.py, scripts/e2e.py and the e2e step of .github/workflows/verify.yml {AD-007}

---

## Phase 2: Foundational (Cross-Work-Item Blockers)

**Blocks all four stories.**

**T003 is prerequisite work, not optional.** `src/api/tests/conformance.py` is hand-written — `jsonschema` is a
`/src/model` distribution the serving boundary may not carry — and its `SUPPORTED_KEYWORDS` implements none of
`allOf`, `anyOf`, `not`, `if`/`then`, `contains` or `maxLength`. E012's contract uses all six, including the
four root conditionals carrying FR-006's withholding rule. Pointed at this contract unextended, a conformance
test never evaluates those conditionals and never reaches `additionalProperties: false` through `allOf`-wrapped
members. T003 therefore precedes T017.

- [ ] T003 [P] {FR-006} Add allOf, anyOf, not, if/then, contains and maxLength to SUPPORTED_KEYWORDS and _check in src/api/tests/conformance.py → exports: validate()
- [ ] T004 [P] {FR-028} Add detail scenarios — covered line, closed line, all three identity states — to src/api/tests/fixtures/frozen_run/seed.py and generate.py {HINT-003}
- [ ] T005 [P] {FR-042} Decide E010 FR-055's non-application case for need_by_override and record it in specs/00012-line-detail-and-traceability/plan.md § Open Items, before T006
- [ ] T006 {FR-042,FR-044,FR-045} Implement the resolution, annotation and section scopes in src/api/src/api/risk_read/detail_states.py after:T005 → exports: resolve_detail_states()
- [ ] T007 [P] {FR-010,FR-013} Read the same stored artifact row and run identification the worklist reads in src/api/src/api/risk_read/line_query.py → exports: load_line_detail()
- [ ] T008 {FR-027} Resolve the three identity states in src/api/src/api/risk_read/traceability.py after:T004 {AD-003} → exports: resolve_identity_state()
- [ ] T009 {FR-028} Test the traversal and all three identity states against frozen fixtures in src/api/tests/test_traceability.py after:T008 {HINT-003}
- [ ] T010 [P] {FR-026,FR-040,FR-043} Commit the state copy for all three scopes in src/web/app/lines/[poLineId]/detailCopy.ts with a distinctness test → exports: RESOLUTION_COPY, SECTION_COPY
- [ ] T011 {FR-036} Add the router and RFC 9457 problem handling in src/api/src/api/routes/line_detail.py, registered in src/api/src/api/main.py after:T006 ← T006:resolve_detail_states

---

## Phase 3: US1 - Inspect the whole distribution behind a line (Priority: P1) 🎯 MVP

**Independent test**: open the detail view for a line with a posterior and confirm the distribution renders, the
need-by mark and its mass are both shown, and no mean, mode or lone quantile appears anywhere.

**T012 must be committed before T013.** FR-046 makes test-first mandatory for `compute/distribution.py`, and QC
checks the commit order rather than merely the presence of tests (HINT-001).

- [ ] T012 [US1] {FR-046} Write Hypothesis property tests first, over marks, quantiles, mass and bands, in src/api/tests/test_distribution.py {HINT-001}
- [ ] T013 [US1] {FR-001,FR-002,FR-004} Implement fifty-mark allocation and nearest-rank quantile extraction in src/api/src/api/compute/distribution.py after:T012
- [ ] T014 [US1] {FR-005,FR-006,FR-041} Derive miss mass both directions, the beyond-horizon bound and absence at the anchor in compute/distribution.py after:T013 {AD-005,HINT-004}
- [ ] T015 [US1] {FR-007,FR-008,FR-016} Derive three quantile-bounded cumulative bands and the residual mass in compute/distribution.py after:T014 {AD-006} → exports: cumulative_bands()
- [ ] T016 [US1] {FR-003,FR-011,FR-012,FR-037,FR-038} Compose figures in five classes and encoding with no array or central-summary member in routes/line_detail.py after:T015
- [ ] T017 [US1] [P] {FR-006,FR-011,FR-012,FR-039} Validate live bodies and assert x-prohibited-members absence in src/api/tests/test_detail_conformance.py after:T016 ← T003:validate
- [ ] T018 [US1] [P] {FR-042} [COMPLETES FR-042] Cover the five resolution states, the annotation sets, ETag/304 and every problem shape in src/api/tests/test_line_detail.py after:T016
- [ ] T019 [US1] {FR-045} Build the route shell, fetch hook and stylesheet marking stale figures in src/web/app/lines/[poLineId]/page.tsx, useLineDetail.ts, page.module.css after:T010
- [ ] T020 [US1] {FR-001,FR-003,FR-004,FR-009} Render fifty marks, the labelled quantile pair and a days-from-as-of axis in src/web/app/lines/[poLineId]/Distribution.tsx after:T019
- [ ] T021 [US1] {FR-005,FR-006} [COMPLETES FR-006] Mark the need-by date, shade the mass beyond it and state it both directions with bound wording in Distribution.tsx after:T020
- [ ] T022 [US1] {FR-007,FR-008,FR-009} Render the increasing cumulative view after the distribution with the residual mass labelled in src/web/app/lines/[poLineId]/Cumulative.tsx after:T021
- [ ] T023 [US1] {FR-014,FR-015,FR-016,FR-017} Render the five-item structured equivalent in the figures' own region in src/web/app/lines/[poLineId]/TextualEquivalent.tsx after:T022
- [ ] T024 [US1] [P] {FR-035} Turn the existing line identity into the detail link, adding no row element, in src/web/app/worklist/Row.tsx and Row.test.tsx {HINT-005}
- [ ] T025 [US1] {FR-037,FR-040} Assert worklist-to-detail navigation, accessibility-tree state text and no central estimate in src/web/e2e/line-detail.spec.ts after:T023,T024

---

## Phase 4: US2 - Read the source page behind a linked record (Priority: P1) 🎯

**Independent test**: from a line with linked records, open one and confirm the originating document page is
displayed and identified by document title and page number.

**AD-010 splits FR-024's second condition across two tiers.** T029 checks the extracted span against
`chunk.body_text` at request time and declares that figure a proxy; T034 re-measures the same condition against
real corpus pages where `pdfplumber` is available. Two tasks, not one — the proxy's validity is established by
the agreement rather than assumed, and disagreement is published.

- [ ] T026 [US2] [P] {FR-018,FR-019,FR-022} Materialise linked records with page, span, confidence and layer in risk_read/traceability.py after:T009 → exports: load_linked_records()
- [ ] T027 [US2] [P] {FR-023} Bind a document identifier to a readable source in src/api/src/api/risk_read/source_binding.py {AD-007} → exports: resolve_source()
- [ ] T028 [US2] {FR-024,FR-025} Aggregate the census by document with its denominator, target and no-interval declaration in risk_read/source_binding.py after:T027 {AD-009}
- [ ] T029 [US2] {FR-024} Check the extracted span against chunk.body_text at request time and declare the runtime figure a proxy in risk_read/source_binding.py after:T028 {AD-010}
- [ ] T030 [US2] {FR-021} Filter every chunk and document read through v_active_ingestion_generation in risk_read/traceability.py after:T026
- [ ] T031 [US2] {FR-020,FR-021,FR-022} Stream the source inline with X-Source-Kind and X-Ingestion-Generation in src/api/src/api/routes/line_detail.py after:T027,T030 ← T027:resolve_source
- [ ] T032 [US2] {FR-018,FR-019,FR-022} [COMPLETES FR-022] List each citation with title, page, span, confidence and layer marker in src/web/app/lines/[poLineId]/LinkedRecords.tsx after:T019,T031
- [ ] T033 [US2] {FR-020,FR-023} [COMPLETES FR-023] Open the cited page at #page=N and render the named unresolvable failure rather than an empty frame in LinkedRecords.tsx after:T032
- [ ] T034 [US2] {FR-024,FR-025} [COMPLETES FR-024] Re-measure span presence against real corpus pages with pdfplumber in src/model/tests/test_corpus_span_acceptance.py after:T029 {AD-010}

---

## Phase 5: US3 - Be told when a line's identity has not been resolved (Priority: P1) 🎯

**Independent test**: open a line with no resolved identity and confirm the view names that condition in text
rather than showing an empty traceability section. The read path and its frozen-fixture tests are already in
Foundational (T008, T009) because this is the state every line is in today, not an edge case.

- [ ] T035 [US3] {FR-027,FR-028} Carry identity_state in every resolution state where a line exists in routes/line_detail.py after:T008 ← T008:resolve_identity_state
- [ ] T036 [US3] {FR-026,FR-027} [COMPLETES FR-027] Render the three identity states from committed copy in src/web/app/lines/[poLineId]/page.tsx after:T019,T035 ← T010:SECTION_COPY
- [ ] T037 [US3] {FR-028,FR-044} [COMPLETES FR-028] Render the absent-or-closed-line outcome and keep the distribution and line record present in both identity states in page.tsx after:T036
- [ ] T038 [US3] [P] {FR-029} Assert no interaction with either endpoint writes an identity-resolution record, in src/api/tests/test_line_detail.py after:T035

---

## Phase 6: US4 - See which covariates the forecast conditioned on (Priority: P1) 🎯

**Independent test**: open a line and confirm the covariates the fit conditioned on and their observed values
are shown, with no contribution figure present and no causal claim made.

- [ ] T039 [US4] {FR-030,FR-032} Reconstruct each covariate the active run records, from the line's lifecycle history, in src/api/src/api/risk_read/covariates.py {AD-004}
- [ ] T040 [US4] {FR-031} Reconcile each value against the fit and withhold under covariate_withheld on mismatch in risk_read/covariates.py after:T039 → exports: reconcile_covariates()
- [ ] T041 [US4] {FR-030,FR-032,FR-033} Carry figures.covariate with no share, weight or percentage member in routes/line_detail.py after:T040 ← T040:reconcile_covariates
- [ ] T042 [US4] {FR-030,FR-031,FR-033,FR-034} [COMPLETES FR-030] Render names, observed values and association-only wording in src/web/app/lines/[poLineId]/Covariates.tsx after:T019,T041

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T043 [P] {FR-046} Assert the property tests and the detail conformance test both run in the merge gate, in tests/checks/test_detail_checks_run_in_the_gate.py after:T012,T017
- [ ] T044 [P] {FR-038,FR-039,FR-041} Assert the five figure classes admit no sixth and every published percentage takes the bounded form, in src/api/tests/test_detail_conformance.py after:T041
- [ ] T045 [P] {FR-040} [COMPLETES FR-040] Assert every state the view resolves to is present as accessibility-tree text in src/web/e2e/line-detail.spec.ts after:T037,T042
- [ ] T046 [P] Assert the memoised per-document census keeps the response inside the 1.5 s p95 envelope, in src/api/tests/test_line_detail.py after:T034 {AD-009}

---

## Dependencies

Setup → Foundational → US1 → US2 / US3 / US4 → Polish

- **T001 is a governance blocker.** `plan.md` § Instructions Check records **FAIL until ADR-0025 lands on
  `main`**, and under v1.2.11 a number claimed only on a feature branch is not a claim.
- **T003 gates T017.** Until the hand-written validator carries the six missing keywords, a conformance test
  pointed at this contract silently skips the four root conditionals that carry FR-006's withholding rule.
- **T012 gates T013–T015 and must be committed first** (FR-046, HINT-001).
- **T004 and T008 gate every traversal test**; all traversal tests run against frozen fixtures (HINT-003).
- T019 gates every web section: T020, T032, T036, T042.
- T029 (request-time proxy) and T034 (corpus-page acceptance) are AD-010's two tiers and must both land.
- US2, US3 and US4 depend only on Foundational plus T019, so they proceed in parallel with one another once
  US1's route shell exists.
- Tasks marked `[P]` can run in parallel within their phase. A task carrying `after:T###` or `← T###:Symbol` is
  never `[P]`-batched with the task it references.
