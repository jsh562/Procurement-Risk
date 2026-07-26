# QC Report: Public Corpus and Manifest

**Feature**: `00002-public-corpus-and-manifest` | **Epic**: E002 | **Date**: 2026-07-26
**Run**: full (no prior report) | **Instructions**: `project-instructions.md` v1.2.0
**Coverage target**: 80 | **Required categories**: linting, coverage | **Profile**: standard

## Overall Verdict: PASS

All required categories passed. One success criterion is a **deliberate, published miss** (SC-001) rather than an oversight, and one work item is PARTIAL on a half that cannot be evidenced from a repository (US5). Both are recorded below with their causes rather than resolved away.

## Test Results

| Suite | Command | Result |
|---|---|---|
| Model unit (CI invocation, under coverage) | `uv run --directory src/model coverage run --source=src/model/roster,src/model/corpus -m pytest tests -q` | **612 passed**, 0 skipped |
| Gateway | `uv run --directory src/gateway python -m pytest tests -q` | **5 passed** |
| Cross-entry checks | `uv run python -m pytest tests/checks -q --deselect tests/checks/test_orchestration.py` | **108 passed**, 8 deselected |
| Corpus validation (epic gate) | `uv run --directory src/model corpus-validate` | **59 rules, 59 passed, 0 failed, 0 skipped**, exit 0 |

Zero tests skipped in the model suite — every symlink and platform-dependent case ran on this machine.

**Environmental error, not a regression.** Four tests in `tests/checks/test_orchestration.py` error with `Bind for 0.0.0.0:5434 failed: port is already allocated`. Verified independently rather than assumed: `docker inspect` reports the port holder as `kayademoprocurementrisk1-db-1`, labelled `working_dir=S:\claudecode\KayaDemoProcurementRisk1` — a different checkout on this machine. `git diff --name-only 76b6e52..HEAD` covers 131 files and does not include `docker-compose.yml`, whose last touching commit is E001's. Classified **SKIPPED (WARNING)**; the fix is to stop the foreign container, and nothing in this repository needs changing.

## Static Analysis

| Check | Result |
|---|---|
| `ruff check` (model / root) | All checks passed |
| `ruff format --check` (model / root) | 39 / 122 files already formatted |
| `import-linter` model | 2 kept, 0 broken — computation boundary, and corpus→provider |
| `import-linter` gateway | 1 kept, 0 broken |
| `import-linter` api | 1 kept, 0 broken |

Architecture contracts are treated as build-gating tests per `project-instructions.md` §Testing & Quality Policy. The corpus→provider contract is FR-022's enforcement and required `include_external_packages = true` to load at all, since `gateway` sits outside the `model` root package; non-vacuity was proven with a planted violation during implementation.

## Security Audit

Not a required category for this project. `uv lock --check` resolves 81 packages, exit 0. `tests/checks/test_supply_chain.py` passes: no alternate index under any entry, no stray `uv.toml`, images digest-pinned, and the credential scan now covers the `model` entry and the `data/` tree.

Supply-chain posture for the 24 added distributions: exact pins throughout, sha256 artifact hashes on every sdist and wheel, all resolving from the default public index. The GPL `rfc3987` is **absent** — `jsonschema[format-nongpl]` pulled `rfc3987-syntax` instead, which is the choice SC-028 exists to evidence. One weak-copyleft entry, `fqdn` (MPL-2.0), is file-scoped and named rather than buried.

No dependency-vulnerability scanner is configured project-wide; its absence is a recorded scope decision with cause, owner, and reversal trigger, not an unmet obligation. Nothing was installed during this run.

## Project Instructions Compliance

No violations.

| Principle | Assessment |
|---|---|
| I. Traceable or It Does Not Ship | Every one of 51 documents carries a manifest entry with layer-appropriate provenance; five digest kinds kept distinct; all recomputed independently during verification with zero mismatches |
| II. Uncertainty Is the Product | N/A — no forecast or metric published by this epic |
| III. Precision Over Recall Where a Mistake Is Silent | A document whose licence basis cannot be established is excluded and recorded; a synthetic entry cannot carry a retrieval field it does not have — the type has no slot for one |
| IV. Agent Output Style | Artifacts emit required sections only |
| V. The Model Extracts, Code Computes | Zero model invocation; enforced by import contract and a socket guard installed before package import |
| VI. Evaluate Before You Tune | N/A — evaluation sets are E014's, excluded by explicit scope decision |
| VII. Publish the Miss | **Exercised.** SC-001's ceiling is exceeded by one document; the criterion is left unamended and the overshoot published with its cause. Retroactively adjusting it was the one move available and the one the principle forbids |
| VIII. Honest Opponents | N/A — no model claim |
| Technology Stack | Four dependencies added inside the declared Python boundary; job invocation matches v1.2.0's amended Infrastructure clause |
| Source Code Layout | All source under `/src/model`; corpus under `data/`; no new entry; the two repo-root checks use the cross-entry exception legitimately |
| Data Provenance | Public-domain or synthetic only; per-layer provenance per v1.2.0; copyrighted standards cited never included, verified over 1,167 extracted pages |

## Requirements Traceability

**Work items** — US1 PASSED (4/4) · US2 PASSED (11/12 repo-verifiable) · US3 PASSED (12/12) · US4 PASSED (6/6) · US5 **PARTIAL** (1.5/2).

