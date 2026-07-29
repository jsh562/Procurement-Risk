# Analysis Report — E006 Document Ingestion and Extraction

**Date**: 2026-07-27 · **Artifacts**: `spec.md` (74 FR, 58 SC), `plan.md` (14 AD), `tasks.md` (91 tasks), `data-model.md`, three ADRs
**Governing document at the time of this report**: `project-instructions.md` v1.2.4 — matched the plan's recorded audit version, so no re-run was owed *then*. **Superseded.** v1.2.5 landed on `main` on 2026-07-28 and merged into this branch at `fead821`; the gate was re-run against it the same day and the result is recorded in the section at the foot of this file and in `plan.md` §Instructions Check. The findings table below is left exactly as it was written on 2026-07-27
**Verdict**: **1 CRITICAL, 9 HIGH, 12 MEDIUM.** No zero-coverage requirement, no missing artifact. Every defect is a stated-versus-actual mismatch rather than a design fault.

---

## Findings

| ID | Category | Sev | Location | Summary | Recommendation |
|---|---|---|---|---|---|
| A-01 | Instructions | **CRITICAL** | FR-051, ADR-0020 §Status | ADR-0020 was allocated during Checklist by scanning for the highest number in use — the exact mechanism Governance's claim-at-epic-start clause exists to rule out | Keep the disclosure; confirm before merge that no concurrent wave epic allocated 0020. Not reversible |
| A-02 | Coverage | HIGH | `tasks.md` T058 | Writes the floor and weights to `ingestion_run` but its whole ancestry is `T058→T057→T056→∅` — no path to the FR-047 gate. Same class as the US1 defect already fixed | Add `after:T009` to T058 |
| A-03 | Coverage | HIGH | `tasks.md`, `plan.md` §Testing | `llm/` has three source modules and **zero test tasks**, while T005 adds an 80% per-package floor and T090 verifies it. The gate cannot be met | Add a test task for `llm/extraction.py` before T055 |
| A-04 | Consistency | HIGH | FR-048, `spec.md` NEW-CONFIG, T006 | All three claim the computation contract "names its modules one by one". False — an import-linter `forbidden` contract's `source_modules` covers descendants. AD-001 has it right | Restate FR-048's reason, fix NEW-CONFIG, convert T006 to a verification task |
| A-05 | Spec quality | HIGH | FR-002 ↔ SC-036 | SC-036 asserts identifiers are "the lower-cased file stem"; FR-002 fixes a three-step transform. `PRJ-001_T0002_R0` lower-cased fails the format SC-036 asserts in the same sentence — unsatisfiable | Restate SC-036 against FR-002's transform |
| A-06 | Spec quality | HIGH | FR-027 ↔ FR-062 | FR-027 forbids storing a canonical form "alongside"; FR-062 requires exactly that for numeric and date kinds. The text-kind scoping is never stated in either | State the kind scoping in both |
| A-07 | Spec quality | HIGH | FR-047 ↔ SC-034 | FR-047 blocks *implementation*; SC-034 measures "before the first extracted value is written". Two gates for one condition; the SC cannot fail on the FR's trigger | Align SC-034 to the FR's trigger |
| A-08 | Spec quality | HIGH | SC-022 vs FR-038, FR-057, FR-070 | SC-022 asserts "zero fields are absent" over FR-038's six; the record carries sixteen columns. Verifies 37% of what it claims | Range SC-022 over the full record |
| A-09 | Spec quality | HIGH | FR-024 → FR-034 | A refusal on a retired or non-vocabulary term maps to none of the seven closed outcomes. Either an outcome is unassigned or the refusal writes no record | Map it to `schema_violation` explicitly |
| A-10 | Consistency | HIGH | `tasks.md` × `plan.md` §Source Code | Seven path groups drift. `src/model/README.md` carries three operator runbooks incl. T084 `[COMPLETES FR-055]` and is absent from the plan; three tasks use root-relative `tests/` incl. T068 `[COMPLETES FR-029]`; seven schema tests and one checks test unlisted; `src/model/fixtures/` unlisted | Add the paths to the plan; normalise the three prefixes |
| A-11 | Coverage | MEDIUM | `tasks.md` T009 | Authors revision `0300` with no edge to T003, which claims the block. Per AD-013 a `03xx` revision before the claim turns CI red three ways | Add `after:T003` |
| A-12 | Consistency | MEDIUM | `tasks.md` §Dependencies | Asserts a `T031→T029` cross-phase edge that exists in neither `after:` nor `←` form — it was deliberately dropped when T031 was redirected to T013 | Remove from the edge list |
| A-13 | Consistency | MEDIUM | `spec.md` Clarifications Q3, Q5; STF-003 | Three stale records contradict current requirements: corpus-wide digest (FR-043 rejects it), run-level generation state (FR-055 is per document), a retention bound and purge (FR-055 says none is needed), and a fourth confidence signal FR-031 withdraws | Correct in place with the amendment attributed |
| A-14 | Instructions | MEDIUM | `plan.md` §Instructions Check Governance row, FR-051 coverage row | Both still read "ADR-0018/0019 claimed at epic start" | Update to 0018–0020 with the Checklist-phase claim named |
| A-15 | Instructions | MEDIUM | `plan.md` Technology Stack row vs AD-014 | Cites the stack's **INT8** clause as evidence for a decision that explicitly chose **FP32** | Re-evidence against ADR-0012's full-precision budget |
| A-16 | Consistency | MEDIUM | `plan.md` §Testing Architecture tier vs T008 | Baseline independence listed under `import-linter`; T008 implements it as pytest, so `lint-imports` never carries it | Declare it as an import-linter contract and align T008 |
| A-17 | Spec quality | MEDIUM | FR-066 ↔ SC-024 | FR-066 enumerates three permitted updates "and nothing else"; SC-024 lists four | Add the fourth to FR-066's enumeration |
| A-18 | Spec quality | MEDIUM | Scope §Included, Compliance Check vs FR-021 | Two places hard-code "384-dimension" while FR-021 requires taking it from the schema at run time | Restate as "the dimension the schema publishes" |
| A-19 | Convention | MEDIUM | `spec.md` §Success Criteria | SC-049/050 appear before SC-044; SC-058 before SC-057 | Reorder; IDs unchanged |
| A-20 | Consistency | MEDIUM | `data-model.md:87` | Quotes FR-055 in its superseded run-level wording | Update the quote; the argument still holds |
| A-21 | Convention | MEDIUM | `spec.md` size | ~153 KB against the spec-authoring skill's 100 KB budget | Tighten prose; keep every ID and normative clause |
| A-22 | Coverage | MEDIUM | `tasks.md` T034 | The report subtree reads populations out of six tables with no declared edge to any writer | Add `after:T032` |

