# Tasks: Document Ingestion and Extraction

**Input**: Design documents from `specs/00006-document-ingestion-and-extraction/`
**Prerequisites**: `plan.md`, `spec.md`, `data-model.md`, `research.md`, `checklists/` (data-integrity, testing, observability — all evaluated)

**Tests**: Included. `plan.md` §Testing Strategy makes tests part of the deliverable, and §The test-first boundary makes strict red-green-refactor **mandatory** for every module under `model/compute/` — `confidence.py`, `coerce.py`, `metrics.py`. Each appears as an ordered pair whose test task precedes its implementation task and whose completion condition is an **observed failure**, never a green suite. Every other module in this feature is test-after.

**Organization**: Grouped by user story (`US#`) per `spec_type: product`. Requirement tags are `FR-###`. `plan.md` §Requirement Coverage Map is the authority for the requirement → component → file assignment below; `data-model.md` §Write Order fixes the per-document statement sequence the writer tasks implement.

**Size**: 92 tasks over 74 requirements and six stories. This exceeds the skill's 5–10-per-phase target in US1 and US2; the epic is not split because every phase reads and writes the same six tables through one transaction boundary, and a sub-feature cut would put that boundary in two workspaces.

## Project Mode

`Brownfield`

E001 scaffolded the four entries, `import-linter`, and the Compose `db` service; E002 committed the corpus, its manifests, and `corpus/derive.py`'s pinned reader; E003 owns the six tables this epic populates and the migration runner; E004 owns the traced path and its fixture discipline. No generic project-initialization tasks appear here. `~` paths in `plan.md` §Project Structure are extensions of files that already exist.

## Epic / Capability Map

- `[US1]` → Corpus becomes citable chunks — manifests, document records, one page reader, the three-class boundary ladder, the committed ONNX encoder, total page containment, the report's corpus figures (P1)
- `[US2]` → Every extracted value points at its page — traced extraction, vocabulary bounds, as-printed storage, deterministic coercion, line-item grouping, the reference set, metrics and the honest baseline (P1)
- `[US3]` → An untrustworthy value is absent, not wrong — validate-then-one-repair, computed confidence, the declared floor, the closed seven outcomes, the attempt ledger (P1)
- `[US4]` → A value split across a page break keeps both pages — contributing-chunk rows, the anchor rule, the published multi-chunk counts (P2)
- `[US5]` → Every value names the run that produced it — the run record, the three run-output associations, generations, figure labels (P2)
- `[US6]` → Re-ingesting is safe and repeatable — per-document transactions, the input tuple, run-level failure, offline replay, the three operator runbooks, reproduction (P3)

## Brownfield Notes

- **Existing flows touched**: `src/model/src/model/corpus/derive.py` (the one committed reader — called, never re-implemented), `corpus/paths.resolve_within`, `corpus/manifest.py`, `src/model/pyproject.toml`, `tests/checks/test_migration_ranges.py`, `.github/workflows/verify.yml`, E003's `src/model/src/model/schema/versions/`
- **Compatibility and migration concerns**: revisions are confined to `0300`–`0399` and chain from E003/E004's head `0103`; **zero columns, constraints, or indexes** are added to the six E003-owned tables (FR-065, VR-015); the promotion removal and the correction procedure run under the schema-owning role, never from the job (FR-041, FR-055)
- **Ordering constraints that shape the phases**:
  - **FR-047 gates everything.** T001 verifies E003's TR-081 amendment is on `main`, and T009 — the first migration — declares `after:T001`. The gate reaches a delivery phase only through a **declared** edge, never by argument: the first row-writing task of each phase names a revision, and T031 is US1's (`after:T013`). It is a task, not a note, because a prose blocker is one nobody can mark done.
  - **Strict test-first for `model/compute/*`** (`plan.md` §The test-first boundary): T044→T045 (`coerce.py`), T049→T050 (`metrics.py`), T056→T057 (`confidence.py`). The test task is complete only when its suite has been run against the absent module and observed to fail; neither pair is ever `[P]`.
  - **Migration chain**: `0300` → `0301` → `0302` → `0303` → `0304` is a hard order — each later revision carries a composite FK to a key an earlier one creates (`data-model.md` §Migration Sequence).
  - **AD-013** — claiming block `0300`–`0399` is a **three-part** edit to `tests/checks/test_migration_ranges.py` (T003). The one-line `BLOCKS` append turns CI red three ways.
  - **HINT-004** — split on the page boundary *before* the structural ladder (T022), or a clean structural split straddles a page and violates the scalar `page_number`.
  - **HINT-002** — the per-document error handler catches **outside** the `with conn.transaction()` block, and what it writes afterwards is a run-level failure on `ingestion_run`, never an `extraction_failure` row (T075, T077).
