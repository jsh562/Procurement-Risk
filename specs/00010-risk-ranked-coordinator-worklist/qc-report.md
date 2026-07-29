# QC Report: E010 — Risk-Ranked Coordinator Worklist

> **Iteration 1's record was restored 2026-07-29 (T114).** It was written at
> `4edb348` and overwritten in place by iteration 2's report at `906dfb4`, so the
> file carried seven of eight iterations while `.completed` said it carried all of
> them. Iteration 1's measurements had survived in the Changes-from-Prior-Run
> table below; its findings and severities had not. Restored verbatim from
> `4edb348` rather than re-derived — a QC record states what was found when it was
> found. Every later iteration appends rather than replaces.

# QC Report — iteration 1

**Date**: 2026-07-29
**Feature Directory**: `specs/00010-risk-ranked-coordinator-worklist/`
**Overall Verdict**: FAIL

Branch `00010-risk-ranked-coordinator-worklist` @ `e0ecf53`. Full run — no prior `qc-report.md`.

Both **required** categories pass. QC fails on requirement compliance: three requirement clauses
are unimplemented, one Principle VII record states falsehoods, and a test fixture this epic added
destroys another epic's dataset.

## Summary

| Check | Status | Details |
|-------|--------|---------|
| Build | PASSED | `tsc --noEmit`, `npm run build` (3 routes), uv build, api image 149 MB |
| Static Analysis / Linting *(required)* | PASSED | ruff, mypy, eslint, tsc clean; 3 import contracts kept, 0 broken |
| Security | SKIPPED / findings | pip-audit blocked by TLS interception; `npm audit` → 3 **production** highs |
| Tests | PASSED (E010 tiers) | api 179, benchmark 3, web 67, e2e 18, gate checks 42, image 34 |
| Code Coverage *(required)* | PASSED | Python 97%, web 99.2%/95.7% — two independent floors, both exit 0 |
| PI Compliance | **FAILED** | Principle VII — T053 |
| Requirements Traceability | **FAILED** | 24/27 SC, 1/4 stories fully PASSED; 3 requirement clauses unimplemented |
| Checklist Fulfillment | PASSED (120/120) with 1 gap | CHK021's agreement rule unasserted — T059 |
| Performance | PASSED | p95 50.0 ms / 44.8 ms vs 1500 ms budget |
| Accessibility | MANUAL VERIFICATION NEEDED | No tooling installed; see `manual-test.md` |
| Browser Runtime | PASSED (headless supplement) | No browser tool exposed; Playwright covered the scenarios |

## Test Results — PASSED (for E010's own tiers)

| Suite | Command | Exit | Counts |
|---|---|---|---|
| api unit + integration | `pytest -m "not benchmark"` | 0 | 179 passed, 1 skipped, 3 deselected |
| api benchmark | `pytest tests/test_worklist_benchmark.py -s` | 0 | 3 passed — p95 **50.0 ms** / **44.8 ms** vs 1500 ms |
| web unit | `vitest run --coverage` | 0 | 67 passed |
| web e2e | `playwright test` | 0 | 18 passed |
| root checks | `pytest tests` | 1 | 243 passed, **3 failed (pre-existing)** |
| image / supply chain | `pytest tests/checks/test_image_*.py test_supply_chain.py` | 0 | 34 passed |
| model entry | `pytest` (shared DB) | 1 | **6 failed, 366 errors — caused by T057** |
| model entry | `pytest` (clean DB) | 0 | 2569 passed |

The 1 skipped api test is `test_serving_the_worklist_records_no_model_invocation` — the
`model_invocation` table does not exist (E008 owns it). The skip is honest; see Traceability Gaps.

**Pre-existing, NOT this epic** — `tests/checks/test_single_import_site.py`, 3 tests. The roster
filename is named in 7 files where 1 is permitted, all in `src/model` from E005/E007. Verified three
ways: `git grep project-vendor-roster main -- src/` returns the identical 7 files;
`git log main..HEAD -- tests/checks/test_single_import_site.py` is empty; `git diff --stat main..HEAD`
touches no file under `src/model`.

