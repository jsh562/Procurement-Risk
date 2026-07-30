# Tasks: Hybrid Retrieval and Reranking

**Input**: Design documents from `specs/00008-hybrid-retrieval-and-reranking/`
**Prerequisites**: `plan.md`, `spec.md`, `contracts/openapi.yaml`, `research.md`, `research-implementation.md`, `research-quality.md`, `checklists/` (performance, testing, api-quality — all evaluated, 119 items)

**Tests**: Included. The quality policy names **risk arithmetic, fusion ranking and scoring functions** as strict red-green-refactor, and that obligation has two limbs which must be answered separately. `retrieval/metrics.py`'s pure scoring functions answer both directly, by spec FR-042: the Wilson interval, the percentile bootstrap and the overlap verdict each appear as an ordered pair (T041→T042, T043→T044, T045→T046). SQL-resident **fusion ranking** answers them differently. Its *property* limb is discharged by the **pseudo-oracle** of `plan.md` §Testing Strategy under that section's four soundness conditions, because FR-002 leaves the module no pure function to property-test directly. Its *red-green* limb is not excused by that argument and is discharged in the ordinary way — three ordered pairs, T027→T024, T029→T025 and T030→T026, each test written and run against the behaviour its partner has not yet built. Every pair on this list, in both modules, completes on an **observed failure**, never a green suite, and no member of a pair is ever `[P]`. Every other module here is test-after.

**Organization**: Grouped by user story (`US#`) per `spec_type: product`. Requirement tags are `FR-###`. `plan.md` §Requirement Coverage Map is the authority for the requirement → component → file assignment below and is not re-derived; `contracts/openapi.yaml` fixes the response members the route, degraded and reporting tasks emit.

**Size**: 106 tasks over 51 requirements, 16 success criteria and six stories. US1 carries 31, which exceeds the skill's 5–10-per-phase target. The epic is not split: SC-001 and SC-002 are US1 criteria and are measured by `metrics.py` over E008's own frozen set, so cutting the measurement surface into a sub-feature would put a P1 criterion outside the phase that owns it.

## Project Mode

`Brownfield`

E001 scaffolded the four entries, `import-linter`, `tests/checks` and the Compose `db` service; E003 owns the `chunk` table and its full-text and vector indexes; E006 populated 6,391 chunks and vendored the encoder; E010 shipped the api entry's first routed surface and its contract-conformance module. This epic adds **no table, column, index or migration**. `~` paths in `plan.md` §Project Structure extend files that already exist; `src/api/src/api/config.py` and `db.py` do **not** exist yet and are created here.

## Epic / Capability Map

- `[US1]` → Ask a question, reach the passage — the single fusion statement, projection-only results, the query-side encoder, the lexical-arm disclosure, and the metrics and frozen set the recall and MRR criteria are measured with (P1)
- `[US2]` → Type a part number, get that item — the declared pattern, direct lookup, fall-through, and the additive union (P1)
- `[US3]` → The ordering is worth trusting — two reranker graphs, load-once-and-warm before readiness, truncation counted, the latency and memory figures, and the strongest-single-arm comparison (P1)
- `[US4]` → A degraded system says so — ready-degraded, mid-request session loss, the unreranked reason vocabulary, and the forced-failure test (P1)
- `[US5]` → Each arm can be measured on its own — five request-selectable arms and the derived 50/50/50 workload (P2)
- `[US6]` → One flag, index usage only — connection-borne breadth, strict iterative scan, and two-process parity (P2)

## Brownfield Notes

