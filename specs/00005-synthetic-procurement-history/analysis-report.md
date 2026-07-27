# Analysis Report: Synthetic Procurement History

**Feature**: `00005-synthetic-procurement-history` (E005) | **Date**: 2026-07-26 | **Mode**: Analysis, then remediation
**Governing document**: `project-instructions.md` **v1.2.3**
**Artifacts**: `spec.md` (37 FR, 33 SC), `plan.md` (10 AD, NC-1…NC-12), `data-model.md` (27 DV), `tasks.md` (83 tasks), `research.md`, two fully-evaluated checklists

First analysis pass for this feature — no prior report to supersede, and no earlier finding-ID series is cited anywhere in the workspace, so the `A-###` series below starts clean.

## Findings

| ID | Category | Severity | Location(s) | Summary | Recommendation |
|----|----------|----------|-------------|---------|----------------|
| A-001 | Cross-artifact contradiction | **HIGH** | `spec.md` FR-006/SC-004; `data-model.md` §Rework, DV-009 | FR-006 requires the rework allocation be "**declared** rather than drawn" and SC-004 asserts realized "**equals the declared one exactly**", but `data-model.md:360` still specifies "**Drawn per line from the line's own stream**". Against a per-line draw SC-004 is unsatisfiable except by luck, and DV-009 asserts only that the histogram is *recorded* | Move the data model to a declared allocation and publish the integer loop vector, as FR-004's analogue already does; tighten DV-009 from record-only to equality |
| A-002 | Untestable criterion | **HIGH** | `spec.md` FR-006, SC-004 | SC-004 asserts realized equals *declared*, but nothing pins the declared count to FR-006's 30%. A declared count of 10 looped lines satisfies it exactly. No rounding rule exists for 70/22/8, so the split is not checkable at any realistic N, and the three-loop stratum — the reason the split exists — is unprotected | Pin the declared count to `round(0.30 × N)` and state a rounding rule that protects the three-loop stratum; assert both in SC-004. Stays exact; invents no tolerance |
| A-003 | Untestable criterion | **HIGH** | `spec.md` SC-017, FR-016 | SC-017 reads "100% of **its recorded** limitations carry all four parts" — a self-selecting denominator. A datasheet recording one limitation passes, so FR-016's minimum set of eight is unverifiable, including the corpus-field gap a compliance FAIL was closed on | Add a completeness leg: the eight limitations FR-016 names must each be present |
| A-004 | Stale compliance record | **HIGH** | `plan.md` § Compliance Result | The recorded PASS predates the checklist amendments and is now half-current: denominators and "Carried into tasks" were refreshed, the Testing & Quality Policy evidence row, the findings history and the Source Code Layout row were not. Nothing signals which half a reader can trust | Re-run the gate and replace the block wholesale rather than patching rows |
| A-005 | Incomplete classification | **HIGH** | `plan.md` § Testing Strategy | `equipment.py` is excluded from the mandatory tier on the grounds that "a mislabel is caught by DV-014's share floor", but clause 3 admits a module unless a wrong value is caught by a delivered constraint, a schema, or a downstream parse — a DV rule is none of those, and admitting one empties the tier. The exclusion is also directionally wrong: a floor catches an under-permissive predicate, never an over-permissive one | Move `equipment.py` to the Property tier, or restate clause 3 — but restating reaches `criticality.py` and `allocate.py` too, so the table would need re-deriving |
| A-006 | Duplication (true) | MEDIUM | `spec.md` SC-007, SC-027 | Both assert the category-adjusted ratio inside 0.12–0.49. SC-027 adds the decomposition and fail-clause; SC-007 uniquely adds the 0.24 target and absolute spreads. A future band change lands in one and they disagree | Merge into SC-007 carrying SC-027's additions across |
| A-007 | Duplication (true) | MEDIUM | `spec.md` FR-028, FR-033 | Both own the same datasheet sentence about unassigned split ownership. FR-033's distinct obligation — the 0.25 assumption — has no criterion at all | FR-028 keeps "no split artifact emitted"; FR-033 owns both disclosure legs and gains a criterion for the 0.25 record |
| A-008 | Coverage gap | MEDIUM | `spec.md` FR-009, SC-012 | FR-009's distinctive obligation — both dates committed as literals, and the explicit prohibition on defaulting to the generation run date — is asserted by nothing. SC-012 varies process, checkout path, hash seed, time zone and locale, but **not the wall-clock date**, which is the one dimension a run-date default breaks | Add a clock-date dimension to SC-012, or a criterion asserting both dates are literals in the emitted envelope |
| A-009 | Coverage gap | MEDIUM | `spec.md` FR-013 | The six-part canonicalization rule set and the JSON-float prohibition have no criterion. A float-carrying fixture reproduces its own hash inside a pinned environment, so SC-012 cannot catch it — the prohibition exists precisely because the failure is cross-environment | Add a criterion asserting the emitted payload contains no JSON float |
| A-010 | Coverage gap | MEDIUM | `spec.md` FR-012, SC-005, SC-015 | No criterion asserts realized criticality conforms to the disclosed table. A generator drawing bands independently and publishing a plausible table passes both existing criteria. FR-032 already carries the needed clause ("the predicate MUST be the same one the criterion is scored against") | Give FR-012 the same clause and assert conformance |
| A-011 | Coverage gap | MEDIUM | `spec.md` SC-005, SC-028; `data-model.md` DV-006 | Nothing requires all five criticality bands to occur. A dataset using bands 2 and 3 only passes everything, in an epic whose criticality feeds the downstream worklist. `data-model.md` DV-006 does require it — an obligation with no requirement above it | Require all five bands to occur, mirroring FR-010's non-empty-state wording |
| A-012 | Untestable criterion | MEDIUM | `spec.md` SC-027 | The fail-branch — unadjusted in band while adjusted is out — is a conditional the actual run will not enter, so it is asserted and never demonstrated. SC-006 and SC-032 both already require their branches be exercised | Require the branch be demonstrated, mirroring SC-006's "both binding regimes MUST be exercised" |
| A-013 | Untestable criterion | MEDIUM | `spec.md` SC-019 | "None of those values is reachable by a query against the loaded database" quantifies over all queries — the identical open-negative shape SC-021 was rewritten to remove | Rewrite as a bounded assertion over the schema's fixed column list |
| A-014 | Ambiguity | MEDIUM | `spec.md` FR-006 | "Declared" carries two meanings in one requirement — declared-not-*drawn* (allocation) and declared-not-*cited* (provenance of the rate). This collision is what produced A-001 | Disambiguate both usages explicitly |
| A-015 | Ambiguity | MEDIUM | `spec.md` FR-012 | "Quantized into terciles" does not say over which population — global or within-category — and the choice materially changes band assignment. `data-model.md` settles it; the spec does not | State the population in FR-012 |
| A-016 | Incomplete enumeration | MEDIUM | `plan.md` NC-11, A1-cont | NC-11 demonstrates the prohibition for SC-026 only, while A1-cont makes FR-034's printed row an obligation of equal standing. `tasks.md` T078 silently covers both, so the task is right and the enumeration the plan says exists "so completeness can be checked" is the incomplete thing | Split NC-11 into two demonstrated cases, as NC-4 already precedents |
| A-017 | File-manifest gap | MEDIUM | `plan.md` § Project Structure | The Build-gating tier and five NC rows require a CI change, and T081/T083 modify `.github/workflows/verify.yml`, but the plan's own modified-file manifest omits it | Add the workflow to the modified-file list |
| A-018 | Unverifiable criterion | MEDIUM | `spec.md` SC-015 | "Every per-transition duration assumption recoverable from the datasheet alone" — no enumeration, and "recoverable" is a reader judgement. US4 AS1 enumerates four of FR-007's six disclosure items | Enumerate FR-007's six items in SC-015 |
| A-019 | Reverse-trace orphans | MEDIUM | `spec.md` SC-031 | Asserts three properties no requirement states: purchase-order grouping by project and vendor, `occurred_at` monotonicity with sequence number, and no event later than the as-of date | Give each a parent requirement or move them under FR-024/FR-029 |
| A-020 | Derivation gap | MEDIUM | `spec.md` FR-008, FR-035, FR-036 | FR-008's 0.12–0.49 band is derived from a shrinkage identity with **no category term**, yet FR-036/SC-007 now assert the *category-adjusted* ratio against that same band. Removing category variance raises the adjusted ratio, so the stated derivation no longer strictly covers the measured quantity. FR-035 also fixes no magnitude for the category offset | Re-derive the band for the adjusted quantity or record why the interval still applies; bound the offset |
| A-021 | Estimator unspecified | MEDIUM | `spec.md` FR-036, SC-027 | "Net of the material-category component" names no estimator; order of entry versus sum-of-squares changes the number asserted against a band | Name the estimator, or state that the artifact records it |
| A-022 | Stale self-report | LOW | `spec.md` § Open, not resolved | States SC-004 "still carries no tolerance that can fail" and quotes "approximately 30%", which SC-004 no longer says. Stale as to text, accidentally correct as to substance (A-002) | Reword to the surviving gap |
| A-023 | Spec MUST unread by the design | LOW | `spec.md` FR-002; `data-model.md`, `plan.md` | FR-002 says the roster hash is recorded "on every generated line"; the design records it once in the envelope and stamps it per row at load. Defensible under the storage-boundary clause and openly disclosed, but it is a reading of a MUST that no spec text records | Record the reading in FR-002 |
| A-024 | Case inconsistency | LOW | `plan.md` AD-008, § Compliance Result vs A1/A3 | AD-008 renders lowercase `blocked`/`withdrawn`; A1/A3 require literally `BLOCKED`/`WITHDRAWN` and make the string assertable. A check written from AD-008 fails against A1 | Use the uppercase literals throughout |
| A-025 | Guard not assertable as described | LOW | `plan.md` § Reporting Obligations; `tasks.md` T078 | The denominator drift guard promises to fail "on an arithmetic mismatch", but T078 asserts the literal constants 33 and 37 and nothing counts IDs. Adding FR-038 leaves the check green and the printed denominator wrong — the exact failure that just occurred when the counts moved 26 → 33 | Derive the expected denominators by counting IDs in `spec.md` |
| A-026 | Unstated MUST | LOW | `spec.md` FR-003 | "MUST allocate near-evenly at roughly 40 lines each" is cancelled by its own next sentence ("carries no statistical requirement beyond being stated") — a MUST nothing can breach | Restate as a record-only obligation |
| A-027 | Vague adjective in a criterion | LOW | `spec.md` SC-002 | "Span roughly 5 to 35 lines" — the bite is "follows the declared vector"; the roughly-clause belongs in FR-004's rationale | Drop the roughly-clause from the criterion |
| A-028 | Cross-reference error | LOW | `spec.md` FR-035 | Attributes the per-category expected duration to "FR-012's", while FR-035 itself defines it | Correct the attribution |
| A-029 | Partial coverage | LOW | `spec.md` SC-033, SC-008 | SC-033 omits FR-027's "exits non-zero" and "names both digests"; SC-008 omits FR-023's "without altering either table's definition" | Extend both criteria |
| A-030 | Uncovered obligations | LOW | `spec.md` FR-001, FR-015 | FR-001's "MUST NOT restate any project or vendor identity in its own source" (SC-001 checks membership, not non-restatement) and FR-015's Distribution-section licence-basis line have no criterion | Add coverage or record as accepted |
| A-031 | Unnamed observation surface | LOW | `plan.md` NC-12/A4; `tasks.md` T077 | The unblocking detector names no artifact to read; it is `data/corpus/synthetic/field-label-vocabulary.json` | Name the file |
| A-032 | Integration obligation not raised | LOW | `data-model.md` G-1; `plan.md` | G-1 instructs "raise in the plan" for E009's tag-normalisation obligation; the plan does not, and it reaches the reader only as datasheet limitation L-9 | Raise it in the plan |

