# Tasks: Delivery Forecast Model

**Input**: Design documents from `specs/00007-delivery-forecast-model/`
**Prerequisites**: `plan.md`, `spec.md`, `data-model.md`, `research.md`, `checklists/` (data-integrity, testing, observability — all 115 items evaluated, so no checklist-completion task appears here)

**Tests**: Included, and for **ten** modules **test-first is mandatory rather than optional**. `plan.md` § What qualifies admits `split.py`, `serialize.py`, `censoring.py`, `posterior.py`, `diagnostics.py`, `shrinkage.py`, `ablation.py`, `likelihood.py`, `compare.py` and `design.py` to the property tier — E005's three-clause rule applied as a *widening* filter over the Testing & Quality Policy's own naming, never as a ground for exclusion. Each is emitted as an ordered **RED → GREEN** pair: the property-test task must be observed failing before its implementation task begins, and the branch history carries a `test:` commit before the `feat:` commit for each pair, checkable before the squash merge. Every other module is test-after.

**Organization**: Grouped by user story (`US#`) per `spec_type: product`. Requirement tags are `FR-###`. `plan.md` § Requirement Coverage Map is the authority for requirement → component → file; § Success Criterion Coverage Map is the authority for criterion → validation rule → negative control; `data-model.md` § Validation Rules is the authority for which tier each `DV-###` lands in, through its **Enforcement point** and **Tier** columns.

## Project Mode

`Brownfield`

E001 scaffolded the four entries and the toolchain; E002 established the `model.corpus` package shape; **E003 delivered the schema and E005 delivered the data, and both are fixed input**. No project-initialization task appears here. `~` paths in `plan.md` § Project Structure extend files that already exist; `+` paths are new.

## Path shorthand

| Token | Expands to |
|---|---|
| `PKG/` | `src/model/src/model/forecast/` |
| `TST/` | `src/model/tests/forecast/` |
| `MIG/` | `src/model/src/model/schema/versions/` |
| `SCH/` | `src/model/tests/schema/` |
| `CHK/` | `tests/checks/` |

## Epic / Capability Map

- `[US1]` → Forecast every open line — the sojourn graph, the sampler, inverse-CDF conditioning, the survival grid, per-vendor shrinkage, the manifest, the write path and the run report (P1)
- `[US2]` → Account for orders that have not finished — the stored censoring indicator checked against its source, and the ablation with its independently derived Kaplan–Meier floor (P1)
- `[US3]` → Hold out data so the forecast can be graded — the second artifact population, split completeness, the emitted-artifact obligations and the four-part limitation set (P1)
- `[US4]` → Refuse a fit that did not converge — the diagnostics store, both pre-sampling preconditions, the post-sampling gate, and the refusal report (P1)
- `[US5]` → Reproduce a published forecast — the percentile oracle, the harness, three outcomes, the two refusals and the warning, and the import contract (P2)

## Brownfield Notes

- **New package `model.forecast`, 20 modules, two console entry points** (`forecast-fit`, `forecast-reproduce`). No new third-party dependency: PyMC, ArviZ, NumPy and pandas are **already declared** in `src/model/pyproject.toml`, and the Plan audit verified this rather than accepting it (Assumption 5). A task that adds one is out of scope, not missing.
- **Reuse, do not re-author.** `model.roster.reader.canonical_bytes` and `content_hash` for canonical serialization — E005's AD-001 records that the rule set already lives in the repo twice and that a **third copy is the defect, not the fix**. `model.procurement.paths` for artifact path resolution. E003's `fn_is_sorted_ascending`, `fn_is_non_increasing` and `fn_all_within_unit_interval` are **called** by the new tables' checks and never re-declared — DV-026 is the assertion, and E007 declares exactly one function of its own.
- **Four migrations, and `0300`'s parent is the head at landing.** E008 and E009 branch from the same Wave-4 baseline; if two of them each name `0103` the prefixes stay disjoint and the revision graph acquires two heads. Whichever lands second **re-parents, never renumbers**. The mechanism that catches a miss is the single-head and linearity check, not the block check (DV-033).
- **Existing files touched**: `pyproject.toml` (coverage `source` **and** `paths`), `src/model/pyproject.toml` (two `[project.scripts]`, one `[tool.importlinter]` contract), `CHK/test_migration_ranges.py`, `SCH/test_migration_chain.py`, `SCH/test_forecast.py`, `SCH/test_constants_agreement.py`, `.github/workflows/verify.yml`.
- **Never run `CHK/test_orchestration.py`** — its teardown destroys the database volume. The local database is on `${PRC_DB_PORT:-5434}`.
- **The nineteen disclosed gaps `G-1`–`G-19` are not tasks.** They are recorded as uncovered enforcement, which is the point of recording them. The one exception is **G-1**, whose remediation *is* AD-007: it lands as T003 with T011 as its failing direction.
- **The eight propagation obligations `P-1`–`P-8` are out of scope and are not tasks.** Each is a statement in another epic's delivered artifact or in a registered document a feature branch may not amend: P-1 and P-2 (E003's data model), P-3 and P-5 (E005, merged and immutable), P-4 (the PRD's ~120-event assumption and its 73–87% band — recorded here, never routed to another feature branch), P-6 ({SAD:ADR-0013}), P-7 (the published anchor constant's documentation and `/src/api`'s serving contract), P-8 (E010's read contract for `held_out_prediction`).
- **Two criteria are discharged by nothing this list can check.** SC-032's prohibition half is verified by **review of the commit history** of `PKG/config.py` (AD-005, G-11) — T077 asserts the mechanism half and records the prohibition half as uncovered rather than letting a green suite read as covering it. SC-022's tie-breaking half is discharged by argument (G-13); only the recorded serialization label is checkable.

## Design constants — quoted, never re-derived

`spec.md` § Published Constants, `plan.md` § Sampling shape and AD-004 fixed these. Tasks consume them rather than re-solving them.