- **Existing flows touched**: `src/model/src/model/ingest/embed.py` (repointed, never re-implemented), `src/model/pyproject.toml`, `src/gateway/pyproject.toml`, `src/api/pyproject.toml`, `tests/checks/helpers/image_contents.py`, `tests/checks/test_dependency_isolation.py`, `tests/checks/test_image_contents.py`, `src/api/src/api/main.py`, `.github/workflows/verify.yml`
- **Patterns to reuse**: `model.ingest.artifacts` for digest-verified loading; `model.ingest.embed`'s masked mean pooling and L2 normalization, moved rather than rewritten; E004's `Resolution.from_environment` for configuration read once; E006's `src/api/tests/fixtures/frozen_run/seed.py` shape for the committed integration fixture
- **Ordering constraints that shape the phases — each is a declared edge, not a note**:
  - **Six amendments gate the epic.** T001–T004, T096 and T097 are the verifiers, each closed by citing the amending revision on the default branch (SC-015). T003 is the one this design cannot proceed without: `project-instructions.md` §Source Code Layout is the clause ADR-0023 contradicts — **and §Testing & Quality Policy is the second clause the same amendment must reach**, because admitting NumPy to `SHARED_INFRASTRUCTURE` breaches its no-modeling-stack-in-the-serving-image assertion by the same reasoning. An amendment naming only the layout clause lets T003 close green with the image assertion still contradicted. T096 and T097 were added at Analyze: T097 closes the `specs/sad.md` twin of T001's `specs/prd.md` defect, which was queued on one document and not the other, and T096 closes the INT8 qualifier that E006 raised in a PR body rather than in the queue. T005, T006, T018, T012, T102 and T101 declare `after:T003`/`after:T004`/`after:T006`. **Not every later chain reaches the gate through them** — 25 tasks do not, as the Dependencies section records; phase order is what covers those, not an edge.
  - **A version check precedes a design commitment.** T012 verifies the `pgvector` extension version in the digest-pinned image before anything depends on iterative scan. Below 0.8.0 the setting does not exist and AD-003's entire filtered-recall approach changes (FR-039, HINT-002). T014 and T087 chain from it.
  - **The two `SHARED_INFRASTRUCTURE` constants are independent copies** — `image_contents.py` for TR-013, `test_dependency_isolation.py` for TR-003/TR-004. T008 declares `after:T007` so they move together; extending one alone fails the build in a way that reads as unrelated. **Only `numpy` is admitted** (HINT-003): the runtime and tokenizer leave the denylist on their own once T005 moves the declarations, and adding them trips `stale = SHARED_INFRASTRUCTURE - declared`. A task implementing ADR-0023 literally fails the build.
  - **`api.retrieval` is the third computation package** (T006). E010's precedent: a boundary that guards one of two is a boundary in name.
  - **The per-arm tie-break goes inside each arm's CTE** (T026, AD-001, HINT-001), not only in the final ordering. Without it a tie at the fiftieth position changes the candidate *set* and the reranker scores different rows between runs.
  - **The search breadth rides on the connection** (T014, AD-002). A per-query `SET` is a second statement and violates FR-002.
  - **The encoder and reranker move to `/src/gateway` before either caller uses them** (T020, T058), and T005 stops `/src/model` declaring the runtime — which is what removes it from the denylist.
- **Three checks that exist and do not cover what you would assume — each gets its own task**:
  - The merge gate runs against an **empty `chunk` table**; `verify.yml` applies migrations and never ingests, and the `reproduce` job aborts before writing chunks by design. T023 commits and seeds the integration fixture.
  - A **benchmark module runs nowhere** unless registered: the api step runs `-m "not benchmark"` and the benchmark step names one file explicitly. T011 registers `test_retrieval_benchmark.py`.
  - **E010's contract-conformance module names its own contract by path**, so E008's would ship unvalidated. T091 adds a second module rather than extending it (AD-014).
- **Regression focus**: `tests/checks/test_dependency_isolation.py` stays green with `numpy` admitted and `heavy` narrowed to `{pymc, arviz, pandas}`, both guards intact rather than silenced; the serving image assertions pass with the runtime admitted; E010's worklist surface and its own conformance module are untouched; the root `coverage combine` gate stays at or above 80% with two new packages in the denominator.

---

## Phase 1: Setup (Repository / Workspace Delta)

**Lands the six amendment gates, the dependency relocation, both mirrored constants, and the two workflow lines before any module they constrain. Nothing below Phase 1 may be started while T001–T004, T096 or T097 are open.**

- [X] T001 {FR-034} Confirm the specs/prd.md MRR-interval amendment is on the default branch and cite the amending revision in plan.md §Pending Amendments item 1
- [X] T002 {FR-035} Confirm the specs/project-plan.md chunk.part_numbers owner amendment is on the default branch and cite the revision in plan.md item 2
- [X] T003 {FR-044} Confirm project-instructions.md excepts a shared inference runtime in BOTH §Source Code Layout and §Testing & Quality Policy's serving-image assertion, and cite the revision in plan.md item 3 (SC-015)
- [X] T004 {FR-045} Confirm specs/sad.md's ADR catalog carries the ADR-0023 row appended after ADR-0022 and cite the revision in plan.md §Pending Amendments item 4 (SC-015)
- [X] T096 {FR-047} Confirm project-instructions.md §Technology Stack no longer restricts ONNX Runtime to INT8 inference and cite the revision in plan.md §Pending Amendments item 10 (SC-015)
- [X] T097 {FR-048} Confirm specs/sad.md's retrieval-quality row no longer specifies a Wilson interval for MRR and cite the revision in plan.md §Pending Amendments item 9 (SC-015)
- [X] T005 Move onnxruntime and tokenizers from src/model/pyproject.toml to src/gateway/pyproject.toml so model inherits them through the gateway (ADR-0023) after:T003
- [X] T006 {FR-002} Add an import-linter forbidden contract naming api.retrieval as source with allow_indirect_imports false, following E010's separate-contract precedent, in src/api/pyproject.toml, and declare the retrieval dependencies after:T003
- [X] T007 Admit numpy alone to SHARED_INFRASTRUCTURE in tests/checks/helpers/image_contents.py, with the reason recorded at the constant (TR-013, HINT-003) after:T005
- [X] T008 Admit numpy, narrow heavy to pymc, arviz, pandas, and remove onnxruntime and tokenizers from DECLARED_BY_THE_MODELING_ENTRY with the reason recorded, in tests/checks/test_dependency_isolation.py (TR-003/TR-004, HINT-003) after:T007
- [X] T101 Keep the pgvector distribution out of src/api/pyproject.toml and bind the query vector as a text-cast parameter, never register_vector, with the reason at the call site (AD-016) after:T006
- [X] T102 Name gateway.inference in the forbidden contract beside gateway.compute in src/gateway/pyproject.toml, following E010's separate-contract precedent (AD-015) after:T003
- [X] T009 Reflect the admitted inference runtime in the serving image's asserted contents in tests/checks/test_image_contents.py after:T008
- [X] T010 {FR-044} Append per-package 80% coverage floors for api/retrieval and gateway/inference to the Coverage gate step in .github/workflows/verify.yml after:T003
- [X] T011 {FR-033} Name src/api/tests/test_retrieval_benchmark.py in the Performance benchmark (api) step under taskset -c 0 in .github/workflows/verify.yml after:T010

