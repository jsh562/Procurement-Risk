---
feature_branch: "00001-monorepo-scaffold-and-contracts"
created: "2026-07-25"
input: "e001"
spec_type: "technical"
spec_maturity: "clarified"
epic_id: "E001"
epic_sources: "{SAD:ADR-0010}{SAD:ADR-0003}{SAD:ADR-0007}{SAD:ADR-0008}"
---

# Feature Specification: Monorepo Scaffold and Contracts

**Feature Branch**: `00001-monorepo-scaffold-and-contracts`
**Created**: 2026-07-25
**Status**: Draft
**Spec Type**: technical
**Spec Maturity**: clarified
**Epic ID**: E001
**Epic Sources**: {SAD:ADR-0010}{SAD:ADR-0003}{SAD:ADR-0007}{SAD:ADR-0008}
**Product Document**: specs/prd.md

## Problem Statement

Three of this project's governing rules — one module may reach the model provider, computation never happens in model-facing code, and the request-serving image never carries the modeling stack — are currently prose. Prose erodes: the first contributor in a hurry adds an import, and nothing objects. Every later epic inherits whichever violations accumulated before anyone noticed. This feature establishes the repository structure, the quality toolchain, and the build-time machinery that turns those rules into failures, so that eighteen downstream epics build on enforcement rather than on intention.

## Scope

### Included

- Four entries under `/src` — three source boundaries plus a shared gateway package — with independent dependency graphs
- Lint, format, type-check, test, and coverage tooling for every boundary, since no later epic can establish them
- Build-time contracts covering the single model-provider import site and the deterministic-computation boundary
- An assertion that the built request-serving image carries only what its lockfile accounts for
- Local container orchestration where one-shot jobs never start with the persistent services
- A committed project and vendor roster with an accompanying datasheet, consumed by two later epics
- A dispatchable workflow that runs every check in one place

### Excluded

- Any application behaviour — no routes, no schema, no model calls — those belong to E003, E004, and later
- **Pull-request triggering and branch protection** — E002 owns these. Push triggering is *included*: the workflow carries `on: push` alongside `workflow_dispatch`, so contract violations fail the build in this epic rather than one epic later. The earlier deferral of all automatic triggering was reversed during the analyze phase, which found it was a scope choice rather than a technical blocker.
- Hosted deployment configuration — E018 owns it
- **Build provenance or attestation for the serving image** — the image is built locally to run this epic's checks and is never published to a registry or deployed here, so no attestation obligation arises. E018 owns hosted deployment and whatever attestation comes with it; what this epic ships in place of provenance is digest-pinned externally pulled images (TR-026) and per-entry lock verification (TR-023).
- Populating the corpus or procurement history — E002 and E005 consume the roster, they do not ship in this epic
- Lifecycle values, dates, or quantities for the roster population — E005 generates those

### Edge Cases & Boundaries

- A module obtains the provider client by reading it off the gateway module rather than importing it — re-export laundering leaves no direct import edge, so the import contract passes and the source scan is the check that catches it.
- A module reaches the provider through a dynamic or computed import — invisible to static analysis, stated as a known limit rather than claimed as covered.
- A modeling distribution reaches the serving image transitively under an unrelated name — the reason the image check compares against a lock-derived allowlist rather than screening for known names.
- A modeling dependency is added to the serving manifest **and** the lockfile is regenerated — the allowlist check passes, because installed and expected now agree. Only the in-image import check catches this case; the scoped build context does not, since it prevents reaching the local modeling source but not installing the same distributions from a package index.
- Date, ranking, or probability logic is written inline inside a model-facing module rather than imported from the reserved computation package — no import edge exists, so the boundary contract cannot see it. This is a known limit until later epics place all such logic in the computation package, which is what converts the convention into an enforceable edge.
- A modeling distribution outside the in-image check's enumerated list reaches the serving image — that check is a denylist and is blind to anything not named in it, which is why its list is derived from the modeling boundary's declared dependencies rather than hand-maintained.
- Vendored or copied source carries no distribution metadata and is invisible to a metadata query.
- Two lockfiles appear inside the web boundary and the framework infers the wrong project root.
- The database or API host port is already occupied on a developer machine, and orchestration fails at start rather than at first use.
- The roster changes after a consumer has generated data from it, and the drift is undetectable without a recorded content hash — which is why the reader computes one and consumers record it (TR-027), rather than the fixture carrying a hand-maintained version field whose forgotten bump would make drift recordable but not detectable.

## Technical Objectives

### Objective 1 - Four Entries with Isolated Dependencies (Priority: P1)

Establish `/src/web`, `/src/api`, `/src/model`, and `/src/gateway`, each owning its dependency manifest and lockfile. The three Python entries are **independent projects, not workspace members** — a shared workspace resolves one dependency graph across members and cannot prevent one member importing another's dependencies, which would defeat the isolation this layout exists to provide. Both Python boundaries depend on the gateway package; neither depends on the other.

**Why this priority**: Every other objective and every later epic assumes this layout; nothing can proceed without it.

**Rationale**: Independent dependency graphs make serving/modeling isolation assertable rather than aspirational. The gateway is a separate minimal entry so that exactly one module in the repository imports the provider client while both consumers reach it without depending on each other.

**Deliverables**:
- Directory structure with four entries under `/src`
- Independent dependency manifest and lockfile per Python entry
- Gateway package manifest carrying the provider client and validation only — no web framework, no modeling stack
- Single JavaScript lockfile inside the web boundary, with framework project root pinned explicitly
- Lock-verification commands runnable per entry

**Validation Criteria**:
1. **Given** the three Python entries, **When** lock verification runs in each, **Then** each succeeds against its own lockfile and none consults another's.
2. **Given** the modeling boundary's directly declared third-party dependencies, **When** the serving boundary's resolved set is searched for each, **Then** none is present. Shared transitive dependencies are unaffected because the comparison is against declared dependencies; the gateway package is excluded because both boundaries declare it by design.
3. **Given** the gateway package's resolved set, **When** inspected, **Then** it contains no distribution from the modeling stack and no web framework.
4. **Given** the manifests, **When** inspected, **Then** neither Python boundary declares the other as a dependency or workspace member, and both declare the gateway package.
5. **Given** `/src`, **When** its entries are counted, **Then** there are exactly four, each carrying its own dependency manifest.
6. **Given** the web boundary, **When** the application builds, **Then** exactly one JavaScript lockfile exists within it and the resolved project root is the web boundary.