## Failure Index

| ID | Category | Severity | File:Line | Description | Bug Task |
|----|----------|----------|-----------|-------------|----------|
| F-01 | PI / Principle VII | CRITICAL | plan.md:247-251 | Security limitation record states two falsehoods; its reversal trigger has fired | T053 |
| F-02 | Requirement clause | ERROR | page.tsx:50 | FR-042 scoping control never on screen in the empty-filter state | T054 |
| F-03 | Requirement clause | ERROR | rows.py:268 | FR-029 row-level stale mark unimplemented | T055 |
| F-04 | Requirement clause | ERROR | Row.tsx:123 | FR-051 bounded forms not announced as words | T056 |
| F-05 | Test isolation | ERROR | seed.py:53 | E2E seed truncates and commits to the shared dev database | T057 |
| F-06 | Test coverage | WARNING | test_worklist_degraded.py | US3-8's retained probability asserted nowhere | T058 |
| F-07 | Provenance | WARNING | rows.py:196 | Per-figure reference class publishes the wrong draw count | T059 |
| F-08 | Test coverage | WARNING | test_probability.py | FR-039 survival-array domain ungenerated; test-first commit evidence absent | T060 |
| F-09 | Test coverage | WARNING | worklist.py:325 | FR-052 untested at the tier that produces it | T061 |
| F-10 | Placement | WARNING | — | FR-024 check absent from the path T026 and the plan name | T062 |
| F-11 | Test coverage | WARNING | fixture.json | FR-036 `need_by_last_in_grid_day` has no named test | T063 |
| F-12 | Test coverage | WARNING | src/api/tests | No response-vs-contract validation | T064 |
| F-13 | Accuracy | WARNING | .completed:11 | Web coverage figures overstated | T065 |

## Code Coverage — PASSED

- Threshold: **80%** (from `.github/sddp-config.md` → Derived QC Policy)
- **Python: 97%** — 480 stmts / 8 miss, 76 branch / 6 partial. `coverage report --fail-under=80` exit 0.
- **Web: 99.2% statements, 95.74% branches, 97.14% functions, 99.13% lines.** Vitest v8 thresholds
  scoped to `app/worklist/**`, enforced by config; exit 0.

Both floors were verified to **fail** when raised, so neither is inert:

| Floor | Raised to | Exit | At 80 | Exit |
|---|---|---|---|---|
| Python | `--fail-under=99` | **2** | `--fail-under=80` | 0 |
| Web | `branches: 100` | **1** | `branches: 80` | 0 |

Lowest files: `api/__init__.py` 50% (1 line), `compute/ordering.py` 93%, `risk_read/rows.py` 94%,
`compute/probability.py` 95%, `compute/ranking.py` 95%.

## Static Analysis — PASSED

| Tool | Scope | Result |
|---|---|---|
| ruff check / format | api, model, gateway | clean — 300 files |
| import-linter | api | **3 contracts kept, 0 broken**; each now has a negative fixture |
| mypy | gateway (PI-scoped) | clean — 18 files |
| eslint | web | 0 errors, 2 warnings (pre-existing, E001) |
| tsc --noEmit | web | clean |
| prettier --check | web | exit 1 — **environmental, not a defect** |

**Prettier**: committed blobs contain **zero** CR bytes (`git cat-file blob HEAD:… | tr -cd '\r' | wc -c`
→ 0); the working tree is CRLF under `core.autocrlf=true`. `--end-of-line auto` → exit 0. CI runs on
`ubuntu-latest` against an LF checkout and passes. Optional hardening: add
`src/web/**/*.{ts,tsx,css} text eol=lf` to `.gitattributes`, or `"endOfLine": "auto"` in `.prettierrc`.

## Security Audit — SKIPPED (not a required category)

- **pip-audit: SKIPPED.** `SSLCertVerificationError` against pypi.org — local TLS interception
  re-signs the connection and pip-audit ships its own CA bundle. Not a vulnerability finding.
  It is also not declared in any `pyproject.toml`; the binary resolved to a global Anaconda install,
  so the result would be machine-dependent even if it ran. Autopilot is false — nothing installed.
