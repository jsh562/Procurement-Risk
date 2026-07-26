# Tasks: Monorepo Scaffold and Contracts

**Input**: Design documents from `specs/00001-monorepo-scaffold-and-contracts/`
**Prerequisites**: `plan.md`, `spec.md`, `data-model.md`, `research.md`, `checklists/`

**Tests**: Included — `spec.md` OBJ2 requires fixture-based negative tests and an aggregated coverage gate, so test tasks are requirement-bearing rather than optional.

**Organization**: Grouped by Technical Objective (`OBJ#`), `spec_type: technical`. Requirement tags are `TR-###`.

## Project Mode

`Greenfield` — zero source files exist. Repository has no commits; the first commit and a hosted remote are established here (HINT-001).

## Epic / Capability Map

- `[OBJ1]` → Four entries under `/src` with independent dependency graphs (P1)
- `[OBJ2]` → Lint, format, type-check, test, and coverage toolchain for every entry (P1)
- `[OBJ3]` → Build-enforced import contracts plus the provider source scan (P1)
- `[OBJ4]` → Serving image accounted for by its own lockfile (P1)
- `[OBJ5]` → Local orchestration with non-starting one-shot jobs (P1)
- `[OBJ6]` → Shared project/vendor roster, single reader, and datasheet (P1)
- `[OBJ7]` → Dispatchable verification workflow (P2)

## Global Execution Rules

- **HINT-005**: Run every Python tool as `uv run --directory src/<entry> …`. A bare `pytest`/`ruff`/`lint-imports` from the repo root resolves against whichever environment is active and silently crosses the boundary the contracts exist to enforce.
- The three Python entries are **standalone `uv` projects, not workspace members**. Never add a `[tool.uv.workspace]` table.
- Import-linter configuration lives in each entry's own `pyproject.toml` and runs from that entry's environment — the tool requires an importable root package.
- Every check exits non-zero naming the violated rule, and the offending module or file where the violation is attributable to a source location (TR-019).

---

## Phase 1: Setup (Repository / Workspace Delta)

- [X] T001 [P] Initialize the repository: `git init`, default branch `main`, root `.gitignore` for `.venv/`, `node_modules/`, `__pycache__/`, `.coverage*`, then make the first commit
- [X] T002 [P] {TR-026} Pull `pgvector/pgvector:pg16` and the `python:3.12-slim` base image and record both digests for later pinning; a pull failure is epic-blocking, not transient

**Notes**: T002 front-loads the ~450 MB cold pull that `docker compose up` would otherwise trigger as a side effect, and produces the two digests TR-026 pins. T001 is the prerequisite for TR-021 — `workflow_dispatch` is unavailable until the workflow sits on a default branch that exists.

---

## Phase 2: OBJ1 - Four Entries with Isolated Dependencies (Priority: P1) 🎯 MVP

- [X] T003 [OBJ1] {TR-002,TR-003,TR-024} Create standalone uv project src/gateway/ (pyproject.toml, src/gateway/__init__.py, uv.lock); PyPI only, no web framework, no modeling stack
- [X] T004 [P] [OBJ1] {TR-002,TR-024} Create standalone uv project src/api/ declaring FastAPI and the gateway path dependency, never src/model; commit uv.lock after:T003
- [X] T005 [P] [OBJ1] {TR-002,TR-024} Create standalone uv project src/model/ declaring PyMC, ArviZ, pandas, NumPy and the gateway path dependency, never src/api; commit uv.lock after:T003
- [X] T006 [OBJ1] {TR-001,TR-024} Scaffold Next.js 16 App Router in src/web with a single package-lock.json; pin turbopack.root and outputFileTracingRoot in next.config.ts
- [X] T007 [OBJ1] {TR-023} Verify each entry's lockfile against its own manifest: `uv lock --check` and `uv sync --locked` per Python entry, `npm ci` in src/web; none consults another's
- [X] T008 [P] [OBJ1] {TR-001} Assert exactly four entries under /src, each with its own dependency manifest, and exactly one JS lockfile in src/web, in tests/checks/test_layout.py
- [X] T009 [P] [OBJ1] {TR-004} Compare declared third-party deps in tests/checks/test_dependency_isolation.py; none of model's appears in api's resolved set, first-party path deps excluded
- [X] T010 [OBJ1] {TR-002,TR-003} [COMPLETES TR-002] In tests/checks/test_dependency_isolation.py assert gateway carries no modeling stack or web framework and neither boundary declares the other

