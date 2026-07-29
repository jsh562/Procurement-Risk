# QC Report: Delivery Forecast Model

**Feature**: E007 · **Branch**: `00007-delivery-forecast-model` · **Date**: 2026-07-28 · **Iterations**: 4
**Governing**: `project-instructions.md` v1.2.4 · **QC profile**: standard · **Required categories**: linting, coverage · **Coverage target**: 80

## Overall Verdict: **PASS** (iteration 4)

### Iteration 4 — CI found what three local passes could not

The first CI run **failed**: 10 tests, all in the reproduction tier, on Ubuntu. Every local iteration had passed at 2569 on Windows. This is the finding of the epic, and it is a design defect rather than a flaky test.

**A recorded run's stored digests do not reproduce on Linux.** The recorded run and the re-fit execute in the **same pytest session on the same machine**, and every one of 68 lines produced a different draw digest — with the recorded library pin matching on all six keys, `blas` included. Measured drift: **0.124 days** on the median, against a published **5.0-day** tolerance — so FR-022's actual gate passed with three orders of magnitude to spare. On Windows no line differs.

**The mechanism is unestablished, and this report previously claimed one.** The first version of this narrative diagnosed the failure as "the fit is not bitwise deterministic, because multi-threaded OpenBLAS varies reduction order". That was written from plausibility, not from an experiment. Probing on the same Linux image under CI-like conditions against the real database ruled out the three candidates it rested on: sampling twice from one built graph at one seed is bitwise identical across all ten posterior variables; rebuilding the graph and resampling is bitwise identical; and a 4005-value float64 array — subnormals, `nextafter(1, 2)` and `1/3` among them — survives the Postgres round-trip bitwise, worst delta `0.0`. The sampler is deterministic, graph construction is deterministic, storage is lossless, and **why the stored digests move is not known**. What is established is the observation — a mismatch reported *with the pin matching* — and that is what the corrected code and specification text are written around. It is sufficient to justify the behaviour: the pin demonstrably does not determine the digest, whatever does.

**This correction was made by hand, and a regeneration will erase it.** `artifact-conventions` has `qc-report.md` written only by `/sddp-qc`. The withdrawn diagnosis was removed from this file, from `spec.md`, from `data-model.md`'s `G-21` and from several code comments after the probes disproved it — everywhere except a QC run's own output, which no longer exists to correct. Any future run that regenerates this report must carry the withdrawal forward: the mechanism is unestablished, three candidates are ruled out by measurement, and no fourth has been demonstrated. Restating the OpenBLAS story would reintroduce a claim this epic disproved on its own hardware.

**What failed was code asserting bitwise equality, which the spec forbids.** FR-022 requires agreement "never as bitwise equality of draws"; {SAD:ADR-0009} says the same. `_digest_claim` nonetheless resolved a mismatch to a **failure** whenever the pins matched, on the written premise that "inside it the environment is the recorded one". That premise is falsified by the run above: the pins matched and the digests differed, so the pin does not determine the digest — whatever does. FR-032's own words are "degrade to a reported scope limit rather than a failure **when the observed environment differs**" — and it did differ, in a dimension the pin does not record. The implementation had narrowed *environment* to *pinned versions*.

| Change | |
|---|---|
| `reproduce.py` | A digest mismatch is now a scope limit in **every** case, with the two readings named apart — pin differs, versus pin matches and does not determine bitwise numerics. `DIGEST_CLAIM_FAILED` deleted. `exit_status` no longer reads the claim at all; FR-022's three outcomes govern it |
| `sample.py` | Its docstring claimed the same seed and versions reproduce the same draws — the false premise at its source |
| `spec.md` | FR-032 and SC-030 restated. SC-030 is recorded as the **seventh** criterion in this spec's history to assert something a correct implementation fails |
| `data-model.md` | DV-019 corrected; **G-21** added — the recorded pin is *measured* not to determine the stored digest, with the mechanism recorded as unestablished and the three ruled-out candidates named so they are not re-investigated. Its reversal trigger and production-scale alternative are restated around what would actually settle it: a fingerprint **shown** to determine the digest, which requires finding the mechanism first, and bisecting the divergence to its source rather than pinning an environment around a guess |
| `plan.md`, `tasks.md` | NC-9, the FR-032 coverage row, the edge case, and T105/T106 all described the withdrawn semantics |

**No test was weakened to pass.** Each restatement still discriminates, and one was strengthened: `test_no_disposition_of_the_claim_is_reported_as_a_failure` substitutes all three claim dispositions into a real outcome and requires the exit status unmoved — an `exit_status` that reads the claim again passes the first substitution and fails the other two, which is the exact shape of this regression.

