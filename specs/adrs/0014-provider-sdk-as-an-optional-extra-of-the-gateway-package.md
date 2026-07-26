---
adr_id: ADR-0014
status: accepted
date: 2026-07-25
tags: [llm, gateway, dependencies, packaging, governance]
supersedes: []
superseded_by: ""
related_artifacts: ["specs/prd.md", "specs/adrs/0007-single-traced-language-model-invocation-boundary.md", "specs/adrs/0010-source-layout-with-a-shared-gateway-package.md", "specs/00004-traced-model-gateway/spec.md", "CAP-002", "CAP-008", "E004"]
deciders: ["Project owner", "Solution architect"]
---

# ADR-0014: Provider SDK as an Optional Extra of the Gateway Package

## Status

Accepted.

The provider SDK is declared as a `provider` optional extra of the gateway manifest and imported lazily inside the single allowlisted invocation function, so the gateway's public surface imports and type-checks in an environment where no provider package is installed.

## Context

ADR-0010 made `/src/gateway` a shared package that both Python boundaries depend on and that carries the provider client, and ADR-0007 permits exactly one module repository-wide to import that client. Together they fix *where* the provider is imported, but they say nothing about *whether the provider must be present* for the gateway to be usable.

E004's specification requires more than the import contract delivers. The gateway's public surface must be provider-type-free, and a consumer must be able to import and type-check against it in an environment where no provider package is present (TR-002, TR-003, OBJ1 VC1, SC-002). That criterion is vacuous while the SDK is a hard runtime dependency of the gateway: any environment that can import the gateway also has the SDK installed, so the harness has nothing to assert. A test that cannot fail is not evidence, and the specification's stated structural guarantee would reduce to a restatement of the import contract already in place.

The decision is needed before the gateway's manifest is rewritten. Moving a dependency after two consumers have declared it is a breaking change to both, and E004 is the epic in which both consumers first bind to that manifest.

## Decision Drivers

- Making the provider-type-free surface a testable property rather than a claim
- Keeping exactly one provider import site in the repository, per ADR-0007
- Keeping each consuming boundary's dependency resolution independent, per ADR-0010
- Changing ADR-0010's accepted shape as little as the problem requires

## Considered Options

### Option A: Optional extra with a lazy import inside the allowlisted module

Declare the SDK under `[project.optional-dependencies]` as a `provider` extra and import it inside the invocation function of the single allowlisted module, re-raising a missing-module error as a gateway-owned error naming the install command. Type the client handle as a locally defined protocol rather than a `TYPE_CHECKING`-guarded SDK import, because `import-linter`'s `exclude_type_checking_imports` defaults to false and a guarded import still violates the contract.

- **Pros**: The absent-provider criterion becomes a real assertion, checked in a synthetic environment resolved without the extra; the single import site is unchanged, so ADR-0007 needs no amendment; the public surface cannot leak an SDK type because none is importable at module scope; both consumers keep independent resolutions.
- **Cons**: A consumer that forgets to declare `gateway[provider]` fails at first invocation rather than at dependency resolution; the lint and test environments must install the extra, because the `protected` contract sets `include_external_packages = true` and a distribution absent from the graph makes the contract error rather than pass; one more thing every future consumer must remember.

### Option B: Hard dependency with a weakened requirement

Keep the SDK as a hard dependency and weaken the requirement to "the gateway declares no direct dependency on the SDK", allowing transitive presence.

- **Pros**: No manifest restructuring; no extra for consumers to declare; no new failure mode at invocation time.
- **Cons**: The criterion asserts nothing the import contract does not already cover, so a stated structural guarantee becomes a restatement; the provider-type-free surface remains a review-enforced convention rather than an environment-enforced fact.

### Option C: Separate types-only stub distribution

Ship a types-only stub distribution that the synthetic consumer type-checks against instead of the real gateway.

- **Pros**: Proves the surface shape without touching the runtime manifest; no new consumer-facing extra.
- **Cons**: A second artifact that can drift from the real one, and drift is invisible until a consumer breaks; the thing under test is no longer the thing that ships.

## Decision Outcome

Chosen option: **Optional extra with a lazy import inside the allowlisted module** — it is the only option under which the absent-provider criterion is checked by an environment rather than asserted by a document. Option B is cheaper but purchases that cheapness by rewriting the requirement into something already guaranteed elsewhere, leaving the provider-type-free surface enforced by review. Option C tests a surface that is not the shipped surface, and the failure mode of stub drift is silent until a consumer breaks. The chosen option's cost is a runtime failure for a consumer that forgets the extra, and the obligation to install the extra in the lint and test environments; in exchange the guarantee is mechanical, the single import site is untouched, and ADR-0010's shape is otherwise unchanged.

## Consequences

### Positive

- The provider-type-free public surface is enforced by an environment resolved without the extra, not by review.
- ADR-0007's single-import-site decision is unchanged and needs no amendment.
- The gateway remains importable by tooling that has no provider credential or provider SDK, which keeps type-checking and unit tests cheap for both consumers.
- Both Python boundaries keep independent resolutions; each declares the extra on its own terms.

### Negative

- A consumer that omits `gateway[provider]` fails at first invocation rather than at dependency resolution, moving a resolution-time error to runtime.
- Every environment that runs the import contract must install the extra, because the `protected` contract's `include_external_packages = true` makes an absent distribution an error rather than a pass.
- One more declaration every future consumer of the gateway must remember.

### Neutral

- The invocation module's lazy import is also where the retry loop and deadline live, so the extra is needed in exactly one place rather than scattered across the package.
- The client handle is typed against a locally defined protocol rather than a `TYPE_CHECKING`-guarded SDK import, since `exclude_type_checking_imports` defaults to false and a guarded import would still violate the contract.

## Links

- [specs/prd.md](../prd.md) — product requirements document
- [ADR-0007](../adrs/0007-single-traced-language-model-invocation-boundary.md) — the single-import-site contract this decision preserves without amendment
- [ADR-0010](../adrs/0010-source-layout-with-a-shared-gateway-package.md) — establishes the shared gateway package whose manifest this decision restructures
- [specs/00004-traced-model-gateway/spec.md](../00004-traced-model-gateway/spec.md) — TR-002, TR-003, OBJ1 VC1, SC-002
- CAP-002 — Document Understanding & Extraction (offline consumer of the gateway manifest)
- CAP-008 — Grounded Question Answering (request-serving consumer of the gateway manifest)
- E004 — Traced Model Gateway, the epic in which both consumers first bind to this manifest
