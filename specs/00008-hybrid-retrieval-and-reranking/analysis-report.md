# Analysis Report: E008 Hybrid Retrieval and Reranking

**Date**: 2026-07-29 · **Audited against**: `project-instructions.md` **v1.2.8** (current, no drift)
**Artifacts**: `spec.md` (46 FR, 16 SC), `plan.md` (15 AD), `tasks.md` (95 tasks), `contracts/openapi.yaml`,
three evaluated checklists, `specs/adrs/0022-*.md`
**Verdict**: **FAIL** — 6 CRITICAL, 6 HIGH, 6 MEDIUM, 4 LOW · **remediated in the same run**
(`apply all`); see §Remediation Applied. The verdict records what the detection passes found, not
the state of the artifacts now.

The Spec Validator terminated twice on server-side 500 errors; its three dimensions — duplication,
ambiguity, underspecification — were assessed directly instead and are reported as F-19 to F-21.

## Findings

| ID | Category | Severity | Location | Summary | Recommendation |
|----|----------|----------|----------|---------|----------------|
| F-01 | Testing policy | **CRITICAL** | `tasks.md:6`, `plan.md:9`, T024–T030 | The policy names **fusion ranking** a module requiring strict test-first **and** property tests. The SQL-residence argument answers only the property limb and was extended to excuse red-green too. T027 and T030 are writable against an absent module, exactly as T041 is | Emit red-green pairs for `fusion.py`: T027→T024 and T030→T025/T026, with observed-failure completion |
| F-02 | Governance | **CRITICAL** | amendment 3, FR-044, T003 | The design also breaches §Testing & Quality Policy's *"request-serving image contains no modeling-stack packages"*. Amendment 3 names §Source Code Layout only — if it lands as drafted, T003 passes green and this clause is still breached | Widen amendment 3 to except a shared inference runtime from the image assertion, or record a fifth blocking amendment |
| F-03 | Governance | **CRITICAL** | `specs/sad.md:262` | `sad.md` carries the **same Wilson-on-MRR defect** as `prd.md`, which is queued as amendment 1. No amendment, requirement or task covers it. On the current record FR-031 loses to a registered document | Record it as a blocking amendment with a requirement and a task |
| F-04 | Governance | **CRITICAL** | `plan.md:44` | The INT8-qualifier conflict is deferred to **E006's PR body** — no queue entry, no requirement, no task, outside SC-015. FR-025 and FR-007 depend on it. The plan's own standard: *"a policy obligation recorded only in a plan has no verifier"* | Add it to §Pending Amendments with a verifier, or raise it here |
| F-05 | Principle IV | **CRITICAL** | `plan.md` | Five sections beyond the template; three are summary epilogues, which §IV forbids explicitly. The E010 `status` defect is recorded twice | Fold the epilogues into existing sections; keep §Pending Amendments (independent Governance basis) |
| F-06 | Principle VII | **CRITICAL** | `spec.md` §Decisions Taken at Checklist | The freeze-discipline decision has scope decision and evidence, **no reversal trigger, no production-scale alternative** — while the decision directly above it carries all four | Complete the four-part format |
| F-07 | Dependencies | **HIGH** | `tasks.md:200` | Claims T084, T088 and T090 reach the seeded fixture T023 *"through their own chains"*. **They do not** — their chains never touch T023. Three integration tasks read a fixture they are not ordered after | Add `after:T023` to T084, T088, T090, or correct the claim |
| F-08 | Dependencies | **HIGH** | `tasks.md:201` | Claims *"T069 declares `after:T011`"*. It declares `after:T067` — changed during the tasks phase, prose not updated | Correct the prose; the benchmark-registration rationale stays valid via phase order |
| F-09 | Governance | **HIGH** | `plan.md:49` | The Governance row names **three** amendments; eight are recorded, four blocking — and the omitted one is **item 3**, which the design cannot proceed without | Restate against the actual queue |
| F-10 | Governance | **HIGH** | `plan.md:574` | *"six recorded needs"* against eight enumerated items; §Sequencing correctly orders all eight | Correct the count |
| F-11 | Governance | **HIGH** | `plan.md:46` | §Source Code Layout row cites *"amendment 4"*; the item is **3**. FR-044, SC-015 and T003 all say 3 | Correct the reference |
| F-12 | Consistency | **HIGH** | `plan.md:110-130, 441` | AD-010 records three decisions as open; the spec settled two (consultation budget, judgement source). Only composition remains | Restate AD-010 against the spec |
| F-13 | Principle VI | **MEDIUM** | `spec.md` FR-004/FR-043 | The replacement for a run budget requires a recorded change and re-measurement, but **no requirement obliges publishing both figures** — the half that makes a re-tune visible | Oblige publishing the before and after figures |
| F-14 | Consistency | **MEDIUM** | `spec.md` SC-016, `plan.md:20,106` | The latency statistic is **settled** at §Decisions Taken at Checklist and still called *"Pending"* / *"unresolved"* in three places | Restate the three; amendment 7 remains valid as a `sad.md` need |
| F-15 | Consistency | **WITHDRAWN** | `plan.md` §Checklist Outcome | **Not a defect.** Eleven items stood open at evaluation (119 − 108); the four user decisions then closed CHK001, CHK002, Testing CHK026 and Testing CHK028, leaving exactly the seven the table lists. The finding read a pre-decision count against a post-decision table | Reconciliation made explicit in the section; no count changed |
| F-16 | Development Workflow | **MEDIUM** | T016, T017 | §Temporary Files has no requirement, no task and no gateway-tier verifier — in the one epic that downloads a model and runs a native toolchain. `test_scratch_location.py` exists only under `src/api/tests/` | Add the obligation to T017 and extend the scratch check to the gateway tier |
| F-17 | Principle VII | **MEDIUM** | `spec.md` Assumptions, `plan.md:777` | The unbudgeted query-level latency is a scope decision with evidence and neither a reversal trigger nor a production-scale alternative | Complete the format |
| F-18 | Completion points | **MEDIUM** | T049–T051 | FR-043 maps to three tasks; the last carries no `[COMPLETES FR-043]`. 17 of 18 qualifying requirements have one | Add the marker to T051 |
| F-19 | Duplication | **LOW** | FR-037 to FR-046 | **No true duplicates.** The late additions each cite what they extend — FR-037 unifies FR-003/FR-018/FR-027, FR-040 governs changes above FR-027's floor, FR-041 extends FR-033's terms. One gap: **FR-046 never cites FR-012**, though FR-012 is the reason it bounds only the ranked portion | Add the cross-reference |
| F-20 | Convention | **LOW** | `tasks.md` T095, T085 | **Downgraded on verification.** No 200-character limit exists in `artifact-conventions`; the figure appears there only as a cap on error-message text, so this was a readability heuristic reported as a rule. T095 (257 chars) and T085 were genuinely bloated and are tightened; the rest stand | Tighten where readability suffers; assert no limit |
| F-21 | Clarity | **LOW** | `tasks.md`, 11 sites | Eleven tasks name a bare filename with no directory, resolvable from phase context but not mechanically | Expand to repo-relative paths |
| F-22 | Structure | **MEDIUM** | `plan.md:454-508` | Seven paths written by tasks are absent from §Project Structure — `verify.yml`, the benchmark and conformance modules, `main.py`, `embed.py`, `test_image_contents.py`, the fixtures directory. Reverse: `test_results.py` is enumerated and no task creates it | Reconcile both directions |
| F-23 | Interface edges | **LOW** | T020/T021→T039, T058→T063, T023→(5) | 15 of 18 exports are never imported; five cross a file or entry boundary with the consumer expressed only as `after:`, under-specifying the interface | Add `← T###:Symbol` on the five cross-boundary consumers |
| F-24 | Dependencies | **MEDIUM** | `tasks.md:195` | Says 29 tasks lack a gate path; the graph shows 26 | Correct the figure |
| F-25 | Principle V | **LOW** | T006 | Names the forbidden module, not the **source** module, and not `allow_indirect_imports = false` — which the file's own comment calls load-bearing. E010's precedent added a separate contract rather than extending a list | Name the source module and the setting |
| F-26 | Data Provenance | **LOW** | FR-016 | The quantization record is carried by plan and tasks but by **no requirement**. Licence-mixing across the two graphs is unaddressed | Extend FR-016 |

