---
feature_branch: "00007-delivery-forecast-model"
created: "2026-07-27"
input: "e007"
spec_type: "product"
spec_maturity: "draft"
epic_id: "E007"
epic_sources: "{PRD:CAP-005}{SAD:ADR-0004}"
---

# Feature Specification: Delivery Forecast Model

## Problem Statement

A procurement coordinator managing open purchase-order lines has no way to say which will arrive late. The delivered dataset records what *happened* to lines that finished and how far along the open ones are, but nothing turns that into a statement about the future. Without one, every downstream capability that ranks, explains, or evidences delivery risk has nothing to work from — CAP-006's worklist, CAP-007's explanation view, and CAP-009's calibration evidence all read from a forecast that does not yet exist.

The consequence of doing nothing is not an absent feature but a misleading one: the natural fallback is to judge open lines by elapsed days, which systematically flatters the ones that have been open longest.

## Scope

### Included

- A hierarchical model over vendor and material category with partial pooling, fitted offline from the delivered procurement dataset.
- Right-censoring for lines still open at a recorded as-of date, so an unfinished order contributes what is known about it rather than a fabricated completion.
- Covariates for lifecycle state, days in state, and approval-cycle count.
- The **train/held-out split** of the procurement dataset — its per-line assignment, canonicalised and hashed, and its realized composition including the held-out uncensored event count.
- Per-line posterior artifacts: a canonical sorted draw array and a derived day-grid survival array, written together.
- A run manifest recording provenance, sampling shape, diagnostics and wall-clock, with an explicitly set active-run pointer.
- A blocking diagnostics gate with published thresholds: a fit that fails convergence writes nothing.

**Numbers claimed at epic start**, per Governance: migration block **`0300`–`0399`** and decision-record numbers from **`0018`**. Workspace `00007` matches the epic number. The migration block is claimed whether or not it is used — Wave 4 runs E007, E008 and E009 in parallel from one baseline, which is the exact collision the clause exists to prevent. If the block is used, `tests/checks/test_migration_ranges.py` hard-codes block ownership and must be updated in the same change; it currently declares only E003 and E004 and asserts the declared blocks tile without gaps.

### Excluded

- **The calibration verdict.** E014 owns the evaluation harness and publishes whether the forecast is calibrated. E007 produces the artifacts that make a verdict possible and asserts no coverage threshold of its own — asserting one here would duplicate E014's gate against a smaller sample and give two epics a vote on one number.
- **Freezing and hashing the evaluation set for E014's harness.** `specs/project-plan.md` assigns the split's construction here and its freeze-and-hash to E014. E007 emits the assignment in a canonicalisable, hashable form so E014 has something to freeze; it does not own the freeze gate.
- **Risk ranking and expected schedule harm.** E010 consumes the posteriors; ordering lines is its job.
- **Any request-time fitting.** Fitting is offline only. This is a registered architectural constraint, not a scope choice this feature may revisit.
- **The survival grid's horizon and the draw count.** `schema_constants.survival_horizon_days` (365) and `schema_constants.draw_count` (4000) are declared constants of E003's schema, and E003 records each as a scope decision with its own reversal trigger. A delivered test asserts the seeded `schema_constants` row carries them.
  - **What is *not* true, and was asserted here in an earlier revision**: nothing enforces either value against a run. E003's own constants table gives the enforcement as "none — carried per-run in `forecast_run.horizon_days` / `.draw_count`", and the delivered schema suite inserts runs at a fixture shape of 5 draws over a 3-day horizon and passes every constraint. The values are fixed by decision, not by a check.
  - **Consequence for Plan**: if E007 wants its runs pinned to the declared shape, E007 must assert that itself — the schema will not. Whether to add that assertion, and where, is a Plan decision this spec deliberately leaves open.

### Edge Cases & Boundaries

