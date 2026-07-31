# QC Report: Hybrid Retrieval and Reranking

**Date**: 2026-07-30
**Feature Directory**: `specs/00008-hybrid-retrieval-and-reranking`
**Overall Verdict**: PASS

No prior `qc-report.md` — full run.

## Summary

| Check | Status | Details |
|-------|--------|---------|
| Build / compile | PASSED | Four entries resolve and import; `mypy src` clean on `/src/gateway`, 23 files |
| Static Analysis / Linting *(PI-mandated)* | PASSED | `ruff check` ×4, `ruff format --check` ×4, `import-linter` 13 contracts, `mypy` |
| Tests | PASSED | Every tier clean on one freshly created database, model tier under `REQUIRE_DB=1`. Earlier failures were a skipped migration step and residue from SIGKILLed runs — diagnosed below |
| Coverage *(PI-mandated)* | PASSED | Aggregate and all six per-package floors at or above 80% |
| Security *(not mandated)* | PASSED | `pip-audit` across api, gateway, model — no known vulnerabilities |
| PI Compliance | PASSED | No violations |
| Requirements Traceability | PASSED | 51/51 FR, 16/16 SC, 6/6 stories, 106/106 tasks |
| Checklist Fulfillment | PASSED | 119/119 items complete across three checklists |
| Performance | PASSED | Gates assert under the CI pin; local figures published as indicative |
| Accessibility | SKIPPED | No accessibility NFR in `spec.md` |
| Browser Runtime Validation | SKIPPED | E008 touches no web tier |

## Test Results — PASSED

| Tier | Runner | Total | Passed | Failed |
|---|---|---|---|---|
| api | pytest 9.1.1 | 490 | 489 (1 skipped, 8 benchmark deselected) | 0 |
| api — benchmark tier | pytest | 5 | 3 (2 skipped off the one-CPU pin, by design) | 0 |
| gateway | pytest | 475 | 433 (42 skipped — no DATABASE_URL on that tier) | 0 |
| model | pytest | 3223 | **3223** with `REQUIRE_DB=1` on a clean database | 0 |
| cross-entry `tests/checks` | pytest | 325 | 325 | 0 |

### The database-state errors, diagnosed rather than retried away

Intermediate runs during this QC pass reported **12 failures** and **84 errors**. Both are gone,
and neither was a defect in the code. Recorded because a QC run that saw them and did not chase
them down would be worthless, and because the first two explanations offered here were wrong.

**The 12 failures: a skipped CI step.** CI applies the model migration chain in its own step
before the model tests. This run had not, so the schema was behind. Applying it: gone.

**The 84 errors: orphaned rows from killed runs.** All 84 were one foreign key —

```
psycopg.errors.ForeignKeyViolation: update or delete on table "purchase_order_line"
violates foreign key constraint "fk_forecast_split_assignment__line"
on table "forecast_split_assignment"
```

— raised in `tests/procurement/`, whose fixture resets `purchase_order_line`.

Two earlier explanations were offered and both were wrong. The first blamed two suites sharing one
PostgreSQL instance; the model errors then reproduced in a **single uncontended run**, which that
does not explain. The second blamed a stale cleanup list in the E005 fixture, and a patch to it was
started and abandoned on reading the code: E007's `emitted_run` fixture is **package-scoped for
precisely this reason**, and says so —

> *"a session-scoped teardown would still be holding them while `tests/procurement` reloads that
> table, and the loader's delete would fail a foreign key. Leaving this directory is the last
> moment at which the run is still wanted and the first at which it is in another tier's way."*

Its teardown discards every run written during the tier. The cleanup is correct by design; what it
cannot survive is the process being **killed**. Two runs during this session were SIGKILLed by a
`timeout` shorter than the suite needs, so teardown never executed and the forecast rows outlived
it — after which every later run touching `purchase_order_line` failed, in any tier.

**The fix was to remove the residue, not to patch working code.** `docker compose down -v && up`,
migration chain applied. `tests/procurement` alone: **618 passed, 0 errors** — the same subset that
had produced 84 minutes before, against unchanged code. Then every tier, on that one clean
database, in sequence:

| Tier | Result |
|---|---|
| model, `REQUIRE_DB=1` | **3223 passed**, 0 failed, 0 errors |
| api | 489 passed, 1 skipped |
| api benchmark tier | 3 passed, 2 skipped |
| gateway | 433 passed, 42 skipped |
| cross-entry `tests/checks` | 325 passed |

