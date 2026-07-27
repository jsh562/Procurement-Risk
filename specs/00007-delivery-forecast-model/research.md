# Research: Delivery Forecast Model

> Feature: E007 Delivery Forecast Model | Date: 2026-07-27 | Purpose: ground story priorities, acceptance criteria, and edge cases for a hierarchical censored fit that materializes canonical draws plus a derived day-grid survival array under a run manifest.

## Partial pooling with very small groups

- **Practice**: Shrinkage toward the population mean scales as ρ_j = τ²/(τ² + σ²/n_j), so a 35-line vendor is near its own data and a 5-line vendor is mostly prior. What pooling buys is a stable, non-extreme estimate and honestly wide intervals; PyMC's primer is explicit that extreme unpooled estimates from few observations should not be trusted. At J=12, τ itself is weakly identified, and a half-t on the group SD is preferred to a uniform.
- **Implies**: Require per-vendor intervals to widen as n_j falls, and require the run to publish the realized shrinkage weight per vendor so a reader can see how much of an estimate is data. Forbid any acceptance criterion phrased as a vendor-level tail quantile below a stated n — E005's datasheet L-1 records shrinkage 0.22 at n=5, i.e. 78% pooled. A story ranking vendors by fitted effect is unsupportable here; a story reporting a per-vendor interval with disclosed shrinkage is.
- **Flag**: No literature threshold for "enough observations" exists — the cutoff is a judgement to be stated, not cited. Scoring the model on recovering τ is not fair at J=12; score coverage of per-vendor offsets against E005's ground-truth artifact instead.
- **Sources**: <https://www.pymc.io/projects/examples/en/latest/generalized_linear_models/multilevel_modeling.html>, <https://sites.stat.columbia.edu/gelman/research/published/taumain.pdf>

## Right-censoring in duration models

- **Practice**: A censored line contributes Pr[T > t_cens] — the survival function / complementary CDF — not a density at t_cens. Observed events contribute the density. The standard failure mode is treating an open line's elapsed days as a completed duration, which biases durations downward; dropping open lines entirely biases downward as well, so neither naive treatment is safe.
- **Implies**: An acceptance criterion should assert the censored contribution is exercised, not described: fit the same input with and without the censoring term and assert the censoring-ignoring fit produces materially shorter median forecasts. Require the censoring indicator to be derived from lifecycle state at a recorded as-of date and stored, never inferred at read time.
- **Flag**: At ~12% censored, roughly 24 lines are open. Those 24 are the served forecast surface for the coordinator worklist; per-vendor open-line counts are 1–3, which supports no per-vendor statement about open work.
- **Sources**: <https://mc-stan.org/docs/stan-users-guide/survival.html>, <https://lifelines.readthedocs.io/en/latest/Survival%20Analysis%20intro.html>

## Posterior storage and reproducibility

- **Practice**: Store draws in a group-structured container that keeps per-draw sampler statistics beside them — ArviZ InferenceData separates `posterior`, `sample_stats` (lp, energy, diverging), `observed_data`, `constant_data`, and `log_likelihood`, with optional `created_at`, `inference_library`, and library-version attributes. The schema reserves `chain`/`draw` as dimension names but mandates no dimension order: canonical ordering is the writer's obligation, not the format's. A seed alone does not reproduce draws across library versions.
- **Implies**: The spec must name the sort key that makes the draw array "canonical" and state it as a total order with deterministic tie-breaking, or the artifact hash is undefined. Require the manifest to carry seeds (sampling, split, generator), code revision, input hash, library versions, and the diagnostics summary, and require cross-run comparison to run against manifest plus artifact hash rather than float equality.
- **Flag**: ADR-0009 already rules out bitwise equality as the gate. Any criterion demanding identical draws on different hardware contradicts a registered decision and must be written as a published tolerance instead.
- **Sources**: <https://python.arviz.org/en/stable/schema/schema.html>, <https://numpy.org/doc/stable/reference/random/compatibility.html>

## Sampling diagnostics that constitute a pass/fail gate

- **Practice**: The conventionally blocking set is R-hat, bulk-ESS, tail-ESS, divergent transitions, and E-BFMI — all validity concerns. Stan's stated numbers: at least four chains with R-hat below 1.01 for full trust (1.1 only in early workflow); bulk-ESS above 100 × chains, i.e. 400 at four chains, and the same bar applied to tail-ESS; E-BFMI nominal threshold 0.3. Max treedepth is an efficiency concern, not a validity one.
- **Implies**: Write the gate as a thresholded, recorded criterion rather than an inspection: on any breach across the monitored parameter set, the fit job fails, writes no rows, and does not move the active-run pointer. The spec should name the thresholds, the monitored parameter set, and the chain count, and should record max-treedepth hits as reported-but-not-blocking.
- **Flag**: Divergence tolerance is genuinely contested — Stan says a small count "cannot be safely ignored" yet also that a few divergences with good R-hat and ESS are often workable. Pick and publish a number (zero is the defensible default at this dataset size); leaving it to judgement makes the gate unfalsifiable.
- **Sources**: <https://mc-stan.org/learn-stan/diagnostics-warnings.html>, <https://mc-stan.org/docs/reference-manual/analysis.html>

