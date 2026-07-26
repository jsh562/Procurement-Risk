---
adr_id: ADR-0011
status: accepted
date: 2026-07-25
tags: [modeling, jobs, orchestration, build-context, governance]
supersedes: ["ADR-0003"]
superseded_by: ""
related_artifacts: ["specs/adrs/0003-offline-modeling-package-instead-of-a-model-service.md", "specs/sad.md", "specs/00002-public-corpus-and-manifest/plan.md", "CAP-001", "CAP-005", "CAP-009", "E001", "E002", "E007"]
deciders: ["Project owner", "Solution architect"]
---

# ADR-0011: Model-Owned One-Shot Jobs Invoked as Console Entry Points

## Status

Accepted. Supersedes [ADR-0003](../adrs/0003-offline-modeling-package-instead-of-a-model-service.md) on one clause only — the invocation path for model-owned one-shot jobs.

ADR-0003's decision is otherwise carried forward verbatim: the modeling boundary remains an offline package of discrete commands, never a service; no posterior is ever sampled at request time; the serving image never installs the modeling stack; and the contract to the serving boundary remains the database. Only "invoked as one-shot container jobs under a non-default profile" is replaced by "invoked as console entry points through the entry's own environment."

## Context

ADR-0003 chose an offline package with command-line entrypoints and, in the same sentence, fixed *how* those entrypoints are reached: "invoked as one-shot container jobs under a non-default profile." The invocation clause was written before any build context existed. E001 has since built one, and the two decisions no longer fit together.

The serving build context is an allowlist by deliberate design. `src/.dockerignore` denies everything with `*` and re-admits exactly two entries, `!api` and `!gateway`, and its header comment states the intent: "An allowlist, not a denylist: a new entry added under /src must be admitted deliberately rather than arriving in the build context by default."

Two E001 checks gate the build on that shape. `tests/checks/test_build_context.py::test_only_the_serving_boundary_and_the_gateway_are_admitted` asserts the admitted set equals exactly `{api, gateway}`, and `::test_excluded_entries_are_unreachable_from_the_build` asserts that `!model` is absent from the ignore rules. Under the project's Testing & Quality Policy, architecture contracts are treated as tests and gate the build; these two are the mechanical form of the serving/modeling isolation that ADR-0003 exists to protect.

A model job image therefore cannot share the `./src` build context — the two persistent job services `ingest` and `fit` in `docker-compose.yml` both build `context: ./src` — without deleting the two contracts that exist precisely to keep the modeling boundary out of that context. Narrowing to a `./src/model` context does not work either: the modeling entry declares `gateway = { path = "../gateway" }` under `[tool.uv.sources]` in `src/model/pyproject.toml`, so the gateway package sits outside any context rooted at `src/model`.

The container-job surface is also unproven rather than established. E001's `ingest` and `fit` services build from `api/Dockerfile` and run trivial `python -c "print(...)"` commands; neither executes any modeling code today. `tests/checks/test_orchestration.py` iterates only the services it declares — `JOBS = frozenset({"ingest", "fit"})` — so declining to add new compose services breaks no existing check.

The decision is needed now because E002 is the first epic to ship real model-owned jobs (corpus retrieve, generate, validate, re-verify), and it cannot pick an invocation path that contradicts an accepted record.

## Decision Drivers

- Preserving the two build-gating build-context contracts, which are the mechanical enforcement of ADR-0003's own serving/modeling isolation
- Preferring an invocation path that exists and is exercised today over one that is declared but never executed
- Not paying for a container surface no current requirement asks for
- Keeping one context policy per directory rather than two divergent ones
- Matching how the modeling entry is already driven in CI

## Considered Options

### Option A: Console entry points invoked through the entry's own environment

Jobs are declared under `[project.scripts]` in `src/model/pyproject.toml` and run as `uv run --directory src/model <entry>`. No compose service, no image.

- **Pros**: Touches neither the build context nor the two contracts guarding it; the modeling entry's `uv`-managed environment already resolves the path dependency on `gateway`, so nothing new is needed to reach it; `.github/workflows/verify.yml` already drives the modeling entry this way — lint, format check, lock check, and `lint-imports` all run `uv run --directory "src/$entry"` over `gateway api model`, and the model unit-test step runs `uv run` with `working-directory: src/model` — so CI invocation is an existing pattern rather than a new one; a developer runs a job with one command and no Docker daemon; no new compose service, so no new orchestration check to maintain.
- **Cons**: Job execution depends on a working local toolchain rather than a pinned image, so environment drift is bounded by the lockfile rather than by a digest; a job that later needs system-level libraries the host lacks has no container to fall back on; the `jobs` compose profile keeps a declared surface that nothing real uses.

### Option B: A model job image sharing the `./src` build context

A model-specific Dockerfile built from `context: ./src`, added as a compose service behind the `jobs` profile alongside `ingest` and `fit`.