- **A vendor with five observations.** The smallest vendor in the dataset carries five lines, and the training split leaves it roughly four. Partial pooling is what makes a forecast produceable at all; the resulting estimate is mostly prior rather than data. The forecast must remain producible and its interval must widen. What must **not** happen is a vendor-level claim presented as if it rested on that vendor's data.
- **A vendor with zero or one training line.** The split stratifies on censoring status, not vendor, so a five-line vendor can land with almost no training data. The run must still record that vendor's shrinkage weight — which will sit near zero — rather than omitting the vendor or failing.
- **A held-out stratum that empties.** With roughly 24 censored lines, an unstratified draw could leave the held-out set with almost none, making the censored contribution untestable downstream. Stratifying on censoring status is the boundary this addresses.
- **A line whose elapsed time exceeds the grid horizon.** Mass beyond the horizon is recorded as residual tail mass rather than truncated.
- **A held-out line that delivered before the as-of date.** The survival grid is anchored at the as-of date, so a line that already delivered has no meaningful grid position under that anchor. Its predictive draws are still gradeable, and the anchor for such a line must be stated separately rather than assumed to be the same one.
- **A fit that does not converge.** No artifact, no pointer move, non-zero exit — the boundary values are the published thresholds below.
- **Zero open lines at the as-of date.** The run must refuse rather than write an empty forecast set that reads as an absence of risk.

## User Scenarios & Testing

### User Story 1 - Forecast every open line (Priority: P1)

A coordinator opens the worklist and every still-open purchase-order line carries a delivery-date distribution rather than a status. The forecast accounts for which vendor supplies the line, what material category it is, where it sits in its lifecycle, how long it has sat there, and how many approval cycles it has been through.

**Why P1**: This is the capability. CAP-005 states it directly, and E010, E012, E014 and E019 each depend on the stored posteriors existing. Nothing downstream of this epic can start without it.

**Independent test**: Fit against the committed dataset and confirm every open line has a stored posterior whose draws and survival array agree, joinable to one run.

**Acceptance scenarios**:

1. **Given** the committed procurement dataset and a recorded as-of date, **When** the fit job runs to completion, **Then** every line open at that date has a stored posterior carrying a sorted draw array, a day-grid survival array, a residual tail mass, and a draw digest.
2. **Given** a stored posterior, **When** its survival array is inspected, **Then** it is non-increasing, every value lies in `[0,1]`, its length equals the horizon recorded on its own run, and its final value equals the residual tail mass.
3. **Given** a stored posterior, **When** residual tail mass is recomputed independently from the draw array as the fraction of draws exceeding the horizon, **Then** it agrees with the stored value within the delivered residual-agreement tolerance.
4. **Given** a fitted run, **When** the model is inspected, **Then** a realized shrinkage weight is recorded for all twelve vendors, including any vendor with no training line.
5. **Given** the vendor with the fewest training observations and the vendor with the most, **When** their vendor-effect intervals are compared, **Then** the sparser vendor's interval is wider.
6. **Given** a completed run, **When** the manifest is read, **Then** it names which covariates entered the fit.

### User Story 2 - Account for orders that have not finished (Priority: P1)

Lines still open at the as-of date are not evidence of a short delivery — they are evidence that delivery took *at least* as long as they have been open so far. The fit must use them that way.

**Why P1**: This is what separates a forecast from an average of completed orders. Treating an open line's elapsed days as a finished duration biases every prediction downward, and the bias is invisible in the output — the numbers look reasonable and are wrong. It is P1 alongside Story 1 because a fit that ignored censoring would satisfy every one of Story 1's assertions.

**Independent test**: Fit the same input twice, once with the censoring contribution and once without, and compare the aggregate median forecast.

**Acceptance scenarios**:

1. **Given** the dataset at a recorded as-of date, **When** a line's lifecycle state is not terminal, **Then** it is marked censored and contributes the probability that its duration exceeds its elapsed time, not a completed-duration likelihood.
2. **Given** two otherwise identical fits, **When** one omits the censoring contribution, **Then** its aggregate median forecast over open lines is at least 10% shorter — a demonstrated ablation, not an assertion.
3. **Given** a stored run, **When** the censoring indicator is read, **Then** it was derived from lifecycle state at the recorded as-of date and stored, not re-inferred at read time.

### User Story 3 - Hold out data so the forecast can be graded (Priority: P1)

The dataset is split into a training portion the model learns from and a held-out portion it never sees. Held-out lines that have already delivered receive stored predictions, because a prediction whose true answer is known is the only kind that can be graded.

**Why P1**: CAP-009 requires published performance claims to be honest against a baseline. A model scored on lines it trained on produces a number that looks like evidence and is not. It is P1 rather than P2 because E014 cannot construct the evidence later — the split has to happen where the fitting happens, or the separation is not real.

**Independent test**: Confirm the stored split assignment covers every line exactly once, is hashable, that no held-out line influenced the fit, and that held-out delivered lines carry posteriors.

