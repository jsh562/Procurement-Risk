# QC Report: Public Corpus and Manifest

**Feature**: `00002-public-corpus-and-manifest` | **Epic**: E002 | **Date**: 2026-07-27
**Run**: Implement+QC loop, 3 iterations — judging the uncommitted FR-037 amendment | **Instructions**: `project-instructions.md` v1.2.4
**Coverage target**: 80 | **Required categories**: linting, coverage | **Profile**: standard
**Tree under test**: branch `00002-public-corpus-and-manifest`, committed at `050d289` and then merged with `main` at instructions v1.2.4. The full-suite figures below were measured before that merge; the merge brought `project-instructions.md`, `specs/00003-core-data-schema/spec.md` and `tests/checks/test_dependency_isolation.py`, and the re-verification of the affected cross-entry checks is recorded at the end of this report.

## Changes from Prior Run

| Metric | Prior run (FAIL) | This run | Delta |
|---|---|---|---|
| Overall verdict | **FAIL** | **PASS** | resolved |
| Model unit tests | 1051 passed, **4 errors** | **1119 passed, 0 errors** | +68, errors cleared |
| Tests skipped | 0 | **0** | — |
| Cross-entry checks | 141 passed | **142 passed** | +1 (T070's regression test) |
| Validation rules | 64 | 64 | — |
| VR-070 / VR-071 values actually judged | **34 of 50** | **50 of 50** | +16 |
| `manufacturers.py` coverage | 72% | **100%** | +28 |
| Coverage, aggregate | 92% | **93%** | +1 |
| Coverage, `model.corpus` alone | 92% | **93%** | +1 |
| Open bug tasks | 6 | **0** | all closed |

No regressions.

## Summary

## Overall Verdict: PASS

The FR-037 amendment is verified. Three loop iterations, seven bug tasks raised and closed, each fix independently re-verified by a fresh audit rather than accepted on the implementer's report.

**SC-001 remains a published miss** — 51 documents against a stated 45–50, unamended, per Principle VII. The amendment did not change the document count.

## What the loop found, and why it took three iterations

Recorded because the pattern is the useful part, not the individual defects.

**Iteration 1 — six defects, all in code the amendment added.** The worst two were failures of verification rather than of function:

- **T065**: the reproducibility fixture could not import the generator in its alternate checkout, because `GENERATOR_INPUTS` was a second, restated copy of the generation-input list that the production tuple did not reach. Four tests errored at setup, so VR-040a/VR-041/VR-042 byte-identity was *unverified* while appearing green. This falsified a claim the amendment had written into `manifest.py` — that "adding a member here is the whole change". The fix derives the fixture's list from `GENERATION_INPUT_PATHS`, making the claim true rather than aspirational.
- **T066**: `_printed_field_values` harvested a value only when it shared a line with its label. The renderer wraps 16 of 50 onto the next line, so VR-070 and VR-071 reported a population of 50 and judged 34. VR-066 could not catch it — `observed` looked full. The rules' own docstrings had justified the blanks as deliberate irregularities, which was false: `PRJ-004-T0003-R0.pdf` declares no irregularity and still read blank.

Also T067 (SC-029 named two clauses no rule judged), T068, T069 (72% coverage), T070 (a pre-existing port-search overflow).

**Iterations 2 and 3 — the same defect class three times: a partial sweep.** T068 fixed the five stale-arity sites it was handed and left twelve, including a self-contradiction where FR-037 declared the manufacturer catalogue a generation input while FR-009b still enumerated three — and FR-009b's own text requires the enumeration be extended "in the same change that gives the generator a further committed input". T071 fixed twenty-nine sites. The audit then found that the notice T071 added to preserve the dated checklist notes *itself* undercounted, naming four moved counts where six moved. Iteration 3 corrected it and stated why the notice enumerates rather than summarises.

Three iterations because each fix addressed the citations it was given rather than the class. That is worth recording next to the fixes.

## Test Results — PASSED

| Suite | Command | Result |
|---|---|---|
| Model unit (CI invocation, under coverage) | `uv run --directory src/model coverage run --source=src/model/roster,src/model/schema,src/model/corpus -m pytest tests -q` | **1119 passed**, 0 failed, 0 errors, **0 skipped** |
| Gateway | `uv run --directory src/gateway coverage run -m pytest tests -q` | 5 passed |
| Cross-entry checks | `uv run coverage run -m pytest tests -q` (no deselect) | **142 passed**, 0 skipped |
| Web | `npm test` / `npm run build` | 3 passed; Next.js 16.2.12 build clean, 4/4 static pages |
| Corpus validation (epic gate) | `uv run --directory src/model corpus-validate` | **64 rules, 64 passed, 0 failed, 0 skipped**, exit 0 |

**Zero skips, verified not assumed.** `tests/schema` collects 435 tests and all executed against a live migrated PostgreSQL. Without `DATABASE_URL` roughly 374 of them skip silently and the suite still reports green.

**The serving image was rebuilt before the cross-entry checks**, as CI does. The `procurement-api:e001` tag is not namespaced per checkout and a sibling checkout on this host had re-pointed it; a stale image produces four false failures in `test_image_contents.py`. Recorded so the next run does not re-diagnose it.

## Failure Index

None. No test failed in any suite.

## Code Coverage — 93%

**93%** aggregate — threshold 80, PASSED. `4405 stmts, 243 miss, 1298 branch, 144 partial`, combined from three data files.

Per-package `model.corpus` alone: **93%**, also passing.

`manufacturers.py`, the module this amendment added, is at **100%** — 130 statements and 58 branches, none missed. It entered the loop at 72%, passing only by averaging into the aggregate, which is the case `verify.yml`'s per-package gate exists to prevent. Lowest files in the package are now `generate.py` 82%, `model.py` 84%, `irregularity.py` 86% — all clear of the floor.

## Static Analysis — PASSED

| Check | Result |
|---|---|
| `ruff check` (root / model) | All checks passed |
| `ruff format --check` (root / model) | 178 / 68 files already formatted |
| `import-linter` model | 2 kept, 0 broken — computation boundary, and corpus→provider |
| `import-linter` gateway / api | 1 kept, 0 broken each |
| `uv lock --check` | passes at root (10), model (89), gateway (32), api (39) |

## Security Audit — SKIPPED (not a required category)

Not required by policy; no scanner run and none claimed. Surfaces as a **WARNING** per the escalation rule. The amendment added no dependency — the model entry still resolves 89 packages, and `tests/checks/test_supply_chain.py` passes.

## Project Instructions Compliance — PASSED

Audited against **v1.2.4**. The gate was re-run when `main` moved to v1.2.4 mid-review, under the Governance rule that a feature audited against a superseded version re-runs its compliance gate before its next phase gate.

**v1.2.4 names `mypy` as the Python type checker and scopes it to `/src/gateway`.** The amendment is explicit that the scope is deliberate rather than provisional — retrofitting `/src/model`'s modules and `/src/api` is recorded as a separate decision, and it states that naming a checker which does not run over two thirds of the Python source would be the unverifiable claim that section exists to prevent. This epic touches no file under `/src/gateway`, so the new obligation does not reach it. That is a finding of inapplicability, not an exemption: were the scope later widened to `/src/model`, `manufacturers.py` and the amended `validate.py` would fall under it and this record would be the wrong one to rely on.

v1.2.2 and v1.2.3 concern Feature Workspace numbering; this workspace is `00002` and its epic is E002, which already satisfies the convention v1.2.3 makes explicit.

| Principle | Finding |
|---|---|
| I. Traceable or It Does Not Ship | FR-037/037a/037b and SC-029 now carry traceability rows in `data-model.md` and `plan.md`; they had none when the amendment first landed, which nothing had flagged |
| III. Precision Over Recall Where a Mistake Is Silent | T066 is this principle exactly — a rule reporting 50 and checking 34 fails silently and reports green. Now 50 of 50 |
| VII. Publish the Miss | SC-001 unamended at 51 against 45–50; the three-iteration partial-sweep pattern is recorded above rather than smoothed out |
| VIII. Honest Opponents | Every new and amended rule carries a failing-direction test naming it, and the audit confirmed those tests are non-vacuous by mutation |
| Data Provenance | The catalogue is synthetic and enforced against the roster's real-firm exclusion list rather than a second list |

No violations.

## Requirements Traceability — 5/5 work items, 29 SC

**Work items** — US1 PASSED (4/4) · US2 PASSED (11/12 repo-verifiable) · US3 **PASSED (12/12)** · US4 PASSED (6/6) · US5 PASSED (2/2).

US3 returns to PASSED: its byte-identity assertions (VR-040a, VR-041, VR-042) execute again now that T065 is fixed.

**Success criteria**: 29 total. **28 PASSED. 1 FAILED — published** (SC-001).

> **SC-029 — PASSED.** All four clauses are now judged by rules rather than asserted. Measured independently by the audit against a live corpus read: 50 printed manufacturer values with **0 blank and 0 unresolved**; 50 part numbers with **0 malformed and 0 whose prefix names no catalogue manufacturer**; **50 item pairs with 0 prefix↔manufacturer mismatches**; 27 distinct printed spellings, so the alias-presence clause is exercised rather than merely true. Non-vacuity was confirmed by mutation: rotating every maker's spellings one key along produces 50 pairing failures and zero of the weaker prefix-exists failures.

> **SC-001 — FAILED, published, unchanged.** 51 documents against 45–50. Cause recorded in `spec.md`, `tasks.md` and above. Not amended — Principle VII forbids moving a target to match a result.

**SC-019 re-confirmed from the committed manifests without regenerating**: 21 of 25 synthetic documents carry ≥1 irregularity class = **84%** against an 80% floor, with all five closed classes present.

**Requirements**: 61 total. FR-037, FR-037a and FR-037b are enforced, not merely stated.

## Traceability Gaps

| Gap | Status |
|---|---|
| FR-009b enumerated three generation inputs while FR-037 declared a fourth | **Closed** — FR-009b now enumerates four keys, five inputs, and carries a clause forbidding any count stated beside it to disagree |
| FR-037/037a/037b and SC-029 had no traceability rows | **Closed** — rows added in `data-model.md` and `plan.md` |
| Stale seven/three arity across spec, data model, plan, schema, datasheet, source | **Closed** — 29 sites; a repository-wide sweep finds no stale claim in live prose |
| US1 AS4 has no instance in the shipped corpus | Open, disclosed, unchanged |
| SC-025 has no durability gate | Open, disclosed, unchanged — and T067 is what that gap looks like in practice: a criterion naming rules that did not judge it |

## Implementation Review Findings — SKIPPED

No `.review-findings` present.

## Checklist Fulfillment — 80/80 spot-checked

Data Integrity 40/40, Testing 40/40, Security 40/40 remain closed. **No checkbox state changed at any point in this loop** — verified by diff, zero `- [ ]`/`- [X]` deltas in either checklist.

Six `<!-- Evaluator: -->` notes state counts the amendment moved. They are dated records of what was true when each item was closed, so they are preserved verbatim rather than rewritten, and each file carries a dated blockquote naming exactly which counts were superseded. The Testing checklist's path-containment item is now satisfied more strongly than when it closed: the drive-letter case is parameterised over four spellings and the two product-field rules judge their full population.

## Performance — SKIPPED

No performance NFR.

## Accessibility — SKIPPED

No accessibility NFR; this epic ships no interface.

## Browser Runtime Validation — SKIPPED (not required)

Step 6.0 probe: no integration-native browser tool and no reachable MCP browser server. `BROWSER_RUNTIME_AVAILABLE = false`. Not required — the epic ships committed files and console entry points and touches no file under `src/web`. The web suite and build were run regardless and pass.

## Manual Testing — Not Required

No `manual-test.md` generated.

## Tool Recommendations

- **Dependency-vulnerability scanning** — still unconfigured; Security stays SKIPPED→WARNING. `uv add --dev pip-audit`, then `uv run pip-audit`.
- **Namespace the `procurement-api:e001` image tag per checkout.** Four sibling checkouts share this host and overwrite each other's tag, which cost two false diagnoses in this loop. Not an E002 defect; worth a repository-level fix.
- Nothing was installed during this loop.

## Bug Context

None outstanding. All seven bug tasks (T065–T071) are closed and their fixes independently re-verified.

## Bug Tasks Generated

**None this iteration.** T065–T071 were raised and closed within the loop; they remain in `tasks.md § Phase: Bug Fixes` as the record of what was found.

## Scope note on the final iteration

Iteration 3 changed two Markdown files under `specs/00002-public-corpus-and-manifest/` and nothing else. Per the QC skill's re-run scoping, its verification was scoped rather than a fourth full suite: confirmed by grep that no test or rule reads that workspace (E003's five hard-coded-path tests read `specs/00003-core-data-schema/`), re-ran `corpus-validate` at 64/64, re-ran the repository-wide arity sweep clean, and confirmed zero checkbox deltas. The full-suite figures above are iteration 2's, measured on a tree identical to this one in every file any check reads. Stated rather than left for a reader to infer.

## Post-merge re-verification

`main` moved to instructions **v1.2.4** while this amendment was in review, bringing two E003 fixes with it. The merge was clean — no conflicts — and touched three files this branch does not own: `project-instructions.md`, `specs/00003-core-data-schema/spec.md`, and `tests/checks/test_dependency_isolation.py` (+119 lines).

Re-verified on the merged tree rather than assuming the earlier figures carried:

| Check | Result |
|---|---|
| `tests/checks/test_dependency_isolation.py` — the file E003 changed | 18 passed |
| Cross-entry checks, full, no deselect | **145 passed** (was 142; +3 from E003's new isolation tests) |
| `corpus-validate` | 64 rules, 64 passed, 0 failed, 0 skipped |
| `ruff check` (root / model) | All checks passed |

The serving image was rebuilt to CI parity first, because a sibling checkout on this host shares the `procurement-api:e001` tag.

The model, gateway, and web suites and the coverage gates were not re-run after the merge: the merge changed no file any of them reads — `specs/00003-core-data-schema/spec.md` is read only by E003's five hard-coded-path tests, which live in the model suite and passed against that same file before the merge because it was already at this revision on `main`. Stated rather than left implicit, so a reader knows which figures are pre-merge and why that is sound.