- **Regression focus**: `tests/checks/test_migration_ranges.py` stays green with two new blocks declared; the existing computation-boundary and single-provider-import contracts keep passing as `model.llm` and `model.ingest` grow; the root `coverage combine` gate stays at or above 80% with three new packages inside its `--source` enumeration.

---

## Phase 1: Setup (Repository / Workspace Delta)

**Lands the gate (T001), the block claim (T003), the contracts, and the coverage wiring before any module or migration they constrain. T003 in particular precedes T009, the first `03xx` revision — see §Dependencies.**

- [X] T001 {FR-047} Confirm E003's TR-081 amendment has landed on main and record the verifying revision in specs/00006-document-ingestion-and-extraction/plan.md
- [X] T002 {FR-051} Verify specs/adrs/0018, 0019, and 0020 are committed and record the ADR and migration-block claim before implementation (SC-034)
- [X] T003 {FR-040} Amend tests/checks/test_migration_ranges.py 3 ways (AD-013): BLOCKS += E005 200-299, E006 300-399; populated assert splits reserved-empty vs claimed; "0200" control -> "0400"
- [X] T004 [P] Add onnxruntime, tokenizers, pysbd, and the pgvector psycopg adapter plus the ingest console script to src/model/pyproject.toml
- [X] T005 [P] Append ingest,llm,compute to the coverage --source enumeration and add a per-package 80% floor for each in .github/workflows/verify.yml
- [X] T006 {FR-048} Verify src/model/pyproject.toml's model.llm forbidden contract needs no edit — plant a model.llm.<new> import of model.compute and observe lint-imports reject it (AD-001)
- [X] T007 {FR-023} Add the placement check — only model.llm may import gateway — with a seeded violation in tests/checks/test_model_facing_placement.py after:T006
- [X] T008 {FR-050} Forbid model.ingest.baseline from model.corpus templates/render/model via [[tool.importlinter.contracts]] type=forbidden, allow_indirect_imports=false in src/model/pyproject.toml

---

## Phase 2: Foundational (Cross-Work-Item Blockers)

**The seven owned objects and the view. Every delivery phase writes through them, so they are lifted here rather than into US5, which would otherwise make P1 unbuildable.**

- [X] T009 {FR-038,FR-040} Author revision 0300_ingestion_run in src/model/src/model/schema/versions/ — agent-id grammar, floor/weight CHECKs, five failure kinds after:T001
- [X] T010 {FR-055,FR-043} Author revision 0301_ingestion_run_document with its single-active partial index, document index, and v_active_ingestion_generation after:T009
- [X] T011 {FR-039} Author revision 0302 — three run-output associations, their generation indexes, and the redundant value UK — in src/model/src/model/schema/versions/ after:T010
- [X] T012 {FR-059,FR-063} Author revision 0303 — extracted_value_line_item and extracted_value_parse_signal with their indexes and composite FKs — after:T011
- [X] T013 {FR-066} Author revision 0304 — grants, revoke UPDATE/DELETE on the six append-only tables, revoke DELETE on ingestion_run — after:T012
- [X] T014 {FR-065} Assert the six E003-owned tables' catalog entries are identical at 0103 and at head in src/model/tests/schema/test_table_ownership.py after:T013
- [X] T015 {FR-040} [COMPLETES FR-040] Verify apply-from-empty, re-apply no-op, single head, 03xx prefixes, and the object inventory in src/model/tests/schema/test_ingestion_migrations.py after:T013