---

## Phase 2: Foundational (Cross-Work-Item Blockers)

**The extension-version check, the configuration and connection, the ranking parameters, both vendored graphs, the gateway inference package, and the seeded corpus every integration tier reads. Each blocks three or more stories, so none is left where its success criterion is labelled.**

- [X] T012 {FR-039} Read the pgvector extension version from the digest-pinned image and gate iterative scan on >= 0.8.0 in src/api/src/api/db.py after:T004 → exports: pgvector_version
- [X] T013 {FR-026,FR-038} Add retrieval configuration — index flag, breadth, fetch depth, intra-op and inter-op threads, memory budget — in src/api/src/api/config.py → exports: RetrievalConfig
- [X] T014 {FR-027,FR-040} Carry the search breadth on the connection options, never a per-query SET (AD-002), in src/api/src/api/db.py after:T012 ← T013:RetrievalConfig
- [X] T015 {FR-004} Fix the fusion constant 60, the tie-break key and the missing-arm convention as lowercase identifier tokens in src/api/src/api/retrieval/parameters.py → exports: RankingParameters
- [X] T016 {FR-016} Vendor the INT8 and FP32 reranker graphs with their tokenizer, digests, licence basis and source under data/reranker/ (AD-007, AD-011)
- [X] T017 {FR-016} Write the one-off dynamic quantization emitting the INT8 graph with generator identity, seed, date and source hash, resolving every scratch path through the checkout's own gitignored .tmp/ per AGENTS.md §Temporary Files, in src/model/tools/quantize_reranker.py after:T016
- [X] T018 {FR-016} Verify identity, revision, licence basis, source and digest before session creation in src/gateway/src/gateway/inference/artifacts.py after:T003 → exports: verify_artifact
- [X] T019 {FR-016} [COMPLETES FR-016] Assert licence basis, source, quantization record and digest for both graphs in tests/checks/test_vendored_model_provenance.py after:T018
- [X] T020 {FR-007} Move masked mean pooling and L2 normalization into src/gateway/src/gateway/inference/encoder.py, fed by the truncating tokenizer (HINT-004) after:T018 → exports: embed_texts
- [X] T021 {FR-007} Refuse retrieval when the query encoder identity differs from the identity recorded on the chunks, in gateway/inference/encoder.py after:T020 → exports: assert_encoder_identity
- [X] T022 Repoint src/model/src/model/ingest/embed.py at gateway.inference.encoder so exactly one pooling implementation exists (ADR-0023) after:T021
- [X] T105 Repoint src/model/src/model/ingest/tokens.py at the gateway so model imports no distribution it stops declaring after:T020
- [X] T023 Commit and seed the integration fixture corpus — the merge gate applies migrations and never ingests — in src/api/tests/retrieval/fixtures/seed_chunks.py → exports: seeded_corpus

---

## Phase 3: US1 - Ask A Question, Reach The Passage (Priority: P1) 🎯 MVP