## Quality Summaries

**Spec Quality** — **FAIL, 22/25.** No structural defects. FR-001…FR-037 and SC-001…SC-033 contiguous with no ID reused, zero clarification markers, every SC names an existing `[US#]` parent, every P1 story carries criteria and a priority rationale, all five STF findings resolved. The three failing rubric items — requirements testable, criteria measurable, requirements covered — are all localised, and every one is an instance of the same family: **a criterion that records a value without being able to fail on it.** That family has now survived a clarify pass, an adversarial stress-test and two checklist evaluations, which is itself the most useful finding in this report.

**Compliance** — **FAIL** against v1.2.3: 3 HIGH, 2 MEDIUM, 4 LOW, **0 CRITICAL**. No `project-instructions.md` MUST is breached; the failures are breaches of the plan's own stated rules and of cross-artifact consistency. Principles I, III, V, VI, VIII, Technology Stack, Source Code Layout, Data Provenance and Governance all PASS. II is correctly N/A. VII and the Testing & Quality Policy fail, on A-001/A-003/A-004 and A-005 respectively. The reporting arithmetic was verified independently against the spec and is correct: printed 37/33, completion 36/32, and no path lets a blocked item render as a pass.

## Coverage Summary

| Dimension | Result |
|---|---|
| Requirement → task | **37/37**, every FR carries at least one `{FR-###}` tag |
| Requirement → plan coverage map | **37/37** |
| Completion points | 20 FRs span 3+ tasks; **20/20** carry `[COMPLETES FR-###]` |
| Cross-phase export edges | **6/6** `← T###:Symbol` edges match a `→ exports:` on the named task |
| Tasks with no requirement tag | 10, all in Setup, Foundational or Polish — permitted |
| DV rules placed | 27/27 at the tier `data-model.md` assigns |
| Negative controls placed | 12/12 |
| Migration tasks | 0, correct — this epic writes rows only |
| Tasks attempting the gated FR-034 | 0 |

## Instructions Alignment Issues

A-004 (stale compliance record) and A-005 (incomplete deterministic-computation classification) are the two that bear on `project-instructions.md` directly — the first on Governance's re-run expectation, the second on the Testing & Quality Policy's mandate. Neither is CRITICAL: the Policy's literal mandate over "risk arithmetic, fusion ranking, and scoring functions" *is* discharged, since `criticality.py` is in the mandatory tier with a red-green pair. A-005 is a breach of the plan's own admission rule rather than of the Policy.

## Unmapped Tasks

None. The 10 tasks without a requirement tag are all Setup, Foundational or Polish, which the workflow treats as permitted phases.

## Metrics

- **Requirements** 37 · **Criteria** 33 · **Tasks** 83 · **Coverage** 100% · **Findings** 32 — CRITICAL 0, HIGH 5, MEDIUM 16, LOW 11