---

## Phase 3: US1 - Corpus Becomes Citable Chunks (Priority: P1) 🎯 MVP

- [X] T016 [P] [US1] {FR-001,FR-005} Read the committed manifests and hash-verify each file in src/model/src/model/ingest/manifest_reader.py → exports: iter_entries, verify_hash
- [X] T017 [US1] {FR-002,FR-006,FR-052} Mint ids, classify the closed type set, abort on a corpus-wide collision in src/model/src/model/ingest/documents.py → exports: mint_document_id
- [X] T018 [US1] {FR-003,FR-004} Attach real specifications to PRJ-000 and carry layer, licence basis, and layer-appropriate provenance unchanged in ingest/documents.py after:T017
- [X] T019 [US1] {FR-007,FR-008} Derive pages and page text only through corpus.derive's committed reader in src/model/src/model/ingest/parse.py → exports: read_pages
- [X] T020 [P] [US1] {FR-008} Assert the ingest package declares no second tolerance map, normalization, or page-text assembly in src/model/tests/ingest/test_single_page_reader.py
- [X] T021 [P] [US1] {FR-014} Count content word pieces against the 254 budget and add the pinned pySBD split in ingest/tokens.py and segment.py → exports: content_pieces, sentences
- [X] T022 [US1] {FR-012,FR-013} Detect UFGS structure and transmittal field blocks, splitting on the page break before the ladder, in ingest/structure.py (HINT-004) after:T019
- [X] T023 [US1] {FR-012,FR-014} Cut the three boundary classes and descend article→paragraph→subparagraph→sentence in ingest/chunker.py after:T022 ← T021:content_pieces
- [X] T024 [US1] {FR-015,FR-016} Assign contiguous zero-based ordinals in reading order and keep bracketed markup verbatim in src/model/src/model/ingest/chunker.py after:T023
- [X] T025 [US1] {FR-014,FR-017} [COMPLETES FR-014] Record the chunker version and fail the run only on an over-long single sentence in ingest/chunker.py after:T024
- [X] T026 [US1] {FR-017} Assert identical boundaries across two chunkings differing in process, hash seed, cwd, and enumeration order in src/model/tests/ingest/test_determinism.py
- [X] T027 [P] [US1] {FR-019} Export the encoder to ONNX FP32, vendor it with its tokenizer and recorded digests under data/encoder/, and commit the two-layer parity probe set (AD-014)
- [X] T028 [US1] {FR-019} Verify the committed encoder and tokenizer digests before the session is created, failing rather than fetching, in ingest/artifacts.py after:T027
- [X] T029 [US1] {FR-019} Implement the ONNX Runtime session with attention-masked mean pooling and L2 normalization in ingest/embed.py after:T028 → exports: embed_chunks
- [X] T030 [US1] {FR-019} [COMPLETES FR-019] Assert pre-declared cosine ≥ 0.999999 and max per-dim ≤ 1e-5 over the probe set in src/model/tests/ingest/test_encoder_parity.py ← T029:embed_chunks
- [X] T031 [US1] {FR-020,FR-021} Record the embedding identity and revision on every chunk and read the vector dimension from schema_constants in ingest/writer.py after:T013
- [X] T032 [US1] {FR-010} Re-check chunk-text containment inside each document's transaction before it commits in src/model/src/model/ingest/writer.py after:T031
- [X] T033 [US1] {FR-010} Assert containment for every chunk against a fresh post-run read, publishing its population, in src/model/tests/ingest/test_page_attribution.py after:T032
- [X] T034 [US1] {FR-071,FR-068} Build the closed-content-list report, failing on a missing item and on an empty population, in ingest/report.py after:T032 → exports: build_report
- [X] T035 [US1] {FR-009,FR-018} Publish the per-layer zero-recognition-error bound and the chunk-identity contract in src/model/src/model/ingest/report.py after:T034
- [X] T036 [US1] {FR-011} Publish the enumerated human-inspection claim set with inspected and defect counts and the 3/n or Wilson bound in ingest/report.py after:T034
- [X] T037 [US1] {FR-053} Publish leaf-length distribution, sentence-split count, boundary-class counts, and page-terminal documents per layer in ingest/report.py after:T034
- [X] T038 [US1] {FR-061} Publish near-duplicate cluster counts by cause as exact matches and at the declared grid 0.80–0.99 in ingest/report.py after:T034
- [X] T039 [US1] {FR-068} Assert every total check names its population and count and that an empty population fails in src/model/tests/ingest/test_total_checks.py after:T034