### Objective 2 - Quality Toolchain for Every Entry (Priority: P1)

Establish linting, formatting, test running, and coverage measurement for all four entries, plus type checking for the TypeScript boundary. This is the only epic in which they can be established; every later epic inherits their presence or absence.

**Why this priority**: Objective 7 claims to run these checks and the project's quality policy requires two categories — linting, which covers lint, static analysis, and code quality together, and coverage. Without this objective those are commitments with nothing behind them.

**Rationale**: A scaffold epic that ships enforcement contracts but no test runner leaves the contracts themselves untested, and leaves both required quality categories with nothing to execute.

**Deliverables**:
- Lint and format configuration per entry, with static analysis failing on violation
- Type checking for the TypeScript boundary
- Test runner configuration per entry, with tests located alongside the code they cover
- Coverage measurement scoped to this epic's executable artifacts — the source scan, the image checks, and the roster reader — since configuration files carry no meaningful coverage denominator
- Fixture-based negative tests proving each contract fails when violated

**Validation Criteria**:
1. **Given** any entry, **When** its lint and format checks run on a clean tree, **Then** they pass with exit code zero.
2. **Given** the TypeScript boundary, **When** type checking runs, **Then** it reports no errors.
3. **Given** this epic's executable artifacts, **When** the test suite runs with coverage, **Then** coverage of those artifacts is at or above the project target.
4. **Given** the committed negative fixtures, **When** the test suite runs, **Then** each contract is proven to fail on its corresponding violation, automatically rather than by manual demonstration.

### Objective 3 - Build-Enforced Architecture Contracts (Priority: P1)

Express two rules as contracts exiting non-zero: exactly one module may **directly** import the model-provider client, and modules on the model-facing path must not reach date, ranking, or probability computation.

**Why this priority**: These are the two rules most likely to be violated silently and most expensive to unwind later.

**Rationale**: The single-import rule needs an allowlist contract checking direct imports only — a contract also following indirect imports is unsatisfiable here, since every module transitively reaching the gateway would be flagged. The computation-boundary rule is the opposite: indirect detection is exactly the point.

**Deliverables**:
- Contract configuration per Python entry, run from that entry's own environment
- An allowlist contract naming the gateway module as the single permitted importer of the provider client
- A boundary contract, with indirect detection enabled, separating computation from model-facing modules
- A source-level scan asserting the provider client is named in exactly one file, across a stated scanned root that excludes test fixtures and contract configuration

**Validation Criteria**:
1. **Given** a clean tree, **When** contracts run, **Then** all pass with exit code zero.
2. **Given** a provider import added to any module outside the gateway, **When** contracts run, **Then** they fail, naming the offending module.
3. **Given** a model-facing module importing the reserved computation package, directly or indirectly, **When** contracts run, **Then** they fail, naming the offending module.
4. **Given** a module that reads the provider client off the gateway rather than importing it, **When** contracts run, **Then** the import contract passes and the source scan reports it.

### Objective 4 - Serving Image Accounted For by Its Lockfile (Priority: P1)

Guarantee structurally, then verify, that the request-serving image contains only what its own lockfile accounts for.

**Why this priority**: This constraint keeps the compute envelope reachable; a violation is invisible until deployment fails.

**Rationale**: Each mechanism covers a different case and none covers all of them. The scoped build context prevents the serving image reaching the local modeling source, but not installing the same distributions from a package index. The allowlist check catches distributions installed but *unaccounted for* by the lockfile; it cannot catch a modeling dependency added to the manifest and legitimately re-locked, because installed and expected would then agree. Only the in-image import check catches that case, and it is a denylist — blind to any modeling distribution not named in its list, which is why the list derives from the modeling boundary's declared dependencies.

**Deliverables**:
- Serving image definition whose build context reaches the serving boundary and the gateway package only
- An allowlist check comparing installed distributions against the lock-derived set
- An in-image check that importing a modeling package fails

**Validation Criteria**:
1. **Given** the serving image definition, **When** its build context is inspected, **Then** it reaches the serving boundary and the gateway package and no other path under `/src`.
2. **Given** the built serving image, **When** the allowlist check runs, **Then** the installed distribution set equals the set derived from the serving lockfile.
3. **Given** a distribution installed in the image that the serving lockfile does not account for, **When** the allowlist check runs, **Then** it fails.
4. **Given** a modeling dependency added to the serving manifest and legitimately re-locked, **When** the image is checked, **Then** the in-image import check is what reports it — the allowlist check passes, by design.
5. **Given** the serving image, **When** a modeling package import is attempted inside it, **Then** the import fails.

### Objective 5 - Local Orchestration with Non-Starting Jobs (Priority: P1)

Provide container orchestration bringing up exactly the persistent services, with one-shot jobs defined but never started implicitly.

**Why this priority**: The no-request-time-sampling rule is enforced by topology; jobs that could start alongside services would erode it.

**Rationale**: Jobs are one-shot by nature. If ordinary startup launched them, the distinction between offline work and request-time work would exist only in documentation.

**Deliverables**:
- Orchestration definition with the persistent services and a database exposing the vector extension
- One-shot job definitions under a non-default profile
- Host port assignments avoiding known conflicts

**Validation Criteria**:
1. **Given** a clean environment, **When** ordinary startup runs, **Then** exactly the persistent services start and no job container runs.
2. **Given** the running stack, **When** a job is invoked explicitly, **Then** it runs to completion and exits without leaving a container behind.
3. **Given** the database service, **When** it becomes healthy, **Then** the vector extension is available.
4. **Given** a machine where the conventional database and API ports are occupied, **When** the stack starts, **Then** it binds its configured ports without conflict.

### Objective 6 - Shared Project and Vendor Roster with Datasheet (Priority: P1)

