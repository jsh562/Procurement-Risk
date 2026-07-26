# Tasks: Public Corpus and Manifest

**Input**: Design documents from `specs/00002-public-corpus-and-manifest/`
**Prerequisites**: `plan.md`, `spec.md`, `data-model.md`, `research.md`, `checklists/` (data-integrity, testing, security — all closed)

**Tests**: Included — SC-021, SC-025, FR-031b and the Testing Strategy's entry criteria make test tasks requirement-bearing rather than optional. The property tests of Phase 2 are strict test-first (HINT-001).

**Organization**: Grouped by user story (`US#`), `spec_type: product`. Requirement tags are `FR-###`. Rule identifiers `VR-###` are `data-model.md`'s.

## Project Mode

`Brownfield` — the repository already carries four entries, the roster reader, the check harness, and `verify.yml`. This feature adds one package inside `/src/model`, one new data tree under `data/`, and edits exactly two existing files (`src/model/pyproject.toml`, `.github/workflows/verify.yml`) plus one existing check (`tests/checks/test_supply_chain.py`).

## Epic / Capability Map

- `[US1]` → Vendored public-domain specification layer, retrieval policy, exclusion ledger (P1)
- `[US2]` → Per-document provenance records and the corpus validator (P1)
- `[US3]` → Seeded, offline synthetic submittal layer generated from the E001 roster (P1)
- `[US4]` → Recorded, independently re-derived formatting irregularity (P2)
- `[US5]` → `pull_request` triggering of the verification workflow (P2)

## Brownfield Notes

- Existing flows touched: `src/model/pyproject.toml` (four dependencies, `[project.scripts]`, a new `[tool.importlinter]` contract), `.github/workflows/verify.yml` (three separate edits, in three phases, by design), `tests/checks/test_supply_chain.py` (credential scan widened), `tests/checks/test_contract_fixtures.py` (one new fixture case).
- Consumed, not redefined: `model.roster.reader.read_roster()` — `Roster.content_hash`, `Roster.projects` (5), `Roster.vendors` (12), `Entry.id`, `Entry.name`. This epic declares no project and no vendor.
- Regression focus: `tests/checks/test_single_import_site.py` must still find exactly one source file naming `project-vendor-roster` (VR-045); `test_only_the_serving_boundary_and_the_gateway_are_admitted` and `test_excluded_entries_are_unreachable_from_the_build` must stay green — **do not add `!model` to `src/.dockerignore`** (HINT-003).

## Global Execution Rules

- Run every Python tool as `uv run --directory src/model …`. A bare `pytest`/`ruff`/`lint-imports` crosses the boundary the contracts exist to enforce.
- **HINT-001 is an entry criterion, not advice.** No rendered PDF and no manifest is committed until T008's property tests pass against T009. T008 lands as its own commit, red, with no `corpus/model.py` beside it; it is not squashed with T009 on the branch.
- **HINT-004**: write every manifest with `Path.write_bytes` or `newline="\n"`. The development machine is Windows; the **Linux verification runner is the platform of record** for every byte-identity claim (MS-4).
- Every rule-group task below carries **one failing-direction case per rule, naming the rule id** (SC-025). The `(n of 72)` counters state how many of the 72 numbered validation rules each group discharges; they sum to 72 exactly.
- Every failure exits non-zero naming the rule id and the offending `location_id`/`location`, and **all** failures are collected (VR-056).

---

## Phase 1: Setup (Repository / Workspace Delta)

- [ ] T001 Create the empty package `src/model/src/model/corpus/__init__.py` so the new import contract and the widened coverage scope resolve against a real module
- [ ] T002 Add reportlab, pillow, pdfplumber, and jsonschema[format-nongpl] to src/model/pyproject.toml and regenerate src/model/uv.lock with exact pins and artifact hashes
- [ ] T003 {FR-015,FR-008b} Declare corpus-retrieve, corpus-generate, corpus-validate, and corpus-reverify in src/model/pyproject.toml `[project.scripts]`
- [ ] T004 {FR-022} Add the forbidden import-linter contract from `model.corpus` to `model.llm` and `gateway`, `allow_indirect_imports = false`, in src/model/pyproject.toml after:T001
- [ ] T005 Widen the model coverage scope to `--source=src/model/roster,src/model/corpus` in .github/workflows/verify.yml after:T001
- [ ] T006 [P] Widen the credential scan in tests/checks/test_supply_chain.py to the `model` entry and the `data/` root

