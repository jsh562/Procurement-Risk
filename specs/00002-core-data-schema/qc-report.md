# QC Report: Core Data Schema

**Feature**: `00002-core-data-schema` (E003) | **Date**: 2026-07-26 | **Run**: 2 (re-audit of run 1, verdict FAIL)
**Governing document**: `project-instructions.md` **v1.2.0** — superseded since this run; **checked, no re-run required.** See below.
**QC policy**: profile `standard` | required categories **linting, coverage** | coverage threshold **80**
**Branch**: `00002-core-data-schema` | database: `kayademoprocurementrisk1-db-1`, host port 5434, chain at head `0010`

> **Version drift, checked and closed — recorded so the next reader does not have to re-derive it.** This run audited **v1.2.0**. Two amendments have landed since: **v1.2.1** and **v1.2.2**. Governance requires that *"a feature whose recorded compliance audit names a superseded version MUST re-run its compliance gate **before passing its next phase gate**,"* with the stated rationale that *"an amendment moves the ground under every epic **already in flight**."* E003 passed its last gate and is merged: it has no next gate and is not in flight, so the clause does not fire.
>
> That is procedure, and procedure alone would be a thin reason. The substance is independent of it: **v1.2.1** only *narrowed* "No second datastore" to "No second datastore of record", and a narrowed prohibition cannot create a new violation — it can only permit more. **v1.2.2** extends epic-start number claiming to Feature Workspace numbers; it is the amendment E003 itself requested as AR-2, it constrains how a *future* workspace number is allocated, and it makes no demand on delivered schema. Neither can change a verdict in this report.
>
> Disclosed rather than glossed: E003 **is** one of the two workspaces whose `00002` prefix collision motivated v1.2.2, and that collision is not retroactively fixable — a workspace number is embedded in every path this epic's artifacts cite. v1.2.2 says so explicitly. E003 is therefore permanently non-compliant with a rule it asked for, by construction, and the amendment was written knowing that. The other two epics in flight at the time (E002, E004) both recorded v1.2.1 and do carry the re-run obligation for their own gates; this note speaks only for E003.

## Overall Verdict: PASS

Run 1's blocking defect is cleared, neither required category fails, and the three WARNING findings this run raised (T064, T065, T066) were fixed and re-measured inside it. All 66 tasks are `[X]`.

An earlier revision of this report held the verdict at "PASS on both required categories, marker withheld", because T066 was a criterion this report had itself claimed as passing and it would have been wrong to certify the feature on an audit whose own error was still open. That has since closed, so the qualification is retired rather than left standing as if unresolved.

**Final measurement, taken after every fix, reproducing `.github/workflows/verify.yml` end to end:**

| Suite | Result |
|---|---|
| Model entry (`src/model`) | **455 passed, 0 failed, 0 skipped** |
| Gateway (`src/gateway`) | **5 passed** |
| Root cross-entry (`tests/checks`) | **75 passed** |
| `coverage combine` | **Combined 3 files** |
| `coverage report --fail-under=80` | **94%** — 535 statements, 28 missed, 76 branches, 9 partial — **exit 0** |
| Lint / format / import contracts / locks | `ruff check` and `ruff format --check` clean at 4 roots; **3 contracts kept, 0 broken**; 3 locks consistent |

The 455 is up from run 1's 425: +4 from T057 and T058, +26 from T066. The denominator grew from 526 statements to 535 because T065 added `src/gateway` to it, and the percentage held at 94%.

> **455 is this run's figure and is left as measured.** Post-QC remediation of analysis findings A-007/A-008/A-011/A-012 (tasks T067-T075, see `tasks.md` § Phase: Post-QC Remediation) subsequently added 8 more tests, taking the model suite to **463 passed, 0 skipped** with the gate still at 94% and exit 0. That work is deliberately **not** folded into the numbers above: those were measured by this QC run, and rewriting them to a later total would claim the run verified something it never saw. The later figure is recorded here so a reader who runs the suite today and counts 463 knows why it differs.

Both required categories are green, measured end to end from `.github/workflows/verify.yml`:

| Required category | Measurement | Result |
|---|---|---|
| **linting** (incl. security per AD-003) | `ruff check` and `ruff format --check` at 4 roots, `lint-imports` at 3 entries, `uv lock --check` at 3 entries | **0 issues, 3 contracts kept, 0 broken** |
| **coverage** | `coverage combine` then `coverage report --fail-under=80` | **94%** - 535 statements, 28 missed, 76 branches, 9 partial - **exit 0** |

