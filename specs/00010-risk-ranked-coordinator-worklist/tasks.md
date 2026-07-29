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
- [X] T026 [P] [US1] {FR-024} Both observation sites — CHK/test_web_has_no_db_driver.py for the manifest and lockfile, TST/test_no_datastore_from_web.py for the request set

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
- [X] T040 [P] [US3] {FR-044} Copy-table distinctness — no entry a substring of another, each holding a phrase in no other — and region assignment, in WTS/worklist.test.tsx
- [X] T041 [US3] {FR-018} [COMPLETES FR-018] All eight states reported as `200` outcomes, counts reconcile, no placeholder anywhere — TST/test_worklist_endpoint.py

---

## Phase 6: US4 - Narrow and Reorder the List (Priority: P2)

**Independent test**: filter to one project and confirm only its lines appear and the ranking reruns within that scope; change the sort key and confirm the order changes and the active key is displayed.

- [X] T042 [US4] {FR-025} `scope.available_projects` with `open_line_count`, the full set in every state including while a scope is active — API/routes/worklist.py
- [X] T043 [US4] {FR-025,FR-051} [COMPLETES FR-025] Scoping control in WEB/page.tsx — full selectable set, keyboard-operable, active scope exposed to assistive technology
- [X] T044 [US4] {FR-026} On-screen enumeration of FR-026's four keys with the active key and direction, in WEB/page.tsx after:T043
- [X] T045 [P] [US4] {FR-026,FR-032} [COMPLETES FR-026] Offered key set holds no delivery-date or single-quantile key; scope reranks — WTS/worklist.test.tsx
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

## Dependencies

Setup → Foundational → US1 → US2 → US3 → US4 → Polish

- **Phase 1 → Phase 2**: T005's `Unit tests (api)` step needs T001's dependencies; nothing in Phase 2 runs in the gate until it lands.
- **Phase 2 is the `no_active_run` slice** and blocks all four stories: `query.py` (T010) → `states.py` (T011) → `routes/worklist.py` (T012) → `page.tsx` (T013).
- **Strict test-first pairs**: T015 → T017 and T016 → T018. The RED task must be observed failing before its GREEN task begins; the branch carries a `test:` commit before the `feat:` commit for each pair.
- **`compute/probability.py` (T018) precedes `risk_read/rows.py` (T020)** — the row assembles finished figures rather than raw values.
- **`risk_read/states.py` (T011, T033) precedes `rows.py` (T020, T035)** — the winning state governs which figures a row carries.
- **The endpoint composes the read modules**: T022 depends on T017, T019, T020 and T021.
- **The interface consumes the contract**: T023 is buildable against the committed `contracts/openapi.yaml` in parallel with the api tier; T024 depends on T023.
- **US3 depends on US1**: T035 extends the `rows.py` T020 and T021 create; T034 and T036 extend the `query.py` T010 and T019 create.
- **US4 depends on US3's P1 half of FR-025**: T042 extends T037's scope handling.
- **Polish depends on all four stories** being complete.
- Tasks marked `[P]` touch disjoint files and carry no ordering dependency on each other. Tasks writing the same file are never `[P]` together: `query.py` (T010, T019, T034, T036), `states.py` (T011, T033), `rows.py` (T020, T021, T035), `routes/worklist.py` (T012, T022, T027, T028, T037, T042), `page.tsx` (T013, T024, T038, T043, T044), `Row.tsx` (T023, T029), `useWorklist.ts` (T030, T031), `test_worklist_endpoint.py` (T014, T025, T032, T041, T046, T049, T051), `worklist.test.tsx` (T040, T045), `worklist.spec.ts` (T047, T048, T051).