**Notes**: T004 and T005 sit here rather than later on purpose — HINT-002 warns that a coverage gate left at `--source=src/model/roster` passes at 80% while measuring none of this epic's code, and an import contract added after the package would leave the offline claim unenforced across every commit in between. Both need T001 first: `lint-imports` errors on a `source_modules` entry that does not exist, and `--source` on an absent path measures nothing. T005 is the first of three separate edits to `verify.yml`; the corpus-validation step is T034 (FR-017) and the `pull_request` trigger is T057 (FR-034). T006 is vacuous on the day it lands — this epic introduces no credential and FR-002a forbids allow-listing a source needing one — and exists to fail the moment one appears.

---

## Phase 2: Foundational (Cross-Work-Item Blockers)

- [ ] T007 [P] Register the Hypothesis profile — `max_examples=200`, `derandomize=True`, `deadline=None` — in src/model/tests/conftest.py and select it in CI
- [ ] T008 {FR-021} Commit failing property tests PB-1…PB-4 and PB-6 with their named boundary cases in src/model/tests/test_corpus_model_hash.py, with no corpus/model.py beside them
- [ ] T009 {FR-007,FR-021} Implement DM-1…DM-6 canonical serialization and document_model_hash in src/model/src/model/corpus/model.py after:T008 → exports: DocumentModel, document_model_hash()
- [ ] T010 {FR-006b,FR-007} Implement entry value objects, the MS-1…MS-6 manifest writer, and the sha256 helpers in src/model/src/model/corpus/manifest.py → exports: ManifestEntry, write_manifest()
- [ ] T011 {FR-006b} Add PB-5 and the duplicate-key, case-collision, and empty-class boundary cases in src/model/tests/test_corpus_manifest.py ← T010:write_manifest
- [ ] T012 {FR-017a,FR-018} Implement corpus root, non-following location discovery, and containment in src/model/src/model/corpus/paths.py → exports: discover_locations(), resolve_within()
- [ ] T013 [P] {FR-006a,FR-007,FR-014} Author the draft 2020-12 base of data/corpus/manifest.schema.json: common entry fields, digest pattern, closed layer enum, additionalProperties false
- [ ] T014 {FR-008,FR-009,FR-010,FR-033} Add the layer conditional and both prohibited-key clauses — eight REAL and seven SYNTHETIC fields — to data/corpus/manifest.schema.json
- [ ] T015 {FR-005,FR-011,FR-012,FR-012a} Add per-layer license-basis subschemas to data/corpus/manifest.schema.json: closed basis_id, closed statute, point-of-use enum, SYNTHETIC consts

**Notes**: Everything here blocks two or more stories. `model.py` and `manifest.py` are where every determinism claim in the epic reduces to — discovering the serialization wrong after 25 PDFs are committed means regenerating all of them (HINT-001). `manifest.schema.json` is authored here rather than in US2 because US1 writes the real manifest against it before the validator exists. T013→T014→T015 edit one file and are sequential; only T013 is parallel-safe. The five digest kinds stay distinct in `manifest.py`: `content_hash` and `generation_inputs.*` are over raw bytes, `roster_hash` is the reader's canonical-content value, `document_model_hash` is over the pre-render model, `upstream_digest` is over retrieved bytes.

---

## Phase 3: US1 - Vendor the Public-Domain Specification Layer (Priority: P1) 🎯 MVP