- [X] T027 [US1] {FR-001} Write the pseudo-oracle property tests from the published definition over generated per-arm rank vectors in src/api/tests/retrieval/test_fusion_oracle.py — done only on an observed failure after:T015
- [X] T024 [US1] {FR-001,FR-002} Author the two-CTE reciprocal-rank fusion statement, one full outer join, in src/api/src/api/retrieval/fusion.py after:T027 → exports: fuse_candidates
- [X] T029 [US1] {FR-003} Assert over EXPLAIN that each arm's LIMIT survives CTE inlining and each node reports the fetch depth, in src/api/tests/retrieval/test_fusion_plan_shape.py — done only on an observed failure after:T024
- [X] T025 [US1] {FR-003,FR-037} Fetch 50 candidates per arm and cut the fused ordering to the reranked count of 50 in src/api/src/api/retrieval/fusion.py after:T029
- [X] T030 [US1] {FR-003} [COMPLETES FR-003] Assert the candidate set at the 50-row cut, with a tie engineered at the last in-window position, in src/api/tests/retrieval/test_candidate_set.py — done only on an observed failure after:T025 ← T023:seeded_corpus
- [X] T026 [US1] {FR-004} Apply the tie-break key inside each arm's CTE as well as in the final ordering, in retrieval/fusion.py (AD-001, HINT-001) after:T030
- [X] T028 [US1] {FR-002} [COMPLETES FR-002] Capture the statements one search executes and assert exactly one ranking statement with no SET in src/api/tests/retrieval/test_single_statement.py after:T026
- [X] T031 [US1] {FR-020} Assert identical ordering twice, under seven flipped planner settings whose EXPLAIN plan differs, and across an exact-path rebuild, in src/api/tests/retrieval/test_determinism.py after:T026
- [X] T032 [US1] {FR-008,FR-013} Construct results only by private-factory projection from the chunk row, carrying page and match kind, in retrieval/results.py → exports: RetrievalResult
- [X] T033 [US1] {FR-009} Report an empty result set as empty and never pad a short set to reach a target count, in src/api/src/api/retrieval/results.py after:T032
- [X] T034 [US1] {FR-008} Scan construction sites and assert the private factory is the only one (AD-004) in src/api/tests/retrieval/test_page_provenance.py after:T033
- [X] T104 [US1] {FR-009,FR-013} Assert an empty result set is reported empty, a short set is never padded, and match_kind is carried, in src/api/tests/retrieval/test_results.py after:T033
- [ ] T035 [US1] {FR-005} Publish the per-layer proportion of retrieved chunks whose weighted fields are all empty, in src/api/src/api/retrieval/report.py after:T002 → exports: weighted_field_report
- [ ] T099 [US1] {FR-049} Carry the ingest generation on every published retrieval figure beside the corpus size, in src/api/src/api/retrieval/report.py after:T035 → exports: ingest_generation
- [ ] T106 [US1] {FR-051} [COMPLETES FR-051] Declare the closed no_interval_reason enum in contracts/openapi.yaml, require an interval or a denominator-plus-reason on every emitted figure, and refuse a figure carrying neither, in src/api/src/api/retrieval/report.py after:T099
- [ ] T036 [US1] {FR-006} Assert over the emitted artifacts that no figure or label names BM25 and the no-corpus-statistics statement is present, in retrieval/report.py and src/api/tests/retrieval/test_report.py after:T035
- [ ] T037 [US1] {FR-029} Emit the ranking parameters in force with every result an evaluation consumes, in src/api/src/api/retrieval/report.py after:T015
- [ ] T038 [US1] {FR-004,FR-029} [COMPLETES FR-004] Assert the three parameters are stable identifier tokens read from the one source in src/api/tests/retrieval/test_parameters.py after:T037
- [ ] T039 [US1] {FR-007} [COMPLETES FR-007] Embed the query through the gateway encoder, refusing on identity mismatch before any search, in src/api/src/api/retrieval/routes.py after:T021
- [ ] T040 [US1] {FR-008,FR-009} [COMPLETES FR-008] Wire GET /api/v1/retrieval/search to RetrievalResponse in retrieval/routes.py after:T039 ← T032:RetrievalResult
- [X] T041 [US1] {FR-030,FR-042} Write failing property tests for recall at five and its Wilson interval in src/api/tests/retrieval/test_metrics.py — done only on an observed failure after:T001
- [X] T042 [US1] {FR-030,FR-042} Implement the recall proportion and the Wilson interval in src/api/src/api/retrieval/metrics.py after:T041 → exports: wilson_interval
- [X] T043 [US1] {FR-031,FR-042} Write failing property tests for the percentile bootstrap over the query set in src/api/tests/retrieval/test_metrics.py — done only on an observed failure after:T042
- [X] T044 [US1] {FR-031,FR-042} Implement MRR with a percentile bootstrap at the B and bit generator specs/sad.md fixes, recording resample count, seed and bit generator, in retrieval/metrics.py after:T043 → exports: percentile_bootstrap
- [X] T045 [US1] {FR-032,FR-042} Write failing property tests for the overlap verdict — symmetry, reflexivity, touching endpoints — in src/api/tests/retrieval/test_metrics.py, done only on an observed failure after:T044
- [X] T046 [US1] {FR-032,FR-042} Implement the unresolvable verdict by FR-032's closed-interval rule in src/api/src/api/retrieval/metrics.py after:T045 → exports: overlap_verdict
- [ ] T047 [US1] {FR-031} [COMPLETES FR-031] Record method, resample count, seed and bit generator on every emitted interval and assert no non-proportion statistic carries wilson, in retrieval/report.py and src/api/tests/retrieval/test_metrics.py after:T046
- [ ] T048 [US1] {FR-042} [COMPLETES FR-042] Assert the generated domains reach set size one upward, all-hit, all-miss, ties and endpoints, and refuse an empty set, in src/api/tests/retrieval/test_metrics.py after:T047
- [ ] T049 [US1] {FR-043} Freeze, hash and commit E008's own query set with generator-derived judgements and the published ceiling in src/api/tests/retrieval/evaluation_set/ (AD-010) after:T044
- [ ] T050 [US1] {FR-043} Abort on digest mismatch before emitting any measurement in src/api/tests/retrieval/evaluation_set/harness.py (Principle VI) after:T049 → exports: load_frozen_set
- [ ] T103 [US1] {FR-050} [COMPLETES FR-050] Ship the evaluation set's datasheet — generator identity, seed, document-model digest, draw method, query count, answerable-by-construction ceiling — in data/evaluation_set/DATASHEET.md and assert its presence in tests/checks/test_vendored_model_provenance.py after:T049
- [ ] T051 [US1] {FR-043} [COMPLETES FR-043] Perturb a copy of the committed set and assert the harness exits non-zero before any measurement, and that a re-tune emits the before and after figures together, in src/api/tests/retrieval/test_evaluation_set.py after:T050