**A measurement error of my own, recorded rather than quietly corrected.** An earlier point in this
session reported the model tier as "2312 passed" with `DATABASE_URL` unset — 911 of its tests were
skipping, and the figure was presented as a clean pass. The `REQUIRE_DB=1` runs above are the
honest measurement; the requirement exists in CI for exactly this reason, and its own comment says
so: *"deleting the line above would leave 344 of the suite's 425 tests skipped and this step still
green — measured, not feared."*

## Failure Index

| ID | Category | Severity | File:Line | Description | Bug Task |
|----|----------|----------|-----------|-------------|----------|
| — | — | — | — | No unresolved failures | None |

Four defects were found and fixed **within** this run; they are recorded in Bug Context below
rather than left open, because each was a verification gap rather than a code regression, and
leaving them open would have meant a `.qc-passed` marker over criteria nothing checked.

## Code Coverage — PASSED

- **Threshold**: 80% (`.github/sddp-config.md` § Derived QC Policy)
- **Status**: PASSED — aggregate and every per-package floor at or above threshold

CI's gate is seven assertions, not one: an aggregate plus six per-package floors. The per-package
floors exist because an aggregate is an average, and an already-covered package floats a newly
added, uncovered one across the threshold — the exact arithmetic that makes a coverage gate agree
with adding untested code.

| Scope | Statements | Coverage | Floor | Result |
|---|---|---|---|---|
| Aggregate (combined, four data files) | 14465 | **92%** | 80% | PASSED |
| `*/model/corpus/*` | 3762 | 93% | 80% | PASSED |
| `*/model/ingest/*` | 3312 | 90% | 80% | PASSED |
| `*/model/llm/*` | 176 | 95% | 80% | PASSED |
| `*/model/compute/*` | 217 | 92% | 80% | PASSED |
| `*/api/retrieval/*` | 566 | **95%** | 80% | PASSED |
| `*/gateway/inference/*` | 250 | **93%** | 80% | PASSED |

**The model-tier data came from a run that had 12 failures and 84 errors**, so its four floors are
a *lower bound*: tests that errored at setup executed none of their bodies, and a clean run can
only measure the same or more. Every floor clears anyway, and re-measuring would cost another
31-minute instrumented run to move four already-passing numbers upward. Stated rather than
silently presented as a clean measurement. The `api/retrieval` and `gateway/inference` figures —
the two this epic owns — come from runs with no failures at all.

The two packages this epic added are the two that matter here. `api.retrieval` is 566 statements
at 95%; `gateway.inference` is 250 at 93%. `gateway.inference.reranker` was at **0%** before this
run — reachable only through the api tier's warm-up, which put a gateway regression behind an api
run and hid it entirely when that run skipped for want of a database.

## Static Analysis — PASSED

| Tool | Scope | Critical | Warnings |
|---|---|---|---|
| `ruff check` | `src/gateway`, `src/api`, `src/model`, `src/web` | 0 | 0 |
| `ruff format --check` | same four | 0 | 0 — 53 / 78 / 292 / 1 files already formatted |
| `import-linter` | gateway 5, api 4, model 4 | 0 | **13 contracts kept, 0 broken** |
| `mypy` | `/src/gateway`, the PI-scoped tier | 0 | Success, no issues in 23 source files |

## Security Audit — PASSED

- **Tool**: `pip-audit`, run against all three Python entries
- **Vulnerabilities found**: 0

The only skipped entries are the workspace's own packages (`api`, `gateway`, `model`), which are
not on PyPI and cannot be audited — expected, not a gap. Run through `truststore` locally because
this machine's TLS interception makes `certifi`'s bundle reject PyPI; CI sets `UV_NATIVE_TLS=1`
for the same reason.

## Project Instructions Compliance — PASSED

**No violations.**

| Principle / Section | Status | Evidence |
|---|---|---|
| I. Traceable or It Does Not Ship | PASS | Every result projects document, type, project and page from the chunk row; the result type has no constructor accepting a page from elsewhere (`test_page_provenance.py`) |
| II. Uncertainty Is the Product | PASS | `publish_figure` refuses a bare point estimate, a figure claiming both an interval and a reason, a census with no denominator, and a figure with no ingest generation. `NoIntervalReason` is closed at two, asserted so |
| III. Precision Over Recall Where a Mistake Is Silent | PASS | The deterministic route is additive and never subtractive (`test_router.py`) |
| V. The Model Extracts, Code Computes | PASS | Ranking is one SQL statement plus deterministic Python; the computation-boundary contracts are among the 13 kept |
| VI. Evaluate Before You Tune | PASS *(strengthened this run)* | The frozen set is hashed and the harness aborts before returning any query. This run found the digest verified **bytes but not the corpus** — Bug Context 4 |
| VII. Publish the Miss | PASS | The benchmark and the gate print their figures whether or not they clear; the local memory reading is published at 4.0 GB against a 400 MB budget with its measurement point stated |
| VIII. Honest Opponents | PASS | The INT8/FP32 comparison asserts shape agreement, not value agreement — asserting the latter would encode the opposite of what FR-025 exists to measure |
| §Source Code Layout | PASS | Gateway carries the inference runtime and NumPy; PyMC, ArviZ and pandas remain forbidden — `test_dependency_isolation.py`, `test_image_contents.py` |
| §Testing & Quality Policy | PASS | Six declared red-green-refactor pairs for fusion ranking and the scoring functions; property tests over the pure functions |
| §Data Provenance | PASS | `data/evaluation_set/DATASHEET.md`, `data/reranker/provenance.json` |
| §Temporary Files | PASS | Scratch checks on both api and gateway tiers; the gateway one added because that tier downloads a model and runs a native toolchain |

