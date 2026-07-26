# Tasks: Core Data Schema

**Input**: Design documents from `specs/00002-core-data-schema/`
**Prerequisites**: `plan.md`, `spec.md`, `data-model.md`, `research.md`, `checklists/data-integrity.md` (all CHK items complete)

**Tests**: Included. The spec's objectives each carry explicit verification deliverables and the epic's entire error surface is constraint rejection, so schema tests are the only observable. **Test strategy is test-after** per project policy for schema and ingestion work — no red-green mandate applies. No deterministic computation module is introduced, so no property-based-test obligation is triggered; Hypothesis is used only for the pure immutable helpers.

**No `contracts/`**: deliberate. This epic has no API surface (plan §API Surface Summary) — E008 and E010 own the queries, this epic owns the shapes they read.

**Normative source for the migration grouping**: `data-model.md` §Migration Sequence (TR-083). Ten migrations, `0001`–`0010`, inside E003's reserved `0001`–`0099` block.

## Path Shorthands

| Shorthand | Expands to |
|-----------|-----------|
| `.../schema/` | `src/model/src/model/schema/` |
| `.../versions/` | `src/model/src/model/schema/versions/` |
| `.../tests/schema/` | `src/model/tests/schema/` |

## Project Mode

`Brownfield` — E001 already froze the four-entry layout, the Compose `db` service, the identifier formats, and the cross-entry check harness. No bootstrap tasks; all work is additive migration, entry-local test, and targeted config change.

## Epic / Capability Map

- `[OBJ1]` → Forward-Only Migration Sequence (P1) — Alembic chain, reserved `0001`–`0099` block, extensions, schema constants, migrate job
- `[OBJ2]` → Retrievable Chunk Store (P1) — `document`, `chunk`, weighted `tsvector` + GIN, `vector(384)` + HNSW
- `[OBJ3]` → Provenance-Enforced Extraction Storage (P1) — `field_vocabulary`, `extracted_value`, contributors, `extraction_failure`
- `[OBJ4]` → Procurement Lifecycle Store (P1) — `purchase_order_line`, `lifecycle_event`, the one deferrable FK
- `[OBJ5]` → Versioned Forecast Artifact Contract (P1) — `forecast_run`, `line_posterior`, active-run pointer
- `[OBJ6]` → Resolved Cross-Document Entity Store (P2) — `resolved_entity`, `resolved_entity_member`

## Brownfield Notes

- **Existing flows touched**: `.github/workflows/verify.yml`, root `pyproject.toml`, `src/model/pyproject.toml`. **`docker-compose.yml` is NOT touched** — migrations run as a console entry point (ADR-0011), so no Compose service is added and `tests/checks/test_supply_chain.py`, `tests/checks/test_dependency_isolation.py`, `.github/workflows/verify.yml`, root `pyproject.toml`, `src/model/pyproject.toml`.
- **Migration ordering is a hard constraint**: the ten migrations form one linear Alembic chain, each with a single parent revision. None is `[P]` against another — parallel authoring produces multiple heads, which TR-005 fails the build on.
- **Two migrations are deliberately multi-object and must not be split.** `0006` creates `extracted_value`, `extracted_value_contributing_chunk`, `extraction_failure`, and the provenance view together. `0007` creates `purchase_order_line` and `lifecycle_event` in one migration because the deferred closing foreign key is a cycle that cannot be split across revisions.
- **ADR-0012 and ADR-0013 already exist and are Accepted.** No ADR authoring task is generated; TR-050 is satisfied by an existing artifact and carries a verification task only. `EMBEDDING_DIM = 384` is therefore already fixed.
- **Regression focus**: E001's orchestration, layout, build-context, image-contents, and supply-chain checks must keep passing **unmodified** — the console entry point touches no Compose service, no build context, and no image pin.
- **Test placement**: entry-local schema tests live in `.../tests/schema/`; only cross-entry checks live in root `tests/checks/` (TR-042 forbids the reverse).
- **Documentation and semantic requirements**: much of TR-053 … TR-086 states reader-facing semantics, retention, hand-off obligations, or authority direction rather than new DDL. Those are mapped to verification or documentation tasks against `data-model.md` and are marked as such in the task description — no build work is invented for them.

---

## Phase 1: Setup (Repository / Workspace Delta)

