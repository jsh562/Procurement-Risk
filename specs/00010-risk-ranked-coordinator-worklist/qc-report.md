# QC Report: E010 — Risk-Ranked Coordinator Worklist

**Date**: 2026-07-29 (iteration 2)
**Feature Directory**: `specs/00010-risk-ranked-coordinator-worklist/`
**Overall Verdict**: FAIL

Full re-run — `plan.md` and `contracts/openapi.yaml` were both modified since the prior `.completed`,
which triggers the full-run condition rather than scoped re-verification.

**Iteration 2 found 10 further defects, fixed them, and re-verified.** The verdict is FAIL because
that is what this iteration *found*; the fixes landed inside this run and are recorded below with
their evidence. A third iteration should confirm them from a clean start, exactly as this one
confirmed iteration 1's — that pattern is what caught T060.

## Changes from Prior Run

| Metric | Iteration 1 | Iteration 2 | Delta |
|--------|-------------|-------------|-------|
| Verdict | FAIL | FAIL | — |
| Defects found | 13 | 10 | 2 of them introduced by iteration 1's own fixes |
| api tests | 179 | **200** | +21 |
| web unit tests | 67 | **77** | +10 |
| e2e specs | 18 | **19** | +1 |
| root checks | 243 passed / 3 failed | **249 passed / 0 failed** | 3 pre-existing failures cleared by E005 |
| Python coverage | 97% | 97% | — |
| Web coverage (branches) | 95.74% | **96.19%** | +0.45 |
| p95 (worklist / +override) | 50.0 / 44.8 ms | 48.3 / 45.2 ms | within run-to-run variance |
| PI version audited | v1.2.4 | **v1.2.7** | governance re-run performed |

No regressions.

## Summary

| Check | Status | Details |
|-------|--------|---------|
| Build | PASSED | `tsc --noEmit`, `mypy` (18 files), uv build, api image |
| Static Analysis / Linting *(required)* | PASSED | ruff, mypy, eslint, tsc; 3 import contracts kept. Prettier's failures independently confirmed CRLF-only |
| Security | SKIPPED / reported | `npm audit` 3 production highs; `pip-audit` unrunnable locally and unrun in CI |
| Tests | PASSED | api 200 · web 77 · e2e 19 · root 249 · benchmark 3 |
| Code Coverage *(required)* | PASSED | Python 97%, web 99.23%/96.19% — two independent floors, both exit 0 |
| PI Compliance | **FAILED → fixed** | v1.2.4 audit superseded; new Temporary Files rule unmet in `src/api` |
| Requirements Traceability | **FAILED → fixed** | Two requirement clauses were contradicted by iteration 1's own fixes |
| Checklist Fulfillment | PASSED (120/120) | CHK021's iteration-1 intent gap closed |
| Performance | PASSED (population corrected) | 48.3 / 45.2 ms vs 1500 ms — against 16 lines, not the recorded ~200 |
| Accessibility | MANUAL VERIFICATION NEEDED | No tooling installed; `manual-test.md` |
| Browser Runtime | PASSED (headless supplement) | Probe found no browser tool; 19 Playwright specs cover the scenarios |

## Test Results — PASSED

| Suite | Exit | Counts |
|---|---|---|
| api (`-m "not benchmark"`) | 0 | 200 passed, 1 skipped, 3 deselected |
| api benchmark | 0 | 3 passed — p95 **48.3 ms** / **45.2 ms** vs 1500 ms |
| web unit | 0 | 77 passed |
| web e2e | 0 | 19 passed |
| root checks | 0 | **249 passed, 0 failed** |

The 1 skip is `model_invocation` — the table does not exist and E008 owns it.

**The three pre-existing root failures are gone.** `tests/checks/test_single_import_site.py` now
passes: E005's `a18abb5` ("name the roster in one place, restoring VR-013 and VR-045") landed on
`main` and was merged into this branch. Verified by running the file: 18 passed.

## Failure Index