**Overflow (LOW, not itemised):** delegation ambiguity in FR-058 ("the declared subset", "roughly ten"), FR-011 ("every other claim"), FR-019 ("recorded digests"), FR-035 ("a diagnostic detail"); nine of ten declared task exports have no consumer; eleven duplication clusters where one obligation is stated under several IDs.

---

## Quality summaries

**Spec quality** — structurally sound; no `[NEEDS CLARIFICATION]`, no placeholders, no "as appropriate"-class wording. Requirement IDs contiguous FR-001–FR-074, criteria SC-001–SC-058. The dominant structural defect is **singularity**: FR-050, FR-060 and FR-019 each carry roughly eight independently-satisfiable obligations under one ID, which makes partial satisfaction unrepresentable. Secondary defect is **rationale inlined into obligations** — roughly half the Requirements section is reasoning rather than requirement, and much of it restates `data-model.md`, which is normative over this spec.

**Compliance** — FAIL on Governance only; all eight principles PASS on substance. Verified against the files rather than the plan's claims: the import contract matches, TR-081 is still unamended so FR-047 still blocks, ADR-0019's supersession matches the ADR-0013/0016 precedent in every particular, and AD-013's three-way-breakage claim is accurate against the actual assertions.

**Coverage** — 74/74 requirements tagged. No gold-plating. All ten completion markers correct. All four export/import edges resolve. All three red-green pairs correctly ordered and none marked parallel.

---

## Coverage summary

| Dimension | Result |
|---|---|
| Requirements with ≥1 task | **74 / 74 (100%)** |
| Tasks with no requirement tag outside exempt phases | 0 |
| Requirements ≥3 tasks with a completion marker | 10 / 10 |
| `← T###:Symbol` edges resolving to a matching export | 4 / 4 |
| Row-writing phases with a path to the FR-047 gate | **8 / 9** — T058 is the gap |
| Task file paths present in the plan's Source Code block | **drift in 7 groups** |

---

## Metrics

- Total requirements: **74** · Total criteria: **58** · Total tasks: **91**
- Requirement coverage: **100%**
- Findings: **22** itemised (1 CRITICAL, 9 HIGH, 12 MEDIUM) plus a LOW overflow
- Blocking for Implement: **A-01 is a disclosed deviation, not a blocker.** A-02, A-03 and A-10 would break a build or a schedule and should be fixed first