- [ ] T001 {TR-008} Add alembic, psycopg[binary], SQLAlchemy Core to deps and pytest-alembic to the dev group in src/model/pyproject.toml; refresh src/model/uv.lock
- [ ] T002 [P] Enable the Ruff `S` ruleset in src/model/pyproject.toml `[tool.ruff.lint] select` with per-file-ignores for S101 under tests (AD-003) after:T001
- [ ] T003 [P] Add src/model/src/model/schema to root pyproject.toml `[tool.coverage.run] source` and a matching `[tool.coverage.paths]` entry, or new code is uncounted
- [ ] T004 [P] Add the digest-pinned pgvector service container and its DATABASE_URL to the model job in .github/workflows/verify.yml

---

## Phase 2: OBJ1 - Forward-Only Migration Sequence (Priority: P1) 🎯 MVP

- [ ] T005 [P] [OBJ1] {TR-050} Verify ADR-0012 and ADR-0013 are present and Accepted in specs/adrs/ and that EMBEDDING_DIM is 384 — verification only, both ADRs already authored
- [ ] T006 [OBJ1] {TR-001,TR-003} Create src/model/alembic.ini plus .../schema/__init__.py and .../schema/env.py reading DATABASE_URL → exports: run_migrations_online()
- [ ] T007 [OBJ1] {TR-002,TR-004} Add the forward-only revision template .../schema/script.py.mako whose downgrade() raises, carrying the 0001-0099 filename-prefix convention
- [ ] T008 [OBJ1] {TR-006} Create .../versions/0001_enable_extensions.py running CREATE EXTENSION IF NOT EXISTS vector after:T007
- [ ] T009 [OBJ1] {TR-033,TR-043,TR-047,TR-056,TR-079} Create .../versions/0002_schema_constants.py: singleton table, six constants, seed 384/365/4000/1e-9 → exports: schema_constants
- [ ] T010 [P] [OBJ1] {TR-042} Create .../tests/schema/conftest.py: DATABASE_URL fixture, savepoint-rollback session, constraint-name assertion helper → exports: db_session, assert_rejects
- [ ] T011 [OBJ1] {TR-001,TR-002,TR-003,TR-004,TR-005} Add .../tests/schema/test_migration_chain.py: single head, upgrade-from-empty, re-apply no-op, prefix range, no downgrade body
- [ ] T012 [OBJ1] {TR-007} Declare the `migrate` console entry point in src/model/pyproject.toml `[project.scripts]` targeting the Alembic runner, invoked as `uv run --directory src/model migrate` (ADR-0011) — no Dockerfile, no image, no Compose service
- [ ] T013 [OBJ1] {TR-007,TR-037} Add a migrate step to .github/workflows/verify.yml using the existing `uv run --directory "src/$entry"` pattern, and assert docker-compose.yml is unchanged so E001's orchestration check passes unmodified after:T012

---

## Phase 3: OBJ2 - Retrievable Chunk Store (Priority: P1) 🎯 MVP

- [ ] T014 [OBJ2] {TR-041,TR-046,TR-057,TR-074,TR-075,TR-078,TR-087} Create .../versions/0003_document.py: id-format check, layer-conditional provenance (REAL: source/issuing body/retrieval date; SYNTHETIC: generator id/seed/generated_at/fixture hashes, each rejected on the other layer), fn_all_sha256_prefixed, one row per source-project after:T009 → exports: document
- [ ] T015 [OBJ2] {TR-009,TR-011,TR-012,TR-014,TR-058} Create .../versions/0004_chunk.py: chunk columns, vector(384), model id/revision, uq (chunk_id,page_number), fk_chunk__document → exports: chunk
- [ ] T016 [OBJ2] {TR-010,TR-013,TR-038} Add to 0004_chunk.py the generated search_vector on 'pg_catalog.english' with A-D weights, its GIN index, and the HNSW cosine index ← T015:chunk
- [ ] T017 [OBJ2] {TR-010,TR-038} Add .../tests/schema/test_chunk.py: heading match outranks body match, and two sessions with differing defaults build identical vectors ← T010:db_session
- [ ] T018 [OBJ2] {TR-011,TR-012,TR-013} Extend test_chunk.py: exact scan and HNSW lookup on the same relation with no DDL between, per-row model identity, gap G-8 mismatch case
- [ ] T019 [OBJ2] {TR-014,TR-041,TR-046,TR-074,TR-075,TR-077,TR-087} Extend test_chunk.py: reject empty body, missing page, bad document ref, bad PRJ id; assert a SYNTHETIC row carrying an issuing body is rejected and a REAL row missing one is rejected; G-9 format

---

## Phase 4: OBJ3 - Provenance-Enforced Extraction Storage (Priority: P1) 🎯 MVP

