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

- [X] T001 Blocked until ADR-0025 and its catalog rows are on `main` — authored on `claim-adr-0025` (commit `7846bae`) — the record is **already authored** at specs/adrs/0025-stored-posterior-arrays-do-not-cross-the-serving-boundary.md and pushed, together with its catalog rows in specs/sad.md and specs/project-plan.md as one serialized amendment. Do not re-author it: a second record under a different slug would claim the number twice. Then re-run the plan's Instructions Check gate, which is FAIL solely on this — **done**: merged at `f6e363f`; ADR-0025 and both catalog rows are on `main`, gate re-run to PASS
- [ ] T002 {FR-023} Pass the corpus root to the api process in scripts/dev.py, scripts/e2e.py and the e2e step of .github/workflows/verify.yml (AD-007)

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
- [ ] T004 [P] {FR-028} Add detail scenarios — covered line, closed line, all three identity states — to src/api/tests/fixtures/frozen_run/seed.py and src/api/tests/fixtures/frozen_run/generate.py (HINT-003)
- [ ] T005 [P] {FR-042} Decide E010 FR-055's non-application case for need_by_override and record it in specs/00012-line-detail-and-traceability/plan.md § Open Items, before T006
- [ ] T006 {FR-042,FR-044,FR-045,FR-061} Implement the resolution, annotation and section scopes, each resolved state reported as a successful outcome carrying the precedence rank it won under and never as a fault, in src/api/src/api/risk_read/detail_states.py after:T005 → exports: resolve_detail_states()
- [ ] T007 [P] {FR-010,FR-013,FR-060} Read the same stored artifact row and run identification the worklist reads, carrying the line identity's manufacturer and part number and never its criticality, in src/api/src/api/risk_read/line_query.py → exports: load_line_detail()
- [ ] T008 {FR-027} Resolve the three identity states in src/api/src/api/risk_read/traceability.py after:T004 (AD-003) → exports: resolve_identity_state()
- [ ] T009 {FR-028} Test the traversal and all three identity states against frozen fixtures in src/api/tests/test_traceability.py after:T008 (HINT-003)
- [ ] T010 [P] {FR-026,FR-040,FR-043,FR-053} Commit the state copy for all three scopes in src/web/app/lines/[poLineId]/detailCopy.ts, each entry naming what its state withholds or bounds, with a distinctness test applying the no-substring and unique-phrase rules across all three scopes together and treating a phrase as two or more contiguous words → exports: RESOLUTION_COPY, ANNOTATION_COPY, SECTION_COPY
- [ ] T011 {FR-036,FR-067,FR-068} Add the router, RFC 9457 problem handling with a correlation identifier on every problem including a refused request, the response validator over exactly the inputs contracts/openapi.yaml §headers.ETag enumerates with 304 on a match, and `Cache-Control: private, no-cache`, in src/api/src/api/routes/line_detail.py, registered in src/api/src/api/main.py after:T006 ← T006:resolve_detail_states

---

## Phase 3: US1 - Inspect the whole distribution behind a line (Priority: P1) 🎯 MVP

**Independent test**: open the detail view for a line with a posterior and confirm the distribution renders, the
need-by mark and its mass are both shown, and no mean, mode or lone quantile appears anywhere.

**T012 must be committed before T013.** FR-046 makes test-first mandatory for `compute/distribution.py`, and QC
checks the commit order rather than merely the presence of tests (HINT-001).

