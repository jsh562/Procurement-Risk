---
feature_branch: "00009-cross-document-identity-resolution"
created: "2026-07-29"
input: "E009"
spec_type: "product"
spec_maturity: "clarified"
epic_id: "E009"
epic_sources: "{PRD:CAP-004}"
instructions_version: "1.2.7"
---

# Feature Specification: Cross-Document Identity Resolution

## Problem Statement

The same physical material appears three times in a project's paper trail under three different names — a specification section calls for a manufacturer's switchgear by catalog family, a submittal names the manufacturer's full legal entity with a model number, and a purchase-order line abbreviates both and adds a unit convention neither of the others used. A coordinator asking "has the switchgear I specified actually been ordered, and when does it land" cannot answer it without joining those three records by hand. Nothing downstream — the risk-ranked worklist, the evaluation harness, the line-detail trace — can attribute a forecast to a specification requirement while that join is missing. The cost of getting it wrong is worse than the cost of not doing it: a wrong merge silently attributes one material's delivery history to another, and no surface in the system displays anything that would reveal it.

## Scope

### Included

- Normalization of manufacturer names to canonical identities through a versioned alias table, and of quantity units to a declared canonical form.
- Candidate-pair generation that reduces the comparison space without discarding true pairs.
- Pairwise scoring over string similarity and attribute agreement, with a two-threshold decision producing merge, withhold, or reject.
- Clustering of merged pairs into resolved entities under a constraint that prevents unscored records being joined transitively.
- A review-queue record for every withheld pair, shaped so a later review workspace can be built on it additively.
- Published quality evidence for the resolution run — merge precision, recall, coverage, blocking pair completeness and reduction ratio, and the withheld set's size — each with its interval.

### Excluded

- **The review workspace itself.** E016 owns the interface where a coordinator adjudicates withheld pairs. This feature emits the records and the contract they satisfy; it renders nothing. Excluded because building a surface for a queue whose shape is not yet proven would fix the shape prematurely.
- **Comparative evaluation against baselines.** `specs/project-plan.md` scopes identity-resolution evaluation to E014, which owns the frozen evaluation harness and the baseline comparisons Principle VIII requires. This feature publishes its own figures with intervals; it does not claim to beat anything. Recorded rather than assumed — see Risks.
- **Learning from adjudications.** Nothing feeds a reviewer's decision back into the scorer. Excluded because a feedback loop needs an adjudication history that does not exist until E016 has run.
- **Resolution across projects.** Identity is resolved within a project, not across the five. Excluded because the same catalog number in two projects is two separate procurements, and merging them would corrupt both lead-time histories.
- **Manufacturer identity as a first-class registry.** The alias table maps strings to canonical names; it is not a vendor master with addresses, contacts, or corporate hierarchy. Excluded as outside CAP-004.
- **Blocking by embedding similarity.** Candidate generation uses deterministic keys only. Excluded because a vector-based block would make pair completeness depend on the embedding model and put a retrieval dependency in a path that does not need one.

### Edge Cases & Boundaries

- A specification section names a material with no part number at all — common, since specifications frequently describe performance rather than catalogue an item. It must still be blockable by manufacturer alias alone.
- A manufacturer appears under an alias absent from the table. The pair is not silently dropped; the unmatched alias is recorded so the table's gaps are visible rather than inferred from missing merges.
- Two records agree on manufacturer and part number but differ in a suffix encoding voltage, enclosure rating, or handing. These are different materials and must not merge — the case where normalization most plausibly destroys a distinction that mattered.
- A purchase-order line covers a material never specified, or a specification item is never ordered. Both are singleton clusters, not failures.
- Two records score above the merge threshold against a third but were never scored against each other. Under plain transitive closure they join anyway; the cluster constraint must prevent it.
- The labeled evaluation set contains a pair the blocking stage never generated. It is a blocking miss, not a scoring miss, and must be attributed to the stage that caused it.
- A record pair matches on both blocking keys and would be generated twice. It is one candidate pair, not two, or every published share has a double-counted denominator.
- A quantity unit is non-dimensional — each, lot, lump sum. It has no conversion to any other unit and comparing it dimensionally is meaningless.
- Every candidate pair falls in the withhold band. The run produces zero merges and a full review queue; this is a legitimate outcome, and precision over zero merges is undefined rather than zero.
- A pair scores exactly at a threshold. The boundary must belong to one band by definition, not by implementation accident.

