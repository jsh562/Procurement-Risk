# Checklist: Observability — Requirements Quality

**Feature**: E009 Cross-Document Identity Resolution | **Domain**: Observability | **Depth**: Standard | **Audience**: Reviewer (PR) | **Date**: 2026-07-29

> These items test whether what the system must make **visible** is specified clearly and completely. They do not test implementation behavior. This feature exists because a wrong merge is silent (Principle III), so what it exposes is the product, not instrumentation around it.

## Negative evidence — gaps as data, not as absence

- [ ] CHK001 For unmatched manufacturer strings, is the required content specified — the raw string, occurrence count, which run — or only that they are "recorded"? [Completeness, FR-013, SC-030]
- [ ] CHK002 Is the retention scope of the unmatched-string record stated (per run, or accumulated), so a reader knows whether a gap closed or simply stopped being observed? [Clarity, FR-013, FR-039]
- [ ] CHK003 Is recording **every generated candidate pair** stated as a requirement in its own right, rather than as a by-product of scoring? Without it, a true pair's absence is unattributable. [Completeness, FR-012]
- [ ] CHK004 Is it specified that a gap in the alias table must be visible **as data** rather than inferred from missing merges, in a form a reader can query? [Clarity, FR-013, spec Edge Cases]

## Attributing a miss to the stage that caused it

- [ ] CHK005 Is the rule that a true pair absent from the candidate set is a **blocking** miss and not a scoring miss stated unambiguously, and is it stated once rather than in two places that could diverge? [Consistency, FR-010, SC-024]
- [ ] CHK006 Is blocking pair completeness required to be measured against ground truth sampled **independently of the blocking keys**, with the sampling frame published beside the figure? [Completeness, FR-010, SC-023, SC-032]
- [ ] CHK007 Is the prohibition on folding blocking losses into a scoring metric stated as a requirement, so PC and RR cannot be presented as part of precision? [Unambiguity, FR-011, SC-023]
- [ ] CHK008 Does a true pair that is withheld, rejected, or never blocked have a specified and *distinguishable* disposition, so all three do not collapse into one undifferentiated "miss"? [Completeness, FR-030, SC-024]

## Figure classification and interval discipline

- [ ] CHK009 Is the **explicit no-interval declaration** required for a census figure specified in its content, not just its existence? Without required content, an implementation satisfies it by silently omitting the interval — the exact failure the classification exists to prevent. [Completeness, FR-025, FR-038, plan AD-008]
- [ ] CHK010 Does every published figure name the stratum it draws on, and is a figure whose stratum is too small required to be reported **undefined with its denominator** rather than computed across strata? [Completeness, FR-037a, SC-037]
- [ ] CHK011 Is the interval's **method and sidedness** required to be published alongside the figure, not merely used? [Completeness, FR-026, SC-017]
- [ ] CHK012 Is the classification of each of the six published figures as estimate or census stated in the artifact, so a reader can tell which is which without re-deriving it? [Clarity, FR-038, SC-031]
- [ ] CHK013 Where merge precision is undefined (zero merges among labeled pairs), is the required published form specified — undefined **with the zero denominator shown** — and explicitly not zero and not omitted? [Unambiguity, FR-028, SC-013]
- [ ] CHK014 Is coverage required to appear in the **same statement** as merge precision, so precision cannot be read without the abstention rate that produced it? [Completeness, FR-029, SC-022]

## Publishing the miss

- [ ] CHK015 Where precision or recall falls below the registered target, is "published with its **cause**" specified to any degree, or is "cause" a word an implementation can satisfy with anything? [Clarity, FR-032, SC-026, project-instructions § Principle VII]
- [ ] CHK016 Is the prohibition on adjusting a target to match a result stated as a requirement of the run's output, not only as a project principle? [Completeness, FR-032, Principle VII]
- [ ] CHK017 Are the feature's four disclosed limitations each recorded with all four required parts — scope decision, supporting evidence, reversal trigger, production-scale alternative? [Completeness, spec Risks, Principle VII]
- [ ] CHK018 Is the specification-leg shortfall — E006 extracts zero values from the 26 real UFGS documents — published as a run-visible disclosure rather than only as spec prose? [Completeness, spec Problem Statement, plan Summary]