**Swept for recurrence.** `draw_digest`, `artifact_hash`, `array_equal`, `approx(0`, `abs=0`, `1e-12`, `rel=0`, and every test driving a fit, across all four entries. Nothing else depends on reproduction being bitwise. One assertion depends on *same-process, same-graph* determinism — NC-6's no-censoring delta — which is a weaker and different dependency, and it **passed on the very Ubuntu run that exposed this defect**.

**What this says about the earlier passes.** Three local QC iterations reported PASS. They were honest about what they measured and wrong about what that implied: convergence and reproduction had been evidenced on **one platform**, and I reported "6/6 seeds pass" without saying so. CI is the first execution on a second platform, and it found the defect immediately.

---

## Verdict at iteration 3: PASS (local confirmation re-run)

### Iteration 3 — confirmation re-run

Invoked with the working tree **clean at the iteration-2 commit**, so `CHANGED_FILES` was empty and there was nothing to scope to. Rather than restate the prior numbers, every required category was re-executed from scratch. All figures below are **freshly measured**, not carried forward.

| Check | Result | Fresh? |
|---|---|---|
| `src/model` suite, under coverage | **2569 passed** (19m30s) | measured |
| `src/gateway` suite | **402 passed**, 5 skipped | measured |
| root `tests/checks` (orchestration excluded) | 194 passed / 13 failed / 8 errors — **exactly** the recorded pre-existing baseline | measured |
| `ruff check --no-cache` × 4 entries | clean | measured |
| `ruff format --check` × 4 entries | clean — gateway 43, api 4, model 223, root 422 | measured |
| `lint-imports` × 3 entries | **8 kept, 0 broken** | measured |
| `mypy src` (gateway) | no issues in 18 source files | measured |
| Coverage, model entry | **92%** against an 80 floor | measured |
| `model/forecast` counted | **21 measured / 21 on disk**, no zero-hit module | measured |

**Story verification was not re-run, and that is a scoping decision rather than an omission.** The workflow re-verifies only FAILED or PARTIAL work items; none remain after iteration 2, and no source file changed between the two runs. Its iteration-2 verdicts — US1–US5 all PASSED, 42/42 criteria — stand on that basis.

**Browser probe: not applicable.** `plan.md` § API Surface Summary is `N/A — no API surface`; this epic is an offline console job with no rendered UI, no navigation and no browser integration, so `BROWSER_RUNTIME_REQUIRED` is false and no `manual-test.md` is owed.

The `model/forecast` counting check matters and was repeated deliberately: E003's QC established that a package absent from the coverage source lands in the denominator uncounted and reads as a pass. All 21 modules carry real hits — lowest `paths.py` 73%, `write.py` 74% (refusal branches), `manifest.py` 79%.

---

## Verdict at iteration 2: PASS

Iteration 1 returned FAIL with two ERROR and four WARNING findings. All six were fixed and **independently re-verified in code**, with the database-backed evidence executed rather than inferred. The iteration-1 detail below is kept in full — a QC report that overwrites the failure it found leaves nothing to check the fix against.

| Iteration | Verdict | Findings | Model tests | Gateway |
|---|---|---|---|---|
| 1 | FAIL | 2 ERROR, 4 WARNING | 2562 | 1 failed / 400 passed |
| 2 | **PASS** | 0 open | **2569** | **402 passed**, 5 skipped |

### Iteration 2 closure

| ID | Closed by | Verified |
|---|---|---|
| B-1 | The gateway ledger test now asserts what its docstring described — E004's block contiguous and in order, head equal to the **last revision on disk** rather than the literal `0103`. Split into two tests | `test_migrations.py` 15 passed |
| B-2 / B-3 | `sd(θⱼ\|data)` is now package code — `vendor_effect_spread`, `vendor_effect_interval`, `VendorEffect` in `shrinkage.py`, marginal over the scale mixture rather than evaluated at median scales. The comparison reconstructs the run's **own** posterior from its recorded provenance and reads τ/σ through `fit.py`'s `_fitted_scales`; operands are counted from `forecast_split_assignment` train rows and cross-checked against `training_line_count`. ρ's interior turning point survives as a live test | 3 DB-backed SC-005 tests passed against a real fit; 48 non-DB shrinkage tests passed |
| B-4 | `LIMITATION_IDENTIFIERS` widened to L-5; the record carries all four parts and a **measured** figure — `open_lines_at_the_decision_state` counts censored lines whose last event at-or-before the anchor landed on the graph's only branching state. DV-037's presence check widened | `test_limitations.py` recomputes the count by independent SQL |
| B-5 | A below-threshold comparison moves **one** line's realized predictive ESS, so an all-quantified implementation still fails. Asserts outside-basis, exit **zero**, neither pass nor failure, with breaches and unpaired both empty so the verdict is attributable to the basis alone. The inclusive boundary is pinned one representable step apart from the other side | 3 tests passed |
| B-6 | All 42 criteria now appear on task lines; SC-036→T089, SC-037→T091, SC-039→T102, SC-041→T103, SC-042→T044/T047. **Nothing renumbered** — task IDs contiguous T001–T133, `spec.md` unmodified | verified programmatically |