- [ ] T012 [US1] {FR-046} Write Hypothesis property tests first, over marks, quantiles, mass and bands, in src/api/tests/test_distribution.py (HINT-001)
- [ ] T013 [US1] {FR-001,FR-002,FR-004,FR-065} Implement fifty-mark allocation and nearest-rank quantile extraction, each quantile carrying both its complementary share and the other quantile of the pair, in src/api/src/api/compute/distribution.py after:T012
- [ ] T014 [US1] {FR-005,FR-006,FR-041,FR-066} Derive miss mass both directions, the beyond-horizon bound and structural absence at the anchor — no null, zero or sentinel — in src/api/src/api/compute/distribution.py after:T013 (AD-005, HINT-004)
- [ ] T015 [US1] {FR-007,FR-008,FR-016} Derive three quantile-bounded cumulative bands and the residual mass in src/api/src/api/compute/distribution.py after:T014 (AD-006) → exports: cumulative_bands()
- [ ] T016 [US1] {FR-003,FR-011,FR-012,FR-037,FR-038,FR-062,FR-063} Compose figures in the five classes and the encoding with no array or central-summary member, against the effective need-by date a carried session adjustment sets, in src/api/src/api/routes/line_detail.py after:T015 (AD-008)
- [ ] T017 [US1] [P] {FR-006,FR-011,FR-012,FR-039,FR-064} Validate live bodies, assert x-prohibited-members absence and assert every object in the 200 tree is closed so a prohibited quantity is unrepresentable rather than merely absent, in src/api/tests/test_detail_conformance.py after:T016 ← T003:validate
- [ ] T018 [US1] [P] {FR-042,FR-061,FR-068} [COMPLETES FR-042] Cover the five resolution states with their precedence ranks, the annotation sets, every problem shape, and the two-sided validator check — every enumerated input held fixed leaves the validator and the response unchanged but for the generated-at timestamp, and each changed in turn moves it, a resolution run that moves the census included — in src/api/tests/test_line_detail.py after:T016
- [ ] T019 [US1] {FR-045,FR-055} Build the route shell, fetch hook and stylesheet marking stale figures once inside the region holding them, naming the as-of date and the threshold, in src/web/app/lines/[poLineId]/page.tsx, src/web/app/lines/[poLineId]/useLineDetail.ts, src/web/app/lines/[poLineId]/page.module.css after:T010
- [ ] T020 [US1] {FR-001,FR-003,FR-004,FR-009,FR-047} Render fifty marks, the labelled quantile pair and a days-from-as-of axis, each share naming its denominator and population in the delivered REFERENCE_CLASS wording, in src/web/app/lines/[poLineId]/Distribution.tsx after:T019 (AD-005, HINT-002)
- [ ] T021 [US1] {FR-005,FR-006,FR-052,FR-054} [COMPLETES FR-006] Mark the need-by date, shade the mass beyond it and state it both directions with bound wording spoken as words, and state an in-force session what-if beside the recorded date, in src/web/app/lines/[poLineId]/Distribution.tsx after:T020 (AD-008)
- [ ] T022 [US1] {FR-007,FR-008,FR-009} Render the increasing cumulative view after the distribution with the residual mass labelled in src/web/app/lines/[poLineId]/Cumulative.tsx after:T021
- [ ] T023 [US1] {FR-014,FR-015,FR-016,FR-017,FR-048,FR-049} Render the five-item structured equivalent in the figures' own region from the same response members the figures render from, visible and in the accessibility tree alike with no second copy, and assert it states the same figures the plot states, in src/web/app/lines/[poLineId]/TextualEquivalent.tsx and src/web/app/lines/[poLineId]/TextualEquivalent.test.tsx after:T022
- [ ] T024 [US1] [P] {FR-035,FR-056} Turn the existing line identity into the detail link with an accessible name identifying the line, adding no row element, in src/web/app/worklist/Row.tsx and src/web/app/worklist/Row.test.tsx (HINT-005)
- [ ] T025 [US1] {FR-037,FR-040} Assert worklist-to-detail navigation, accessibility-tree state text and no central estimate in src/web/e2e/line-detail.spec.ts after:T023,T024

---

## Phase 4: US2 - Read the source page behind a linked record (Priority: P1) 🎯

**Independent test**: from a line with linked records, open one and confirm the originating document page is
displayed and identified by document title and page number.

**AD-010 splits FR-024's second condition across two tiers.** T029 checks the extracted span against
`chunk.body_text` at request time and declares that figure a proxy; T034 re-measures the same condition against
real corpus pages where `pdfplumber` is available. Two tasks, not one — the proxy's validity is established by
the agreement rather than assumed, and disagreement is published.

- [ ] T026 [US2] [P] {FR-018,FR-019,FR-022} Materialise linked records with page, span, confidence and layer in src/api/src/api/risk_read/traceability.py after:T009 → exports: load_linked_records()
- [ ] T027 [US2] [P] {FR-023,FR-059} Bind a document identifier to a readable source, drawing every cause from the one SourceUnresolvableReason enumeration that also marks a citation known in advance not to open, in src/api/src/api/risk_read/source_binding.py (AD-007) → exports: resolve_source()
- [ ] T028 [US2] {FR-024,FR-025,FR-068} Aggregate the census by document with its denominator, target, no-interval declaration and the unjudged verdict's cause at a zero denominator, and contribute the active resolution run's identity and linked-record digest to the response validator, in src/api/src/api/risk_read/source_binding.py after:T027 (AD-009)
- [ ] T029 [US2] {FR-024,FR-058} Check the extracted span against chunk.body_text at request time and declare the runtime figure a proxy, with the basis published beside the figure on every instance, in src/api/src/api/risk_read/source_binding.py after:T028 (AD-010)
- [ ] T030 [US2] {FR-021} Filter every chunk and document read through v_active_ingestion_generation in src/api/src/api/risk_read/traceability.py after:T026
- [ ] T031 [US2] {FR-020,FR-021,FR-022} Stream the source inline with X-Source-Kind and X-Ingestion-Generation in src/api/src/api/routes/line_detail.py after:T027,T030 ← T027:resolve_source
- [ ] T032 [US2] {FR-018,FR-019,FR-022,FR-051} [COMPLETES FR-022] List each citation with title, page, span, layer marker and confidence rendered as a self-reported extraction score rather than a percentage, and render the census with its population, no-interval declaration and licensed reason in words, in src/web/app/lines/[poLineId]/LinkedRecords.tsx after:T019,T031
- [ ] T033 [US2] {FR-020,FR-020a,FR-023,FR-050,FR-057,FR-059} [COMPLETES FR-023] Open the cited page at #page=N, state on the offer that the whole document is served positioned at that page, mark a citation the view already knows will not open with its cause before the reader opens it, and render the named unresolvable failure with its untruncated correlation identifier rather than an empty frame, in src/web/app/lines/[poLineId]/LinkedRecords.tsx after:T032
- [ ] T034 [US2] {FR-024,FR-025,FR-058} [COMPLETES FR-024] Re-measure span presence against real corpus pages with pdfplumber in src/model/tests/test_corpus_span_acceptance.py after:T029 (AD-010) — and write both counts, the two measurements and any disagreement between them to a committed evidence artifact under the feature workspace, because AD-010 promises disagreement is *published* and a test that measures then discards publishes nothing