## User Scenarios & Testing

### User Story 1 - Link the same material across specification, submittal, and purchase order (Priority: P1)

A coordinator looking at a purchase-order line can see which specification section required it and which submittal proposed it, because the three records have been resolved to one material identity despite naming it three different ways.

**Why this priority**: This is CAP-004's core value proposition and the join every downstream epic reads. E014 cannot evaluate resolution quality and E016 has nothing to review without it. Nothing else in this feature has meaning if the links are not produced.

**Independent Test**: Run resolution over a project's records and demonstrate that a known specification/submittal/purchase-order triple appears as one resolved entity, with each member traceable to its source record.

**Acceptance Scenarios**:

1. **Given** a specification section naming "Square D" and a purchase-order line naming "SQ D", **When** resolution runs, **Then** both resolve to one canonical manufacturer and the pair is scored rather than discarded.
2. **Given** two records whose part numbers differ only in a suffix encoding enclosure rating, **When** resolution runs, **Then** they are not merged, and the suffix difference is recorded as the attribute that separated them.
3. **Given** a specification record carrying no part number and at least one submittal record sharing its manufacturer, **When** candidate generation runs, **Then** the specification record enters a block on manufacturer alias alone and is scored against that submittal record.
4. **Given** a resolved entity, **When** its membership is inspected, **Then** every member record is traceable to its source document and location, and every pair the cluster implies carries a recorded score exceeding the merge threshold.
5. **Given** a specification quantity in millimetres and a purchase-order quantity for the same material in inches, **When** the pair is scored, **Then** the unit difference contributes to the score as a scored attribute and does not exclude the pair from consideration.
6. **Given** two records whose quantities are expressed in non-dimensional units — "EA" and "LS" — **When** the pair is scored, **Then** the units are compared for equality only and are not converted into a shared canonical form.

### User Story 2 - Withhold uncertain pairs instead of merging them (Priority: P1)

A pair the system is not confident about is held back and written to a review queue, so a coordinator sees an open question rather than a silently wrong answer.

**Why this priority**: Principle III names this feature specifically — where an incorrect result would be invisible, the system must bias toward refusal. A merge-everything resolver would satisfy US1's mechanics while corrupting the record, and nothing in the interface would reveal it. This is security-critical in the sense that matters here: silent corruption.

**Independent Test**: Present a pair engineered to score inside the withhold band and demonstrate it produces a review-queue record and no merge, with the queue record carrying enough evidence for a human to decide.

**Acceptance Scenarios**:

1. **Given** a candidate pair scoring between the reject and merge thresholds, **When** the decision is made, **Then** no merge occurs, a review item is written, and the item carries both records, the score, the band it fell in, and the attribute agreements that produced it.
2. **Given** a run in which every candidate pair falls in the withhold band, **When** the run completes, **Then** it reports zero merges and a fully populated review queue as a successful outcome, and reports merge precision as undefined with its zero denominator rather than as a figure.
3. **Given** a withheld pair, **When** the resolved-entity output is inspected, **Then** the two records appear in separate clusters — withholding is not a deferred merge.
4. **Given** a normalization rule that would map two records known to be distinct onto the same key, **When** the collision guard runs, **Then** the run writes no resolved entities, no review items, and no manifest.

### User Story 3 - Read the resolution's own quality evidence (Priority: P1)

A technical evaluator can see how good the resolution is, with intervals on every estimated figure, and can tell which stage caused a miss.

**Why this priority**: `specs/prd.md` gates both identity metrics at "P1 release", so the MVP slice must carry the registered gate's evidence or the gate is unmeetable at the release it names. Raised from P2 at clarification for that reason. It also breaks a circular dependency: `specs/project-plan.md` gives E014 the LabeledPair entity while E014 depends on E009, so the frozen set has to be authored here and consumed there.

**Independent Test**: Run resolution against the frozen labeled set and demonstrate a report carrying merge precision, recall, coverage, blocking pair completeness and reduction ratio, and the withheld set's size — each with its interval.

**Acceptance Scenarios**:

