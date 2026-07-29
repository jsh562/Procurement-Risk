# Tasks: Risk-Ranked Coordinator Worklist

**Input**: Design documents from `specs/00010-risk-ranked-coordinator-worklist/`
**Prerequisites**: `plan.md`, `spec.md`, `contracts/openapi.yaml`, `research.md`, `checklists/` (ux, api-quality, testing — all three complete, so no checklist-completion task appears here)

**Tests**: Included. For **two** modules test-first is **mandatory rather than preferred** — `API/compute/ranking.py` and `API/compute/probability.py` are deterministic computation modules under the Testing & Quality Policy, carried as FR-039 rather than as a hint and sequenced by HINT-005. Each lands as an ordered **RED → GREEN** pair: the failing property test is a task in its own right and must be observed failing before its module task begins. Every other tier is test-after.

**Organization**: Grouped by user story (`US#`) per `spec_type: product`. `plan.md` § Requirement Coverage Map is the authority for requirement → component → file; § Story Phasing Note is the authority for FR-025's P1/P2 split; `contracts/openapi.yaml` is the authority for response shape, parameter admissibility and problem vocabulary.

**Size**: 52 tasks against the guide's 40-task split threshold. The four delivery phases hold 32 of them (12 / 6 / 9 / 5); the remaining 20 are the repository-root tooling delta and the shared `no_active_run` slice. Recorded rather than absorbed by thinning a phase — this is the product's first user-facing screen, its first API route, and it carries three owned CI deviations.

## Project Mode

`Mixed`

`plan.md` records `brownfield` for the code, and that is accurate for `/src`. The three owned deviations also change repository-root tooling — `.github/workflows/verify.yml`, root `pyproject.toml`, `src/web/package.json` — which is what makes the task-generation mode Mixed rather than Brownfield. No project-initialization task appears. `~` paths in `plan.md` § Project Structure extend files that already exist; `+` paths are new.

## Path shorthand

| Token | Expands to |
|---|---|
| `API/` | `src/api/src/api/` |
| `TST/` | `src/api/tests/` |
| `WEB/` | `src/web/app/worklist/` |
| `WTS/` | `src/web/__tests__/` |
| `E2E/` | `src/web/e2e/` |
| `CHK/` | `tests/checks/` |

## Epic / Capability Map

- `[US1]` → See what to chase first — the harm score and its ordering, the row's four comparison quantities and its subordinate context, and the read path's boundary guarantees (P1)
- `[US2]` → Ask what happens if a date moves — the `need_by_override` server re-query, its admissibility rules, the session what-if and both acknowledgement outcomes (P1)
- `[US3]` → Know when the system has nothing to say — FR-018a precedence, the eight states, staleness, the excluded group, the empty filter, and the unreadable-artifact failure (P1)
- `[US4]` → Narrow and reorder the list — the scoping control with `available_projects`, and the on-screen enumeration of FR-026's four keys (P2)

## Brownfield Notes

- **`src/api` is nearly empty** — three `__init__.py` files, no app, no route, no `tests/` directory. This is the first code in `compute/` and the first `risk_read/`, `routes/` and `TST/` content anywhere. The computation-boundary contract already guards `compute/`.
- **`src/web` still carries the Next.js starter page**; `WEB/` is the first real route, and `WTS/` holds one unrelated test today.
- **Existing files touched**: `.github/workflows/verify.yml`, root `pyproject.toml`, `src/api/pyproject.toml`, `src/web/package.json`.
- **Every Python tool runs as `uv run --directory src/<entry> …`** — never a bare `pytest`, `ruff` or `coverage`.
- **Entry-local tests never move to root `/tests`**, which is reserved for cross-entry verification. Only `CHK/test_web_has_no_db_driver.py` belongs there, because it compares one entry's dependency set against another's.
- **Reads only.** `purchase_order_line`, `forecast_run` and `line_posterior` were committed by E003 and populated by E005 and E007. This feature adds no table, no migration and no write path.

## Gotchas carried into these tasks

- **The probability of lateness is `survival[k]`, with no complement** (HINT-002, STF-001). Every artifact that said otherwise was corrected on 2026-07-28; a stale copy ranks the safest lines first and looks plausible doing it. The beyond-horizon figure is `residual_tail_mass` itself, not `1 − residual_tail_mass`.
- **`today` is an injected input, never a clock read inside `risk_read` or `compute`** (FR-038, HINT-006). A frozen fixture is stated in absolute dates, so a clock read inside the computation makes FR-029's age, FR-030's calendar-passed flag and the stale banner drift as wall-clock time advances — an acceptance run that passed in July fails in August with no change to the code or the fixture.
- **Round once, in `probability.py`, and derive the complement as `100 − displayed`** (FR-008, HINT-004). Two independent roundings of `0.4951` and `0.5049` produce an FR-006 pair summing to 101%. Python's built-in `round` is half-to-**even** and is forbidden here: `round(12.5)` is 12.
- **The rounding boundary is constructible at exactly four stored doubles** — `0.125`, `0.375`, `0.625`, `0.875`. The fixture carries `0.125` and `0.875`; asserting half-up anywhere else asserts the direction of a representation error.
- **Build the `no_active_run` path before any code can assume a figure exists** (HINT-001). Phase 2 is that slice, end to end.
- **The endpoint address is not settled by implementation** (HINT-003). `specs/sad.md:124` and the contract name different addresses; the branch records the amendment and does not perform it.