- [ ] T016 [US1] {FR-002,FR-002a} Author data/corpus/real/retrieval-policy.json: allow-listed hosts with the 301 origin, closed agency_variants map, justified target sections, anchor 01 33 00
- [ ] T017 [P] [US1] {FR-002} Author the committed target-section list weighted to Division 26 and 23 long-lead equipment in src/model/src/model/corpus/sources.py → exports: TARGET_SECTIONS
- [ ] T018 [US1] {FR-002b} Implement the hop-walking retrieval client in src/model/src/model/corpus/retrieve.py: https only, exact host match, ≤5 hops, 50 MB cap → exports: fetch_document()
- [ ] T019 [US1] {FR-001,FR-003,FR-008,FR-008c} Digest the response body before the write and vendor bytes unmodified in .../corpus/retrieve.py ← T018:fetch_document
- [ ] T020 [US1] {FR-004,FR-005} Author data/corpus/real/exclusions.json and the ledger writer in .../corpus/retrieve.py — closed cause enum, every exclusion recorded with its cause
- [ ] T021 [US1] {FR-001,FR-002} Run corpus-retrieve and vendor ≥20 sections over ≥6 distinct long-lead sections plus 01 33 00 into data/corpus/real/ufgs/ after:T019
- [ ] T022 [US1] {FR-006b,FR-007,FR-008} Write data/corpus/real/ufgs/manifest.json once through the canonical writer — eight REAL fields and a four-part license basis per entry ← T010:write_manifest
- [ ] T023 [US1] {FR-008b} Implement the re-verification entry point in src/model/src/model/corpus/reverify.py — re-fetch, compare to upstream_digest, never invoked by verify.yml
- [ ] T024 [US1] {FR-002b,FR-008b} [COMPLETES FR-008b] Fixture tests for both clients in src/model/tests/test_corpus_retrieve.py and test_corpus_reverify.py — eight conditions, no network

**Notes**: T024's eight conditions, stated once here: a redirect hop re-checked against the allow-list; a hop landing outside it; a host that merely *ends in* an allow-listed name (rejected — matching is exact equality); a redirect into a non-`https` scheme; a non-200 status; a sixth redirect hop; a body past 50 MB; and a digest diverging from the recorded `upstream_digest`. Both clients share one policy so the two network paths cannot drift. T018/T019/T020 write one file and are sequential. **The rules that check this layer land in Phase 4** — T030 (VR-018), T032 (VR-019…022), T033 (VR-025, VR-026, VR-062) — because they belong to the validator, not to retrieval. FR-008c's procedure (digest from the response body, never back-filled from the committed file) is observed by no committed check and is published as uncovered; T019 is where the obligation is discharged. A manual fallback for a blocking host records exactly the eight FR-008 fields and no others — an extra key fails the schema.

---

## Phase 4: US2 - Record and Validate Per-Document Provenance (Priority: P1) 🎯 MVP

- [ ] T025 [US2] {FR-015} Implement the validator core in src/model/src/model/corpus/validate.py — rule registry, all failures collected, non-zero exit — VR-056, VR-057, VR-066 (3 of 72)
- [ ] T026 [US2] {FR-015} [COMPLETES FR-015] Build the negative-corpus fixture harness in src/model/tests/test_corpus_validate.py — tmp_path builder plus an assert-fails-naming-rule helper
- [ ] T027 [US2] {FR-006a,FR-006b,FR-010,FR-014} Schema-conformance and field-set group in .../corpus/validate.py — VR-001…003, VR-014…017, VR-027, VR-058, VR-063 (10 of 72)
- [ ] T028 [US2] {FR-001a,FR-006,FR-014a,FR-017a,FR-018a} Location topology and file↔entry reconciliation group in .../corpus/validate.py — VR-004…007, 010, 011, 013, 059, 060, 064, 065 (11 of 72)
- [ ] T029 [US2] {FR-006,FR-018} Path containment, symlink prohibition, and case-collision group in .../corpus/validate.py — VR-009, VR-067, VR-068 (3 of 72) ← T012:resolve_within
- [ ] T030 [US2] {FR-007,FR-008a} [COMPLETES FR-007] Digest recomputation group in .../corpus/validate.py — VR-012 file bytes, VR-018 content hash equals upstream digest (2 of 72)
- [ ] T031 [US2] {FR-005,FR-011,FR-012,FR-012a,FR-013} [COMPLETES FR-005] License-basis group in .../corpus/validate.py — VR-008, VR-023, VR-024, VR-028 (4 of 72)
- [ ] T032 [US2] {FR-003,FR-008} [COMPLETES FR-008] REAL field-value group in .../corpus/validate.py — VR-019, VR-020, VR-021, VR-022 (4 of 72)
- [ ] T033 [US2] {FR-002,FR-002a,FR-004,FR-008d} [COMPLETES FR-002] Policy-agreement and ledger group in .../corpus/validate.py — VR-025, VR-026, VR-062 (3 of 72)
- [ ] T034 [US2] {FR-017} Add the corpus-validation step invoking corpus-validate to .github/workflows/verify.yml after:T025