1. **Given** a completed resolution run, **When** the quality report is read, **Then** merge precision appears as a point estimate accompanied by a 95% interval, and the interval's method and sidedness are named.
2. **Given** a run with zero false merges among the merges made on labeled pairs, **When** the interval is computed, **Then** the rule of three is used with that merge count as its denominator, and the report discloses when that count falls below 30, where the approximation is not reliable.
3. **Given** a run with one or more false merges among labeled pairs, **When** the interval is computed, **Then** a one-sided exact binomial interval is used instead, because the rule of three does not apply once an error is observed.
4. **Given** a true pair the blocking stage never generated, **When** the report attributes the miss, **Then** it is counted against blocking pair completeness and against recall, and is not attributed to scoring.
5. **Given** a completed run, **When** any published figure is read, **Then** it carries an interval, and a figure with no defined interval is reported as undefined rather than bare.
6. **Given** a run whose merge precision or recall falls below its registered target, **When** the report is written, **Then** the shortfall is published with its cause rather than omitted or adjusted for.

### User Story 4 - Maintain the alias table as an auditable, versioned artifact (Priority: P3)

Someone maintaining the system can add a manufacturer alias, see which merges it would change, and know which alias-table version produced any historical resolution.

**Why this priority**: Future foundation. The MVP ships with a fixed table and never edits it, so nothing breaks without this. It earns its place because editing the table silently rewrites what past runs would have decided, and the version identifier that makes that visible costs nothing to record at the outset and cannot be back-filled later.

**Independent Test**: Change one alias, re-run resolution, and demonstrate that the run records a different alias-table version and that the affected merges are identifiable from the two runs' records.

**Acceptance Scenarios**:

1. **Given** an alias table in which one alias maps to two canonical manufacturers, **When** the table is loaded, **Then** loading fails with the offending alias named, rather than resolving the ambiguity at match time.
2. **Given** a completed resolution run, **When** any merge is inspected, **Then** it records the alias rule that fired and the alias-table version in force.
3. **Given** a manufacturer string matching no alias, **When** the run completes, **Then** the unmatched string is recorded, so the table's gaps are visible as data.

## Requirements

### Functional Requirements

#### Normalization

- **FR-001**: System MUST resolve every manufacturer string to a canonical manufacturer identity through an alias table, and MUST record which alias rule fired for each resolution.
- **FR-002**: The alias-to-canonical mapping MUST be a function. An alias resolving to two canonical manufacturers MUST fail table loading with the alias named, and MUST NOT be resolved by a runtime tie-break, a first-match rule, or a score.
- **FR-003**: Normalization MUST be additive: the raw string as it appeared in the source record MUST be retained alongside the normalized form, and no stage may discard the raw value.
- **FR-004**: System MUST record an alias-table version identifier on every resolution run, and every merge MUST reference the version in force when it was decided.
- **FR-005**: System MUST canonicalize dimensional quantity units to a declared base-unit form, and MUST classify non-dimensional procurement units — each, lot, lump sum — as arbitrary, comparing them only for equality after alias mapping and never converting them.
- **FR-006**: Unit agreement MUST contribute to a pair's score as a scored attribute and MUST NOT act as a hard filter that excludes a pair from consideration.
- **FR-007**: System MUST verify that no normalization rule maps two records known to be distinct onto the same key, tested over the labeled negative pairs, and MUST fail rather than emit a resolution when the guard is breached.

#### Candidate generation

- **FR-008**: System MUST generate candidate pairs such that a record missing either a canonical manufacturer or a part number still enters comparison, using manufacturer and part-number prefix as independent keys rather than requiring both.
- **FR-009**: A candidate pair MUST be identified by its unordered record pair. A record pair matching on more than one blocking key is one candidate pair, and MUST be counted once in every published denominator.
- **FR-010**: System MUST measure blocking pair completeness against ground truth sampled independently of the blocking keys, and MUST state the sampling frame with the figure.
- **FR-011**: System MUST publish blocking pair completeness and reduction ratio as figures separate from merge precision, and MUST NOT fold blocking losses into a scoring metric.
- **FR-012**: System MUST record every candidate pair it generated, so a true pair absent from the candidate set is attributable to blocking rather than inferred from a missing merge.
- **FR-013**: System MUST record every manufacturer string that matched no alias, so gaps in the alias table are visible as data rather than deduced from absent merges.

#### Scoring and decision