---

## Phase 4: US2 - Type A Part Number, Get That Item (Priority: P1) 🎯 MVP

- [ ] T052 [US2] {FR-010,FR-014} Declare the part-number pattern and recognise matching tokens anywhere in the query, in src/api/src/api/retrieval/router.py → exports: recognise_part_numbers
- [ ] T053 [US2] {FR-010} Resolve recognised tokens by direct lookup before hybrid retrieval runs, on the three fused arms only, in retrieval/router.py after:T052
- [ ] T054 [US2] {FR-011} Fall through to hybrid retrieval when the lookup matches nothing, never returning empty on the route alone, in retrieval/router.py after:T053
- [ ] T055 [US2] {FR-012,FR-013} Union route matches additively with a null fused rank and a deterministic match kind, counted outside limit, in retrieval/router.py after:T054
- [ ] T095 [US2] {FR-046} Bound the result array by rule and assert the bound, in src/api/src/api/retrieval/results.py and contracts/openapi.yaml after:T055
- [ ] T056 [US2] {FR-010} [COMPLETES FR-010] Verify the pattern against the part numbers enumerated from the generator's pre-render document model, in src/api/tests/retrieval/test_part_number_coverage.py after:T055
- [ ] T057 [P] [US2] {FR-011,FR-012,FR-014} Assert fall-through, the additive union against the route-disabled result, and arm_excludes_route on the single-arm paths, in src/api/tests/retrieval/test_router.py

---

## Phase 5: US3 - The Ordering Is Worth Trusting (Priority: P1) 🎯 MVP