Run 1's blocking defect is cleared. All nine bug tasks T055-T063 were re-verified independently of their task text; each holds. Three new WARNING findings are raised as **T064-T066**; none is gate-blocking and none is a regression. T066 was found by story verification after this audit had already recorded the criterion as passing — see the correction under Requirements Traceability.

Run 1 arithmetic corrected: it reported "24 of 28 PASSED" alongside three PARTIAL criteria, which totals 27. The correct run-1 figure was 25 of 28.

## Test Results

| Suite | Command | Result |
|---|---|---|
| Model entry | `COVERAGE_FILE=$REPO/.coverage.model DATABASE_URL=...@localhost:5434/... REQUIRE_DB=1 ./.venv/Scripts/python.exe -m coverage run --source=src/model/roster,src/model/schema -m pytest tests -q` (in `src/model`) | **455 passed, 0 failed, 0 skipped** - 429 mid-run, 455 after T066 |
| Root cross-entry | `COVERAGE_FILE=$REPO/.coverage.checks ./.venv/Scripts/python.exe -m coverage run -m pytest tests/checks --ignore=tests/checks/test_orchestration.py -q` | **75 passed, 0 failed** |
| Gateway | `COVERAGE_FILE=$REPO/.coverage.gateway ./.venv/Scripts/python.exe -m coverage run -m pytest tests -q` (in `src/gateway`) | 5 passed - now under coverage per T065 |
| API | - | no `tests/` directory; verify.yml declares no api test step |

455, against run 1's 425. T057 added two migration-chain tests and T058 two forecast tests, taking it to 429; T066 then added 26 unconditional-provenance cases. Each group was also run in isolation and passes.

**Not run - 8 tests**: `tests/checks/test_orchestration.py`. Its module-scoped fixture teardown runs `docker compose down -v`, which destroys the `db-data` volume and the migration chain, and it binds host port 5434 that a sibling checkout also uses. `--collect-only` confirms 83 tests in `tests/checks`, of which 75 ran. CI runs all 83 (`coverage run -m pytest tests/checks -q`, no ignore). The one criterion these tests carry - OBJ1 VC6 - is separately evidenced below without running them.

**Web suite** (`npm run lint`, `prettier --check`, `tsc --noEmit`, `npm test`, `npm run build`): not re-run. `git status` shows no file under `src/web` touched by this epic, so no outcome there can have changed; E001's QC measured them green.

## Static Analysis (includes security per AD-003): PASSED

| Check | Result |
|---|---|
| `ruff check .` (root) | 0 issues, exit 0 |
| `ruff format --check .` (root) | 111 files already formatted, exit 0 |
| `ruff check .` (`src/model`) | 0 issues, exit 0 |
| `ruff format --check .` (`src/model`) | 36 files already formatted, exit 0 |
| `ruff check .` / `format --check .` (`src/gateway`) | 0 issues, 4 files formatted |
| `ruff check .` / `format --check .` (`src/api`) | 0 issues, 4 files formatted |
| `lint-imports` (`src/model`) | "Model-facing code does not reach the computation package" **KEPT** - 1 kept, 0 broken |
| `lint-imports` (`src/gateway`) | "Only the provider wrapper imports the model-provider client" **KEPT** - 1 kept, 0 broken |
| `lint-imports` (`src/api`) | "Model-facing code does not reach the computation package" **KEPT** - 1 kept, 0 broken |
| `uv lock --check` (gateway / api / model) | exit 0 three times - 32 / 39 / 65 packages resolved |

Ruff's `S` ruleset was re-verified **live**, not merely read off the config. A probe written into `src/model/src/model/schema/` returned `S105` (hardcoded password) and `S608` (SQL built by concatenation); the same probe pattern under `tests/schema/` returned "All checks passed", confirming the `S101` per-file ignore. Both probe files were removed; `git status` is back to the pre-audit set.

**Two corrections to run 1's numbers.** Run 1 reported 70 formatted files at root and 35 at `src/model`; the measured figures are 111 and 36. Ruff 0.16.0's formatter accepts Markdown (verified directly: `ruff format --check ./AGENTS.md` returns "1 file already formatted"), so the root count is 71 Python files plus 40 Markdown. Worth recording because it has a consequence: **root `ruff format --check .` is not a CI step at all** - verify.yml's "Format check (Python)" iterates `gateway api model` only, and root gets `ruff check .` alone. Had the root format check been in CI, verify.yml's `paths-ignore: specs/**` comment ("Specification artifacts cannot change any check's outcome") would be false, since a malformed Python block inside a spec would fail it. As configured, the comment holds.