**Notes**: The gateway must exist before either boundary's manifest can resolve — both declare it as a path dependency, neither declares the other. Use `app/` in src/web, never a nested `src/app/`; one silently wins. Nothing downstream can be linted, tested, or contract-checked until this phase completes.

---

## Phase 3: OBJ2 - Quality Toolchain for Every Entry (Priority: P1) 🎯 MVP

- [X] T011 [P] [OBJ2] {TR-005} Add dev toolchain to src/api: pytest, pytest-cov, hypothesis, ruff, import-linter, coverage; ruff lint and format config in src/api/pyproject.toml
- [X] T012 [P] [OBJ2] {TR-005} Add dev toolchain to src/model: pytest, pytest-cov, hypothesis, ruff, import-linter, coverage; ruff lint and format config in src/model/pyproject.toml
- [X] T013 [P] [OBJ2] {TR-005} Add dev toolchain to src/gateway: pytest, pytest-cov, ruff, import-linter, coverage; no Hypothesis this epic per AD-005
- [X] T014 [P] [OBJ2] {TR-005} Configure src/web tooling: ESLint, Prettier, Vitest, and tsc in eslint.config.mjs, .prettierrc, vitest.config.ts, and src/web/__tests__/
- [X] T015 [OBJ2] {TR-006} Configure aggregated coverage in root pyproject.toml: distinct COVERAGE_FILE per entry, `coverage combine`, `report --fail-under=80`, per-file rows
- [X] T016 [OBJ2] {TR-007} Create the fixture harness: tests/fixtures/ outside every production contract root, plus a fixture-specific import-linter config and a non-zero-exit helper
- [X] T017 [OBJ2] {TR-005} [COMPLETES TR-005] Run lint, format, and type checks clean for all four entries: `ruff check`, `ruff format --check`, `npm run lint`, `npx tsc --noEmit`

**Notes**: **HINT-003** — pin one identical `coverage` version across T011, T012, T013, and the root combine step in T015; data files carry a schema version and `combine` rejects mismatches. T011–T014 touch four disjoint manifests and are parallel; each still writes the same `pyproject.toml` its OBJ1 and OBJ3 tasks write, so it is never parallel with those. The coverage denominator holds only the source scan, the two image checks, and the roster reader — per AD-002 that logic lives in importable helpers under `tests/checks/helpers/`, imported by thin test modules, so it is not tautologically covered.

---

## Phase 4: OBJ3 - Build-Enforced Architecture Contracts (Priority: P1) 🎯 MVP

- [X] T018 [OBJ3] {TR-008,TR-019} Add the `protected` import-linter contract to src/gateway/pyproject.toml: `anthropic` protected, the gateway module its sole importer, direct imports only
- [X] T019 [P] [OBJ3] {TR-009} Reserve empty src/api/src/api/compute/ and src/api/src/api/llm/, and add the `forbidden` contract with indirect detection on to src/api/pyproject.toml
- [X] T020 [P] [OBJ3] {TR-009} Reserve empty src/model/src/model/compute/ and src/model/src/model/llm/, and add the `forbidden` contract with indirect detection on to src/model/pyproject.toml
- [X] T021 [OBJ3] {TR-010,TR-019} Implement the provider-name source scan in tests/checks/helpers/source_scan.py and tests/checks/test_single_import_site.py, scanning only .py/.ts/.tsx/.js/.jsx under the four entries and skipping manifests, lockfiles, and installed-package directories → exports: scan_source_root(root,name)
- [X] T022 [P] [OBJ3] {TR-007} Add the allowlist-contract negative fixture in tests/fixtures/provider_import/, asserting a non-zero exit; the clean-tree run is its positive control after:T018
- [X] T023 [P] [OBJ3] {TR-007} Add the re-export laundering fixture in tests/fixtures/reexport/: the import contract passes while the source scan exits non-zero naming the file after:T021
- [X] T024 [OBJ3] {TR-007} Add boundary-contract negative fixtures in tests/fixtures/computation_boundary/ covering a direct path and an indirect path through an intermediate module after:T020

**Notes**: `protected` (not `forbidden`) is correct for the single-import rule — a `forbidden` contract detects indirect imports by default and would flag every module transitively reaching the gateway. The reserved `compute` and `llm` packages are empty here; they are what makes the boundary contract non-vacuous once later epics populate them. The scan's root is all four entries under `/src`, excluding each Python entry's contract configuration and every path under `tests/fixtures/`.