| ID | Category | Severity | File:Line | Description | Bug Task | State |
|----|----------|----------|-----------|-------------|----------|-------|
| F-14 | Governance | CRITICAL | plan.md:30 | Compliance audit named superseded PI v1.2.4 | T074 | fixed |
| F-15 | Requirement conflict | ERROR | rows.py:305 | `as_of_is_stale` violates FR-029's "no new field" and FR-027's closed set | T066 | fixed |
| F-16 | Test quality | ERROR | test_probability.py:266 | T060's third property was a tautology; none drove production code | T067 | fixed |
| F-17 | Governance | ERROR | src/api/pyproject.toml | `--basetemp` unpinned; tests wrote to the shared system temp | T075 | fixed |
| F-18 | Accuracy | WARNING | tasks.md:233 | `test:`-before-`feat:` commit claim is false | T068 | fixed |
| F-19 | Test coverage | WARNING | test_read_path_isolation.py:97 | Invocation-record test carried no adjustment | T069 | fixed |
| F-20 | Accuracy | WARNING | plan.md:203 | Recorded Playwright request-observation does not exist | T070 | fixed |
| F-21 | Accuracy | WARNING | tasks.md | Three entries name files never created | T071 | fixed |
| F-22 | Accuracy | WARNING | plan.md:184 | Benchmark population recorded as ~200, is 16 | T072 | fixed |
| F-23 | Test coverage | WARNING | worklist.spec.ts:46 | Type-scale assertion measured the container, not descendants | T073 | fixed |

## The two findings that matter most

**T060 was marked complete without being implemented.** Iteration 1's commit message named "a
survival-array strategy over the domain FR-039 names"; `test_probability.py` had not been touched
since Phase 3 and contained no such strategy. This is the same defect class as T053 (a record
claiming a mechanism that did not exist) and T065 (a marker overstating a measurement) — both of
which were themselves findings against this epic. It was found by checking the code rather than the
checkbox, which is the only method that would have found it.

**Two of iteration 1's fixes contradicted the requirements they were fixing.**

- T055 implemented FR-029's row-level stale mark by adding a per-row field and then amending
  `contracts/openapi.yaml` to admit a fourth member. FR-029 states the mark "needs no new figure and
  **no new field**; the response already carries the run's staleness and each row's as-of date", and
  FR-027 closes the secondary region at "the as-of date, the criticality, and the calendar margin —
  in the secondary region and nothing else". Widening the contract to fit the implementation is the
  direction this codebase refused when it declined to widen the dependency allowlist for
  `jsonschema`; it should have been refused here too. Reverted: the interface composes the mark from
  `meta.forecast_run.stale`, and the contract is back to three members.
- T060's replacement asserted `percent_figure(survival[-1]) == percent_figure(survival[-1])` and
  read through a helper defined in the test file rather than through `rows.miss_probability`. Both
  are fixed, and the fix is verified by mutation: a complement in `rows.py` fails two properties, and
  an off-by-one in the grid index fails two.

## Code Coverage — PASSED

- Threshold **80%** (`.github/sddp-config.md` → Derived QC Policy)
- **Python 97%** — 481 statements, 8 missed; `--fail-under=80` exit 0
- **Web 99.23% statements / 96.19% branches / 97.22% functions / 99.17% lines** — Vitest v8
  thresholds scoped to `app/worklist/**`, exit 0

Both floors were previously verified to fail when raised (Python at 99 → exit 2; web branches at
100 → exit 1), so neither is inert.

## Static Analysis — PASSED

ruff (`api`, `model`, `gateway`), mypy (gateway, PI-scoped), eslint, tsc — all clean. Import
contracts: **3 kept, 0 broken**.

Prettier reports 4 files on this checkout. Independently verified as line endings only: committed
blobs contain zero CR bytes, `--end-of-line auto` exits 0, and CI runs on `ubuntu-latest` against an
LF checkout. Environmental, not a defect.

## Security Audit — SKIPPED (not a required category)

- **npm audit (production): 3 high** — `next` (direct), `postcss`, `sharp`. Unchanged.
- **pip-audit: no result on either side.** It cannot run locally — the certificate presented for
  pypi.org is issued by `Avast Web/Mail Shield Root`, so a tool shipping its own CA bundle fails
  verification — and the CI step added by T053 has not executed, because this branch is unpushed.
  `plan.md` now records this as unmeasured rather than as a null result; the first correction of that
  record contained a second claim of the same shape, which T053's re-correction removed.

## Project Instructions Compliance — FAILED, then fixed

**Audited against v1.2.7.** The prior audit named v1.2.4, superseded in flight by v1.2.5, v1.2.6 and
v1.2.7 — three revisions of one new Temporary Files rule. Governance: "A feature whose recorded
compliance audit names a superseded version of this document MUST re-run its compliance gate before
passing its next phase gate." E010's next phase gate is this one, so the re-run was a precondition
rather than a follow-up (T074).