**Regression spot-checks clean**: the struck DV-005 items — the decile comparison and the fit-derived `survival[1]` floor — appear only in prose explaining why they were struck, nowhere as a live assertion. Refusal-by-ordering still holds; `fit.py`'s only change this round was the `_fitted_scales` extraction.

**Coverage re-measured**: all 21 `model/forecast` modules carry real hits, model entry **92%**.

---

## Iteration 1 detail (retained)

### Verdict at iteration 1: FAIL

Both required categories pass. The failure is one test regression this epic caused and three success criteria that are not achievable as implemented. Nothing here is a marker or bookkeeping problem — every item below is a real defect with a named location.

| Category | Status | Evidence |
|---|---|---|
| Compilation *(N/A — Python)* | PASSED | `mypy src` on `/src/gateway`, the only entry in scope at v1.2.4 — `no issues found in 18 source files` |
| Static analysis / linting **(required)** | **PASSED** | `ruff check --no-cache` clean on all four entries; `ruff format --check` clean on all four (gateway 43, api 4, model 223, root 421); `lint-imports` **8 contracts kept, 0 broken**, including E007's new "Forecast code does not reach the model provider" |
| Security | PASSED | Ruff `S` ruleset enabled in `src/model` and `src/gateway`, both clean. No separate tool configured or required; nothing installed |
| Tests | **FAILED** | `src/model` **2562 passed**; `src/gateway` **1 failed / 400 passed / 5 skipped**; root `tests/checks` 194 passed / 13 failed / 8 errors — all pre-existing and attributed below |
| Coverage **(required)** | **PASSED** | **91%** aggregate against an 80 floor. `model/forecast` **87%** across all 21 modules |
| Story verification | **FAILED** | US1 PARTIAL, US2 PASSED, US3 PASSED, US4 PASSED, US5 PARTIAL. 38 of 42 criteria pass |

## Coverage

`model/forecast` is **genuinely counted**, verified three ways rather than assumed — E003's QC established that an unlisted package lands in the denominator uncounted and reads as a pass:

1. All **21** modules appear with non-zero hits, not the zero-hit denominator signature.
2. The measured file set equals the package exactly — 20 modules plus `__init__.py`.
3. All three registration sites carry it: root `[tool.coverage.run] source`, `[tool.coverage.paths]`, and `verify.yml`'s inline `--source=`. **T111's remediation held** — without it the package would have run and counted for nothing.

Lowest: `paths.py` 73%, `write.py` 74% (mostly refusal branches), `manifest.py` 79%. No per-package floor gates these.

## Red-Green Pairs: 10 / 10

Verified from file-addition history, not asserted: for every pair, the property-test file lands in a `test:` commit strictly earlier than the `feat:` commit that first adds its module.

`serialize` `censoring` `split` `likelihood` `design` → `760ef02` → `223a66e` · `posterior` `shrinkage` → `ce77cd4` → `8c06331` · `ablation` → `b8775ec` → `4365508` · `diagnostics` → `f4c170c` → `d57e8fa` · `compare` → `c0ded31` → `b922e99`

E005 closed this obligation at six of seven because a test and its implementation shared one commit. E007 closes at ten of ten. Pairs bundled five- and two-to-a-commit still satisfy the per-pair ordering.

## Findings