- [ ] T058 [US3] {FR-015} Create the cross-encoder session scoring the query and each candidate jointly, in src/gateway/src/gateway/inference/reranker.py after:T018 → exports: RerankerSession
- [ ] T059 [US3] {FR-038} Set intra-op from the container's vCPU quota and inter-op to one from configuration, reporting the values in force, in gateway/inference/reranker.py after:T058
- [ ] T060 [US3] {FR-017} Load each graph once per process and warm at batch 50 by the declared sequence limit, reporting the shape and the duration, in reranker.py after:T059
- [ ] T061 [US3] {FR-018,FR-037} Score exactly the top 50 of the fused ordering at the fixed batch shape, in src/gateway/src/gateway/inference/reranker.py after:T060
- [ ] T062 [US3] {FR-019} Truncate at the declared sequence limit and count truncated candidates rather than truncating silently, in reranker.py after:T061 → exports: TruncationReport
- [ ] T063 [US3] {FR-017,FR-025} Load and warm both the INT8 and FP32 sessions before readiness (AD-011, AD-013) in src/api/src/api/retrieval/readiness.py after:T062
- [ ] T064 [US3] {FR-017} [COMPLETES FR-017] Withhold readiness until warm-up completes, inside the lifespan hook (HINT-005), in retrieval/readiness.py and src/api/src/api/main.py after:T063
- [ ] T065 [US3] {FR-015,FR-018,FR-038} [COMPLETES FR-038] Cover load-once, the warmed shape, joint scoring of the fused set and the thread counts, in src/gateway/tests/test_inference.py after:T064
- [ ] T066 [US3] {FR-019} Publish the sequence limit as a number with the candidate-length distribution and the truncated fraction, in retrieval/report.py ← T062:TruncationReport
- [ ] T067 [US3] {FR-033} Report per-query reranking latency with the fusion-statement and encoder times beside it, and per-session RSS against the budget, in retrieval/report.py after:T066
- [ ] T068 [US3] {FR-033,FR-049} Assert the report carries workload, environment, measurement point, occasion, counter, arm, corpus size and ingest generation, in src/api/tests/retrieval/test_performance_report.py after:T067
- [ ] T100 [US3] {FR-049} [COMPLETES FR-049] Assert two figures differing only in ingest generation are distinguishable, since the repair changes no chunk count, in src/api/tests/retrieval/test_report.py after:T099
- [ ] T069 [US3] {FR-033} [COMPLETES FR-033] Take the latency and memory figures under the one-vCPU quota after readiness, in src/api/tests/test_retrieval_benchmark.py after:T067
- [ ] T070 [US3] {FR-020} Assert the reranked ordering repeats identically and SC-007's three counters read zero after readiness, in src/api/tests/retrieval/test_readiness.py after:T064
- [ ] T071 [US3] {FR-036} Select the strongest single arm by the fixed per-statistic rule and compute paired per-query differences against it, in retrieval/metrics.py after:T046
- [ ] T072 [US3] {FR-036} Label fusion-only as the weak comparator and record the selected arm and the statistic that selected it, in src/api/src/api/retrieval/report.py after:T071
- [ ] T073 [US3] {FR-036} [COMPLETES FR-036] Assert the selection rule over constructed figures, including the overlapping-interval case where both are reported, in src/api/tests/retrieval/test_metrics.py after:T072

---

## Phase 6: US4 - A Degraded System Says So (Priority: P1) 🎯 MVP

- [ ] T074 [US4] {FR-021} Catch reranker load failure inside the lifespan hook and still yield, reporting ready-degraded rather than not-ready (HINT-005), in retrieval/readiness.py after:T064
- [ ] T075 [US4] {FR-021} Complete a session lost mid-request as a degraded success, distinguishing reranker_failed_during_request from reranker_unavailable, in retrieval/routes.py after:T074
- [ ] T076 [US4] {FR-021} [COMPLETES FR-021] Report partially_available per session and refuse the unavailable arm explicitly rather than serving the other, in retrieval/readiness.py after:T075
- [ ] T077 [US4] {FR-022} State fusion-only and unreranked in every degraded response body and expose the degraded state at /readyz, in src/api/src/api/retrieval/routes.py after:T076
- [ ] T078 [US4] {FR-022} Carry a machine-readable unreranked reason — arm_excludes_reranking, no_candidates_to_score — without claiming fusion-only, in retrieval/routes.py after:T077
- [ ] T079 [US4] {FR-023} Record the mode a run executed in on every evaluation-facing output, in src/api/src/api/retrieval/report.py after:T078
- [ ] T080 [US4] {FR-024} Force load failure at the artifact-loading boundary and assert ready-degraded, the fusion-only statement with results, and the recorded mode, in src/api/tests/retrieval/test_degraded.py after:T079
- [ ] T081 [US4] {FR-041} Report the degraded path's per-query latency on FR-033's terms and assert it never exceeds the reranked path's over the same query set, in src/api/tests/retrieval/test_degraded.py after:T080

---

## Phase 7: US5 - Each Arm Can Be Measured On Its Own (Priority: P2)

- [ ] T082 [US5] {FR-025} Implement the five request-selectable arms — lexical, dense, fused, fused_reranked, fused_reranked_full_precision — in retrieval/arms.py after:T063 → exports: run_arm
- [ ] T083 [US5] {FR-025} Run lexical-only and dense-only without fusion or reranking, each returning results independently, in src/api/src/api/retrieval/arms.py after:T082
- [ ] T084 [US5] {FR-025} [COMPLETES FR-025] Assert each of the five arms returns independently and identically across two runs on an unrebuilt index, in src/api/tests/retrieval/test_arms.py after:T083 ← T023:seeded_corpus
- [ ] T085 [US5] {FR-037} [COMPLETES FR-037] Assert the derived 50/50/50 constraint over a fused set of up to 100, in src/api/tests/retrieval/test_workload.py after:T084

---

## Phase 8: US6 - One Flag, Index Usage Only (Priority: P2)