Declare five projects and twelve vendors once, as a committed machine-readable fixture whose integrity is a reader-computed content hash, with an accompanying datasheet, read by the corpus and procurement-history epics rather than redefined by either.

**Why this priority**: Two later epics must agree on these names; without a single declaration the second one to run has to retrofit the first one's invented names.

**Rationale**: This is the project's first committed synthetic dataset, so the data-provenance rule requiring a datasheet for every synthetic dataset applies. Names follow a documented invented-name convention: synthesized documents sit beside genuine public-domain specifications, and the system's output is claims about vendors delivering late — attaching that to a name resembling a real firm would be both a provenance failure and unfair to a real company.

**Deliverables**:
- A committed roster fixture under the data directory, in a format the offline generators can read without an added dependency
- Stable identifiers for each project and vendor, distinct from display names
- A reader-computed content hash over a canonical serialization, so a consumer records which revision it read and drift is detectable rather than merely recordable. No stored version field — a forgotten bump is the failure this replaces.
- A documented invented-name convention and a real-firm exclusion list
- A datasheet recording population sizes and their source, the naming convention and its rationale, the identifier scheme, the SYNTHETIC declaration, and what is deliberately out of scope

**Validation Criteria**:
1. **Given** the roster fixture, **When** parsed, **Then** it yields exactly five projects and twelve vendors, each with a stable identifier and a display name.
2. **Given** the roster fixture, **When** each name is checked against the documented naming convention and the exclusion list, **Then** every name conforms and none matches an excluded entry.
3. **Given** the roster fixture, **When** read, **Then** the reader returns a content hash over a canonical serialization that a consumer records alongside generated data, and the fixture itself carries no stored version field.
4. **Given** the modeling boundary's roster reader, **When** it reads the roster, **Then** parsing succeeds without adding a dependency beyond that entry's existing manifest, and it returns a content hash over a canonical serialization.
5. **Given** the datasheet, **When** inspected, **Then** it discloses population sizes, naming convention, identifier scheme, synthetic status, and out-of-scope items.

### Objective 7 - Dispatchable Verification Workflow (Priority: P2)

Provide one workflow running every check — lint, format, type check, tests, coverage, contracts, and the image checks — triggered manually.

**Why this priority**: The checks in Objectives 2, 3, and 4 are the substance and are runnable without it; this collects them in one place and, via `on: push`, turns them into the enforcement mechanism rather than a convenience. It remains P2 because the checks are individually runnable without it — the workflow makes them unavoidable, not possible.

**Rationale**: A single entry point makes the full check set reproducible for anyone, and establishes the shape the automatic triggers will adopt when E002 adds them.

**Deliverables**:
- A manually dispatched workflow invoking every check
- Per-check reporting so a failure names which contract or assertion failed

**Validation Criteria**:
1. **Given** a clean tree, **When** the workflow is dispatched, **Then** every check runs and the workflow succeeds.
2. **Given** a tree with any single contract violated, **When** the workflow is dispatched, **Then** it fails and the output names the violated check.

### Technical Constraints

- All project source resides under `/src`, in four entries: `/src/web`, `/src/api`, `/src/model`, `/src/gateway`.
- The three Python entries are independent projects with separate lockfiles — not workspace members.
- Neither Python boundary may declare the other as a dependency; both may depend on the gateway package.
- The gateway package carries the provider client and validation only — no web framework, no modeling stack.
- The web boundary contains exactly one JavaScript lockfile and uses npm.
- Host ports: database on 5434, API on 8001, web on 3000. The conventional database and API ports are occupied on the target machine.
- The serving image build context reaches the serving boundary and the gateway only.
- The roster is declared once and read; no consumer redefines projects or vendors.
- Contracts run from each Python entry's own environment, since a single combined configuration would require every stack installed together — defeating the isolation being proved.
- Tests live alongside the code they cover, within their entry. Enforcement scripts live within the entry they verify, or are classified as build tooling outside `/src`.

## Integration Points

- **IP-001**: E002 depends on the roster fixture for the synthesized project-document layer, and records the `roster_hash` the reader returned alongside every artifact it generates (TR-027); its public-domain sourcing half has no dependency on this epic.
- **IP-002**: E002 additionally owns adding automatic workflow triggers, which this epic defers.
- **IP-003**: E005 depends on the roster fixture for generating procurement history, consuming the same identifiers E002 used, and records the `roster_hash` — the reader-computed content hash — it read (TR-027).
- **IP-004**: E003 depends on the entry layout and orchestration to add schema migrations against the database service.
- **IP-005**: E004 implements the gateway module inside `/src/gateway`, which the allowlist contract designates as the sole permitted importer of the provider client. E004 therefore also owns provider credential supply and the redaction of credential material inside the traced invocation path — redaction is a property of that boundary rather than of each caller — neither of which exists in this epic, which ships no invocation path (TR-025).
- **IP-006**: E006 and E011 both reach the provider through the gateway package — the offline pipeline and the request-serving boundary consume it without depending on each other.
- **IP-007**: All later epics extend and invoke the contract harness; it gates only when invoked, and automatic triggering is E002's to add.
- **IP-008**: The database service depends on a published image providing the vector extension, pulled on first start.

## Requirements

### Technical Requirements