---

## Phase 4: US2 - Every Extracted Value Points At Its Page (Priority: P1) 🎯 MVP

- [ ] T040 [US2] {FR-023} Create the extraction module — the only module in the repository importing gateway — in src/model/src/model/llm/extraction.py → exports: extract_fields
- [ ] T041 [P] [US2] {FR-024,FR-058} Bound field names to unretired vocabulary terms and declare the transmittal field subset in src/model/src/model/llm/schemas.py and prompts.py
- [ ] T042 [US2] {FR-022} Restrict extraction to the 25 synthetic transmittals and record the 26-specification exclusion in src/model/src/model/ingest/cli.py after:T040
- [ ] T043 [P] [US2] {FR-027,FR-028} Store manufacturer and part number as printed with no normalized twin; assert no identity claims in src/model/tests/ingest/test_no_identity_claims.py
- [ ] T044 [US2] {FR-049} Write failing property tests for coercion round-trip and metamorphism in src/model/tests/compute/test_coerce.py — done only on an observed collection error
- [ ] T045 [US2] {FR-049,FR-062} Implement deterministic numeric and date coercion, printed text kept as the evidence, in compute/coerce.py after:T044 → exports: coerce_value
- [ ] T046 [US2] {FR-029} Inherit the cited page from the source chunk and anchor a page-split value on the chunk printing the value in ingest/writer.py after:T032
- [ ] T047 [US2] {FR-059} Group values by run, document, and item ordinal, with ordinal 0 for document-scoped values, in ingest/lineitems.py after:T012 → exports: group_line_items
- [ ] T048 [P] [US2] {FR-067} Reproduce the pre-render document model from committed generation inputs and check it against the manifest digest in ingest/reference.py
- [ ] T049 [US2] {FR-060} Write failing property tests for the continuity-corrected Wilson interval in src/model/tests/compute/test_metrics.py — done only on an observed collection error
- [ ] T050 [US2] {FR-060} Implement precision, recall, and continuity-corrected Wilson intervals — no F1 — in compute/metrics.py after:T049 → exports: wilson_interval
- [ ] T051 [US2] {FR-050} Author the deterministic per-vendor template baseline from rendered text only in src/model/src/model/ingest/baseline.py after:T008
- [ ] T052 [US2] {FR-050} [COMPLETES FR-050] Record the declared baseline label before any figure, read the observed label off the table, publish disagreement in ingest/report.py after:T051
- [ ] T053 [US2] {FR-060} [COMPLETES FR-060] Publish per-field per-layer figures with denominators beside the baseline, the real layer not measured, in ingest/report.py ← T050:wilson_interval
- [ ] T054 [US2] {FR-070} Issue every invocation under one run-scoped trace id and reconcile attempted against recorded counts in ingest/cli.py and report.py after:T042
- [ ] T092 [US2] {FR-023} Cover llm/extraction.py's gateway invocation path and the schemas and prompts it imports in src/model/tests/llm/test_extraction.py after:T040

---