| Constant | Value |
|---|---|
| R-hat max / bulk ESS min / tail ESS min | 1.01 / 400 / 400 — *blocking diagnostics* |
| Divergent transitions max / E-BFMI min | 0 / 0.3 — *blocking diagnostics* |
| Chains, minimum | **4** — *blocking **precondition**; refuses before sampling, so nothing is sampled* |
| Max treedepth hits | 0 — **reported, never blocking** (`ck_forecast_diagnostic__blocking_matches_metric`) |
| Sampling shape | 4 chains × 1,000 post-warmup draws = `draw_count` 4,000; 1,000 tuning draws; horizon 365 |
| Held-out fraction / split seed | `HELD_OUT_FRACTION = 0.25` and `SPLIT_SEED`, both **committed constants**, never run flags (AD-005) |
| Reproduction tolerance | **5.0 days** absolute, on each line's median and P80, across **both** stores (AD-004), with the predictive-ESS basis condition published beside it |
| Residual-agreement tolerance | `schema_constants.probability_sum_tolerance` (`1e-9`) — read, never restated |
| Serialization labels | `canonical-json-sorted-keys-utf8`; draws `float64-le-c-contiguous` |
| Recorded semantics | `conditional_remaining_duration_from_run_as_of_date` (open) / `total_duration_from_line_order_date` (held-out) |
| Grid and residual identity | `survival[k] = count(draws > k)/draw_count`, `k = 1..horizon_days`; the residual is the same strict `>` at `k = horizon_days` |

---

## Phase 1: Setup (Repository / Workspace Delta)

- [X] T001 [P] Add `model/forecast` to coverage `source` **and** a `forecast` entry to `[tool.coverage.paths]` in pyproject.toml — two edits, or the package is uncounted
- [X] T002 [P] Declare the `forecast-fit` and `forecast-reproduce` console entry points in src/model/pyproject.toml

---

## Phase 2: Foundational (Cross-Work-Item Blockers)

**The schema and four shared pure modules gate every delivery phase.** `serialize.py` gates all three recomputable digests, `censoring.py` gates the split's stratum and the likelihood, `split.py` gates the fit's own input frame, `likelihood.py` gates the graph. **T003 → T004 → T005 land as one change** — see § Dependencies.

- [X] T003 AD-007's six parts in one change — (a)–(e) in CHK/test_migration_ranges.py, (f) the second `DECLARED_BLOCKS` in SCH/test_migration_chain.py; graph checks unaltered
- [X] T004 {FR-036} Migration `0300` in MIG/0300_forecast_run_provenance.py — empty-table guard with a named error, `fn_vendor_shrinkage_wellformed`, fourteen NOT NULL columns
- [X] T005 {FR-036} G-2 — extend **all three** `forecast_run` builders in SCH/test_forecast.py: two `INSERT` constants *and* `FIXTURE_RUN`, fourteen values each after:T004
- [X] T006 {FR-005} Migration `0301` in MIG/0301_forecast_split_assignment.py — table, `uq_…__run_ordinal`, `ix_…__po_line`, explicit `GRANT SELECT, INSERT, DELETE`
- [X] T007 {FR-012} Migration `0302` in MIG/0302_held_out_prediction.py — `uq_purchase_order_line__order_anchor` **and** `held_out_prediction` in one revision (HINT-001)
- [X] T008 {FR-016} Migration `0303` in MIG/0303_forecast_diagnostic.py — the table and `uq_forecast_diagnostic__run_metric_parameter` `NULLS NOT DISTINCT`; same grant
- [X] T009 G-3 — generalise the drift test in SCH/test_constants_agreement.py to every constraint carrying a double-precision literal, so the fourth `1e-9` is audited after:T007
- [X] T010 DV-026 — no second `CREATE FUNCTION` in the four revisions and `pg_proc` counted before and after the chain, in TST/test_function_inventory.py
- [X] T011 **NC-12** — AD-007 verified: `0400` probes fail as outside, `0200`–`0299` passes as declared-but-unpopulated, part (a) alone leaves the suite red; DV-033/DV-034 green
- [X] T012 Create the forecast fixtures — live PostgreSQL on `${PRC_DB_PORT:-5434}` at head `0303`, tmp report roots — in TST/conftest.py, following SCH/conftest.py
- [X] T013 [P] Create `PKG/__init__.py` and the artifact/report path resolution in PKG/paths.py → exports: run_report_path(), refusal_report_path()
- [X] T014 [P] {FR-005,FR-016,FR-028} Publish the constants in PKG/config.py — thresholds, four-chain minimum, `HELD_OUT_FRACTION`, `SPLIT_SEED`, day tolerance, monitored set (AD-006)
- [X] T015 {FR-024,FR-025} Declare the forbidden `import-linter` contract from `model.forecast` to `model.llm` and `gateway`, indirect detection on, in src/model/pyproject.toml
- [X] T016 {FR-001} Read `purchase_order_line` and `lifecycle_event` over the connection in PKG/read.py — never a re-derived copy → exports: read_lines_and_events()
- [X] T017 {FR-009} **RED** Failing serializer property tests in TST/test_serialize_properties.py — the digest is invariant to row order and `created_at`, and moves on any serialized value
- [X] T018 {FR-009,FR-014} **GREEN** Canonical row serialization and digests in PKG/serialize.py, reusing `roster.reader` → exports: input_data_hash(), split_assignment_hash() after:T017
- [X] T019 [P] {FR-003,FR-004} **RED** Failing censoring property tests in TST/test_censoring_properties.py — censored iff no terminal event at the as-of date; elapsed; monotone in as-of
- [X] T020 {FR-003,FR-004} **GREEN** Implement the indicator and elapsed time at the as-of date in PKG/censoring.py → exports: censoring_indicator(), elapsed_days() after:T019
- [X] T021 {FR-005,FR-007} **RED** Failing split property tests in TST/test_split_properties.py — one side per line, ordinal contiguous from 1, both strata within one line, order-invariance
- [X] T022 {FR-005,FR-007} **GREEN** Stratified split, canonical ordinal and assignment hash in PKG/split.py ← T020:censoring_indicator → exports: assign_split() after:T021
- [X] T023 {FR-003} **RED** Failing likelihood property tests in TST/test_likelihood_properties.py — `log S(t)` for a censored row and `log f(t)` for a completed one, never interchanged
- [X] T024 {FR-003} **GREEN** Implement the per-row log-contribution as a pure NumPy function in PKG/likelihood.py → exports: log_contribution() after:T023

---

## Phase 3: US1 - Forecast Every Open Line (Priority: P1) 🎯 MVP

**Independent test**: fit against the committed dataset at the recorded as-of date and confirm every open line carries a stored posterior whose draws and survival array agree, joinable to exactly one run.