US5's committed halves are complete and asserted: `pull_request` declared against `main`, `pull_request_target` absent, `permissions: contents: read`, and every contract's negative fixture must name its contract inside a step proven unconditional in a pull-request-triggered workflow. The remaining half — that a run actually happened — is recorded by URL outside the repository and cannot be evidenced from a checkout.

**Requirements**: 58 total. 44 PASSED. 13 **PARTIAL — disclosed**, each with its cause and bound recorded in `data-model.md` §Uncovered Requirements: FR-001 (byte-equality to *published* bytes is uncoverable offline), FR-002 ("weighted" carries no threshold), FR-004 (ledger completeness leaves no artifact), FR-005 / FR-011 (recorded human judgement), FR-008a / FR-008c (conditional tautology, trust on first use), FR-008b (cadence evidenced only by a release record), FR-009b (enumeration currentness), FR-031a (injector and deriver share one vocabulary), FR-031b (per-document degradation stays generator-asserted), FR-034a (two of four clauses are stated posture, bounded rather than prevented). 1 with **no mechanical verification and none claimed**: FR-036, an assigned review gate with an accountable owner.

**Success criteria**: 28 total. 26 PASSED. 1 PARTIAL (SC-022, run-record half). 1 **FAILED — published**:

> **SC-001** states 45–50 documents; the corpus is **51**. Cause: 26 long-lead UFGS sections were verified reachable and individually justified where the criterion floors at 20, and SC-010 floors the synthetic layer at 25. Trimming a legitimately retrieved public-domain section to reach a round ceiling would optimise the number over the corpus, and the closed exclusion-cause enum has no cause that honestly describes it. The criterion is **not amended** — Principle VII forbids adjusting a target to match a result. 51 remains inside the project's 30–60 envelope, and SC-001's real-layer half is met independently: 26 ≥ 20 documents over 26 distinct sections ≥ 6, spanning Divisions 26 and 23.

## Traceability Gaps

Five were found by the story verifier and **all five were closed before this verdict** rather than carried as warnings:

| Gap | Resolution |
|---|---|
| SC-008's corpus-validation step had no committed assertion — deleting it would be caught by nothing | Three assertions added to `tests/checks/test_workflow_triggers.py` (step present, unconditional, unscoped), each with a failing-direction control against a planted real workflow, plus a non-vacuity control since all three pass by finding nothing |
| SC-026's per-package floor was not gated; CI enforced only the aggregate | Per-package gate added to `verify.yml`. Proven catchable: a planted 790-statement uncovered module let the **aggregate pass at 80% while the per-package gate failed at 79%** — the exact failure the criterion was written to prevent |
| VR-039's composed oracle was disclosed only in code | Row added to `data-model.md` §Uncovered Requirements where every other published exposure lives |
| `data-model.md` and `plan.md` both assigned VR-001…039 to the validator, but VR-034 is in the test suite and the validator registers 59 rules, not 60 | Corrected in both documents and in a third place found during the fix — `validate.py`'s module docstring carried the same wrong range |
| `plan.md` listed `test_corpus_render.py`, which never existed, and omitted four test files that do | Structure block corrected; listing now matches the tree exactly, 13 test files and 16 corpus modules |

Remaining, and inherent rather than fixable: that the pull-request run occurred; that FR-036's sign-off was given; that the FR-008c digest was taken at retrieval rather than back-filled; that a release record carries a re-verification outcome (zero tags exist); and that a committed document is *visually* degraded. Each is disclosed at its own site.

## Code Coverage

**93%** — threshold 80, PASSED. `TOTAL 3783 stmts, 213 miss, 1122 branch, 126 BrPart`.

SC-026's side condition also holds and is **now gated in CI**: `model.corpus` measured alone is **93.0%** (3474 stmts), so the widened package carries itself rather than being floated across the threshold by the already-covered roster. Lowest single corpus module is `generate.py` at 81%. The four network-path modules — `retrieve.py`, `reverify.py`, `sources.py`, `equipment.py` — are at 100%, covered by assertions rather than by import.

The combined figure is not inflated by the orchestration deselection: all five `tests/checks/helpers/*` modules report ≥86%, so none is reachable only through the deselected module.

## Checklist Fulfillment

All three checklists closed before implementation: Data Integrity 40/40, Testing 40/40, Security 40/40. Spot-check of the Security and Testing categories against the implementation found no gaps — the path-containment, redirect, supply-chain, and offline controls each landed with a failing-direction case, and the property-based specification (relation class, generator domain, example count, named boundary cases) is implemented as written.

## Performance

SKIPPED — no performance NFR. The spec's Excluded section records the absence of size, weight, page-count, and validation-runtime bounds as an explicit decision with cause and reversal trigger.

## Accessibility

SKIPPED — no accessibility NFR; this epic ships no interface.

## Browser Runtime Validation

SKIPPED — not required. This epic produces committed files and command-line entry points; it touches no file under `src/web`.

## Manual Testing

None required. No `manual-test.md` generated.

## Tool Recommendations

None outstanding. Every tool the plan configures was already present (`uv 0.8.14`, ruff, import-linter, pytest, Hypothesis, coverage.py). No installation was performed or requested.

## Bug Tasks Generated

**None.** No required category failed. The five traceability gaps were closed in place rather than deferred into bug tasks, and each fix carries its own failing-direction evidence.