- [ ] T086 [US6] {FR-026} Make the exact/approximate flag control index usage only, with filters, fusion, depth and reranking shared, in src/api/src/api/config.py and retrieval/arms.py after:T082
- [ ] T087 [US6] {FR-028} Return the requested candidate count from a filtered vector search under strict iterative scan (AD-003), in src/api/src/api/retrieval/arms.py after:T086
- [ ] T088 [US6] {FR-027,FR-028} Assert the connection-borne breadth is at or above the fetch depth and a filtered query returns the requested count, in src/api/tests/retrieval/test_vector_settings.py after:T087 ← T023:seeded_corpus
- [ ] T089 [US6] {FR-039,FR-040} Record the observed pgvector version and the breadth with the index settings, any value above the floor being a recorded change, in retrieval/parameters.py after:T088
- [ ] T090 [US6] {FR-026} [COMPLETES FR-026] Build two differently configured application instances and compare the observable set FR-026 enumerates, in src/api/tests/retrieval/test_flag_parity.py after:T089 ← T023:seeded_corpus

---

## Phase 9: Polish & Cross-Cutting Concerns

- [ ] T091 Add contract conformance for E008's own contract, following E010's module rather than extending it (AD-014), in src/api/tests/test_retrieval_contract_conformance.py after:T090
- [ ] T092 [P] Verify api/retrieval and gateway/inference each reach the 80% per-package floor in the combined coverage report after:T091
- [ ] T093 [P] Verify the architecture contracts stay green — api.retrieval forbidden, numpy admitted in both copies, the serving image's contents — in tests/checks/ after:T009
- [ ] T098 [P] Extend the scratch-location check to the gateway tier so model download and session creation resolve temporary paths inside the checkout, in src/gateway/tests/test_scratch_location.py after:T018
- [ ] T094 Run the gate measurement over the frozen set and record recall at five, MRR, their intervals, the comparator arm, the mode and the corpus size after:T091

---

## Dependencies

Setup → Foundational → Delivery Work Items (US1 → US2 → US3 → US4 by P1, then US5 → US6 by P2) → Polish