- **FR-014**: System MUST score each candidate pair on the agreement of its attributes, and MUST record each component's contribution to the total.
- **FR-015**: System MUST apply two thresholds producing three disjoint outcomes — merge, withhold, reject — and MUST assign each threshold's exact value to exactly one band, so no score is undecided or doubly decided.
- **FR-016**: Both thresholds MUST be calibrated against the frozen labeled set before any resolution run is published, MUST be recorded as committed constants, and MUST NOT be changed after observing a run's precision or recall.
- **FR-017**: System MUST freeze and hash the labeled evaluation set before threshold calibration, and MUST verify the hash on every run that reports against it.

#### Clustering

- **FR-018**: Every pair induced by a resolved entity's membership MUST carry a recorded score exceeding the merge threshold. A pair joined into a cluster without having been scored is a defect, not a weaker merge.
- **FR-019**: System MUST measure merge precision over the labeled pairs the emitted clusters induce, and MUST state that population with the figure.
- **FR-020**: System MUST resolve identity within a project and MUST NOT merge records across projects, even where manufacturer and part number agree exactly.

#### Withholding and review

- **FR-021**: System MUST write exactly one review item for every withheld candidate pair, and MUST NOT merge a withheld pair under any subsequent stage including clustering.
- **FR-022**: A review item MUST carry both source records, the pair's score, the band it fell in, the per-attribute agreements that produced the score, and the alias-table version in force.
- **FR-023**: The review-item record MUST be shaped so that a later workspace can add adjudication state without altering the fields this feature writes.
- **FR-024**: A run in which every candidate pair is withheld MUST complete and report zero merges with a populated review queue, and MUST NOT be reported as a failure.

#### Publication and provenance

- **FR-025**: Every published figure MUST carry an interval. A figure whose interval is undefined at the realized sample size MUST be reported as undefined together with its denominator, never as a bare point estimate.
- **FR-026**: System MUST publish merge precision as a point estimate accompanied by a 95% interval, naming both the method and its sidedness.
- **FR-027**: The interval method MUST be the rule of three when zero false merges are observed among labeled pairs, and a one-sided exact binomial interval otherwise. The denominator MUST be the number of merges made among labeled pairs, not the size of the labeled set and not the run's total merge count. Where that denominator falls below 30, the run MUST disclose that the rule-of-three approximation is outside its reliable range.
- **FR-028**: Where the merge count among labeled pairs is zero, merge precision and its interval MUST be reported as undefined with the denominator shown, and MUST NOT be reported as zero or omitted.
- **FR-029**: System MUST publish coverage — the share of candidate pairs auto-decided — in the same statement as merge precision, so precision cannot be read without the abstention rate that produced it.
- **FR-030**: System MUST publish recall as the share of true pairs that were auto-merged, over the true pairs in the frozen labeled set as denominator. A true pair that was withheld, rejected, or never generated by blocking MUST count as a recall miss.
- **FR-031**: System MUST publish the withheld set's count, its share of candidate pairs, and, where adjudication data exists, the share of withheld pairs that were true.
- **FR-032**: Where merge precision or recall falls below the target registered in `specs/prd.md`, the run MUST publish the shortfall with its cause, and MUST NOT adjust the target to match the result.
- **FR-033**: Every resolved entity MUST be traceable to its member source records and their document locations.
- **FR-034**: System MUST write a run manifest recording the alias-table version, the threshold constants, the labeled-set hash, and the input record counts, so any published figure resolves to the run that produced it.
- **FR-035**: A run that cannot satisfy FR-007's collision guard or FR-017's hash verification MUST write no resolved entities, no review items, and no manifest, rather than writing a partial result.
- **FR-036**: A true pair MUST be established by annotator judgment recorded in the frozen labeled set, over pairs sampled from the within-project pair space independently of the blocking keys. No stage may derive a true pair from a blocking key, since that would make blocking pair completeness true by construction.
- **FR-037**: The frozen labeled set MUST be balanced at approximately equal counts of true and false pairs, and MUST record its sampling frame alongside its hash so every published denominator is attributable.
- **FR-038**: A proportion estimated from the labeled set — recall and blocking pair completeness — MUST be published with a Wilson 95% interval. A census over the run's own candidate set — coverage, reduction ratio, and the withheld share — MUST be published as an exact count with its denominator and MUST NOT carry an interval, because there is no sampling uncertainty to express.
- **FR-039**: Resolution runs MUST be append-only and immutable. A later run MUST NOT alter or delete an earlier run's resolved entities or review items, and consumers MUST select a run through an explicit active-run pointer rather than by recency.
- **FR-040**: The alias table's initial contents MUST derive from E002's committed manufacturer catalogue, and the run manifest MUST record that catalogue's digest alongside the alias-table version.
- **FR-041**: A ResolvedEntity MUST contain at most one specification-section record. Purchase-order lines are unbounded within a cluster, because partial shipments and change orders legitimately produce several against one specified material.
- **FR-042**: Each threshold's exact value MUST belong to the more conservative adjacent band: a score exactly at the merge threshold is withheld, and a score exactly at the reject threshold is rejected.

