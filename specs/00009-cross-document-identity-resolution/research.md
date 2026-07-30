# Research: Cross-Document Identity Resolution

> Feature: E009 Cross-Document Identity Resolution | Date: 2026-07-29 | Purpose: ground the blocking-recall claim, the threshold and withhold-band contract, the clustering guard, the small-sample error bound, and the alias/unit normalization contract for precision-biased linking across specification, submittal, and purchase-order records.

## Pipeline stages and how blocking recall is measured

- **Decision**: Report blocking as its own stage with its own metrics — pair completeness (share of true pairs surviving into a shared block), pairs quality, and reduction ratio — measured against ground truth sampled **independently of the blocking keys**, and reported separately from end-to-end precision and recall.
- **Rationale**: Blocking is a preprocessing step, so ordinary precision and recall do not apply to it; the literature uses PC/PQ/RR precisely because a pair discarded at blocking cannot be recovered downstream, which is why the blocking phase conventionally prioritises pair completeness over reduction. Blocking errors are also attributed to invalid key assumptions and to records simply lacking the discriminating attribute — both live here, since a submittal may name a manufacturer the purchase order abbreviates and a specification may carry no part number at all.
- **Pitfall (criterion-breaking)**: If the labeled pair set is drawn from the *blocked candidate pool*, pair completeness is 1.0 by construction and the criterion "blocking drops no true pair present in the labeled set" measures nothing. Blocking ground truth must come from a source that never saw the blocking keys.
- **Implication for the spec**: Require blocking recall measured against generator/corpus ground truth rather than the hand-labeled scoring set; require the sampling frame to be stated; require PC and RR published as separate figures alongside merge precision, never folded into it. Require a union of blocking keys (manufacturer alias **or** part-number prefix, not both) — a single conjunctive key is the standard way pair completeness is lost.
- **Sources**: <https://arxiv.org/pdf/1905.06397>, <https://arxiv.org/abs/2008.04443>

## Precision-biased thresholds and the withhold band

- **Decision**: Adopt the two-threshold Fellegi–Sunter decision rule literally — above the upper cutoff merge, below the lower cutoff reject, between them withhold to review — and treat the **size of the withhold band as a published output, not a tuned target**.
- **Rationale**: The Fellegi–Sunter theorem is exactly a statement about this shape: for *fixed bounds on the false-match and false-non-match rates*, the rule minimises the clerical-review region. The honest publication order is therefore (1) declare the error bounds, (2) report the resulting review-region size. The model gives no guidance on where the cutoffs sit — that is left entirely to the practitioner — so thresholds must be recorded as a calibrated, frozen artifact rather than presented as derived.
- **Pitfall (criterion-breaking)**: Precision alone is trivially gamed by withholding almost everything; a precision-only criterion is passed by a system that merges three pairs. Coverage — the share of candidate pairs auto-decided — must be published with it, and the secondary recall floor is what actually restrains the abstention.
- **Implication for the spec**: State precision and coverage as one criterion, not two independent ones. Define explicitly whether a *withheld* true pair counts as a recall miss (convention: yes — recall counts only auto-merged true pairs, otherwise recall cannot fall and is meaningless). Require the withheld set to publish its own count, its share of candidates, and, once labeled, its yield — the share of withheld pairs that were true — because that yield sizes E016's workload.
- **Sources**: <https://moj-analytical-services.github.io/splink/topic_guides/theory/fellegi_sunter.html>, <https://arxiv.org/abs/2008.04443>

## Clustering from pairwise scores — the transitivity guard