---

## Phase 5: US3 - Be told when a line's identity has not been resolved (Priority: P1) 🎯

**Independent test**: open a line with no resolved identity and confirm the view names that condition in text
rather than showing an empty traceability section. The read path and its frozen-fixture tests are already in
Foundational (T008, T009) because this is the state every line is in today, not an edge case.

- [ ] T035 [US3] {FR-027,FR-028} Carry identity_state in every resolution state where a line exists in src/api/src/api/routes/line_detail.py after:T008 ← T008:resolve_identity_state
- [ ] T036 [US3] {FR-026,FR-027} [COMPLETES FR-027] Render the three identity states from committed copy in src/web/app/lines/[poLineId]/page.tsx after:T019,T035 ← T010:SECTION_COPY
- [ ] T037 [US3] {FR-028,FR-044} [COMPLETES FR-028] Render the absent-or-closed-line outcome and keep the distribution and line record present in both identity states in src/web/app/lines/[poLineId]/page.tsx after:T036
- [ ] T038 [US3] [P] {FR-029,FR-069} Assert no interaction with either endpoint writes an identity-resolution record or any other stored state, and that the router exposes no verb but GET, in src/api/tests/test_line_detail.py after:T035

---

## Phase 6: US4 - See which covariates the forecast conditioned on (Priority: P1) 🎯

**Independent test**: open a line and confirm the covariates the fit conditioned on and their observed values
are shown, with no contribution figure present and no causal claim made.

- [ ] T039 [US4] {FR-030,FR-032} Reconstruct each covariate the active run records, from the line's lifecycle history, in src/api/src/api/risk_read/covariates.py (AD-004)
- [ ] T040 [US4] {FR-031,FR-066} Reconcile each value against the fit and withhold under covariate_withheld on mismatch — the observed value absent rather than null, zero or a dash — in src/api/src/api/risk_read/covariates.py after:T039 → exports: reconcile_covariates()
- [ ] T041 [US4] {FR-030,FR-032,FR-033} Carry figures.covariate with no share, weight or percentage member in src/api/src/api/routes/line_detail.py after:T040 ← T040:reconcile_covariates
- [ ] T042 [US4] {FR-030,FR-031,FR-033,FR-034,FR-034a} [COMPLETES FR-030] Render names, observed values and association-only wording in src/web/app/lines/[poLineId]/Covariates.tsx after:T019,T041

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T043 [P] {FR-046,FR-048} Assert the property tests, the detail conformance test and the equivalent/plot agreement test all run in the merge gate, in tests/checks/test_detail_checks_run_in_the_gate.py after:T012,T017,T023
- [ ] T044 [P] {FR-038,FR-039,FR-041,FR-052,FR-063,FR-064} Assert the five figure classes admit no sixth in the response as well as on the view — each class's members at the locations x-figure-classes states, StatedFigures closed, and criticality carried nowhere — and that every published percentage — the quantiles' shares and the miss pair as well as the three FR-041 names — takes the bounded form, in src/api/tests/test_detail_conformance.py after:T041; additionally assert has_interval is false, no_interval_reason is the single licensed member, population is the active resolution run's whole linked-record set, span_check_basis declares the proxy, meets_target is null rather than true at a zero denominator with shortfall_causes carrying no_linked_records_to_measure there, and total_count is identical across two different po_line_id responses
- [ ] T045 [P] {FR-040,FR-049} [COMPLETES FR-040] Assert every state the view resolves to is present as accessibility-tree text and visible in the rendered output from the same carrier, in src/web/e2e/line-detail.spec.ts after:T037,T042
- [ ] T046 {FR-024} [P] Assert the memoised per-document census keeps the response inside the 1.5 s p95 envelope, in src/api/tests/test_line_detail.py after:T028 (AD-009)

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
- **T028 completes the validator T011 composes.** FR-068's admitted inputs include the active resolution run
  and a digest over its whole linked-record set, because `source_census` is run-level and moves when a
  resolution run touches some other line. Until T028 supplies them, the validator is scoped to this line's
  own entity and citations and would answer 304 over a census that had changed — which is why T018's
  two-sided check runs after both.
- US2, US3 and US4 depend only on Foundational plus T019, so they proceed in parallel with one another once
  US1's route shell exists.
- Tasks marked `[P]` can run in parallel within their phase. A task carrying `after:T###` or `← T###:Symbol` is
  never `[P]`-batched with the task it references.