---

## Phase 5: OBJ4 - Serving Image Accounted For by Its Lockfile (Priority: P1) 🎯 MVP

- [X] T025 [OBJ4] {TR-011,TR-025,TR-026} Author src/api/Dockerfile (digest-pinned base) and .dockerignore scoping the build context to src/api and src/gateway only; no credentials after:T002
- [X] T026 [OBJ4] {TR-011} Build the serving image and assert in tests/checks/test_build_context.py that the committed definition reaches no other path under /src
- [X] T027 [OBJ4] {TR-012,TR-019} Implement the lock-derived allowlist check in tests/checks/helpers/image_contents.py and tests/checks/test_image_contents.py → exports: expected_dists(lock)
- [X] T028 [OBJ4] {TR-013,TR-019} Implement the in-image denylist in tests/checks/helpers/image_contents.py and test_image_contents.py; names derived from model metadata
- [X] T029 [P] [OBJ4] {TR-007} Negative case for the allowlist check: inject a stub distribution into a container started from the real image at runtime, then run the check there after:T027
- [X] T030 [P] [OBJ4] {TR-007} [COMPLETES TR-007] Negative case for the denylist and its positive control: inject a stub modeling module with matching metadata into a live container after:T028

**Notes**: **HINT-002** — `uv export` resolves environment markers for the *running* platform; the host is Windows and the image is Linux, so export with an explicit Linux platform and normalize both sides per PEP 503 before comparing. Restrict the export to the serving boundary's default group; development-only groups sit outside both sides. **HINT-004** — `docker run --entrypoint python` may resolve the system interpreter rather than the copied venv: pin the venv interpreter path, assert `ModuleNotFoundError` specifically rather than any non-zero exit, and assert the web framework *does* import as the positive control. T029 and T030 take runtime injection into a container from the real image, not a committed source fixture — an import-linter-shaped fixture cannot express an image violation, and this avoids a second image build and a multi-hundred-megabyte modeling install.

---

## Phase 6: OBJ5 - Local Orchestration with Non-Starting Jobs (Priority: P1) 🎯 MVP

- [X] T031 [OBJ5] {TR-015,TR-026} Add digest-pinned pgvector Postgres to docker-compose.yml on port 5434 with committed dev credentials and a healthcheck after:T002
- [X] T032 [OBJ5] {TR-015} Add the api service on host port 8001 and the web service on host port 3000 to docker-compose.yml, avoiding the occupied 5432 and 8000 defaults
- [X] T033 [OBJ5] {TR-014} Define the one-shot job services under a non-default `jobs` profile in docker-compose.yml so ordinary startup never launches them
- [X] T034 [OBJ5] {TR-014,TR-015} [COMPLETES TR-015] Verify `docker compose up -d`: only persistent services start, zero job containers, Postgres healthy with the vector extension, all ports bind
- [X] T035 [OBJ5] {TR-014} [COMPLETES TR-014] Verify `docker compose --profile jobs run --rm <job>` runs to completion, exits, and leaves no container behind

**Notes**: The pgvector image is already local after T002, so T034 exercises orchestration rather than the download. Credentials here are literal development-only values committed in the orchestration definition, declared non-secret because the service binds locally — TR-025's prohibition stays scoped to provider credentials.

---

## Phase 7: OBJ6 - Shared Project and Vendor Roster with Datasheet (Priority: P1) 🎯 MVP

- [X] T036 [P] [OBJ6] {TR-017} Author data/roster/naming-convention.json and real-firm-exclusions.json with the committed patterns, normalization object, and sorted unique entries
- [X] T037 [P] [OBJ6] {TR-018} Author data/roster/roster-datasheet.md with the five required level-2 sections and no literal digest anywhere in it
- [X] T038 [OBJ6] {TR-016} Author data/roster/project-vendor-roster.json: exactly 5 PRJ-### projects and 12 VND-### vendors, UTF-8 without BOM, two top-level keys, no version field after:T036
- [X] T039 [OBJ6] {TR-016,TR-027,TR-017,TR-019} Implement the stdlib-only roster reader in src/model/src/model/roster/reader.py → exports: read_roster(path)->(roster,hash)
- [X] T040 [OBJ6] {TR-027,TR-017} [COMPLETES TR-017] Test the reader in src/model/tests/test_roster_reader.py — VR-008a/b/c determinism, VR-001…VR-007, VR-009, VR-011, VR-015
- [X] T041 [OBJ6] {TR-016} [COMPLETES TR-016] Add the VR-013 single-reader scan to tests/checks/test_single_import_site.py, scanning /src and excluding /tests and /data after:T021
- [X] T042 [OBJ6] {TR-018} Assert datasheet completeness in src/model/tests/test_roster_datasheet.py: VR-010 heading and sub-condition checks, VR-016 absence of any literal digest after:T037