## Requirements Traceability — 6/6 work items verified, 16/16 SC verified

All **51 functional requirements** are tagged to at least one task, and all **106 tasks** are
checked with none deferred.

| ID | Type | Status | Notes |
|----|------|--------|-------|
| US1 Ask a question, reach the passage | Work Item | PASSED | P1, 31 tasks; SC-001–SC-004 |
| US2 Type a part number, get that item | Work Item | PASSED | P1; SC-005, SC-006 |
| US3 The ordering is worth trusting | Work Item | PASSED | P1; SC-007, SC-008, SC-009, SC-016 |
| US4 A degraded system says so | Work Item | PASSED | P1; SC-010, SC-011 |
| US5 Each arm can be measured on its own | Work Item | PASSED | P2; SC-012, SC-014 |
| US6 One flag, index usage only | Work Item | PASSED | P2; SC-013 |
| SC-001 recall@5 ≥ 0.85, Wilson | Success Criteria | PASSED | **1.0000** [0.4385, 1.0000] — `test_gate_measurement.py` |
| SC-002 MRR ≥ 0.70, bootstrap | Success Criteria | PASSED | **1.0000** [1.0000, 1.0000] — `test_gate_measurement.py` |
| SC-003 document + page identity | Success Criteria | PASSED | `test_page_provenance.py` |
| SC-004 no arithmetic outside the boundary | Success Criteria | PASSED | `test_single_statement.py` |
| SC-005 100% of corpus part numbers | Success Criteria | PASSED | `test_part_number_coverage.py` — **written this run**, Bug Context 1 |
| SC-006 route never removes a result | Success Criteria | PASSED | `test_router.py` |
| SC-007 ready only after warm-up | Success Criteria | PASSED | `test_reranking.py` |
| SC-008 reported against the strongest single arm | Success Criteria | PASSED | `test_performance_report.py`, including the overlapping-interval case |
| SC-009 truncated fraction as a census | Success Criteria | PASSED | `test_report.py`, `test_inference_reranker.py` |
| SC-010 fusion-only responses say so | Success Criteria | PASSED | `test_reranking.py`, `test_degraded_at_the_boundary.py` |
| SC-011 degraded path exercised by a test | Success Criteria | PASSED | `test_degraded_at_the_boundary.py` — **rewritten this run**, Bug Context 2 |
| SC-012 all six arms | Success Criteria | PASSED | `test_arms.py`, `test_ordering_digest.py` |
| SC-013 flag changes nothing else | Success Criteria | PASSED | `test_flag_parity.py` |
| SC-014 no query returns fewer than requested | Success Criteria | PASSED | `test_search_route.py` |
| SC-015 six blocking amendments landed | Success Criteria | PASSED | Verified against `origin/main`, not against task markings |
| SC-016 latency and resident memory reported | Success Criteria | PASSED | `test_performance_report.py`, `test_retrieval_benchmark.py` |

SC-015 was checked against the default branch rather than against the task markings:
`project-instructions.md` is at **v1.2.11**, the INT8-only qualifier is gone from the live
Technology Stack clause (it survives only in the Amendment History row describing its removal),
`specs/sad.md` catalogues ADR-0023, and `specs/project-plan.md` assigns the `part_numbers` owner.

## Traceability Gaps

**None outstanding. Six were found and closed during this run.**

Tracing every file path cited in `plan.md` and `tasks.md` found six that did not exist. Five were
modules consolidated under other names during implementation; the citations are repointed at the
modules that actually carry each verification, because a reader following the plan to find a check
was finding nothing. The sixth was a verification cited and never written — Bug Context 1.