- [X] T025 [US1] {FR-010,FR-011,FR-029} **RED** Failing posterior property tests in TST/test_posterior_properties.py — DV-004's grid identity, DV-003 by a second path, monotone conditioning
- [X] T026 [US1] {FR-010,FR-011,FR-029} **GREEN** Inverse-CDF conditioning, sort, grid and residual in PKG/posterior.py → exports: conditional_remaining_draws(), survival_grid() after:T025
- [X] T027 [US1] {FR-001,FR-002} Build the PyMC graph — one lognormal per transition, the vendor and category hierarchy, the rework sub-model — in PKG/model.py ← T024:log_contribution after:T115
- [X] T028 [US1] {FR-003} [COMPLETES FR-003] The graph's `logp` agrees with `likelihood.py` to floating tolerance over extreme σ, τ and a one-row vendor — TST/test_model_logp.py
- [X] T029 [US1] {FR-016} Seeded sampling at 4 chains × 1,000 draws with 1,000 tuning draws in PKG/sample.py, fitting the `train` side only
- [X] T030 [P] [US1] {FR-019} **RED** Failing shrinkage property tests in TST/test_shrinkage_properties.py — ρ monotone in nⱼ, triple ordered inside `[0,1]`, interval widens as nⱼ falls
- [X] T031 [US1] {FR-019} **GREEN** ρⱼ = τ²/(τ² + σ²/nⱼ) as a median with an HPDI per vendor in PKG/shrinkage.py → exports: vendor_shrinkage() after:T030
- [X] T032 [US1] {FR-002,FR-006,FR-009,FR-043,FR-044} [COMPLETES FR-009] Assemble the manifest in PKG/manifest.py — every SC-020 field, both digests, both SC-022 labels → exports: build_manifest()
- [X] T033 [US1] {FR-008,FR-013,FR-034} Transaction 1 in PKG/write.py — run row, split assignments, `line_posterior`; both arrays one row ← T032:build_manifest → exports: write_artifact_set()
- [X] T034 [US1] {FR-015} Transaction 2 in PKG/write.py — clear the active flag, then set it on this run; explicit, never implied by recency
- [X] T035 [US1] {FR-039} Wire `forecast-fit` in PKG/fit.py — read, censor, split, sample, condition, write, publish — and print **one** stdout line, the `run_id` ← T033:write_artifact_set
- [X] T036 [US1] {FR-027,FR-031,FR-040} Render the run report as a **closed schema** in PKG/report.py — layer label, datasheet reference, `L-1`–`L-4` four-part with the computed maximum (G-10)
- [X] T037 [US1] {FR-019,FR-020} Report each vendor's shrinkage weight beside its training observation count and a per-vendor supported/not-supported verdict, in PKG/report.py
- [X] T038 [US1] {FR-008} DV-001 / SC-001 — one `line_posterior` row per open line and `open_line_count` equal to that count — TST/test_open_population.py after:T035
- [X] T039 [US1] {FR-010,FR-011} DV-003 and DV-004 over stored `line_posterior` rows — SC-002 and SC-003 for the open population — TST/test_stored_arrays.py after:T035
- [X] T040 [US1] {FR-029} DV-005 / SC-027 — the longest-elapsed decile's median draw is no smaller than the shortest's, and no `survival[1]` below the floor — TST/test_conditioning.py
- [X] T041 [US1] {FR-002} DV-036 / SC-006 — `covariate_names` equals the design matrix's covariate set, over the input frame — TST/test_covariates.py
- [X] T042 [US1] {FR-019} DV-009 / SC-004 — exactly the twelve roster `vendor_id`s including a vendor with no training line, each triple ordered — TST/test_shrinkage_membership.py
- [X] T043 [US1] {FR-019} [COMPLETES FR-019] **NC-11** / DV-010 / SC-005 — a **strict** comparison between the two extremes, not a threshold — TST/test_shrinkage_properties.py after:T031
- [X] T044 [US1] {FR-030} DV-014 / SC-028 — every run this test emits carries the shape in `schema_constants` **read over the connection** (HINT-005) — TST/test_run_shape.py
- [X] T045 [US1] {FR-030} **NC-10** — a run emitted at 5 draws over a 3-day horizon **fails** the assertion, the shape E003's suite legally passes — TST/test_run_shape.py
- [X] T046 [US1] {FR-034} DV-032 / SC-031 — the grant read from `information_schema`, and **no `UPDATE`** anywhere in `model.forecast` — TST/test_artifact_immutability.py
- [X] T047 [US1] {FR-013,FR-015} SC-020 and SC-023 — every manifest field present, the pointer set explicitly, and the write order asserted — TST/test_write_order.py after:T035

---

## Phase 4: US2 - Account for Orders That Have Not Finished (Priority: P1) 🎯 MVP

**Independent test**: fit the same input twice, once with the censoring contribution and once without, and compare the aggregate median forecast over open lines against the independently derived floor.

- [X] T048 [US2] {FR-004} [COMPLETES FR-004] DV-029 / SC-007 — stored `is_censored` agrees with `lifecycle_event` by an independent path — TST/test_censoring_stored.py after:T035
- [X] T049 [P] [US2] {FR-033} **RED** Failing ablation property tests in TST/test_ablation_properties.py — the KM floor uses the training split alone and no held-out row moves it
- [X] T050 [US2] {FR-033} **GREEN** Kaplan–Meier floor against the naive completed-duration mean, and the realized delta, in PKG/ablation.py (AD-008) → exports: kaplan_meier_floor() after:T049
- [X] T051 [US2] {FR-033} Run the censoring-ignoring comparator over repeated seeds for the delta's interval in PKG/ablation.py — an **ablation comparator, never a baseline** after:T118
- [X] T052 [US2] {FR-033,FR-038} DV-020's report half — the ablation entry carries the delta, its interval, the derived floor **and a met-or-missed verdict**, in PKG/report.py
- [X] T053 [US2] {FR-033} **NC-6** — two cases: the censoring-ignoring fit's median is **shorter**, and a no-censoring input gives a delta at zero — TST/test_ablation_controls.py
- [X] T054 [US2] {FR-033} SC-008 — the delta sits at or above the floor, or is published as a four-part shortfall; never a single-seed pass — TST/test_ablation.py

---

