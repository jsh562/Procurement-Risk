# Analysis Report: Line Detail and Traceability

> Feature: E012 | Date: 2026-07-30 | Phase: Analyze | Instructions version: v1.2.11

Cross-artifact analysis over `spec.md` (46 FR, 32 SC), `plan.md` (10 AD), `contracts/openapi.yaml` (35 schemas), `tasks.md` (46 tasks). Sources: Spec Validator (read-only), Policy Auditor, Task Tracker, and local coverage/consistency/convention passes.

## Verdicts

| Source | Verdict |
|---|---|
| Spec Validator | **FAIL — 23/25.** Same score as the Specify pass, entirely different failing content. None of these appear in the spec's own Compliance Check |
| Policy Auditor | **PASS — 0 CRITICAL, 4 MEDIUM, 11 LOW.** Nothing blocks the Implement gate |
| Coverage / consistency / conventions | **PASS on 4 of 5.** One file-path mismatch |

## Metrics

| Metric | Value |
|---|---|
| Requirements | 46 (FR-001…FR-046, contiguous) |
| Requirement → task coverage | **46/46 (100%)**, by literal tag |
| Tasks | 46 (T001…T046, contiguous, 1 complete) |
| Untagged tasks outside optional phases | 0 |
| `[COMPLETES]` markers on 3+-task requirements | 9/9 |
| Dependency edges (`←` vs `→ exports:`) | 6/6 matched |
| Artifact-convention violations | 0 |
| CRITICAL findings | **0** |

## Findings

Severity per `artifact-conventions/SKILL.md` plus the analysis criteria. `A-` = this pass.

### HIGH

| ID | Category | Location | Summary | Recommendation |
|---|---|---|---|---|
| A-001 | Decidability | `spec.md` FR-038 | The domain predicate is circular. "Anything scalar and stated that falls outside the five classes is a sixth" defines membership by an adjective pair with no locus, so `draw_count`, `horizon_days`, `age_days`, `staleness_threshold_days` and `mark_count` are each a sixth class by FR-038's own test. FR-013 identifies four things; one is in the closed set and three are outside it, and no rule says why | Bind the domain to a locus as E010 FR-027 does ("defined by where it sits"), then state the rule that admits FR-013's as-of date and excludes its other three |
| A-002 | Contradiction | `spec.md` FR-039 | The criterion offered is satisfied by the thing it exempts. FR-039 excludes the encoding on the ground that Principle II binds what is "stated, labelled, or **carried as a field**" — and `encoding.marks.offsets_days` *is* a required field of fifty integers | Say "carried as a **scalar** field". Also resolve the band disagreement: FR-038 reasons about bands as in-domain, FR-039 excludes the encoding unqualified |
| A-003 | Coverage gap | `spec.md` FR-042 | **E010's `calendar-passed` state is absent from E012 entirely** — the word "Today" never appears in this spec. A line whose need-by has passed relative to Today while remaining inside the forecast frame carries a state on the worklist and none here: exactly the divergence FR-042's by-reference adoption exists to prevent | Add the annotation, or record explicitly why this view omits E010's eighth state |
| A-004 | Licensing conflict | `spec.md` FR-024 | "Adopted by reference" imports a set **neither of whose members is available to this figure**. E009 licenses reason (i) "only to the three census figures of FR-038" and closes it: *"No published figure may be recorded as a census that is not one of those three."* E012's source-resolution share is a fourth. The iteration-3 remediation fixed the enumeration and created the conflict | State the availability rule locally and drop the "adopted by reference" claim, **or** record a shared-document amendment extending E009 (recorded on this branch, performed on the default branch) |
| A-005 | Vacuous gate | `spec.md` FR-025, `contracts/` `CensusFigure` | The 100% target is **met vacuously in the state the spec calls the common path**. `meets_target` is *"True where `total_count` is zero: nothing failed"*, and identity resolution has not run. This is FR-025's own indictment: "A rate published against no threshold cannot be missed and therefore evidences nothing" | Publish the target as **unjudged** at a zero denominator, with its cause — E009 FR-028 sets the precedent for this exact shape |
| A-006 | Untestable criterion | `spec.md` SC-020 | Falsified by design. SC-020 forbids any published figure rendering as a certainty; FR-024's census renders exactly 100% and the contract defends it ("rendering it `>99%` would understate a figure that is exact"). FR-041 scopes the bounded form to three named figures; SC-020 is scoped to all | Scope SC-020 to the figures FR-041 names, or to distribution figures generally |
| A-007 | Unmet requirement | `spec.md` FR-020 | "MUST display the originating document page **itself, not a reference to it** … linking to a whole document transfers the verification cost back to the reader." The design streams the whole document with `#page=N`. `plan.md` records this as a four-field Recorded Limitation, but **the requirement stands unamended** | Record the gap in the spec as a known unmet requirement under Principle VII, keeping the requirement as the standard rather than weakening it to match the implementation |