`lint-imports` was invoked as `src/model/.venv/Scripts/lint-imports.exe` with `PYTHONUTF8=1`. `python -m importlinter.cli` prints nothing on this platform.

## Security Audit: PASSED (within the lint category)

Per AD-003 the project mandates exactly two QC categories and folds security into lint rather than opening a third. No separate scanner was run and none is required. Evidence is the live `S` positive control above.

## Code Coverage: **94% against threshold 80 - PASSED**

Three data files, not two: `src/gateway` joined the denominator under T065, which is why the statement total moved from 526 to 535 while the percentage held.

Reproduced step-for-step from `.github/workflows/verify.yml` lines 224-279:

```
coverage combine          -> Combined 3 files, exit 0
coverage report --fail-under=80
TOTAL   535   28   76   9   94%
exit 0
```

| Lowest-covered files | Cover | Missing |
|---|---|---|
| `src/model/src/model/schema/env.py` | 76% | 42->51, 76-85, 100-101, 124 |
| `src/model/src/model/schema/url.py` | 82% | 73, 84-85 |
| `src/model/src/model/schema/cli.py` | 83% | 82, 137-138, 140-141 |
| `tests/checks/helpers/source_scan.py` | 89% | 49, 68-69, 79-83 |
| `src/model/src/model/roster/reader.py` | 91% | 76, 87, 99, 125-126 |

All ten `versions/*.py` migration modules: **100%**. `helpers.py`, `entries.py`, `root_checks.py`, `versions/__init__.py`: 100%.

`cli.py` rose from run 1's 58% to 83% because T057's helper drives the `migrate` console entry point's `main()` rather than `alembic.command.upgrade` directly.

## Bug-Fix Verification (T055-T063)

Each verified against the artifact, not the task text.

**T055 - CLEARED.** `.github/workflows/verify.yml:244` now reads `--source=src/model/roster,src/model/schema`. Root `pyproject.toml:38` reads `source = ["tests/checks/helpers", "src/model/src/model/roster", "src/model/src/model/schema"]`, and `[tool.coverage.paths]` gained the matching `schema` remap at line 46. The two agree on both model packages. `tests/checks/helpers` is correctly absent from the workflow's `--source`: the root-checks step overrides nothing, so it measures all three, and `combine` unions the hits - which is why the report above shows one row per file rather than duplicates under two roots.

**T056 - CLEARED, and the mechanism was tested rather than assumed.** `src/model/src/model/schema/versions/__init__.py` exists (2,950 bytes, docstring only, no symbols). With the chain deliberately not executed:

```
env -u DATABASE_URL -u REQUIRE_DB -u CI COVERAGE_FILE=$REPO/.coverage.t056 coverage run --source=src/model/roster,src/model/schema -m pytest tests/schema/test_helpers.py -q
   -> 18 skipped
coverage report
   -> all ten versions/*.py present at 0%, TOTAL 4%
```

The ten revision modules are now **in the denominator at 0%** instead of absent. The aggregate drops to 4%, so a chain that stops executing now fails the gate rather than raising the percentage. Alembic is unaffected: `alembic heads` gives `0010 (head)` (exactly one), `alembic history` gives ten revisions from `<base> -> 0001` through `0009 -> 0010`, `alembic current` gives `0010 (head)`.

**T057 - CLEARED, and the tests assert the property rather than a weaker one.** `test_migration_chain.py` gained two tests. They do not patch or mock the failure: a real `CREATE TABLE "ix_chunk__project"` is committed on a scratch database, so revision `0004` - which emits `CREATE TABLE chunk`, then `CREATE INDEX ix_chunk__document_page`, then `CREATE INDEX ix_chunk__project` (verified at `0004_chunk.py:265-266`) - dies with a genuine `psycopg.errors.DuplicateTable` **after** it has already emitted DDL.

- `test_a_run_that_fails_partway_leaves_none_of_its_own_work_behind` records through Alembic's `on_version_apply` hook that `0001`-`0003` **executed**, then asserts every relation they created is **absent**, `alembic_version` included. Measuring work-performed and work-persisted separately is what distinguishes a single-transaction run from a per-migration one; a weaker test could not.
- `test_a_failed_run_leaves_the_version_table_agreeing_with_the_objects_present` starts from a *committed* prefix (`migrate 0003`), fails on `0004`, then asserts `alembic_version == {"0003"}`, asserts the present-relation set equals exactly what `0003` implies, and - the assertion that makes it non-vacuous - asserts `ix_chunk__document_page`, created one statement before the failure, is gone. Recovery asserts on the **exact list** `0004` through `0010`, so a run that restarted from the beginning would fail; and the blocking name is asserted to go in as `relkind='r'` and come back as `relkind='i'`, proving the failed statement was re-issued rather than skipped.

