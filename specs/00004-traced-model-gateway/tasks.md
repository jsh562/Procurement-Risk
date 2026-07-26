# Tasks: Traced Model Gateway

**Input**: Design documents from `specs/00004-traced-model-gateway/`
**Prerequisites**: `plan.md`, `spec.md`, `data-model.md`, `research.md`, `checklists/` (data-integrity, security, observability — all evaluated)

**Tests**: Included. `plan.md` §Testing Strategy makes tests part of the deliverable, and `HINT-001` makes strict red-green-refactor **mandatory** for `compute/pricing.py`, `compute/hashing.py`, and `compute/timing.py` — the failing property-based test task precedes the implementation task for each. Every other module in this feature is test-after.

**Organization**: Grouped by technical objective (`OBJ#`) per `spec_type: technical`. Requirement tags are `TR-###`. `plan.md` §Requirement Coverage Map is the authority for the requirement → component → file assignment below.

## Project Mode

`Brownfield`

E001 scaffolded the four entries, the `uv` toolchain, the `import-linter` harness, the seeded-violation fixture pattern, and the Compose `db` service. No generic project-initialization tasks appear here. `~` paths in `plan.md` §Project Structure are extensions of files that already exist.

## Epic / Capability Map

- `[OBJ1]` → Provider-type-free traced invocation path — public surface, lazy provider import, orchestration seam, three build-time contracts (P1)
- `[OBJ2]` → Schema-validated output with bounded repair — validation, one repair, transport budget, deadline, total outcome mapping (P1)
- `[OBJ3]` → Invocation record with recomputable cost — revisions `0100`–`0103` authored into E003's directory, pricing and timing arithmetic, record writer, spool and reconcile, read contract (P1)
- `[OBJ4]` → Content-hash fixtures and offline replay — canonical serialization, digests, fixture store, mode selection, network guard (P1)
- `[OBJ5]` → Credential redaction and error normalization — five-sink egress inventory, two-detector scan, normalized errors, closed log field list (P1)
- `[OBJ6]` → Provider-reaching smoke and fixture refresh — opt-in gated, separable from P1 (P2)

## Brownfield Notes

- **Existing flows touched**: `src/gateway/src/gateway/provider.py` (E001's placeholder seam), `src/gateway/pyproject.toml` (E001's `protected` contract), `src/gateway/tests/test_provider.py`, `tests/checks/test_single_import_site.py`, `tests/checks/test_contract_fixtures.py`, `tests/checks/test_dependency_isolation.py`, `.github/workflows/verify.yml`
- **Compatibility and migration concerns**: `client_type()` is removed, not deprecated (TR-004, `BREAKING-CHANGE` signal); the provider SDK moves from base runtime to a `provider` extra (ADR-0014); migrations are confined to `0100`–`0199` and share E003's runner and ledger (IP-008); the SQLite spool is a transient buffer, not a datastore of record (ADR-0015)
- **Ordering constraints that shape the phases**:
  - `HINT-001` — the manifest change and the two new `import-linter` contracts land in Phase 1, *before* any module they constrain. A contract added after the code it should have blocked cannot prove it would have blocked it.
  - `HINT-002` — CI syncs the `provider` extra before `lint-imports`, or the `protected` contract errors on an ungraphed distribution rather than passing.
  - `data-model.md` §Migrations — `0100` → `0101` → `0102` → `0103` is a hard dependency; each later file carries a NOT NULL FK to a table an earlier file creates.
  - `HINT-007` — the aggregated latency column is `duration_ms`, never `latency_ms`, and cost is `cost_usd`, never `cost`. Binding on T029, T041, and every task touching `models.py`.
- **Regression focus**: E001's clean-tree contract runs, the single-naming-site scan, and the root `coverage combine` must all keep passing as the gateway package grows.

---

## Phase 1: Setup (Repository / Workspace Delta)

**Lands the manifest, the contracts, and the CI wiring before any module they constrain (HINT-001, HINT-002, HINT-003).**

- [ ] T001 {TR-003,TR-028,TR-029} Move anthropic into a `provider` extra, add psycopg base, hypothesis dev, and Ruff `S` in src/gateway/pyproject.toml — no migration extra, ADR-0013 puts tooling in /src/model
- [ ] T002 {TR-002,TR-032} Add the computation-boundary and public-surface-purity import-linter contracts to src/gateway/pyproject.toml after:T001
- [ ] T003 {TR-002,TR-032} Seed violation fixtures under tests/fixtures/ and assert both new contracts fail in tests/checks/test_contract_fixtures.py after:T002
- [ ] T004 {TR-003} Sync the gateway `provider` extra before the architecture-contracts step in .github/workflows/verify.yml (HINT-002) after:T001
- [ ] T005 Wire gateway coverage into the root combine with a repo-root COVERAGE_FILE and `coverage run` in .github/workflows/verify.yml (HINT-003) after:T004
- [ ] T006 Add a database-backed CI job carrying E001's `db` service for the OBJ3 migration and record criteria in .github/workflows/verify.yml after:T005