### MEDIUM

| ID | Category | Location | Summary | Recommendation |
|---|---|---|---|---|
| A-008 | Composability | `spec.md` FR-042 | Imports E010 FR-018a's *order* without FR-033's *co-occurrence* rules, ranking `no_active_run` 4th beneath two states that are undefined whenever it holds | Adopt FR-033's constraints alongside FR-018a's order |
| A-009 | Underspecification | `spec.md` FR-042 | "Exactly one per section" is undecidable for the two multi-item sections and for a rendered section with zero items — the shipping state. The contract supplies the rule; the spec does not | Promote the contract's composition rule into the spec |
| A-010 | Ambiguity | `spec.md` FR-024 | "The active run" means the *forecast* run everywhere else in this spec; linked records come from the resolution run. The contract reads it as the resolution run using a term the spec does not use | Say "the active **resolution** run" |
| A-011 | Untestable criterion | `spec.md` SC-004 | A closed line also has no posterior and resolves to `absent_or_closed_line`, a fourth condition | Qualify to a line that exists and is open, or widen to four |
| A-012 | Untestable criterion | `spec.md` SC-010 | Unevaluable at a zero denominator, and asserts of one figure both "published as an exact count" and "measured over real corpus documents", which AD-010 splits across two measurements | Split to match AD-010's two measurements; add the zero case |
| A-013 | Divergent duplicates | `spec.md` FR-003 / FR-012 | Same prohibition, two loci, **different lists**: "an expected overrun" is forbidden in the response and permitted on the screen | Align the enumerations verbatim. *(Merging would change a requirement ID — CRITICAL under preservation rules — so not done)* |
| A-014 | Conflict | `spec.md` FR-009 / FR-037 | FR-009 permits a calendar date attached to a labelled quantile; FR-037 forbids a single predicted delivery date in "a figure, an axis, a label, a tooltip" with no "bare" qualifier. Reconciliation lives only in FR-038 | Add FR-038's "bare" qualifier to FR-037 |
| A-015 | Proxy not disclosed where read | `contracts/` `CensusFigure` | AD-010 names the request-time check "near-tautological" in the plan; the contract describes the *corpus* check and never says the published figure is a proxy. Principle VII requires the miss published with its cause | Add a required `span_check_basis` member declaring the proxy and where the corroborating measurement lives |
| A-016 | Unpublished evidence | `tasks.md` T034 | AD-010 promises "disagreement is published"; T034 is a pytest module, which publishes pass/fail, not a figure | T034 must emit both counts and their disagreement to a committed artifact |
| A-017 | Structure incomplete | `plan.md` § Project Structure | Declares 22 paths; `tasks.md` legitimately names 32. Eight are real source/test files, including the **`NEW-CONFIG` corpus-root plumbing** the spec signals and AD-007 covers. The § Instructions Check row "New code lands under existing `/src/api` and `/src/web` entries" is consequently inaccurate — code lands in four locations | Add all paths with `+`/`~` markers; amend the Source Layout evidence to name all four locations and the ground for each |
| A-018 | Missing assertion | `tasks.md` T044 | No task asserts the census's Principle II obligations: `has_interval === false`, the licensed reason, the population, or the contract's own invariant that `total_count` is identical on every line's response | Extend T044, including a cross-line `total_count` comparison |
| A-019 | Path ambiguity | `tasks.md` ×15 | Fifteen tasks name relative or bare-filename paths (`compute/distribution.py`, `useLineDetail.ts`) rather than repo-root paths | Qualify every path to repo root |
| A-020 | Format deviation | `tasks.md` ×16 | `{AD-###}` and `{HINT-###}` reuse the `{...}` syntax the grammar reserves for `FR\|TR\|OR\|RR`, so a naive parser emits false requirement IDs | Move annotations outside braces, or accept and document the deviation |