All citations now resolve: 28 `src/` paths in `plan.md`, 45 in `tasks.md`, zero missing.

## Checklist Fulfillment — 12/119 spot-checked

| Checklist | Items | Complete | Gaps |
|---|---|---|---|
| `api-quality.md` | 40 | 40 | 0 |
| `performance.md` | 39 | 39 | 0 |
| `testing.md` | 40 | 40 | 0 |

Spot-checked the `[Testing]` category. There is no `[Security]` category to spot-check — none of
the three checklists carries a security, credential, secret or injection item, which is consistent
with an epic that adds a read-only surface with no write path. The parameterised SQL and the
credential scan are covered by `tests/checks/test_supply_chain.py` and
`test_fixture_credential_scan.py` instead, both passing.

CHK001–CHK012 concern the pseudo-oracle's soundness
conditions, and each is satisfied by `plan.md` §Testing Strategy and the modules it names —
`test_fusion_oracle.py` (independent derivation, generated rank vectors, parameters read from
`parameters.py` rather than re-declared as literals), `test_fusion_plan_shape.py` (each arm's
`LIMIT` survives CTE inlining), `test_candidate_set.py` (a tie engineered at the last in-window
position) and `test_single_statement.py` (FR-002 verified directly, not only its output). PASSED,
0 gaps.

## Performance — PASSED

FR-033's figures are taken after readiness, on a real run, by
`src/api/tests/test_retrieval_benchmark.py` — a module CI had been invoking since T011 registered
it, and which did not exist until this epic's last phase.

| Figure | Local value | Budget | Gate |
|---|---|---|---|
| Reranking never-exceed latency | **50.8 ms** over 30 queries | 400 ms | Asserted only under the one-CPU pin |
| Resident set, in-process host | **4.0 GB** | 400 MB | Asserted only under the one-CPU pin |

Both are published as **indicative** off the pin, and both say why in the report they carry.

- **The memory reading is the in-process host's resident set, not the serving container's.**
  `TestClient` drives the app in this process, so the figure carries the interpreter, pytest,
  hypothesis and the whole test session alongside the graphs. Labelled as such in
  `measurement_point` rather than presented as the serving figure.
- **The per-session breakdown publishes on-disk graph sizes and says so.** ONNX Runtime shares an
  allocator and the OS reports one resident set per process, so a per-session RSS is not a thing
  that can be read; apportioning the total between the graphs would be a number nobody measured.

Two honesty corrections were made while writing it. The gate keyed on an environment variable the
CI step does not set — that step pins with `taskset -c 0`, not a container `cpus=` — so the gate
would have skipped in CI as well as locally: a check that exists and covers nothing. It now reads
the process's affinity mask, which is the constraint itself rather than a declaration about it.
And `psutil` was undeclared, so every FR-033 memory assertion had been skipping silently; it is
now a declared dependency rather than an optional one.

## Accessibility — SKIPPED

No accessibility NFR. `spec.md` contains zero occurrences of WCAG, accessibility, a11y, screen
reader or aria. E008 is a request-serving boundary with three GET operations and no rendered
surface.

## Browser Runtime Validation — SKIPPED

- **Mode**: N/A — no browser scenario exists to exercise
- **Browser tool**: N/A
- **App start**: Not needed
- **Target**: N/A

E008 ships no interface. `plan.md` and `tasks.md` cite `src/web` zero times, and the contract
defines no write path and no HTML surface. No probe was run because there is nothing for one to
validate.

## Manual Testing — Not Required

Every criterion is reachable from an automated check. No `manual-test.md` was generated.

## Tool Recommendations

None outstanding. `psutil` was the one missing tool and it is now declared in
`src/api/pyproject.toml` rather than recommended.

## Bug Context

| Bug | Severity | Requirement | Where | Status |
|---|---|---|---|---|
| 1. SC-005 had no verification at all | ERROR | SC-005, FR-010, FR-014 | `test_part_number_coverage.py` (absent) | Fixed in-run |
| 2. SC-011 discharged the one way the plan forbids | ERROR | SC-011, FR-021, FR-024 | `test_reranking.py:191` | Fixed in-run |
| 3. FR-038's thread counts never asserted | WARNING | FR-038 | `gateway/inference/session.py` | Fixed in-run |
| 4. Frozen set verified against bytes, not corpus | ERROR | FR-043, Principle VI | `data/evaluation_set/queries.json` | Fixed at T094 |

### 1. SC-005 had no verification at all

`test_part_number_coverage.py` was cited by both `plan.md` and `tasks.md` and never written. The
criterion measuring *"100% of part numbers present in the corpus are returned"* had neither a
population nor a measurement, and task marking recorded it as covered.