**Notes**: 40 of the 72 rules are discharged here, grouped by what they read rather than one task per rule. Rule totals: 3 + 10 + 11 + 3 + 2 + 4 + 4 + 3 = 40. The remaining 32 are Phase 5 (19) and Phase 6 (13). Two orderings are load-bearing: T028's containment and link tests use non-following stats throughout — a symlink to a regular file passes a link-following `is_file()` test exactly, which is why VR-067 exists separately — and T029 resolves the real path **before** comparing against the entry's own location directory, never after. T030's VR-018 is an internal-consistency check, not a provenance proof; its force is conditional on FR-008c and the residual is published. The validator never re-runs the generator: anything requiring regeneration lives in the test suite (Phases 5–6), because a validator that regenerated to validate could not tell a corpus defect from a generator defect.

---

## Phase 5: US3 - Generate the Project-Document Layer from the Roster (Priority: P1) 🎯 MVP

- [ ] T035 [P] [US3] {FR-023a} Define the approximated descriptor codes, review-code letters, and field labels in src/model/src/model/corpus/codes.py → exports: DESCRIPTOR_CODES, ACTION_CODES
- [ ] T036 [US3] {FR-009a,FR-026} Author the three committed generation inputs in data/corpus/synthetic/ — generation-config.json, equipment-category-map.json, field-label-vocabulary.json after:T021
- [ ] T037 [US3] {FR-026} Implement equipment-category to MasterFormat mapping in src/model/src/model/corpus/equipment.py after:T036 → exports: section_for_category()
- [ ] T038 [US3] {FR-023,FR-029} Implement per-vendor layout templates carrying the six structural fields in src/model/src/model/corpus/templates.py ← T035:DESCRIPTOR_CODES
- [ ] T039 [US3] {FR-021a,FR-021b,FR-032} Implement the deterministic ReportLab canvas — invariant=True, explicit producer, header outside the raster rect — in .../corpus/render.py
- [ ] T040 [US3] {FR-019,FR-020,FR-024,FR-025} Implement the generator in src/model/src/model/corpus/generate.py — roster only via read_roster(), roster_hash verbatim, pre-write coverage assertions
- [ ] T041 [US3] {FR-009,FR-009b,FR-017a,FR-031} [COMPLETES FR-017a] Emit the five per-project manifests from .../corpus/generate.py — seven SYNTHETIC fields, generation_inputs digests, classes
- [ ] T042 [US3] {FR-009a,FR-009b,FR-016,FR-020} Roster and generation-input drift group in .../corpus/validate.py — VR-029, VR-030, VR-061 (3 of 72), naming every stale document
- [ ] T043 [US3] {FR-023a,FR-027,FR-032a} Author data/corpus/synthetic/datasheet.md and add the datasheet group in .../corpus/validate.py — VR-051…055 (5 of 72)
- [ ] T044 [US3] {FR-022} Offline and boundary group — VR-043 socket guard, VR-044 negative fixture in tests/fixtures/corpus_offline/, VR-045 roster import site (3 of 72)
- [ ] T045 [US3] {FR-021} [COMPLETES FR-021] Determinism tests in src/model/tests/test_corpus_generate.py — VR-040a stability against the committed manifest, VR-040b sensitivity (2 of 72)
- [ ] T046 [US3] {FR-006b,FR-021a} [COMPLETES FR-006b] Byte-identity tests in src/model/tests/test_corpus_generate.py — VR-041 re-render, VR-042 manifests into tmp_path (2 of 72)
- [ ] T047 [US3] {FR-023,FR-024,FR-025,FR-026} [COMPLETES FR-026] Content and population tests in src/model/tests/test_corpus_generate.py — VR-034, VR-046, VR-047, VR-048 (4 of 72)