## Phase 5: US3 - Hold Out Data So the Forecast Can Be Graded (Priority: P1) 🎯 MVP

**Independent test**: confirm the stored split covers every line exactly once, is hashable, that no held-out line entered the design matrix, and that held-out delivered lines carry order-date-anchored predictions. **No integration task in this phase is `[P]`** — each drives the one live PostgreSQL on `${PRC_DB_PORT:-5434}`.

- [X] T055 [US3] {FR-008,FR-029} Draw **total** durations from each held-out line's own order date in PKG/posterior.py — no conditioning, same grid and residual path after:T026,T117
- [X] T056 [US3] {FR-012,FR-034} Insert `held_out_prediction` inside transaction 1 in PKG/write.py — anchor, delivered flag, both label columns, insert-only
- [X] T057 [US3] {FR-012} DV-002 / SC-013 — one prediction row per held-out delivered line and none for any other, each joinable to one run — TST/test_held_out_population.py
- [X] T058 [US3] {FR-012} DV-023 — a positive control that `fk_held_out_prediction__line_anchor` is present, so a dropped FK fails — TST/test_anchor_control.py
- [X] T059 [US3] {FR-012} [COMPLETES FR-012] **NC-5** / SC-002 — a planted row whose `anchor_date` differs from its line's `order_date` is rejected by the FK — TST/test_anchor_control.py
- [X] T060 [US3] {FR-010} DV-003 and DV-004 over stored `held_out_prediction` rows — the store no delivered constraint reaches — TST/test_stored_arrays.py
- [X] T061 [US3] {FR-011} [COMPLETES FR-011] DV-027 — pair `pg_constraint` definitions and assert all seven array invariants on both stores — TST/test_array_parity.py
- [X] T062 [US3] {FR-029} DV-040 — the stored held-out median lands inside a pre-published band around the training KM median — TST/test_held_out_semantic.py after:T050
- [X] T063 [US3] {FR-005} DV-006 / SC-009 — one row per line per run against `purchase_order_line`, ordinal contiguous from 1 in the canonical order — TST/test_split_completeness.py
- [X] T064 [US3] {FR-005} DV-007 / SC-010 — both strata on both sides, each realized proportion within one line of the declared fraction — TST/test_split_properties.py after:T022
- [X] T065 [US3] {FR-007} [COMPLETES FR-007] DV-008 / SC-011 — no held-out `po_line_id` in the fit's design matrix, recorded as a proxy — TST/test_training_isolation.py after:T029
- [X] T066 [US3] {FR-006} DV-028 / SC-012 — all four run-row scalars agree with their child rows, each a single SQL comparison — TST/test_run_scalars.py
- [X] T067 [US3] {FR-008} [COMPLETES FR-008] DV-030 — the three populations exhaustive and disjoint; no line holds rows in both stores (G-5) — TST/test_population_disjointness.py
- [X] T068 [US3] {FR-044} DV-031 — `artifact_hash` recomputed in `(population_rank, canonical_ordinal)` order reproduces the value — TST/test_artifact_hash.py
- [X] T069 [US3] {FR-005} [COMPLETES FR-005] **NC-13** / AD-011 — a re-run gives the **same** `split_assignment_hash`, one mutated row a **different** one — TST/test_split_determinism.py
- [X] T070 [US3] {FR-028} DV-025 / SC-025 — the realized event count published **with** a statement of whether it supports the band's precision (L-3) — TST/test_report_event_count.py after:T036
- [X] T071 [US3] {FR-020,FR-027,FR-031} DV-024, DV-037 / SC-024, SC-029 — four parts on each limitation, the observation count stated, `L-1`–`L-4` present by identity — TST/test_limitations.py
- [X] T072 [US3] {FR-027} [COMPLETES FR-027] **NC-8** — a deliberately three-part limitation record **fails** the checker — TST/test_limitation_controls.py after:T071
- [X] T073 [US3] {FR-040} DV-041 / SC-035 — the emitted set is an **equality** against FR-040's three files, every report field in its declared schema — TST/test_emitted_set.py after:T098
- [X] T074 [US3] {FR-040} **NC-21** — a planted fourth file fails the equality and a planted unlisted field fails schema validation — TST/test_emitted_set_controls.py
- [X] T075 [US3] {FR-026} DV-021 / SC-026 — no emitted artifact carries a threshold or verdict, by the **closed-schema predicate**, never a term search — TST/test_no_verdict.py
- [X] T076 [US3] {FR-026} **NC-7** — a planted artifact containing a coverage threshold **fails** the absence check — TST/test_no_verdict_controls.py after:T075
- [X] T077 [US3] {FR-028} [COMPLETES FR-028] SC-032 — no flag or env var overrides `HELD_OUT_FRACTION` or `SPLIT_SEED`; pre-registration uncovered (G-11) — TST/test_pre_registration.py after:T014

---

## Phase 6: US4 - Refuse a Fit That Did Not Converge (Priority: P1) 🎯 MVP

**Independent test**: force a non-converging configuration and confirm nothing was written in any of the five stores, the pointer is unmoved, and the refusal left a durable record. **No task in this phase is `[P]`.**

