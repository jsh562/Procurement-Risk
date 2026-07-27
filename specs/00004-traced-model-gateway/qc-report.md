# QC Report — E004 Traced Model Gateway

**Run 2 · 2026-07-27 · verdict: PASS**
Governing document: `project-instructions.md` v1.2.4.
Measured on the merged tree — main `79f4f68` merged into the branch first, so
this describes what a merge would produce rather than what the branch produced
in isolation.

---

## Required categories (profile `standard`)

### linting — PASS

Static analysis and code quality, run for real, no cache:

| Check | Sites | Result |
|---|---|---|
| `ruff check` | gateway, api, model, **repository root** | clean at all four |
| `ruff format --check` | gateway, api, model, root | clean at all four |
| `mypy --strict` | gateway (`src`) | no issues, 18 source files |
| `lint-imports` | gateway 4, api 1, model 2 | 7 contracts kept, 0 broken |
| `uv lock --check` | gateway, api, model | all consistent |

**The root `ruff check .` is listed separately on purpose.** `verify.yml`'s
"Lint (Python)" step runs four commands, not three, and the fourth covers
`/tests`. Running only the per-entry three left `/tests` unlinted through most
of this epic and was the failure on PR #6. It is named here so a later reader
does not repeat the omission.

### coverage — PASS

| Gate | Threshold | Measured | Exit |
|---|---|---|---|
| Combined | 80 | **94%** (5458 statements, 280 missed) | 0 |
| `*/model/corpus/*` | 80 | **93%** | 0 |
| Gateway entry (AD-007) | 85 | **95%** (939 statements, 33 missed) | — |

Combined from three data files, as CI does: `.coverage.model`,
`.coverage.gateway`, `.coverage.checks`.

---

## Tests

| Suite | Result |
|---|---|
| Gateway | 401 passed, 5 skipped |
| Root cross-entry | 211 passed |
| Model | 1141 passed |

**The 5 skips are the provider-reaching smoke check** (`test_provider_smoke.py`),
skipped because `GATEWAY_ALLOW_PROVIDER_CALLS` is unset. That is OBJ6 VC1
holding rather than a gap: the suite passes with no credential and no network,
which is the property the opt-in exists to produce. The same file *fails* rather
than skips when the gate is set with no credential (VC3), so the skip cannot
hide an opted-in run that did nothing.

---

## Requirement coverage

**81 of 81 technical requirements are cited** in code or tests — checked by
extracting every `TR-NNN` the spec declares and every one appearing under
`src/gateway`, `src/model/.../versions`, and `tests/checks`, then differencing
the two sets. Zero declared-but-uncited.

Objectives: 6. Validation criteria: 43. Success criteria: 27.

Citation is not the same as verification, and this report does not claim it is.
It establishes that no requirement was silently dropped; the per-criterion
evidence is in the test files named against each requirement.

---

## Not measured — recorded rather than implied

**OBJ6 VC2 — one live invocation end to end.** Needs a real credential and
spends money. `test_provider_smoke.py` covers the reachable half (the gate, the
credential handle, the guard exemption) and skips the call itself. This is the
one criterion in the epic that no automated run can discharge, by design.

**The `record` arm is tested through an injected client, not a real one.** The
transport budget, the fixture write, and the provenance sidecar are exercised;
the SDK's own wire behaviour is not.

---

## Closed since run 1

**Schema-validated output now runs through `invoke()`.** Run 1 disclosed this as
not-measured: `validate_or_repair` was implemented and unit-tested, but
`InvocationRequest` carried no schema field and `_invoke` hardcoded zero repairs,
so **TR-006 held in units and was violated by the public entry point on every
call** — an unvalidated value reached the caller each time, and `repaired` and
validation-`failed` were unreachable on real rows.

Disclosure is not satisfaction, so it was closed rather than carried:

- `InvocationRequest` gains an optional `output_schema`. Optional because a
  caller wanting raw text is legitimate; when it is absent the gateway returns
  the raw content and claims nothing about it.
- `_invoke` calls `validate_or_repair` and uses the real repair count.
- The failure path writes the row with `outcome='failed'` and
  `error_type='validation_failed'` **before** raising (TR-008).
- **A repair in `replay` resolves a second fixture**, keyed on the original
  request plus the instruction that provoked it. The alternative — replay
  cannot repair — was rejected because it would leave `repaired` unreachable in
  the only mode continuous integration runs.

Ten new tests in `test_invoke.py` cover valid, repaired, twice-failed, and
no-schema, on both arms. `repaired` and `failed` are now reachable states.

## Corrections made during this epic, recorded because they were caught by checks

**TR-070's pin was wrong.** It named semconv `1.36.0` "as the version carrying
`gen_ai.provider.name`". That release defines `gen_ai.system`. Corrected to
`1.37.0` across all five recording sites. Found by T026's mandated verification,
not by review.

**Three of E003's checks encoded "E003 is the sole author of this chain"**,
which {SAD:ADR-0013} had made false. Rescoped to authorship on the user's
decision; each kept its claim and had its proxy corrected. E003 remains green.

**Five self-referential test bugs.** A check scanning source text matches the
prose explaining what it looks for. All five are now AST walks. The general
rule: a check whose subject is code must read the parse tree.

**E001's supply-chain scan and single-naming-site scan each caught a defect in
this epic's own work** — a key-shaped test literal, and a comment naming the
provider distribution while explaining the rule against naming it.

---

## Environment note

`tests/checks/test_orchestration.py` ran `docker compose down -v` **unscoped**
until main's `e01acb2`, so it defaulted to the Compose project named after the
working directory and destroyed the development database on every
`pytest tests/checks` run. That is fixed upstream. It is recorded here because
it produced hours of apparent external interference during this epic, and the
symptom — only this checkout's container vanishing — is easy to misattribute.

---

## Verdict

**PASS.** Both required categories green, all three suites green, all 81
requirements cited, both coverage gates exit 0.
