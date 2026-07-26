# QC Report: Public Corpus and Manifest

**Feature**: `00002-public-corpus-and-manifest` | **Epic**: E002 | **Date**: 2026-07-26
**Run**: full re-run (second run; prior report superseded) | **Instructions**: `project-instructions.md` v1.2.1
**Coverage target**: 80 | **Required categories**: linting, coverage | **Profile**: standard
**Tree under test**: merged head `43a8514` — the branch is level with `origin/main`, which is what the figures below describe.

## Changes from Prior Run

| Metric | Previous | Current | Delta |
|---|---|---|---|
| Model unit tests | 612 passed | **1050 passed** | +438 (E003's schema suite now in the same entry; +3 from `f21266f`) |
| Model tests skipped | 0 | **0** | — (435 schema tests executed against a live migrated database) |
| Cross-entry checks | 108 passed, **8 deselected** | **141 passed, 0 deselected** | +33; the orchestration suite ran for the first time |
| Gateway | 5 passed | 5 passed | — |
| Web | not run | 3 passed, build clean | newly exercised |
| `corpus-validate` | 59/59, 0 skipped | 59/59, 0 skipped | — |
| Coverage, aggregate | 93% | **93%** | — (4117 stmts vs 3783; denominator grew) |
| Coverage, `model.corpus` alone | 93.0% | **93%** | — |
| Lockfile packages (model) | 81 | 89 | +8 (merge consequence) |
| Success criteria PASSED | 26 | **27** | +1 (SC-022) |
| US5 | PARTIAL (1.5/2) | **PASSED (2/2)** | closed |
| Instructions version audited | v1.2.0 | **v1.2.1** | header corrected |

No regressions. Two categories improved because evidence that could not be gathered before now exists: the orchestration suite runs (the host-port collision that blocked it is fixed), and the pull-request run record exists.

## Summary

## Overall Verdict: PASS

All required categories passed. Two things are recorded rather than resolved away:

1. **SC-001 remains a published miss** — 51 documents against a stated 45–50, criterion unamended, per Principle VII.
2. **A defect escaped the prior QC pass** and was fixed after `.qc-passed` was written. It is recorded below with an honest account of its severity, which is narrower than first reported.

## Escaped Defect — recorded, not resolved away

The prior run wrote `.qc-passed` at `2026-07-26T00:00:00Z`. Commit `f21266f` landed after it, fixing a defect that pull-request CI found and QC did not.

**What was wrong.** `resolve_within` in `src/model/src/model/corpus/paths.py` judged whether a manifest `location` was absolute by asking `pathlib`, which answers for the running operating system. To a `PosixPath`, `"C:/Windows/win.ini"` is a *relative* path two segments deep whose first component happens to be named `C:`, and it reports no drive. VR-009's refusal therefore fired on a Windows developer machine and not on Linux — the platform CI runs on, the platform the container image runs on, and the platform this plan's own Testing Strategy names as the *platform of record* for byte-identity claims.

**Severity, stated precisely.** Verified rather than assumed: joining `C:/Windows/win.ini` onto the base yields `<base>/C:/Windows/win.ini`, which resolves **inside** the base, so containment was never breached and nothing escaped `data/corpus/`. The traversal spelling `C:/../../../../etc/passwd` resolves outside the base and was still refused — by the containment comparison in step 4, not by the name test in step 1. What was actually lost is that **VR-009 did not refuse by name**: a Windows-authored absolute value was silently reinterpreted as a relative filename and surfaced, if at all, as a caller's missing-file error carrying no rule id. This is a defence-in-depth and correctness defect, not a demonstrated path-traversal escape. An earlier characterisation of it as an inert traversal guard overstated it.

**Why QC missed it.** The negative test asserted a single spelling of the input class, so exactly that spelling was guarded — and it passed on Windows for the same reason the code failed on Linux: both consulted the host instead of the string. The prior report's claim that *"every symlink and platform-dependent case ran on this machine"* was true as written and misleading in effect, because the platform-dependent case that mattered passed vacuously. **The prior run's evidence was single-platform.** That is the honest correction.

**Evidence trail.**

| Item | Record |
|---|---|
| Detected by | https://github.com/jsh562/Procurement-Risk-Demo/actions/runs/30220850872 — failed at step 16; steps 17–23 never ran |
| Fixed in | `f21266f` |
| Verified by | https://github.com/jsh562/Procurement-Risk-Demo/actions/runs/30222959869 — all 23 steps |

**Fix.** Absoluteness is now a property of the string, judged under both path flavours, covering the forward-slash, backslash, lower-case, drive-relative (`C:doc.pdf` — a drive with no root, absolute under neither flavour) and UNC forms. The test is parameterised over four spellings and asserts `VR-009` is named rather than that some error occurred, which is what SC-025 actually requires. `paths.py` is the only source file in the repository that tests path absoluteness at all; no sibling site carries the same assumption.

**Cross-epic check.** The same Linux reproduction was run against `main` covering E001 and E003: model suite 1050 passed with zero skips, `corpus-validate` 59/59, gateway 5, cross-entry (non-Docker) 89. No sibling defect found. The sweep looks in one direction only — it would not catch a Windows-only defect — and cannot execute the five Docker-dependent checks.

No bug task is generated: the defect is fixed and no check currently fails.

## Test Results — PASSED

| Suite | Command | Result |
|---|---|---|
| Model unit (CI invocation, under coverage) | `uv run --directory src/model coverage run --source=src/model/roster,src/model/schema,src/model/corpus -m pytest tests -q` | **1050 passed**, 0 failed, **0 skipped**, 0 deselected |
| Gateway | `uv run --directory src/gateway pytest -q` | **5 passed** |
| Cross-entry checks | `uv run pytest tests -q` (no deselect) | **141 passed**, 0 deselected |
| Web | `npm test` / `npm run build` in `src/web` | **3 passed**; build compiled 4 static pages |
| Corpus validation (epic gate) | `uv run --directory src/model corpus-validate` | **59 rules, 59 passed, 0 failed, 0 skipped**, exit 0 |

**Zero skips, and that was verified rather than assumed.** `DATABASE_URL` was set against a live migrated PostgreSQL, so all 435 schema tests executed; `pytest --collect-only` collects 1050, matching the passed count. Without that variable roughly 374 tests skip silently and the suite still reports green — a green run that skipped them would say nothing about this tree.

**The orchestration suite ran.** The prior report deselected `tests/checks/test_orchestration.py` and classified its failure as environmental. It now executes: the resolver substituted host port 5435 and emitted the designed warning naming the holder — `db: 5435 — substituted because the committed default 5434 is held by container 'kayademoprocurementrisk1-db-1'`. That warning is the disclosure working as specified, not a failure. The orchestration contract (SC-009, SC-010) is verified on this machine for the first time.

## Failure Index

None. No test failed in any suite.

## Code Coverage — 93%

**93%** aggregate — threshold 80, PASSED. `TOTAL 4117 stmts, 235 miss, 1164 branch, 136 BrPart`, combined from three data files (model, gateway, root checks).

SC-026's side condition holds and is gated in CI: `model.corpus` measured alone is **93%** (3475 stmts), so the widened package carries itself rather than being floated across the threshold by the already-covered roster. Lowest corpus module is `generate.py` at 81%. The four network-path modules — `retrieve.py`, `reverify.py`, `sources.py`, `equipment.py` — are at 100%, covered by assertions rather than by import.

The figure is no longer qualified by a deselection: the orchestration module now runs, so the check-harness helpers are measured through their real caller.

**Observation, not a failure.** The aggregate gate has no per-file floor, so `src/model/schema/url.py` (73%) and `env.py` (76%) pass by averaging. That is the configured behaviour of `fail_under` on the total, and both files belong to E003.

## Static Analysis — PASSED

| Check | Result |
|---|---|
| `ruff check` (root / model) | All checks passed |
| `ruff format --check` (root / model) | 167 / 66 files already formatted |
| `import-linter` model | 2 kept, 0 broken — computation boundary, and corpus→provider |
| `import-linter` gateway | 1 kept, 0 broken |
| `import-linter` api | 1 kept, 0 broken |
| `uv lock --check` | passes at root (10), gateway (32), api (39), model (89) |
| Web: `prettier --check`, `tsc --noEmit`, `npm run lint` | exit 0; **1 warning** |

Architecture contracts are treated as build-gating tests per `project-instructions.md` §Testing & Quality Policy. The corpus→provider contract is FR-022's enforcement and requires `include_external_packages = true` to load at all, since `gateway` sits outside the `model` root package.

**One warning, not owned by this epic**: `src/web/__tests__/boundary.test.ts:1` — `'readFileSync' is defined but never used`. ESLint exits 0, so CI does not fail on it. It is a real unused import in an E001 file; recorded here rather than fixed, because this epic touches no file under `src/web`.

## Security Audit — SKIPPED (not a required category)

Not required by policy (`Profile: standard`, required categories are linting and coverage). No scanner was run and none is claimed. Per the SKIPPED-escalation rule this surfaces as a **WARNING**, not a pass.

What *is* evidenced: `uv lock --check` resolves 89 packages for the model entry, exit 0. `tests/checks/test_supply_chain.py` passes — no alternate index under any entry, no stray `uv.toml`, images digest-pinned, and the credential scan covers the `model` entry and the `data/` tree.

Supply-chain posture for the added distributions: exact pins throughout, sha256 artifact hashes on every sdist and wheel, all resolving from the default public index. The GPL `rfc3987` is **absent** — `jsonschema[format-nongpl]` pulled `rfc3987-syntax` instead, which is what SC-028 exists to evidence. One weak-copyleft entry, `fqdn` (MPL-2.0), is file-scoped and named rather than buried.

No dependency-vulnerability scanner is configured project-wide; its absence is a recorded scope decision with cause, owner, and reversal trigger. Nothing was installed during this run.

## Project Instructions Compliance — PASSED

Audited against **v1.2.1**. The tree changed by one commit since the last compliance audit (`f21266f`: a portability fix inside `model.corpus` and its test). It adds no dependency, no datastore, no provider import, and no new entry, so no previously cleared judgement is disturbed.

| Principle / Policy | Finding |
|---|---|
| I. Traceable or It Does Not Ship | Every requirement maps to a task and a check; the 14 that cannot be mechanically observed are disclosed in `data-model.md` §Uncovered Requirements rather than asserted |
| III. Precision Over Recall Where a Mistake Is Silent | The escaped defect is the live example: VR-009 failing silently rather than refusing by name is exactly this principle's concern, and the fix restores the loud failure |
| VII. Publish the Miss | SC-001 stands unamended at 45–50 against a shipped 51; the escaped defect is published above rather than absorbed into a passing report |
| VIII. Honest Opponents | Negative fixtures plant real violations against each contract and execute inside the pull-request run |
| Testing & Quality Policy | 80% floor met at 93% aggregate and 93% per-package; architecture contracts build-gating |
| Technology Stack / Infrastructure | Console entry points for modeling-owned jobs, per ADR-0011 |
| Source Code Layout | All source under `/src/model`; corpus under `data/`; no new entry |
| Data Provenance | Public-domain or synthetic only; per-layer provenance per v1.2.0; copyrighted standards cited, never included, verified over 1,167 extracted pages |
| Governance | The compliance gate was re-run against v1.2.1 as required after the version moved; this report's header now names the audited version correctly |

No violations.

## Requirements Traceability — 5/5 work items verified, 28/28 SC verified

**Work items** — US1 PASSED (4/4) · US2 PASSED (11/12 repo-verifiable) · US3 PASSED (12/12) · US4 PASSED (6/6) · **US5 PASSED (2/2)**.

US5 closes. Its committed halves were already asserted — `pull_request` declared against `main`, `pull_request_target` absent, `permissions: contents: read`, and every contract's negative fixture naming its contract inside an unconditional step. The remaining half, that a run actually happened, is now on the record below.

**Success criteria**: 28 total. **27 PASSED. 1 FAILED — published.**

> **SC-022 — PASSED** (was PARTIAL). The criterion splits its evidence three ways and names this report as the home of the third. All three now hold: the trigger half is asserted by a committed check that reads the workflow file; the failing-check half is carried by the negative fixtures, which executed inside a pull-request run at step 22; and the run record is:
>
> - **Run**: https://github.com/jsh562/Procurement-Risk-Demo/actions/runs/30222959869 — event `pull_request`, head `f21266f`, conclusion **success**, all 23 steps executed
> - **Pull request**: https://github.com/jsh562/Procurement-Risk-Demo/pull/3 — merged as `43a8514`
>
> Steps 17–23 (corpus validation, gateway, web tests, web build, image build, cross-entry checks, coverage gate) ran on CI for the first time in this run; the earlier failing run stopped at step 16 and never reached them.

> **SC-001 — FAILED, published.** The criterion states 45–50 documents; the corpus is **51**. Cause: 26 long-lead UFGS sections were verified reachable and individually justified where the criterion floors at 20, and SC-010 floors the synthetic layer at 25. Trimming a legitimately retrieved public-domain section to reach a round ceiling would optimise the number over the corpus, and the closed exclusion-cause enum has no cause that honestly describes it. The criterion is **not amended** — Principle VII forbids adjusting a target to match a result. 51 remains inside the project's 30–60 envelope, and SC-001's real-layer half is met independently: 26 ≥ 20 documents over 26 distinct sections ≥ 6, spanning Divisions 26 and 23.

SC-007 is materially repaired by `f21266f` rather than merely re-asserted: VR-009's written claim that a drive-letter prefix is absolute and fails did not hold on Linux until this commit. SC-025 re-checked mechanically at head — all 72 rule ids appear in a failing-direction assertion naming the rule.

**Requirements**: 58 total. 44 PASSED. 13 **PARTIAL — disclosed**, each with its cause and bound recorded in `data-model.md` §Uncovered Requirements: FR-001, FR-002, FR-004, FR-005 / FR-011, FR-008a / FR-008c, FR-008b, FR-009b, FR-031a, FR-031b, FR-034a. 1 with **no mechanical verification and none claimed**: FR-036, an assigned review gate with an accountable owner. No requirement changed category this run. FR-018's basis moved from asserted-but-untrue-on-CI to enforced-and-tested-over-four-spellings.

## Traceability Gaps

| Gap | Status |
|---|---|
| SC-022's run record absent from this report | **Closed by this run** — recorded above with URL |
| `.qc-passed` attested a tree carrying the VR-009 defect | **Closed by this run** — the escape is published above and the marker is re-stamped against `43a8514` |
| Prior report figures stale (1039 tests, 81 packages) | **Closed by this run** — regenerated at 1050 and 89 |
| **US1 AS4 has no instance in the shipped corpus** | **Open, disclosed.** All 26 REAL entries carry distinct MasterFormat numbers (UNIFIED 21, USACE 3, NAVFAC 2), so no number is vendored under two agency variants. The mechanism is implemented and negatively tested (VR-021 uniqueness over `(section, variant, revision_date)`; VR-025 counts distinct sections by number), but the scenario's antecedent is unrealised. **Satisfied by mechanism, not by population** — the prior report scored US1 4/4 without recording this. |
| **SC-025 completeness has no durability gate** | **Open, disclosed, pre-existing.** The three registry tests in `test_corpus_validate.py` assert `expected <= registered` (subset), so a newly registered rule shipping without a failing-direction case would fail nothing. The 72-rule sweep is a one-time task-level check (T063), not a standing assertion. Not a regression; carried forward as a known limit. |

## Implementation Review Findings — SKIPPED

No `.review-findings` file present; no `priorityChecks` supplied.

## Checklist Fulfillment — 80/80 spot-checked

All three checklists closed before implementation: Data Integrity 40/40, Testing 40/40, Security 40/40. Spot-check of the Security and Testing categories against the implementation found one gap, now closed:

- **Path containment** — the checklist requires a failing-direction case per control. One existed for the drive-letter case, but it asserted a single spelling and was satisfied by the host's own path flavour. The prior report recorded this control as landing "with a failing-direction case", which was true and insufficient. `f21266f` parameterises it over four spellings and asserts the rule id. **Gap closed.**
- Redirect, supply-chain, and offline controls each landed with a failing-direction case — re-confirmed, no change.
- The property-based specification (relation class, generator domain, example count, named boundary cases) is implemented as written.

## Performance — SKIPPED

No performance NFR. The spec's Excluded section records the absence of size, weight, page-count, and validation-runtime bounds as an explicit decision with cause and reversal trigger.

## Accessibility — SKIPPED

No accessibility NFR; this epic ships no interface.

## Browser Runtime Validation — SKIPPED (not required)

Active probe performed per Step 6.0: the harness exposes no integration-native browser tool, and no MCP browser server is reachable (the connected MCP servers are Gmail, Calendar, and Drive, none browser-capable and all unauthenticated). `BROWSER_RUNTIME_AVAILABLE = false`.

Not required regardless: this epic produces committed files and command-line entry points and touches no file under `src/web`. The web suite and build were nonetheless executed and pass.

## Manual Testing — Not Required

No `manual-test.md` generated.

## Tool Recommendations

- **Dependency-vulnerability scanning** — not configured project-wide, and the Security category is consequently SKIPPED→WARNING. Install with `uv add --dev pip-audit` at an entry, then `uv run pip-audit`. Recorded as a scope decision with cause, owner, and reversal trigger; not an unmet obligation of this epic.
- No tool the plan configures is missing. `uv`, ruff, import-linter, pytest, Hypothesis, and coverage.py were all present. Nothing was installed during this run.

## Bug Context

None. No check failed in this run.

## Bug Tasks Generated

**None.** No required category failed. The escaped defect was already fixed in `f21266f` before this run and is recorded above rather than re-opened as a task; the two open traceability gaps (US1 AS4's unrealised antecedent, SC-025's missing durability gate) are disclosed limits, not failures, and neither blocks the verdict.