---

## Phase 1: Setup (Repository / Workspace Delta)

**Three owned deviations close here.** As `verify.yml` stands, none of this feature's api tests, rendered-page tests or performance measurements would execute, and the 80% gate measures neither `/src/api` nor `/src/web`. FR-040 is what makes that gating rather than merely noted.

- [X] T001 [P] Add the `psycopg` runtime dependency and the `pytest-benchmark` dev dependency in src/api/pyproject.toml, via `uv add --directory src/api`
- [X] T002 {FR-035} src/api/pyproject.toml — `forbidden_modules += api.risk_read`, plus a contract barring `api.risk_read`, `api.routes.worklist` and `api.compute` from `gateway`
- [X] T003 [P] {FR-040} Root pyproject.toml — `source += src/api/src/api` **and** a matching `[tool.coverage.paths]` entry; two edits, or the package is uncounted
- [X] T004 [P] {FR-040} src/web/package.json — `test:e2e` script and `@playwright/test`; `--coverage` with a Vitest v8 floor of 80% scoped to `app/worklist`
- [X] T005 {FR-040} Add `Unit tests (api)`, `E2E (web)` and `Performance benchmark (api)` steps to .github/workflows/verify.yml — api under `coverage run` with its own `COVERAGE_FILE`
- [X] T006 [P] {FR-057} Record the same-commit change rule and the recorded consumers E012, E017, E019 in contracts/openapi.yaml `info` § Compatibility — a review obligation, not a runtime one
- [X] T007 [P] Record the `specs/sad.md:124` `GET /lines?project=…` vs contract `/api/v1/worklist` conflict as a default-branch amendment request — this branch records, never performs (HINT-003)

---

## Phase 2: Foundational (Cross-Work-Item Blockers)

**The `no_active_run` slice ships first, end to end (HINT-001).** It is the only state reachable with an empty `forecast_run`, it is a P1 acceptance scenario in its own right, and building it first forces the absent-figure path to exist before any code can assume figures are present — the failure Principle III names. Every delivery phase extends the same four files this slice creates.

- [X] T008 [P] {FR-036,FR-037} Frozen fixture in TST/fixtures/frozen_run/ — generator, seed, generation date, regeneration command, row digest; loaded only through the migrated schema
- [X] T009 [P] {FR-044} Committed degraded-state copy table keyed by state, each entry naming the cause and what would change it, in WEB/stateCopy.ts → exports: STATE_COPY
- [X] T010 [P] {FR-002,FR-022,FR-038} Active-run pointer lookup, open-line filter excluding terminal lines, injected `today` in API/risk_read/query.py → exports: load_worklist(today, tz)
- [X] T011 {FR-015,FR-018,FR-054} Page states, `no_active_run` echoed onto every row, structural absence in API/risk_read/states.py ← T010:load_worklist → exports: resolve_states()
- [X] T012 {FR-018,FR-038} `GET /api/v1/worklist` and app wiring in API/routes/worklist.py — meta envelope, counts, empty-sequence `ordering_digest` ← T011:resolve_states
- [X] T013 {FR-015,FR-023,FR-024} Server component in WEB/page.tsx — endpoint only, no datastore connection, banner before any row content ← T009:STATE_COPY
- [X] T014 {FR-015} [COMPLETES FR-015] `no_active_run` against an empty `forecast_run` in TST/test_worklist_endpoint.py — every open line listed, no figure anywhere

---

## Phase 3: US1 - See What to Chase First (Priority: P1) 🎯 MVP

**Independent test**: load the worklist against the frozen fixture and confirm the rendered order, figures and per-row decomposition match expected values exactly, with no row added to the model-invocation record.