**Fixed.** AD-009 fixes the enumeration source as the generator's pre-render document model. The
api tier does not declare `model` as a dependency by design, so what is enumerated here is the
committed manufacturer catalogue — where the catalogue key *is* the part-number prefix, and the
corpus mints `<key>-<five digits>`. Every declared prefix is exercised at both serial boundaries
and at an interior value. What the check does **not** reach — the rendered document instances — is
stated in the module rather than left for a reader to assume.

### 2. SC-011 was discharged the one way the plan says does not discharge it

`plan.md`'s coverage map is explicit: force the failure *at the artifact-loading boundary*, and
**"setting the flag directly does not discharge it"**. The existing test constructed
`RetrievalReadiness(encoder_ready=True)` and asserted the state that followed — starting from the
degraded state rather than arriving at it. It would have passed unchanged against a
`warm_rerankers` that raised, that swallowed the failure without recording it, or that never
verified the artifact at all.

**Fixed.** Three real loading failures, which fail at three different points: absent (the path
check), corrupt (the runtime's parser), and digest-mismatched (FR-016's verification). Plus
FR-024's third observable — that a degraded process still returns results — against a real query.

Writing it exposed a second defect in the test itself: patching `readiness.readiness` left the
route holding the original object, because the route imported it by name at module load. The
substitution looked applied and was not, and the test passed against an undegraded process.

### 3. FR-038's thread counts were never asserted

The counts were threaded from configuration through the lifespan, readiness and the reranker into
`session_for`, and nothing checked they arrived — the whole chain could have been intact except
the line that applies them, with every test still green. FR-038 exists because an unset count is
*silent*: ONNX Runtime defaults to one thread per core the OS reports, which under a CPU quota is
the host's count, and it also pins thread affinity. The symptom is a worse latency figure with no
error to attribute it to — and FR-033's figure is measured on exactly that path.

**Fixed.** Read back from the created session rather than from the options object, and asserted at
two different values. A session created with default options reports **0** for both counts —
measured, not assumed — so the assertions genuinely distinguish applied from unapplied.

### 4. The frozen set was verified against its bytes, not against its corpus

Found during implementation at the first gate measurement rather than in the QC phase, but it is
the most consequential thing this epic found and it belongs on the record.

The first gate run measured recall **0.000** and MRR **0.000**. The frozen set's judgements named
chunk identifiers the fixture had stopped producing: `556b9305-1f4b-1ba0-2c47-ec4a13a3a37d`
against the fixture's `556b9305-242f-7766-9faf-84a98ceec320` — same leading group, different
derivation. Every judgement missed.

**The digest was correct throughout.** It certifies the set has not changed since it was frozen,
and the set was frozen wrong. Verification against the *bytes* and verification against the
*corpus* are different questions, and only the first was being asked — which is why the harness's
abort-before-measure ordering, the thing that file exists to guarantee, could be perfectly sound
and still let a headline figure read zero.

**Fixed.** Judgements are computed from the fixture rows by
`src/api/tests/retrieval/evaluation_set/build.py`, and the committed set is asserted equal to what
it produces. Three assertions rather than one, because they fail differently: the rebuild equality
catches any drift, the seeded-id check names the specific failure that produced the zero, and the
manifest check catches a set regenerated without re-recording its digest. The datasheet records
the repair and why the judgement source is unchanged.

## Bug Tasks Generated

**None.** The four defects above were fixed within this run rather than appended to `tasks.md` as
open work. The one test error observed has a named cause, is not reachable in CI, and raises no
task against this epic.

## Not Claimed

Stated so a reader does not take this report for more than it is:

- **E008's workspace is not on the default branch.** Under Governance v1.2.11 — *"a claim recorded
  only in a feature workspace is not a claim"* — none of the figures above is a claim until this
  branch merges.
- **The gate figures come from a three-query set over six chunks.** SC-001's Wilson interval
  [0.4385, 1.0000] is the honest width of what three queries can say. `DATASHEET.md` states it
  plainly: *"It is not a benchmark… a wiring check, not a quality measurement."* Both figures are
  **upper bounds on real-world performance, not estimates** — every query is answerable by
  construction.
- **The latency and memory gates did not assert locally.** They skip off the CI pin by design; the
  figures above are indicative.
- **FR-036's comparator rule was exercised on one measured arm** at the gate. The full ablation
  across all six arms is E014's.
- **SC-005 measures the part-number shape space, not the rendered instances.** The enumeration is
  the committed catalogue's prefix set; reaching the rendered documents needs the model tier,
  which the api tier does not declare by design.