## The run manifest

- [ ] CHK019 Does the manifest's required content cover every input a published figure depends on — alias-table version, threshold constants, **weight vector**, both stratum hashes, E002 catalogue digest, input record counts? [Completeness, FR-034, FR-044, FR-047]
- [ ] CHK020 Is it stated that **every** published figure resolves to the manifest of the run that produced it, with nothing publishable that the manifest cannot account for? [Completeness, FR-034, Principle I]
- [ ] CHK021 Is the active-run pointer specified so a consumer selects a run **explicitly** rather than by recency, and is its behavior defined when no run is active? [Completeness, FR-039, SC-033]
- [ ] CHK022 Are the alias-table version and the E002 catalogue digest recorded as **two separate** facts, so editing the table without reseeding is detectable? [Clarity, FR-004, FR-040, SC-036]

## The review queue as an observable

- [ ] CHK023 Does a review item carry enough for a person to adjudicate **without re-running anything** — both source records, the score, the band, the per-attribute agreements, the alias-table version? [Completeness, FR-022, SC-012]
- [ ] CHK024 Is the withheld set's **yield** defined for the case where no adjudication data exists yet, rather than left as an undefined figure at first run? [Completeness, FR-031]
- [ ] CHK025 Is the review item's shape specified as extensible **additively** by E016, with the fields E009 writes named as the ones that must not change? [Clarity, FR-023]
- [ ] CHK026 Across runs, is a pair withheld twice required to be **groupable** by a stable identity, so the queue is readable rather than duplicative? [Completeness, FR-043, SC-039]
- [ ] CHK027 Are the withheld count and its share required as exact counts with their denominators and **no interval**, consistent with the census classification? [Consistency, FR-031, FR-038, SC-025]

## The rendered report

- [ ] CHK028 Is the report's required **content** specified, or only its path and existence? [Completeness, plan Project Structure, tasks T064–T067]
- [ ] CHK029 Does the report specify the baseline's figures beside the resolver's, so Principle VIII's comparison is visible in the artifact a reader actually opens? [Completeness, FR-046, SC-042]
- [ ] CHK030 Is the report required to distinguish the estimation stratum from the hard-negative stratum wherever a figure appears, rather than leaving the reader to infer it? [Clarity, FR-037a]

## Failure visibility

- [ ] CHK031 A refused run writes no manifest — is what the **operator** sees instead specified, rather than only what is absent from the database? [Completeness, FR-035, SC-015]
- [ ] CHK032 Can a run that **refused** be distinguished from one that never started? Both leave no manifest, and conflating them hides a guard breach as a scheduling gap. [Unambiguity, FR-035, FR-044, FR-047]
- [ ] CHK033 Is the failing guard required to be **named** in the refusal output, so a collision-guard breach and a hash mismatch are not reported identically? [Clarity, FR-007, FR-017, FR-035]
- [ ] CHK034 For a threshold or weight divergence, is the refusal required to report **both** constant sets, so post-hoc tuning is diagnosable rather than merely blocked? [Completeness, FR-044, FR-047, SC-040, SC-043]
- [ ] CHK035 Is an all-withheld run required to be reported as a **legitimate outcome** rather than a failure, with the review queue populated and precision undefined? [Unambiguity, FR-024, SC-013]

---

**Traceability**: 35 of 35 items carry a reference (100%).
**Recorded gap found while writing**: several requirements say a thing is "recorded" without specifying what — CHK001, CHK015, CHK024 and CHK028 each name one. These are the items most likely to fail evaluation, and that is their purpose.