**Notes**: **The committed layer is written once, at T056 in Phase 6, not here.** Generating a clean layer at the end of this phase and regenerating it after the US4 injectors land would commit ~30 PDFs twice into a repository that deliberately ships the corpus without large-file indirection (spec §Excluded, SC-014), leaving a dead binary blob per document in history permanently. US3 stays independently testable without that commit: T045, T046, and T047 run the full pipeline into `tmp_path` and assert every US3 scenario there, and T042's drift rules run against the emitted tree. If delivery stops at P1, run T056 with the injector set empty. T036 depends on T021 because FR-026 requires every equipment category to map to a section the vendored real layer actually holds — the real layer must exist first. T045's two runs differ in six named dimensions — absolute checkout path, process, `PYTHONHASHSEED`, non-UTC `TZ`, non-C `LC_ALL`, shuffled directory enumeration — so the criterion is not satisfiable by running one command twice in one directory; T046 writes into a temporary tree, never the working copy, or the byte comparison compares a file against itself. T044's socket guard is installed **before** the generator package is imported, so an import-time fetch is inside the observation window.

---

## Phase 6: US4 - Keep the Synthetic Layer Honestly Messy (Priority: P2)

- [ ] T048 [US4] {FR-030} Implement the closed five-value class enum and the four structural injectors in src/model/src/model/corpus/irregularity.py → exports: IrregularityClass, inject()
- [ ] T049 [US4] {FR-032} Implement Pillow page-image degradation with the retained invisible text layer in src/model/src/model/corpus/degrade.py → exports: degrade_page()
- [ ] T050 [US4] {FR-001a,FR-031a} Implement pdfplumber structural re-derivation with explicitly pinned word tolerances in src/model/src/model/corpus/derive.py → exports: derive_classes()
- [ ] T051 [US4] {FR-031a} Author hand-written deriver fixtures in src/model/tests/test_corpus_derive.py — one positive and one negative document per structural class, fixture vocabulary after:T050
- [ ] T052 [US4] {FR-031b,FR-032} Injector unit tests in src/model/tests/test_corpus_irregularity.py — VR-050 (1 of 72), control-page oracle across the declared parameter domain
- [ ] T053 [US4] {FR-030,FR-031,FR-031a} [COMPLETES FR-031a] Structural re-derivation group in .../corpus/validate.py — VR-031…033, VR-035a…035d, VR-036 (8 of 72)
- [ ] T054 [US4] {FR-032,FR-033} [COMPLETES FR-032] Citation-anchor group in .../corpus/validate.py — VR-037, VR-038, VR-039 (3 of 72)
- [ ] T055 [US4] {FR-029} Layout-variety assertion in src/model/tests/test_corpus_generate.py — VR-049 (1 of 72): ≥2 template ids, none spanning all twelve vendors
- [ ] T056 [US4] {FR-028,FR-030} [COMPLETES FR-030] Run corpus-generate once with injection enabled and commit 25–30 PDFs and five manifests under data/corpus/synthetic/PRJ-001…005/ after:T052

