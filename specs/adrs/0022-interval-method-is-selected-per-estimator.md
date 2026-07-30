---
adr_id: ADR-0022
status: accepted
date: 2026-07-29
tags: [evaluation, statistics, reproducibility, retrieval]
supersedes: []
superseded_by: ""
related_artifacts: ["specs/prd.md", "CAP-003", "CAP-009", "CAP-010", "ADR-0005", "ADR-0009", "E008", "E014", "E015"]
deciders: ["Project owner", "Solution architect"]
---

# ADR-0022: Interval Method Is Selected Per Estimator, Not Per Document

## Status

Accepted. Extends [ADR-0009](0009-reproducibility-gate-as-a-published-tolerance.md), which remains accepted and unchanged: it governs the tolerance within which published numbers must reproduce. This record governs which interval method each published metric is entitled to, and adds the bootstrap's resample count, seed, and bit generator to the surface ADR-0009's reproduce job must pin.

## Context

`specs/prd.md` Success Metrics registered both retrieval gates with one phrase. recall@5 ≥ 0.85 was to be "published with a Wilson 95% confidence interval", and MRR ≥ 0.70 was to be "published with a Wilson 95% confidence interval". `specs/sad.md`'s Quality Attributes table compressed the pair further, into a single cell: "recall@5 ≥ 0.85, MRR ≥ 0.70 on the frozen 50-item set, with Wilson 95% CIs".

The Wilson score interval is obtained by inverting the score test for a **binomial proportion**. It is parameterised by a trial count `n` and a success count, and its entire derivation rests on the estimator being a proportion of Bernoulli trials.

recall@5 on the frozen 50-item set qualifies exactly. Each of the 50 queries either returns a relevant chunk in its top 5 or does not; that is 50 Bernoulli trials, and Wilson is the right choice for them — notably better than the normal approximation at the small `n` and near-boundary proportions this set will produce.

MRR does not qualify. It is the arithmetic mean of 50 per-query reciprocal ranks, each drawn from the discrete set `{1, 1/2, 1/3, …, 1/k, 0}`. There is no trial count to invert a score test on and no success proportion to bound. A Wilson interval computed over it is not a conservative interval for MRR, nor an approximate one — it is an interval for a different quantity, reported under MRR's name.

That matters more here than it would elsewhere, for a reason internal to the project. Principle II ("Uncertainty Is the Product") makes the interval part of the deliverable rather than a footnote, and Principle VII ("Publish the Miss") makes the published figure the thing a skeptical evaluator is invited to check. The PRD's Morgan persona "discounts any metric published without an interval or a baseline" — and an interval whose method is inadmissible for its estimator is strictly worse than a missing one, because it presents as the discipline it fails to exercise. An evaluator who checks the method finds the defect at exactly the moment the project was asking to be trusted.

The repository already contains the correct instinct applied to a neighbouring case, which is why this is a gap rather than a misunderstanding. `src/model/src/model/compute/metrics.py` **declines to publish F1 at all**, and publishes the omission with its reason: a Wilson interval inverts the score test for a binomial proportion, and F1, a harmonic mean of two proportions with different denominators, is not one. The same reasoning was available for MRR and was not applied to it, because MRR was registered in a different document, in a row that named its method in the same breath as a metric that genuinely was a proportion. One phrase covered two estimators, and only one of them qualified.

A decision is needed now rather than at implementation time because E008 and E014 are both unstarted, and the registered documents are what they will be built and QC'd against. No code computes MRR yet, so this costs nothing now and would cost a published-number correction later.

## Decision Drivers

- A published interval must be admissible for the estimator it bounds — a correctness property, not a presentational preference
- The method must be stated with the figure rather than inferred from a document-level convention, since a single convention is what allowed one phrase to cover two different estimators
- The interval must be reproducible by the ADR-0009 reproduce job from committed inputs, with no live sampling and no unpinned randomness
- recall@5's existing treatment must not be disturbed — it is correct, and Wilson is genuinely the better choice for a proportion at n=50
- Neither published threshold (0.85, 0.70) may move; this decision is about what the interval measures, not about how good retrieval must be
- The retrieval ablation compares arms on the same 50 queries, so the method must extend to comparing two arms without inviting the overlapping-intervals fallacy
- Minimum new machinery — the modeling entry already owns a `compute` module holding the Wilson implementation and property-based tests over it

## Considered Options

### Option A: Percentile bootstrap over queries for MRR; Wilson retained for recall@5