- **The six amendment gates precede everything.** Each phase's first module-creating task declares the edge, and within a phase sequential `T###` order carries it — the convention stated at the foot of this section. **The claim is that shape, not that every task names a gate**: 25 do not, including T015's fusion chain, and an earlier draft of this line asserted otherwise. Concretely: T003 blocks the relocation and the gateway package (T005, T006, T018 declare `after:T003`); T004 blocks the first database task (T012 declares `after:T004`); T001 blocks the metrics red-green chain (T041 declares `after:T001`), because until the `specs/prd.md` amendment lands a registered document specifies a Wilson interval on MRR and FR-031 forbids one; T002 blocks the lexical-arm disclosure (T035 declares `after:T002`), because the inert field weighting has no recorded owner until it lands. T010 declares `after:T003` because it writes the `gateway/inference` path into CI that T003 must legalise. T096 and T097 declare no downstream edge and are gated by phase order alone, like T001–T004's own placement: both are Phase 1 confirmations and Phase 1 completes before Phase 2 begins. Under phase order plus these edges, no module, session, statement or figure is built while any of T001–T004, T096 or T097 is open — but a purely edge-driven executor must respect phase boundaries to get that guarantee.
- **The version check is a task, not an assumption.** T012 verifies the `pgvector` extension version against the pinned image digest before iterative scan is relied on (FR-039, HINT-002). T014 (`after:T012`) puts the breadth on the connection and T087 (`after:T086`, in a chain rooted at T063→T082) implements strict iterative scan. Below 0.8.0 the setting does not exist, AD-003's approach is unavailable in its entirety, and the in-scope remedy is a wider breadth whose tradeoff FR-040 bounds — recorded by T089.
- **The two mirrored constants move together by edge.** T008 declares `after:T007`. Only `numpy` is admitted; T005 removes `onnxruntime` and `tokenizers` from the denylist by relocating the declarations, and adding them explicitly trips the staleness assertion that every excluded name is still declared by `model` (HINT-003, ADR-0023 §Negative). T009 then reflects the admitted runtime in the image assertions, `after:T008`.
- **Mandatory red-green pairs** (spec FR-042, quality policy). For `retrieval/metrics.py`: T041→T042, T043→T044, T045→T046 — a collection error for the absent module in the first pair, a missing-symbol failure in the second and third. For **fusion ranking**, which the same policy clause names: T027→T024, T029→T025, T030→T026. Each of those three reds is a distinct observation and none is merely the module's absence — T027 fails to collect against an absent `fusion.py`; T029 finds a statement whose per-arm nodes do not yet report the fetch depth; T030 finds the candidate set at the fiftieth position varying between runs because the per-arm tie-break is not yet inside each CTE. Every test task on this list is complete only when its suite has been run and **observed to fail**, with the observation recorded on the task line, and no member of any pair is ever `[P]`. The pseudo-oracle discharges the *property* limb of the clause and not the red-green limb: FR-002 leaves fusion no pure function to property-test, so T027 also carries the four soundness conditions in `plan.md` §Testing Strategy — derived not transcribed, generated not read-back inputs, an enumerated uncovered surface, and adjudicated disagreement.
- **What the oracle cannot see has its own tasks.** T030 covers candidate-set selection at the 50-row cut and the per-arm tie-break; T029 covers limit semantics under CTE inlining; T028 covers the one-statement property itself, which every result-level assertion would miss. AD-005's carve-out — sorting by scores the reranker already returned is ordering, not ranking arithmetic — is what stops T093's contract being weakened later to accommodate it.
- **The integration tier seeds its own corpus.** T023 commits and seeds the fixture because the merge gate applies migrations and never ingests. T030, T031, T084, T088 and T090 all read it. Only T031 reaches it through its own chain (T031→T026→T030); the other four each carry a declared edge, `← T023:seeded_corpus`, because their chains do **not** otherwise touch T023 and an edge-driven executor would run them against an unseeded corpus. The symbol form rather than `after:` is deliberate — it names the interface being consumed, and by the rule below it gates execution identically.
- **The benchmark module is registered before it is written.** T011 names `src/api/tests/test_retrieval_benchmark.py` in the `Performance benchmark (api)` step; T069 declares `after:T067`, and reaches T011 through phase order rather than a declared edge — Phase 1 completes before Phase 5 begins. The api unit step runs `-m "not benchmark"`, so a benchmark module absent from that step's file list runs nowhere at all.
- **Two reranker graphs, both resident.** T016 vendors both, T017 generates the INT8 graph and its generated-artifact provenance, T019 asserts licence basis, source, quantization record and digest for both. T063 loads and warms both before readiness, because AD-006 makes arm selection a request parameter and FR-017 forbids loading a graph on a request path.
- **Declared edges**: T005→T003, T006→T003, T007→T005, T008→T007, T009→T008, T010→T003, T011→T010, T012→T004, T014→T012, T017→T016, T018→T003, T019→T018, T020→T018, T021→T020, T022→T021, T027→T015, T024→T027, T029→T024, T025→T029, T030→T025, T026→T030, T031→T026, T033→T032, T035→T002, T036→T035, T037→T015, T038→T037, T039→T021, T040→T039, T041→T001, T042→T041, T043→T042, T044→T043, T045→T044, T046→T045, T047→T046, T048→T047, T049→T044, T050→T049, T051→T050, T053→T052, T054→T053, T055→T054, T056→T055, T095→T055, T058→T018, T059→T058, T060→T059, T061→T060, T062→T061, T063→T062, T064→T063, T065→T064, T067→T066, T068→T067, T069→T067, T070→T064, T071→T046, T072→T071, T073→T072, T074→T064, T075→T074, T076→T075, T077→T076, T078→T077, T079→T078, T080→T079, T081→T080, T082→T063, T083→T082, T084→T083, T085→T084, T086→T082, T087→T086, T088→T087, T089→T088, T090→T089, T091→T090, T092→T091, T093→T009, T094→T091, T098→T018, T099→T035, T100→T099, T101→T006, T102→T003, T103→T049, T104→T033, T105→T020, T028→T026, T034→T033, T106→T099. T096 and T097 declare none: they are Phase 1 amendment gates carried by phase order, as T001–T004 are.
- **Symbol-import edges gate execution exactly as `after:` does**: T014←T013:RetrievalConfig, T040←T032:RetrievalResult, T066←T062:TruncationReport, and T030/T084/T088/T090←T023:seeded_corpus. A consumer honouring only `after:` under-constrains them.
- **P1 boundary**: Phases 1–6 are the viable deliverable — by phase, not by numeric range, since T095–T105 were appended into earlier phases — a coordinator's question reaches ranked passages carrying their page, part numbers resolve deterministically and additively, the fused set is reranked by a warmed cross-encoder, and a system without one says so. Phases 7–9 are omittable without breaking any P1 criterion, at the cost of E014's ablation table and SC-012/SC-013/SC-014.
- Tasks marked `[P]` touch distinct files and carry no `after:T###` or `← T###:` edge to another task in the same batch. Within a phase, sequential `T###` order already implies the dependency where no `after:` is written — T034 scans the module T032–T033 create. **Four phases are listed in execution rather than numeric order** — Phase 3 (the red-green interleave), and Phases 1, 4, 5 and 9 where T095–T105 were appended into position, because its six red-green tasks interleave: T027, T024, T029, T025, T030, T026. **Read the declared edges throughout this file, not the numbering.** Every task in Phases 3–8 now carries an explicit `after:` or `←` edge for exactly this reason — T028, T032 and T034 previously relied on numeric order inside the one phase that disclaims it.
- A task with `after:T###` or `← T###:Symbol` must not be `[P]`-batched with the referenced task; the implementing agent must verify the referenced task is `[X]` before executing.
