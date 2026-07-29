# QC Report: E010 — Risk-Ranked Coordinator Worklist

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