- [X] T078 [US4] {FR-016} **RED** Failing diagnostics property tests in TST/test_diagnostics_properties.py — a breach never yields `passed`, in **both** directions; at threshold; `NaN`
- [X] T079 [US4] {FR-016,FR-018} **GREEN** Threshold comparisons, direction per metric and the pass verdict in PKG/diagnostics.py → exports: evaluate_diagnostics() after:T078
- [X] T080 [US4] {FR-016,FR-018} Insert `forecast_diagnostic` rows inside transaction 1 in PKG/write.py — three parameter-scope rows per monitored parameter, three run-scope rows
- [X] T081 [US4] {FR-021,FR-035} Evaluate the pre-sampling preconditions first in PKG/fit.py — **chain count and no open line**, each naming the precondition and its value. **Schema head is excluded**: this line originally named three, but only two carry a requirement tag, and the third is recorded as deliberately unimplemented in plan.md § Error Handling Strategy
- [X] T082 [US4] {FR-017,FR-038} Gate after sampling, **before the first statement**, in PKG/fit.py — **every** breach with parameter, value, threshold and direction, on stderr
- [X] T083 [US4] {FR-017,FR-037,FR-040} Emit one refusal report **per attempt** in PKG/report.py — a refused-attempt identifier, never overwritten, wall-clock and shape or nothing-sampled
- [X] T084 [US4] {FR-016} DV-011 / SC-016 — three rows per monitored parameter with no partial coverage, and exactly three run-scope rows — TST/test_diagnostics_completeness.py
- [X] T085 [US4] {FR-018} [COMPLETES FR-018] DV-012 — every stored blocking row passed, and treedepth is the only non-blocking row — TST/test_diagnostics_facts.py
- [X] T086 [US4] {FR-016,FR-035} [COMPLETES FR-016] DV-035 — every run this test emits records `chain_count` at the four-chain minimum — TST/test_run_shape.py after:T084
- [X] T087 [US4] {FR-017} [COMPLETES FR-017] **NC-1** / DV-013 / SC-014 / SC-015 — a forced non-converging run leaves five tables and the pointer as found — TST/test_refusal_guarantee.py
- [X] T088 [US4] {FR-021} **NC-2** / SC-017 — an as-of date past every terminal event refuses rather than writing `open_line_count = 0` — TST/test_refusal_controls.py
- [X] T089 [US4] {FR-035} [COMPLETES FR-035] **NC-14** — below four chains, a non-zero exit naming the precondition **with nothing sampled** — TST/test_refusal_controls.py
- [X] T090 [US4] {FR-006} [COMPLETES FR-006] **NC-16** — three planted cases: a split row removed, an `ess_tail` row omitted, a vendor absent — TST/test_completeness_controls.py
- [X] T091 [US4] {FR-036} [COMPLETES FR-036] **NC-18** — `0300` against a **populated** `forecast_run` raises the migration's named error — TST/test_migration_guard.py after:T012
- [X] T092 [US4] {FR-037} DV-038 / SC-033 — the refusal file exists **and** carries the stream's whole field set, post- and pre-sampling — TST/test_refusal_report.py after:T087
- [X] T093 [US4] {FR-037} [COMPLETES FR-037] **NC-19** — two directions: no report file at all fails, and a report omitting a threshold or direction fails — TST/test_refusal_report_controls.py

---

## Phase 7: US5 - Reproduce a Published Forecast (Priority: P2)

**Separable from P1: the manifest and both artifact stores ship in US1 and US3, and nothing in US1–US4 depends on this phase. T096–T100 share one file and are strictly sequential.**

- [X] T094 [P] [US5] {FR-022} **RED** Failing compare property tests in TST/test_compare_properties.py — nearest rank is `draws[ceil(p·n)]` 1-indexed; `p·n` integral; one outlier fails all
- [X] T095 [US5] {FR-022} **GREEN** Nearest-rank percentile lookup and the day-tolerance comparison in PKG/compare.py → exports: nearest_rank_percentile(), within_tolerance() after:T094
- [X] T096 [US5] {FR-022} Implement `forecast-reproduce` in PKG/reproduce.py — re-fit from a manifest and pair every stored line across **both** stores ← T095:nearest_rank_percentile
- [X] T097 [US5] {FR-022} Apply the 5.0-day tolerance to each median and P80 in PKG/reproduce.py, resolving **three** outcomes — the third a predictive ESS below half the draws (AD-004)
- [X] T098 [US5] {FR-022,FR-040} [COMPLETES FR-040] Emit the reproduction report in PKG/reproduce.py — FR-038's unit per line and **both** `run_id`s, so the verdict names its operands
- [X] T099 [US5] {FR-023} Refuse before sampling on a moved `input_data_hash` or `split_assignment_hash` in PKG/reproduce.py, naming which moved and both values (DV-015, DV-017 / SC-019)
- [X] T100 [US5] {FR-023} A moved `input_fixture_digest` against an unchanged row hash **warns and completes with a zero exit** in PKG/reproduce.py (DV-016), never refuses
- [X] T101 [US5] {FR-023} **NC-3** — two cases, a mutated row and a mutated split assignment, each naming *that* input — TST/test_reproduce_refusals.py after:T099
- [X] T102 [US5] {FR-023} [COMPLETES FR-023] **NC-4** — a mutated fixture against unchanged rows warns **and the run completes** — TST/test_provenance_warning.py after:T100
- [X] T103 [US5] {FR-022} DV-018 / SC-018 — per-line agreement in both stores within the tolerance and **exact** provenance equality — TST/test_reproduction.py after:T098
- [X] T104 [US5] {FR-022} [COMPLETES FR-022] **NC-17** — one line's P80 perturbed beyond the tolerance makes the harness exit non-zero naming it — TST/test_reproduction_controls.py
- [X] T105 [US5] {FR-032} DV-019 / SC-030 — a digest mismatch under a version outside the **whole** recorded pin is a scope limit, not a failure — TST/test_pin_scope.py after:T098
- [X] T106 [US5] {FR-032} **NC-9** — an **injected** version outside the pin reports a scope limit; the same mismatch inside the pin fails — TST/test_pin_scope_controls.py
- [X] T107 [US5] {FR-025} DV-022 / SC-021 — `/src/api` has no import path to `model.forecast`, over the transitive graph — CHK/test_dependency_isolation.py after:T015
- [X] T108 [US5] {FR-024} **NC-15** — two planted imports to `gateway`, one direct and one indirect, each fail the contract (G-17 bounds the rest) — TST/test_import_contract_controls.py

---

## Phase 8: Polish & Cross-Cutting Concerns

- [X] T109 {FR-038} DV-039 / SC-034 — every measure with a criterion appears as measure, value, criterion **with direction** and verdict; wall-clock none — TST/test_reportable_unit.py after:T098
- [X] T110 {FR-038} **NC-20** — two planted entries fail: a value with no criterion, and a verdict against no criterion — TST/test_reportable_unit_controls.py
- [X] T111 [P] Verify `model.forecast` is counted in the root combined coverage report rather than landing in the denominator uncounted — **`.github/workflows/verify.yml:307` carries an inline `--source=src/model/roster,schema,corpus,procurement` that overrides the root `pyproject.toml` list T001 extended, so `forecast` is uncounted in CI while `tests/forecast` still runs**; add it there too, or the two halves of T001 buy nothing on the only run that gates — .github/workflows/verify.yml after:T001
- [X] T112 [P] Confirm the model entry declares **no new dependency** for `model.forecast` and keep CHK/test_dependency_isolation.py green after:T002
- [X] T113 Add the release gate — emitted-set equality, import contract, migration-range remediation, reportable-unit checker — to .github/workflows/verify.yml after:T110