- **TR-001**: The repository MUST contain exactly four entries under `/src` — three source boundaries and one shared gateway package — each with its own dependency manifest.
- **TR-002**: The three Python entries MUST resolve dependencies independently, each as a standalone `uv` project with its own `pyproject.toml` and `uv.lock` — not as members of a shared workspace, whose single resolution cannot keep one member from reaching another's dependencies. Each Python boundary MUST NOT declare the other as a dependency, and both MUST declare the gateway package as a path dependency.
- **TR-003**: The gateway package's resolved dependency set MUST NOT contain any distribution from the modeling stack or any web framework.
- **TR-004**: The serving boundary's resolved dependency set MUST NOT contain any third-party distribution that the modeling boundary declares as a direct dependency. First-party path dependencies are excluded from the comparison by derivation rule, since both boundaries declare the gateway package and its presence in the serving set is required rather than a violation. The comparison set is the directly-declared subset of the Glossary's *modeling stack*; the distributions that term also admits — those reachable only transitively through those declarations — are deliberately outside it, because a shared transitive dependency is legitimate (OBJ1 VC2) and a modeling distribution arriving under an unrelated name is TR-012's case rather than this one. The comparison is scoped to the Python entries: the web boundary resolves from a different ecosystem and shares no distribution namespace with them, so no dependency-isolation obligation under this requirement reaches it.
- **TR-005**: Every entry MUST provide lint, format, and test-running configuration, and the TypeScript boundary MUST provide type checking, each failing on violation.
- **TR-006**: Coverage MUST be measured as a single aggregated report across this epic's executable artifacts — the source scan, the image checks, and the roster reader — and MUST meet the project coverage target on that combined denominator. Per-entry runners MAY report coverage but MUST NOT gate independently, since two entries have an empty denominator. The denominator holds those three artifacts' implementation modules only; the test modules that exercise them and their fixture code sit outside it, so the reported figure cannot be inflated by tests measuring themselves.
- **TR-007**: The system MUST provide committed fixture-based tests proving each contract fails on its corresponding violation. The contracts and checks requiring a negative case are the five enforcement mechanisms this epic ships — the allowlist contract (TR-008), the computation-boundary contract (TR-009), the source-level scan (TR-010), the allowlist check (TR-012), and the in-image check (TR-013) — each with its own case, since no single violation exercises two of them, and each corresponding to a stated validation criterion in OBJ3 VC2–VC4 and OBJ4 VC3–VC4. Fixtures MUST live outside every production contract's scanned root and be exercised through a fixture-specific contract configuration asserting a non-zero exit, so that no production configuration carries an exclusion that could drift. The two image-operating checks (TR-012, TR-013) take a different fixture form, because an import-linter-shaped fixture cannot express an image violation: their negative cases MUST inject the violation at runtime into a container started from the real serving image — creating a stub distribution, a top-level module with matching distribution metadata, inside the running container — and then run the check there. This exercises the real check end to end without a second image build and without installing a multi-hundred-megabyte modeling distribution to prove a denylist fires. The computation-boundary contract's negative case MUST exercise an indirect import path — a model-facing module reaching the computation package through an intermediate module — as well as a direct one, since indirect detection is that contract's distinguishing property. Each negative case MUST be paired with a positive control establishing that the same mechanism passes when the violation is absent — the clean-tree runs of OBJ3 VC1 and OBJ4 VC2 for the source-level mechanisms, and for the in-image check an assertion that a distribution the serving image is required to carry does import — so a failure caused by the environment rather than by the rule under test is distinguishable.
- **TR-008**: The system MUST provide an allowlist contract — an import rule naming the modules permitted to import a protected package and checking direct imports only — that fails when any module outside the gateway module directly imports the model-provider client. The protected package is the `anthropic` distribution and the top-level module of the same name, named here rather than referred to generically so it is identifiable without reading a manifest. The claim is an import-edge claim: no network-level restriction on reaching the provider exists or is required by this epic, so a module could reach the provider over HTTP without importing the client, and no mechanism this epic ships would see it. The contract is also Python-only. `/src/web` is covered by TR-010's source scan but by no import contract, so a TypeScript provider client would be named-and-caught rather than structurally prevented. That is a deliberate scope decision for this epic, not an oversight: the web boundary has no reason to reach a provider until E010 or E011 gives it one, and whichever epic does MUST revisit whether a web-side restricted-import rule is warranted.
- **TR-009**: The system MUST reserve a computation package and a model-facing package in each Python boundary — the serving and modeling boundaries, not the gateway package, which carries the provider client and its validation only — and MUST provide a contract with indirect imports detected that fails when the model-facing package reaches the computation package. The packages are empty in this epic; later epics MUST place date, ranking, and probability logic in the computation package and provider-adjacent logic in the model-facing package, which is what makes the contract non-vacuous.
- **TR-010**: The system MUST provide a source-level check asserting the provider client is named in exactly one file. The scanned root is all four entries under `/src`, and the scanned **file set within it is source files only** — `.py`, `.ts`, `.tsx`, `.js`, `.jsx` — explicitly excluding dependency manifests, lockfiles, installed-package directories, each Python entry's contract configuration, and any path under the fixture root. The file set is stated because the directory root alone does not survive contact with a clean tree: the gateway's manifest declares the provider client by name, all three `uv.lock` files record it (transitively, through the path dependency TR-002 requires), and the check would report more than one file before a single line of source existed — contradicting OBJ3 VC1's clean-tree pass. A match is a case-sensitive occurrence of the distribution's import name as a whole word, in any context including comments and strings; the check is deliberately blind to neither, because a name reached by string construction is exactly the evasion TR-008's contract already cannot see. The root deliberately includes `/src/web` even though TR-008's contract is Python-only, so that a JavaScript provider client appearing there would trip this check rather than pass unseen. The repository root is not scannable — the specification, plan, and research documents all name the provider client in prose.
- **TR-011**: The serving image build context MUST reach the serving boundary and the gateway package only. The scoping constrains local source reachability only: it does not prevent the image installing the same distributions from a package index, which is why TR-012 and TR-013 exist alongside it. The data directory is outside the context and therefore unreachable from the serving image, which is what TR-016's single-reader placement rests on. The obligation is scoped to the serving image, the only image this epic builds: the database image is pulled rather than built (TR-015, IP-008), and no reciprocal obligation constrains what a modeling job container may reach, since the rule exists to keep the serving compute envelope reachable rather than to isolate the offline side.
- **TR-012**: The system MUST provide an allowlist check asserting that the serving image's installed distribution set equals the set derived from the serving lockfile. The expected set MUST be derived from that lockfile for the dependency groups the image installs — the serving boundary's default group only, with development-only groups outside both sides of the comparison, since the image runs the service while every check that exercises it runs from outside it — resolved for the image's target platform with environment markers evaluated for that platform rather than for the host running the check, and both sides MUST be compared under PEP 503 normalized distribution names, so two independent implementations derive the same set. The check reads distribution metadata, so source copied or vendored into the image without metadata is invisible to it — an absent finding is not evidence that a package is absent.
- **TR-013**: The system MUST provide an in-image check that importing a modeling package fails, with the checked names derived from the modeling boundary's declared dependencies minus its first-party path dependencies. The exclusion is a derivation rule, not a hand-maintained name list — without it the derived set would include the gateway package, which the serving image is required to contain. Declared dependencies are distribution names and the check attempts imports, which are module names, so the module names attempted MUST be derived from the modeling boundary's installed distribution metadata — the top-level modules each declared distribution provides — rather than assumed equal to the distribution names or hand-mapped. The derivation MUST run on the host, against the modeling boundary's synced environment, and the resulting module list MUST be passed into the container — it cannot run inside the serving image, because TR-011 keeps the modeling boundary out of that image's build context and its metadata is therefore unreachable from within. The derived list MUST be asserted non-empty before the import attempts run: a check host without the modeling environment synced would otherwise derive nothing, attempt no imports, and pass vacuously. TR-007's positive control does not catch that case — it proves the import mechanism works, not that the list it was given is non-empty. The check is a denylist: it is blind to any modeling distribution outside its derived list, and to any module a distribution exposes that its metadata does not record.
- **TR-014**: Ordinary orchestration startup MUST start only persistent services; one-shot jobs MUST require explicit invocation.
- **TR-015**: The database service MUST provide the vector extension and bind a host port that does not collide with a conventional default. Its credentials MUST be literal development-only values committed in the orchestration definition, declared non-secret on the grounds that the service binds locally and is never publicly exposed. This is the one credential this epic consumes, and committing it is what lets a fresh clone start with no setup step; TR-025's prohibition is scoped to provider credentials and is not weakened by it.
- **TR-016**: The system MUST provide a committed machine-readable roster declaring five projects and twelve vendors with stable identifiers, readable without an added dependency by the offline generators that consume it. The roster MUST be committed at `data/roster/project-vendor-roster.json`, serialized as JSON, and encoded as UTF-8 without a byte-order mark — path, format, and encoding fixed here rather than left to whichever file happens to land, since the reader's parse depends on them. Encoding gates the parse and not the digest: TR-027's hash covers a canonical re-serialization of parsed content, which is by construction independent of the source file's byte layout, so a byte-order mark is a read failure rather than a different hash. A single roster reader MUST live in the modeling boundary; the serving boundary never reads the roster, because TR-011 keeps the data directory out of its build context.
- **TR-027** *(added during clarification to refine TR-016; kept adjacent to it for readability, numbered at the end of the sequence because requirement identifiers carry no suffix form)*: The roster reader MUST compute a content hash over a canonical serialization of the roster, and consumers MUST record that hash alongside data they generate from it, so drift is mechanically detectable rather than dependent on a hand-maintained marker. The canonical serialization MUST be defined by a committed rule fixing member ordering, separators, string and number form, encoding, and the field set inside the hashed scope, so that two independent implementations derive the same digest from the same roster; without it a change to the reader's serialization moves the hash while the roster is unchanged, and this requirement's own drift response — mandatory regeneration, not a warning — would fire on a false positive. Validation means the roster parses, carries exactly the declared populations with unique identifiers and the required fields on each entry, and carries no unknown top-level key; a roster failing any of these MUST cause the reader to exit non-zero rather than return a hash over invalid content. The reader MUST return the validated roster — its five projects and twelve vendors, each carrying its stable identifier and display name — together with that hash as a string of the form `sha256:` followed by 64 lowercase hexadecimal characters, so both consuming epics integrate against one interface rather than two. E002 and E005 are the recording consumers (IP-001, IP-003) and MUST record the value under the field name `roster_hash`, with that same type and format, alongside every artifact they generate; where that field is persisted is E003's schema decision. Drift is detected by comparing an artifact's recorded `roster_hash` against the hash the reader returns for the current fixture, and on a mismatch every artifact carrying the earlier value is stale by definition and MUST be regenerated and re-recorded, or the roster change reverted — the response is regeneration, not a warning and not per-epic discretion. This epic ships detection only: there is no reconciliation mechanism and no automatic correction.
- **TR-017**: Roster names MUST conform to a documented invented-name convention committed alongside the fixture and expressed as a rule a check can apply, and MUST NOT match any entry in a committed real-firm exclusion list. The check MUST be executable rather than applied by review, exiting non-zero and naming the offending entry — an obligation stated explicitly because "expressible as a rule a check can apply" would otherwise be satisfied by a convention no check ever applies. It is therefore one of TR-019's enumerated mechanisms, appears in TR-020's dispatched run, and its implementation falls inside TR-006's coverage denominator alongside the source scan, the image checks, and the roster reader.
- **TR-018**: The roster MUST ship a datasheet disclosing population sizes and their source, the naming convention and rationale, the identifier scheme, synthetic status, and deliberately out-of-scope content.
- **TR-019**: Every enforcement mechanism this epic ships MUST exit non-zero on violation, naming the specific rule that failed and, where the violation is attributable to a source location, the offending module or file. The subjects are enumerated rather than left to the reader, because the spec's vocabulary uses *contract*, *check*, *scan*, and *assertion* interchangeably and each admits a different subject set: the allowlist contract (TR-008), the computation-boundary contract (TR-009), the source-level scan (TR-010), the image allowlist check (TR-012), the image denylist check (TR-013), the naming and exclusion-list check (TR-017), and the index-configuration, digest-pinning, and credential checks (SC-016, SC-017, SC-018). The per-entry runners of TR-005 already fail non-zero by their own tooling's contract and are not restated here.
- **TR-020**: The system MUST provide a workflow invoking every check and reporting per-check results, triggered both automatically on push and by manual dispatch — the push trigger so a contract violation fails the build rather than raising a review comment, and the dispatch trigger so TR-022's violation-injection input can be exercised on demand. No check it invokes requires credential access — each is a static analysis, a test over committed fixtures and pure functions, or a local image build and run against public images — so the dispatched run requires no repository secret, and its secret surface is knowable from this requirement rather than only from the workflow file. A later epic adding a check that does require one MUST state that check's secret surface.
- **TR-021**: The workflow file MUST be landed on the default branch before this epic closes, so it becomes dispatchable and the clean run required by SC-013 can be evidenced against the feature branch rather than deferred past completion.
- **TR-022**: The system MUST provide an evidence path for the violated dispatch that does not require committing a violation to a production contract root — either a throwaway branch pushed solely to capture the failing run and then deleted, or a workflow input that injects a violation into a scratch copy of a production root. Without this, TR-007's design makes every committed violation a passing test, so no dispatched run could ever fail.
- **TR-023**: Every entry MUST commit a lockfile for its dependency manifest, and lock verification MUST be runnable per entry — each verification checking that entry's lockfile against its own manifest and consulting no other entry's: the three Python entries against their `uv.lock` files, the web boundary against its single JavaScript lockfile. Each committed lockfile is that entry's enumerated third-party dependency inventory; an asserted equality between such an inventory and what is actually installed is required only for the serving image (TR-012), whose contents are the property this epic constrains. Reliance on the lockfiles is scoped to version pinning, expected-set derivation, and the artifact hashes the locked install verifies as it installs — no separate digest-verification check is required here.
- **TR-024**: Every entry MUST resolve its third-party dependencies from the default public index for its ecosystem — PyPI for the three Python entries, the public npm registry for the web boundary — with no alternate, supplemental, or private index configured, so the index each entry resolves from is a stated property rather than whichever default a toolchain happens to carry. First-party packages MUST resolve from their local path (TR-002): the gateway package is declared as a path dependency by both Python boundaries and is never resolved by name, so no distribution published to a public index under the same name can substitute for it.
- **TR-025**: Model-provider credentials MUST NOT be committed to the repository, and credential material MUST NOT enter the serving image's build context or any of its resulting layers. This epic supplies no provider credential at build time — none of the checks, contracts, or image builds it ships consumes one — so the obligation is a stated boundary rather than a handling procedure. Provider credential supply, and the redaction of credential material inside the traced invocation path, arrive with the gateway module E004 implements (IP-005).
- **TR-026**: Every externally pulled image this epic names MUST be pinned by digest alongside its name and tag — the serving image's base image and the database service image (TR-015, IP-008) — so the pulled artifact is fixed rather than following a moving tag, and environment drift cannot silently change what TR-012's lock-derived comparison is comparing against.