### Key Entities

| Entity | Description |
|---|---|
| **ResolvedEntity** | A cluster of source records judged to describe one material. Carries its members, the canonical manufacturer, and the run that produced it. |
| **CandidatePair** | Two records that shared at least one blocking key, identified by the unordered record pair, with the score, per-attribute agreements, and the decision — merge, withhold, or reject. |
| **ReviewQueueItem** | A withheld pair written for human adjudication, carrying both records and the evidence behind the score. Shaped for E016 to extend. |
| **ManufacturerAlias** | A mapping from an observed string to a canonical manufacturer, with a version identifier and a class distinguishing display-worthy alternates from match-only variants. |
| **ResolutionRun** | The manifest binding a set of resolved entities and review items to the alias-table version, threshold constants, and labeled-set hash in force. |

## Assumptions & Risks

### Assumptions

- **The registered merge-precision target of 0.95 is a point estimate, not an interval lower bound.** This reading is what FR-026 and FR-027 encode, and it is the only reading under which the criterion is satisfiable at this sample size. It is an assumption this specification makes, not a settled question — see the amendment need in Risks.
- **No upstream epic records a material-identity link, and none is assumed.** E005 settles the manufacturer/part-number overlap as shared vocabulary with no foreign key, and E002 links generated submittals to the real specification layer at equipment-category granularity only. Ground truth for this feature is therefore annotator judgment over a sampled frame (FR-036), not a generator-emitted key.
- E006's extraction output carries manufacturer and part-number fields at a fidelity sufficient to block and score on.
- The alias table's initial contents derive from E002's committed manufacturer catalogue, which guarantees a canonical name, at least one alias spelling, and a part-number prefix per manufacturer. A distinct misspelling class is not guaranteed by that catalogue and is not assumed here.
- Resolution runs offline as a batch job over a project's records, not at request time.
- The 40 hand-labeled pairs are labeled by a single annotator, consistent with the product document's disclosure that no inter-annotator agreement is measured.

### Risks

- **The published precision criterion may be unreachable as written, and this branch may not correct it (likelihood: high, impact: high).** `specs/prd.md` and `specs/sad.md` both require merge precision "≥ 0.95 on 40 hand-labeled pairs, published with its rule-of-three error bound". Read as the *bound* clearing 0.95, this is arithmetically impossible at this sample size: the rule of three gives a 95% error-rate bound of 3/n, so even at zero errors with all 40 pairs merged the lower bound on precision is 0.925. A 0.05 bound needs at least 60 merges; on a balanced labeled set the realistic denominator is nearer 15–20, giving 0.85 or wider. A correct implementation would fail it.

  **Amendment need — recorded here, not performed.** Both documents are registered, and `project-instructions.md` § Governance reserves amendments to the default branch: a feature branch records the need and does not perform it, and may not route it to another feature branch. **Two distinct needs, both owed:** *(a)* state whether 0.95 is the point estimate or the interval's lower bound, and if the latter, reconcile it with the labeled-set size — this specification assumes the former and says so in Assumptions; *(b)* the registered criterion names only the rule of three, which is inapplicable once a single false merge is observed, so an estimator for the non-zero-error case must be named. FR-027 adopts a one-sided exact binomial interval for that branch, which is a divergence from both registered documents and is recorded here rather than silently taken. **Both needs name `specs/prd.md` and `specs/sad.md`; neither is performed on this branch.**

  Recorded as a four-part limitation per Principle VII — **Scope decision**: proceed on the point-estimate reading and publish the interval as a mandatory disclosure. **Supporting evidence**: 3/40 = 0.075 at the theoretical maximum denominator; the realized denominator is the merges made among labeled pairs, which is smaller. **Reversal trigger**: a labeled set of at least 60 merge-eligible pairs, at which a 0.05 rule-of-three bound becomes reachable and the interval reading becomes satisfiable. **Production-scale alternative**: label a sample sized from the interval width required rather than from annotator budget, and register the width as the target instead of the point estimate.