---

## Appended After Analysis (pass A)

Task IDs are never reused or renumbered, so the tenth red-green pair appends here rather than being inserted into Phase 2. **It is Foundational work and must run before T027** — the dependency edge, not the position in this file, is what orders it.

- [X] T114 {FR-001,FR-002} **RED** Property tests for the vendor and material-category index mapping and the pooling structure in TST/test_design_properties.py — a swapped vendor index must be detected, and the mapping must be a pure function of the roster order — A-002
- [X] T115 [COMPLETES FR-002] {FR-001,FR-002} **GREEN** Extract the index mapping and design-matrix construction from PKG/model.py into PKG/design.py after:T114 → exports: vendor_index(), category_index(), design_matrix()
- [X] T116 [COMPLETES FR-001] {FR-001} Rework T028 so the `logp` agreement oracle builds `likelihood.py`'s inputs from PKG/design.py rather than from the assembled graph — TST/test_model_logp.py after:T115
- [X] T117 [US1] {FR-010,FR-029} **RED** Property test for the held-out **total-duration** path in PKG/posterior.py, absent from T025's set — TST/test_posterior_properties.py — A-015
- [X] T118 [COMPLETES FR-033] [US2] {FR-033} **RED** Property test for the repeated-seed delta interval in PKG/ablation.py, absent from T049's set — TST/test_ablation_properties.py — A-015
- [X] T119 [COMPLETES FR-038] {FR-038} Assert SC-034's reportable unit over the **run** report as well, so a P1-only cut still evidences it — TST/test_reportable_unit.py — A-016
- [X] T120 [COMPLETES FR-034] {FR-034} **NC-22** — a planted `UPDATE` in a `model.forecast` module must fail the absence check; an absence check with no planted positive is green when it greps nothing — TST/test_no_update_statements.py after:T046
- [X] T121 [COMPLETES FR-031] {FR-031} **NC-23** — a limitation set of four well-formed records that omits **L-2** must fail; NC-8 plants a three-part record and so exercises form, not presence-by-identity — TST/test_limitation_presence.py after:T072
- [X] T122 {FR-039} **DV-042 / SC-038 / NC-24** — stdout carries exactly the `run_id` and nothing on refusal; every diagnostic on stderr; exit zero exactly on completion — TST/test_streams_and_exit.py after:T035
- [X] T123 [COMPLETES FR-045] {FR-045} **DV-043 / SC-040** — the layer label and datasheet reference reach the reader-facing artifact, not only the manifest row — TST/test_report_provenance.py after:T068
- [X] T124 {FR-041} Any bound this epic judges a stored quantity against is a single module-level literal, published before the draws it judges — DV-040's held-out band is the instance — TST/test_band_preregistration.py after:T077
- [X] T125 {FR-010} [COMPLETES FR-010] Assert the day-grid identity `survival[k] = count(draws > k)/draw_count` over emitted rows in **both** stores — TST/test_grid_identity.py after:T117
- [X] T126 {FR-029} [COMPLETES FR-029] Assert both recorded duration semantics over emitted rows: conditional-remaining for open lines, total-from-order-date for held-out — TST/test_duration_semantics.py after:T117
- [X] T127 {FR-042} [COMPLETES FR-042] The fixture-file digest is recorded beside the row hash as a distinct value, and DV-016 proves the mismatch **warns** where DV-015 refuses — the two dispositions separately evidenced — TST/test_fixture_digest.py after:T018

**A-015 — and the pair rule reaches them by this sentence, not by a label.** T055 and T051 add behaviour to property-tier modules outside any of the ten pairs. T117 and T118 are their RED halves and T055 and T051 are the corresponding GREEN halves, **so the `test:`-before-`feat:` commit ordering binds those two couples exactly as it binds the ten** — they are recorded here rather than in the table because the table is keyed by module and both modules already appear in it. T117 and T118 supply the missing RED halves; T055 gains `after:T117` and T051 gains `after:T118` below.

**T124's scope was corrected during implementation.** Its line named SC-036 and SC-037, which are US4 criteria already carried by T089 and T091 against machinery US3 does not have. T124 owns FR-041 only.

**A-016**: the P1-viable claim had two counterexamples. SC-034 is tagged `[US1]` but was asserted only by T109/T110 in Phase 8 — T119 fixes that. SC-035's equality ranges over the reproduction report emitted by T098 in Phase 7 (P2); T073 now carries `after:T098`, and the P1 boundary note below records that SC-035 is **P1-scoped to the kinds a P1 cut emits**.

## Dependencies

Setup → Foundational → US1 → US2 → US3 → US4 → US5 → Polish