| ID | Sev | Location | Defect |
|---|---|---|---|
| B-1 | **ERROR** | `src/gateway/tests/test_migrations.py:242` | **E007 broke a CI-gating test.** `EPIC_REVISIONS` hardcodes E004's block and asserts the chain head is `0103`; E007's `0300`–`0303` moved it to `0303`. `AssertionError: expected the head to be '0103', found ['0303']`. Attributed by `git log --diff-filter=A` on `0300_forecast_run_provenance.py` → `0082e2c`; E005 added no migrations, so E007 is the first epic since E004 to move the head. Breaks `verify.yml`'s "Unit tests (gateway)" step. The test's own docstring anticipates this — "a later chain had been grafted on" — so the fix needs a decision about intended semantics, not a literal bump |
| B-2 | **ERROR** | `src/model/src/model/forecast/shrinkage.py:24`; `tests/forecast/test_shrinkage_properties.py:99, 585` | **SC-005 is not achievable as implemented.** The vendor-effect interval `sd(θⱼ\|data) = τσ/√(nτ²+σ²)` exists **only in the test file** — `shrinkage.py` exports `ShrinkageError, VendorShrinkage, vendor_shrinkage` and nothing else. So the comparison runs against a seeded **stand-in posterior**, not the run's own fitted τ/σ, and its operands are hard-coded `SPARSE_COUNT = 5` / `DENSE_COUNT = 35` rather than "counted from the run's own `forecast_split_assignment` rows with `split_side = 'train'`", which SC-005 requires **by name** as its defence against choosing operands after seeing the intervals. US1 acceptance scenario 5 has no implementation-side realisation |
| B-3 | WARNING | `data-model.md:556` vs `test_shrinkage_properties.py:99` | **DV-010 runs at a different tier than it declares.** The data model tiers it "asserted over the fitted posterior as an in-memory artifact"; the delivered assertion uses a stand-in. The map is the normative document and the two disagree. Closing B-2 closes this |
| B-4 | WARNING | `src/model/src/model/forecast/report.py:220` | **L-5 is declared but never emitted.** `data-model.md:634` records it as a four-part limitation under AD-013 — the rework-loop bias that makes an open line at a decision point forecast short. `LIMITATION_IDENTIFIERS` stops at L-4 and DV-037 is scoped to L-1–L-4, so nothing detects the omission. AD-013 added L-5 **specifically so it would not live in a source comment**, and a source comment is where it stayed. SC-024 PARTIAL |
| B-5 | WARNING | `src/model/src/model/forecast/reproduce.py:942` | **SC-018's third outcome has no covering test in either direction.** The outside-basis branch is live code — `REPRODUCTION_PREDICTIVE_ESS_FRACTION_MIN = 0.5`, exit zero, neither pass nor failure — but `test_reproduction.py:89` only asserts the verdict is one of three and `:222` asserts the outside-basis list is empty. No `LineComparison` is ever constructed below the threshold. By `plan.md` § Negative Controls' own admission rule, a claim outside the three exclusion classes with no entry **is a miss** |
| B-6 | WARNING | `tasks.md:333`, `plan.md` § SC Coverage Map | **Traceability stops at SC-035 while the spec carries 42.** `tasks.md`'s validation table claims "35 / 35 (SC-001…SC-035)". SC-036–SC-038 and SC-040 were retro-fitted into the plan's Analyze-gate appendix and do have tasks; **SC-039, SC-041 and SC-042 carry no task tag at all**, and SC-041/SC-042 are named by no test. All three are substantively achievable, so this is a mapping defect rather than a functional one — but the split table is how the omission survived |

### Not E007's — verified, not assumed

`tests/checks`: 13 failed / 8 errors, matching the recorded baseline **exactly**, in five files — `test_image_contents`, `test_image_negatives`, `test_supply_chain` (need a locally built Docker image), `test_gateway_no_provider_env` (needs network), and `test_single_import_site` (3 failures naming E005's `model/procurement/{generate,load,validate}.py`). Confirmed against `main` in a scratch worktree. E007's own gate members — `test_migration_ranges.py` and `test_dependency_isolation.py` — both pass.

### Environment defect, repaired during QC

The `.exe` console-script launchers in `src/gateway/.venv` and `src/api/.venv` embedded the **sibling checkout's** interpreter (`S:\claudecode\KayaDemoProcurementRisk\`, no trailing `1`), so the documented commands ran against the wrong tree and collapsed with 15 collection errors. The auditor detected it, discarded the invalid results, and re-ran through a verified interpreter. Repaired here with `uv sync --reinstall-package`, then `--extra provider` restored after the first sync pruned `anthropic` and turned 1 failure into 9. Same hazard class as the root venv repaired earlier in this branch; it does not affect CI, which syncs a fresh checkout.

## Definition of Done

| # | Criterion | Status |
|---|---|---|
| 1 | All tests pass | **NO** — B-1 |
| 2 | Coverage meets threshold | YES — 91% ≥ 80% |
| 3 | No CRITICAL/ERROR static analysis findings | YES |
| 4 | No CRITICAL security vulnerabilities | YES |
| 5 | All P1 work items PASSED | **NO** — US1 PARTIAL (B-2) |
| 6 | All success criteria PASSED | **NO** — 38/42; SC-005 failed, SC-018 and SC-024 partial |
| 7 | PI compliance: no violations | YES |
| 8 | No unresolved `[BUG]` tasks | **NO** — six logged |
| 9 | No unacknowledged SKIPPED checks for PI-mandated categories | YES |

**`.completed` removed. Six `[BUG]` tasks appended to `tasks.md` as T128–T133.**