- [ ] T020 [OBJ3] {TR-044,TR-079} Create .../versions/0005_field_vocabulary.py: lookup table, uq (field_name,value_kind), 22 seeded rows after:T016 → exports: field_vocabulary
- [ ] T021 [OBJ3] {TR-015,TR-016,TR-017,TR-045,TR-054,TR-082} Create .../versions/0006_extraction.py: NOT NULL citation/confidence, composite FK (chunk_id,page_number) → exports: extracted_value
- [ ] T022 [OBJ3] {TR-018,TR-058,TR-059,TR-060} Add extracted_value_contributing_chunk (ordinals 2..N) and v_extracted_value_provenance to 0006_extraction.py ← T021:extracted_value
- [ ] T023 [OBJ3] {TR-019,TR-061} Add extraction_failure to the same 0006_extraction.py: attempted field, source chunk, outcome set including missing_citation, repair_attempt_count
- [ ] T024 [OBJ3] {TR-015,TR-016,TR-017} Add .../tests/schema/test_extraction.py: reject missing citation, missing or out-of-range confidence, cited page not equal to chunk page ← T010:assert_rejects
- [ ] T025 [OBJ3] {TR-018,TR-059,TR-060} Extend test_extraction.py: a three-chunk value is fully recoverable through the provenance view; gap G-1 count and G-2 duplicate-anchor cases
- [ ] T026 [OBJ3] {TR-019,TR-044,TR-061} Extend test_extraction.py: failure record with no value row (G-5), unknown field rejected, a new term usable by INSERT alone, retired term (G-7)
- [ ] T027 [OBJ3] {TR-045,TR-054,TR-081,TR-082,TR-085} Extend test_extraction.py: text + optional numeric only, no FK to purchase_order_line; verify confidence/agent/retention rules in data-model.md

---

## Phase 5: OBJ4 - Procurement Lifecycle Store (Priority: P1) 🎯 MVP

- [ ] T028 [OBJ4] {TR-022} Create .../schema/helpers.py holding fn_is_legal_lifecycle_transition as IMMUTABLE STRICT PARALLEL SAFE → exports: FN_IS_LEGAL_LIFECYCLE_TRANSITION
- [ ] T029 [OBJ4] {TR-020,TR-023,TR-024,TR-025,TR-066} Create .../versions/0007_procurement.py: purchase_order_line, PRJ/VND/sha256 and date-order checks after:T023 → exports: purchase_order_line
- [ ] T030 [OBJ4] {TR-022} Add lifecycle_event, fk_lifecycle_event__chain, the terminal-flag check, per-line and per-vendor indexes, and v_purchase_order_line_current_state to 0007 ← T028:FN_IS_LEGAL_LIFECYCLE_TRANSITION
- [ ] T031 [OBJ4] {TR-021,TR-065,TR-067} Add fk_purchase_order_line__closing_event DEFERRABLE INITIALLY DEFERRED to 0007 and record the shape taken in data-model.md's mechanism map
- [ ] T032 [OBJ4] {TR-021,TR-066,TR-067} Add .../tests/schema/test_procurement.py: an open line persists as censored; a closed line with no terminal event fails at COMMIT (HINT-002)
- [ ] T033 [OBJ4] {TR-022} [COMPLETES TR-022] Extend test_procurement.py: two rework cycles recoverable in sequence order; gap G-3 state agreement and G-4 occurred_at monotonicity
- [ ] T034 [OBJ4] {TR-023,TR-024,TR-025} Extend test_procurement.py: reject an inverted order/need-by pair, a malformed PRJ-###, a malformed VND-###, and a malformed roster hash

---

## Phase 6: OBJ5 - Versioned Forecast Artifact Contract (Priority: P1) 🎯 MVP

