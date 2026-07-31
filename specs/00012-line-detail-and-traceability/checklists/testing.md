# Testing: Line Detail and Traceability

**Created**: 2026-07-30 | **Feature**: [spec.md](../spec.md)

## Requirement Testability and Decidability

- [ ] CHK001 Does FR-046 state the invariants the property-based tests must hold over mark allocation, quantile extraction, mass derivation and band alignment, so a property that holds trivially is distinguishable from one that constrains the function? [Testability, Spec §FR-046]
- [ ] CHK002 Does FR-046 define "runs in the merge gate" as an observable property of a named workflow, so a check that is present but deselected, skipped or marked expected-failure does not satisfy it? [Clarity, Spec §FR-046]
- [ ] CHK003 Is the test-first obligation expressed as evidence a reviewer can inspect — the ordering of named commits — rather than as an intention, given that the disclosed squash merge removes that evidence once the branch lands? [Testability, Spec §FR-046 / Plan §HINT-001]
- [ ] CHK004 Does any requirement state that the property-based tests must have been observed failing before the derivation module existed, so test-first is evidenced by a red run rather than inferred from file presence? [Completeness, Spec §FR-046, §SC-025]
- [ ] CHK005 Is the squash-merge limit on ordering evidence recorded with its scope decision, supporting evidence, reversal trigger and production-scale alternative, rather than as an implementation hint? [Completeness, Plan §HINT-001 / Spec §Recorded Limitations]
- [ ] CHK006 Does FR-024 name the basis on which its second condition is measured, or does the proxy declaration exist only in SC-010a, AD-010 and the contract's `span_check_basis` — so a check derived from the requirement alone would treat the proxy as the property itself? [Traceability, Spec §FR-024 / Plan §AD-010]
- [ ] CHK007 Does FR-031 state the comparison basis and tolerance under which a reconstructed covariate value counts as reconciled, so "cannot be reconciled" is decidable rather than left to an implementation's judgement? [Testability, Spec §FR-031]
- [ ] CHK008 Do FR-034 and FR-034a give a decidable predicate for the absence of a causal claim, or does compliance rest on a reader's reading of committed copy? [Testability, Spec §FR-034, §FR-034a]
- [ ] CHK009 Does FR-029 state the observation that would falsify it — a write count over named identity tables across an enumerated interaction set — rather than resting on the absence of a write route in the operation table? [Testability, Spec §FR-029]

## Success Criterion Measurability

- [ ] CHK010 Is each success criterion stated so that a failing run names the requirement that broke, given that SC-005, SC-010 and SC-018 each carry three or more conjuncts under a single identifier? [Testability, Spec §SC-005, §SC-010, §SC-018]
- [ ] CHK011 Does SC-003 name the population of lines and the set of figures compared, so "no case in which the two disagree" carries a denominator rather than an unbounded universal? [Measurability, Spec §SC-003]
- [ ] CHK012 Does SC-031 carry FR-015's decidable always-present form — no hover, focus, expansion, click or panel — or the weaker "reachable" phrasing that FR-015 was repaired to replace? [Consistency, Spec §SC-031 vs §FR-015]
- [ ] CHK013 Does SC-020's enumeration reach every location the response publishes a bounded percentage, including each cumulative band's `delivered_by`, or can a percentage outside the five named figures render as a certainty without failing any criterion? [Completeness, Spec §SC-020, §FR-041]
- [ ] CHK014 Does SC-026 bound the combinations it quantifies over — the arity as well as the operand set — so the check terminates and a failure names the operands that produced the bare figure? [Testability, Spec §SC-026, §FR-038]
- [ ] CHK015 Does SC-016 state the observation that decides "without any element having been added to a worklist row" against E010's three closed content classes? [Measurability, Spec §SC-016, §FR-035]
- [ ] CHK016 Does SC-029 name which order is observed for "follows the distribution in reading order" — document order, focus order or visual order — so two reviewers reach the same verdict? [Clarity, Spec §SC-029, §FR-007]
- [ ] CHK017 Is FR-025's claim that the 100% target was fixed before the first measurement backed by evidence that survives the merge, or does it share the post-merge evidence loss FR-046's ordering claim carries? [Testability, Spec §FR-025]
- [ ] CHK018 Does any criterion make FR-020a's reversal trigger observable — a document that breaks the latency envelope, or a supported viewer ignoring the page fragment — or is the shortfall recorded with no measurement that would report it widening? [Testability, Spec §FR-020a]

## Evidence That the Checks Run

