# Tasks: Synthetic Procurement History

**Input**: Design documents from `specs/00005-synthetic-procurement-history/`
**Prerequisites**: `plan.md`, `spec.md`, `data-model.md`, `research.md`, `checklists/` (data-integrity, testing — both evaluated 40/40, so no checklist-completion task appears here)

**Tests**: Included, and for six modules **test-first is mandatory rather than optional**. `plan.md` §Testing Strategy admits `serialize.py`, `allocate.py`, `seeds.py`, `durations.py`, `censor.py` and `criticality.py` to the property tier under a three-clause rule — pure, computes rather than transcribes, wrong is silent. Each is emitted as an ordered **RED → GREEN** pair: the property-test task must be observed failing before its implementation task begins, and the branch history carries a `test:` commit before the `feat:` commit for each pair, checkable before the squash merge. Every other module is test-after.

**Organization**: Grouped by user story (`US#`) per `spec_type: product`. Requirement tags are `FR-###`. `plan.md` §Requirement Coverage Map is the authority for requirement → component → file; `data-model.md` §Enforcement point and test tier is the authority for which tier each `DV-###` rule lands in.

## Project Mode

`Brownfield`

E001 scaffolded the four entries, the `uv` toolchain and the Compose `db` service; E002 established the `model.corpus` package shape and its `generate` / `validate` / `reverify` entry-point split; **E003 delivered `purchase_order_line` and `lifecycle_event`, and they are fixed input**. No generic project-initialization task appears here. `~` paths in `plan.md` §Project Structure extend files that already exist.

## Path shorthand

| Token | Expands to |
|---|---|
| `PKG/` | `src/model/src/model/procurement/` |
| `TST/` | `src/model/tests/procurement/` |
| `DAT/` | `data/procurement/` |
| `GT/` | `data/ground-truth/` |

## Epic / Capability Map

- `[US1]` → Generate the procurement history — allocation, seeds, durations, censoring, criticality, lifecycle, equipment, the ground-truth record, the generator entry point and the committed fixture (P1)
- `[US2]` → Load the history into the delivered schema — staged comparison, two refusals, forced write order, closure at commit (P1)
- `[US3]` → Reproduce the dataset exactly — the hash oracle, input-drift refusal, provenance agreement, the pin scope limit (P1)
- `[US4]` → Audit the dataset from its datasheet — seven sections, the disclosures, ten four-part limitations, no split (P1)
- `[US5]` → Keep known truth out of the model's reach — isolation enumerated from the fitting entry point's own configuration (P2)

## Brownfield Notes