### LOW

| ID | Location | Summary |
|---|---|---|
| A-021 | `spec.md` FR-034 | Direction ("associated with slower delivery") has no source of truth; `covariate_names` records which covariates entered the fit, not which way each points. Hand-authored direction is the unfalsifiable claim FR-031 refuses for values |
| A-022 | `spec.md` FR-015 | "Reachable by every reader without assistive technology" is undefined; a `<details>` disclosure satisfies it literally. E010 FR-019 already spells the fix out |
| A-023 | `spec.md` FR-045 | Staleness threshold, basis and comparison date are E010 FR-029's and are not cited here |
| A-024 | `spec.md` Glossary | Missing "stated scalar figure", "distribution encoding", "linked record", "covered by the active run", "Today"; "anchor date" and "as-of date" used interchangeably |
| A-025 | `spec.md` SC-026 / SC-001 | SC-026 quantifies over the undecided FR-038 domain; SC-001's "full spread" is not what FR-011 delivers |
| A-026 | `contracts/` | `no_interval_reason` enum admits both reasons while the prose says only one is available; inert `default` beside `required` |
| A-027 | `contracts/` | `share` described as "exact"; the example publishes `0.9923` for 388/391 |
| A-028 | `tasks.md` T046 | Declares `after:T034` (a `/src/model` corpus test); its real dependency is the memoised aggregate. Also carries no requirement tag |
| A-029 | `plan.md` HINT-001 | "QC checks the commit order" — feature branches are **squash merged**, so the ordering evidence is destroyed at merge. Pre-merge review only; undisclosed |
| A-030 | `tasks.md` Brownfield Notes | `src/model/tests/…` justified by a *dependency* argument against an *ownership* rule. Placement right, reasoning wrong |
| A-031 | `plan.md` §Post-Design | "**Verdict: FAIL**" prints 14 rows above the "stands at PASS" reconciliation |
| A-032 | `plan.md` AD table | AD-010 precedes AD-009 |
| A-033 | `tasks.md` T001 | Reads as a feature task directing default-branch mutation, in tension with "a feature branch does not perform it". Conduct was correct; the text is not |
| A-034 | `plan.md` AD-010 | SC-010 divergence does not invoke ADR-0017, which exists for exactly this |
| A-035 | `tasks.md` T005 / T036 | T005's precedence is prose not `after:`; T036 imports `← T010:SECTION_COPY` without a direct `after:T010` (sound transitively via T019) |

## Not findings — recorded so they are not "fixed"

- **ADR-0025:152 is stale but MUST NOT be edited.** It says the catalog rows are not performed; both now exist. Decision records are append-only — correcting it would be the breach.
- **ADR-0003 status drift** — `project-plan.md` says `accepted`, `sad.md` says `superseded`. Pre-existing, not E012's, and belongs to a separate amendment.
- **ADR-0025 landed with full content rather than a `status: claimed` placeholder.** This over-satisfies the clause's stated purpose and is correct.

## Coverage summary

| Dimension | Result |
|---|---|
| FR → task | 46/46 by literal tag; zero-coverage: none |
| Task → requirement | 0 untagged outside Setup/Foundational/Polish |
| Completion points | 9/9 requirements spanning 3+ tasks carry `[COMPLETES]` |
| Dependency edges | 6/6 `←` consumptions matched by `→ exports:` |
| Story independence | US1–US4 all independently testable; US2 needs frozen fixtures until E009 runs |

## Next actions

No CRITICAL. The Implement gate is not blocked. The HIGH findings are spec-decidability defects that will otherwise surface as QC bug tasks against implemented code, so they are worth closing first — and A-004, A-005 and A-006 in particular, because each is a published figure that is wrong in a way a reader cannot see.

## Remediation applied