- **Decision**: Forbid plain transitive closure. Require that every pair *induced* by a final cluster has itself been scored and has itself exceeded the merge threshold — a complete-linkage / clique constraint — and measure precision over the induced pair set, not only over the pairs the scorer decided.
- **Rationale**: Connected components computes the transitive closure of all links and raises recall, but is well documented as sensitive to noise: one wrong link chains two entities that were never compared into a single large cluster, which is exactly the silent corruption Principle III exists to prevent. Correlation clustering is the principled alternative but is NP-hard, so implementations are approximations; a common published pattern is two-step — transitive closure to soft clusters, then a refinement pass that splits them for precision. Simple single-pass center/merge-center methods reportedly match or beat more complex ones, so complexity buys little here.
- **Pitfall (criterion-breaking)**: "Merge precision on the hand-labeled pair set" does **not** bound cluster precision if closure is used. A system can score 100% on every labeled pair and still emit a cluster containing two different pumps, and the published criterion would not detect it. This is the most likely way a passing criterion coexists with a broken result.
- **Implication for the spec**: State which pair population precision is measured over, and make that population the transitive closure of the emitted clusters. Add an acceptance criterion that no cluster contains a pair scoring below the merge threshold. Consider a cardinality constraint where the domain supports one (for example, at most one specification-section record per cluster) as a second, cheap guard.
- **Sources**: <https://arxiv.org/pdf/1905.06397>, <https://arxiv.org/abs/2112.06331>

## Evaluating on forty hand-labeled pairs

- **Decision**: Publish merge precision as a **point estimate with a disclosed interval**, and do not write the target as a lower confidence bound. Name the fallback estimator for the non-zero-error case before it is needed.
- **Rationale, with the arithmetic**: The rule of three says that with zero observed errors in n trials, [0, 3/n] is a 95% interval for the error rate. The relevant n is **the number of merges made among labeled pairs**, not 40 — withheld and rejected pairs are not in the precision denominator. Even in the impossible best case where all 40 labeled pairs are merged with zero errors, 3/40 = 0.075, so the 95% lower bound on precision is **0.925 — below the 0.95 target**. A 3/n bound of 0.05 requires n ≥ 60 merges. If the labeled set is balanced at roughly 20 true and 20 false pairs, realistic n is nearer 15–20 and the bound is 0.85 or wider. The rule also carries an n > 30 caveat for the approximation itself.
- **Pitfall (criterion-breaking)**: Two ways a correct implementation fails a published criterion. First, if "merge precision ≥ 0.95 with a rule-of-three bound" is read as *the bound* clearing 0.95, it is arithmetically unreachable at this sample size and a perfect system fails. Second, the rule of three applies **only at zero errors** — a single false merge makes it inapplicable, and an exact binomial (Clopper–Pearson) one-sided lower bound at 1 error in 40 sits near 0.88. A criterion naming only the rule of three becomes unmeasurable in exactly the case it was written for.
- **Also**: ER-specific guidance warns that labeling error and the sampling procedure themselves bias these estimates, and that results must disclose record counts, noise level and label reliability. Precision on a curated 40-pair set is not precision over the deployed merge population and should not be presented as such.
- **Implication for the spec**: State the composition of the 40 pairs (how many true, how many false, how sampled) — without it neither precision nor recall has a defined denominator. Write the criterion as "point estimate ≥ 0.95, published together with its 95% interval", per Principle II. Name the interval method for both branches: rule of three at zero errors, exact binomial otherwise. Record that the interval width, not the point estimate, is the honest statement of what 40 pairs support, and treat that as a Principle VII disclosure rather than a shortfall.
- **Sources**: <https://en.wikipedia.org/wiki/Rule_of_three_(statistics)>, <https://arxiv.org/abs/2008.04443>

## Manufacturer alias tables — structure, versioning, auditability

- **Decision**: Model the alias table on the SKOS labeling pattern — exactly one preferred label per canonical manufacturer, unlimited alternate labels, and a separate hidden-label class for known misspellings and OCR variants that should match but never display. Enforce that alias-to-canonical is a **function**: an alias resolving to two manufacturers is a validation failure, not a runtime tie-break.
- **Rationale**: SKOS makes the preferred/alternate/hidden properties pairwise disjoint and limits a resource to one preferred label per language; its integrity conditions are the ready-made shape for an auditable alias table. The hidden-label class matters specifically here — extraction noise and vendor abbreviations belong in matching but not in display, and separating them keeps the display name stable while the match surface grows.
- **Pitfall (the normalization-destroys-a-distinction failure)**: Unicode's normalization guidance is explicit that compatibility forms "must not be blindly applied to arbitrary text" because they erase distinctions irreversibly. The caution transfers directly to part numbers: case-folding, hyphen-stripping and suffix-trimming routinely collapse catalog numbers whose suffix encodes voltage, enclosure rating or handing — genuinely different materials, silently merged.
- **Implication for the spec**: Require normalization to be **additive** — the raw string is retained, the normalized form is stored beside it, and every merge records which alias rule and which alias-table version fired, so the decision is reproducible (Principle I). Require an alias-table version identifier on every resolution run, since editing the table silently changes historical merges. Add a collision guard as an acceptance criterion: no normalization rule may map two records known to be distinct onto the same key, tested over the labeled negative pairs.
- **Sources**: <https://www.w3.org/TR/skos-reference/>, <https://unicode.org/reports/tr15/>