- [X] T015 [P] [US1] {FR-039,FR-013a} **RED** Failing property test in TST/test_ranking.py — criticality monotonicity under the default key, tiebreak totality, inputs from E003's domain
- [X] T016 [P] [US1] {FR-039,FR-013} **RED** Failing property test in TST/test_probability.py — half-up at stored `0.125`/`0.875`; pair sums to 100 over `bounded:false`, `measure:point` only
- [X] T017 [US1] {FR-001,FR-010,FR-013a} **GREEN** Harm score and FR-010's tiebreak in API/compute/ranking.py after:T015 → exports: expected_harm(), order_lines()
- [X] T018 [US1] {FR-007,FR-008,FR-013,FR-017,FR-030,FR-053} **GREEN** Rounding, bounds, complement, both measures in API/compute/probability.py after:T016 → exports: percent_figure()
- [X] T019 [US1] {FR-020,FR-020a,FR-052} As-of frame, run identification and the ETag validator over exactly FR-020a's admitted inputs (SC-027) in API/risk_read/query.py
- [X] T020 [US1] {FR-003,FR-004,FR-005,FR-006,FR-027} `PrimaryFigures` in API/risk_read/rows.py — pair as one labelled unit ← T018:percent_figure → exports: build_row()
- [X] T021 [US1] {FR-009,FR-041,FR-053,FR-054} `SecondaryContext` in API/risk_read/rows.py — as-of date, criticality, calendar margin; no harm score; explicit empty for a withheld figure
- [X] T022 [US1] {FR-002,FR-026} Compose query, states, rows and ranking in that order, and emit `SortState` in API/routes/worklist.py ← T017:order_lines
- [X] T023 [P] [US1] {FR-032,FR-048,FR-049,FR-050} WEB/Row.tsx — primary/secondary regions, reading order, rank as text, pair under one accessible name, state as text
- [X] T024 [US1] {FR-047} Ranked list, active key and direction, and the server-sent tiebreak rule in WEB/page.tsx after:T023
- [X] T025 [US1] {FR-002,FR-023,FR-035} [COMPLETES FR-002] Provider unreachable renders fully and the invocation record gains no row — TST/test_worklist_endpoint.py
- [X] T026 [P] [US1] {FR-024} Both observation sites — CHK/test_web_has_no_db_driver.py for the manifest and lockfile, TST/test_read_path_isolation.py for the request set (this second path was written as `test_no_datastore_from_web.py`; the work landed in `test_read_path_isolation.py` beside the other read-path assertions, and the entry is corrected to the file that holds it)

---

## Phase 4: US2 - Ask What Happens If a Date Moves (Priority: P1) 🎯 MVP

**Independent test**: change one line's need-by date and confirm the list reorders, the probability moves in the expected direction, and the invocation record gains no row.

- [X] T027 [US2] {FR-055} Repeatable `need_by_override`, admissibility (cap 25, duplicate, malformed, ten-year window) and the problem+json 422 handler in API/routes/worklist.py
- [X] T028 [US2] {FR-031,FR-055} Apply overrides to the effective need-by, `NeedBy.source`/`unsaved`, `overrides.applied`/`unapplied` in API/routes/worklist.py
- [X] T029 [P] [US2] {FR-031,FR-051} Keyboard-operable need-by control and the unsaved text mark adjacent to the date it qualifies, in WEB/Row.tsx
- [X] T030 [US2] {FR-011,FR-012} Session override set, server re-query, `ordering_digest` comparison and the persistent unchanged acknowledgement in WEB/useWorklist.ts
- [X] T031 [US2] {FR-012,FR-046} Order-changed acknowledgement naming the new position, in the same live region, focus retained on the adjusted control — WEB/useWorklist.ts
- [X] T032 [US2] {FR-055} [COMPLETES FR-055] Overrides applied, refused with cause, and reported unapplied with which of the three — TST/test_worklist_endpoint.py

---

## Phase 5: US3 - Know When the System Has Nothing to Say (Priority: P1) 🎯 MVP

**Independent test**: load the worklist in each of the eight degraded states and confirm each renders its own distinct wording with no risk figure fabricated. The `project_id` parameter is delivered here, not in US4, because `empty_filter` is otherwise unreachable at P1 (§ Story Phasing Note).

- [X] T033 [P] [US3] {FR-016,FR-018a,FR-021} FR-018a precedence and the excluded group in API/risk_read/states.py — roster mismatch, not covered, beyond horizon, already late, calendar passed
- [X] T034 [P] [US3] {FR-019,FR-029} Age as `today − as_of_date`, `stale`, the threshold with its recorded basis, per-row as-of date in API/risk_read/query.py
- [X] T035 [US3] {FR-017,FR-030,FR-054} [COMPLETES FR-054] Already-late suppresses only the miss probability; beyond horizon bounds by `residual_tail_mass` — API/risk_read/rows.py
- [X] T036 [US3] {FR-045} Excluded group ordered by need-by ascending then `po_line_id` ascending, invariant under every sort key — API/risk_read/query.py
- [X] T037 [US3] {FR-025,FR-042} P1 half of FR-025 — `project_id` parameter, its pattern validator and `WHERE` clause — plus the `empty_filter` page state, in API/routes/worklist.py
- [X] T038 [US3] {FR-043} 500 `unsupported-artifact-schema` / `internal-error` and 503 `datastore-unavailable` with `correlation_id`, and the page-level failure state in WEB/page.tsx
- [X] T039 [P] [US3] {FR-018a,FR-033} Jointly constructible co-occurrences and the precedence winner for each pair, in TST/test_states.py
- [X] T040 [P] [US3] {FR-044} Copy-table distinctness — no entry a substring of another, each holding a phrase in no other — and region assignment, in WTS/stateCopy.test.ts (written as `worklist.test.tsx`; the assertions belong beside the table they pin, and colocation is what keeps the two from being moved apart)
- [X] T041 [US3] {FR-018} [COMPLETES FR-018] All eight states reported as `200` outcomes, counts reconcile, no placeholder anywhere — TST/test_worklist_endpoint.py