## Survival arrays on a day grid

- **Practice**: The sorted draw array is the canonical posterior; the day grid is a derived step-function view of S(t) at integer day offsets from a stated anchor, kept so the read path is an array index rather than a sort (ADR-0004). Consistency means the grid is a pure function of the draws — non-increasing, S(0) = 1, and each grid value equal to the fraction of draws exceeding that day. Evaluation grids are conventionally capped inside observed follow-up, because beyond the largest observed duration there is no censoring support.
- **Implies**: Require an explicit anchor-date convention and a stated grid horizon in the spec, plus a recomputation criterion: regenerate the survival array from the stored draws and compare within a published tolerance. Require both representations to carry the same run id and schema version and to be written in one transaction, so a partial write cannot leave a served array whose draws disagree.
- **Flag**: Inverse-cumulative percentile lookups are limited by grid resolution; the whole-day rounding ADR-0004 already mandates must be stated as the published convention, not left to the reader. A horizon chosen past the longest observed duration turns the tail of every curve into extrapolation.
- **Sources**: <https://scikit-survival.readthedocs.io/en/stable/user_guide/evaluating-survival-models.html>, <https://mc-stan.org/docs/stan-users-guide/survival.html>

## Calibration of an uncertainty product

- **Practice**: Forecasts are calibrated when observations look like draws from their predictive distributions; the goal is maximum sharpness subject to calibration. The standard instruments are the PIT histogram (uniformity), nominal-versus-empirical interval coverage, and proper scoring rules. Under censoring the plain PIT is undefined for open lines; D-calibration is the standard repair, splitting each censored case uniformly across the probability bins after its censoring time.
- **Implies**: E007 owns the artifacts, not the verdict. Acceptance criteria should require per-line predictive draws for held-out lines, the stored split assignment, the realized split fraction, and — critically — the realized held-out *uncensored event count*, all joinable to the run id. E007 should carry no coverage threshold of its own; asserting one would duplicate E014's gate against a smaller sample.
- **Flag**: The realized split fraction is load-bearing on E014's gate and the arithmetic does not currently close. The PRD's 73–87% band around 80% coverage is ~1.96 standard errors at about 120 uncensored events; a 0.25 held-out split of ~175 uncensored lines yields roughly 44, where the same interval is about ±12 points. Either the held-out slice must be far larger than 0.25, or coverage must be assessed by cross-validation rather than a single holdout, or the band is unmeetable and belongs in Publish-the-Miss. E007 should surface the event count so the choice is made explicitly rather than discovered by E014.
- **Sources**: <https://sites.stat.washington.edu/raftery/Research/PDF/Gneiting2007jrssb.pdf>, <https://jmlr.org/papers/v21/18-772.html>

## Open questions carried into the specification

- **Split fraction versus calibration precision.** E005's research flagged that "~120 uncensored events after held-out splitting" is ambiguous between the training subset and the whole dataset. E007 is the epic that resolves it by performing the split. **Decided during elicitation**: E007 publishes the realized held-out uncensored event count and states that ~44 events cannot support a ±7-point band; E014 owns the calibration verdict and chooses cross-validation or a widened band with that number in hand.
- **Divergence tolerance has no citable constant.** **Decided**: zero, published as the threshold, with the diagnostics gate blocking.
- **Grid horizon has no external convention.** Bounded above by observed follow-up; the exact value is a stated design choice to be made in Plan.
- **"Mutually consistent" needs a numeric tolerance.** Recomputation from draws to grid involves floating-point aggregation; the criterion is only checkable once the tolerance and the rounding convention are written down.

## Sources Index

| URL | Topic | Fetched |
|-----|-------|---------|
| <https://www.pymc.io/projects/examples/en/latest/generalized_linear_models/multilevel_modeling.html> | partial pooling | 2026-07-27 |
| <https://sites.stat.columbia.edu/gelman/research/published/taumain.pdf> | partial pooling | 2026-07-26 (cached, E005) |
| <https://mc-stan.org/docs/stan-users-guide/survival.html> | censoring; survival grid | 2026-07-27 |
| <https://lifelines.readthedocs.io/en/latest/Survival%20Analysis%20intro.html> | censoring | 2026-07-27 |
| <https://python.arviz.org/en/stable/schema/schema.html> | posterior storage | 2026-07-27 |
| <https://numpy.org/doc/stable/reference/random/compatibility.html> | posterior storage | 2026-07-26 (cached, E005) |
| <https://mc-stan.org/learn-stan/diagnostics-warnings.html> | diagnostics gate | 2026-07-27 |
| <https://mc-stan.org/docs/reference-manual/analysis.html> | diagnostics gate | 2026-07-27 |
| <https://scikit-survival.readthedocs.io/en/stable/user_guide/evaluating-survival-models.html> | survival grid | 2026-07-27 |
| <https://sites.stat.washington.edu/raftery/Research/PDF/Gneiting2007jrssb.pdf> | calibration | 2026-07-27 |
| <https://jmlr.org/papers/v21/18-772.html> | calibration | 2026-07-27 |
