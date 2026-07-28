# QC Report: Delivery Forecast Model

**Feature**: E007 · **Branch**: `00007-delivery-forecast-model` · **Date**: 2026-07-28 · **Iteration**: 1
**Governing**: `project-instructions.md` v1.2.4 · **QC profile**: standard · **Required categories**: linting, coverage · **Coverage target**: 80

## Overall Verdict: **FAIL**

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