---

## Phase 6: US4 - Narrow and Reorder the List (Priority: P2)

**Independent test**: filter to one project and confirm only its lines appear and the ranking reruns within that scope; change the sort key and confirm the order changes and the active key is displayed.

- [X] T042 [US4] {FR-025} `scope.available_projects` with `open_line_count`, the full set in every state including while a scope is active — API/routes/worklist.py
- [X] T043 [US4] {FR-025,FR-051} [COMPLETES FR-025] Scoping control in WEB/page.tsx — full selectable set, keyboard-operable, active scope exposed to assistive technology
- [X] T044 [US4] {FR-026} On-screen enumeration of FR-026's four keys with the active key and direction, in WEB/page.tsx after:T043
- [X] T045 [P] [US4] {FR-026,FR-032} [COMPLETES FR-026] Offered key set holds no delivery-date or single-quantile key; scope reranks — WTS/WorklistBoard.test.tsx (written as `worklist.test.tsx`; the sort control lives in `WorklistBoard`, so its assertions do too)
- [X] T046 [P] [US4] {FR-045} Excluded-group order and scope invariance under all four keys, in TST/test_worklist_endpoint.py

---

## Phase 7: Polish & Cross-Cutting Concerns

- [X] T047 {FR-032,FR-041,FR-046} [COMPLETES FR-032] Presentation contract in E2E/worklist.spec.ts — type scale, reading order, as-of date without hover, live region for both outcomes
- [X] T048 {FR-048,FR-049,FR-050,FR-051} [COMPLETES FR-051] Accessibility in E2E/worklist.spec.ts — position as text, pair under one name, keyboard operation, bounded forms spoken
- [X] T049 [P] {FR-028,FR-031,FR-056} [COMPLETES FR-031] No write path, no criticality mutation, no adjusted date persisted, no authentication scheme — TST/test_worklist_endpoint.py
- [X] T050 [P] {FR-040,FR-039} [COMPLETES FR-039] Both p95 variants under the plan's recorded conditions — one vCPU limit, warm, 200 samples — in TST/test_worklist_benchmark.py
- [X] T051 {FR-034} Named observables for SC-001, SC-004 and SC-008 in TST/test_worklist_endpoint.py and E2E/worklist.spec.ts
- [X] T052 {FR-040} [COMPLETES FR-040] Verify every check this feature adds runs in the merge gate and that both coverage floors fail independently below 80%

---

## Phase: Bug Fixes

- [X] T053 [BUG:CRITICAL] {FR-040} [pi-violation] Principle VII — the recorded security limitation states two falsehoods and its own reversal trigger has fired — specs/00010-risk-ranked-coordinator-worklist/plan.md:247-251
  > Measured: `npm audit --omit=dev` reports 3 high advisories on **production** dependencies of /src/web — `next` (direct), `postcss`, `sharp` (CVE-2026-33327/33328/35590/35591). The record's evidence claims all 12 chain from `brace-expansion` through ESLint and are "dev-only".
  > The record's scope decision says "dependency advisories are surfaced in CI and do not fail the build". No `pip-audit` or `npm audit` step exists in .github/workflows/verify.yml — they are surfaced nowhere.
  > The record's stated reversal trigger is "any advisory affecting a runtime dependency of /src/api or /src/web". It has fired.
  > Fix hint: correct the evidence, act on the trigger (pin resolutions or record a waiver), and either add a non-gating audit step or drop the claim that CI surfaces them.

- [X] T054 [BUG:ERROR] {FR-042} [behaviour] The scoping control is never on screen in the empty-filter state — src/web/app/worklist/page.tsx:50
  > FR-042: "MUST leave the scoping control and its full set of selectable projects on screen so the coordinator can leave the scope without reloading or guessing."
  > `empty_filter` is emitted only when `scoped and not resolved` (states.py:196), so `counts.total === 0` always holds in that state. page.tsx:50 then renders the empty paragraph and never mounts `WorklistBoard`, which is where `Controls` lives (WorklistBoard.tsx:51).
  > Verified against the running endpoint: GET /api/v1/worklist?project_id=PRJ-009 returns page_states ['stale_run','empty_filter'], counts.total 0, and available_projects ['PRJ-001','PRJ-002','PRJ-003']. The payload is correct; the render discards it. The coordinator's only exit is a reload.
  > Fix hint: render the controls whenever a scope is active, independently of `counts.total`. Add a page test for the empty-filter state asserting the project select is present.