**Notes**: T039 implements CS-1…CS-6 canonicalization and VR-001…VR-015 in that order — every validation rule is evaluated before any hash is emitted, and a failing roster yields a non-zero exit and no hash. Permitted imports are `json`, `hashlib`, `unicodedata`, `pathlib`, `re` only; adding a dependency to `src/model/pyproject.toml` for the roster fails VR-009. Hash determinism in T040 is property-based: formatting, key order, entry order, line endings (load-bearing on Windows, where `core.autocrlf` rewrites the working copy), and trailing newline must not move the hash.

---

## Phase 8: OBJ7 - Dispatchable Verification Workflow (Priority: P2)

- [X] T043 [OBJ7] {TR-020,TR-019} Author .github/workflows/verify.yml with both `on: push` and `workflow_dispatch`, and one named step per check: lint, format, types, tests, locks, contracts, image, coverage, index config, digest pinning, credential absence
- [X] T044 [OBJ7] {TR-022} Add the violation-injection workflow input to .github/workflows/verify.yml, writing the violation into the runner's ephemeral working tree, inside the real contract root so the contracts actually see it, and never committing it
- [X] T045 [OBJ7] {TR-021} Create the hosted remote and land verify.yml on the default branch `main`, then push the feature branch, so dispatch becomes available at all after:T001
- [X] T046 [OBJ7] {TR-020} Evidence SC-013's first half via the push trigger on a clean tree — every check runs and the workflow succeeds. Uses TR-022's pushed-branch path rather than `workflow_dispatch`, which needs a token this environment does not hold; SC-013 permits either after:T045
- [X] T047 [OBJ7] {TR-022} Evidence SC-013's second half by pushing a throwaway branch carrying an injected violation; the run fails and names the violated check, then the branch is deleted after:T046

**Notes**: T045 is gated on T001's first commit and cannot be simulated — `workflow_dispatch` is genuinely unavailable until the file exists on the default branch. No check the workflow invokes requires credential access, so the dispatched run needs no repository secret. Automatic `push`/`pull_request` triggers are deliberately out of scope and owned by E002.

---

## Phase 9: Polish & Cross-Cutting Concerns

- [X] T048 {TR-019} [COMPLETES TR-019] Audit every contract and check for a non-zero exit naming the violated rule and, where attributable, the offending module or file
- [X] T049 [P] Run `pip-audit` per Python entry and `npm audit` in src/web, recording results as reported-not-gated per the plan's Testing Strategy
- [X] T050 [P] {TR-006} Run the aggregated coverage gate at the repo root: `coverage combine && coverage report --fail-under=80` over the source scan, image checks, and roster reader
- [X] T051 [P] {TR-024} [COMPLETES TR-024] Implement the index-configuration check in tests/checks/test_supply_chain.py, inspecting each Python entry's pyproject.toml and any uv.toml plus the web boundary's .npmrc and package.json, exiting non-zero and naming the file when an alternate, supplemental, or private index is configured
- [X] T052 [P] {TR-026} [COMPLETES TR-026] Implement the digest-pinning check in tests/checks/test_supply_chain.py, scanning src/api/Dockerfile and docker-compose.yml and exiting non-zero naming any image reference carrying no digest
- [X] T053 [P] {TR-025} [COMPLETES TR-025] Implement the credential-absence check in tests/checks/test_supply_chain.py over the serving image build context and the built image's layers; it passes vacuously this epic and exists to fail when a provider credential is first introduced

---

## Dependencies

Setup → OBJ1 → OBJ2 → OBJ3 → OBJ4 → OBJ5/OBJ6 → OBJ7 → Polish

