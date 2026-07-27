# Observability: Document Ingestion and Extraction
**Created**: 2026-07-27 | **Feature**: [spec.md](../spec.md)

## The Ingestion Report as a Published Artifact

- [ ] CHK001 Is the ingestion report's required content enumerated as a closed set, given SC-032 names five items while FR-053, FR-061, SC-005, SC-018, SC-041, SC-047, and SC-048 each oblige further content the same artifact must carry? [Completeness, Spec §SC-032 / §FR-053 / §FR-061]
- [ ] CHK002 Is the report's identity fixed at requirement level — its committed location, its form, and whether one exists per epic or per run — given Scope §Included names only "an ingestion report committed with this epic" and a path appears solely in plan §Project Structure? [Completeness, Spec §Scope Included / plan §Project Structure]
- [ ] CHK003 Is it stated which run's figures a published report carries, given FR-043 admits a run that ingests 3 of 51 documents while the other 48 documents' live rows belong to earlier runs? [Unambiguous, Spec §FR-043 / §FR-055]
- [ ] CHK004 Does a requirement oblige each published figure to name the run or generation it was computed over, and the report to name the run record it describes, given data-model records every figure as recomputable by query and none as stored? [Traceability, data-model §Scope "Not a table" / project-instructions §Principle I]
- [ ] CHK005 Is the disposition of a previously committed report stated when a re-run supersedes some documents' generations — replaced, appended to, or left standing beside figures it no longer describes? [Completeness, Spec §FR-043 / §FR-055]
- [ ] CHK006 Is the artifact that records the real-specification extraction exclusion named, given FR-022 and SC-012 require the exclusion to be "recorded" without stating where it is published? [Traceability, Spec §FR-022 / §SC-012]
- [ ] CHK007 Are the report obligations that exist only as success criteria — chunk counts against the 5,000–15,000 estimate (SC-005) and the separated valid, repaired, and failed counts (SC-018) — carried by a functional requirement, or do they rest on a criterion with no requirement behind it? [Traceability, Spec §SC-005 / §SC-018 / §FR-033]
- [ ] CHK008 Is the set of artifacts constituting a run's account closed at the ingestion-run row plus the report, so that no part of "what this run did" depends on output the repository does not retain? [Completeness, Spec §FR-038 / §SC-032]
- [ ] CHK009 Are the report's figures subject to a reproduction obligation with a stated tolerance, given the CI requirements make a reproduction job confirm published metrics before a release tag and FR-045 makes a full run replayable from committed fixtures? [Able to be validated, Spec §FR-045 / project-instructions §CI Requirements]

## Run Attribution and the Granularity of Agent Identity

- [ ] CHK010 Is the granularity of "agent identity" stated — a person, a software version, an SDD agent role, or a provider model — given FR-038 and SC-022 require the field and data-model constrains it only to non-empty text? [Precision, Spec §FR-038 / §SC-022 / data-model §ingestion_run.agent_id]
- [ ] CHK011 Is the resolution-mode value set closed at requirement level, given FR-038 names the field while only data-model fixes it to `record` and `replay`? [Completeness, Spec §FR-038 / data-model §ck_ingestion_run__resolution_mode]
- [ ] CHK012 Does FR-055's "mark each ingestion run active or superseded" agree with the per-document placement of that state, or does the requirement still assert a run-level property the data model records as unrepresentable under FR-043's skip rule? [Consistency, Spec §FR-055 / §FR-043 / data-model §ingestion_run "No status column"]
- [ ] CHK013 Is run attribution specified for the row kinds FR-039 does not name — contributing-chunk rows and line-item rows — or do they resolve to a run only by inference through a parent value? [Completeness, Spec §FR-039 / §SC-021 / data-model §Entities]
- [ ] CHK014 Is the at-least-one half of "exactly one ingestion run" stated as an obligation distinct from at-most-one, given data-model records the existence half as carried by no database mechanism? [Completeness, Spec §SC-021 / data-model §G-1]
- [ ] CHK015 Is the retention bound FR-055 requires to be stated given a value at requirement level, so a reader can tell how far back a run's outputs stay reconstructable, rather than the number appearing only in data-model §Operator Procedures? [Precision, Spec §FR-055 / data-model §Operator Procedures 3]

## Failure Taxonomy and What a Partial Run Reports

- [ ] CHK016 Are the seven per-field outcomes enumerated in this spec, or referenced only by count and by the three names that happen to appear in US3's acceptance scenarios? [Completeness, Spec §FR-034 / §SC-016 / §US3]
- [ ] CHK017 Is the run-level failure set closed at requirement level, given FR-056 names two conditions, data-model fixes five, and FR-005, FR-014, and FR-052 each abort a run without naming the kind their abort is recorded under? [Completeness, Spec §FR-056 / §FR-005 / §FR-014 / §FR-052 / data-model §ck_ingestion_run__failure_kind_domain]
- [ ] CHK018 Is disjointness stated as a property of the two outcome sets, rather than only as a prohibition on one recording path in FR-056? [Consistency, Spec §FR-056 / §SC-016 / data-model §VR-007]
- [ ] CHK019 Is it specified what a partially completed run reports about what it did and did not do — which documents committed, which rolled back, and which were never reached — given FR-042 guarantees only that no document is left half-ingested? [Completeness, Spec §FR-042 / §SC-042 / §FR-056]
- [ ] CHK020 Is the required content of a run-level failure's diagnostic detail specified — in particular whether it names the document in flight — given FR-035 fixes five fields for a per-field failure and FR-056 fixes none for a run-level one? [Completeness, Spec §FR-035 / §FR-056 / data-model §run_failure_detail]
- [ ] CHK021 For a field absent from an entire document, is the source chunk and attempted page such a failure record carries specified, given FR-058 records the absence once per document while FR-035 and SC-039 require both fields on every failure? [Unambiguous, Spec §FR-058 / §FR-035 / §SC-039]
- [ ] CHK022 Is the standing of an aborted run's already-committed generations stated at requirement level — whether downstream readers may treat them as active, and whether an aborted run is distinguishable from one in flight — given both carry no finish and only a failure kind separates them? [Consistency, Spec §FR-042 / §FR-055 / §SC-044 / data-model §finished_at]

