# QC Report: Monorepo Scaffold and Contracts

**Date**: 2026-07-25 · **Iterations**: 2
**Feature Directory**: `specs/00001-monorepo-scaffold-and-contracts`
**Overall Verdict**: **FAIL** — one item. Iteration 3 scoped to OBJ7; `verify.yml` was the only changed file.

## Changes from Prior Run

| Metric | Iteration 1 | Iteration 2 | Delta |
|---|---|---|---|
| Tests | 86 | **110** | +24 |
| Coverage | 94% | **95%** | +1 |
| Success criteria verified | 12/18 | **17/18** | +5 |
| Objectives fully passed | 3/7 | **6/7** | +3 |
| Open bug tasks | 12 | **0** | −12 |
| CI steps | 21 | **24** | +3 |
| PI violations | 1 CRITICAL | **0** | −1 |

17 bug tasks were closed across two iterations: the 12 from iteration 1, three regressions I introduced
while fixing them, and two gaps iteration 1 recorded as PARTIAL without raising tasks for.

## Summary

| Check | Status | Details |
|---|---|---|
| Build / compile | PASSED | `tsc --noEmit`, `next build`, serving image |
| Static Analysis / Linting *(PI-mandated)* | PASSED | ruff ×4, format ×4, ESLint, Prettier, import-linter ×3 |
| Tests | PASSED | 110 passed, 0 failed |
| Coverage *(PI-mandated)* | PASSED | 95% vs 80% |
| Security *(not mandated)* | WARNING | `pip-audit` unrunnable; `npm audit` 12 high, 0 critical, dev-only |
| PI Compliance | PASSED | Next.js deviation resolved at v1.1.3 |
| Requirements Traceability | **FAILED** | 17/18 SC; OBJ7 unevidenced |
| Checklist Fulfillment | PASSED | 8 spot-checked, 0 gaps |
| Performance / Accessibility / Browser | SKIPPED | No NFRs; web ships no behaviour |

## Iteration 3 — what the dispatch revealed

Both dispatches have now run.

| Run | Input | Result |
|---|---|---|
| #18 | `inject_violation: none` | **success**, 23/24 steps green — **evidences OBJ7 VC1** |
| #19 | `inject_violation: provider-import` | **failure at "Lint (Python)"** — not at the contract |

Run #19 is the first execution of the injection path repaired in T066, and it confirms that fix: the
injection step itself **succeeded**. But the run then failed for the wrong reason. The injected file is
a bare `import anthropic`, which ruff reports as **F401 (imported but unused)**, so lint fails three
steps before `Architecture contracts` ever runs.

SC-013 requires the violated run to *name the violated check*. Naming "Lint (Python)" is not evidence
that the contract works — the contract never executed. Fixed under T071: both payloads now carry an
`__all__` line making the import used. Verified locally on the exact payload — ruff clean, format
clean, contract **BROKEN and named**. One more dispatch evidences it.

This is the dispatch doing its job: it existed to prove the evidence path works, and the first real
execution found that it didn't.

## OBJ7 — the remaining failure

OBJ7 VC1 and VC2 both read *"When the workflow is dispatched"*. **No `workflow_dispatch` run has ever
occurred** — all runs are `push` events. SC-013 is satisfied by the pushed-branch path (run #14 clean,
run #7 failing at `Architecture contracts`), but SC-013's alternative clause governs which branch the
*violated* run uses, not the trigger event. Marking T046 honest was not the same as making it true.

Dispatching requires a token this environment does not hold. **Two clicks in the Actions tab close it**:

1. Actions → `verify` → *Run workflow* on `main`, leaving `inject_violation` at `none` → expect success.
2. Same, with `inject_violation: provider-import` → expect failure at **Architecture contracts**.

The second is worth running specifically because that path was broken until this iteration and has
still never executed — see T066.

## Regressions found and fixed within this run

| ID | What | How it hid |
|---|---|---|
| T066 | The dispatch injection guard fired on the **success** path and killed the step. `git diff` ignores untracked files, so `git diff --quiet` exited 0; `\|\| true` cannot catch an `exit`. | The step is `skipped` in every push run, so 11 green runs never touched it. Introduced by T063's own fix. |
| T067 | Gateway tests ran nowhere in CI — the entry holding the single permitted provider import site. | Added by T065 without a workflow step; local runs passed. |
| T068 | Marker environment hardcoded Python 3.12.7; the image runs 3.12.13. | Harmless against today's two marker forms. |

## Requirements Traceability — 6/7 objectives, 17/18 SC

| ID | Status | Evidence |
|---|---|---|
| OBJ1 | PASSED | `next build` now runs in CI; the boundary test resolves both roots instead of regex-matching config text |
| OBJ2, OBJ3, OBJ6 | PASSED | Unchanged from iteration 1 |
| OBJ4 | PASSED | VC1 via `test_build_context.py` (asks Docker what the build stage holds); VC2 equality now genuine; VC4's blind spot asserted |
| OBJ5 | PASSED | `test_orchestration.py` — pgvector asserted, jobs proven to run, exit, and leave nothing |
| OBJ7 | **FAILED** | No dispatch has ever occurred |
| SC-001…SC-006, SC-008, SC-011…SC-018 | PASSED | 17 of 18 |
| SC-013 | PASSED (qualified) | Runs #14 and #7, both `push` events |

## Verified non-vacuous

Independent re-verification confirmed the fixes are real, not cosmetic: marker evaluation is
load-bearing (substituting the host environment reintroduces `colorama`); `installed_distributions`
uses `check=True` so a Docker failure raises rather than returning an empty set; the two-directional
equality means an empty `installed` fails on `missing`; the orchestration fixture uses `check=True`
so a failed `up` errors rather than skipping; and `ls /build` is compared with `==`, not containment.

## Security Audit — WARNING (not PI-mandated)

- `pip-audit`: SKIPPED. `requests` reads `certifi`, which lacks the locally-installed TLS-interception
  root; `uv` works only via `UV_NATIVE_TLS=1`. Environmental, not a code defect.
- `npm audit`: 12 high, **0 critical**, all `brace-expansion` through ESLint — dev-only, absent from the
  serving image. No fix without a major-version bump.

## Bug Tasks Generated

None. T054–T070 are all closed. OBJ7 needs a dispatch, not a code change.