## Quality Summaries

**Spec quality** — assessed directly after two validator failures. No duplicate or near-duplicate
requirements: every late addition cites what it extends rather than restating it, which is the right
shape for a spec that grew in three waves. Ambiguity is low and the seven deliberately-unset
quantities are disclosed with occasions rather than vague. One missing cross-reference (F-19). The
FR-022/SC-010 amendment is coherent across both, and the contract's field description resolves in the
paragraph below its opening sentence — flagged and withdrawn on reading rather than on grep.

**Compliance** — **FAIL**. Six CRITICAL. Four are governance-shaped: a clause breached with no
amendment covering it, a registered document carrying a defect its twin has queued, an obligation
deferred to another epic's PR body, and a queue certified at three items when it holds eight. One is
a testing-policy limb answered with an argument that only reaches the other limb. One is a limitation
recorded without the format the same document enforced twice elsewhere.

## Coverage Summary

| Dimension | Result |
|---|---|
| Requirements FR-001 to FR-046 with a task | **46 / 46** |
| Uncovered requirements | **0** |
| Unmapped delivery-phase tasks | **0** |
| Completion markers on qualifying requirements | 17 / 18 (FR-043 missing) |
| Symbol imports resolving to an export | 3 / 3 |
| Tasks naming a file | 92 / 95 |