- [ ] T035 [OBJ5] {TR-070} Add fn_is_sorted_ascending, fn_is_non_increasing, and fn_all_within_unit_interval to .../schema/helpers.py, with Hypothesis unit tests after:T028 → exports: fn_is_sorted_ascending
- [ ] T036 [OBJ5] {TR-026,TR-049,TR-062,TR-071} Create .../versions/0008_forecast.py: forecast_run, nine NOT NULL reproducibility columns, as_of_date, horizon_days after:T031
- [ ] T037 [OBJ5] {TR-027,TR-032,TR-040,TR-080} Add ix_forecast_run__single_active, artifact_schema_version, draw_serialization, artifact_hash bytea, and v_active_forecast_run to 0008
- [ ] T038 [OBJ5] {TR-028,TR-031,TR-068,TR-069,TR-073} Add line_posterior to 0008: both arrays in one row, fk_line_posterior__run_shape, sortedness check ← T035:fn_is_sorted_ascending
- [ ] T039 [OBJ5] {TR-029,TR-030,TR-055,TR-072} Add the survival array, residual_tail_mass, ck_line_posterior__survival_length and __residual_matches_grid_tail at 1e-9 to line_posterior
- [ ] T040 [OBJ5] {TR-026,TR-027} Add .../tests/schema/test_forecast.py: every reproducibility field rejected when null; a second active run rejected; no active run returns no row
- [ ] T041 [OBJ5] {TR-028,TR-069,TR-070,TR-072,TR-073} Extend test_forecast.py: unsorted draws, wrong-length draws, and a wrong-length survival array are each rejected by named constraint
- [ ] T042 [OBJ5] {TR-029,TR-030,TR-055} Extend test_forecast.py: the survival array plus its residual account for the full distribution within 1e-9 at double precision, never by exact equality
- [ ] T043 [OBJ5] {TR-031,TR-040,TR-068} Extend test_forecast.py: neither array insertable without the other; the digest is identical across differing numeric text-rendering settings
- [ ] T044 [P] [OBJ5] {TR-033,TR-049,TR-053,TR-064} Verify data-model.md records the anchor and nearest-rank conventions, the beyond-horizon answer 1-residual, and G-10's reader gate — docs only

---

## Phase 7: OBJ6 - Resolved Cross-Document Entity Store (Priority: P2)

- [ ] T045 [OBJ6] {TR-034} Create .../versions/0010_resolved_entity.py: normalized manufacturer, part number, agreement_attribute_names after:T048 → exports: resolved_entity
- [ ] T046 [OBJ6] {TR-035,TR-045} [COMPLETES TR-045] Add resolved_entity_member to 0010: XOR target, uq_rem__extracted_value, uq_rem__po_line, CASCADE to the entity ← T045:resolved_entity
- [ ] T047 [OBJ6] {TR-034,TR-035} Add .../tests/schema/test_resolved_entity.py: three-source membership recoverable, a second entity rejected, a single-member entity persists, gap G-6

---

## Phase 8: Polish & Cross-Cutting Concerns

- [ ] T048 {TR-084,TR-086} Create .../versions/0009_provenance_privileges.py: REVOKE UPDATE/DELETE on the two provenance tables from the app role, retained for the migration role after:T039
- [ ] T049 {TR-084,TR-086} Extend .../tests/schema/test_extraction.py: UPDATE and DELETE refused as the app role and permitted as the migration role, evidencing SC-028 after:T048
- [ ] T050 [P] {TR-047,TR-048,TR-076,TR-079} [COMPLETES TR-079] Add .../tests/schema/test_constants_agreement.py: published dimension equals the chunk typmod, tolerance the DDL literal after:T048
- [ ] T051 [P] {TR-039,TR-051,TR-063} Add .../tests/schema/test_constraint_audit.py: every range check paired with NOT NULL, zero deferrable checks, the declared defaults enumerated after:T048
- [ ] T052 [P] {TR-036,TR-083} Add .../tests/schema/test_table_ownership.py: the six named other-epic tables absent, every created object present in data-model.md after:T048
- [ ] T053 [P] {TR-008,TR-042} Extend tests/checks/test_dependency_isolation.py: only src/model may declare alembic/psycopg/SQLAlchemy; assert no entry-local schema test sits at the repo root
- [ ] T054 [P] {TR-052} Verify plan.md's Amendment Requests section records AR-1 with exact replacement text for both Shared Data Entities cells — record only; v1.2.0 forbids this branch editing specs/project-plan.md

---

## Dependencies

**Phase graph**

```
Phase 1 Setup (T001-T004)
        ↓
Phase 2 OBJ1 (T005-T013)  ── the Alembic chain root: 0001, 0002
        ↓
Phase 3 OBJ2 (T014-T019)  ── 0003, 0004
        ↓
Phase 4 OBJ3 (T020-T027)  ── 0005, 0006
        ↓
Phase 5 OBJ4 (T028-T034)  ── 0007
        ↓
Phase 6 OBJ5 (T035-T044)  ── 0008
        ↓
Phase 7 OBJ6 (T045-T047)  ── 0010   (P2, last in chain)
        ↓
Phase 8 Polish (T048-T054) ── 0009 privileges + whole-schema audits
```

**Rules in force**