- **Pros**: Uniform with the existing compose services; job runtime is pinned by image digest; matches ADR-0003's original wording without amendment.
- **Cons**: Requires admitting `!model` to `src/.dockerignore`, which deletes `test_only_the_serving_boundary_and_the_gateway_are_admitted` and `test_excluded_entries_are_unreachable_from_the_build` — two build-gating architecture contracts written to keep the modeling boundary out of the serving build context; the deletions would weaken the isolation property ADR-0003 was written to establish, in order to satisfy ADR-0003's incidental invocation clause; every serving image build would carry the modeling boundary in its context, making the allowlist a denylist in effect.

### Option C: A per-Dockerfile ignore file admitting `model` for a model Dockerfile only

BuildKit's `<dockerfile>.dockerignore` convention, so a model-specific Dockerfile gets a context admitting `model` while `api/Dockerfile` keeps the current allowlist.

- **Pros**: Leaves the two contracts and `src/.dockerignore` untouched; still yields a pinned container surface for jobs.
- **Cons**: Leaves the repository with two divergent context policies for one directory, so "what is in the `/src` build context" no longer has a single answer and the existing checks assert only one of them; a reader must know a BuildKit-specific resolution rule to understand which policy applies to which build; requires the BuildKit backend, adding a build-environment precondition the repository does not currently have; buys a container surface no current requirement asks for, at the cost of the one property that made the context assertion legible.

## Decision Outcome

Chosen option: **Console entry points invoked through the entry's own environment** — it is the only option that leaves the serving build context and its two gating contracts exactly as E001 built them, and it does so while using an invocation mechanism the repository already exercises on every CI run. Option B satisfies ADR-0003's invocation wording by deleting the checks that enforce ADR-0003's substance, which inverts the record's own priorities: the isolation property is the decision, the container form was the incidental packaging. Option C avoids the deletions but replaces one legible context policy with two that diverge by Dockerfile, and pays that cost for a container surface nothing yet requires. The clause being replaced is narrow and the rest of ADR-0003 is unaffected: no endpoint samples on demand, no modeling package enters the serving image, and each run still terminates and emits a versioned artifact — the invocation path changes, not the topology.

## Consequences

### Positive

- `src/.dockerignore` and both build-context contracts survive unchanged, so the serving/modeling isolation ADR-0003 established stays mechanically enforced rather than becoming a convention.
- Jobs run identically in CI and on a developer machine, through the same `uv`-managed environment the modeling entry's lint, format, lock, architecture-contract, and unit-test steps already use.
- Running a job requires no Docker daemon and no image build, which shortens the loop for the corpus-generation and validation work in E002.
- No new compose service means no new orchestration check, and `tests/checks/test_orchestration.py` continues to pass unmodified.

### Negative

- Job runtime is reproducible to the lockfile, not to an image digest; a host-level difference (system library, toolchain version) is not caught by the same mechanism that pins the serving image.
- A future job needing system-level dependencies absent from a developer's or CI's environment has no containerized fallback under this decision and must trigger a revisit.
- `project-instructions.md` Technology Stack → Infrastructure ("Docker Compose for local development, with one-shot jobs under a non-default profile") and the `specs/sad.md` Deployment and Infrastructure view both still describe the container-job form and now disagree with this record until amended.

### Neutral

- The `jobs` compose profile stays exactly as E001 left it. `ingest` and `fit` remain declared behind the non-default profile, and this decision does not remove them — it declines to make that profile the invocation path for corpus generation and validation.
- ADR-0003's neutral consequences carry forward in intent: whatever job surface exists is non-default, and the serving boundary never declares a startup dependency on a job.
- Reversal trigger, stated explicitly: a model-owned job that cannot run in a developer's or CI's local environment. A later epic that genuinely needs the modeling stack in a container — E007's forecast fit is the likely first — must revisit this record rather than work around it.

## Links

- [ADR-0003](../adrs/0003-offline-modeling-package-instead-of-a-model-service.md) — superseded on the invocation clause only; its offline-package decision, no-request-time-sampling constraint, and database contract carry forward unchanged
- [specs/sad.md](../sad.md) — Deployment and Infrastructure view and Container view describe the container-job form; requires amendment via `.github/skills/amend-project/SKILL.md`
- `project-instructions.md` — Technology Stack → Infrastructure states "one-shot jobs under a non-default profile"; requires amendment via `.github/skills/amend-project/SKILL.md`
- `src/.dockerignore` — the allowlist whose header comment records the deliberate-admission design
- `tests/checks/test_build_context.py` — `test_only_the_serving_boundary_and_the_gateway_are_admitted` and `test_excluded_entries_are_unreachable_from_the_build`, the two build-gating contracts this decision preserves
- `tests/checks/test_orchestration.py` — declares `JOBS = {"ingest", "fit"}`; unaffected by this decision
- `src/model/pyproject.toml` — `[tool.uv.sources]` path dependency on `gateway` that rules out a narrower `./src/model` context
- `.github/workflows/verify.yml` — existing `uv run --directory "src/$entry"` invocation pattern for the modeling entry
- [specs/00002-public-corpus-and-manifest/plan.md](../00002-public-corpus-and-manifest/plan.md) — E002, which raised this decision as AD-006 before it was correctly routed to a superseding record
- E001 — foundation epic that built the serving build context and the `jobs` profile
- E007 — Delivery Forecast Model, the likely first candidate to trigger the reversal