## Metrics

| | Before | After |
|---|---|---|
| Requirements | 46 | **48** (FR-047, FR-048) |
| Success criteria | 16 | 16 (SC-015 extended) |
| Architecture decisions | 15 | 15 |
| Tasks | 95 | **98** (T096, T097, T098) |
| Blocking amendments | 4 | **6** |
| Requirement coverage | 100% | 100% |

- Findings: **26** raised — 6 CRITICAL, 6 HIGH, 6 MEDIUM, 4 LOW after F-15 was withdrawn and F-20
  downgraded on verification.

## Remediation Applied — 2026-07-29

All 25 surviving findings were applied in this run. Two did not survive verification and are marked
in the table: **F-15 was withdrawn** (the count reconciles once the four user decisions are applied)
and **F-20 was downgraded** (no 200-character rule exists — that was a heuristic of mine reported as
a convention). The substantive changes:

**Testing policy (F-01)** — three ordered red-green pairs now exist for `fusion.py`: T027→T024,
T029→T025, T030→T026. Phase 3 is listed in **execution order rather than numeric order** as a
result, and says so. Each red is a distinct observation, not three restatements of "the module is
absent": the oracle fails to collect, the plan-shape assertion finds nodes not yet reporting the
fetch depth, and the candidate-set assertion finds the fiftieth position varying between runs. The
SQL-residence argument is now confined to the property limb it actually answers.

**Governance (F-02, F-03, F-04, F-09, F-11)** — the queue holds ten items, six of them blocking.
Amendment 3 is stated to reach **two** clauses (§Source Code Layout and §Testing & Quality Policy),
because an amendment naming only the first would let T003 close green with the serving-image
assertion still contradicted. Two new blocking items: **9**, the `specs/sad.md` Wilson-on-MRR twin of
item 1 — verified directly at `specs/sad.md:262`, where "Wilson 95% CIs" governs both recall and MRR
— and **10**, the §Technology Stack INT8 qualifier that had been left to E006's PR body, which is
not a queue and obliges nobody. Each has a requirement (FR-048, FR-047) and a task (T097, T096).
SC-015 gates all six.

**Dependencies (F-07, F-08)** — T084, T088 and T090 now carry `← T023:seeded_corpus`. The symbol
form rather than a second `after:` is deliberate: the grammar permits one `after:` per line, and the
symbol edge names the interface being consumed while gating execution identically. The T069 claim is
corrected to `after:T067`. The declared-edge roster was diffed against the task lines mechanically
and matches exactly in both directions.

**Principle IV, VI, VII (F-05, F-06, F-13, F-17)** — the three summary epilogues are removed rather
than merely disclosed, and their load-bearing content left with the sections that own it. Both
incomplete limitations gained a reversal trigger and a production-scale alternative. FR-043 now
obliges publishing the before **and** after figures, which was the half that makes a re-tune visible
and was carried by prose alone.

**Structure and provenance (F-16, F-22, F-25, F-26)** — §Project Structure reconciled in both
directions: seven missing paths added, `test_results.py` removed rather than given a task. T098
extends the scratch check to the gateway tier, the one tier that downloads a model. T006 now names
the source module and `allow_indirect_imports = false`. FR-016 carries the generated-artifact record
and a per-graph licence basis.

## Verification

| Check | Result |
|---|---|
| Task-line grammar (`T###`, single `after:`) | 98 / 98 pass |
| Requirements with at least one task | 48 / 48 |
| Declared-edge roster vs. actual task lines | exact match, both directions |
| `specs/sad.md:262` Wilson-on-MRR claim | confirmed by reading the row |

## Remaining — not this branch's to perform

Six amendments must land on the **default branch** before `/sddp-implement`: items 1, 2, 3, 4, 9
and 10. Governance serializes amendments there and a feature branch records rather than performs
them; T001–T004, T096 and T097 are the verifiers, each closed by citing the amending revision.
One design question stays open by choice — AD-010's evaluation-set composition (CHK027), owned
jointly with E014.