Both run in 4.42s and pass. This is genuine VC7 evidence, not a restatement.

**T058 - CLEARED, and the attribution is correct.** `test_forecast.py` gained `test_a_run_with_no_as_of_anchor_date_is_rejected` and `test_one_array_offset_denotes_the_same_calendar_day_for_every_line_in_a_run`; both pass in isolation.

On the deliberate decision not to add `as_of_date` to `REPRODUCIBILITY_FIELDS`: **the attribution is right.** TR-001 through TR-087 were read directly. TR-026 enumerates exactly nine columns - run identifier, code revision, input data hash, sampling seeds, library versions, artifact hash, schema version, model version, creation time - and SC-012 says "all nine reproducibility fields". `as_of_date` is TR-049's separate obligation and SC-022's subject. Folding it into the parametrized nine would have made `test_each_reproducibility_field_is_rejected_when_null` a ten-case test under a docstring claiming nine, and a regression would have named TR-026 instead of TR-049. The code records this reasoning at `test_forecast.py:151-158`.

The array-offset test is stronger than the criterion's wording requires. It resolves the offset through `v_active_forecast_run` (the only supported read path, TR-027), compares against `AS_OF_DATE + 2` computed in Python rather than only line-against-line - two lines agreeing on the wrong day is still agreement - uses offset 2 rather than 1 so an off-by-one base cannot pass, asserts both survival arrays are the same length, and then asserts `line_posterior` carries **no date or timestamp column at all**, which is what makes the run-level anchor the only anchor and would catch a later per-line date that the arithmetic assertion alone would keep passing over.

**T059-T062 - CLEARED. Each of run 1's four contradictions was resolved, not reworded:**

| Run-1 contradiction | Resolution, verified |
|---|---|
| `data-model.md` TR-037 row described a Compose job | `data-model.md:708` now reads "`docker-compose.yml` unchanged entirely - no service added. Migrations run as the `migrate` console entry point on the modeling entry (ADR-0011); there is no migration job, no image, and no build context" |
| `spec.md` Scope > Excluded said a profiled job was in scope | `spec.md:53` now reads "`docker-compose.yml` is not modified at all: ADR-0011 requires a modeling-owned job to run as a console entry point, so no migration service is added (TR-007, TR-037, SC-003)". `git diff HEAD -- docker-compose.yml` is empty and the file has not been touched since E001's `6ee3562`, so the claim is true, not merely consistent |
| `tasks.md` inverted the `0009`/`0010` edge list | `tasks.md:165` now reads `0009` (T048, provenance privileges) then `0010` (T045, T046, resolved entity), which matches `alembic history` exactly |
| `tasks.md` said "86 requirements TR-001 ... TR-086" | `tasks.md:179` now says 87 / TR-087. Independently counted: `spec.md` defines 87 requirements, TR-001 through TR-087 with **no gaps**; all 87 carry a `{TR-###}` tag in `tasks.md` and all 87 appear in `plan.md`; zero tagged-but-undefined |

T059's and T060's rewrites of SC-009 and SC-028 are treated below under PI Compliance, because amending a success criterion after measuring it touches Principle VII and should not be recorded silently.

**T063 - CLEARED, precedence verified empirically.** `tests/schema/conftest.py` adds `database_is_required()` and a `pytest_configure` hook raising `pytest.UsageError`. Measured, all with `DATABASE_URL` unset, against `tests/schema/test_helpers.py`:

| `REQUIRE_DB` | `CI` | Result |
|---|---|---|
| `1` | unset | refuses to start |
| unset | `true` | refuses to start |
| `0` | `true` | 18 skipped, exit 0 - explicit opt-out honoured |
| unset | unset | 18 skipped, exit 0 - permissive |
| `1` | `false` | refuses to start - **`REQUIRE_DB` wins over `CI`** |
| `yes` | unset | refuses to start |
| blank (two spaces) | unset | 18 skipped - blank is absent, not false |