### Key Entities

- **ProjectVendorRoster**: The single declaration of the synthetic population. Contains exactly five projects and twelve vendors, with integrity carried by a reader-computed content hash rather than a stored version field; read by E002 and E005, redefined by neither.
- **Project**: A construction project in the synthetic population. Stable identifier plus display name; the unit that later groups procurement lines and documents.
- **Vendor**: A supplier in the synthetic population. Stable identifier plus display name; later carries the forecast model's vendor-level grouping.
- **RosterDatasheet**: The disclosure accompanying the roster — population sizes and their source, naming convention, identifier scheme, synthetic status, out-of-scope content.

## Assumptions & Risks

### Assumptions

- Push triggering ships in this epic, so the checks gate automatically from the first commit carrying the workflow. Pull-request triggering and branch protection remain with E002; `on: push` alone means a violation is reported after the push rather than blocked before the merge.
- The target machine already provides the required runtimes; this epic configures, it does not install them.
- Twelve vendors and five projects is the right population size, taken from the product document; the roster's shape is fixed here, its lifecycle values are not.
- A published image supplying the vector extension is available and pullable.
- The repository has no commits yet. This epic establishes the first commit and a hosted remote with a default branch, both of which the dispatchable workflow requires; until they exist, no history-dependent tooling can be assumed to work.

