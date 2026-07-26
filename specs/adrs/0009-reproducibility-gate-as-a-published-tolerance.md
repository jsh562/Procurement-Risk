---
adr_id: ADR-0009
status: accepted
date: 2026-07-25
tags: [reproducibility, evaluation, governance]
supersedes: []
superseded_by: ""
related_artifacts: ["specs/prd.md", "CAP-009", "CAP-010", "ADR-0003", "ADR-0005", "ADR-0006", "ADR-0007"]
deciders: ["Project owner", "Solution architect"]
---

# ADR-0009: Reproducibility Gate as a Published Tolerance

## Status

Accepted.

## Context

The product treats reproducibility of published evaluation numbers as a release gate, and requires evaluation sets to be frozen and hashed before any tuning run touches them. Both are stated as governance requirements rather than suggestions. Neither states what "reproduces" means numerically, and that gap has to be closed before the evaluation harness is written.

Implementing the gate literally, as byte-for-byte equality, is not achievable. Markov chain Monte Carlo results are not bit-identical across different linear-algebra builds or processor architectures, and hosted model decoding is not deterministic even with sampling parameters removed. A gate that promised exact equality would be false the first time anyone ran it on hardware other than the author's — which is worse than a weaker but honest guarantee, because the gate exists specifically to establish credibility with a skeptical reader who intends to run it.

Separately, the freeze-before-tuning rule is a process commitment that no code currently enforces. A process commitment that is not mechanised is not a commitment; it is an intention that survives exactly as long as it is convenient. The small evaluation sets — fifty retrieval items, forty labeled pairs, roughly a hundred and twenty uncensored delivery events — are precisely the sizes at which tuning against the test set is both tempting and invisible.

A decision is needed now because both the tolerance and the hash check are properties of the harness rather than things that can be bolted on afterwards. A harness written without a tolerance has no comparison step to add one to, and a hash check introduced after the first tuning run cannot certify the ordering it exists to certify.

## Decision Drivers

- The gate must state something that is actually true on other people's hardware
- Turning the freeze-before-tuning rule into an exit code rather than a promise
- Preserving the credibility that the whole reproducibility claim exists to establish
- Keeping the check cheap enough to run on every evaluation invocation

## Considered Options

### Option A: Published tolerance with hash-pinned inputs

The gate is that published metrics reproduce within a stated tolerance from a clean checkout — a small absolute band on retrieval and resolution metrics, and a small relative band on forecast skill — with the base image pinned by digest and dependencies pinned by hash. Evaluation sets are canonicalised, hashed, and committed; the harness verifies the hash before running, aborts on mismatch, and prints the verified hash into the published results.

- **Pros**:
  - States a guarantee that holds on hardware the author has never seen
  - Converts freeze-before-tuning from a policy into an exit code
  - The published tolerance is itself evidence the author understands where the variance comes from
  - Cheap: a hash check and a comparison against a committed results manifest
  - Composes with the fixed reranker and the exact-search evaluation path, which already remove two variance sources
- **Cons**:
  - A weaker-sounding claim than exact reproduction
  - The tolerance is a judgement call that must be defended
  - Requires a comparison job and a committed results manifest to be maintained

### Option B: Bitwise equality

Published numbers must reproduce exactly.

- **Pros**:
  - Strongest possible statement
  - No tolerance to justify
- **Cons**:
  - Not achievable across linear-algebra builds or processor architectures for sampling-based inference
  - A reader familiar with the numerical stack will read it as evidence the claim was never tested
  - Fails on the first machine that differs from the author's, undermining the credibility the gate exists to build

### Option C: Replay-only reproduction

Ship cached outputs and stored draws; reproduction replays artifacts rather than recomputing them.

- **Pros**:
  - Always succeeds
  - Fastest to run
- **Cons**:
  - Demonstrates that the pipeline executes, not that results reproduce
  - Much weaker than what the product requirement implies
  - An informed reader will identify the substitution immediately

## Decision Outcome

Chosen option: **Published tolerance with hash-pinned inputs** — the gate is that published metrics reproduce within a stated numeric band from a clean checkout, with evaluation-set hashes verified by the harness before any metric is computed.

The deciding argument is which claim survives being tested by someone else. Option B makes the strongest statement and is the only one that cannot be honoured: sampling-based inference is not bit-identical across linear-algebra builds or processor architectures, and hosted decoding is not deterministic regardless of sampling parameters. Its first failure would occur on the first unfamiliar machine, and to a reader who knows the numerical stack that failure reads as proof the claim was never run — the precise inference the gate exists to prevent. A bounded claim that holds is worth more than an exact claim that does not.

Option C inverts the purpose. Replaying stored draws and cached outputs shows that the pipeline executes, which was never in question; it does not show that the numbers reproduce. Because the substitution is obvious to the audience the gate is written for, it converts a rigor artifact into a liability.

Option A is also the only option that mechanises the freeze-before-tuning ordering. Canonicalising and committing the evaluation sets, verifying their hash at harness startup, and aborting non-zero on mismatch turns a governance requirement into a condition the harness enforces on every invocation — and printing the verified hash into the published results lets a reader confirm which set produced which number. The cost is one hash comparison and one diff against a committed results manifest, which is cheap enough to run unconditionally.

The tolerance is defensible because the remaining variance is small and its sources are known. The evaluation path already uses exact vector search rather than a non-deterministic graph index (ADR-0005) and a pinned local reranker rather than a vendor-versioned service (ADR-0006), and every fit emits a manifest recording code revision, input hash, seeds, and library versions (ADR-0003). What is left is sampling variation and hosted decoding, both disclosed. Stating the band and naming its cause is a stronger signal of command over the stack than asserting a band of zero.

## Consequences

### Positive

- The reproduction claim survives contact with unfamiliar hardware, which is the only test that matters for it.
- Evaluation-set integrity is enforced by the harness exiting non-zero, with the verified hash printed into published results.
- Stating the tolerance and its cause reads as command of the numerical stack rather than as hedging.
- Combined with the pinned reranker and exact-search evaluation path, the remaining variance is confined to sampling and decoding, both of which are disclosed.

### Negative

- The gate is a bounded rather than an exact claim, and the bounds must be justified in the published write-up.
- A committed results manifest and a comparison job become artifacts requiring maintenance.

### Neutral

- Sampling seeds, the synthetic generator seed, and the train and held-out split seed are recorded in each run manifest.
- The base image is pinned by digest and dependencies by hash, so environment drift is not a permitted source of variance.
- A missed target is published with its cause under the standing published-miss rule; the tolerance governs reproduction, not whether a target was met.

## Links

- [specs/prd.md](../prd.md) — CAP-009 (Evaluation & Calibration Evidence), CAP-010 (Rigor & Limitations Documentation)
- [ADR-0003: Offline Modeling Package Instead of a Model Service](0003-offline-modeling-package-instead-of-a-model-service.md) — supplies the per-run manifest this gate checks against
- [ADR-0005: Exact Vector Search for Evaluation, Approximate for Serving](0005-exact-vector-search-for-evaluation-approximate-for-serving.md) — removes approximate-index variance from published retrieval numbers
- [ADR-0006: Local Quantized Cross-Encoder Reranker](0006-local-quantized-cross-encoder-reranker.md) — removes vendor model-version variance from published retrieval numbers
- [ADR-0007: Single Traced Language-Model Invocation Boundary](0007-single-traced-language-model-invocation-boundary.md) — supplies the hash-keyed response fixtures that make extraction results reproducible