- [X] T055 [BUG:ERROR] {FR-029} [missing-clause] The row-level stale mark is unimplemented — src/api/src/api/risk_read/rows.py:268
  > FR-029 (spec.md:236): "Because a page-level banner alone stops carrying once rows are sorted, filtered, or read one at a time, the row MUST carry the signal too: while the active run is stale, every populated row's as-of date ... MUST be marked as stale in the row itself."
  > `build_secondary` returns exactly {as_of_date, criticality, calendar_margin_days}; `RowInputs` carries no staleness; Row.tsx:182 renders "Forecast as of <date>" unqualified. Staleness reaches the client only via meta.forecast_run.stale and the page banner — the exact insufficiency the clause names.
  > The requirement notes this needs no new figure and no new field: the response already carries the run's staleness and each row's as-of date.
  > Fix hint: thread `stale` into `RowInputs`, qualify the row's as-of text when set, assert at the API and rendered tiers.

- [X] T056 [BUG:ERROR] {FR-051} [missing-clause] Bounded forms are not announced as words — src/web/app/worklist/Row.tsx:123
  > FR-051: "FR-008's bounded forms MUST be announced as words, 'less than one percent' and 'greater than ninety-nine percent': the < and > glyphs are read inconsistently or dropped outright, and a dropped < turns <1% into a flat 1%, which is a false precision of exactly the kind FR-008 exists to remove."
  > Row.tsx:123 renders the display string raw. Searching src/web/app and src/web/e2e for "less than one percent" or "greater than ninety" returns nothing.
  > The e2e test named for this asserts only that <1% is visible and 0%/100% are absent, and is wrapped in an `if (count > 0)` guard so it vacates silently if the fixture stops producing a bound.
  > Fix hint: add a visually-hidden spoken form or aria-label beside the glyph; assert the words; remove the count guard.

- [X] T057 [BUG:ERROR] {FR-036} [test-isolation] The E2E seed truncates and commits to the shared development database — src/api/tests/fixtures/frozen_run/seed.py:53
  > seed.py unconditionally DELETEs line_posterior, forecast_run, lifecycle_event and purchase_order_line and commits, replacing E005's ~200-line dataset with E010's 16 fixture lines.
  > src/model/tests/forecast/conftest.py:578 treats any non-empty purchase_order_line as proof the committed dataset is loaded (`if loaded: return loaded`), so the forecast tier fits against E010's fixture instead of reloading.
  > Reproduced: `uv run --directory src/model pytest tests/forecast -x` -> "forecast-fit refused: ModelError: the sojourn frame is empty ... read 16 lines and 6 lifecycle events" — E010's exact counts.
  > CI is green only incidentally: Unit tests (model) is step 14, Seed the frozen fixture is step 22. Nothing enforces that ordering.
  > Fix hint: give the E2E tier its own database, or restore E005's dataset on teardown. Separately worth raising against `committed_dataset`, which should verify the dataset's identity rather than its non-emptiness.

- [X] T058 [BUG:WARNING] {FR-030} [test-coverage] A calendar-passed row's retained probability is asserted nowhere — src/api/tests/test_worklist_degraded.py
  > US3 scenario 8 requires the row to show its forecast probability and separately flag the passed date. `test_calendar_passed` asserts only the state value.
  > Both suite-wide probability loops skip None figures, so a regression suppressing PO-4475-1's miss probability would pass every test in the suite.
  > Fix hint: assert miss_probability is not None and its value for the calendar-passed fixture line.

- [X] T059 [BUG:WARNING] {FR-003} [provenance] The per-figure reference class publishes schema_constants' draw count, not the run's — src/api/src/api/risk_read/rows.py:196
  > FR-003 and CHK021 require the reference class published twice — per quantile and at page scope — to agree, with the figure's copy authoritative; a disagreement is "a provenance defect under Principle I, not a display choice".
  > Both rows.py:196 and routes/worklist.py:338 read inputs.conventions.draw_count, sourced from schema_constants. The figure was computed from the run's draws, whose count is forecast_run.draw_count — a separate column. Only ck_schema_constants__draw_count_positive and ck_forecast_run__draw_count_positive exist; nothing ties the two values together.
  > They agree today only because the fixture uses 4000 for both. A run fitted at a different draw count would publish a wrong denominator on every quantile.
  > Fix hint: source the per-figure copy from the run; assert the two agree within a response.