**Acceptance scenarios**:

1. **Given** the procurement dataset, **When** the split runs, **Then** every line is assigned to exactly one side, the assignment is recorded in a canonical order with its hash, and the split seed is recorded.
2. **Given** the split, **When** its composition is inspected, **Then** delivered and censored lines each appear on both sides, and each stratum's realized proportion matches the declared fraction to within one line.
3. **Given** a fitted run, **When** the training inputs are inspected, **Then** no held-out line contributed to the fit.
4. **Given** a completed run, **When** the manifest is read, **Then** it records the realized held-out fraction, the realized held-out **uncensored event count**, and the split assignment hash.
5. **Given** the run, **When** posteriors are counted, **Then** open lines and held-out delivered lines both have them, and the anchor convention used for each population is recorded.
6. **Given** a reproduction attempt whose split assignment hash differs from the recorded one, **When** it runs, **Then** it refuses and names the split as the thing that moved.

### User Story 4 - Refuse a fit that did not converge (Priority: P1)

A run whose sampler breaches any published diagnostic threshold writes no artifact, moves no active-run pointer, and exits non-zero. Its diagnostics are reported so a reader can see why.

**Why P1**: A badly-converged posterior still produces intervals that look entirely plausible — the failure is invisible downstream and every consumer would treat the numbers as sound. It is P1 because the alternative is shipping a forecast nobody can distinguish from a good one.

**Independent test**: Force a non-converging configuration and confirm no artifact is written and the previously active run is untouched.

**Acceptance scenarios**:

1. **Given** a fit breaching any threshold in Published Constants, **When** sampling completes, **Then** no run record and no per-line posterior is written, the existing active-run pointer is unchanged, and the job exits non-zero naming the breached diagnostic, its realized value, and the threshold.
2. **Given** a fit whose diagnostics all pass, **When** the job completes, **Then** every monitored diagnostic is recorded beside the threshold it was judged against, the monitored parameter set is named, and maximum-treedepth hits are recorded as non-blocking.
3. **Given** a run that refuses, **When** the forecast tables and split assignment are compared with their prior state, **Then** no row has been added or modified and the active-run pointer is unmoved.
4. **Given** an as-of date at which no line is open, **When** the job runs, **Then** it refuses rather than writing an empty forecast set.

### User Story 5 - Reproduce a published forecast (Priority: P2)

Given a run manifest, a later reader can re-run the fit and confirm the result matches within the published tolerance.

**Why P2**: The manifest and artifact hash are written under Story 1, so the *recording* is P1. Exercising the comparison end-to-end is what makes the claim checkable, and it can follow the first working fit without blocking any downstream epic. Not P3, because a reproduction claim nobody has run once is an assertion.

**Independent test**: Re-run from a recorded manifest and compare artifact hashes and summary statistics within tolerance.

**Acceptance scenarios**:

1. **Given** a completed run's manifest, **When** a fit is re-run with the recorded seeds, code revision and input hash, **Then** the artifacts agree within the published tolerance — never asserted as bitwise equality.
2. **Given** two runs of the same input, **When** their manifests are compared, **Then** any difference in library version, code revision or seed is visible without reading the artifacts.
3. **Given** a run whose recorded input hash no longer matches the dataset present, **When** reproduction is attempted, **Then** it refuses and names the input that moved.
4. **Given** the fit job, **When** its execution path is inspected, **Then** no request-time entry point can reach it and no model-provider call occurs on any path.

## Requirements

### Published Constants

Stated here rather than deferred, because four requirements below gate on them and a gate against an unpublished threshold is not a gate.

| Constant | Value | Basis |
|---|---|---|
| R-hat, maximum | 1.01 | Convergence convention at four or more chains |
| Bulk effective sample size, minimum | 400 | 100 × chain count at the four-chain minimum |
| Tail effective sample size, minimum | 400 | Same bar applied to the tail |
| Divergent transitions, maximum | **0** | Contested in general; zero is the defensible choice at this dataset size, and publishing a number is what makes the gate falsifiable |
| E-BFMI, minimum | 0.3 | Nominal threshold |
| Maximum treedepth hits | reported, **not** blocking | An efficiency concern, not a validity one |
| Reproduction tolerance | set in Plan and published before any reproduction claim is made | ADR-0009 makes the gate a published tolerance. The draw count is *declared* at 4,000 by E003 and recorded there as a scope decision, but no check binds a run to it — so Plan sets the tolerance against the shape E007 commits to, and committing to one is itself a Plan obligation rather than something the schema supplies |
| Residual-agreement tolerance | `schema_constants.probability_sum_tolerance` (delivered as `1e-9`) | A delivered constant of E003's schema and enforced by a delivered constraint, so this spec names it rather than restating a second value for one quantity |