Precedence is exactly as specified: `REQUIRE_DB` decides whenever it says anything including no, else `CI`, else permissive. Full suite with `REQUIRE_DB=1` and no `DATABASE_URL` gives **exit 2**. Run 1's false green is reproducible only in permissive mode (81 passed, 348 skipped, exit 0 - 344 plus the four new tests) and is now impossible under either demand. `verify.yml:238` sets `REQUIRE_DB: "1"` on the model test step, redundantly with the runner's `CI`. The hook is correctly scoped: `REQUIRE_DB=1 pytest tests/test_roster_reader.py` runs 16 tests normally, because `pytest_configure` replays only when collection reaches `tests/schema/`.

## Delivered Schema, Measured Against the Live Database

| Fact | Measured |
|---|---|
| Tables | 13 (plus `alembic_version`) |
| Views | 3 - `v_active_forecast_run`, `v_extracted_value_provenance`, `v_purchase_order_line_current_state` |
| `IMMUTABLE` helpers | **5** - `fn_is_sorted_ascending`, `fn_is_non_increasing`, `fn_all_within_unit_interval`, `fn_all_sha256_prefixed`, `fn_is_legal_lifecycle_transition` |
| Triggers (non-internal) | **0** - "the schema carries zero triggers by design" holds |
| Roles | `procurement` (SUPERUSER), `procurement_app` (not superuser) - exactly the split G-11 discloses |
| Server / extension | PostgreSQL 16.14, `pgvector` 0.8.5 |

Five helpers, not four: `fn_all_sha256_prefixed` was added by the v1.2.0 reconciliation. `data-model.md` documents all five (lines 436-440) so TR-083 holds; `plan.md`'s summary count does not - see T064.

## PI Compliance (v1.2.0)

| Principle | Verdict |
|---|---|
| I. Traceable or It Does Not Ship | PASS - citation page tied to source chunk by composite FK (`fk_extracted_value__chunk_page`), not a trigger; layer-conditional provenance makes a fabricated issuing body unrepresentable (`test_layer_conditional_provenance_is_enforced`) |
| II. Uncertainty Is the Product | PASS - full sorted draw array plus day-grid survival plus explicit residual, one row, no summary-only path; `test_the_delivered_residual_check_is_a_tolerance_and_would_fail_as_an_equality` |
| III. Precision Over Recall | PASS - failed extraction is a failure record, never a partial value row; a record cannot join two resolved entities (`test_an_extracted_value_already_a_member_cannot_join_a_second_entity`) |
| V. Model Extracts, Code Computes | PASS - no provider surface in this epic; provenance is a storage fact; all three import contracts KEPT |
| VII. Publish the Miss | PASS - **eleven** gaps disclosed, each with outcome-on-violation, reversal trigger, and production-scale alternative. G-11 is the strongest case: it states plainly that TR-084 is "latent, not active" and "must not be reported as fully enforced". See the note below on T059/T060 |
| Technology Stack | PASS - PostgreSQL 16.14 plus pgvector 0.8.5, single instance; migration runner is a console entry point, which v1.2.0's Infrastructure clause admits for a modeling-owned job |
| Source Code Layout | PASS - four entries (`api`, `gateway`, `model`, `web`), no fifth; schema assets and their tests inside `src/model`; the one new root file, `tests/checks/helpers/root_checks.py`, reads the check tree itself and qualifies for the cross-entry exception |
| **Testing & Quality Policy** | **PASS** - CI Requirements clause satisfied: lint clean, all executed tests passing, coverage 94% at or above 80, three architecture contracts KEPT. No deterministic computation module is introduced, so no test-first or property-test obligation is triggered; Hypothesis is used for the pure helpers regardless |
| Data Provenance | PASS - layer-dependent per v1.2.0; only migration-seeded reference data is written |
| Governance | PASS - amendment serialization: v1.2.0 was amended on `main` (`f2fc9de`); `git log main..HEAD` shows this branch carries only E003 artifacts and touches no registered document. Decision-record numbers monotonic (0011, 0012, 0013), claimed at epic start. Branch `00002-core-data-schema` matches `#####-feature-name` and resolves to the workspace. Compliance record names v1.2.0 audited 2026-07-26, so no re-run clause is triggered |

**Note on T059/T060, recorded rather than buried.** Both bug fixes amended a success criterion's text after it had been measured, which brushes against Principle VII's "targets MUST NOT be retroactively adjusted to match results". The judgement here is that both amendments narrow the claim while making the shortfall *more* visible, which is disclosure rather than evasion:

