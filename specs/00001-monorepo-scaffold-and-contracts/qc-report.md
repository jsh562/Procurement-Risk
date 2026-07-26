# QC Report: Monorepo Scaffold and Contracts

**Date**: 2026-07-25
**Feature Directory**: `specs/00001-monorepo-scaffold-and-contracts`
**Overall Verdict**: **FAIL**

Every executable check passes. QC fails on requirements traceability: one project-instructions
violation and six success criteria that are stated but not actually verified by anything. The
distinction matters — nothing here is broken, but several things claimed as proven are not.

## Summary

| Check | Status | Details |
|-------|--------|---------|
| Build / compile | PASSED | `tsc --noEmit` clean; serving image builds |
| Static Analysis / Linting *(PI-mandated)* | PASSED | 12/12 — ruff ×4, ruff format ×3, ESLint, Prettier, import-linter ×3 |
| Tests | PASSED | 86 passed, 0 failed |
| Code Coverage *(PI-mandated)* | PASSED | 94% vs 80% threshold |
| Security *(not PI-mandated)* | WARNING | `pip-audit` unrunnable; `npm audit` 12 high, 0 critical |
| PI Compliance | **FAILED** | Next.js major version deviates from the declared stack |
| Requirements Traceability | **FAILED** | 12/18 SC verified; 3/7 objectives fully passed |
| Checklist Fulfillment | PASSED | 8 spot-checked, 0 gaps |
| Performance / Accessibility | SKIPPED | No NFRs in `spec.md` |
| Browser Runtime | SKIPPED | Web boundary ships no behaviour this epic |

## Test Results — PASSED

- Runner: pytest 9.1.1 + Vitest. Total **86**, Passed **86**, Failed **0**.
- Cross-entry checks 55 · model unit tests 28 · web unit tests 3.
- CI: run #9 (`main`, `de57fb4`) all steps green. Run #7 (injected violation) failed at
  **Architecture contracts** with every earlier step green — the contracts demonstrably gate.

## Code Coverage — 94%

- Threshold: 80% (`.github/sddp-config.md` → Derived QC Policy). Status: **PASSED**.
- `reader.py` 91%, `source_scan.py` 89%, `contract_runner.py` 96%, `entries.py` 100%, `image_contents.py` 100%.
- Denominator is scoped by TR-006 to the source scan, image checks, and roster reader.
  `src/gateway/src/gateway/provider.py` sits outside it and has **no test** — see W6.

## Static Analysis — PASSED

- ruff 0.16.0, ESLint 9, Prettier, `tsc`, import-linter 2.13. Critical issues 0, warnings 0.
- All three architecture contracts KEPT on a clean tree and BROKEN on planted violations.

## Security Audit — WARNING (category not PI-mandated)

- `pip-audit`: **SKIPPED** — cannot reach PyPI. `requests` reads `certifi`, which lacks the
  locally-installed TLS-interception root; `uv` works only because `UV_NATIVE_TLS=1` reads the
  Windows store. Not a code defect.
- `npm audit`: 12 high, **0 critical**. All chain from `brace-expansion` (DoS) through ESLint —
  **dev dependencies only**, absent from the serving image. `npm audit fix` resolves none without
  a major-version bump.

## Project Instructions Compliance — FAILED

**CRITICAL — Technology Stack deviation.** `project-instructions.md` and `plan.md` both specify
**Next.js 15 (App Router)**. `src/web/package.json` pins `next: 16.2.12` and
`eslint-config-next: 16.2.12`. `create-next-app@latest` installed the current major and nothing
recorded or justified the bump. Per `AGENTS.md`, any `project-instructions.md` violation is CRITICAL.

Verified compliant: four entries under `/src`; no `uv` workspace table; neither Python boundary
declares the other; Python 3.12 / Node 22; roster labelled SYNTHETIC with a datasheet; contracts
gate the build via `on: push`; no committed credential.

> A blanket regex initially flagged a credential. Verified false: the only matches were the
> detector's own pattern literal and a deliberately-fake `sk-ant-planted12345` inside its
> positive-control test, which writes to a temp directory.

## Requirements Traceability — 3/7 objectives PASSED, 12/18 SC verified