### Risks

- **Deferred triggers leave the enforcement epic itself unenforced** *(likelihood: high, impact: high)*: This epic builds the machinery that makes violations fail, then ships without anything invoking it automatically. Contract violations introduced during E001 or E002 accumulate until E002 adds the triggers. Mitigation: E002 is the named owner and the deferral window is one epic; the gap is recorded here rather than discovered later.
- **Contracts appear stronger than they are** *(likelihood: high, impact: medium)*: Static import analysis cannot see dynamic imports or a client read off the gateway module. Mitigation: the source scan covers the second case, and success criteria are worded as direct-import claims rather than absolute ones.
- **First orchestration start pulls a large image** *(likelihood: high, impact: low)*: The vector-extension image is not cached locally, so the first start is a several-hundred-megabyte download and the first point a platform problem would surface. Mitigation: expect it, and treat a pull failure as an epic-blocking finding rather than a transient error.

## Implementation Signals

- `NEW-CONFIG` — Per-entry dependency manifests and lockfiles, lint/format/type-check/test/coverage configuration, contract configuration per Python entry, orchestration definition with a non-default job profile, and a manually dispatched workflow.
- `NEW-ENTITY` — The project and vendor roster fixture and its datasheet, the first committed data artifacts and the shared input for two later epics.
- `EXTERNAL-SERVICE` — A database image providing the vector extension, pulled from a public registry on first start.
- `BREAKING-CHANGE` — The layout gains a fourth entry under `/src`. Every rule and count asserting exactly three boundaries has been restated, including the project instructions' Source Code Layout section at v1.1.0; the signal remains so the plan phase treats the entry count as load-bearing rather than incidental.

## Success Criteria

### Measurable Outcomes