- [X] T060 [BUG:WARNING] {FR-039} [test-coverage] The survival-array input domain is generated nowhere, and the test-first commit evidence is absent — src/api/tests/test_probability.py
  > FR-039 names the generated domain: "survival arrays non-increasing and within [0,1] at length horizon_days, with the last element equal to residual_tail_mass". No such strategy exists; test_probability.py's only strategy is st.floats(0, 1). FR-013's property is therefore discharged by two endpoint examples plus a display-order property, not by a property over the offset domain.
  > Separately: tasks.md Dependencies states "the branch carries a `test:` commit before the `feat:` commit for each pair". It does not — T015-T018 landed together in 1b96a5d. The RED state was observed during implementation, but the committed evidence the tasks file promises is absent. E007 on this same history used `test(E007): T094 RED`, so the convention exists and E010 departed from it.
  > Fix hint: add a survival-array strategy and a property over as_of < d <= as_of + horizon_days.

- [X] T061 [BUG:WARNING] {FR-052} [test-coverage] Run identification is untested at the tier that produces it — src/api/src/api/routes/worklist.py:325
  > _meta emits run_id, model_version and artifact_schema_version, but no API-tier assertion covers them. The only evidence is page.test.tsx "names the run and its as-of date once one is active", which renders a hand-authored stub rather than a server response.
  > Fix hint: assert the three fields against the frozen fixture's run in test_worklist_ranked.py.

- [X] T062 [BUG:WARNING] {FR-024} [placement] The manifest check does not exist at the path T026 and the plan both name — tests/checks/test_web_has_no_db_driver.py
  > T026 and plan.md Observation procedures both name tests/checks/test_web_has_no_db_driver.py, with an explicit Source Code Layout rationale for the root placement. The assertions exist and run, but live in src/api/tests/test_read_path_isolation.py:144-190.
  > Fix hint: move the two manifest/lockfile assertions to the named path, or amend the task and plan to record the actual location and why.

- [X] T063 [BUG:WARNING] {FR-036} [test-coverage] One declared boundary case has a fixture line but no named test — src/api/tests/fixtures/frozen_run/fixture.json
  > need_by_last_in_grid_day (PO-4475-2) is generated, committed and verified for its own values, but no acceptance test reads it. The boundary is asserted with a synthetic line in test_states.py::test_the_last_in_grid_day_is_not_beyond_the_horizon.
  > FR-036 requires one named test per case against the committed fixture.
  > Fix hint: assert PO-4475-2 through the endpoint — its miss probability equals its residual tail mass at the last in-grid day.

- [X] T064 [BUG:WARNING] {FR-057} [test-coverage] No test validates a served response against contracts/openapi.yaml — src/api/tests
  > The contract declares closed objects (additionalProperties: false) and FR-057 binds three later epics to it, but nothing machine-checks a response against the document. Closure rests on hand-written key-set assertions covering primary, secondary and unranked.primary — not miss_probability, duration_pair, meta or sort.
  > Fix hint: validate a live response against the schema with openapi-core or jsonschema.

- [X] T065 [BUG:WARNING] {FR-040} [accuracy] The .completed marker overstates web coverage — specs/00010-risk-ranked-coordinator-worklist/.completed:11
  > Claims "100% statements / 98.4% branches". Measured this run: 99.2% statements (125/126), 95.74% branches (90/94). The figures predate Phase 6's Controls component and its tests. Both remain far above the 80 floor, so no gate impact — but a published figure that does not match its measurement is the defect Principle I names.
  > Fix hint: regenerate the marker's figures from the run that writes it.


- [X] T066 [BUG:ERROR] {FR-027,FR-029} [requirement-conflict] `as_of_is_stale` is a new row field both requirements forbid, and the contract was amended to accommodate it — src/api/src/api/risk_read/rows.py:305
  > FR-029 states the row-level mark "needs no new figure and **no new field**; the response already carries the run's staleness and each row's as-of date". The T055 fix added a per-row field anyway.
  > FR-027 closes the secondary region at "exactly the three items FR-009 and FR-019 require -- the as-of date, the criticality, and the calendar margin -- in the secondary region and nothing else", and adds "Anything outside these three closed sets is a fifth comparison quantity whatever region it is rendered in, and that is the counting procedure SC-014 is evaluated by".
  > `contracts/openapi.yaml:917` was amended to four required members with prose justifying the addition; `spec.md` was not, so the two now disagree and only the contract records the divergence.
  > This is the same wrong direction as widening a dependency allowlist to make a test pass: the requirement said how to do it and the implementation did something else, then moved the contract to match.
  > Fix hint: remove the member from the payload, the contract and the TS type; have the renderer qualify the row's as-of text from `meta.forecast_run.stale`, which the response already carries. Re-target the API-tier assertions at `meta.forecast_run.stale`. Keep the rendered-tier and e2e assertions — those test the requirement, not the mechanism.