---

## Phase 2: OBJ1 - Provider-Type-Free Traced Invocation Path (Priority: P1) 🎯 MVP

- [ ] T007 [P] [OBJ1] {TR-002} Create gateway-owned request, result, and record types in src/gateway/src/gateway/models.py → exports: InvocationRequest, InvocationResult
- [ ] T008 [P] [OBJ1] {TR-002} Create the gateway-owned error hierarchy in src/gateway/src/gateway/errors.py → exports: GatewayError, GatewayConfigError
- [ ] T009 [OBJ1] {TR-001,TR-003,TR-004} Replace the client_type placeholder with a function-local lazy anthropic import in src/gateway/src/gateway/provider.py
- [ ] T010 [OBJ1] {TR-002} Create the public invocation surface in src/gateway/src/gateway/api.py ← T007:InvocationRequest → exports: invoke
- [ ] T011 [OBJ1] {TR-031,TR-047,TR-080} Add the explicit trace-id request field, 32-hex domain validation at the boundary, and generation in src/gateway/src/gateway/models.py and src/gateway/src/gateway/api.py after:T010
- [ ] T012 [OBJ1] {TR-032,TR-075} [COMPLETES TR-032] Create the orchestration module over provider, validation, compute, and record in src/gateway/src/gateway/orchestrator.py
- [ ] T013 [P] [OBJ1] {TR-002} Add the runtime public-surface check reporting a leaked provider type by name in src/gateway/tests/test_public_surface.py
- [ ] T014 [OBJ1] {TR-002,TR-004} [COMPLETES TR-002] Enumerate the public entry points and assert client_type is absent in src/gateway/tests/test_api_surface.py
- [ ] T015 [OBJ1] {TR-003} [COMPLETES TR-003] Add the no-provider-extra import and type-check harness in tests/checks/test_gateway_no_provider_env.py
- [ ] T016 [P] [OBJ1] {TR-001} Extend the provider naming-site source scan to the enlarged gateway package in tests/checks/test_single_import_site.py
- [ ] T017 [P] [OBJ1] {TR-029,TR-075} Assert the built gateway env carries no web framework, modeling stack, or OpenTelemetry SDK in tests/checks/test_dependency_isolation.py

---

## Phase 3: OBJ2 - Schema-Validated Output with Bounded Repair (Priority: P1) 🎯 MVP

- [ ] T018 [OBJ2] {TR-034} Create the configuration loader with the 120 s per-request deadline default in src/gateway/src/gateway/config.py → exports: GatewayConfig, load_config
- [ ] T019 [OBJ2] {TR-005} Implement native structured-output submission with unsupported keywords as post-decode validators in src/gateway/src/gateway/validation.py
- [ ] T020 [OBJ2] {TR-006,TR-007} Implement validate-before-return and the single repair carrying the failing field path in src/gateway/src/gateway/validation.py → exports: validate_or_repair
- [ ] T021 [OBJ2] {TR-010,TR-034} Implement the 2-retry / 3-attempt transport budget inside an outer monotonic deadline in src/gateway/src/gateway/provider.py after:T009 ← T018:GatewayConfig
- [ ] T022 [OBJ2] {TR-009,TR-042,TR-078} Implement the total terminal-state-to-outcome mapping in src/gateway/src/gateway/orchestrator.py after:T012 ← T020:validate_or_repair
- [ ] T023 [OBJ2] {TR-008} Write the invocation record before raising the fail-closed gateway error in src/gateway/src/gateway/orchestrator.py after:T022
- [ ] T024 [OBJ2] {TR-009,TR-078} Add the table-driven outcome test over every reachable combination in src/gateway/tests/test_validation_repair.py (VR-034) after:T022
- [ ] T025 [OBJ2] {TR-010,TR-034} [COMPLETES TR-034] Extend src/gateway/tests/test_provider.py for the lazy import, the retry budget, and deadline expiry after:T021

---

## Phase 4: OBJ3 - Invocation Record with Recomputable Cost (Priority: P1) 🎯 MVP