### Functional Requirements

- **FR-001**: System MUST fit a hierarchical model over vendor and material category with partial pooling, reading procurement lines and lifecycle events from the delivered schema rather than any re-derived copy.
- **FR-002**: System MUST include covariates for lifecycle state, days in state, and approval-cycle count, and MUST record in the manifest which covariates entered the fit.
- **FR-003**: System MUST treat a line whose lifecycle state is non-terminal at the recorded as-of date as right-censored, contributing the probability that its duration exceeds its elapsed time.
- **FR-004**: System MUST store the censoring indicator and the as-of date it was derived from, and MUST NOT re-infer censoring at read time.
- **FR-005**: System MUST perform a train/held-out split of the procurement lines, **stratified on censoring status**, at a declared fraction of [NEEDS CLARIFICATION: 0.25 is E005's *assumed* cross-epic fraction, which its datasheet explicitly disclaims as "not a value this dataset observes or commits anyone to", and `specs/project-plan.md` assigns E007 the job of replacing that assumption with an observed value. Risk 1 shows 0.25 makes the PRD's published coverage band unmeetable. Adopt 0.25 anyway and record the miss, raise the fraction until the band is measurable, or move to cross-validation?], and MUST store the per-line assignment in a canonical order with its hash.
- **FR-006**: System MUST record the realized held-out fraction, the realized held-out **uncensored event count**, and the split assignment hash. The event count is the quantity a calibration band's precision depends on; recording only the fraction leaves that precision unknowable.
- **FR-007**: System MUST fit only on the training portion. No held-out line may influence the fitted parameters.
- **FR-008**: System MUST write a canonical sorted draw array for every line open at the as-of date and for every held-out line that has already delivered.
- **FR-009**: System MUST define the draw array's canonical order as a total order with deterministic tie-breaking and record the serialization used, because an artifact hash over an unspecified order is undefined.
- **FR-010**: System MUST derive a day-grid survival array from each stored draw array such that element `k` is the probability the line is still undelivered `k` days after the anchor, matching the delivered schema's `k = 1..horizon_days` indexing.
- **FR-011**: System MUST record residual tail mass — the probability beyond the grid horizon — rather than truncating it, and MUST compute it from the draws rather than copying the final grid element, so the two are independently checkable.
- **FR-012**: System MUST state the anchor convention for each population it stores posteriors for. The as-of date anchors open lines; a held-out line that delivered before the as-of date requires a separately stated anchor, or its grid has no meaning.
- **FR-013**: System MUST write each draw array and its derived survival array atomically, so no reader can observe a survival array whose draws disagree with it.
- **FR-014**: System MUST record in the run manifest: code revision and worktree cleanliness, input data hash, **the input's layer label and a reference to its datasheet**, all seeds (sampling and split), chain count, draw count, tuning count, library versions, artifact hash and its serialization, artifact schema version, model version, as-of date, horizon, roster hash, and wall-clock duration. The layer label is what lets a reader of a forecast know that every number descending from it is synthetic-derived.
- **FR-015**: System MUST set the active-run pointer explicitly, and MUST NOT leave it implied by recency.
- **FR-016**: System MUST monitor R-hat, bulk effective sample size, tail effective sample size, divergent transitions, and E-BFMI against the thresholds in Published Constants, and MUST name the parameter set they are monitored over.
- **FR-017**: System MUST **refuse to write any artifact** when any published threshold is breached: no run record, no per-line posterior, no change to the active-run pointer, and a non-zero exit naming the breached diagnostic, its realized value, and the threshold.
- **FR-018**: System MUST record maximum-treedepth hits as reported-but-not-blocking, distinguishing an efficiency warning from a validity failure.
- **FR-019**: System MUST record the realized per-vendor shrinkage weight for every vendor, so a reader can see how much of each estimate is that vendor's data rather than the population.
- **FR-020**: System MUST state, in a reader-facing artifact, the observation count below which it does not support a vendor-level claim, and MUST state that count rather than leaving it to the reader's judgement.
- **FR-021**: System MUST refuse to emit a run in which no line is open at the as-of date, rather than writing an empty forecast set that reads as an absence of risk.
- **FR-022**: System MUST express reproduction as agreement within a published tolerance, never as bitwise equality of draws, and MUST publish that tolerance before any reproduction claim is made.
- **FR-023**: System MUST refuse a reproduction attempt whose recorded input hash or split assignment hash does not match what is present, naming which one moved.
- **FR-024**: System MUST keep all probability arithmetic inside the deterministic-computation boundary, with no model-provider call on any path.
- **FR-025**: System MUST NOT fit at request time. Fitting is an offline job, and no request-time entry point may reach it.
- **FR-026**: System MUST NOT publish a calibration verdict or a coverage threshold of its own. It records the inputs a calibration verdict needs; the verdict belongs to the evaluation harness.
- **FR-027**: System MUST record each disclosed limitation in four parts — scope decision, supporting evidence, reversal trigger, and production-scale alternative — so a limitation carries the condition that would retire it rather than standing as an apology.
- **FR-028**: System MUST record that the held-out fraction it adopts leaves a realized uncensored event count, and MUST state whether that count supports the precision the registered coverage band claims. Any change to a published band MUST be pre-registered before coverage is computed; adjusting a band after seeing the result is prohibited.