- **Foundational gates everything.** T018's serializer feeds the manifest, the split hash and the artifact hash; T020's indicator feeds the split's stratum and the likelihood; T022's split feeds the fit's input frame; T024's likelihood feeds the graph. The migrations gate every integration task, because T012's fixture requires head `0303`.
- **T003 → T004 → T005 land as one change.** `0300` applying without T005 turns E003's delivered `SCH/test_forecast.py` red in three places at once — two explicit-column `INSERT` constants *and* the `FIXTURE_RUN` mapping, all three omitting the fourteen columns (G-2, HINT-002). T003 must precede both, because **both** hard-coded block tables report `0300`–`0303` as outside every declared block until it lands, and **doing AD-007 part (a) alone turns one red assertion into two** (HINT-003). Grep for `DECLARED_BLOCKS`, not for the filename.
- **`0302` is one revision, not two.** `uq_purchase_order_line__order_anchor` must exist before `fk_held_out_prediction__line_anchor` targets it; splitting them leaves a revision that cannot be applied alone (HINT-001).
- **`0300`'s `down_revision` is the head at landing**, not the literal `0103`. If E008 or E009 lands first, re-parent — never renumber. DV-033 is the covering assertion and T011 verifies it stayed green.
- **US1 gates US2, US3 and US4.** Nothing has a run to inspect until T035 emits one: T048→T035, T063→T056→T055→T026, T084→T083→T082→T081→T080.
- **Transaction 1 is built by three tasks on one file, in phase order**: T033 (`forecast_run`, `forecast_split_assignment`, `line_posterior`) → T056 (`held_out_prediction`) → T080 (`forecast_diagnostic`). It is complete only after T080, which is why DV-013's five-store snapshot (T087) sits at the end of US4 rather than earlier.
- **The gate is inserted, not retrofitted.** T035 wires the pipeline; T081 and T082 insert the pre-sampling preconditions and the post-sampling gate ahead of the first statement. The refusal guarantee is achieved by **ordering**, not by rollback — no statement is issued on a refusing path, so there is nothing to roll back (AD-010). T034's pointer flip is transaction 2 and runs only after transaction 1 commits.
- **Hints carried by tasks whose line had no room**: T026 implements HINT-004 (draw by inverse-CDF, never by rejection and never by re-basing — the re-based alternative passes `ck_line_posterior__draws_non_negative` and every other delivered constraint, so nothing downstream catches it); T044 implements HINT-005; T004 implements HINT-001's guard and T007 its one-revision rule; T003 implements HINT-003 and T005 HINT-002.
- **Live database required**: T010–T012, T038–T048, T056–T070, T080–T093 and T096–T107 need PostgreSQL on `${PRC_DB_PORT:-5434}` through T012's fixture. **None is `[P]`.** **Never run `CHK/test_orchestration.py`** — its teardown destroys the volume.
- **Same-file sequential runs** (never `[P]` together): T026→T055 (`posterior.py`); T033→T034→T056→T080 (`write.py`); T035→T081→T082 (`fit.py`); T036→T037→T052→T083 (`report.py`); T050→T051 (`ablation.py`); T096→T097→T098→T099→T100 (`reproduce.py`); T044→T045→T086 (`test_run_shape.py`); T039→T060 (`test_stored_arrays.py`); T058→T059, T073→T074, T075→T076, T088→T089, T092→T093, T099→T101, T109→T110 (one test file each).
- **Cross-phase edges**: T043→T031, T048→T035, T055→T026, T062→T050, T064→T022, T065→T029, T070→T036, T077→T014, T091→T012, T107→T015, T109→T098, T111→T001, T112→T002.
- Tasks marked `[P]` touch distinct files and carry no `after:T###` or `← T###:` edge to another task in the same batch. The implementing agent verifies the referenced task is `[X]` before executing.

### Mandatory red-green pairs

`plan.md` § The test-first observable requires this list and this is it. **Ten** modules, ten ordered pairs. The test task must be observed **failing** before its implementation task begins, and the branch history must carry a `test:` commit before the `feat:` commit for each pair. **E005 closed at six of seven because two tasks landed in one commit** — the list exists so that is a checkable miss rather than an invisible one.

| # | Module | RED (property test) | GREEN (implementation) | Phase |
|---|---|---|---|---|
| 1 | `serialize.py` | **T017** | **T018** | Foundational |
| 2 | `censoring.py` | **T019** | **T020** | Foundational |
| 3 | `split.py` | **T021** | **T022** | Foundational |
| 4 | `likelihood.py` | **T023** | **T024** | Foundational |
| 5 | `posterior.py` | **T025** | **T026** | US1 |
| 6 | `shrinkage.py` | **T030** | **T031** | US1 |
| 7 | `ablation.py` | **T049** | **T050** | US2 |
| 8 | `diagnostics.py` | **T078** | **T079** | US4 |
| 9 | `compare.py` | **T094** | **T095** | US5 |
| 10 | `design.py` | **T114** | **T115** | Foundational *(appended; must precede T027)* |

`model.py`, `sample.py`, `read.py`, `write.py`, `manifest.py`, `report.py`, `config.py`, `paths.py`, `fit.py` and `reproduce.py` are test-after under the admission rule — but `model.py`'s `logp` is asserted against `likelihood.py` at **T028**, which is the only check that reaches the PyMC graph at all.

### Negative controls, one task each

`plan.md` § Negative Controls derives this set by applying the admission rule to every `SC-###` and every `DV-###`; a claim outside the three exclusion classes with no entry is a miss. Several controls carry two or three cases and the cases are not separable — one direction cannot stand for the other.

| NC | Task | Cases |
|---|---|---|
| NC-1 | T087 | 1 — all five tables and the pointer snapshotted |
| NC-2 | T088 | 1 |
| NC-3 | T101 | **2** — a mutated row and a mutated split assignment, each naming *that* input |
| NC-4 | T102 | **2** — the warning and the completed run, evidenced separately from the refusal |
| NC-5 | T059 | 1 — rejected by the FK, not by a test |
| NC-6 | T053 | **2** — the shorter median, and a no-censoring input at a zero delta |
| NC-7 | T076 | 1 |
| NC-8 | T072 | 1 |
| NC-9 | T106 | **2** — outside the pin reports a scope limit, inside it fails |
| NC-10 | T045 | 1 |
| NC-11 | T043 | 1 — a strict comparison, not a threshold any width satisfies |
| NC-12 | T011 | **3** — `0400` probes outside, `0200`–`0299` declared-but-unpopulated, part (a) alone red |
| NC-13 | T069 | **2** — the same hash on a re-run, a different hash on one mutated row |
| NC-14 | T089 | 1 — **nothing sampled**, the evidence NC-1 cannot supply |
| NC-15 | T108 | **2** — a direct planted import and an indirect one |
| NC-16 | T090 | **3** — a split row, an `ess_tail` row and a vendor, each removed |
| NC-17 | T104 | 1 |
| NC-18 | T091 | 1 — the guard's whole value is its message, so it must be read once |
| NC-19 | T093 | **2** — no report file at all, and a report missing a threshold or its direction |
| NC-20 | T110 | **2** — a value with no criterion, and a verdict against no criterion |
| NC-21 | T074 | **2** — a planted fourth file, and a planted unlisted field |

### Not tasks, by design