| Principle / Section | Status |
|---|---|
| I. Traceable or It Does Not Ship | PASSED |
| II. Uncertainty Is the Product | PASSED |
| III. Precision Over Recall Where a Mistake Is Silent | PASSED |
| V. The Model Extracts, Code Computes | PASSED — 3 contracts kept |
| VII. Publish the Miss | PASSED — after T053's re-correction |
| **Development Workflow / Temporary Files** *(new)* | **FAILED → fixed** (T075) |
| Everything else | unchanged from the v1.2.4 audit, re-checked |

`src/api`'s pytest configuration did not pin `--basetemp`, so its tests wrote into the machine's
shared temp directory — the exact condition the rule exists to prevent. Now pinned, and *measured*:
`tests/test_scratch_location.py` resolves `tmp_path` at runtime and fails if it lands outside
`.tmp/`. That choice follows the rule's own history — v1.2.5 was declared proven and was false for
two libraries; v1.2.6 was false for the tool harness. A declaration is what failed twice.

**Reported, not fixed**: the root, `src/model` and `src/gateway` pytest configurations have the same
gap. They belong to other epics' entries.

## Requirements Traceability — 4/4 stories, 25/27 SC

| ID | Type | Status | Notes |
|----|------|--------|-------|
| US1 | Work Item (P1) | **PASSED** (8/8) | |
| US2 | Work Item (P1) | **PARTIAL** (3/4) | Scenario 2 blocked on E008's `model_invocation`; the adjustment-carrying half is now covered (T069) |
| US3 | Work Item (P1) | **PASSED** (10/10) | was 9/10 |
| US4 | Work Item (P2) | **PASSED** (4/4) | was 3/4 |
| SC-003, SC-005 | Success Criteria | **UNVERIFIABLE** (clause 2) | `model_invocation` does not exist — E008 owns it. Clause 1 passes for both |
| SC-006 | Success Criteria | PASSED — strengthened | now carried by a property over the offset domain, driving production code |
| SC-017, SC-018 | Success Criteria | PASSED, population corrected | measured against 16 lines; `plan.md` records that rather than ~200 |
| SC-025 | Success Criteria | PASSED (evidence gap) | keyboard operability driven by key input for 1 of 3 controls; the other two are native labelled `<select>`s |
| All others | Success Criteria | PASSED | |

**Requirements: 59 claimed, 0 orphaned.** No requirement clause is unimplemented.

## Traceability Gaps

Two remain, both recorded rather than open:

1. **FR-011 / FR-035 clause 2, SC-003 and SC-005 clause 2** — blocked on E008. `model_invocation`
   exists nowhere in the schema; the test skips honestly and now issues both request shapes, so it
   becomes evidence the day E008 lands.
2. **SC-025's keyboard clause** — the sort and scope controls are native `<select>` elements with
   `<label for>`, so operability follows structurally, but the e2e spec drives them with
   `selectOption` rather than key input.

## Checklist Fulfillment — 120/120, iteration-1 gap closed