- **npm audit (production only): 3 high.** `next` (direct), `postcss`, `sharp`. These falsify the
  plan's limitation record — see T053. They affect the Vercel-deployed web tier; the separate claim
  "absent from the serving image" still holds for the api container.

## Project Instructions Compliance — FAILED

Audited against `project-instructions.md` **v1.2.4** — the current version, so the governance
re-run rule does not fire.

| Principle | Status | Note |
|---|---|---|
| I. Traceable or It Does Not Ship | PARTIAL | Run identity is emitted but untested at its own tier (T061); per-figure reference class publishes a draw count that is not the run's (T059) |
| II. Uncertainty Is the Product | PASSED | No point estimate on any surface — asserted structurally (closed shapes), by scan (no numeric array, no harm score), and on the rendered page |
| III. Precision Over Recall Where a Mistake Is Silent | PASSED | Schema-version refusal, override refusals, structural absence vs explicit empty |
| IV. Agent Output Style | PASSED | — |
| V. The Model Extracts, Code Computes | PASSED | 3 import contracts kept; each has a negative fixture |
| VI. Evaluate Before You Tune | N/A | No tuning in this epic |
| **VII. Publish the Miss** | **VIOLATED** | See T053 |
| VIII. Honest Opponents | N/A | No model claim in this epic |

**Principle VII violation (CRITICAL).** The recorded security limitation has all four required parts,
but two of them state falsehoods and its own reversal trigger has fired without action:

- *Scope decision* — "dependency advisories are surfaced in CI and do not fail the build." No
  `pip-audit` or `npm audit` step exists in `verify.yml`. They are surfaced nowhere.
- *Supporting evidence* — "`npm audit` currently reports 12 high advisories that all chain from
  `brace-expansion` through ESLint — dev-only." Measured: 3 of them are production dependencies.
- *Reversal trigger* — "any advisory affecting a runtime dependency of `/src/api` or `/src/web`."
  **Fired.**

Per `AGENTS.md`, any `project-instructions.md` violation is CRITICAL severity.

## Requirements Traceability — 1/4 stories fully PASSED, 24/27 SC PASSED

| ID | Type | Status | Notes |
|----|------|--------|-------|
| US1 | Work Item (P1) | **PASSED** (8/8) | Ranking, decomposition, dual framing, tie resolution, provider-unreachable — all discharged by executing assertions |
| US2 | Work Item (P1) | **PARTIAL** (3/4) | Scenario 2's observable (model-invocation record) does not exist; its test always skips and carries no adjustment |
| US3 | Work Item (P1) | **PARTIAL** (9/10) | Scenario 8's "shows the forecast probability" has no covering assertion — T058 |
| US4 | Work Item (P2) | **PARTIAL** (3/4) | Scenario 4's wording passes; FR-042's on-screen clause is broken and untested — T054 |
| SC-001 … SC-002 | Success Criteria | PASSED | SC-001's named observable is split across tiers; the e2e test asserts rank-1-unexpanded, the API tier asserts max-harm-first |
| SC-003 | Success Criteria | **UNVERIFIABLE** (clause 2) | Contract half PASSED. Model-invocation half has no observable |
| SC-004 | Success Criteria | PASSED | FR-034's observable asserted at `e2e:278` — all three score inputs in one `li` |
| SC-005 | Success Criteria | **UNVERIFIABLE** (clause 2) | Reorder clause PASSED; invocation-record clause blocked as above |
| SC-006 … SC-016 | Success Criteria | PASSED | Includes the staleness `>` vs `>=` boundary pinned from both sides |
| SC-017, SC-018 | Success Criteria | PASSED | 50.0 ms / 44.8 ms, 200 samples, nearest-rank p95, under `taskset -c 0` |
| SC-019 … SC-024 | Success Criteria | PASSED | SC-020 is the inversion guard (STF-001); SC-024 evaluated over the enumerated content classes |
| SC-025 | Success Criteria | PASSED (evidence gap) | Keyboard operability asserted for the need-by control only; the two selects follow structurally but are driven by `selectOption`, not the keyboard |
| SC-026, SC-027 | Success Criteria | PASSED | Both acknowledgements distinguishable, focus retained; validator two-sided |