| ID | Type | Status | Notes |
|----|------|--------|-------|
| OBJ1 | Work Item | PARTIAL (5/6) | VC6's "when the application builds" never executes — `next build` runs nowhere; root pinning asserted by regex over config text |
| OBJ2 | Work Item | PASSED | All five TR-007 mechanisms have real negative fixtures with positive controls |
| OBJ3 | Work Item | PASSED | VC2 additionally evidenced live by CI run #7 |
| OBJ4 | Work Item | PARTIAL (2/5) | VC1 has no check; **VC2 FAILED**; VC4 half-evidenced |
| OBJ5 | Work Item | PARTIAL (2/4) | VC2 and VC3 have no executable check, no CI step, no recorded run |
| OBJ6 | Work Item | PASSED | VC1–VC5 met |
| OBJ7 | Work Item | PARTIAL | Substance met via push runs; **no `workflow_dispatch` run has ever occurred** |
| SC-001…SC-006, SC-008 | Success Criteria | PASSED | Lock isolation, layout, coverage, fixtures, laundering, indirect reach, in-image imports |
| SC-007 | Success Criteria | **FAILED** | Equality not asserted and currently false — see E1 |
| SC-009 | Success Criteria | **FAILED** | Job completion / no leftover container verified by hand only |
| SC-010 | Success Criteria | **FAILED** | Vector extension asserted nowhere; healthcheck is `pg_isready` only |
| SC-011…SC-015, SC-017 | Success Criteria | PASSED | SC-015 artifact-only (no executable check) |
| SC-016 | Success Criteria | **FAILED** | `package.json` — named by the criterion — inspected by nothing |
| SC-018 | Success Criteria | **FAILED** | Built image and its layers never inspected |

## Traceability Gaps

- `tasks.md` names **six files that do not exist**: `test_build_context.py`, `helpers/image_allowlist.py`,
  `test_image_allowlist.py`, `helpers/image_denylist.py`, `test_index_config.py`, `test_no_credentials.py`.
  Five were consolidated into `image_contents.py` / `test_image_contents.py` / `test_supply_chain.py`;
  **`test_build_context.py` has no successor**, so SC-015 has no regression guard.
- **T026 and T046 were marked complete without their deliverable existing.** T046 claims a dispatched
  run; all ten runs in the repository are `push` events. This is a marking error on my part, not a
  tooling failure.
- OBJ6 VC4's "without adding a dependency" is true by inspection but asserted by no test.
- `src/api/tests/` and `src/gateway/tests/` do not exist while both `pyproject.toml` files set
  `testpaths = ["tests"]`; collectors exit 0 with `PytestConfigWarning`.

## Checklist Fulfillment — 8/8 spot-checked, 0 gaps

Security: no committed credential (PASSED), build context deny-all + two allows (PASSED), three
external images digest-pinned (PASSED), no alternate index configured (PASSED).
Testing: three negative fixtures (PASSED), positive controls present (PASSED), vacuous-pass guards
present on image checks (PASSED), fixtures outside production roots (PASSED).

## Performance — SKIPPED
No performance NFRs in `spec.md` (0 keyword signals).

## Accessibility — SKIPPED
No accessibility NFRs in `spec.md` (0 keyword signals).

## Browser Runtime Validation — SKIPPED
Not required. The web boundary ships no behaviour this epic; its three tests assert filesystem and
configuration properties. No probe performed.

## Manual Testing — Not Required

## Tool Recommendations

- `pip-audit`: unrunnable under local TLS interception. Fix — build a combined PEM (certifi + the
  interception root exported from the Windows store) and set `SSL_CERT_FILE` / `REQUESTS_CA_BUNDLE`.

## Bug Context

| Bug Task | Error Output | Related |
|----------|-------------|---------|
| T054 | `next: 16.2.12` vs declared `Next.js 15` | `src/web/package.json`, `project-instructions.md` |
| T055 | `expected - installed == {'colorama'}`; `click -> colorama marker=sys_platform == 'win32'` | `helpers/image_contents.py:47` |
| T056 | `test_no_credential_material_in_the_serving_build_context` walks source only | `test_supply_chain.py` |
| T057 | `.npmrc` absent → test returns early; `package.json` never read | `test_supply_chain.py:34` |
| T058 | No `CREATE EXTENSION vector` and no assertion anywhere | `docker-compose.yml` |
| T059 | No check invokes `docker compose --profile jobs run` | — |
| T060 | `tests/checks/test_build_context.py` absent, no successor | `tasks.md` T026 |

## Bug Tasks Generated

T054 (CRITICAL) · T055–T060 (ERROR) · T061–T065 (WARNING). See `tasks.md § Phase: Bug Fixes`.