- **E009 depends on E006, which has no workspace (likelihood: high, impact: high).** E006 has claimed decision records ADR-0019 through ADR-0021 but has not been specified, so the extraction output this feature blocks and scores on does not exist. Nothing here is implementable until E006 lands. Mitigation: the specification is written against E006's declared contract from `specs/project-plan.md` rather than against a delivered schema, and the dependency is recorded rather than assumed away. Planning should not begin until E006's data model is readable.

- **The registered recall target has the same interval problem as precision, and it is not recorded upstream (likelihood: medium, impact: medium).** `specs/prd.md` sets identity-resolution recall at "≥ 0.80, explicitly secondary to precision". At roughly 20 true pairs in a balanced labeled set, a 95% Wilson interval around 0.80 spans approximately 0.58–0.92. Read as an interval lower bound the target is unreachable, exactly as the precision target is. This specification reads it as a point estimate (SC-018) on the same basis. **Amendment need — recorded here, not performed**: `specs/prd.md` and `specs/sad.md` should state whether 0.80 is a point estimate or a bound. Governance reserves that to the default branch.

- **This feature depends on E002 and the registered plan does not say so (likelihood: high, impact: low).** `specs/project-plan.md` records E009 as depending on E005 and E006. Clarification established that the alias table derives from E002's committed manufacturer catalogue (FR-040), which makes E002 a real dependency. **Amendment need — recorded here, not performed**: add the E002 edge to E009's dependency contract in the project plan. Nothing on this branch writes it.

- **The MVP slice did not carry the registered gate's evidence — resolved at clarification (likelihood: medium, impact: medium).** `specs/prd.md` gates merge precision and recall at "P1 release", while every publication requirement for those figures sat in US3 at P2. P1 therefore resolves identities and withholds correctly but publishes no quality evidence. Recorded rather than resolved by promoting US3: the evidence is only computable once merges exist, and re-priorit­ising it would make P1 depend on the frozen labeled set that E014 owns. **Resolved**: US3 was raised to P1, and FR-017 makes E009 the author of the frozen labeled set that E014 then consumes — which also breaks the circular dependency, since the project plan gives E014 the LabeledPair entity while E014 depends on E009. The MVP slice now carries the gate's evidence.

## Implementation Signals

- `NEW-ENTITY` — ResolvedEntity, CandidatePair, ReviewQueueItem, ManufacturerAlias, ResolutionRun.
- `MIGRATION` — new tables for resolved entities, candidate pairs, review items, and the run manifest. **Migration block `0400`–`0499` is claimed at epic start** per `project-instructions.md` § Governance, by scanning the highest block already declared: E003 `0001`–`0099`, E004 `0100`–`0199`, E005 `0200`–`0299`, E007 `0300`–`0399`. Wave 4 runs E007, E008 and E009 from one baseline and E006 is unstarted, which is exactly the collision the clause exists to prevent — the claim is recorded here so a later allocation scans against it.
- `NEW-CONFIG` — threshold constants and the alias-table version identifier, both committed rather than tuned at runtime. **Decision-record number `0022` is claimed at epic start**, scanning above ADR-0021, the highest in `specs/adrs/`. Anticipated to go unused; the claim stands regardless so a later need cannot collide.
- `NEW-WORKER` — an offline resolution job, consistent with the project's model-owned one-shot job pattern.

## Success Criteria

### Measurable Outcomes