### Key Entities

- **ForecastRun** — one fit. Provenance (code revision, input hash and layer, seeds, library versions), sampling shape, artifact hash and serialization, model version, as-of date, horizon, roster hash, wall-clock, active flag. The delivered `forecast_run` table supplies most of these.
- **LinePosterior** — one line's forecast under one run: canonical sorted draw array, derived day-grid survival array, residual tail mass, draw digest. Delivered by E003 as `line_posterior`.
- **SplitAssignment** — which side of the train/held-out split each line fell on, in canonical order with a hash, joinable to the run. Its realized composition, including the uncensored event count, is what makes a later calibration claim interpretable.
- **DiagnosticsSummary** — the convergence evidence for a run: R-hat, bulk and tail effective sample size, divergence count, E-BFMI, treedepth hits, each beside the threshold it was judged against, plus the monitored parameter set.

## Assumptions & Risks

### Assumptions

1. The delivered procurement dataset is the fit's only source of lines and events; nothing re-derives them.
2. **The delivered schema does not hold everything this spec requires recorded.** `forecast_run` and `line_posterior` carry the posteriors and most provenance, but there is no column for the split assignment, the held-out event count, any sampling diagnostic, the per-vendor shrinkage weight, or the covariate list. Where those land — new columns under E007's claimed migration block, or a committed artifact outside the database — is a Plan decision. This spec requires that they be recorded and joinable to the run; it does not decide where.
3. The as-of date for a fit is supplied explicitly, following the dataset's convention of committed dates over clock reads.
4. Roughly 24 lines are open and roughly 175 delivered at the committed dataset's censoring level; exact counts are realized, not assumed.
5. The modeling entry's existing dependencies cover the fit, so no new third-party dependency is introduced.

### Risks

1. **The registered coverage band may be unmeetable at this sample size** — *likelihood high, impact high*.
   - **Scope decision**: E007 publishes the realized held-out uncensored event count and states whether it supports the registered band's precision. It does not adjust the band.
   - **Supporting evidence**: `specs/prd.md` states that approximately 120 uncensored delivery events will be available after held-out splitting, bounding calibration precision, and justifies a 73–87% band around 80% coverage as the sampling error the dataset supports. A 0.25 held-out split of roughly 175 delivered lines yields about 44 events, at which the same interval is roughly ±12 points rather than ±7. The PRD is also internally inconsistent: ±4 does not follow from 120 events; ±7 does.
   - **Reversal trigger**: a held-out set large enough that the realized uncensored event count supports the published band, or a cross-validated coverage estimate that does.
   - **Production-scale alternative**: at production volume the held-out set is large enough that the band is measurable from a single split, and none of this arises.
   - **Amendment need — recorded here, not performed**: the conflict is between this spec's arithmetic and a *registered* document. `specs/prd.md` is canonical for scope; where a downstream artifact conflicts with it, the registered document wins and the downstream artifact is corrected — or the registered document is amended on the default branch. A feature branch may record the need and may not perform it, and it may not route the resolution to another feature branch either. **The need is: reconcile the PRD's ~120-event assumption and its 73–87% band with the realized held-out event count E007 will publish.** Whoever owns the PRD performs it.
