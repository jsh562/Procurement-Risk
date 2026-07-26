<!-- template-version: 2 -->
# Procurement Risk Copilot Project Instructions

## Core Principles

### I. Traceable or It Does Not Ship

Every extracted value MUST carry a page citation and a per-field confidence, and every published figure MUST resolve to the artifact it was computed from — an unattributable number is a defect, not a rough edge. Provenance MUST be derived deterministically from parsing and enforced at the storage boundary, never asserted by a language model. — The product's entire claim is that a coordinator can check the system's reasoning; a number nobody can trace back is worth less than no number at all, because it invites trust it has not earned.

### II. Uncertainty Is the Product

The system MUST communicate distributions and intervals, never a bare point estimate. Collapsing a forecast to a single date anywhere in the interface is a regression, and every reported metric MUST be published together with its interval. — Replacing the submittal log's single optimistic integer with an honest distribution is the reason this product exists; a lone number reintroduces exactly the false confidence it was built to remove.

### III. Precision Over Recall Where a Mistake Is Silent

Where an incorrect result would be invisible — cross-document identity merges above all — the system MUST bias toward refusal. Uncertain pairs MUST be withheld and routed to review rather than merged, and a value failing validation MUST be recorded as absent rather than stored wrong. — A wrong merge corrupts the record silently and propagates; a missing link is visible and recoverable. Optimize for the failure a human can see.

### IV. Agent Output Style

All agent output MUST be concise and outcome-oriented. This principle supersedes any verbose defaults.

- **Progress reports**: Facts and outcomes only — no narration, no restating the task.
- **Artifacts**: Emit required sections only — no preamble paragraphs, no summary epilogues.
- **Reasoning**: Omit unless the user asks "why" or the decision is non-obvious.
- **Errors / blockers**: State the problem, the attempted fix, and the result — nothing else.
- **Phase-boundary reports**: ≤ 5 bullet points.
- **Preserve without compressing**: Artifact template structure and required sections; explicit decision / registration / validation guidance in shared skills; delegation constraints and sub-agent role definitions; existing size limits (spec ≤ 1000 KB, research ≤ 400 KB, stories ≤ 200 words).

### V. The Model Extracts, Code Computes

Language models MUST only identify and structure information. All date arithmetic, ranking, and probability computation MUST happen in deterministic, testable code, enforced by an architecture test that fails the build when computation appears in a model-facing module. Exactly one module may reach the model provider, enforced by an import contract. — Model output is not reproducible and cannot be unit-tested; anything that must be defensible has to be computed somewhere that can be.

### VI. Evaluate Before You Tune

Evaluation sets MUST be frozen, hashed, and committed before any tuning run touches them, and the evaluation harness MUST verify the hash and abort on mismatch. — Small evaluation sets invite tuning to the test set. Making the freeze an exit code rather than a promise is the only version of this rule that survives schedule pressure.

### VII. Publish the Miss

A target that is not met MUST be published with its cause. Targets MUST NOT be retroactively adjusted to match results, and a limitation MUST be recorded as scope decision, supporting evidence, reversal trigger, and production-scale alternative. — A project that only ever reports its wins tells a reader nothing about whether the measurements were honest. Disclosed shortfalls are what make the successes credible.

### VIII. Honest Opponents

Every model claim MUST be reported against a baseline strong enough that beating it means something, and where a weak baseline is also reported it MUST be labeled as such. — Beating a deliberately poor comparison is a rhetorical move, not a result. A baseline that could plausibly win is the only one whose defeat carries information.

## Technology Stack

<!-- Downstream phases (Plan, QC, Autopilot) read this section as the authoritative tech-stack reference. -->

- **Language/Runtime**: TypeScript 5.x on Node 22 (web); Python 3.12 (api, model, gateway)
- **Frameworks**: Next.js 15 (App Router) and React; FastAPI with Pydantic; PyMC, ArviZ, pandas, NumPy; ONNX Runtime for INT8 CPU inference; Anthropic SDK targeting `claude-opus-5`
- **Storage**: PostgreSQL 16 with `pgvector` and native `tsvector` — a single instance holding document chunks, procurement records, resolved entities, posterior artifacts, and model-invocation records. No second datastore.
- **Infrastructure**: Docker Compose for local development, with one-shot jobs under a non-default profile; Vercel plus a container host with managed Postgres for the hosted demonstration

## Testing & Quality Policy

<!-- QC extracts enforcement rules from this section by keyword. The canonical keyword list lives in -->
<!-- .github/skills/instructions-management/SKILL.md — it is referenced rather than reproduced here, because -->
<!-- restating it inside a scanned section activates every category it names. The authoritative machine-read -->
<!-- source is `## Derived QC Policy` in .github/sddp-config.md, written by init and never by QC. -->