## Phase 5: US3 - An Untrustworthy Value Is Absent, Not Wrong (Priority: P1) 🎯 MVP

- [ ] T055 [US3] {FR-025,FR-026} Validate every model output against the caller's schema and attempt at most one repair, then fail closed, in llm/extraction.py after:T040
- [ ] T056 [US3] {FR-030,FR-031,FR-057} Write failing tests over all eight signal combinations in src/model/tests/compute/test_confidence.py — done only on an observed collection error
- [ ] T057 [US3] {FR-030,FR-031,FR-057} Compute confidence as 1.0 less deductions applied alternate → page-split → repair in compute/confidence.py after:T056 → exports: compute_confidence
- [ ] T058 [US3] {FR-032,FR-057} [COMPLETES FR-057] Record the declared floor 0.80 and the three deduction weights on the run row before the first document in ingest/runs.py after:T009
- [ ] T059 [US3] {FR-063} Write one parse-signal row per stored value carrying label form, source chunk count, and repair flag in ingest/writer.py after:T012
- [ ] T060 [US3] {FR-063} [COMPLETES FR-063] Recompute every stored confidence from its signal row and its own run's weights in src/model/tests/schema/test_parse_signals.py ← T057:compute_confidence
- [ ] T061 [P] [US3] {FR-034,FR-035,FR-036} Classify failures over the closed seven with the five required fields and no value or confidence in src/model/src/model/ingest/failures.py
- [ ] T062 [US3] {FR-037} Record a field the document does not print as no_value_found once per document, on its lowest-ordinal chunk, in ingest/failures.py after:T061
- [ ] T063 [US3] {FR-069} Keep the attempt ledger and resolve every attempt to a stored value or a failure, naming its unit, in ingest/cli.py and report.py after:T061
- [ ] T064 [US3] {FR-033,FR-046} Publish the floor, all eight scores with stored and rejected counts, the weights, and their order in ingest/report.py after:T058
- [ ] T065 [US3] {FR-034} Publish the failure count broken down by each of the seven outcomes, zeros included, in src/model/src/model/ingest/report.py after:T061

---

## Phase 6: US4 - A Value Split Across A Page Break Keeps Both Pages (Priority: P2)

- [ ] T066 [US4] {FR-029} Write one contributing-chunk row per additional page, the anchor never appearing among them, in ingest/writer.py after:T046
- [ ] T067 [US4] {FR-029} Publish the count of multi-chunk values and of their contributing-chunk rows in src/model/src/model/ingest/report.py after:T066
- [ ] T068 [US4] {FR-029} [COMPLETES FR-029] Assert the seeded page-split value cites the page printing it and reassembles in page order in src/model/tests/ingest/test_page_split.py after:T066

---

## Phase 7: US5 - Every Value Names The Run That Produced It (Priority: P2)

- [ ] T069 [US5] {FR-038} Write the run record with the composite principal-and-build agent identity and a finish only on completion in ingest/runs.py after:T009 → exports: write_run_record
- [ ] T070 [US5] {FR-039} Insert the three run-output associations in the document transaction and hold value-level rows' run and document equal in ingest/writer.py after:T011
- [ ] T071 [US5] {FR-039} [COMPLETES FR-039] Anti-join every chunk, value, and failure against its association corpus-wide in src/model/tests/schema/test_run_attribution.py after:T070
- [ ] T072 [US5] {FR-055} Mark the prior generation superseded and remove it leaf-up before inserting the successor as active in ingest/runs.py (HINT-003) after:T069
- [ ] T073 [US5] {FR-055} Assert one active generation per document, zero superseded rows at commit, and zero rows left after a promotion in src/model/tests/schema/test_generations.py after:T072
- [ ] T074 [US5] {FR-072} Label every published figure with its run, generation set, kind, unit, and layer, naming the run record by identifier, in ingest/report.py after:T069

---

## Phase 8: US6 - Re-Ingesting Is Safe And Repeatable (Priority: P3)