**Requirements: 59 claimed, 0 orphaned.** FR-014 is deliberately vacant (STF-002).

## Traceability Gaps

**Three requirement clauses are unimplemented** — these are the load-bearing findings:

1. **FR-042** (T054) — the scoping control is never rendered in the empty-filter state. Confirmed
   against the running endpoint: the response carries `available_projects: ['PRJ-001','PRJ-002','PRJ-003']`
   and `counts.total: 0`; `page.tsx:50` branches on the count and never mounts the controls. The
   payload is correct and the render discards it. A coordinator who filters to a project with nothing
   open cannot leave the scope except by reloading — exactly what the requirement forbids.
2. **FR-029** (T055) — the row-level stale mark. The clause is explicit that a page banner alone is
   insufficient "once rows are sorted, filtered, or read one at a time". No code implements it.
3. **FR-051** (T056) — bounded forms announced as words. `<1%` renders as the raw glyph string; the
   requirement names the exact failure ("a dropped `<` turns `<1%` into a flat `1%`").

**Thinly evidenced** (code present, assertion weak or absent): FR-003 (T059), FR-024 (T062),
FR-030 (T058), FR-036 (T063), FR-039 (T060), FR-052 (T061), FR-057 (T064).

**Blocked by a dependency, not by this epic**: FR-011 / FR-035 clause 2, SC-003 and SC-005 clause 2.
`model_invocation` exists nowhere in the schema — E008 owns it. The test skips honestly. Note that
even once E008 lands, the test issues a request with **no** `need_by_override`, so the
"including one carrying a need-by adjustment" clause would still be uncovered; that half is fixable
now and is folded into T061's neighbourhood rather than blocked.

## Checklist Fulfillment — 120/120 complete, 1 intent gap

`api-quality.md` 40/40, `testing.md` 40/40, `ux.md` 40/40. Spot-checked `[Security]` and `[Testing]`
intent rather than box state:

- CHK005 (run / model / schema version identification) — PASSED in code, **GAP** in evidence → T061
- CHK010 (who may read the worklist) — PASSED, `test_worklist_read_only.py`
- CHK014 (`today` injectable, not a clock read) — PASSED, `now_in_zone` monkeypatched throughout
- CHK021 (two reference classes, which authoritative, must agree) — **GAP**, nothing asserts
  agreement and the per-figure copy is sourced from the wrong table → T059
- CHK030 (stale-run co-occurrence vs a 7-day threshold) — PASSED, `test_states.py`
- CHK033 (response non-shareability) — PASSED, `Cache-Control: private, no-cache` asserted

## Performance — PASSED

`test_worklist_benchmark.py`, under the plan's recorded conditions: warm (20 discarded), 200 timed
samples per variant, p95 by nearest rank, server-side measurement, `taskset -c 0` in CI.

| Variant | p95 | Budget | Criterion |
|---|---|---|---|
| Unmodified worklist | **50.0 ms** | 1500 ms | SC-017 |
| One `need_by_override` | **44.8 ms** | 1500 ms | SC-018 |

A ratio guard (`test_an_adjustment_costs_no_model_call`) catches a provider call hiding inside the
generous absolute budget.

## Accessibility — MANUAL VERIFICATION NEEDED

The named obligations FR-048–FR-051 are asserted by the Playwright tier: position as text, the
quantile pair under one accessible name, state carried by text, keyboard operation of the need-by
control, live region for both acknowledgement outcomes. **One clause is unimplemented** — FR-051's
spoken bounded forms (T056).

No general WCAG audit ran. `axe-core`, `@axe-core/playwright`, `pa11y` and `lighthouse` are all
absent and autopilot is false, so none was installed. Accessibility is not a required category, so
this does not gate. Residual surface — colour contrast, heading order, reflow, focus visibility,
real screen-reader announcement — is enumerated in `manual-test.md`.

## Browser Runtime Validation — PASSED (headless supplement)