- [X] T067 [BUG:ERROR] {FR-039} [test-quality] T060's third property is a tautology, and none of the three exercise the production reader — src/api/tests/test_probability.py:266
  > `_read(survival, offset)` is `percent_figure(survival[offset - 1])`. `test_the_residual_tail_mass_is_the_last_grid_entry` asserts `_read(survival, len(survival)).display == percent_figure(survival[-1]).display` — both sides are the same call on the same float. It asserts `x == x` and can never fail on its own terms.
  > It is named in `.completed` as one of three properties discharging T060, so the marker overstates what was delivered — the same defect T065 corrected.
  > `survival_arrays` does not generate `residual_tail_mass`, though its docstring and FR-039 both name "the last element equal to `residual_tail_mass` within `PROB_SUM_TOLERANCE`" as part of the domain.
  > All three properties assert over the test-local `_read` rather than `api.risk_read.rows.miss_probability`, which is the function that actually performs this read. An off-by-one or a complement introduced in `rows.py:160` would leave all three green; the inversion is guarded only by an example at `test_worklist_ranked.py:108`, which is what T060 said was insufficient.
  > Fix hint: generate `(survival, residual_tail_mass)` as a pair so the storage invariant is modelled, and drive the properties through `miss_probability` with a constructed `RowInputs` so production code is under test. Then re-check that a mutation to `rows.py` fails them.

- [X] T068 [BUG:WARNING] {FR-039} [accuracy] tasks.md still claims a `test:` commit precedes each `feat:` commit — specs/00010-risk-ranked-coordinator-worklist/tasks.md:233
  > The Dependencies section reads "the branch carries a `test:` commit before the `feat:` commit for each pair". `git log --all -- src/api/tests/test_ranking.py src/api/src/api/compute/ranking.py` returns `1b96a5d` for both files: T015-T018 landed together.
  > The RED state was observed during implementation and FR-039's substance was met; the committed evidence the tasks file promises does not exist. T065 corrected a figure of exactly this kind and this sentence was left standing.
  > Fix hint: amend the sentence to record what happened, or drop the claim. Do not retro-fit history.

- [X] T069 [BUG:WARNING] {FR-035} [test-coverage] The invocation-record test carries no adjustment — src/api/tests/test_read_path_isolation.py:97
  > FR-035 and SC-003 require that a worklist request, "including one carrying a need-by adjustment", adds no row to the model-invocation record. The test issues a plain `client.get("/api/v1/worklist")` with no `need_by_override`.
  > The table half is blocked on E008 and the test skips honestly. The adjustment half is not blocked and was flagged in QC iteration 1 as fixable now.
  > Fix hint: add the override-carrying request to the same test so it is covered the day E008's table lands.

- [X] T070 [BUG:WARNING] {FR-024} [accuracy] The recorded runtime observation for FR-024 does not exist — specs/00010-risk-ranked-coordinator-worklist/plan.md:203
  > The plan records: "during a Playwright page load every outbound request the page issues is recorded, and each must target the worklist endpoint with none targeting the database port". `src/web/e2e/worklist.spec.ts` contains no `page.on('request')` and no route interception.
  > The runtime half is discharged instead by a source scan of `worklist.ts` at `src/api/tests/test_read_path_isolation.py:129`. That is a weaker observable than the one recorded, and the record does not say so — the same defect class T062 fixed for the manifest half.
  > Fix hint: add the request-recording spec, or amend the plan to record the source scan as the actual observable with its limitation.

- [X] T071 [BUG:WARNING] {FR-040} [accuracy] Three task/plan entries name files that do not exist — specs/00010-risk-ranked-coordinator-worklist/tasks.md
  > T026 and plan.md:237 name `TST/test_no_datastore_from_web.py`; T040/T045 and plan.md:235 name `WTS/worklist.test.tsx`. Neither exists. The work lives in `src/api/tests/test_read_path_isolation.py` and `src/web/app/worklist/*.test.tsx`.
  > T062 moved one file to match its named path and left these standing, so the naming is now inconsistent rather than uniformly aspirational.
  > Fix hint: amend the entries to the paths that hold the work.

- [X] T072 [BUG:WARNING] {FR-040} [accuracy] SC-017 and SC-018 are measured against 16 lines, not the recorded population — src/api/tests/test_worklist_benchmark.py:87
  > plan.md:184 records the benchmark's line population as "The E005 seeded set -- ~200 open lines across 5 projects"; plan.md:115 repeats "~200 lines". The benchmark takes the `frozen_run` fixture, which truncates and seeds 16.
  > QC iteration 1 reported both criteria as PASSED "under the plan's recorded conditions". One of those conditions is not met. The measured margin is roughly 30x, so the criteria very likely hold at 200 lines — but "likely holds" is not the claim that was published.
  > Fix hint: benchmark against the E005 population, or amend the recorded condition to the population actually used and re-state the figures as scoped to it.

- [X] T073 [BUG:WARNING] {FR-032} [test-coverage] The type-scale assertion measures the secondary container, not its descendants — src/web/e2e/worklist.spec.ts:46
  > FR-032 requires the secondary region to be smaller than the primary and never heavier. The spec reads the computed weight of `[class*='secondary']` (400) rather than of the elements inside it.
  > The T055 fix added `.staleAsOf { font-weight: 600 }` inside that region. It holds by equality with `.identity`'s 600, so the requirement is not currently violated — but the assertion would not have noticed if it were, which is the property FR-032 asks to be asserted rather than reviewed.
  > Fix hint: assert over the heaviest descendant of the secondary region, not the container.