- Setup has no dependencies. Every later phase depends on T001 (the dependency manifest) and, for anything touching the database, on T004 (the CI service container).
- No Foundational phase exists. The two structures shared across objectives — the Alembic environment (T006) and the test fixtures (T010) — are OBJ1 deliverables in their own right (TR-001, TR-003, TR-042), so they are created in the earliest work item that needs them rather than lifted into a shared phase.
- **The migration chain is strictly linear and is the spine of the phase graph.** Single-parent revisions in order: `0001` (T008) → `0002` (T009) → `0003` (T014) → `0004` (T015, T016) → `0005` (T020) → `0006` (T021, T022, T023) → `0007` (T029, T030, T031) → `0008` (T036-T039) → `0009` (T045, T046) → `0010` (T048). **No migration task is `[P]` against another.** Parallel authoring produces multiple Alembic heads, which TR-005 fails the build on.
- **`0006` and `0007` are each one migration carrying several tasks.** T021, T022, and T023 all write `0006_extraction.py`; T029, T030, and T031 all write `0007_procurement.py`. Splitting `0007` is not an option — the closing foreign key is a cycle between line and event and cannot cross a revision boundary.
- Cross-phase migration edges are carried explicitly: `after:T009` on T014, `after:T016` on T020, `after:T023` on T029, `after:T031` on T036, `after:T039` on T048, `after:T048` on T045.
- Within a phase: helper functions → migration → tests. Tests for a table group may start as soon as that group's migration lands; they are sequential only because each objective's tests share a single test file.
- **Same-file chains are ordered even where no `after:` edge is written.** Tasks that add to one migration file (T021→T022→T023 on `0006`; T029→T030→T031 on `0007`; T036→T037→T038→T039 on `0008`; T045→T046 on `0010`) and every "Extend test_*.py" run must execute in listed order. None is `[P]`, so a scheduler honouring `[P]` alone will not reorder them — but do not infer independence from the absence of an edge.
- **No Compose change at all.** ADR-0011 requires a modeling-owned job to be a console entry point, and a context rooted at `src/model` could not resolve the `gateway = { path = "../gateway" }` dependency in any case. `docker-compose.yml`, `src/.dockerignore`, and `tests/checks/test_supply_chain.py` are all left alone, which removes the red-tree window the earlier Compose plan had.
- **`0009_provenance_privileges.py` precedes the P2 migration, so no P1 obligation waits on P2.** TR-084 and TR-086 are OBJ3 (P1) obligations. The migration grants only against tables `0006` creates, so its position is free; it sits at `0009`, before OBJ6's `0010`, and T048 depends on T039 (the last P1 migration task) rather than on any P2 task. T048/T049 keep their Polish task IDs — IDs are never renumbered — but they are P1 work and are not droppable with P2. T049 evidences SC-028.
- **The whole-schema audits (T050-T052) are in Polish because they read the migrated object set as a whole.** The constants-agreement test needs `0004`'s declared typmod, the constraint audit needs every constraint, and the ownership check needs the final object set. None can run before the chain is complete.
- `[P]` batches: `{T002, T003, T004}` (three distinct config files; T002's dependency T001 is outside the batch), `{T005}`, `{T010}`, `{T044}`, and `{T050, T051, T052, T053, T054}` (five distinct files, no edges among them).
- A task carrying `after:T###` or `← T###:Symbol` is never `[P]`-batched with the task it references.
- Polish depends on all six objectives being complete.

## Requirement Coverage

All 86 requirements TR-001 … TR-086 carry at least one `{TR-###}` tag. Requirements spanning three or more tasks carry `[COMPLETES]` on the last: **TR-022** (T028, T030, T033), **TR-045** (T021, T027, T046), **TR-079** (T009, T020, T050).

Requirements satisfied by verification or documentation rather than new build work, and the task that closes each:

| Requirement | Nature | Closed by |
|-------------|--------|-----------|
| TR-050 | ADR-0012 and ADR-0013 already exist and are Accepted | T005 (verification only) |
| TR-053, TR-064 | Reader-side array semantics owned by E010 | T044 (data-model.md check) |
| TR-057, TR-078 | Document-revision and key-space-change semantics | T014 (DDL) + data-model.md |
| TR-062, TR-080 | Run-granularity provenance; no maximum artifact age here | T036, T037 (column presence and deliberate absence) |
| TR-065 | Fallback ladder and the obligation to record the shape taken | T031 (mechanism-map entry) |
| TR-076 | Direction of authority: the DDL literal governs the published row | T050 |
| TR-077 | E002/E006 obligation to adopt the declared `document_id` format | T019 (gap G-9 format assertion) |
| TR-079 | Seeded reference data recovers only by re-apply-from-empty; loss is detected, not repaired in place | T050 (constants-agreement check named by the requirement) |
| TR-081, TR-082, TR-085 | Confidence is self-reported, agent identity is per ingestion run, rows are retained for the life of the database | T027 |
| TR-083 | `data-model.md` normative for column semantics; no undocumented object | T052 |