- SC-009 previously asserted its second half was proven by the G-5 test. The G-5 test asserts `contradictions == 1` - the opposite. Verified at `test_extraction.py:1266-1273`. The new text states the cross-row half is a disclosed gap and "must not be read as" a guarantee. The target did not move; a false attribution was removed.
- SC-028 previously read as an unconditional 100% claim. The new text says the criterion is satisfied against `procurement_app` and "**not** against the connecting role", that append-only is "latent rather than operative today", and "do not read this criterion as evidence that an in-place edit is currently impossible". Confirmed against the live database: `procurement` is SUPERUSER, `procurement_app` is not. The rewrite reads as a loss, not a win.

Both therefore pass. The amendments are flagged here so a later reader can see the criteria were narrowed and judge for themselves.

## Requirements Traceability

87 requirements, TR-001 through TR-087, no gaps in the range. Every one carries at least one `{TR-###}` task tag and appears in `plan.md`'s Requirement Coverage Map. Zero untraceable; zero tagged-but-undefined. 63/63 tasks marked `[X]`.

| Work Item | Priority | Status |
|---|---|---|
| OBJ1 Forward-Only Migration Sequence | P1 | **PASSED 12/12** - VC7 now evidenced by two tests (was 11/12) |
| OBJ2 Retrievable Chunk Store | P1 | **PASSED 9/9** - VC7 evidenced by T066's 26 cases (was 8/9 mid-run) |
| OBJ3 Provenance-Enforced Extraction | P1 | PASSED 8/8 |
| OBJ4 Procurement Lifecycle Store | P1 | PASSED 5/5 |
| OBJ5 Versioned Forecast Contract | P1 | **PASSED 10/10** - VC9 now evidenced by two tests (was 9/10) |
| OBJ6 Resolved Entity Store | P2 | PASSED 3/3 |

**47 of 47 validation criteria PASSED. 28 of 28 success criteria PASSED** (run 1: 45/47 and 25/28).

**One of those 47 was an overclaim for most of this run, and the sequence is recorded because it is the more useful finding.** This audit first reported 47/47 with OBJ2 PASSED 9/9. An independent story-verification pass over the same tree then found **OBJ2 VC7 had no test at all** — the criterion requiring that a document row missing its license basis or its REAL/SYNTHETIC layer label be rejected on either layer. Confirmed directly before acting on it: across all of `src/model/tests/`, `license_basis` and `source_kind` appeared only as fixture baseline values plus one read-back assertion, and neither was ever set to NULL, blank, or an invalid label. The `PROVENANCE_REJECTIONS` table covers the eight layer-**conditional** field groups (16 constraints), so it structurally could not reach the two fields VC7 calls unconditional. **Run 1 recorded the same criterion as passing**, so this was a two-run overclaim rather than a regression.

Closed by **T066** with 26 parametrized cases, each negative-controlled by real DDL inside a rolled-back session. The count above is therefore now measured rather than assumed.

Two things this uncovered that matter beyond the criterion:

- **The helper made the criterion untestable, which is likely why it went untested for two runs.** `document_row(source_kind, **overrides)` named its first parameter after the very column VC7 perturbs, so `document_row("REAL", source_kind=None)` raised `TypeError: got multiple values for argument 'source_kind'`. The discriminator was unreachable through the only helper that builds these rows. Renamed to `layer`; all 12 call sites pass it positionally, so the rename is behaviour-preserving.
- **A null or unrecognised layer label defeats all 16 conditional provenance checks at once**, and this is now pinned by test rather than inferred. With `source_kind` NULL every conditional check evaluates to NULL (`NULL <> 'REAL' OR …`), and with `source_kind = 'MADE_UP'` every one evaluates to true, so no branch fires. Control 3 dropped the NOT NULL and the row was **accepted**; control 4 dropped `ck_document__source_kind` and a row carrying complete REAL provenance labelled `MADE_UP` **inserted cleanly**. The NOT NULL and the closed set are the only rules standing between the table and a provenance shape no branch recognises — the same PostgreSQL three-valued-logic family that produced this epic's earlier `coalesce` defects.

Control 5 is worth noting as well: re-adding the presence check with the *declared-but-wrong* trim set `E' \t\n\r\f\v'` failed exactly four of the sixteen relevant cases — the two vertical-tab (`U+000B`) rejections and the two asserting a license basis of `'vvv'` is accepted — so the `U+000B`-versus-letter-`v` spelling is now pinned in both directions rather than only one.

OBJ1 VC6 is the one criterion not re-measured by executing its test, because `test_orchestration.py` cannot be run here. It is evidenced instead by two facts that are the substance of the criterion: `git diff HEAD -- docker-compose.yml` is empty and the file has not been touched since E001's `6ee3562`, and `tests/checks/test_orchestration.py` is likewise unmodified. An unchanged check against an unchanged file cannot have changed outcome, and E001's QC measured it green.