- **SC-001** [US1]: A known submittal/purchase-order pair for one material resolves to a single ResolvedEntity with both members present. The specification member is associated at equipment-category granularity rather than merged, because the specification layer is real, verbatim-vendored text carrying no synthetic manufacturer or part number to match on — published as a limitation, not as a shortfall.
- **SC-002** [US1]: Records naming the same manufacturer through the alias spellings E002's catalogue guarantees — canonical name and at least one alternate — resolve to one canonical manufacturer. A misspelling class is matched where present in the catalogue and is not required to exist.
- **SC-003** [US1]: Two records whose part numbers differ only in a suffix encoding a material property are not merged, and the differing attribute is recorded on the pair.
- **SC-004** [US1]: A record carrying no part number, where at least one other record shares its canonical manufacturer, appears in at least one candidate pair.
- **SC-005** [US1]: Every pair induced by an emitted ResolvedEntity's membership carries a recorded score, and that score exceeds the merge threshold. A pair present in a cluster with no recorded score is a failure.
- **SC-006** [US1]: No ResolvedEntity contains records from more than one project.
- **SC-007** [US1]: Every ResolvedEntity member resolves to its source document and location.
- **SC-008** [US1]: Two records for the same material whose quantities use different dimensional units are scored, and the unit difference appears as a scored attribute rather than excluding the pair.
- **SC-009** [US1]: Two records whose quantities use non-dimensional units are compared for equality only, and no conversion factor is applied between them.
- **SC-010** [US1]: The raw manufacturer string from each source record is retrievable alongside its normalized form.
- **SC-011** [US2]: Every candidate pair scoring inside the withhold band produces exactly one ReviewQueueItem and no merge, where a candidate pair is counted once regardless of how many blocking keys generated it.
- **SC-012** [US2]: Every ReviewQueueItem carries both source records, the score, the band, the per-attribute agreements, and the alias-table version.
- **SC-013** [US2]: A run in which all candidate pairs are withheld completes successfully, reports zero merges, writes a review item per pair, and reports merge precision as undefined with its zero denominator.
- **SC-014** [US2]: Records in a withheld pair appear in separate ResolvedEntities.
- **SC-015** [US2]: A run whose collision guard or labeled-set hash verification fails writes no resolved entities, no review items, and no manifest.
- **SC-016** [US2]: Every threshold's exact value falls in exactly one band, demonstrated at both cutoffs.
- **SC-017** [US3]: Merge precision on the frozen labeled set is at least 0.95 as a point estimate, published together with its 95% interval and the interval's method and sidedness.
- **SC-018** [US3]: Recall is at least 0.80, published with its interval and its denominator, and reported as secondary to precision.
- **SC-019** [US3]: With zero false merges among labeled pairs, the published interval uses the rule of three with the count of merges made among labeled pairs as its denominator; with one or more, it uses a one-sided exact binomial interval.
- **SC-020** [US3]: Where the count of merges among labeled pairs is below 30, the run discloses that the rule-of-three approximation is outside its reliable range.
- **SC-021** [US3]: Merge precision is measured over the labeled pairs induced by emitted clusters, and that population is stated with the figure.
- **SC-022** [US3]: Coverage is published in the same statement as merge precision, each with its interval.
- **SC-023** [US3]: Blocking pair completeness and reduction ratio are published as figures distinct from merge precision, each with its interval and with the sampling frame stated.
- **SC-024** [US3]: A true pair absent from the candidate set is reported against blocking pair completeness and counted as a recall miss, and is not attributed to scoring.
- **SC-025** [US3]: The withheld set's count and its share of candidate pairs are published with every run, with intervals on the share.
- **SC-026** [US3]: A run whose merge precision or recall falls below its registered target publishes the shortfall together with its cause.
- **SC-027** [US3]: Every run writes a manifest recording the alias-table version, threshold constants, labeled-set hash, and input record counts, and every published figure resolves to it.
- **SC-028** [US4]: An alias table containing an alias mapped to two canonical manufacturers fails to load, and the error names the alias.
- **SC-029** [US4]: Every merge records the alias rule that fired and the alias-table version in force.
- **SC-030** [US4]: Every manufacturer string matching no alias is recorded and retrievable after the run.
- **SC-031** [US3]: Recall and blocking pair completeness are published with Wilson 95% intervals; coverage, reduction ratio and the withheld share are published as exact counts with denominators and no interval.
- **SC-032** [US3]: The frozen labeled set records its sampling frame and its balance of true to false pairs, and no true pair in it is derived from a blocking key.
- **SC-033** [US2]: A second resolution run leaves the first run's resolved entities and review items unaltered, and the active-run pointer identifies which run a consumer reads.
- **SC-034** [US1]: No ResolvedEntity contains more than one specification-section record; clusters containing several purchase-order lines are emitted without error.
- **SC-035** [US2]: A score exactly at the merge threshold is withheld and a score exactly at the reject threshold is rejected, demonstrated at both cutoffs.
- **SC-036** [US4]: The run manifest records the E002 manufacturer-catalogue digest from which the alias table derives, alongside the alias-table version.