- [ ] T026 [OBJ3] {TR-070,TR-071,TR-072} Verify semconv 1.36.0 defines every convention-named attribute and record `otel_genai_semconv_version` in src/gateway/src/gateway/config.py after:T018
- [ ] T027 [OBJ3] {TR-015,TR-081} Author Alembic revision 0100_price_table_version (mandatory snapshot_date and source_url, slug CHECK) in src/model/migrations/versions/
- [ ] T028 [OBJ3] {TR-015,TR-046,TR-049} Author Alembic revision 0101_price_table_entry (composite PK, four NUMERIC(12,6) rates, restrictive FK) in src/model/migrations/versions/ after:T027
- [ ] T029 [OBJ3] {TR-012,TR-013,TR-016,TR-031,TR-044,TR-046} Author Alembic revision 0102_llm_invocation (named CHECKs, both indexes, pin COMMENT) in src/model/migrations/versions/ after:T028
- [ ] T030 [OBJ3] {TR-015,TR-081} [COMPLETES TR-015] Author Alembic revision 0103_seed_price_table (sourced version plus entries, ON CONFLICT DO NOTHING) in src/model/migrations/versions/ after:T029
- [ ] T031 [OBJ3] {TR-017} WITHDRAWN 2026-07-26 — ADR-0013 gives E003 the Alembic runner in src/model; E004 builds none. No work remains under this id
- [ ] T032 [OBJ3] {TR-017,TR-050} Verify apply-from-empty and second-run-no-op for this epic's revisions against E003's runner in src/gateway/tests/test_migrations.py (VR-027) after:T030
- [ ] T033 [P] [OBJ3] {TR-018,TR-051} Add the prefix-block, duplicate-prefix, and single-head check over E003's revision directory in tests/checks/test_migration_ranges.py (VR-028)
- [ ] T034 [P] [OBJ3] {TR-014,TR-028,TR-049} Write failing Hypothesis property tests for cost, sum-then-quantize-once, and decimal round trip in src/gateway/tests/test_compute_pricing.py
- [ ] T035 [OBJ3] {TR-014,TR-049} [COMPLETES TR-049] Implement the pure cost function in src/gateway/src/gateway/compute/pricing.py after:T034 → exports: compute_cost
- [ ] T036 [OBJ3] {TR-016,TR-039,TR-057} Implement the within-pin lookup with UTC calendar-date comparison in src/gateway/src/gateway/compute/pricing.py after:T035 → exports: resolve_price_entry
- [ ] T037 [OBJ3] {TR-039,TR-057} Add within-pin, case-sensitive, and two-session-timezone lookup tests in src/gateway/tests/test_compute_pricing.py (HINT-006) after:T036
- [ ] T038 [P] [OBJ3] {TR-028,TR-040,TR-056} Write failing Hypothesis property tests for duration and attempt aggregation in src/gateway/tests/test_compute_timing.py
- [ ] T039 [OBJ3] {TR-040,TR-056} Implement monotonic duration arithmetic and attempt-count aggregation in src/gateway/src/gateway/compute/timing.py after:T038 → exports: elapsed_ms
- [ ] T040 [OBJ3] {TR-035,TR-045} Implement the gateway-owned connection and own-transaction write in src/gateway/src/gateway/record/writer.py → exports: RecordWriter
- [ ] T041 [OBJ3] {TR-011,TR-012,TR-037,TR-043} Populate the closed field list, resolution mode, fixture key, and pricing timestamp in src/gateway/src/gateway/record/writer.py ← T035:compute_cost ← T040:RecordWriter ← T039:elapsed_ms
- [ ] T042 [OBJ3] {TR-048} Verify the pinned price-table version resolves before request construction in src/gateway/src/gateway/config.py and src/gateway/src/gateway/orchestrator.py (VR-025) after:T026
- [ ] T043 [OBJ3] {TR-058,TR-077} Emit the absent-cost warning and the invocation-completion log line in src/gateway/src/gateway/record/writer.py after:T041
- [ ] T044 [OBJ3] {TR-036,TR-041,TR-045} Implement fail-closed spooling to local SQLite in src/gateway/src/gateway/record/spool.py after:T040 → exports: InvocationSpool
- [ ] T045 [OBJ3] {TR-052,TR-053,TR-054,TR-077} Implement the invocation-triggered exactly-once drain with depth logging in src/gateway/src/gateway/record/reconcile.py ← T044:InvocationSpool
- [ ] T046 [OBJ3] {TR-035,TR-044,TR-055} Add caller-rollback survival and constraint-pairing tests in src/gateway/tests/test_record_writer.py (VR-016, VR-021) after:T041
- [ ] T047 [OBJ3] {TR-041,TR-052,TR-053,TR-054} Add spool, double-drain, and poisoned-row tests in src/gateway/tests/test_spool_reconcile.py (VR-018, VR-019) after:T045
- [ ] T048 [OBJ3] {TR-013,TR-070,TR-071,TR-072,TR-073} [COMPLETES TR-070] Add the forward-only transform, classification, and collision check in src/gateway/tests/test_field_naming.py after:T029
- [ ] T049 [OBJ3] {TR-012,TR-068,TR-079} [COMPLETES TR-012] Add the information-schema read-contract comparison and repaired-rate query in src/gateway/tests/test_read_contract.py after:T041
- [ ] T050 [OBJ3] {TR-055,TR-069,TR-074} Document the read-contract change, pin-bump, and append-only-by-convention procedures in src/gateway/README.md after:T048