SC-027 re-verified end to end: `plan.md`'s AR-1 states both replacement cells exactly, and the current `specs/project-plan.md:657,660` really do read `ResolvedEntity | E009` and `PosteriorDraws / SurvivalArray | E007`, so the request is precise as well as present. The file is untouched by this branch.

## Traceability Gaps

All four of run 1's gaps are closed:

1. OBJ1 VC7 unevidenced - closed: two tests, verified above.
2. OBJ5 VC9 / SC-022 unevidenced - closed: two tests, verified above.
3. SC-009 misattribution - closed: criterion restated to match what the G-5 test asserts.
4. SC-028 unqualified - closed: scope qualifier carried into the criterion's own text.

**One gap remains, found in this run and missed by run 1: OBJ2 VC7 (T066).** See the correction under Requirements Traceability. It is the same species as run 1's gaps 1 and 2 — a criterion whose rule the schema enforces and whose enforcement nothing asserts — and it is treated the same way: a WARNING bug task, not a gate blocker, because neither required category depends on it.

**Method note, recorded because it changes how much this report's PASS is worth.** Both this run and run 1 read OBJ2 as fully evidenced. What found the gap was not a more careful audit but a *second, differently-framed* pass over the same tree: the audit checked whether the fixes held, and the story verification checked whether each criterion's cited evidence actually supports it. The second question is the one that catches an untested criterion, and one pass asking it found a defect two passes of the first kind had missed. A single-pass PASS on this feature should be read as weaker evidence than its numbers suggest.

## Documentation / Code Contradictions

Run 1's four are all resolved (see T059-T062 above). Two new ones, both in `plan.md`, both raised as **T064**:

- `plan.md:37` - "all ten disclosed gaps" inside the Principle VII gate verdict. There are eleven; G-11 was added during implementation and the count was not propagated.
- `plan.md:102` - "4 immutable helpers ... and 10 disclosed gaps". There are five helpers and eleven gaps.

`data-model.md`, which TR-083 makes normative for column semantics and object inventory, is correct on both counts, and `test_table_ownership.py` asserts every created object appears in it. Only the plan's summary is stale.

Two nits not raised as tasks: `data-model.md:905` lists `artifact_schema_version` as an addition to "all nine reproducibility fields" when it is one of the nine - redundant, not contradictory. And `plan.md:115`'s Testing Strategy row still describes the root coverage `source` in its plan-time state ("lists only `tests/checks/helpers` and `src/model/src/model/roster`"), which is now false; that row records a plan-phase decision rather than a claim about the delivered system, so it is left alone.

## Checklist Fulfillment (spot-check)

`checklists/data-integrity.md` - 36/36 complete. `[Testing]` and `[Security]` intent satisfied by the delivered suites. **GAP (WARNING, unchanged from run 1)**: `checklists/.checklists` still lists `CHL002 Testing` and `CHL003 Security` unchecked with no generated files; the epic carries `skip_checklist`, so advisory only.

## Performance / Accessibility

SKIPPED - no request-path NFRs in scope for this epic (`plan.md` records Performance Goals as N/A at this tier); no accessibility surface.

## Browser Runtime Validation

SKIPPED - not required. This epic has no API surface and no UI.

## Manual Testing

Not required; `manual-test.md` not generated.

## Tool Recommendations

- **`src/gateway` is outside the coverage denominator** and the comment justifying that is inaccurate for it. Raised as T065. Not gate-blocking - 34 statements cannot move 94% below 80 - but it is the same species of hole T055 and T056 just closed, in the module verify.yml itself calls "the most load-bearing module in the repository".
- **Ruff `S` remains scoped to `src/model` only.** `src/api`, `src/gateway`, and root select `["E","F","I","UP","B","SIM"]`. AD-003 scoped `S` to this entry, so this epic's commitment is met; three of four entries still have no security ruleset. Out of scope here, worth raising project-wide. Carried forward unchanged from run 1.
- **VC-level traceability in tests is by inference, not by tag.** Only 14 of the 47 validation criteria are named in test source; the rest are traced through `{TR-###}` tags, which is sound but means a deleted test does not point at the criterion it was carrying. A `VC` reference in the docstring of each criterion-bearing test would make objective coverage mechanically checkable, as T057 and T058 already do for VC7 and VC9.

## Environment Notes