---

## Compliance re-run — `project-instructions.md` v1.2.5

**Date**: 2026-07-28 · **Trigger**: Governance — "a feature whose recorded compliance audit names a superseded version MUST re-run its compliance gate before passing its next phase gate". The audit above named v1.2.4; v1.2.5 was amended 2026-07-28 and merged into this branch at `fead821`. The next gate is QC.

**Method**: v1.2.5 re-read in full and every clause re-checked against the files on this branch, rather than the v1.2.4→v1.2.5 diff applied to the earlier verdict. The amendment is one clause — Development Workflow gains **Temporary Files** — but a delta-only re-run would have found neither A-24 nor A-25, which are older than the amendment and were simply never checked.

**Verdict**: **3 further violations, all repaired on this branch.** No principle changed verdict; the three sit in Development Workflow, Source Code Layout and CI Requirements.

| ID | Category | Sev | Location | Summary | Disposition |
|---|---|---|---|---|---|
| A-23 | Instructions | **CRITICAL** | root `pyproject.toml` `[tool.pytest.ini_options]`; `.github/workflows/verify.yml` `verify` job | v1.2.5's Temporary Files rule unenforced at the root tier, at two layers. The root pinned no `--basetemp` while all three entries did, and it hosts `tests/checks/test_gateway_no_provider_env.py` — the only pytest code in the repository that builds a **virtual environment**. The `verify` job set no `TMPDIR`/`TEMP`/`TMP` while the `reproduce` job set all three. Live rather than theoretical: a `no-provider-env0` directory dated 2026-07-28 sits in `%LOCALAPPDATA%\Temp\pytest-of-<user>/pytest-15` | **Fixed.** `addopts = "--basetemp=.tmp/pytest"` at the root; the three variables set on the `verify` job with a `mkdir -p "$TMPDIR"` step ahead of the first tool. *(The pin is now `.tmp/pytest-checks`, and the three entries carry `-model`, `-api`, `-gateway`: all four originally named one directory, and pytest clears its basetemp at start of run, so two tiers running concurrently wiped each other's `tmp_path`. Corrected 2026-07-28 after QC iteration 2; `test_scratch_stays_in_the_checkout.py` now asserts distinctness as well as containment.)* |
| A-24 | Instructions | **CRITICAL** | `/tools/{build_probes,build_reference,fetch_encoder}.py` | Source outside `/src` under `ENFORCE_SRC_ROOT`, whose single exception is `/tests` for cross-entry verification. These are neither tests nor cross-entry — `build_probes.py` imports `model.ingest.documents`, `manifest_reader` and `parse` | **Fixed.** Moved to `src/model/tools/`, beside `src/model/src/` so it stays unpackaged, uncollected and un-imported. Two of the three also held one checkout's absolute path, which on a disk holding several checkouts rewrote the wrong tree's artifact; both now derive the root from `__file__` |
| A-25 | Instructions | HIGH | `src/gateway/tests/test_migrations.py` | CI Requirements — "all tests passing". The **Unit tests (gateway)** step was red on this branch: the file asserted the Alembic head equals `0103`, which stopped being true when this epic chained `0300`–`0304` onto it. The assertion enforces E004's TR-018 block claim and could not simply be deleted | **Fixed.** Restated against what TR-018 says — E004's four revisions present in the applied chain, contiguous, and exactly what that chain carries inside `0100`–`0199` — with **three negative controls and one positive control**: the three plant a renumbered, a dropped and a foreign revision and require each to be reported; the positive control runs the same assertion over an *undamaged* copy, without which a copy helper that produced an unreadable directory would make all three "fail correctly" for the wrong reason and the negative controls would be evidence about the copier |

**Wording gap, recorded as an amendment request and not performed** (Governance serializes amendments onto the default branch): v1.2.5's Temporary Files clause enumerates "`--basetemp` pinned there in **each entry's** pytest configuration". The root cross-entry harness is not one of the four entries, which is how it became the one Python tier without the pin while hosting the one venv-building test. The clause's general obligation — *every command* directs scratch into `.tmp/` — already covers it, so this is under-specification rather than a licence.

**Re-confirmed unchanged**: all eight principles; Technology Stack; Data Provenance; Governance, where ADR-0020's Checklist-phase allocation (A-01) stands as a disclosed, irreversible deviation.