- **OBJ1 gates OBJ2 and OBJ3**: nothing can be linted, tested, or contract-checked before the entries and their manifests exist.
- **T003 gates T004 and T005**: both boundaries declare the gateway as a path dependency, so it must resolve first.
- **OBJ2 gates OBJ3 and OBJ4**: import-linter and pytest must be installed per entry before any contract or check runs.
- **OBJ3's T021 gates OBJ6's T041**: the VR-013 single-reader scan reuses the source-scan helper.
- **Setup's T002 gates T025 and T031**: both pin a digest recorded by the pull.
- **Setup's T001 gates T045**: the repository has zero commits, and dispatch requires a default branch carrying the workflow.
- **OBJ5, OBJ6 are independent of each other** and of OBJ4 once OBJ1 and OBJ2 are complete; OBJ7 depends on every check it invokes existing.
- Tasks with `after:T###` depend on the referenced task — verify it is `[X]` before executing.
- A task with `after:T###` or `← T###:Symbol` is never `[P]`-batched with the task it references.

## Parallel Opportunities

Contiguous runs of `[P]` tasks form one parallel batch:

| Batch | Tasks | Disjoint files |
|-------|-------|----------------|
| Setup | T001, T002 | git tree vs. Docker image cache |
| OBJ1 manifests | T004, T005 | `src/api/pyproject.toml` vs. `src/model/pyproject.toml` |
| OBJ1 checks | T008, T009 | `test_layout.py` vs. `test_dependency_isolation.py` |
| OBJ2 toolchain | T011, T012, T013, T014 | four disjoint entry manifests |
| OBJ3 boundary contracts | T019, T020 | `src/api/pyproject.toml` vs. `src/model/pyproject.toml` |
| OBJ3 source fixtures | T022, T023 | `tests/fixtures/provider_import/` vs. `tests/fixtures/reexport/` |
| OBJ4 image negatives | T029, T030 | separate containers, separate check modules |
| OBJ6 authored data | T036, T037 | convention/exclusions vs. datasheet |
| Polish | T049, T050, T051, T052, T053 | audit reports, coverage combine, and three disjoint new check modules |

**Not parallel despite serving different requirements**: any two tasks writing the same `pyproject.toml` — T003/T013, T004/T011/T019, T005/T012/T020 — and any two tasks writing `docker-compose.yml` (T031, T032, T033) or `verify.yml` (T043, T044).

---

## Phase: Bug Fixes

Generated by `/sddp-qc` 2026-07-25. Every executable check passed; these are traceability and
compliance failures — things claimed as verified that nothing actually verifies.

- [X] T054 [BUG:CRITICAL] {TR-001} [pi-violation] Next.js pinned at 16.2.12 against the declared Next.js 15 stack — src/web/package.json
  > Error: `next: 16.2.12`, `eslint-config-next: 16.2.12`; project-instructions.md and plan.md both specify Next.js 15 (App Router)
  > Fix hint: `create-next-app@latest` installed the current major. Either pin to 15.x and re-verify the four web checks, or amend project-instructions.md and plan.md to declare 16 with a recorded reason. Do not leave the stack undeclared.
- [X] T055 [BUG:ERROR] {TR-012} [requirement-gap] Image allowlist asserts containment, not equality, and equality is currently false — tests/checks/helpers/image_contents.py:47
  > Error: expected - installed == {'colorama'}; `click -> colorama marker=sys_platform == 'win32'`
  > Fix hint: expected_distributions() ignores the `marker` field entirely. Evaluate markers for linux-x86_64 (HINT-002 specifies exporting with an explicit Linux platform), then assert both directions. Also drop the hand-maintained {"pip","setuptools"} exemptions — neither is installed, and an allowlist carrying dead exemptions can mask a real leak.
- [X] T056 [BUG:ERROR] {TR-025} [requirement-gap] SC-018 requires scanning the built image's layers; only source files are scanned — tests/checks/test_supply_chain.py
  > Error: test_no_credential_material_in_the_serving_build_context walks src/api and src/gateway only
  > Fix hint: add a `docker history --no-trunc` / in-image filesystem scan so a credential baked into a layer is detectable, not just one committed to source.
- [X] T057 [BUG:ERROR] {TR-024} [requirement-gap] SC-016 names package.json among inspected artifacts; nothing reads it — tests/checks/test_supply_chain.py:34
  > Error: `.npmrc` is absent so the test returns early; package.json is never opened
  > Fix hint: inspect `publishConfig.registry`, `overrides`, and any tarball/git URL dependency. A registry override there passes unseen today.
- [X] T058 [BUG:ERROR] {TR-015} [requirement-gap] SC-010's vector extension is asserted nowhere — docker-compose.yml
  > Error: healthcheck is `pg_isready` only; no CREATE EXTENSION vector and no assertion
  > Fix hint: add an init script creating the extension plus a check querying pg_extension. It was confirmed by hand during implementation, which is exactly the evidence this epic exists to replace.