---

## Phase 5: OBJ4 - Content-Hash Fixtures and Offline Replay (Priority: P1) 🎯 MVP

- [ ] T051 [OBJ4] {TR-021,TR-027,TR-063} Add mode selection with no default and the GATEWAY_ALLOW_PROVIDER_CALLS opt-in to src/gateway/src/gateway/config.py after:T018
- [ ] T052 [OBJ4] {TR-023} Add the replay-mode credential-presence guard over the gateway's own process environment in src/gateway/src/gateway/config.py after:T051
- [ ] T053 [P] [OBJ4] {TR-019,TR-020,TR-028,TR-038} [COMPLETES TR-028] Write failing Hypothesis property tests for canonical serialization and digests in src/gateway/tests/test_compute_hashing.py
- [ ] T054 [OBJ4] {TR-019,TR-020,TR-038} Implement canonical serialization, the closed hashed-field list, and digests in src/gateway/src/gateway/compute/hashing.py after:T053
- [ ] T055 [OBJ4] {TR-022,TR-033} Implement the content-hash store with provenance sidecars and replay-miss-raises in src/gateway/src/gateway/fixtures.py → exports: FixtureStore
- [ ] T056 [OBJ4] {TR-037,TR-056} [COMPLETES TR-056] Source replay token counts from fixture provenance and count a lookup as one transport attempt in src/gateway/src/gateway/fixtures.py
- [ ] T057 [P] [OBJ4] {TR-033} Commit the initial fixture store layout with one provenance sidecar per fixture under src/gateway/fixtures/
- [ ] T058 [OBJ4] {TR-067} Install the autouse network guard and the credential-free child environment in src/gateway/tests/conftest.py
- [ ] T059 [OBJ4] {TR-067} Add the outbound-egress observation check establishing SC-008 in tests/checks/test_no_outbound_egress.py after:T058
- [ ] T060 [P] [OBJ4] {TR-063} Assert GATEWAY_ALLOW_PROVIDER_CALLS is absent from every CI environment in tests/checks/test_ci_provider_gate_absent.py
- [ ] T061 [OBJ4] {TR-021,TR-022} Add replay end-to-end, miss, hashed-field-change, and mode-absent tests in src/gateway/tests/test_fixtures.py after:T055

---

## Phase 6: OBJ5 - Credential Redaction and Error Normalization (Priority: P1) 🎯 MVP

- [ ] T062 [OBJ5] {TR-062,TR-065} Add the single credential key and the bounded configuration-failure message exclusion set to src/gateway/src/gateway/config.py after:T052
- [ ] T063 [OBJ5] {TR-024,TR-059} Implement fail-closed credential redaction over the five-sink egress inventory in src/gateway/src/gateway/redaction.py → exports: redact
- [ ] T064 [OBJ5] {TR-061} Read the credential once at construction and hold it off every repr'd or serialized object in src/gateway/src/gateway/provider.py after:T009
- [ ] T065 [OBJ5] {TR-025,TR-064} Normalize provider exceptions to status, error type, and request id with no chained cause in src/gateway/src/gateway/errors.py and src/gateway/src/gateway/provider.py after:T008
- [ ] T066 [OBJ5] {TR-026,TR-066} Default content capture off and apply the closed log field list in src/gateway/src/gateway/config.py, src/gateway/src/gateway/redaction.py, and src/gateway/src/gateway/record/writer.py ← T063:redact
- [ ] T067 [OBJ5] {TR-030,TR-060} Implement the two-detector scan with a seeded positive case per sink in src/gateway/tests/test_fixture_credential_scan.py after:T063
- [ ] T068 [P] [OBJ5] {TR-024,TR-061} Add repr, traceback, and committed-fixture redaction tests in src/gateway/tests/test_redaction.py
- [ ] T069 [P] [OBJ5] {TR-066,TR-076} Add the per-sink not-captured marker test over record, spool, error, logs, and fixture in src/gateway/tests/test_not_captured.py (VR-037)
- [ ] T070 [P] [OBJ5] {TR-062,TR-065} Add mode, credential-key, and message-exclusion tests in src/gateway/tests/test_config_modes.py