- **Coverage Target**: 80%
- **Required QC Categories**: linting (the category covering lint, static analysis, and code quality), coverage. Two categories, not three — quality control resolves lint and static analysis to the same one.
- **Test Strategy**: Test-after for interface, ingestion, and integration work. Strict test-first (red-green-refactor) is mandatory for deterministic computation modules — risk arithmetic, fusion ranking, and scoring functions — which additionally require property-based tests over their pure functions. Architecture contracts are treated as tests and gate the build: the single-provider-import contract, the computation-boundary contract, and the assertion that the request-serving image contains no modeling-stack packages. The evaluation harness is a separate release gate, not a substitute for code coverage; it measures model quality, which is a different failure mode from code correctness.
- **Linting / Formatting**: Ruff (lint and format) for Python; ESLint with Prettier for TypeScript; `import-linter` for architecture contracts. Static analysis failures block merge.

## Source Code Layout

- **Policy**: ENFORCE_SRC_ROOT
- **Convention**: All project source code MUST live under `/src`, organized into four entries: three source boundaries — `/src/web` (interface), `/src/api` (request serving), `/src/model` (offline modeling package) — plus `/src/gateway`, a shared package holding the model-provider client with its validation and tracing wrapper. Each entry keeps an independent dependency manifest; this is what makes serving/modeling isolation mechanically assertable rather than conventional. Neither Python boundary may declare the other as a dependency, and both depend on the gateway package, so exactly one module in the repository imports the provider client. The gateway package carries neither a web framework nor the modeling stack. Tests live alongside the code they cover within each entry. The one exception is cross-entry verification that has no single owning entry — comparing one entry's dependency set against another's, or asserting on a built image — which lives under `/tests` at the repository root, because assigning it to any one entry would be arbitrary and a fifth entry under `/src` would contradict the four-entry rule above. Entry-local tests MUST NOT be moved there to claim the exception. Specification artifacts live under `specs/`; data, corpus manifests, and datasheets under `data/`.

## Development Workflow

- **Branching**: Feature branches cut from `main`, squash merged. Branch names MUST match `#####-feature-name` so the Feature Workspace resolves from the branch.
- **Commit Convention**: Conventional Commits.
- **CI Requirements**: Lint clean, no type errors, all tests passing, and coverage at or above target before merge. Architecture contracts must pass — a violation of the source layout, the single-provider-import rule, or the computation boundary fails the build rather than raising a review comment. The evaluation reproduction job must confirm published metrics within the stated tolerance before a release tag.

## Data Provenance

- All data MUST be public domain or synthetic. No proprietary, confidential, or customer content enters the repository at any point.
- Every corpus document MUST carry a manifest entry recording source, issuing body, retrieval date, license basis, and a REAL or SYNTHETIC label.
- Copyrighted reference standards MUST be cited, never included. Licenses MUST NOT be mixed within a corpus location.
- Every synthetic dataset MUST ship a datasheet disclosing its generative assumptions.

## Governance

- Project instructions supersede all other documentation and practices.
- Amendments require a version bump with ISO-dated changelog entry.
- All implementations MUST pass the Instructions Check gate during planning.
- Complexity beyond these principles MUST be justified and documented.
- The registered Product Document, Technical Context Document, and Project Plan are the canonical sources for scope, architecture, and sequencing. Where a downstream artifact conflicts with one of them, the registered document wins and the downstream artifact is corrected.
- Project-level architectural decisions live as standalone records under `specs/adrs/`. Decision records are append-only: any change to a chosen option, its drivers, or its consequences MUST supersede rather than edit. Numbers are monotonic and never reused.
- Constraints recorded as architectural — offline-only forecast fitting, the request-time compute envelope, single-path model invocation, and the deterministic computation boundary — MUST NOT be relaxed by a feature-level decision. Relaxing one requires a superseding decision record.

## Amendment History

| Version | Date | Change |
|---------|------|--------|
| 1.1.2 | 2026-07-25 | Source Code Layout gained a narrow exception to "tests live alongside the code they cover": cross-entry verification with no single owning entry lives under `/tests` at the repository root. Raised by the E001 analyze phase, which found the check harness placed there under a "build tooling" classification the instructions did not grant. Several E001 checks compare the serving boundary's dependency set against the modeling boundary's, or assert on a built image — no entry owns them, and a fifth entry under `/src` would contradict the four-entry rule. The exception is scoped so entry-local tests cannot migrate under it. |
| 1.1.1 | 2026-07-25 | Clarified Required QC Categories: "linting, static analysis, coverage" named three phrases that resolve to two categories, since quality control maps lint and static analysis to the same one. An audit read the phrasing as a third enforced category and filed a false finding against the derived policy. Wording only — keyword extraction and the derived policy are unchanged. |
| 1.1.0 | 2026-07-25 | Source Code Layout expanded from three boundaries to four entries under `/src`, adding `/src/gateway` as a shared package, and Technology Stack updated to name three Python entries rather than two. Propagates ADR-0010, which supersedes ADR-0001 to resolve a conflict that had no solution under the three-boundary rule: both Python boundaries require model-provider access, exactly one module repository-wide may import the provider client, and neither boundary may depend on the other. |
| 1.0.0 | 2026-07-25 | Initial project instructions. |

**Version**: 1.1.2 | **Last Amended**: 2026-07-25