**Notes**: T056 is the single generation run this epic performs — the reason Phase 5 stops short of committing output. T050's tolerances (`x_tolerance`, `y_tolerance`, `keep_blank_chars`, `use_text_flow`) come from one module-level constant, never library defaults, and pdfplumber is pinned exactly in the lockfile the way the renderer is: the derived set is the oracle T053 judges the recorded set against, so a tolerance change is a change to the oracle, not a library upgrade. T051 exists because set equality over the committed layer alone is satisfied by a deriver that echoes what the entry recorded; its vocabulary is a fixture rather than the committed file, which is what makes the derivation independent of the injector. T052's oracle is the same page rendered with degradation disabled — body raster differs, extracted text layer identical, citation anchor outside every raster rectangle — asserted across the injector's declared parameter domain rather than over the pages the committed layer happens to hold. Rule totals: 1 + 8 + 3 + 1 = 13.

---

## Phase 7: US5 - Verify Pull Requests Automatically (Priority: P2)

- [ ] T057 [US5] {FR-034,FR-034a} Add the `pull_request` trigger against the default branch to .github/workflows/verify.yml, keeping `permissions: contents: read`
- [ ] T058 [US5] {FR-034,FR-034a} Assert the three trigger properties in tests/checks/test_workflow_triggers.py — pull_request declared, pull_request_target absent, contents: read after:T057
- [ ] T059 [US5] {FR-035} Extend tests/checks/test_contract_fixtures.py so every contract's negative fixture reports a failing check inside the pull-request run, naming the contract

**Notes**: SC-022's evidence is split three ways and only two halves are committable. T058 is the checkable half — a file assertion needing no run. T059 carries the failing-check half through fixtures that execute inside the pull-request run rather than under a manual dispatch nobody is obliged to trigger. The "a run happened" half is this epic's own pull-request run, recorded by URL in the QC report. `pull_request_target` is asserted **absent** deliberately: it runs the base ref's workflow with base-repository token and secret access while checking out head content, which is a different execution surface from the one FR-034a states.

---

## Phase 8: Polish & Cross-Cutting Concerns

- [ ] T060 Run corpus-validate over the complete committed corpus and confirm a green run reporting each rule's observed population size after:T056
- [ ] T061 Confirm the coverage gate at the repository root and `model.corpus` measured alone, both at or above 80% (SC-026) after:T056
- [ ] T062 Enumerate this epic's added distributions from src/model/uv.lock and confirm exact pins, artifact hashes, default public index, and redistributable licences (SC-027, SC-028)
- [ ] T063 Sweep all 72 numbered validation rules for a failing-direction case naming the rule id (SC-025), across the eighteen rule-carrying tasks after:T055

**Notes**: T061's second half is the point — an already-covered package must not carry an uncovered new one across the aggregate threshold. T062 enumerates from the lockfile's own transitive closure, not from a hand-written list: `charset-normalizer`, `cryptography`, `rfc3339-validator`, and the non-GPL URI validator are examples of the review surface, not the closure. Neither T062 nor SC-027 asserts install-time artifact-hash verification; that is a project-level installer posture and a disclosed exposure. T063's eighteen rule-carrying tasks are T025, T027–T033 (40 rules), T042–T047 (19), T052–T055 (13).

---

## Validation Rule Coverage (72 numbered rules, no task per rule)

| Task | Group | Rules | Count |
|------|-------|-------|-------|
| T025 | Reporting and non-vacuity | VR-056, VR-057, VR-066 | 3 |
| T027 | Schema conformance and field sets | VR-001…003, 014…017, 027, 058, 063 | 10 |
| T028 | Location topology and file↔entry reconciliation | VR-004…007, 010, 011, 013, 059, 060, 064, 065 | 11 |
| T029 | Path containment, links, case collision | VR-009, 067, 068 | 3 |
| T030 | Digest recomputation | VR-012, 018 | 2 |
| T031 | License basis | VR-008, 023, 024, 028 | 4 |
| T032 | REAL field values | VR-019…022 | 4 |
| T033 | Policy agreement and exclusion ledger | VR-025, 026, 062 | 3 |
| T042 | Roster and generation-input drift | VR-029, 030, 061 | 3 |
| T043 | Datasheet | VR-051…055 | 5 |
| T044 | Offline and boundary contracts | VR-043, 044, 045 | 3 |
| T045 | Document-model determinism | VR-040a, 040b | 2 |
| T046 | Byte identity | VR-041, 042 | 2 |
| T047 | Content and population | VR-034, 046, 047, 048 | 4 |
| T052 | Injector evidence | VR-050 | 1 |
| T053 | Structural re-derivation | VR-031…033, 035a…035d, 036 | 8 |
| T054 | Citation anchor | VR-037, 038, 039 | 3 |
| T055 | Layout variety | VR-049 | 1 |
| | **Total** | | **72** |