- [ ] T075 [US6] {FR-054,FR-042} Commit one document per transaction on an autocommit connection in the stated 0a–7 order in ingest/writer.py (HINT-002) after:T070
- [ ] T076 [US6] {FR-043} Compute the per-document input tuple digest, skip unchanged documents, and reload only those that differ in ingest/runs.py after:T075
- [ ] T077 [US6] {FR-056} Record a run-level failure from the closed five after the rollback, in a fresh transaction, in ingest/cli.py and runs.py after:T075
- [ ] T078 [US6] {FR-056} Assert the five run-level kinds and the seven per-field outcomes are disjoint by reading both CHECK bodies in src/model/tests/schema/test_failure_domains.py after:T077
- [ ] T079 [US6] {FR-044,FR-045} Add the offline ingest console entry in record and replay modes, reaching no network, in src/model/src/model/ingest/cli.py after:T054
- [ ] T080 [US6] {FR-044} Assert no ingestion module is reachable from a request-serving entry point in tests/checks/test_ingest_offline_only.py after:T079
- [ ] T081 [US6] {FR-045} Commit the extraction fixtures and document the prompt- and schema-digest re-record trigger in src/model/fixtures/ and src/model/README.md after:T079
- [ ] T082 [US6] {FR-041} Document the whole-document remove-and-reload correction under the schema-owning role in src/model/README.md after:T075
- [ ] T083 [US6] {FR-064} Document the HNSW index drop and rebuild, the sequential-scan window, and abort recovery in src/model/README.md and ingest/report.py after:T079
- [ ] T084 [US6] {FR-055} [COMPLETES FR-055] Document promotion-with-removal under the schema-owning role, and that a first-ingest run stays unattended, in src/model/README.md after:T072
- [ ] T085 [US6] {FR-066} Assert the thirteen privilege refusals under SET LOCAL ROLE procurement_app in src/model/tests/schema/test_privileges.py after:T013
- [ ] T086 [US6] {FR-073} Publish the four-way per-document disposition ledger summing to the enumerated corpus in ingest/cli.py and report.py after:T076
- [ ] T087 [US6] {FR-074} Print the reproduction tolerance beside every figure and emit the committed results manifest in ingest/report.py after:T074

---

## Phase 9: Polish & Cross-Cutting Concerns

- [ ] T088 {FR-074} Add the replay-mode reproduction job comparing a clean-checkout run against the committed results manifest in .github/workflows/verify.yml after:T087
- [ ] T089 {FR-071} Run the full replay pipeline and commit the regenerated specs/00006-document-ingestion-and-extraction/ingestion-report.md after:T087
- [ ] T090 [P] Verify ingest, llm, and compute each reach the 80% per-package coverage floor in the combined report after:T005
- [ ] T091 [P] Verify the four architecture checks stay green — computation boundary, gateway placement, single page reader, baseline independence after:T051

---

## Dependencies

Setup → Foundational → Delivery Work Items (US1 → US2 → US3 → US4 → US5 → US6, by priority) → Polish