## Unit canonicalization

- **Decision**: Canonicalize dimensional quantities to a declared base-unit form in the UCUM manner, and classify non-dimensional procurement units — each, lot, lump sum — explicitly as **arbitrary and non-convertible** rather than forcing them into the same scheme.
- **Rationale**: UCUM exists to make unit expressions unambiguous machine-to-machine, resolving different notations to the same meaning by dimensional analysis over seven base units. It is equally explicit that arbitrary units have no defined relation to any other unit, cannot be converted or compared, and contaminate any expression containing them. It also warns that legacy units come in conflicting variants — the calorie and the BTU are the named examples — where the wrong variant produces errors of enormous magnitude.
- **Pitfall (criterion-breaking)**: Treating unit agreement as a merge gate. A specification in millimetres and a purchase order in inches describe the same part; a mismatch is weak negative evidence, not a veto. Conversely, forcing "EA" and "LS" into a shared canonical form manufactures agreement that does not exist.
- **Implication for the spec**: Require unit agreement to be a scored attribute contributing to the pair score, never a hard filter. Require the canonical form and its base units to be declared in the artifact, require conflicting-variant units to be resolved by an explicit recorded choice, and require non-convertible units to compare only for equality after alias mapping.
- **Sources**: <https://ucum.org/ucum>

## Open questions for clarification

Places where the evidence does not support a specific number, and a fabricated constant would be worse than an open question.

- **The composition of the 40 labeled pairs is undefined and every metric depends on it.** Precision's denominator is merged pairs, recall's is true pairs, and neither is knowable until the true/false split and the sampling frame are stated. Highest-value clarification in the feature.
- **The 0.95 target and the rule-of-three bound cannot both be lower bounds at n = 40.** The spec must decide which is the claim. Recommended: point estimate is the target, interval is a mandatory disclosure.
- **No source gives a conventional review-queue rate for entity resolution.** Practitioner write-ups cite figures in the 10–15% band, but these are operational blog guidance, not standards. Treat the withhold-band size as a measured output with no target.
- **No authoritative threshold value exists.** Fellegi–Sunter deliberately leaves cutoff placement to the practitioner, and commonly quoted 0.85–0.95 bands are practitioner convention rather than derivation. Thresholds must be calibrated on the frozen set and recorded, per Principle VI, not cited.
- **Whether a cluster may legitimately contain two purchase-order lines is a domain question.** The cardinality guard above depends on it, and the answer determines whether one spec section plus one submittal plus many PO lines is the expected cluster shape.

## Sources Index

| URL | Topic | Fetched |
|-----|-------|---------|
| <https://arxiv.org/pdf/1905.06397> | blocking metrics, clustering | 2026-07-29 |
| <https://arxiv.org/abs/2008.04443> | pipeline stages, thresholds, small-sample evaluation | 2026-07-29 |
| <https://moj-analytical-services.github.io/splink/topic_guides/theory/fellegi_sunter.html> | thresholds and withhold band | 2026-07-29 |
| <https://arxiv.org/abs/2112.06331> | clustering refinement after transitive closure | 2026-07-29 |
| <https://en.wikipedia.org/wiki/Rule_of_three_(statistics)> | small-sample error bound | 2026-07-29 |
| <https://www.w3.org/TR/skos-reference/> | alias table structure | 2026-07-29 |
| <https://unicode.org/reports/tr15/> | lossy normalization | 2026-07-29 |
| <https://ucum.org/ucum> | unit canonicalization | 2026-07-29 |