## Published Figures and Their Honesty

- [ ] CHK023 Is publication of the denominator an obligation on the figure, given FR-060 and SC-047 require Wilson 95% intervals while the printed denominator appears only in plan §AD-011 and research? [Completeness, Spec §FR-060 / §SC-047 / plan §AD-011]
- [ ] CHK024 Is the Wilson variant fixed — continuity-corrected or not — given research records under-coverage at extreme proportions for very small denominators and requires the variant to be stated and used consistently? [Precision, Spec §FR-060 / research §Wilson score intervals]
- [ ] CHK025 Is "extraction quality figure" defined as a named set, so a reader can tell whether the repaired rate, the confidence distribution, and the near-duplicate counts fall inside FR-050's baseline-and-interval obligation or outside it? [Unambiguous, Spec §FR-050 / §SC-029 / §SC-018]
- [ ] CHK026 Is the criterion for labelling the baseline strong or weak stated, or is the label left to the author's judgement while FR-050 asserts in prose that a template extractor could plausibly win? [Able to be validated, Spec §FR-050 / §SC-029]
- [ ] CHK027 Is the census-versus-estimate distinction stated for the report as a whole, so a total check such as SC-002 or SC-013 is not published with an interval it does not need and no estimate is published without one? [Clarity, Spec §SC-002 / §SC-013 / §FR-011 / project-instructions §Principle II]
- [ ] CHK028 Is the method FR-011 requires for a sampled claim's error bound named, or is "a stated method" satisfiable by a method chosen after the inspection has happened? [Able to be validated, Spec §FR-011 / research §Validating parser page attribution]
- [ ] CHK029 Is layer labelling an obligation on every published figure rather than on extraction figures alone, given FR-053 requires a leaf-length distribution measured "across all 51 documents" while the Risks entry requires every extraction figure to be labelled by layer? [Consistency, Spec §FR-053 / §FR-060 / §Risks]
- [ ] CHK030 Is the form of the published confidence distribution specified — whether all eight scores FR-057's three signals admit appear with their counts, including scores no stored value took? [Precision, Spec §FR-033 / §FR-057 / §SC-017]
## Disclosed Shortfalls and Their Form

- [ ] CHK031 Are the data-model gaps this epic expects a reader to act on — G-1, G-4, and G-7 above all — recorded in the four-part form Principle VII fixes, given each carries a consequence and a reversal but no production-scale alternative? [Compliance, data-model §Disclosed Gaps / project-instructions §Principle VII]
- [ ] CHK032 Is the latent append-only enforcement's reporting obligation binding — that SC-024 is published as enforced by design and unenforced in deployment rather than as passing — or does that instruction exist only in plan §Open Items? [Traceability, Spec §Disclosed Limitations / §SC-024 / plan §Open Items G-6]
- [ ] CHK033 Is the HNSW index's absence after an aborted run — correct-but-slow retrieval with no signal — carried as a disclosed limitation of this epic, given the operator procedure that drops it is introduced here? [Completeness, data-model §Operator Procedures 1 / §G-7 / Spec §Disclosed Limitations]
- [ ] CHK034 Is the uncalibrated-confidence statement and its reversal trigger required beside the published distribution, or only in a limitations table a reader of the figures may never reach? [Consistency, Spec §Disclosed Limitations / §FR-031 / §SC-017]

## Counts Published Rather Than Silent

- [ ] CHK035 Is the count of documents skipped on a re-run a required published count, given FR-043 makes skipping the expected outcome and SC-025 measures only that zero rows were added? [Completeness, Spec §FR-043 / §SC-025]
- [ ] CHK036 Is the page-terminal fallback published as a list, a per-document count, or both, given FR-053 requires "the list of documents" and US1 scenario 10 requires every such document "with its count"? [Consistency, Spec §FR-053 / §US1 AS10 / §SC-041]
- [ ] CHK037 Is the similarity threshold FR-061 counts near-duplicate clusters against given a value, and is it fixed before the run in the way FR-032 fixes the confidence floor? [Precision, Spec §FR-061 / §SC-048 / §FR-032]
- [ ] CHK038 Is a breakdown of failure counts by outcome a required published count, so a run dominated by `no_value_found` is distinguishable from one dominated by `repair_budget_exhausted`? [Completeness, Spec §FR-034 / §SC-016 / §SC-018]
- [ ] CHK039 Is the per-boundary-class chunk count a required published figure, given SC-038 asserts that every boundary falls in one of three named classes without the share taken by each being visible? [Completeness, Spec §FR-012 / §SC-038 / §FR-053]
- [ ] CHK040 Is the count of multi-chunk values a required published count, given SC-020 requires only that at least one exists and the page-split irregularity is the reason the contributing-chunk record exists at all? [Completeness, Spec §SC-020 / §SC-019 / §FR-029]