No database incident this run. The database stayed healthy throughout and stayed at head `0010`; the `0 skipped` validity check was met on every full-suite run - 429 mid-run and **455 passed, 0 skipped** on the final one, with `document` left empty and all its constraints verified intact after T066's negative controls. Scratch artefacts created during the audit (`.coverage.t056`, two ruff probe files) were removed; `git status` matches its pre-audit state apart from this report and the `tasks.md` additions.

**Operational note — half of this was fixed on `main` after the run, half was not.** The original note read: *"`tests/checks/test_orchestration.py` is not safe to run from two checkouts of this repository on one host — it binds 5434 and tears down the volume with `-v`."*

`main` commit `83df9b3` addressed the **cross-checkout** half. `docker-compose.yml` now publishes `${PRC_DB_PORT:-5434}`, `tests/checks/helpers/ports.py` resolves a substitute port when the default is held, and the run warns loudly when it binds one so a green result cannot silently claim evidence for the committed topology. A sibling checkout no longer collides.

The **local** half stands: teardown is still `_compose("down", "-v", …)` (`test_orchestration.py:168`), and Compose scopes that to this checkout's own project, so running the file still destroys this checkout's `db-data` volume and the applied migration chain. It therefore remains excluded from local QC runs for the reason that actually cost this epic a re-run, and OBJ1 VC6's second half is still evidenced structurally rather than by execution. CI is unaffected — it runs all of `tests/checks` against a fresh service container with nothing to lose.

Measured after merging `main` into this branch: root cross-entry rises from **75 to 89** passing (`test_ports.py` adds 14), the coverage denominator from 535 to **607** statements as `helpers/ports.py` enters it at 89%, and the gate holds at **93%**, exit 0.

**Second sibling-checkout defect, found while closing T065 and worth more attention than the task that exposed it.** Two of this checkout's three Python entry virtualenvs had their own package pointing at the sibling checkout `S:\claudecode\KayaDemoProcurementRisk` (no trailing `1`):

| Entry venv | `import <pkg>` resolved to | |
|---|---|---|
| `src/model` | `...Risk1\src\model\src\model\__init__.py` | correct |
| `src/gateway` | `...Risk\src\gateway\src\gateway\__init__.py` | **sibling** |
| `src/api` | `...Risk\src\api\src\api\__init__.py` | **sibling** |

The mechanism was a stale absolute path in `src/gateway/.venv/Lib/site-packages/gateway.pth`, contents `S:\claudecode\KayaDemoProcurementRisk\src\gateway\src`. The visible symptom was trivial - the new gateway coverage step reported `No data was collected` because the executed file lay outside the measured tree. The invisible symptom is not: the five gateway tests were passing against a **different working tree's source**, so a local green said nothing about this branch's gateway code. Repaired with `uv sync --locked --directory src/gateway` and `src/api`, which named the swap in its own output (`- gateway==0.1.0 (from file:///S:/claudecode/KayaDemoProcurementRisk/src/gateway)` → `+ ... /KayaDemoProcurementRisk1/...`).

Scope of the consequence, stated rather than glossed: `src/model` was **correct**, so every measurement in this report - the 429 model tests, the 94% gate, all schema evidence - was taken against this checkout and stands. Only the gateway and api entries were affected, and CI is immune because it syncs a fresh checkout. There is no repository-side fix available: `.venv` is untracked, and the bad path was generated locally. Recommend `uv sync --locked` across all entries as the first step of any local QC run, and treat an unexplained `No data was collected` as a resolution problem rather than a coverage-configuration one.

## Bug Tasks Generated

3 tasks, **T064-T066**, appended under `## Phase: Bug Fixes` in `tasks.md`. All three **WARNING**; none blocks either required category. All three were fixed and re-measured within this run, so `tasks.md` closes at **66/66 `[X]`** with no open bug task.

T066 was not generated by this audit. It came from an independent story-verification pass and contradicts what this audit had already recorded, which is why it is called out under Requirements Traceability rather than listed here as a routine finding.

## Bug Context

**T064** - `plan.md:37` and `plan.md:102`. Delivered: five `fn_*` helpers and eleven gaps, both confirmed against the live database and `data-model.md`. Plan says four and ten. Line 37's count sits inside a Principle VII compliance verdict, which is why a stale number there matters more than it looks.

**T065** - root `pyproject.toml:35-38`:

```
# ... two of the four entries ship no runtime code yet ...
source = ["tests/checks/helpers", "src/model/src/model/roster", "src/model/src/model/schema"]
```

True of `src/api` (three empty `__init__.py`). False of `src/gateway`, which ships `provider.py` at 34 lines with five tests that verify.yml already runs - measured by nothing.