2. **Twelve vendors weakly identify the between-vendor spread** — *likelihood high, impact medium*.
   - **Scope decision**: report per-vendor intervals with their shrinkage weights; make no claim about how much vendors differ overall.
   - **Supporting evidence**: at twelve groups the group-level scale is poorly determined, so scoring the model on recovering that spread would be unfair. Coverage of the per-vendor offsets against the dataset's own ground-truth record is the defensible check.
   - **Reversal trigger**: a dataset with materially more vendors.
   - **Production-scale alternative**: a real vendor population identifies the spread directly.
3. **The training split leaves the smallest vendor with about four observations** — *likelihood certain, impact medium*.
   - **Scope decision**: keep the vendor in the model, record its shrinkage weight, and state the count below which no vendor-level claim is made.
   - **Supporting evidence**: the upstream dataset's own datasheet already discloses that five observations cannot support a vendor-level tail claim. Its published shrinkage figure of 0.22 at n=5 is a property of *that dataset's generative constants*, not a measurement of this fit — E007 fits its own variance components with different covariates, so its realized shrinkage must be recorded rather than borrowed.
   - **Reversal trigger**: more lines per vendor, or a vendor-level claim that survives at the realized shrinkage.
   - **Production-scale alternative**: real purchase history gives every vendor enough observations to stand on.

## Implementation Signals

- `NEW-WORKER` — an offline fit job, invoked as a console entry point in the modeling entry alongside the existing corpus and procurement commands.
- `NEW-CONFIG` — the published diagnostic thresholds, the split fraction, the anchor conventions, and the reproduction tolerance.
- `NEW-ENTITY` — a stored split assignment, a diagnostics summary, and per-vendor shrinkage weights, each joinable to a run. None has a home in the delivered schema.
- `MIGRATION` — **possible, and the block is claimed for it**. E003 delivers the posterior tables but no storage for the split, the diagnostics, or the shrinkage weights. If Plan puts them in the database, migration block `0300`–`0399` is used and the migration-range check needs its block table extended.
- `EXTERNAL-SERVICE` — none. No provider call on any path.
- `BREAKING-CHANGE` — none.

## Success Criteria

### Measurable Outcomes

- **SC-001** [US1]: Every line open at the recorded as-of date has a stored posterior joinable to exactly one run.
- **SC-002** [US1]: Every stored survival array is non-increasing, lies within `[0,1]`, has length equal to the horizon recorded on its own run, and has a final element equal to the stored residual tail mass.
- **SC-003** [US1]: Residual tail mass recomputed independently from the draw array agrees with the stored value within the **delivered residual-agreement tolerance**, for every posterior. Named separately from the reproduction tolerance because they are different quantities and one phrase for both is how a gate ends up pointing at the wrong number.
- **SC-004** [US1]: A realized shrinkage weight is recorded for all twelve vendors, including any vendor with no training line.
- **SC-005** [US1]: Comparing the vendor with the fewest training observations against the vendor with the most, the sparser vendor's vendor-effect interval is the wider of the two.
- **SC-006** [US1]: The manifest names the covariates that entered the fit.
- **SC-007** [US2]: Every non-terminal line at the as-of date is marked censored, and the censoring indicator and as-of date are stored.
- **SC-008** [US2]: A fit omitting the censoring contribution produces an aggregate median forecast over open lines at least 10% shorter than one including it.
- **SC-009** [US3]: Every line is assigned to exactly one side of the split; the assignment is stored in canonical order with its hash, and the split seed is recorded.
- **SC-010** [US3]: Delivered and censored lines each appear on both sides of the split, and each stratum's realized proportion matches the declared fraction to within one line.
- **SC-011** [US3]: No held-out line contributed to the fitted parameters.
- **SC-012** [US3]: The manifest records the realized held-out fraction, the realized held-out uncensored event count, and the split assignment hash.
- **SC-013** [US3]: Held-out lines that have already delivered carry stored posteriors, and the anchor convention used for each population is recorded.
- **SC-014** [US4]: A run breaching any threshold in Published Constants writes no run record and no posterior, leaves the active-run pointer unchanged, and exits non-zero naming the breached diagnostic, its realized value, and the threshold.
- **SC-015** [US4]: After a refused run, no row in the forecast tables or the split assignment has been added or modified, and the active-run pointer is unmoved.
- **SC-016** [US4]: A passing run records every monitored diagnostic beside its threshold, names the monitored parameter set, and records treedepth hits as non-blocking.
- **SC-017** [US4]: A run with no open line at the as-of date refuses rather than emitting.
- **SC-018** [US5]: A fit re-run from a recorded manifest agrees with the original within the published tolerance, and the comparison is never expressed as bitwise equality.
- **SC-019** [US5]: A reproduction attempt against a moved input hash or a moved split assignment hash refuses and names which one moved.
- **SC-020** [US1]: The run manifest records code revision, worktree cleanliness, input hash, input layer label and datasheet reference, all seeds, chain count, draw count, tuning count, library versions, artifact hash and serialization, artifact schema version, model version, as-of date, horizon, roster hash and wall-clock, with the active-run pointer set explicitly.
- **SC-021** [US5]: No model-provider call occurs on any path of the fit job, all probability arithmetic sits inside the deterministic-computation boundary, and no request-time entry point can reach the fit.
- **SC-022** [US1]: The canonical draw order is a stated total order with deterministic tie-breaking, and the serialization is recorded — so the artifact hash is well defined rather than merely present.
- **SC-023** [US1]: A draw array and its derived survival array cannot be observed in disagreement by any reader, at any point during or after a run.
- **SC-024** [US3]: Every disclosed limitation carries all four parts, and the observation count below which no vendor-level claim is made is stated in a reader-facing artifact.
- **SC-025** [US3]: The realized held-out uncensored event count is published together with a statement of whether it supports the precision the registered coverage band claims.
- **SC-026** [US3]: No artifact this epic emits carries a coverage threshold, a calibration verdict, or a pass/fail judgement on forecast quality — checked as an absence over the emitted set, so the boundary with the evaluation harness is verified rather than intended.