- **SC-001** [OBJ1]: Each of the four entries verifies against its own lockfile with no reference to another's — the three Python entries against their `uv.lock` files and the web boundary against its single JavaScript lockfile — and no third-party distribution the modeling boundary declares as a direct dependency appears in the serving boundary's resolved set.
- **SC-002** [OBJ1]: `/src` holds exactly four entries; neither Python boundary declares the other as a dependency, both declare the gateway package, and the gateway's resolved set contains no modeling-stack distribution and no web framework.
- **SC-003** [OBJ2]: Lint and format checks pass on a clean tree for every entry, type checking passes for the TypeScript boundary, and coverage of this epic's executable artifacts — the source scan, the image checks, and the roster reader — meets the project target.
- **SC-004** [OBJ2]: Each contract has a committed negative fixture proving it fails on violation, run automatically rather than demonstrated manually.
- **SC-005** [OBJ3]: A provider import introduced anywhere outside the gateway module causes a non-zero exit naming that module; the claim covers direct imports, with dynamic imports stated as out of scope.
- **SC-006** [OBJ3]: A model-facing module that imports the reserved computation package, directly or indirectly, causes a non-zero exit naming that module. Computation written inline rather than imported produces no edge and is out of scope for this mechanism.
- **SC-007** [OBJ4]: The serving image's installed distribution set equals the set derived from its lockfile, and a distribution the lockfile does not account for causes a failure. A modeling dependency added to the serving manifest and legitimately re-locked is outside this criterion — installed and expected then agree — and is covered by SC-008 instead.
- **SC-008** [OBJ4]: Attempting a modeling-package import inside the serving image fails.
- **SC-009** [OBJ5]: Ordinary startup brings up exactly the persistent services and zero job containers; an explicitly invoked job runs to completion and leaves no container behind.
- **SC-010** [OBJ5]: The database reports healthy with the vector extension available, on a host port that does not collide with a conventional default.
- **SC-011** [OBJ6]: The roster parses to exactly five projects and twelve vendors with stable identifiers, is readable by the offline generators without an added dependency, exposes a reader-computed content hash consumers can record, and every name conforms to the documented convention with none matching the exclusion list.
- **SC-012** [OBJ6]: A datasheet accompanies the roster disclosing population sizes and source, naming convention, identifier scheme, synthetic status, and out-of-scope content.
- **SC-013** [OBJ7]: With the workflow landed on the default branch, a dispatched run against the feature branch on a clean tree passes every check; and a dispatched run using the TR-022 evidence path, with any single contract violated, fails and names the violated check. The violated run is dispatched against the feature branch under TR-022's workflow-input path, or against the throwaway branch under TR-022's pushed-branch path.
- **SC-014** [OBJ3]: A module that reads the provider client off the gateway rather than importing it leaves the import contract passing, while the source-level scan reports the provider client named in more than one file within its scanned root, exiting non-zero and naming the offending file.
- **SC-015** [OBJ4]: The serving image definition's build context reaches the serving boundary and the gateway package and no other path under `/src`, observable from the committed image definition; the scoping evidences local source unreachability only and is not evidence that the image carries no modeling distribution.
- **SC-016** [OBJ1]: Every entry's index configuration resolves third-party dependencies from its ecosystem's default public index, verified by inspecting a stated artifact set — each Python entry's `pyproject.toml` and any `uv.toml`, and the web boundary's `.npmrc` and `package.json` — and exiting non-zero naming the file when an alternate, supplemental, or private index is configured. Absence of the configuration counts as compliance; the check asserts nothing about the index a developer's ambient environment might supply.
- **SC-017** [OBJ4]: Every externally pulled image the epic names carries a digest alongside its name and tag, verified by scanning the committed image definition and the orchestration definition and exiting non-zero naming any image reference that carries no digest.
- **SC-018** [OBJ4]: No credential material appears in the serving image's build context or in any layer of the built image, verified against the committed definition and the built image rather than asserted; this epic supplies no provider credential, so the criterion passes vacuously and exists to fail the moment one is introduced.

## Clarifications

### Session 2026-07-25 (checklist evaluation)

- Q: What is the source scan's scanned root, and does VR-013 use the same one? -> A: All four entries under `/src`, excluding each Python entry's contract configuration and the fixture root; VR-013 mirrors it excluding `/tests` and `/data`. Raised independently by all three checklists (TR-010, VR-013).
- Q: How does a violation reach a built image for the image-check negative cases, given an import-linter fixture cannot express one? -> A: Runtime injection of a stub distribution into a container started from the real image, then run the check there — no second build, no large modeling install (TR-007).
- Q: Does the single-import-site claim reach `/src/web`? -> A: Scoped to Python for now, with the gap disclosed rather than half-enforced. `/src/web` is covered by the source scan but by no import contract; E010 or E011 — whichever first gives the web boundary a reason to reach a provider — must revisit a web-side restricted-import rule (TR-008, TR-010).
- Q: How is the local development database credential supplied? -> A: Literal development-only values committed in the orchestration definition, declared non-secret since the service binds locally and is never publicly exposed. TR-025's prohibition stays scoped to provider credentials (TR-015).

### Session 2026-07-25

- Q: Which Python packaging toolchain backs the per-entry lockfiles and the lock-derived expected set? -> A: `uv`, as three standalone projects rather than workspace members — already researched and chosen, but recorded in no requirement until now (TR-002).
- Q: TR-009's computation-boundary contract has no operands, and ADR-0008 and Principle V state the rule as converses. What does the contract name? -> A: Reserve a computation package and a model-facing package per Python entry; forbid model-facing reaching computation, indirect detection on. Later epics populate them (TR-009).
- Q: Where do the committed negative fixtures live, given a committed violating module would also trip the production contracts? -> A: Outside every production contract's scanned root, exercised by a fixture-specific configuration asserting non-zero exit — no production exclusion to drift (TR-007).
- Q: Where does the roster reader live, given the gateway is provider-only, the boundaries cannot depend on each other, and the serving image excludes the data directory? -> A: One reader in the modeling boundary; the claim narrows from every Python entry to the offline generators that actually consume it (TR-016).
- Q: Is the roster's integrity a version marker or a content hash? -> A: A reader-computed content hash over a canonical serialization, making drift mechanically detectable rather than dependent on someone remembering to bump a field (TR-027).
- Q: How is the 80% coverage target evaluated when two entries have an empty denominator? -> A: One aggregated report across the three executable artifacts; per-entry runners report but do not gate (TR-006).
- Q: How is SC-013 evidenced, given dispatch requires the workflow on the default branch and the repo has no commits? -> A: Land the workflow on the default branch before the epic closes, then dispatch against the feature branch for both runs (TR-021).

## Stress-Test Findings

### Session 2026-07-25