---

## Requirements and Exposures With No Implementation Task

Recorded so a later audit reads these as decided rather than missed.

| Item | Why no task | Owner |
|------|-------------|-------|
| **FR-036** — loosening-direction sign-off on `manifest.schema.json`, `retrieval-policy.json`, `exclusions.json`, and FR-009b's enumeration | An **assigned control, not a mechanical one**. No committed check observes the sign-off and none is claimed. A `CODEOWNERS` file or platform review rule is deliberately not used — it would move the control outside the repository boundary and duplicate the branch-protection deviation. The sign-off is given in the pull request that lands this epic, which lands all three files | Epic owner |
| FR-001 — bytes equal the **published** bytes | No offline check can establish it; VR-018 is internal consistency, not provenance proof | Repository administrator, via FR-008b before each release tag |
| FR-008a / FR-008c — the equality's force, and trust on first use | The validator cannot tell a digest recorded at retrieval from one back-filled out of the committed file, and at first retrieval there was no pre-recorded digest to compare against at all | Retrieval procedure; FR-008b's re-fetch |
| FR-004 / SC-003 — ledger *completeness* | A candidate dropped without a record leaves no artifact. T020 and T033 cover the ledger's integrity only | Epic owner |
| FR-005 / FR-011 — point-of-use copyright check | A recorded human judgement nothing re-derives. The enum makes it stated and closed, not verified | Retriever; epic owner accountable |
| FR-002 — "weighted toward" long-lead equipment | Carries no threshold; the judgement lives in the committed `target_sections` with per-section justification and is reviewable, not checkable | Epic owner |
| FR-031b — that *this* document is visually degraded | Generator-asserted; VR-036 is a necessary condition only, VR-050 evidences the injector | — |
| FR-031a — the shared `field-label-vocabulary.json` | Injector and deriver both read it; T051's fixtures stand in for a second vocabulary, but a mistake common to both is unobservable | — |
| FR-009b — enumeration currentness | Nothing reads the generator's source to confirm the list still equals what it opens; assigned to FR-036 | Epic owner |
| FR-008b — currentness and cadence | Excluded from the per-push run by design; nothing committed observes that the pre-release run happened. The release record is the only evidence | Repository administrator |
| SC-027 — install-time artifact-hash verification | `uv lock --check` and `uv sync --locked` compare a lockfile against a manifest, never fetched bytes against a digest. Hash-checked installation is a project-level installer posture | Project level |
| Commit-SHA pinning of `verify.yml`'s third-party actions | A repository-wide supply-chain posture with ongoing renovation cost, decided out of scope in this epic. Reverses when any workflow gains a secret or a write-scoped token | Repository administrator |
| Branch protection on the default branch | A hosting-platform setting no committed artifact can assert | Repository administrator |

---

## Dependencies

Setup → Foundational → US1 → US2 → US3 → US4 → US5 → Polish

- **T008 before T009** is a gate, not a preference (HINT-001): the red commit is a fact in the branch history, not a claim.
- **T021 before T036** — FR-026 requires every synthetic equipment category to map to a section the vendored real layer holds.
- **T052 before T056** — the single generation run happens with the injectors in place, so the committed layer is written once.
- **T056 before T060** — corpus-validate's entry criterion is a complete corpus: six locations, five synthetic in bijection with the roster's projects, ≥20 REAL and ≥25 SYNTHETIC. A partial checkout fails as a zero population under VR-066 rather than passing silently.
- Tasks marked `[P]` touch disjoint files and carry no dependency on another task in their batch.
- `verify.yml` is edited in three phases (T005, T034, T057); each edit is additive and none reverts another.