## Glossary

| Term | Meaning |
|---|---|
| Right-censored | A line still open at the as-of date. Its true duration is unknown; what is known is that it exceeds the elapsed time so far. |
| Partial pooling | Estimating each vendor's effect using both its own lines and the population, so a vendor with few observations is pulled toward the average rather than trusted alone. |
| Shrinkage weight | How much of a vendor's estimate comes from its own data rather than the population. Near 1 means mostly its own; near 0 means mostly borrowed. |
| Held-out set | Lines deliberately withheld from fitting so a later claim about forecast quality can be checked against data the model never saw. |
| Uncensored event count | How many held-out lines actually finished. Only these can grade a forecast, so this — not the split fraction — governs how precise a calibration claim can be. |
| Day-grid survival array | The probability the line is still undelivered at each whole-day offset from an anchor date. Derived from the draws so the read path is an array index rather than a sort. |
| Residual tail mass | Probability lying beyond the grid's horizon, recorded rather than truncated. |
| Active-run pointer | The explicit marker of which fit downstream readers should use. |
| Divergent transition | A sampler failure indicating the posterior was not explored reliably. Treated here as a validity failure, not a warning. |
| Pre-registration | Fixing a threshold before the result it judges is computed, so a band cannot be widened to accommodate a number already seen. |

## Compliance Check

**Audited against**: `project-instructions.md` v1.2.4 (2026-07-26) · **Date**: 2026-07-27 · **Phase**: Specify · **Pass**: 2 · **Verdict**: PASS

Pass 1 returned FAIL with 4 CRITICAL, 5 HIGH, 4 MEDIUM, 3 LOW. All 16 were verified fixed against source in pass 2.