- **T001 gates the epic, and every phase reaches it by a declared edge.** FR-047 blocks implementation until E003's TR-081 amendment is on `main`. T009 declares `after:T001` and T010–T013 chain from it. Each delivery phase then names a revision from its **first row-writing task**, so a scheduler honouring only declared edges cannot start a write before the gate has cleared: **T031 `after:T013`** (US1 — the first task touching `ingest/writer.py`), T047 `after:T012` (US2), **T058 `after:T009`** and T059 `after:T012` (US3 — T058 writes the floor and the three deduction weights to `ingestion_run` and is US3's first row-writing task, so naming only T059 left it ancestor-less), T069 `after:T009` and T070 `after:T011` (US5), T075 `after:T070` and T085 `after:T013` (US6). US4 reaches the chain through T066 → T046 → T032 → T031. No migration is authored and no row is written before T001 is `[X]`.
- **The migration-free US1 tasks are migration-free on purpose.** T016–T030 — manifests, id minting, the one page reader, the boundary ladder, the tokenizer and segmenter, the ONNX export and its parity assertion — touch no database and carry no edge into the chain. That is a genuine parallel opportunity against the migration work and must not be serialized by widening T031's edge onto them. T029 → T031 needs no `after:` of its own: both sit in Phase 3 and sequential `T###` ordering within a phase already implies it, which is what leaves T031's single `after:` free to carry the cross-phase edge.
- **Foundational is not optional and is not US5.** The seven owned objects block US1, US2, US3, US4, US5, and US6. Placing them in US5 (P2) would leave the three P1 stories unbuildable, so they are lifted rather than left where their success criteria are labelled.
- **Migration chain**: T009 → T010 → T011 → T012 → T013 is a hard order; each later revision carries a composite FK to a key an earlier one creates (`data-model.md` §Migration Sequence). T014 and T015 verify the set and run after T013.
- **The block claim precedes the first revision it authorises.** T003 declares `0300`–`0399` in `tests/checks/test_migration_ranges.py` (AD-013). It is the first migration-facing task in Setup, and Setup completes before Foundational begins, so T003 precedes T009 by the phase gate rather than by an `after:` — T009's single `after:` is spent on the FR-047 gate (`after:T001`) and this file's grammar admits one `after:` per task. The ordering is recorded here as a requirement, not an accident: a `03xx` revision merged before T003 turns CI red three ways.
- **Mandatory red-green pairs** (`plan.md` §The test-first boundary): T044 before T045, T049 before T050, T056 before T057. Each test task is complete only when its suite has been run against the absent module and **observed to fail** — a collection error for the missing module, recorded on the task line. A test task marked complete beside a passing suite is the defect the condition exists to name. Neither member of a pair is ever `[P]`. T058 consumes T057's weights and sits directly after it in Phase 5, so sequential `T###` ordering within the phase carries that edge and leaves T058's single `after:` free for the cross-phase gate `after:T009` — the same construction T031 uses.
- **Write order is a task, not a convention**: T075 implements `data-model.md` §Write Order steps 0a–7 exactly — mark, capture identifier sets, leaf-up removal, generation row, chunks, values, contributing chunks, failures, run associations, then the line-item and parse-signal rows. T066, T059, T070, and T072 attach to named steps in it and must not reorder them.
- **The three operator procedures are deliverables**: T082 (whole-document correction, FR-041), T083 (index drop and rebuild, FR-064), T084 (promotion with removal, FR-055 / ADR-0020). None is reachable from the ingestion job, which is why each needs a runbook rather than code.
- **Declared edges**: T009→T001, T023→T021, T030→T029, T031→T013, T032→T031, T034→T032, T046→T032, T047→T012, T051→T008, T055→T040, T058→T009, T059→T012, T060→T057, T069→T009, T070→T011, T075→T070, T079→T054, T085→T013, T090→T005, T091→T051, T092→T040. There is no `T031→T029` edge: it was dropped when T031 was redirected to `after:T013`, and within-phase ordering carries T029 before T031 as the bullet above explains.
- **Symbol-import edges gate execution exactly as `after:` does.** T023←T021, T030←T029, T053←T050, T060←T057 carry a `← T###:Symbol` edge; a consumer reading only `after:` under-constrains them, and both forms must be honoured.
- **P1 boundary**: Phases 1–5 (T001–T065, plus T092 at the tail of US2) are the viable deliverable — all 51 documents chunked, embedded, and citable, the 25 transmittals extracted with page citations and computed confidences, and untrustworthy values recorded as failures. Phases 6–9 are omittable without breaking any P1 criterion.
- Tasks marked `[P]` can run in parallel within their phase — they touch distinct files and carry no `after:T###` or `← T###:` edge to another task in the same batch.
- A task with `after:T###` or `← T###:Symbol` must not be `[P]`-batched with the referenced task; the implementing agent must verify the referenced task is `[X]` before executing.