- **The nineteen disclosed gaps `G-1`–`G-19`** are recorded in `data-model.md` as enforcement this design does not carry; recording them *is* the treatment. **G-1 is the sole exception**: its remediation is AD-007, which lands as T003 with T011 as its failing direction.
- **The eight propagation obligations `P-1`–`P-8`** belong to other epics or to registered documents a feature branch may not amend — see § Brownfield Notes. A feature branch records the need and does not perform it, and may not route it to another feature branch.
- **No project-initialization task.** E001 scaffolded the entries and the toolchain, E002 established the package shape, E003 delivered the schema and E005 delivered the data — all fixed input.
- **No checklist-completion task.** All 115 items across data-integrity, testing and observability are evaluated and checked.
- **P1 boundary**: Phases 1–6 (T001–T093) plus the appended Foundational pair T114–T116 are the viable deliverable. Phase 7 (US5, P2) and Phase 8 are omittable, though SC-018, SC-019, SC-021 and SC-030 go unasserted without them. **Two exceptions were found at the Analyze gate (A-016), and an earlier revision of this line claimed no P1 criterion breaks.** *SC-034* is tagged `[US1]` — P1 — yet was asserted only by T109/T110 in Phase 8; **T119** adds a P1-side assertion over the run report. *SC-035* asserts a closed-kind equality over a set including the reproduction report, which T098 emits in Phase 7; under a P1-only cut the equality ranges over the kinds a P1 cut actually emits, and T073 now carries `after:T098` so the full-scope form is ordered correctly whenever Phase 7 is present.

## Validation Performed Before Write

| Check | Result |
|---|---|
| Task IDs contiguous from T001 | **127** — T001–T127, no gap and no withdrawal. T114–T127 were appended at the Analyze gate; an earlier revision of this row still said 113 |
| Every FR carries at least one `{FR-###}` tag | **40 / 40** (FR-001…FR-040) |
| Every SC reachable from at least one task | **35 / 35** (SC-001…SC-035). SC-032's prohibition half is reachable only by commit-history review (G-11) and T077 says so rather than claiming a check; SC-022's tie-breaking half is argued (G-13) |
| `[COMPLETES FR-###]` on the last task of every requirement spanning 3+ tasks | **28 markers**, no task carrying two |
| Every DV rule implemented or asserted, at the tier `data-model.md` assigns it | **41 / 41** (DV-001…DV-041, including the appended DV-040 and DV-041) |
| Every negative control lands as a task | **21 / 21** (NC-1…NC-21), with the multi-case controls enumerated above |
| Mandatory red-green pairs present and correctly ordered | **10 / 10** named, each RED immediately preceding its GREEN. The evidence is a `test:` commit before the `feat:` commit per pair, verified at implementation — it is **not** asserted here, because a list that claimed it in advance is the miss E005 recorded |
| AD-007 emitted as one task across both files | **yes** — T003. Splitting it is what HINT-003 forbids, and part (f) alone leaves the chain unverified |
| `0300` and the G-2 fixture extension adjacent and undivided | **yes** — T004 → T005, one change, one phase |
| `0302` creates the unique key and the table in one revision | **yes** — T007 |
| Coverage emitted as two edits | **yes** — T001 names the `source` list **and** the `[tool.coverage.paths]` entry |
| Delivery tasks carry a `[US#]` label | **84 / 84** (T025–T108); Setup, Foundational and Polish carry none, as required |
| No orphan `after:` reference; graph acyclic | **0 orphans**, and no cycle. **But the "lower ID" invariant no longer holds and an earlier revision of this row asserted it.** The Analyze gate appended T114–T126, and four of their edges run from a lower-numbered task to a higher one — `T027→T115`, `T051→T118`, `T055→T117`, `T073→T098`. Task IDs are never reused or renumbered, so Foundational work discovered after decomposition necessarily lands at a high number. **Execution order is the dependency graph, not the ID order**, and the graph was verified acyclic by traversal rather than assumed acyclic by construction |
| No `[P]` pair sharing a file; no `[P]` batch containing a task and its dependency | **0 violations after correction.** An earlier revision of this table asserted zero while T043 carried `[P]` against two of its own rules — it shares `TST/test_shrinkage_properties.py` with T030, and it sits inside the T038–T048 live-database range this document declares to hold no `[P]`. The marker is removed. The claim was false in the same breath as the rule it was claiming to satisfy, which is the failure mode a self-reported validation table is most prone to |
| Every `← T###:Symbol` has a matching `→ exports:` on T### | **5 / 5** — T022←T020, T027←T024, T033←T032, T035←T033, T096←T095 |
| No task line exceeds 200 characters | **pass** — measured, not asserted |
| Migration tasks emitted | **4** — `0300`–`0303` inside the claimed block, each `downgrade()` raising |
| Tasks for `G-1`–`G-19` other than G-1 | **0**, by design — a disclosed gap is a record, not work |
| Tasks for `P-1`–`P-8` | **0**, by design — each belongs to another epic or to a registered document |

## QC Bug Tasks — iteration 1

Appended by `/sddp-qc` 2026-07-28. Numbering continues from T127; no existing ID is reused or renumbered.

- [ ] T128 [BUG:ERROR] {FR-030} [test-failure] E007 moved the chain head to 0303 and gateway's ledger test still asserts 0103 — src/gateway/tests/test_migrations.py:242
- [ ] T129 [BUG:ERROR] {FR-019} [requirement-gap] SC-005's vendor-effect interval is computed only in the test; implement sd(theta_j|data) in shrinkage.py and drive the comparison from the run's own fitted tau/sigma — src/model/src/model/forecast/shrinkage.py:24
- [ ] T130 [BUG:ERROR] {FR-019} [requirement-gap] SC-005's operands must be counted from the run's forecast_split_assignment train rows, not hard-coded at 5 and 35 — src/model/tests/forecast/test_shrinkage_properties.py:90 after:T129
- [ ] T131 [BUG:WARNING] {FR-027} [requirement-gap] L-5 is declared under AD-013 but never emitted; add it to LIMITATION_IDENTIFIERS and widen DV-037's scope — src/model/src/model/forecast/report.py:220
- [ ] T132 [BUG:WARNING] {FR-022} [coverage-gap] SC-018's outside-basis outcome has no covering test in either direction; construct a below-half predictive-ESS comparison — src/model/tests/forecast/test_reproduction.py:89
- [ ] T133 [BUG:WARNING] {FR-041} [requirement-gap] Traceability stops at SC-035; SC-039, SC-041 and SC-042 carry no task tag and SC-041/SC-042 are named by no test — specs/00007-delivery-forecast-model/tasks.md:333

**B-3 is not a separate task**: DV-010's declared tier disagrees with its delivered tier only because the quantity is unimplemented. T129 closes it.