- **Mode**: Headless CLI supplement (Step 6b)
- **Browser tool**: N/A — active probe found none
- **Probe result**: no integration-native browser tool is exposed. A tool search for
  `browser|navigate|puppeteer|playwright|web_browse|browse_url|screenshot` returned only `WebFetch`,
  which converts a URL to markdown and executes no JavaScript — it cannot exercise the adjustment
  flow, focus retention, live regions or computed styles. The three configured MCP servers
  (claude.ai Gmail / Calendar / Drive) are unauthenticated and are not browser tools.
  `BROWSER_RUNTIME_AVAILABLE = false`.
- **App start**: Playwright's own `webServer` — uvicorn on 8000 + `npm run build && npm run start`
- **Target**: `http://127.0.0.1:3000/worklist`
- **Scenarios**: 18 specs — presentation contract (type scale by computed style, reading order,
  as-of without hover, sort keys on screen), accessibility (rank as text, pair under one accessible
  name, degraded state as text, keyboard adjustment, both acknowledgements, bounded form), and
  FR-034's three named observables. All passed against the real page served by the real boundary.

One reproducibility note: the port overrides are only honoured after `rm -rf .next`, because Next
inlines `NEXT_PUBLIC_*` at build time and reuses cached client chunks. Worth folding into the
webServer command.

## Manual Testing — Required

`manual-test.md` — the residual WCAG surface no installed tool measures (A1–A7), with startup,
readiness and cleanup steps. Cleanup includes restoring E005's dataset, which T057 currently destroys.

## Tool Recommendations

| Tool | Category | Install |
|---|---|---|
| `@axe-core/playwright` | Accessibility | `npm i -D @axe-core/playwright` — preferred, reuses the existing e2e tier |
| `pa11y` | Accessibility | `npm i -D pa11y` |
| `pip-audit` | Security | `uv add --dev --directory src/api pip-audit` — currently resolves to a global install, so results are machine-dependent |
| `openapi-core` | Contract validation | `uv add --dev --directory src/api openapi-core` — for T064 |

## Bug Context

| Bug Task | Error Output | Related Test |
|----------|-------------|--------------|
| T053 | `npm audit --omit=dev` → 3 high on production deps: `next` (direct), `postcss`, `sharp`. No audit step in `verify.yml`. | — |
| T054 | `GET /api/v1/worklist?project_id=PRJ-009` → `page_states: ['stale_run','empty_filter']`, `counts.total: 0`, `available_projects: ['PRJ-001','PRJ-002','PRJ-003']`; `page.tsx:50` branches on the count | none — the state is untested at the render tier |
| T055 | `build_secondary` returns `{as_of_date, criticality, calendar_margin_days}`; `Row.tsx:182` renders "Forecast as of \<date\>" unqualified | none |
| T056 | `grep -rniE "less than one percent\|greater than ninety" src/web/app src/web/e2e` → no matches | `worklist.spec.ts:245` (guarded, asserts the glyph only) |
| T057 | `forecast-fit refused: ModelError: the sojourn frame is empty; every line supplied had no lifecycle event at or before the as-of date` — `read 16 lines and 6 lifecycle events` | `src/model/tests/forecast/*` — 6 failed, 366 errors on the shared DB; 2569 passed on a clean one |
| T059 | `rows.py:196` and `worklist.py:338` both read `inputs.conventions.draw_count` (from `schema_constants`); only `ck_schema_constants__draw_count_positive` and `ck_forecast_run__draw_count_positive` exist | none |
| T060 | `git show --stat 1b96a5d` → `test_ranking.py`, `test_probability.py`, `ranking.py`, `probability.py` in one commit | — |

## Bug Tasks Generated

13 appended to `tasks.md` under `## Phase: Bug Fixes` — **1 CRITICAL, 4 ERROR, 8 WARNING**:

T053 (CRITICAL), T054, T055, T056, T057 (ERROR), T058–T065 (WARNING).

`.completed` removed.

---

# QC Report — iteration 2

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