- **No DDL and no migration.** E003 owns both tables and they are delivered; this epic writes rows only. E005's reserved filename block `0200`–`0299` is claimed and expected to go unused, as are decision-record numbers from `0018`. **No task in this list authors a migration**, and a task that would is out of scope rather than missing.
- **Reuse, do not re-author.** `model.roster.reader` (`read_roster`, `canonical_bytes`, `content_hash`), `model.corpus.manifest` (`sha256_of_file`, `sha256_of_bytes`, `DIGEST_PATTERN`), `model.roster.naming` (the real-firm exclusion list), `model.corpus.paths` (path-resolution pattern). AD-001 exists because the canonicalization rule set already lives in the repo **twice** — `roster/reader.py:55` and `corpus/model.py:255`. A third copy is the defect, not the fix.
- **Existing files touched**: `pyproject.toml` (coverage `source` + `paths`), `src/model/pyproject.toml` (three console entry points), `.gitattributes`, `tests/checks/test_dependency_isolation.py`, `.github/workflows/verify.yml`. Nothing under `src/model/src/model/schema/` and no Alembic revision is touched.
- **Per-owner digest conventions (AD-010)**: the roster is hashed by `roster.reader.content_hash` (canonical content), the category map by `corpus.manifest.sha256_of_file` (raw bytes). Recomputing both under one convention yields a false mismatch on one of them — binding on T033, T038, T050 and T052.
- **Gated, and deliberately unimplemented**: **FR-034 and SC-026** require manufacturer and part-number fields E002 does not publish. T077 records the blocked state and implements the unblocking *trigger*. No task attempts to satisfy them, and none may.
- **Regression focus**: the root `coverage combine` must count `model.procurement` (T001, T081 — E003's QC proved an unlisted package is silently uncounted); `tests/checks/test_dependency_isolation.py` must stay green (T082).

## Design constants — quoted, never re-derived

`plan.md` §Design constants and `data-model.md` §Declared Generative Constants solved these. Tasks consume them rather than re-solving them.

| Constant | Value |
|---|---|
| Order-date window / as-of date | `2025-06-16` … `2026-02-16` / `2026-04-01` |
| Per-vendor line vector (`VND-001`…`012`) | 35, 28, 24, 21, 18, 16, 14, 12, 10, 9, 7, 5 — Σ 199 |
| Per-project vector | `PRJ-001`…`004` = 40 each, `PRJ-005` = 39; PO grouping cycle `(1,1,2,1,3,1,1,2,1,1)` |
| σ_w / τ / σ_c / σ_r / σ₀ | 0.51 / 0.1224 / 0.219 / 0.4605 / 0.77 *(calibrated)* |
| Transition shares | 0.12, 0.20, 0.08, 0.46, 0.14 forward; 0.16, 0.12 rework — **five durations across six forward transitions** |
| Category tiers | T1 +0.20 (8 keys), T2 0.00 (8 keys), T3 −0.40 (4 keys), mean-zero at the declared weights |
| Slack | `f ~ Normal(0.15, 0.10)` truncated at 0, **multiplicative** on the line's expected duration (AD-009) |
| Rework | **Declared, not drawn**: `L = round(0.30 x N)` looped lines — 60 at N=199 — split `(42, 13, 5)` across one/two/three loops by largest-remainder apportionment, three-loop stratum protected at five, hard cap 3. Realized must **equal** declared (DV-009), not merely be recorded |
| `NS_E005` | `6a5c9561-8a6b-58f7-8fbd-db51856db549` |

---

## Phase 1: Setup (Repository / Workspace Delta)

- [X] T001 [P] Add `src/model/src/model/procurement` to coverage `source` and a `procurement` path entry in pyproject.toml — an unlisted package lands in the denominator uncounted
- [X] T002 [P] Declare the `procurement-generate`, `procurement-load` and `procurement-validate` console entry points in src/model/pyproject.toml
- [X] T003 [P] Pin `data/procurement/**/*.json` and `data/ground-truth/**/*.json` to `text eol=lf` in .gitattributes

---

## Phase 2: Foundational (Cross-Work-Item Blockers)

**The record types, the path resolution and the canonical serializer gate every delivery phase. `serialize.py` gates the hash oracle and the hash oracle gates the validator, so T007 → T034 → T051 is the spine FR-021 rests on.**

- [X] T004 [P] Create the package docstring and path resolution in PKG/__init__.py and PKG/paths.py following `corpus.paths` → exports: fixture_path(), truth_path()
- [X] T005 [P] {FR-013} Create the closed envelope, line and event record types and the `NS_E005` namespace uuid in PKG/model.py → exports: FixtureEnvelope, FixtureLine, NS_E005
- [X] T006 {FR-013,FR-021} **RED** Failing serializer property tests in TST/test_serialize_properties.py — parse/canonicalize round-trip, key-order and file-layout invariance after:T005
- [X] T007 {FR-013,FR-021} **GREEN** Implement the canonical serializer in PKG/serialize.py — reuse `roster.reader.canonical_bytes`, no JSON float after:T006 → exports: dataset_content_hash()
- [X] T008 {FR-013} Add worked-case serializer unit tests — non-ASCII description, trailing-zero quantity, absent note, CRLF and LF checkouts — in TST/test_serialize.py after:T007
- [X] T009 Create the procurement test fixtures — live PostgreSQL on `${PRC_DB_PORT:-5434}` and tmp artifact roots — in TST/conftest.py, after src/model/tests/schema/conftest.py

---

## Phase 3: US1 - Generate the Procurement History (Priority: P1) 🎯 MVP

**Independent test**: run the generator against the frozen roster at the recorded seed and confirm it emits the fixture, its digest sidecar and the ground-truth record, with line and event counts inside the intended shape.

- [ ] T010 [US1] {FR-004} **RED** Failing allocate property tests in TST/test_allocate_properties.py — exact margins, totals at 190 and 210, the 5-line vendor, the N=200 crossover (DV-002)
- [ ] T011 [US1] {FR-003,FR-004} **GREEN** Implement the declared vendor and project vectors, greedy fill and cyclic PO grouping in PKG/allocate.py after:T010 → exports: allocate_lines()
- [ ] T012 [P] [US1] {FR-003} Assert DV-001 (190–210 lines, 5 projects, 12 vendors from `read_roster().identifiers()`) and DV-003 in TST/test_allocate.py after:T011
- [ ] T013 [P] [US1] {FR-019} **RED** Failing seeds property tests in TST/test_seeds_properties.py — insert or reorder changes no other stream key; the key is pure in the natural key
- [ ] T014 [US1] {FR-019,FR-020} **GREEN** Implement the content-addressed `SeedSequence` spawn-key derivation in PKG/seeds.py after:T013 → exports: line_stream_key(), line_generator()
- [ ] T015 [US1] {FR-007,FR-008,FR-036} **RED** Failing durations property tests in TST/test_durations_properties.py — the four relations in plan §Mandated properties (DV-011, DV-012)
- [ ] T016 [US1] {FR-007,FR-035} **GREEN** Implement the lognormal draws, σ₀/T_pre solves, 1-day floor and vendor/tier offsets in PKG/durations.py after:T015 → exports: draw_line_durations()
- [ ] T017 [US1] {FR-008,FR-036} Implement the vendor/category/residual decomposition and the category-adjusted band check in PKG/durations.py after:T016 → exports: decompose_variance()
- [ ] T018 [P] [US1] {FR-036} [COMPLETES FR-036] **NC-6** — an unadjusted ratio in band whose category-adjusted ratio is outside it must fail, in TST/test_spread_ratio_control.py after:T017
- [ ] T019 [P] [US1] {FR-009} **RED** Failing censor property tests in TST/test_censor_properties.py — no instant past as-of, event 1 equals `order_date`, monotone delivered set (DV-008)
- [ ] T020 [US1] {FR-009,FR-010} **GREEN** Implement the committed window and as-of truncation with DV-010's three floors asserted as one window in PKG/censor.py after:T019
- [ ] T021 [P] [US1] {FR-010} **NC-5** — event-floor, censoring-floor and empty-non-terminal-state breaches all exit non-zero, no artifact written — TST/test_shape_floor_controls.py after:T020
- [ ] T022 [P] [US1] {FR-011,FR-012} **RED** Failing criticality property tests in TST/test_criticality_properties.py — all nine cells, tercile ties at a cut point, zero slack (DV-006, DV-013)
- [ ] T023 [US1] {FR-011,FR-012,FR-035} **GREEN** Implement multiplicative slack, need-by, the pressure terciles and the tier×tercile table in PKG/criticality.py after:T022
- [ ] T024 [US1] {FR-005,FR-006} Implement the legal transition walk and the declared rework allocation (`6+3L` events, new positions) in PKG/lifecycle.py ← T014:line_generator
- [ ] T025 [US1] {FR-005,FR-006} Assert DV-007 (contiguous sequence, first event `submitted`, every pair legal, no position reused) and DV-009 (cap 3, and the realized looped-line count and one/two/three histogram **equal** the declared `L = round(0.30 x N)` and `(42, 13, 5)` — equality, not recording) in TST/test_lifecycle.py after:T024
- [ ] T026 [US1] {FR-031,FR-032,FR-037} **RED** Write the failing property tests for the description grammars, the disjoint manufacturer space, the part number and the four-clause overlap predicate in TST/test_equipment_properties.py — `equipment.py` is the seventh mandatory deterministic-computation module, so its test commit MUST be observed failing first ← T014:line_generator
- [ ] T027 [US1] {FR-031,FR-032,FR-037} **GREEN** Implement the description grammars, the disjoint manufacturer space, the part number and the four-clause predicate in PKG/equipment.py, then assert DV-004 (six non-blank descriptive fields, category a map key), DV-005 (fixed scale 1, UoM domain) and DV-014 in TST/test_equipment.py after:T026
- [ ] T028 [P] [US1] {FR-032} [COMPLETES FR-032] **NC-7** — every complement line fails all four clauses, so the share can fall below 60% — TST/test_overlap_predicate_control.py after:T026
- [ ] T029 [P] [US1] {FR-037} DV-021 — no `manufacturer` enters E001's real-firm exclusion list or matches its vendor convention, via `roster.naming` — TST/test_manufacturer_exclusion.py after:T026
- [ ] T030 [US1] {FR-017} Emit the ground-truth record — σ_w, τ, both ratios, the decomposition and 12 vendor offsets — in PKG/truth.py ← T017:decompose_variance → exports: write_truth_record()
- [ ] T031 [P] [US1] {FR-017} DV-017 — exactly 12 unique `vendor_id`s covering the roster and a `dataset_content_hash` equal to the fixture's — in TST/test_truth_record.py after:T030
- [ ] T032 [P] [US1] {FR-001} Implement `procurement-generate` in PKG/generate.py — identities only via `read_roster()`, plus a source scan asserting no `PRJ-`/`VND-` literal ← T011:allocate_lines
- [ ] T033 [US1] {FR-002,FR-009,FR-015,FR-027} [COMPLETES FR-009] Assemble the closed 13-key envelope in PKG/generate.py — both `generation_inputs` carry their own `digest_kind` after:T032
- [ ] T034 [US1] {FR-010,FR-013,FR-021} [COMPLETES FR-010] Wire the fail-fast shape gate and the write path (fixture, sidecar, truth record) in PKG/generate.py ← T007:dataset_content_hash after:T033
- [ ] T035 [P] [US1] {FR-020} DV-023 — both stated total orders hold, two runs at one seed are byte-identical, no hash-ordered iteration in the write path — TST/test_generate_ordering.py after:T034
- [ ] T036 [P] [US1] {FR-031} [COMPLETES FR-031] End-to-end test — all three artifacts emitted, six columns non-blank, `note` absent (DV-022) — TST/test_generate.py after:T034
- [ ] T037 [US1] {FR-013,FR-017} [COMPLETES FR-013] Run `procurement-generate` and commit DAT/procurement-history.json, DAT/procurement-history.hash.json and GT/vendor-offsets.json after:T036

---

## Phase 4: US2 - Load the History into the Schema (Priority: P1) 🎯 MVP

**Independent test**: load the committed fixture into an empty migrated database, load it again, and confirm both row counts and row contents are unchanged. **No task in this phase is `[P]`** — every integration task drives the one live PostgreSQL on `${PRC_DB_PORT:-5434}`, and the file-level `[P]` rule does not model a shared resource.

- [ ] T038 [US2] {FR-023,FR-027} Implement the load pre-flight in PKG/load.py — one `REPEATABLE READ` txn, `SET LOCAL TimeZone='UTC'`, both digests rechecked, `COPY` into `TEMP` staging after:T037
- [ ] T039 [US2] {FR-002,FR-023,FR-029} Compute the load-derived values in PKG/load.py — both `uuid5` keys, the five derived fields, `note` NULL, `roster_hash` from the envelope ← T005:NS_E005
- [ ] T040 [US2] {FR-025,FR-026,FR-030} Reconcile with `EXCEPT ALL` both ways over the 17-field line and 6-field event projection in PKG/load.py — skip, refuse-divergence, refuse-superset after:T039
- [ ] T041 [US2] {FR-023,FR-024,FR-029} Insert lines first, then events ascending by `(po_line_id, sequence_no)`, naming no `GENERATED`/`DEFAULT` column, then commit, in PKG/load.py after:T040
- [ ] T042 [US2] {FR-026} Wire the `procurement-load` entry — non-zero exits naming the diverging or extra keys, plus the rowcount concurrency guard — in PKG/load.py after:T041 → exports: main()
- [ ] T043 [US2] {FR-023} SC-008 — load into an empty migrated database, every delivered constraint enforced, none disabled (DV-004, DV-005, DV-007 at load) — TST/test_load_integration.py after:T042
- [ ] T044 [US2] {FR-025} SC-009 — a reload changes no count and no content over the stated projection, `created_at` excluded (DV-022) — TST/test_load_idempotency.py after:T042
- [ ] T045 [US2] {FR-026} Prove `EXCEPT`'s not-distinct NULL semantics over `closing_event_id`, `from_state` and `note`, both directions — TST/test_load_null_semantics.py after:T042
- [ ] T046 [US2] {FR-029} [COMPLETES FR-029] SC-011 — terminal lines closed and naming their event, others open and indexed, invariant holding at commit — TST/test_load_closure.py after:T042
- [ ] T047 [US2] {FR-024} SC-030 — events insert in ascending `sequence_no`, the non-deferrable chain FK satisfied at every statement, not only at commit — TST/test_load_ordering.py after:T042
- [ ] T048 [US2] {FR-023} [COMPLETES FR-023] SC-031 — one project and vendor per PO, `occurred_at` increasing, no instant past as-of (DV-003, DV-008) — TST/test_load_cross_row.py after:T042
- [ ] T049 [US2] {FR-026,FR-030} [COMPLETES FR-026] **NC-9** — both refusals leave the database unchanged, no row inserted first (DV-027) — TST/test_load_refusal_controls.py after:T042
- [ ] T050 [US2] {FR-027} **NC-4** integration half — a mutated roster and a mutated category map each refuse the load naming that input (DV-016) — TST/test_load_input_drift.py after:T042

---

## Phase 5: US3 - Reproduce the Dataset Exactly (Priority: P1) 🎯 MVP

**Independent test**: regenerate from the recorded seed in the pinned environment and confirm the canonical serialization hashes to the committed value.

- [ ] T051 [US3] {FR-021} Implement `procurement-validate` in PKG/validate.py — regenerate, recompute over the **parsed** payload, compare to the sidecar (DV-015) after:T037
- [ ] T052 [US3] {FR-027} Add the symmetric input-drift check to PKG/validate.py — each input recomputed under its own `digest_kind`, refusing and naming the offender (DV-016) after:T051
- [ ] T053 [P] [US3] {FR-027} [COMPLETES FR-027] **NC-4** unit half — a mutated roster and a mutated category map each produce a refusal naming that input, in TST/test_input_drift.py after:T052
- [ ] T054 [P] [US3] {FR-022} Add the provenance-agreement check (DV-025) to PKG/validate.py — datasheet fields equal their envelope counterparts, `library_pin` equals the resolved version after:T052
- [ ] T055 [US3] {FR-022} Add the scope-limit report to PKG/validate.py — the observed version an **injected** parameter defaulting to `numpy.__version__`, reported not claimed after:T054
- [ ] T056 [P] [US3] {FR-022} **NC-10** — an injected observed version outside the pin produces the scope-limit report and no reproduction claim (SC-032), in TST/test_pin_scope_control.py after:T055
- [ ] T057 [P] [US3] {FR-021} SC-012 — the digest reproduces under a fresh process, a changed absolute checkout path, hash seed, time zone and locale, in TST/test_reproduction_oracle.py after:T051
- [ ] T058 [P] [US3] {FR-021} [COMPLETES FR-021] **NC-1** / SC-013 — a different `root_seed` yields a different digest, so the oracle can fail, in TST/test_reproduction_control.py after:T051
- [ ] T059 [P] [US3] {FR-019} [COMPLETES FR-019] DV-024 / SC-014 — adding or moving one line changes no other line's generated values — TST/test_line_independence.py after:T034

---

## Phase 6: US4 - Audit the Dataset from Its Datasheet (Priority: P1) 🎯 MVP

**Independent test**: give the datasheet to a reader with no access to the generator source and confirm every generative assumption is recoverable from it.

- [ ] T060 [US4] {FR-014} Emit the seven named sections deterministically with no clock read in PKG/datasheet.py — Motivation … Maintenance, per FR-014 after:T037
- [ ] T061 [US4] {FR-015,FR-022} [COMPLETES FR-022] Write the Generation Process provenance in PKG/datasheet.py — identity, revision, seed, scheme, date, label, both digests, `library_pin` after:T060
- [ ] T062 [US4] {FR-007} [COMPLETES FR-007] Disclose the duration model in PKG/datasheet.py — family, parameters in the generator's parameterization, unit, rounding, floor, apportionment after:T061
- [ ] T063 [US4] {FR-035} [COMPLETES FR-035] Disclose the per-category expected duration offset, the tier assignment and the two named duration quantities (SC-028) in PKG/datasheet.py after:T062
- [ ] T064 [US4] {FR-015} Record realized against intended in PKG/datasheet.py — every figure in data-model §Generation Process disclosures, each naming its bounding criterion after:T063
- [ ] T065 [US4] {FR-016} Emit the ten limitation records L-1…L-10, each with scope decision, evidence, reversal trigger and production-scale alternative, in PKG/datasheet.py after:T064
- [ ] T066 [US4] {FR-028,FR-033} State in PKG/datasheet.py that no split is emitted, that ownership of the split is unassigned, and that 0.25 is an assumed cross-epic fraction after:T065
- [ ] T067 [US4] {FR-014,FR-016} Add the datasheet conformance check (DV-019) to PKG/validate.py — all seven sections present and 100% of limitation records carrying all four parts after:T066
- [ ] T068 [P] [US4] {FR-016} [COMPLETES FR-016] **NC-8** — a three-part limitation record must fail the checker — TST/test_limitation_format_control.py after:T067
- [ ] T069 [P] [US4] {FR-028} DV-020 / SC-021 — the emitted set is exactly the four artifacts, no file partitions `lines[]`, no split label — TST/test_emitted_artifact_set.py after:T066
- [ ] T070 [P] [US4] {FR-015} [COMPLETES FR-015] SC-015 / SC-018 — every duration assumption and the criticality mapping recoverable from the datasheet alone — TST/test_datasheet.py after:T066
- [ ] T071 [US4] {FR-014} [COMPLETES FR-014] Run `procurement-generate` and commit DAT/datasheet.md after:T070

---

## Phase 7: US5 - Keep Known Truth Out of the Model's Reach (Priority: P2)

**Separable from P1: the record itself ships in US1 and nothing in US1–US4 depends on this phase. T072–T074 share one file and are strictly sequential.**

- [ ] T072 [US5] {FR-018} DV-018 — enumerate the fitting entry point's input roots from its **own configuration**; GT/ is outside every one — TST/test_ground_truth_isolation.py after:T037
- [ ] T073 [US5] {FR-018} **NC-3** / DV-026 — the root set is non-empty and contains DAT/; an empty enumeration fails rather than passing vacuously — TST/test_ground_truth_isolation.py after:T072
- [ ] T074 [US5] {FR-018} [COMPLETES FR-018] **NC-2** — a probe copy of the record placed inside an enumerated root makes the check fail, in TST/test_ground_truth_isolation.py after:T073
- [ ] T075 [P] [US5] {FR-017} SC-019 — no loaded column exposes an offset: `roster_hash` is the only provenance column and `note` is NULL on every event, in TST/test_truth_not_in_db.py after:T042
- [ ] T076 [P] [US5] {FR-017} [COMPLETES FR-017] Add the ground-truth binding check (DV-017) to PKG/validate.py — 12 unique vendor offsets, `dataset_content_hash` equal to the fixture's after:T054

---

## Phase 8: Polish & Cross-Cutting Concerns

- [ ] T077 [P] {FR-034} Record FR-034 / SC-026 **BLOCKED** and implement **NC-12** — `data/corpus/synthetic/field-label-vocabulary.json` publishes neither field, and the check fails when that artifact gains them — TST/test_fr034_unblock_trigger.py
- [ ] T078 [P] **NC-11** / A1–A2 — a report-conformance check over qc-report.md: SC-026 and FR-034 render `BLOCKED`, printed denominators equal the count of `SC-###` and `FR-###` IDs defined in spec.md — 33 and 37 today, derived by counting rather than asserted as literals, because the counts already moved once from 26, `PASS` fails — TST/test_report_conformance.py
- [ ] T079 A3 — `WITHDRAWN` is a printed state distinct from `BLOCKED`, fails without a cited amendment reference, and stays in the printed denominators — TST/test_report_conformance.py after:T078
- [ ] T080 Record the ±4pp coverage-convention reassignment to E014 and assert no E005 artifact publishes a coverage level or reference proportion for it — TST/test_report_conformance.py after:T079
- [ ] T081 [P] Verify `model.procurement` is counted in the root combined coverage report rather than landing in the denominator uncounted, in .github/workflows/verify.yml after:T001
- [ ] T082 [P] Confirm the model entry declares no new dependency for `model.procurement` and keep tests/checks/test_dependency_isolation.py green after:T002
- [ ] T083 Add the build-gating step — reproduction oracle, isolation, emitted artifact set, report conformance, FR-034 trigger — as a release gate in .github/workflows/verify.yml after:T078

---

## Dependencies

Setup → Foundational → US1 → US2 / US3 / US4 → US5 → Polish

- **Foundational gates everything.** T005's record types and T007's canonical serializer are consumed by the generator, the loader and the validator. `serialize.py` gates the hash oracle and the oracle gates the validator: **T007 → T034 → T051**.
- **US1 gates US2, US3 and US4.** The generator must exist before the loader has a fixture to load (T037 → T038), before the validator has anything to re-derive (T037 → T051), and before the datasheet has realized figures to record (T037 → T060).
- **Mandatory red-green pairs** (`plan.md` §The test-first observable — the six property-tier modules): **T006 before T007** (`serialize.py`), **T010 before T011** (`allocate.py`), **T013 before T014** (`seeds.py`), **T015 before T016** (`durations.py`), **T019 before T020** (`censor.py`), **T022 before T023** (`criticality.py`). The test task must be observed **failing** before its implementation task begins, and the branch history must carry a `test:` commit before the `feat:` commit for each pair.
- **Loader write order is forced, not chosen** (`data-model.md` §Write Order, HINT-004): T041 inserts `purchase_order_line` first because `fk_lifecycle_event__line` is non-deferrable, then `lifecycle_event` ascending by `(po_line_id, sequence_no)` because `fk_lifecycle_event__chain` is non-deferrable, then commits — where `fk_purchase_order_line__closing_event`, the schema's only `DEFERRABLE INITIALLY DEFERRED` constraint, validates. T040 must complete before T041: the comparison runs against staged rows before any write, which is what makes a refusal leave the database unchanged.
- **Live database required**: T043–T050 and T075 need PostgreSQL on `${PRC_DB_PORT:-5434}` through T009's fixture. **None is `[P]`.**
- **Cross-phase edges**: T051→T037, T059→T034, T060→T037, T067→T066 (over `validate.py`, created at T051), T072→T037, T075→T042, T076→T054, T081→T001, T082→T002.
- **Same-file sequential runs** (never `[P]` together): T016→T017 and T032→T033→T034 (`generate.py`); T038→T039→T040→T041→T042 (`load.py`); T051→T052→T054→T055, then T067 and T076 (`validate.py`); T060→T066 (`datasheet.py`); T072→T073→T074 and T078→T079→T080 (one test file each).
- **Hints carried by tasks whose line had no room**: T034 implements HINT-002 (`timespec="seconds"`, or field width varies row to row and the digest moves); T039 implements HINT-001 (`uuid4` reads `os.urandom` and ignores the seed — every surrogate key is derived); T041 implements HINT-003 (`execute_batch` does not exist in psycopg 3, and `executemany` issues individual pipelined statements, which is why ascending `sequence_no` is load-bearing rather than tidy); T007 and T040 both implement HINT-005 and AD-004 (`numeric` equality ignores trailing zeros, so `quantity` is written at a fixed scale of exactly 1 and quantized through `Decimal`, or the SQL comparison and the digest disagree).
- **Negative controls, one task each** (`plan.md` §Negative Controls): NC-1 T058, NC-2 T074, NC-3 T073, NC-4 T050 and T053, NC-5 T021, NC-6 T018, NC-7 T028, NC-8 T068, NC-9 T049, NC-10 T056, NC-11 T078, NC-12 T077.
- **Reporting obligations carried from `plan.md`**: A1/A2 at T078 (SC-026 `BLOCKED`, printed denominators held at 33 and 37), A3 at T079 (`WITHDRAWN` distinct, with a cited amendment), A4 at T077 (the unblocking trigger), the FR-022 scope-limit **test** at T056 rather than only the code path at T055, and the ±4pp reassignment to E014 at T080.
- **P1 boundary**: Phases 1–6 (T001–T071) are the viable deliverable. Phase 7 (US5, P2) and Phase 8 are omittable without breaking a P1 criterion, though SC-019 and SC-020 go unasserted without Phase 7.
- Tasks marked `[P]` touch distinct files and carry no `after:T###` or `← T###:` edge to another task in the same batch. A task with either edge must not be `[P]`-batched with the task it references; the implementing agent verifies the referenced task is `[X]` before executing.

## Validation Performed Before Write

| Check | Result |
|---|---|
| Task IDs contiguous from T001 | **83 tasks, T001–T083**, no gap and no duplicate |
| Every FR carries at least one `{FR-###}` tag | **37 / 37** (FR-001…FR-037) |
| `[COMPLETES FR-###]` on the last task of every requirement spanning 3+ tasks | **20 markers**, no task carrying two |
| Every DV rule implemented or asserted, at the tier `data-model.md` assigns it | **27 / 27** (DV-001…DV-027) |
| Every negative control lands as a task | **12 / 12** (NC-1…NC-12) |
| Mandatory red-green pairs present and correctly ordered | **6 / 6** |
| Delivery tasks carry a `[US#]` label | **67 / 67** (T010–T076); Setup, Foundational and Polish carry none, as required |
| No orphan `after:` reference | **0** — every `after:T###` names a lower, existing ID |
| No `[P]` pair sharing a file; no `[P]` batch containing a task and its dependency | **0 violations** |
| Every `← T###:Symbol` has a matching `→ exports:` on T### | **6 / 6** — T024←T014, T026←T014, T030←T017, T032←T011, T034←T007, T039←T005 |
| No task line exceeds 200 characters | **pass** |
| Migration tasks emitted | **0**, by design — the `0200`–`0299` block is claimed and goes unused |
| Tasks attempting FR-034 or SC-026 | **0** — T077 records the blocked state and the unblocking trigger only |