- [ ] CHK019 Do the success criteria state which are dischargeable inside the merge gate and which require an acceptance run, so a criterion measured only outside the gate is not counted as gated evidence? [Completeness, Spec §Success Criteria / Plan §Testing Strategy]
- [ ] CHK020 Does any requirement forbid an environmentally skipped check from being reported as evidence, given that the corpus-page measurement runs only where `pdfplumber` is available? [Testability, Plan §AD-010 / Tasks §T034]
- [ ] CHK021 Does the gate-membership obligation cover every check the requirements mandate, or only the property tests and the detail conformance check the polish phase names? [Coverage, Spec §FR-046 / Tasks §T043]
- [ ] CHK022 Is every success criterion mapped to a tier or check in the Testing Strategy, so a criterion with no owning check is visible as unevidenced rather than assumed covered? [Coverage, Plan §Testing Strategy / Spec §Success Criteria]
- [ ] CHK023 Is there a stated convention binding an assertion to the requirement identifier it discharges, so a failing run names a requirement rather than only a file? [Traceability, Spec §Success Criteria / Tasks §Epic Capability Map]

## Evidence That the Checks Bite

- [ ] CHK024 Do the requirements demand that each structural claim be shown to reject a non-conforming body — a `miss` member present alongside `already_late`, or a central-summary member — rather than only to accept a conforming one? [Testability, Spec §FR-006, §FR-012]
- [ ] CHK025 Is the conformance validator's coverage of the constructs the contract uses stated as a requirement, so a contract that later adds a keyword the validator does not implement fails rather than reports green? [Completeness, Plan §Testing Strategy / Tasks §T003]
- [ ] CHK026 Do the requirements distinguish "the clause was evaluated" from "the response passed", given that `figures`, `encoding`, `identity_state` and `page_state` are reachable only through `allOf` and FR-006's withholding rule only through the root `if`/`then` pairs? [Testability, Spec §FR-011, §FR-012 / Contract §LineDetailResponse]
- [ ] CHK027 Is the `x-prohibited-members` name list bound to FR-003's and FR-012's enumerations by a stated obligation, so a prohibition added to a requirement cannot leave the machine-readable list behind? [Consistency, Spec §FR-003, §FR-012]
- [ ] CHK028 Do the requirements state that a central summary carried under a name absent from that list is still caught, so the prohibition does not depend on anticipating the name a regression would use? [Completeness, Spec §FR-012, §SC-007]
- [ ] CHK029 Is the near-tautological character of the `chunk.body_text` check stated as a reason the request-time figure alone cannot discharge FR-024's second condition, in a requirement rather than only in a plan decision row? [Clarity, Spec §FR-024 / Plan §AD-010]
- [ ] CHK030 Does any requirement state what a disagreement between the request-time proxy and the corpus-page measurement obliges — whether the published share remains publishable once the agreement that establishes the proxy fails? [Completeness, Spec §SC-010a / Plan §AD-010]
- [ ] CHK031 Does SC-010a define "published together" by a named artifact, its location and its contents, so the two measurements and their disagreement are discoverable by a reader rather than existing only as a passing check? [Clarity, Spec §SC-010a / Tasks §T034]

## Fixture-Evidenced Versus Corpus-Evidenced Claims

- [ ] CHK032 Do the requirements distinguish a claim evidenced against frozen fixtures from one evidenced against the corpus, given that `resolved_entity_member` is empty until E009 runs and every traversal check runs against fixtures? [Completeness, Spec §Assumptions / Plan §Integration Points]
- [ ] CHK033 Does SC-021 declare itself unjudged at an empty linked-record set, in the shape FR-025 fixes for a zero denominator, rather than passing vacuously in the state the spec calls the common path? [Testability, Spec §SC-021 vs §FR-025]
- [ ] CHK034 Do SC-008 and SC-009 state the precondition that linked records exist, so a run with none reports them unjudged rather than met? [Measurability, Spec §SC-008, §SC-009]
- [ ] CHK035 Is the obligation to re-verify the traversal after E009's identity-schema change stated as a requirement with an owner and a trigger, rather than as a mitigation cell in a risk table? [Completeness, Spec §Risks / Plan §AD-003]
- [ ] CHK036 Do the requirements state which of US2's acceptance scenarios are dischargeable before identity resolution first runs, so the story's independent test is not reported as passing on evidence the fixtures supplied? [Clarity, Spec §US2 / Tasks §Brownfield Notes]

## Coverage and Traceability of the Requirement Set

- [ ] CHK037 Does the Requirement Coverage Map name, for each requirement, the check that fails when it regresses, rather than only the component and file that implement it? [Traceability, Plan §Requirement Coverage Map]
- [ ] CHK038 Does every one of the 48 requirements reach at least one success criterion, so a requirement carrying task coverage but no criterion is visible as unmeasured? [Coverage, Spec §Requirements vs §Success Criteria]
- [ ] CHK039 Are the census invariants a polish task asserts — `has_interval` false, the single licensed reason, the run-scoped population, and `total_count` identical across two lines' responses — stated in a requirement, so removing the assertion removes evidence rather than the obligation? [Completeness, Spec §FR-024 / Tasks §T044]
- [ ] CHK040 Is SC-007's scope stated as this surface alone, so ADR-0025's absent repository-wide scan is visible as an open obligation rather than implied to be covered here? [Clarity, Spec §SC-007 / Plan §Open Items]