- [X] T059 [BUG:ERROR] {TR-014} [requirement-gap] SC-009's job-completion half has no executable check
  > Error: no test invokes `docker compose --profile jobs run --rm`, and no CI step does either
  > Fix hint: assert the job runs to completion, exits zero, and leaves no container behind. The non-start half is structurally verifiable from the profile; the completion half is not.
- [X] T060 [BUG:ERROR] {TR-011} [requirement-gap] SC-015 has no regression guard; T026's named file was never created — tests/checks/test_build_context.py
  > Error: tasks.md T026 marked [X] naming tests/checks/test_build_context.py; the file does not exist and, unlike the other consolidated files, has no successor
  > Fix hint: assert the committed image definition reaches no path under /src beyond api and gateway. The property holds today by .dockerignore, but nothing would catch a regression.
- [X] T061 [BUG:WARNING] {TR-013} [requirement-gap] Denylist module names derive from manifest strings, not installed metadata — tests/checks/helpers/image_contents.py:86
  > Error: `{name.replace("-","_") for name in declared - first_party}` substitutes for the metadata lookup TR-013 mandates
  > Fix hint: TR-013 requires top-level modules read from the modeling boundary's installed distribution metadata, run against its synced environment. Correct today for arviz/numpy/pandas/pymc by coincidence of naming.
- [X] T062 [BUG:WARNING] {TR-017} [coverage-gap] Naming-convention check lives inline in test bodies, outside the coverage denominator — src/model/tests/test_roster_datasheet.py:41
  > Error: TR-017 states its check "falls inside TR-006's coverage denominator"; coverage source is ["tests/checks/helpers", "src/model/src/model/roster"]
  > Fix hint: extract to an importable helper, as AD-002 requires and as every other check already does.
- [X] T063 [BUG:WARNING] {TR-022} [requirement-gap] Violation-injection writes into production roots, not a scratch copy — .github/workflows/verify.yml
  > Error: step writes src/gateway/src/gateway/_injected.py directly, while its own comment and T044 both describe copying a contract root to a scratch path
  > Fix hint: implement the scratch copy, or correct the comment and T044. Nothing is committed so TR-022 holds, but the described mechanism does not exist.
- [X] T064 [BUG:WARNING] {TR-020} [requirement-gap] T046 marked complete without a workflow_dispatch run
  > Error: all ten runs are `push` events; no dispatch has ever occurred
  > Fix hint: SC-013 is satisfied via the pushed-branch path, so the substance holds — but the task text claims a dispatch. Either run one or reword T046 to the path actually used.
- [X] T065 [BUG:WARNING] {TR-008} [test-coverage] The single permitted provider import site has no test — src/gateway/src/gateway/provider.py
  > Error: client_type() is invoked by nothing; the module is exercised only as a side effect of import-linter building its graph
  > Fix hint: the most architecturally load-bearing module in the repository is its least tested. E004 implements the wrapper; a test asserting client_type() returns the SDK class costs three lines now.
- [X] T066 [BUG:ERROR] [RECURRING] {TR-022} [requirement-gap] The dispatch injection guard fired on the success path and killed the step — .github/workflows/verify.yml
  > Error: `git diff --quiet && { ...; exit 1; } || true` — the injected file is untracked, `git diff` ignores untracked files, so the guard fired whenever injection *succeeded*; `|| true` cannot catch an `exit`
  > Fix hint: introduced by T063's own fix and invisible because the step is `skipped` in every push run. Replaced with `test -s "$target"`. Reproduced verbatim in a throwaway repo before and after.
- [X] T067 [BUG:ERROR] {TR-020} [requirement-gap] Gateway tests ran nowhere in CI, so "every check runs" was false
  > Error: verify.yml had `Unit tests (model)` and `Unit tests (web)` only; the 5 gateway tests added for T065 were never invoked on a runner
  > Fix hint: added a `Unit tests (gateway)` step. The gateway holds the single permitted provider import site, so it was the worst possible entry to omit.
- [X] T068 [BUG:WARNING] {TR-012} [requirement-gap] Marker environment hardcoded a patch version the image does not run
  > Error: IMAGE_ENVIRONMENT pinned python_full_version 3.12.7; the image runs 3.12.13
  > Fix hint: read from the image at check time. Harmless against today's two marker forms, wrong the moment a patch-level boundary appears.