Resample the 50 queries with replacement B times, recompute MRR on each resample, and take the 2.5th and 97.5th percentiles of the resample distribution. B and the RNG seed are published with the figure. recall@5 keeps its Wilson interval.

- **Pros**:
  - Makes no distributional assumption about the per-query score, which is the assumption that cannot be defended here
  - The standard treatment for a rank-based mean over topics in IR evaluation
  - The resampling unit is the query — the actual unit of sampling variability the frozen 50-item set represents
  - Exactly reproducible given a pinned seed and bit generator
  - Each row states its own method, so the two estimators can never again share one phrase
  - Extends naturally to a paired arm-vs-arm comparison by reusing one resample index matrix across arms
  - Small implementation, into a module that already exists and is already property-tested
- **Cons**:
  - Two interval methods now live in the published results instead of one, so every consumer must read the method beside the figure
  - A bootstrap at n=50 has genuinely limited resolution in the tails, and percentile bounds are known to under-cover slightly for skewed statistics
  - Introduces a seed into the reproducible surface — a new thing ADR-0009's reproduce job must pin
  - B becomes a registered figure that could drift if nothing asserts it

### Option B: Keep Wilson for both (status quo)

- **Pros**:
  - One method, one phrase, no change to any document, no seed
- **Cons**:
  - **Publishes an interval for a quantity that is not the quantity named**, which is the defect itself
  - Falsifies the project's own claim that every metric is published with its interval, because the MRR interval is not MRR's
  - Directly contradicts the reasoning already committed in `compute/metrics.py` for refusing F1
  - Cannot survive the evaluator check the project exists to invite

### Option C: Normal-approximation / Student-t interval on the 50 per-query reciprocal ranks

Treat the 50 reciprocal ranks as a sample and publish mean ± t·SE.

- **Pros**:
  - Closed form
  - No seed, so nothing new enters the reproducible surface
  - Admissible in the weak sense that it bounds a mean rather than a proportion
- **Cons**:
  - The per-query reciprocal-rank distribution is severely non-normal — discrete, bounded on `[0,1]`, and heavily massed at exactly 1 and exactly 0, since a hit at rank 1 and a total miss are both common — which is close to the worst case for a t-interval at n=50
  - Can produce bounds outside `[0,1]`, which is indefensible on a published figure
  - The appeal to the central limit theorem is precisely the unexamined assumption the bootstrap avoids

### Option D: BCa (bias-corrected and accelerated) bootstrap

- **Pros**:
  - Better coverage than the percentile method for skewed statistics
  - The technically superior interval
- **Cons**:
  - Requires jackknife acceleration estimates on top of the resampling, roughly tripling both the implementation and the property-based test surface
  - The accuracy gain is immaterial relative to a 50-query set whose dominant limitation is its size, not its interval construction
  - Harder for a reader to verify by hand, which cuts against the project's audit-by-a-skeptical-reader posture

### Option E: Percentile bootstrap for both metrics, for method uniformity

- **Pros**:
  - One method across the retrieval table
  - The query is a defensible resampling unit for both metrics
- **Cons**:
  - Replaces a correct, closed-form, seed-free interval with a resampled one for no gain — Wilson is the better interval for a proportion at n=50, especially near the boundary
  - Needlessly widens the reproducible surface by making recall@5's bounds seed-dependent too
  - The uniformity it buys is the exact instinct that caused the defect: treating "the interval method" as a document-level constant is what let one phrase cover two estimators

## Decision Outcome

Chosen option: **Percentile bootstrap over queries for MRR; Wilson retained for recall@5** — the interval method is a property of the estimator, not of the document or the table the figure appears in.

Option E is the one worth rejecting explicitly, because uniformity is the intuitive answer and uniformity is the actual cause. A single "Wilson 95%" phrase covering a table of metrics is how an inadmissible method reached a published gate without anyone ever choosing it for the estimator it landed on. Making the table uniform in the other direction would repair this instance while preserving the mechanism, and would additionally give up a correct closed-form interval for a genuine proportion.

Option B is refused on its own terms: it publishes bounds belonging to a different quantity under MRR's name, and it contradicts a refusal the repository has already committed for F1 on identical grounds. Option C trades the assumption for a worse one — a t-interval on a discrete distribution massed at both endpoints, capable of printing a bound outside `[0,1]`. Option D is better statistics than is warranted by a 50-query set, and buys coverage at the cost of an implementation and test surface no reader would check by hand.

The registered parameters below are the decision's concrete content:

- **Method for MRR**: percentile bootstrap, 95%, resampling the 50 queries with replacement.
- **B = 10,000 resamples**, registered in `specs/sad.md`'s Quality Attributes measurement column.
- **The seed is recorded in the committed results manifest**, alongside the interval bounds it produced.
- **The bit generator is pinned explicitly** — a named PCG64 generator, seeded by integer, sampling integer query indices — and not merely the seed. A seed alone does not fix a bit stream across library versions, and an interval that silently moved on a dependency bump would read as drift to the reproduce job.
- **Method for recall@5**: continuity-corrected Wilson 95%, unchanged, on the proportion of queries with a relevant chunk in the top 5.
- **Every published interval names its own method with the figure.**

Neither threshold moved. The point estimate remains what the release gate compares against: the interval is published beside the gate, it is not the gate, so this change cannot make a passing metric fail or a failing one pass.

## Consequences

### Positive

- The MRR interval bounds MRR. That is the point of the record.
- Each row of the published retrieval table names its own method, so the two estimators cannot silently re-merge under one phrase.
- With a pinned seed and bit generator the bootstrap bounds are *exactly* reproducible, so the reproduce job can assert equality on them rather than a tolerance — a stronger check than ADR-0009's ±0.01 absolute band, which stays in force for the point estimates.
- The resampling unit is the query, which is the unit the frozen 50-item set actually samples, so the interval answers the question an evaluator is asking: how much would this number move on a different draw of 50 queries.
- The ablation gains a correct paired comparison — reusing one resample index matrix across arms yields an interval on the *per-query difference* between two arms.
- Consistency is restored with the F1 refusal already committed in `compute/metrics.py`. The project now applies one rule in both places instead of applying it in one.

### Negative

- Two interval methods appear in one published table, and a reader who skims the method column will draw a false comparison between a Wilson bound and a bootstrap bound.
- A seed and a resample count enter the reproducible surface, and both must be committed for the interval to be evidence. An unrecorded seed makes a published bound unreproducible, which is precisely what ADR-0009 exists to prevent — so the recording is a requirement, not a nicety.
- Percentile bootstrap bounds under-cover somewhat for a skewed statistic at n=50. This is disclosed rather than papered over with a claim of exactness; the limitation is the set size, and is published as such under the PRD's standing sample-size risk.
- B = 10,000 is a registered figure that nothing yet asserts. An implementation that quietly runs 1,000 produces a valid but different interval, and only a check on the recorded count would catch it.
- Independent arm-wise intervals on the ablation invite the overlapping-intervals fallacy: two arms whose intervals overlap may still differ significantly on paired data. The paired-difference interval is the mitigation, and it has to be *the* published comparison rather than a supplement to arm-wise bounds.

### Neutral

- No code changes. Nothing in the repository computes MRR yet, and the existing Wilson implementation in `src/model/src/model/compute/metrics.py` bounds extraction-defect proportions, which are genuine proportions and are untouched.
- Both thresholds (0.85, 0.70) are unchanged, and the gate still compares point estimates.
- BCa is the recorded refinement path, to be revisited if the evaluation set grows enough for the interval construction, rather than the set size, to become the binding limitation.
- Wilson's continuity correction, already the project's chosen variant, is unchanged for recall@5.

## Links

- [ADR-0009: Reproducibility Gate as a Published Tolerance](0009-reproducibility-gate-as-a-published-tolerance.md) — the decision this extends; supplies the tolerance and the committed results manifest the seed and bounds are recorded in
- [ADR-0005: Exact Vector Search for Evaluation, Approximate for Serving](0005-exact-vector-search-for-evaluation-approximate-for-serving.md) — the exact-search evaluation path these metrics are measured over
- [specs/prd.md](../prd.md) — Success Metrics (Retrieval recall@5, Retrieval MRR), CAP-003 (Hybrid Retrieval), CAP-009 (Evaluation & Calibration Evidence), CAP-010 (Rigor & Limitations Documentation), and the sample-size Risk
- [specs/sad.md](../sad.md) — Quality Attributes (Retrieval quality, Evaluation reproducibility)
- [specs/project-plan.md](../project-plan.md) — E014 (publishes the metrics and their intervals), E008 (produces the retrieval arms), E015 (reads the results manifest)
- [src/model/src/model/compute/metrics.py](../../src/model/src/model/compute/metrics.py) — the existing continuity-corrected Wilson implementation, and the F1 refusal that applies the same reasoning