- [X] T074 [BUG:CRITICAL] {FR-040} [governance] E010's compliance audit names a superseded project-instructions version — specs/00010-risk-ranked-coordinator-worklist/plan.md:30
  > The Instructions Check records "Audited against `project-instructions.md` v1.2.4". `main` now carries **v1.2.7**, amended 2026-07-29 by v1.2.5, v1.2.6 and v1.2.7 — three revisions of one new Temporary Files rule.
  > Governance states: "A feature whose recorded compliance audit names a superseded version of this document MUST re-run its compliance gate before passing its next phase gate. — An amendment moves the ground under every epic already in flight, and their branches keep validating against the version they were cut from."
  > E010's next phase gate is QC. The re-run is therefore a precondition of `.qc-passed`, not a follow-up.
  > Fix hint: re-audit every row of the Instructions Check against v1.2.7, add a row for the new Temporary Files rule, and update the recorded version. T075 is the one row that does not already pass.

- [X] T075 [BUG:ERROR] {FR-040} [governance] `src/api`'s pytest configuration does not pin `--basetemp` — src/api/pyproject.toml:48
  > project-instructions.md v1.2.5+ § Temporary Files: "Every command run against this repository MUST direct temporary files into the checkout's own gitignored `.tmp/` — `TMPDIR`, `TEMP` and `TMP` set to `$PWD/.tmp`, and **`--basetemp` pinned there in the root and in each entry's pytest configuration**." Each path MUST be absolute.
  > Measured: no `basetemp` appears in `src/api/pyproject.toml`, `src/model/pyproject.toml`, `src/gateway/pyproject.toml` or the root `pyproject.toml`. `.tmp/` is gitignored (`.gitignore:17`), so the destination exists and nothing writes to it.
  > E010 owns `src/api`'s pytest configuration — it added `testpaths` and `markers` there — so that entry is this epic's to fix. The other three are outside E010's scope and are reported rather than changed.
  > The rule postdates E010's recorded audit (v1.2.4), which is why T074 and this task arrive together.
  > Fix hint: add `addopts = "--basetemp=..."` resolved to an absolute path under the checkout, and confirm pytest actually writes there.

---

## Dependencies

Setup → Foundational → US1 → US2 → US3 → US4 → Polish

- **Phase 1 → Phase 2**: T005's `Unit tests (api)` step needs T001's dependencies; nothing in Phase 2 runs in the gate until it lands.
- **Phase 2 is the `no_active_run` slice** and blocks all four stories: `query.py` (T010) → `states.py` (T011) → `routes/worklist.py` (T012) → `page.tsx` (T013).
- **Strict test-first pairs**: T015 → T017 and T016 → T018. The RED task must be observed failing before its GREEN task begins. **The branch does not carry a separate `test:` commit for either pair** — this line originally promised one, and QC found the promise false: `git log` shows `test_ranking.py`, `test_probability.py`, `compute/ranking.py` and `compute/probability.py` all first appearing in `1b96a5d`. The RED state *was* observed — both modules were imported by tests that failed with `ModuleNotFoundError` before either was written — but the committed evidence a reader could check does not exist. Corrected rather than back-filled: a changelog states what happened, and rewriting history to match a claim is the opposite of the property FR-039 asks for.
- **`compute/probability.py` (T018) precedes `risk_read/rows.py` (T020)** — the row assembles finished figures rather than raw values.
- **`risk_read/states.py` (T011, T033) precedes `rows.py` (T020, T035)** — the winning state governs which figures a row carries.
- **The endpoint composes the read modules**: T022 depends on T017, T019, T020 and T021.
- **The interface consumes the contract**: T023 is buildable against the committed `contracts/openapi.yaml` in parallel with the api tier; T024 depends on T023.
- **US3 depends on US1**: T035 extends the `rows.py` T020 and T021 create; T034 and T036 extend the `query.py` T010 and T019 create.
- **US4 depends on US3's P1 half of FR-025**: T042 extends T037's scope handling.
- **Polish depends on all four stories** being complete.
- Tasks marked `[P]` touch disjoint files and carry no ordering dependency on each other. Tasks writing the same file are never `[P]` together: `query.py` (T010, T019, T034, T036), `states.py` (T011, T033), `rows.py` (T020, T021, T035), `routes/worklist.py` (T012, T022, T027, T028, T037, T042), `page.tsx` (T013, T024, T038, T043, T044), `Row.tsx` (T023, T029), `useWorklist.ts` (T030, T031), `test_worklist_endpoint.py` (T014, T025, T032, T041, T046, T049, T051), `worklist.test.tsx` (T040, T045), `worklist.spec.ts` (T047, T048, T051).