- STF-001: Cross-Requirement Contradiction (CRITICAL) — Affected: TR-013, TR-002, TR-011, SC-008, OBJ4 — Gateway is a declared modeling dependency, so TR-013's derived denylist forbids the very import the serving image needs. **Resolved** — TR-013 now excludes first-party path dependencies by derivation rule.
- STF-002: Cross-Requirement Contradiction (CRITICAL) — Affected: TR-004, TR-002, SC-001, OBJ1 — OBJ1 VC2 and VC4 contradicted each other: the gateway must be declared by both boundaries yet must not appear in the serving resolved set. **Resolved** — TR-004, SC-001, and OBJ1 VC2 now compare third-party declarations only.
- STF-003: Constraint Impossibility (HIGH) — Affected: TR-009, SC-006, SC-004, OBJ3, TR-007 — SC-006 claimed computation detection, but TR-009's mechanism only sees imports of a package empty in this epic. **Resolved** — SC-006 and OBJ3 VC3 narrowed to import edges, and the inline-computation blind spot added to Edge Cases.
- STF-004: Constraint Impossibility (HIGH) — Affected: TR-021, SC-013, TR-007, TR-020, OBJ7 — SC-013's violated dispatch could not be evidenced, because TR-007 makes every committed violation a passing test, and no assumption established a remote or default branch. **Resolved** — TR-022 adds an evidence path that avoids committing to a production root, and the assumption now names the first commit and hosted remote.
- STF-005: Cross-Requirement Contradiction (HIGH) — Affected: OBJ6, TR-016, TR-011, TR-003, SC-011 — OBJ6's deliverable and VC4 still demanded every Python entry read the roster after TR-016 narrowed it to one reader. **Resolved** — both narrowed to the offline generators.

## Compliance Check

Audited against `project-instructions.md` v1.0.0; the amendment to v1.1.0 that resolved the layout conflict came after this audit and is recorded at the end of this section. Re-baselined to **v1.1.1** during the analyze phase — v1.1.1 changed only the QC-category wording and introduced no new obligation on this spec. Initial audit returned FAIL with two CRITICAL and three HIGH findings; all but one are remediated in this revision. The analyze phase then raised one further open item, recorded in the last row.

| Finding | Status |
|---|---|
| Roster is a synthetic dataset and requires a datasheet | Resolved — OBJ6 deliverable, TR-018, SC-012 |
| No objective delivered the lint, type-check, test, or coverage tooling OBJ7 invokes | Resolved — OBJ2 added |
| Coverage target had no denominator for a configuration-heavy epic | Resolved — TR-006 scopes coverage to this epic's executable artifacts |
| Both Python boundaries need provider access, but one importing module was permitted repository-wide and neither boundary may depend on the other | Resolved — ADR-0010 supersedes ADR-0001, adding a shared gateway package |
| Roster had no integrity marker, unlike every other shared artifact | Resolved — reader-computed content hash in OBJ6, TR-027 (a hand-maintained version field was considered and rejected during clarification) |
| Test and enforcement-script placement unstated in the epic that establishes layout | Resolved — Technical Constraints |
| Spec built ADR-0007's enforcement half without carrying its trace tag | Resolved — `epic_sources` now carries ADR-0007 and ADR-0010 |
| Dispatch-only workflow deviates from the CI requirement that contract violations fail the build | Resolved — the workflow now carries `on: push` alongside `workflow_dispatch`, so contract violations gate for real. The deferral to E002 was a scope choice, not a technical blocker; the analyze phase surfaced it and the one-line trigger was added rather than carried |
| Check harness placed outside `/src`, against ENFORCE_SRC_ROOT's "tests live alongside the code they cover within each entry" | Resolved — `project-instructions.md` v1.1.2 adds a scoped exception for cross-entry verification with no single owning entry. Raised by the analyze phase and fixed at the governance layer rather than asserted at the plan layer |

**Propagation resolved**: ADR-0010's four-entry layout has been propagated. `project-instructions.md` Source Code Layout now mandates four entries at v1.1.0, and the `specs/sad.md` and `specs/project-plan.md` narratives match. This spec is no longer in conflict with any registered document, and nothing blocks Plan on that account.

## Glossary

| Term | Definition |
|------|------------|
| Entry | One of the four top-level areas under `/src`, each owning its own dependency manifest — the three source boundaries plus the shared gateway package |
| Boundary | One of the three source areas that hold application code: web, api, model |
| Gateway package | The minimal shared entry holding the model-provider client and its validation and tracing wrapper, depended on by both Python boundaries |
| Computation package | The reserved package in each Python boundary where date, ranking, and probability logic must live. Empty in this epic; later epics populate it, which is what gives the boundary contract something to enforce. |
| Model-facing package | The reserved package in each Python boundary for provider-adjacent logic. The boundary contract forbids it reaching the computation package, with indirect imports detected. |
| Modeling stack | The **third-party** distributions the modeling boundary declares as direct dependencies — the probabilistic modeling library, its tensor backend, its diagnostics library, and the numerical libraries it names — together with any distribution reachable only through them. Membership is read from the modeling boundary's manifest, never hand-listed. First-party path dependencies and anything reachable only through them are excluded by the same derivation rule TR-013 states: without that exclusion the gateway package, which TR-002 requires the modeling boundary to declare, would fall inside this term, and TR-003 would then forbid the gateway's resolved set from containing the provider client TR-008 and OBJ1 require it to carry. |
| Web framework | Any distribution the request-serving boundary declares to serve HTTP, together with its server, excluding first-party path dependencies as above. Membership is read from the request-serving boundary's manifest rather than hand-listed, but unlike the modeling stack this term needs one judgement the manifest cannot supply — which declared distributions serve HTTP, as opposed to being libraries the serving boundary happens to share with the gateway. That judgement MUST be recorded alongside the check rather than re-made per reviewer. |
| Contract | A build-time rule exiting non-zero when violated, as opposed to a convention checked by review |
| Allowlist contract | An import rule naming the modules permitted to import a protected package, checking direct imports only |
| Allowlist check | An image check comparing installed distributions against a lock-derived expected set, rather than screening for known-bad names |
| Job | A one-shot container that runs to completion and exits, never started by ordinary orchestration startup |
| Roster | The single committed declaration of the five synthetic projects and twelve synthetic vendors |
| Re-export laundering | Obtaining a protected client by reading it off a permitted module rather than importing it, producing no direct import edge |
| Dispatch-only | A workflow that runs when manually triggered and therefore gates nothing automatically |