| Principle / Section | Verdict | Evidence |
|---|---|---|
| I. Traceable or It Does Not Ship | PASS | FR-014 manifest (code revision, worktree state, input hash and layer, seeds, versions, artifact hash and serialization, schema version, roster hash); FR-015 explicit active-run pointer; SC-020 |
| II. Uncertainty Is the Product | PASS | Draw arrays and survival arrays with residual tail mass, never a point date; FR-019 shrinkage weights; SC-005 vendor-effect intervals |
| III. Precision Over Recall Where a Mistake Is Silent | PASS | FR-017 refuse-on-breach; FR-021 zero-open-line refusal; FR-023 moved-hash refusal; FR-019 records a near-zero weight rather than omitting a vendor; SC-014, SC-015, SC-017, SC-019 |
| IV. Agent Output Style | PASS | Product-spec sections only; no preamble, no epilogue |
| V. The Model Extracts, Code Computes | PASS | FR-024 computation boundary and no provider call; FR-025 no request-time fitting; SC-021 |
| VI. Evaluate Before You Tune | PASS | FR-005 per-line assignment in canonical order with its hash; FR-007 training-only fit; FR-023 refuses a moved split hash; SC-009, SC-011, SC-012, SC-019. Freeze-and-hash for the harness sits with E014 per `specs/project-plan.md`, and Scope/Excluded records the boundary |
| VII. Publish the Miss | PASS | All three Risks in four parts; FR-027 requires the form and SC-024 checks it; FR-028 publishes the realized uncensored event count against the registered band and forbids post-hoc band adjustment; SC-025. Risk 1 records the `specs/prd.md` amendment need without performing it and without routing it to another feature branch |
| VIII. Honest Opponents | PASS | No performance claim published here; the only comparison is SC-008's censoring ablation. Baselines and the calibration verdict are E014's, per FR-026 and SC-026 |
| Technology Stack | PASS | Modeling-entry console entry point per ADR-0011; no new dependency (Assumption 5); no second datastore of record |
| Testing & Quality Policy | N/A | Test strategy and property-based coverage for the computation modules are Plan-phase obligations |
| Source Code Layout | PASS | Offline fit job in `/src/model`; specification artifacts under `specs/`, datasheets under `data/` |
| Data Provenance | PASS | FR-014 records the input's layer label and datasheet reference; SC-020 |
| Development Workflow | PASS | Branch `00007-delivery-forecast-model` matches `#####-feature-name`; the workspace resolves from it |
| Governance | PASS | Migration block `0300`–`0399` and decision records from `0018` claimed at epic start; workspace `00007` matches epic E007; offline-only fitting treated as a registered architectural constraint, not a scope choice; the PRD conflict is recorded for a default-branch amendment |

### Open items for Plan — none blocks this gate

- **HIGH** — FR-012's per-population anchor has no home in the delivered schema. `forecast_run.as_of_date` is documented as the single anchor for every line's grid in a run, `ck_schema_constants__anchor_convention` pins the convention to `run_as_of_date`, and `line_posterior` carries no per-row anchor while `survival` is NOT NULL. Plan must add per-row anchor storage under the claimed migration block, or store held-out delivered lines as draws only. Assumption 2's storage-gap list does not name this.
- **MEDIUM** — Published Constants justifies the ESS and R-hat thresholds at a four-chain minimum that no requirement imposes; FR-014 only records the chain count.
- **MEDIUM** — SC-002 and US1 scenario 2 assert exact equality between the survival array's final element and the stored residual tail mass; the delivered constraint is a `1e-9` tolerance and E003 explicitly forbids the exact-equality form.
- **MEDIUM** — Nothing requires the split assignment to be committed before the fit that consumes it, or reused unchanged across re-fits; and FR-028's pre-registration duty should extend to the held-out fraction FR-005 leaves open, since Risk 1 contemplates raising it to make a band measurable.
- **MEDIUM** — The `0018` decision-record claim is unbounded and duplicates E005's identical claim; E008 and E009 branch from the same Wave-4 baseline. Bound the range.
- **MEDIUM** — Extending `tests/checks/test_migration_ranges.py` with `0300`–`0399` alone leaves a gap at `0200`–`0299` and fails the partition assertion; declaring E005's unused block instead fails the block-populated assertion.
- **MEDIUM** — Nothing binds `forecast_run.draw_count` or `.horizon_days` to the declared constants. If E007 wants its runs pinned to 4,000 draws over 365 days, E007 must assert it; the schema will not.
- **LOW** — FR-028's pre-registration prohibition has no covering success criterion; SC-025 covers only the publish-and-state half.
- **LOW** — SC-008's ablation delta is stated without an interval.
- **LOW** — The reproduction tolerance is deferred to Plan while the Published Constants preamble says four requirements gate on values stated there.
- **LOW** — `data/procurement/datasheet.md` still records the split's ownership as unassigned, now stale against `specs/project-plan.md`. E005's artifact; E007's Plan should record the propagation.

**Unresolved marker**: FR-005's split fraction. Deliberate — `specs/project-plan.md` assigns E007 the replacement of E005's assumed 0.25 with an observed value, and the choice interacts with the coverage-band conflict in Risk 1.