Run immediately after analysis, as requested. 33 of 35 findings applied; 2 skipped on preservation grounds.

| Finding | Severity | File(s) | Change | Status |
|---|---|---|---|---|
| A-001 | HIGH | `spec.md` | FR-038's domain bound to a **locus** (the regions the view renders figures about this line in), following E010 FR-027's device; artifact identification excluded by construction | Applied |
| A-002 | HIGH | `spec.md` | FR-039 now says "carried as a **scalar** field" — the missing word was what let its own criterion readmit the array it exempts | Applied |
| A-003 | HIGH | `spec.md` | FR-042's annotation set extended to four with **calendar-passed**, E010's eighth state | Applied |
| A-004 | HIGH | `spec.md`, `contracts/` | The licensed reason is stated **locally**; the "adopted by reference" claim is dropped, because E009's set closes at three figures and this is a fourth. Extending E009 recorded as a shared-document amendment, not performed | Applied |
| A-005 | HIGH | `spec.md`, `contracts/` | A zero denominator publishes the target **unjudged** with its cause; `meets_target` is nullable and null there | Applied |
| A-006 | HIGH | `spec.md` | SC-020 scoped to distribution figures, so it no longer forbids the exact 100% FR-024 requires | Applied |
| A-007 | HIGH | `spec.md` | FR-020a records the page-vs-document gap as an **open shortfall with its reversal trigger**, keeping FR-020 as the standard rather than weakening it to match what shipped | Applied |
| A-008 | MEDIUM | `spec.md` | E010 FR-033's co-occurrence constraints adopted alongside FR-018a's order | Applied |
| A-009 | MEDIUM | `spec.md` | Section composition rule and the empty-section case stated | Applied |
| A-010 | MEDIUM | `spec.md` | "the active **resolution** run" | Applied |
| A-011 / A-012 / A-025 | MEDIUM | `spec.md` | SC-004 qualified; SC-010 split with SC-010a for the corpus measurement; SC-026 re-anchored on the locus domain; SC-001 no longer promises "full spread" | Applied |
| A-013 / A-014 | MEDIUM | `spec.md` | FR-003's enumeration aligned with FR-012's; FR-037 gained the "bare" qualifier | Applied |
| A-015 | MEDIUM | `contracts/` | `span_check_basis` declares the proxy beside the figure a reader actually sees | Applied |
| A-016 | MEDIUM | `tasks.md` | T034 must write both measurements and their disagreement to a committed artifact | Applied |
| A-017 | MEDIUM | `plan.md` | Eight paths added incl. the `NEW-CONFIG` plumbing; Source Layout evidence now names all four locations | Applied |
| A-018 | MEDIUM | `tasks.md` | T044 gained six census assertions incl. a cross-line `total_count` comparison | Applied |
| A-019 / A-020 | MEDIUM | `tasks.md` | 16 shorthand paths qualified to repo root; 13 annotation groups moved out of requirement braces | Applied |
| A-021…A-024 | LOW | `spec.md` | FR-034a (direction from a committed source); FR-015 adopts E010's decidable form; FR-045 cites E010 FR-029; 5 glossary terms; "anchor date" normalised | Applied |
| A-026…A-035 | LOW | all four | `const` on the licensed reason; `share` marked non-authoritative; T046 re-pointed to T028 and tagged; T001 reworded as a dependency; verdict reconciliation surfaced; AD table reordered; ADR-0017 invoked; squash-merge limit disclosed | Applied |

**Skipped — 2, both on preservation grounds:**

| Finding | Why skipped |
|---|---|
| Merge FR-003→FR-012, FR-019→FR-018, FR-023's cause clause→FR-036; delete FR-017 | Each **changes or deletes a requirement ID**, which `artifact-conventions` classifies **CRITICAL**. The substance was addressed instead by aligning FR-003's enumeration with FR-012's verbatim. The remaining overlaps are redundancy, not contradiction |
| Correct ADR-0025's now-stale sentence about the catalog rows | Decision records are append-only. The sentence is stale because the rows landed; editing it would be the breach |

Two requirements were added during remediation (FR-020a, FR-034a) and both were given task coverage in the same pass — 48/48 requirements now reach a task.