---

## Phase 7: OBJ6 - Provider-Reaching Smoke and Fixture Refresh (Priority: P2)

**Separable from P1: nothing in OBJ1–OBJ5 depends on these tasks, and the credential-free suite passes without them.**

- [ ] T071 [OBJ6] {TR-027,TR-063} [COMPLETES TR-063] Add the opt-in-gated provider-reaching smoke check, skipped without the gate, in src/gateway/tests/test_provider_smoke.py after:T060
- [ ] T072 [OBJ6] {TR-027} [COMPLETES TR-027] Document the fixture regeneration procedure and its trigger conditions in src/gateway/README.md after:T071

---

## Phase 8: Polish & Cross-Cutting Concerns

- [ ] T073 [P] Add the fixture-store 25 MB and spool-depth 10 MB soft-cap warning checks in src/gateway/tests/test_fixtures.py and src/gateway/tests/test_spool_reconcile.py (AD-008)
- [ ] T074 Verify the gateway entry reaches ≥85% line coverage in the root combined report with the provider-path exclusion stated (AD-007) after:T005
- [ ] T075 [P] Benchmark gateway overhead ≤50 ms p95 excluding provider time and replay resolution ≤10 ms p95 (AD-008)

---

## Dependencies

Setup → Delivery Work Items (OBJ1 → OBJ2 → OBJ3 → OBJ4 → OBJ5 → OBJ6, by priority) → Polish

- **Foundational is omitted.** No task blocks multiple objectives without belonging to one; shared structures (`models.py`, `errors.py`, `config.py`) are created in the earliest objective that needs them and extended in place by later phases via `after:T###` edges.
- **Phase 1 gates everything.** T001–T003 must land before any module they constrain (HINT-001). T004 must land with T001, or `lint-imports` errors on an ungraphed distribution (HINT-002).
- **Migration chain**: T027 → T028 → T029 → T030 is a hard order — each revision carries a NOT NULL FK to a table an earlier one creates (`data-model.md` §Migrations). All four land in E003's directory under ADR-0013, so applying them waits on E003; authoring does not. T026 precedes T029 because the pinned convention document decides `0102`'s column spellings (HINT-008).
- **Mandatory red-green pairs** (HINT-001): T034 before T035, T038 before T039, T053 before T054. The test task must be observed failing before its implementation task begins.
- **Hints carried by tasks whose line had no room for the reference**: T035 implements HINT-004 (sum all four billing-class terms at full precision, quantize once); T045 implements HINT-005 (`ON CONFLICT DO NOTHING` suppresses primary-key conflicts only — an unresolvable FK is retained and logged, never dropped, and never fails the triggering invocation); T049 implements HINT-009 (compare against the information schema of a migrated database, never against `models.py`).
- **Cross-phase edges**: T021→T009/T018, T022→T012/T020, T025→T021, T042→T026, T051/T052→T018, T062→T052, T064→T009, T065→T008, T071→T060, T074→T005.
- **Within-phase edges** (same rule, listed separately so the cross-phase set stays accurate): T023→T022, T066→T063.
- **Symbol-import edges gate execution exactly as `after:` does.** Four tasks carry a `← T###:Symbol` edge and no `after:` at all — T010←T007, T041←T035/T040/T039, T045←T044, T066←T063 — so a consumer reading only `after:` under-constrains them. Both forms must be honoured.
- **P1 boundary**: Phases 1–6 (T001–T070) are the viable deliverable. Phase 7 (OBJ6, P2) and Phase 8 are omittable without breaking any P1 criterion.
- Tasks marked `[P]` can run in parallel within their phase — they touch distinct files and carry no `after:T###` or `← T###:` edge to another task in the same batch.
- A task with `after:T###` or `← T###:Symbol` must not be `[P]`-batched with the referenced task; the implementing agent must verify the referenced task is `[X]` before executing.