Four of the five were artifacts overstating what the repository does; the fifth,
T079, was a test that did not measure what it claimed. No executable gate failed.

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
(16 in the database — `PO-4479-1` is closed and excluded under FR-022), `page_states
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


---

# QC Report — iteration 7

**Date**: 2026-07-29 | **Feature**: E010 | **Result**: **FAIL** | **Bug tasks**: T100-T106

Audited at `07fa810`. Every executable gate passed and every published figure
re-measured correct; all seven findings were in what the feature publishes about
itself.

| ID | Severity | Finding |
|---|---|---|
| T100 | ERROR | `tasks.md:52`'s gotcha still read "The endpoint address is not settled by implementation… the branch records the amendment and does not perform it" — present tense, no closure marker, all three clauses false. T098 was filed because the T081 fix missed a fourth site in `plan.md`; T098's own fix then swept `plan.md` and missed this one, in a different file. |
| T101 | ERROR | `.completed`'s heading read "What four iterations of QC actually found" above six enumerated blocks, in a file whose second line said "six QC iterations". |
| T102 | ERROR | `.completed` claimed *every* post-iteration-1 finding was an artifact overclaim rather than a fault. This file disproves it at `rows.py:305` (T066, a field on the wire FR-029 forbids) and in a pyproject (T075, pytest writing to the machine's shared temp directory). Replaced with the measured split. |
| T103 | WARNING | Four sites cited SC-008 for `PO-4479-1`'s exclusion. SC-008 governs a line the run does not *cover* and requires it be visible; `PO-4479-1` is covered, terminal, and in neither group. The rule is FR-022, which `test_worklist_ranked.py:330` measures. |
| T104 | WARNING | T097's citation of HINT-003 broke a second time — the hint moved from `plan.md:437` to `:442` when T098's fix grew the section above it. |
| T105 | WARNING | This file held no record of iteration 6 — the same omission T095 was filed for at iteration 3. |
| T106 | WARNING | Principle VII's production-scale alternative named "the E005 seeded set". Measured: E005 holds 199 lines across 5 projects of which **24 are open**, FR-022 excluding the other 175 — so it moved the benchmark from 16 rows to 24, not to the ~200 the record calls the working scale. Two further sites asserted "~200 open lines" directly. |

**Measured**: api 203/1; benchmark 3 passed; root 292; web unit 77; e2e 19;
conformance 9; python coverage 97%; web coverage 99.23/96.19/97.22/99.17;
contracts 11 kept 0 broken; ruff/eslint/tsc/mypy clean; E005 199 lines, 24 open.

Closed by `03254ac`.

---

# QC Report — iteration 8

**Date**: 2026-07-29 | **Feature**: E010 | **Result**: **FAIL** | **Bug tasks**: T107-T112

Audited at `03254ac`. The auditor's summary is worth recording verbatim: "The
software is sound and I found nothing wrong with it." Every executable gate
passed and every re-measurable figure was exact. All six findings were in the
record.

| ID | Severity | Finding |
|---|---|---|
| T107 | WARNING | T103 counted four sites and its fix corrected three; `qc-report.md:391` still cited SC-008 for the terminal line. |
| T108 | WARNING | The commit closing iteration 7 inserted its own block between iteration 6's two entries, stranding T099 under the iteration-7 heading — the same anchored-edit failure as T088. |
| T109 | ERROR | `.completed` described iteration 3 as "5 findings, all this marker or the plan overstating", contradicting the split T102 installed one iteration earlier, which classes T079 as a test-quality defect. Same rubric T102 was filed under. |
| T110 | WARNING | This file held no record of iteration 7 — the third recurrence of T095/T105, whose own fix hint named it. |
| T111 | WARNING | Two sites attributed a quotation to `plan.md:46` that only HINT-003 ever carried; the Governance row deferred to it rather than restating it. |
| T112 | WARNING | `.completed` called T100 "T099's sibling finding"; T099 is an iteration-6 finding about a different subject. |

**Measured**: api 203/1 (200+3 deselected without benchmarks); benchmark 3 passed,
p95 45.4/49.7 ms against 1500; root 292; web unit 77; e2e 19; conformance 9
(5 positives, 3 negative controls, 1 construct coverage); python coverage 97%
(481/8); web coverage 99.23/96.19/97.22/99.17 over six modules; contracts 11 kept
0 broken; ruff check and format clean over four tiers; mypy 18 files; tsc clean;
eslint 0 errors 2 warnings; 106 tasks all checked; checklists 120/120; E005 199
lines / 24 open; frozen fixture 16 lines / 15 open.

The auditor re-resolved 150+ `file:line` citations across every artifact and found
one wrong (T111). The T102 split was independently recounted and confirmed
arithmetically correct with defensible membership.


---

# QC Report — iteration 9

**Date**: 2026-07-29 | **Feature**: E010 | **Result**: **FAIL** | **Bug tasks**: T113-T115

Audited at `0e2bcda`, the commit that rewrote `.completed` from 278 lines to 146
by moving the per-iteration narrative here. Every executable gate passed and every
re-measurable figure was exact — including the four ruff file counts, the
131-statement coverage denominator, the 5/3/1 conformance split and the 3/4/4
contract split.

| ID | Severity | Finding |
|---|---|---|
| T113 | ERROR | The rewrite's CRLF caveat read "`npx prettier --check .` **and `ruff format --check`** report files as unformatted that are clean as stored in git". The prettier half is measured and true; the ruff half is false — ruff defaults to `line-ending = "auto"`, preserving each file's existing endings, and measured on this CRLF tree `ruff format --check` reports zero files across all four tiers. The same file said so 67 lines above. Material because T083 was a genuine root format failure that failed CI's `Format check (Python)` step, and its record says "Not a CRLF artifact" — this sentence pre-excused that class. |
| T114 | WARNING | `qc-report.md` carried seven of eight iterations. Iteration 1's report was written at `4edb348` and **overwritten in place** by iteration 2's at `906dfb4`; its measurements survived in the Changes-from-Prior-Run table, its 13 findings and severities did not. Restored verbatim. Fourth instance of the class T095, T105 and T110 were filed for. |
| T115 | WARNING | `test_contract_conformance.py:140` read "the four above" then "five green tests" two sentences later. Five is correct. |

**Measured**: api 203/1 (200 + 3 deselected without benchmarks); benchmark 3
passed, p95 42.6 / 43.5 ms against 1500; root 292; web unit 77; e2e 19;
conformance 9; python coverage 97% (481/8); web coverage 99.23/96.19/97.22/99.17
over six modules and 131 statements; contracts 11 kept 0 broken; ruff check and
format clean over four tiers (43/37/290/577); mypy 18 files; tsc clean; eslint 0
errors 2 warnings; 112 tasks all checked; checklists 120/120; E005 199 lines / 24
open.

The auditor re-resolved 157 `file:line` citations with none out of range, and
independently recounted the classification split.

Closed by `a3da202`.

---

# QC Report — iteration 10

**Date**: 2026-07-29 | **Feature**: E010 | **Result**: **FAIL** | **Bug tasks**: T116-T117

Audited at `a3da202`. Every executable gate green; every published figure except
one re-measured exactly, several to the digit.

| ID | Severity | Finding |
|---|---|---|
| T116 | ERROR | `.completed:115` published "Per-iteration findings … are in `qc-report.md`, which carries all **nine**" against a measured **eight**. The omitted one was iteration 9 — the most recent, and the one a reader deciding on release would most want. The claim was made *stronger* by the same commit that failed to append the record. Fifth instance of the class filed as T095, T105, T110 and T114, and T114 was therefore closed at the site its title named rather than across its class. |
| T117 | WARNING | `qc-report.md:3` attributed iteration 1's restoration to T113; T114 owns it. T113 is the CRLF caveat. The `(TNNN)` convention elsewhere in these artifacts names the owning task. |

**Measured**: api 203/1; benchmark 3 passed, p95 45.8 / 50.4 ms against 1500;
root 292; web unit 77; e2e 19; conformance 9; python coverage 97% (481/8), floor
verified non-inert (`--fail-under=99` exits 2); web coverage
99.23/96.19/97.22/99.17 over six modules, 131 statements; contracts 11 kept 0
broken; ruff clean 43/37/290/577; mypy 18 files; tsc clean; eslint 0 errors 2
warnings; 115 tasks all checked; checklists 120/120; E005 199 lines / 24 open;
frozen fixture 16 lines / 15 open; npm audit 3 high production, 12 total.

Principles I, II, III, V and VII re-checked and passing; phase gates satisfied;
size budgets met; 59 FR IDs matched between `spec.md` and the coverage map with 0
orphaned; `manual-test.md` verified runnable against `procurement_e2e`.

**Process change adopted here.** Five iterations in a row omitted a QC record
because the record was treated as follow-up to closing an iteration rather than
as part of it. From iteration 9 onward the record is appended in the same commit
that logs the bug tasks, before the fixes are made — which is why iterations 9 and
10 are both present above.


---

# QC Report — iteration 11

**Date**: 2026-07-29 | **Feature**: E010 | **Result**: **PASS** | **Bug tasks**: none

Audited at `7b954d1`. **Zero findings at ERROR or WARNING.** Every executable gate
green and every published figure re-derived from a command rather than from a
prior report.

## What was verified

- **T116/T117 closed, and the process change holds structurally.** `qc-report.md`
  carries one section per iteration for 1-10, no duplicate and no gap. The marker
  no longer publishes a count that must be bumped in lockstep. Iterations 9 and 10
  were both appended in the commit that logged their bug tasks, which is the
  behaviour the change describes.
- **Bug-task ranges reconcile to the sections**: 13+10+5+15+2+2+7+6+3+2 = 65
  (T053-T117). No task claimed by two iterations, none by none.
- **The classification split — wrong twice — is now exact.** 65 bug tasks, 13 in
  iteration 1, 52 after. 4 + 5 + 43 = 52, and set arithmetic over T066-T117
  confirms the membership. Every `[governance]` and `[process]` member was checked
  to be an artifact defect rather than a code one.
- **Release gate holds.** `24456b9` is `origin/main`'s head and is contained in
  this branch; `git diff origin/main -- specs/sad.md` empty; T081 checked; four
  address sites resolve to `GET /api/v1/worklist` with no composition required.
- **Principles I, II, III, V, VII** each re-checked against the live payload and
  the contract, not against prior reports.
- **manual-test.md re-executed**: `counts.total == 15`, and every row identity its
  scenarios name resolves and carries the named state.

## Measured

api 203 passed / 1 skipped (200 + 3 deselected without benchmarks); benchmark 3
passed, p95 46.0 / 52.1 ms against 1500; web unit 77; e2e 19 against the real page
and real boundary; root checks 292; conformance 9 (5 positives, 3 negative
controls, 1 construct coverage); python coverage 97% (481 statements, 8 missed);
web coverage 99.23 / 96.19 / 97.22 / 99.17 over six modules and 131 statements;
import contracts 11 kept 0 broken with a negative fixture each; ruff check and
format clean over four tiers (43/37/290/577); mypy 18 files; tsc clean; eslint 0
errors 2 warnings, neither in an E010 file; 117 tasks all checked; checklists
120/120; E005 199 lines / 24 open; npm audit 3 high on production dependencies.

## Observations recorded, not filed

The auditor raised two accuracy points below the finding bar and declined to file
them. Both were corrected in the commit that wrote `.qc-passed`, because shipping
a sentence known to be inaccurate is the defect class this feature spent eleven
iterations on:

- `.completed` said five iterations "in a row" omitted a QC record. The five are
  real and correctly enumerated but were not consecutive. Corrected.
- `plan.md:38` said "both recorded limitations carry their four parts"; there are
  now three in that form, the third added by T093. Corrected.

A third observation is recorded as **not E010's**: the root tier's
`tests/checks/test_ports.py::test_the_substitute_is_actually_bindable` failed on
one of three runs and passed on the other two and in isolation. It is E002's file,
and the failure is a port-availability race against TIME_WAIT sockets left by the
Playwright and uvicorn runs the audit had just performed.

## Disposition

**PASS.** `.qc-passed` written at the commit that carries these two corrections.