## Clarifications

### Session 2026-07-29

- Q: What is a "true pair", given that no upstream epic records a material-identity link? -> A: Annotator judgment on a labeled set sampled from the within-project pair space independently of the blocking keys. SC-001's triple is restated as the submittal-to-purchase-order link the data can carry; the specification member is reduced to a category-level association and the reduction is published as a limitation.
- Q: What is the composition and sampling frame of the 40 hand-labeled pairs? -> A: Balanced, approximately 20 true and 20 false, sampled from the full within-project pair space independently of the blocking keys — the only frame under which blocking pair completeness measures anything.
- Q: FR-025 mandates an interval on every published figure, but only merge precision has a named method. How are the rest handled? -> A: Wilson intervals for proportions estimated on the labeled set (recall, blocking pair completeness); census figures over the run's own candidate set (coverage, reduction ratio, withheld share) are reported as exact counts with their denominators rather than with intervals. Recall's 0.80 is a point estimate on the same reading applied to precision's 0.95.
- Q: Who authors and freezes the labeled pair set, and does US3 rise to P1 with it? -> A: E009 authors and freezes it; E014 consumes that hash rather than minting a second. US3 rises to P1 so the registered "P1 release" gate has its evidence inside the MVP slice.
- Q: What happens when resolution runs a second time and disagrees with the first? -> A: Runs are append-only and immutable, with an explicit active-run pointer that downstream consumers read — the contract E003 and E007 already establish and `specs/sad.md` mandates.
- Q: Where does the alias table come from? -> A: Derived from E002's committed manufacturer catalogue, with the catalogue digest recorded as a run-manifest input alongside the alias-table version. E002 is added as a dependency, and SC-002 is weakened to the alias spellings E002 actually guarantees.
- Q: May a ResolvedEntity hold more than one purchase-order line, or more than one specification section? -> A: At most one specification-section record per cluster; purchase-order lines unbounded, because partial shipments and change orders make multiple lines against one specified material ordinary.
- Q: Which band owns each threshold's exact value? -> A: The more conservative neighbour — a score exactly at the merge threshold withholds, a score exactly at the reject threshold rejects.

## Glossary

| Term | Definition |
|---|---|
| **Blocking** | Restricting comparison to record pairs sharing a key, so the comparison space is reduced without scoring every possible pair. |
| **Candidate pair** | An unordered pair of records sharing at least one blocking key, counted once however many keys generated it. |
| **Pair completeness** | The share of true pairs that survive blocking into a shared block. A pair lost here cannot be recovered by any later stage. |
| **Reduction ratio** | The share of all possible pairs that blocking eliminates from consideration. |
| **Withhold band** | The score range between the reject and merge thresholds, in which a pair is neither merged nor rejected but routed to review. |
| **Coverage** | The share of candidate pairs the system decided automatically, rather than withholding. |
| **True pair** | Two records an annotator judged to describe the same material, recorded in the frozen labeled set. Established by judgment over an independently sampled frame, never derived from a blocking key. |
| **Attribute agreement** | The per-attribute comparison outcome — manufacturer, part number, description, quantity unit — contributing a recorded component to a pair's score. |
| **Recall** | The share of true pairs in the frozen labeled set that were auto-merged. A true pair withheld, rejected, or never generated by blocking counts as a miss. |
| **Census figure** | A figure computed over the whole of the run's own candidate set — coverage, reduction ratio, withheld share — carrying no sampling uncertainty and therefore no interval. |
| **Induced pair set** | Every pair implied by a cluster's membership, including pairs whose records were never directly compared. |
| **Rule of three** | An approximation giving a 95% upper bound of 3/n on an error rate when zero errors are observed in n trials. Applies only at zero errors, and is unreliable below n = 30. |
| **Arbitrary unit** | A unit with no defined relation to any other — each, lot, lump sum — which cannot be converted or dimensionally compared. |