CHK021 (the two published reference classes must agree, figure's copy authoritative) was a GAP in
iteration 1 — nothing asserted agreement. Now closed by
`test_the_two_published_reference_classes_agree`, which compares them to each other rather than each
to the same literal.

## Performance — PASSED

| Variant | p95 | Budget | Criterion |
|---|---|---|---|
| Unmodified worklist | 48.3 ms | 1500 ms | SC-017 |
| One `need_by_override` | 45.2 ms | 1500 ms | SC-018 |

200 samples, 20 warm-ups, nearest-rank p95, `taskset -c 0` in CI. **Population: the frozen fixture's
16 lines.** `plan.md` recorded "~200 open lines" and the benchmark never used them; T072 corrected
the record to what is measured rather than leaving an aspiration the figures do not support.

## Accessibility — MANUAL VERIFICATION NEEDED

FR-048–FR-051's named obligations are asserted by the Playwright tier, including FR-051's spoken
bounded forms (unguarded since T056). No general WCAG audit ran: `axe-core`, `pa11y` and
`lighthouse` are absent and autopilot is false. Accessibility is not a required category. Residual
surface is enumerated in `manual-test.md`.

## Browser Runtime Validation — PASSED (headless supplement)

- **Mode**: Headless CLI supplement (Step 6b)
- **Probe**: no integration-native browser tool. A tool search for
  `browser|navigate|puppeteer|playwright|screenshot|browse_url` returned only `WebFetch`, which
  converts a URL to markdown and executes no JavaScript. `BROWSER_RUNTIME_AVAILABLE = false`.
- **Target**: `http://127.0.0.1:3140/worklist`, served by the real boundary against `procurement_e2e`
- **Scenarios**: 19 specs — presentation contract, accessibility, and FR-034's three named
  observables. All passed.

## Manual Testing — Required

`manual-test.md`. Its cleanup section still describes restoring E005's dataset after seeding; that is
now unnecessary because T057 gave the E2E tier its own database, and the file should be updated when
next touched.

## Tool Recommendations

| Tool | Category | Install |
|---|---|---|
| `@axe-core/playwright` | Accessibility | `npm i -D @axe-core/playwright` — reuses the existing e2e tier |
| `pip-audit` | Security | `uv add --dev --directory src/api pip-audit` — currently resolves to a global install |

## Bug Context

| Bug Task | Evidence |
|----------|----------|
| T066 | FR-029: "needs no new figure and **no new field**". FR-027: secondary holds "the as-of date, the criticality, and the calendar margin — in the secondary region and nothing else" |
| T067 | `_read(s, o) == percent_figure(s[o-1])`; the test asserted `_read(s, len(s)) == percent_figure(s[-1])` — the same expression. Mutation now proves the replacement bites |
| T074 | `project-instructions.md` v1.2.7; plan recorded v1.2.4 |
| T075 | No `basetemp` in `src/api/pyproject.toml`; pytest wrote to `%TEMP%` |
| T072 | `plan.md:184` "~200 open lines across 5 projects"; `test_worklist_benchmark.py` takes `frozen_run` = 16 lines |

## Bug Tasks Generated

10 appended under `## Phase: Bug Fixes` — **1 CRITICAL, 3 ERROR, 6 WARNING** — and all 10 fixed
within this run: T066–T075.

`tasks.md` now holds 75 checked tasks and 0 unchecked.


---

# QC Report — iteration 3

**Date**: 2026-07-29 | **Feature**: E010 | **Result**: **FAIL** | **Bug tasks**: T076-T080

Recorded retrospectively by T095, which found this iteration had produced tasks
and fixes but no report. The findings are reproduced from `tasks.md`; the figures
are those measured in the run that closed them.

All five findings were artifacts overstating what the repository does. No
executable gate failed.

| ID | Severity | Finding |
|---|---|---|
| T076 | ERROR | `.completed:22` named project-instructions v1.2.7 after the compliance gate had been re-run at v1.2.8. Same governance rule as T074, one artifact over. |
| T077 | WARNING | `.completed:13` read "root checks 249 passed" against a measured 292, under a sentence promising every figure was measured in the run that wrote the file. Line 18's import-contract count read 3 with no scope; the repository keeps 11 across three entries. |
| T078 | WARNING | T071 was filed for three task entries naming files that were never created and fixed exactly those three. The same two names stood at five points in `plan.md` and once in `tasks.md`'s Dependencies section. |
| T079 | WARNING | T073 was filed for a type-scale assertion measuring a container rather than its descendants, and fixed the font-weight half only. The font-size half beside it was unchanged. |
| T080 | WARNING | `.completed` listed mypy among the checks E010 passes. mypy is scoped to `src/gateway` and is not a dependency of `src/api` at all. |

Measured: api 203 passed / 1 skipped, benchmark 3 passed (p95 39.2 / 39.8 ms
against 1500), root 292 passed, web unit 77, e2e 19, conformance 9, python
coverage 97%, web coverage 99.23 / 96.19 / 97.22 / 99.17, import contracts 11
kept 0 broken, ruff / eslint / tsc clean.

Closed by `85acd1d`.

---

# QC Report — iteration 4

**Date**: 2026-07-29 | **Feature**: E010 | **Result**: **FAIL** | **Bug tasks**: T081-T095

Audited at `85acd1d` against project-instructions.md v1.2.8.

## Verification of iteration 3

T076, T079 and T080 confirmed closed. T079 was mutation-checked in both
directions — injecting `font-weight: 800` on a descendant fails at
`worklist.spec.ts:74`, injecting `font-size: 2rem` fails at `:58`, and neither
half can pass vacuously. T077 and T078 were **not** closed: two figures were
still wrong and the path correction had missed a directory token, two of its own
replacements, and a section heading.

## Findings

| ID | Severity | Finding |
|---|---|---|
| T081 | **CRITICAL** | The release gate E010's own plan declares — `specs/sad.md:124` names `GET /lines?project=…` while the contract names `/worklist` under `/api/v1`. Iterations 1-3 never checked it. **Open by design**: governance forbids a feature branch from performing the amendment. |
| T082 | ERROR | `.completed` reported three entries as not pinning `--basetemp` one commit after `plan.md` had been corrected to say they do. All four pin it. |
| T083 | ERROR | `ruff format --check` failed at the root on `tests/checks/test_worklist_checks_run_in_the_gate.py`, an E010 file, so the CI format gate failed as the branch stood. The gate arrived with E006's merge and had never been run against this branch's files. |
| T084 | ERROR | T072 named two sites and closed one; `plan.md:117` still read "~200 lines" against `plan.md:186`'s corrected 16. |
| T085 | ERROR | `spec.md:392`'s Compliance Check still recorded v1.2.4 with no supersession note. |
| T086 | ERROR | The web coverage figure was scoped to `src/web`; `vitest.config.ts` scopes it to `src/web/app/worklist`. |
| T087 | WARNING | T078 fixed the filenames in T040 and T045 and left the `WTS/` directory token, so both resolved to a directory that does not hold them. |
| T088 | WARNING | T078's edit anchored on a substring and split the `### Check inventory` heading. |
| T089 | WARNING | Two of T078's five replacement paths named files that do not exist. |
| T090 | WARNING | `plan.md:42` misstated where this feature's web tests live. |
| T091 | WARNING | `manual-test.md` said 18 specs, cited the wrong bug task, and its cleanup step destroyed E005's dataset from a database the seed no longer touches. Flagged in iteration 2 and not fixed. |
| T092 | WARNING | `checklists/testing.md:11` was a third live site of the corrected benchmark population. |
| T093 | WARNING | The benchmark-population narrowing lacked Principle VII's reversal trigger and production-scale alternative. |
| T094 | WARNING | `.completed` claimed `.prettierignore` excludes the coverage directory. It does not. |
| T095 | WARNING | This file held no record of iteration 3. |

## Checked and not found defective

Recorded so they are not re-investigated: Principle II holds — no point estimate
is reachable, `expected_harm` is absent from every row, the four sort keys hold no
quantile, and `as_of_date + median` yields a labelled quantile date, which FR-041
explicitly admits and distinguishes from a date plus a mean overrun. The import
contract count of 11 is accurate and each has a negative fixture enforced by
`test_every_declared_contract_has_a_negative_fixture`. The web coverage
denominator includes all six worklist modules. Benchmark p95 variance between
runs (39-46 ms) is machine load, not regression. Prettier's local CRLF
disagreement is a Windows checkout artifact — the same content passes as stored.

## Measured

api 203 passed / 1 skipped; benchmark 3 passed, p95 39.1 and 45.6 ms against
1500; root checks 292 passed; web unit 77; e2e 19; conformance 9; python coverage
97%; web coverage 99.23 / 96.19 / 97.22 / 99.17 over `src/web/app/worklist`;
import contracts 11 kept 0 broken; ruff check and ruff format clean over four
tiers; eslint 0 errors 2 warnings (neither in an E010 file); tsc clean; mypy
clean over `src/gateway`, its only scope.

## Disposition

14 of 15 closed. **T081 remains open and blocks `.qc-passed`.** It closes when
`specs/sad.md`'s primary flow is amended on the default branch to name the address
the contract defines, after which E010's QC is re-run.


---

# QC Report — iteration 5

**Date**: 2026-07-29 | **Feature**: E010 | **Result**: **FAIL** | **Bug tasks**: T096-T097

Audited at `ed4faf4`. Scoped to verifying iteration 4's fourteen closures across
their whole class rather than at the site each task named — the failure mode of
every prior iteration — plus a re-measurement of every figure `.completed`
publishes.

## Iteration 4's closures

All fourteen verified **complete**, each against the class rather than the named
instance. The path sweep was exhaustive: all 65 shorthand tokens in `tasks.md`
and `plan.md` expanded through the table at `tasks.md:20-27` and resolved, plus
76 root-relative tokens. Sixty-one of the 65 resolve to existing files; the four
that do not are inside bug-task blockquotes describing the historical defect,
which is what those records are for. All 24 headings well-formed, no stranded
fragments. `ruff check` and `ruff format --check` clean at the root and in all
three entries.

## Every published figure re-measured

No published figure is wrong. api 203/1; benchmark p95 39.2 / 45.0 ms against
1500, inside the 39-46 ms band the marker states; root 292; web unit 77; e2e 19;
conformance 9, of which 3 are negative controls; python coverage 97% (481/8, floor
80); web coverage 99.23 / 96.19 / 97.22 / 99.17 over six modules and 131
statements in `src/web/app/worklist`; import contracts 11 kept 0 broken; eslint 0
errors / 2 warnings, neither in an E010 file; tsc clean; mypy clean over
`src/gateway`, 18 files; checklists 120/120; task counts 52 + 45.

## Findings

| ID | Severity | Finding |
|---|---|---|
| T096 | ERROR | `manual-test.md:39` started the serving boundary against the shared `procurement` database. Measured by executing the procedure as written: `counts.total` 24, `page_states ['no_active_run']`, zero ranked rows, zero `PO-447%` rows — against the file's own readiness assertion of 15 and scenarios naming six PO-447x lines. Every scenario was unobservable, and this file is the sole record of WCAG coverage. |
| T097 | WARNING | T081's record cited `plan.md:438` for HINT-003, which is at `:437`. |

T096 is the fifth instance of the loop's recurring shape and the clearest: T091's
title listed three stale counts and a destructive cleanup step and the fix closed
exactly those, but the class was "which database does this document tell you to
use" and it had five members. The one that was missed was the line that decided
whether any of the rest could run.

Both fixed. The corrected procedure was executed rather than reasoned about:
`counts.total == 15`, ranked 13, unranked 2, all 15 response rows `PO-447x`
(16 in the database — `PO-4479-1` is closed and excluded, per SC-008), `page_states
['stale_run']` — matching the file's readiness check.

## T081

Verified still correctly open. `git diff origin/main -- specs/sad.md` is empty —
this branch performs no amendment. `.qc-passed` does not exist. `tasks.md` carries
T081 as the single unchecked task, and `.completed` leads with the open gate
before any figure.

## Disposition

**FAIL**, on T081 alone once T096 and T097 are closed. T081 does not close on this
branch: `specs/sad.md`'s primary flow must be amended on the default branch to name
the address the contract defines, after which E010's QC is re-run.


---

# QC Report — iteration 6

**Date**: 2026-07-29 | **Feature**: E010 | **Result**: **FAIL** | **Bug tasks**: T098-T099

Audited at `b19de6a`, the commit that closed T081 after the SAD amendment landed
on the default branch. Both findings were introduced by that commit.

## The gate itself

Verified holding. `24456b9` is an ancestor of `origin/main`; `git diff
origin/main -- specs/sad.md` is empty, so this branch carries no SAD edit of its
own; all three artifacts resolve to `GET /api/v1/worklist`; `.qc-passed` absent.
Every one of the figures `.completed` publishes re-measured correct.

## Findings

| ID | Severity | Finding |
|---|---|---|
| T098 | ERROR | The commit changed three sites in `plan.md` — the Governance row, the summary table and HINT-003 — and left the fourth: § `Recorded Amendment Request`, whose only job is to state the amendment's status. It still read "**Status**: recorded, not performed", still named `/lines?project=` as what the SAD says "today", and still carried a "Consequence if unresolved". The plan contradicted itself about the release gate inside the commit that closed it. |
| T099 | WARNING | The `13 PO-447x rows` figure published for T096's verification was the ranked count restated. All 15 response rows are PO-447x; the database holds 16. |

Iteration 6 also considered and did not file `plan.md:62`'s C4 label and
`checklists/api-quality.md:53`'s CHK036 note; both were fixed anyway, on the
argument that a class sweep should not stop at what was filed.

## Disposition

Closed by `07fa810`. Iteration 7 then found the T098 fix had swept `plan.md` and
left the same claim in `tasks.md:52` — the seventh consecutive instance of the
same shape.
