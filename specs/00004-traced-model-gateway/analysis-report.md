# Analysis Report: Traced Model Gateway (E004)

**Feature**: `specs/00004-traced-model-gateway/` | **Date**: 2026-07-26 | **Spec type**: technical
**Instructions version audited**: `project-instructions.md` **v1.2.1**

This run discharges **OI-6** — the compliance re-run the v1.2.0 concurrency protocol required after the prior gate ran against the superseded v1.1.3. The re-run verdict is **FAIL**, so OI-6 closes as *performed*, not as *passed*.

Most findings trace to one cause: the ADR-0013 reconciliation reached the requirements and integration points but missed several derived statements — deliverables, success criteria, and citations. A blanket ADR renumbering also mis-retargeted one legitimate reference.

## Findings

| ID | Category | Severity | Location(s) | Summary | Recommendation |
|----|----------|----------|-------------|---------|----------------|
| A-001 | Cross-artifact contradiction | CRITICAL | `spec.md` OBJ3 Deliverables L128 | Deliverable still reads "Migration runner and this epic's migration, from a migration-number range claimed disjoint from E003's" — directly contradicts TR-017, which forbids introducing a runner. Live section, no supersession banner | Rewrite as Alembic revisions authored into E003's `/src/model` arrangement in the `0100`–`0199` block |
| A-002 | Wrong cross-reference | CRITICAL | `spec.md` `NEW-WORKER` L367 | `{SAD:ADR-0014}` cites the provider-extra decision for console entry points. The blanket ADR-0011→0014 renumber hit a reference that legitimately pointed at main's ADR-0011 (model-owned one-shot jobs) | Change to `{SAD:ADR-0011}` |
| A-003 | Non-existent version citation | CRITICAL | `spec.md` L230; `plan.md` PI-1 L317 | Both cite `project-instructions.md` **v1.1.4**. No such version exists — history runs 1.1.3 → 1.2.0 → 1.2.1. This is the load-bearing citation for the spool's compliance, so the justification currently resolves to nothing | Change both to v1.2.1 |
| A-004 | Cross-artifact contradiction | HIGH | `spec.md` SC-015 L387 | Denominator is "the numbered migration files in the gateway's own migration source directory" — a directory ADR-0013 removed. Contradicts SC-007, which correctly routes through E003's runner | Restate denominator against E003's revision directory |
| A-005 | Under-scoped base requirement | HIGH | `spec.md` TR-024 vs TR-059; TR-030 vs TR-060 vs SC-010 | TR-024 names three egress sinks; TR-059 closes the same obligation at five. TR-030 scopes the scan to the fixture store; TR-060 widens it to five; SC-010 then asserts the wider scope *is* TR-030's, which is false as written. An implementer working from the base requirement under-implements | Point TR-024 and TR-030 at their closures; retarget SC-010's referent to TR-060 |
| A-006 | Internal contradiction | HIGH | `spec.md` TR-011 L265 vs TR-056 L310 | TR-011's closed exclusion set claims all eight members "fail before any provider request or fixture lookup". A `replay` miss (TR-022) is *detected by* a fixture lookup, and TR-056 counts a lookup as a transport attempt | Exempt the replay miss in TR-011's closure sentence |
| A-007 | Governance — missing version stamp | HIGH | `plan.md` Instructions Check | No audited instruction version recorded. v1.2.0 makes the recorded version the drift-detection mechanism; `spec.md` records it, the plan does not | Stamp the table with the audited version and date |
| A-008 | Governance — stale row | HIGH | `plan.md` Governance row L43 | Cites only ADR-0014/0015. Omits ADR-0016 — the record sanctioning this epic's only base-runtime dependency — and omits the 0011/0012 → 0014/0015 renumbering, which is exactly what v1.2.0's claim-numbers-at-epic-start rule addresses | Add ADR-0016, the renumbering, and the number-claiming rule |
| A-009 | Governance — inaccurate disposition | HIGH | `plan.md` Project Structure L289-291, PI-1, OI-1, OI-2 | Records the feature as having amended `project-instructions.md`, `sad.md` and `project-plan.md` ("Amended this phase"). v1.2.0 forbids a feature branch performing an amendment. The amendments *were* correctly performed on the default branch in their own commits — this is a records-accuracy defect, not a live breach, but it reads as one | Restate as needs raised here and discharged on the default branch; drop from the feature's file-change list |
| A-010 | Verification coverage | HIGH | `spec.md` TR-046, TR-054, TR-055, TR-058, TR-068, TR-072, TR-075, TR-076, TR-077, TR-080, TR-081 | 11 requirements have neither a validation criterion nor a success criterion. TR-068 is the worst: IP-005's E013-contract checkability claim rests on it, and it mandates a build failure with nothing asserting it | Add coverage, TR-068 first |
| A-011 | Placement — root `/tests` exception | HIGH | `plan.md` IP-008; `tasks.md` T033, T032 | After ADR-0013 there is one revision directory, so the prefix/single-head check is no longer "cross-entry" in the sense the exception grants. `plan.md` TR-051 row and Project Structure still say "both directories" | **Judgment required** — see Deferred |
| A-012 | Unresolved placeholder | MEDIUM | `spec.md` TR-034 L289 | "a stated default" — never stated. `plan.md` and `tasks.md` T018 both say 120 s; the spec does not. SC-020 tests against it | State 120 s in TR-034 |
| A-013 | Unnamed controls | MEDIUM | `spec.md` TR-065 L319 | Two of four configuration keys the error message must name are placeholders ("the mode key", "the pinned price-table version identifier") while the other two are concrete. No check can assert on an unnamed key | Name both keys |
| A-014 | Path inconsistency | MEDIUM | `plan.md` Requirement Coverage Map | Same migration artifact given both extensions: `0101_price_table_entry` as `.sql` (TR-015) and `.py` (TR-046/049); `0102_llm_invocation` as `.py` and `.sql`; `0103` likewise | Normalise to `.py` — Alembic revisions |
| A-015 | Stale path form | MEDIUM | `plan.md` TR-013, TR-055, TR-069, TR-074 rows | Bare `migrations/` now reads gateway-local, a location ADR-0016 forbids for schema assets | Qualify as `src/model/migrations/versions/` |
| A-016 | Principle VII form incomplete | MEDIUM | `plan.md` Instructions Check, Principle V row | The disclosed residual (a forbidden contract catches import edges only) carries scope decision and evidence but no reversal trigger and no production-scale alternative. Both sibling limitation rows carry all four | Add the two missing parts |
| A-017 | Stale compliance row | MEDIUM | `plan.md` Data Provenance row L42 | Predates v1.2.0's per-layer provenance restatement. The fixture sidecar's fields match neither layer's required set, and the row asserts PASS without citing OI-5's deferral | Mark N/A with reason or apply the generated-layer field set; cross-reference OI-5 |
| A-018 | Unaddressed amendment | MEDIUM | `plan.md` IP-002 | v1.2.1 Infrastructure admits console entry points for modeling-owned jobs; E004's revisions are now applied by one. IP-002 adds the `db` service but never says how the migration is invoked | Name the invocation mechanism |
| A-019 | Superseded citation | MEDIUM | `plan.md` L14, 84, 136, 142, 150, 175 | Six `{SAD:ADR-0013}` citations with no forward pointer to ADR-0016. Substance carries forward, so no decision is wrong | Add a forward pointer at first use |
| A-020 | Frontmatter | MEDIUM | `spec.md` L8 | `epic_sources` still `{SAD:ADR-0007}{SAD:ADR-0010}` though the spec now depends materially on ADR-0013/0014/0015/0016 | Extend |
| A-021 | Mis-homed obligation | MEDIUM | `spec.md` TR-050 L304 | Requires E004 to record per-file checksums in a ledger that TR-017 says E003 owns, and that stock Alembic does not keep | **Judgment required** — see Deferred |
| A-022 | Closure not enumerable | MEDIUM | `spec.md` TR-019 vs TR-020 | TR-020 requires the hashed field list be closed; TR-019 lists "every sampling parameter", a category. A new parameter is simultaneously inside the list and unenumerated | State how the closed-list check treats an unenumerated parameter |
| A-023 | Criterion weaker than requirement | MEDIUM | `spec.md` OBJ3 VC1 L138 | "a second run is a no-op" is the exit-code formulation TR-050 explicitly rejects | Align VC1 to the ledger-and-schema-identity postcondition |
| A-024 | Wrong cross-reference | MEDIUM | `spec.md` OBJ3 VC8 L145 | "cost is conditional per TR-016" — TR-048 closes the absent-cost reason set at three; TR-016 supplies one of them | Retarget to TR-048 |
| A-025 | Circular attribution | MEDIUM | `spec.md` TR-051 L305 | "each revision's filename prefix falls inside the block its epic claims" — nothing maps a revision to an owning epic; if membership derives from the prefix, the assertion is circular | State as a range partition over the whole directory |
| A-026 | Unmatched task paths | MEDIUM | `tasks.md` T011, T042, T065, T066, T073 | Bare filenames (`models.py`, `config.py`, `errors.py`) that a mechanical path checker cannot match against the plan's Project Structure. All unambiguously resolvable from sibling tasks | Qualify the paths |
| A-027 | Undefined term | LOW | `spec.md` TR-033 | "the gateway revision that produced it" — git SHA or package version, unstated | Define |
| A-028 | Unenumerable obligation | LOW | `spec.md` TR-055, TR-062, TR-080 | Each requires a limit be disclosed "wherever the claim is made" — open location set, no check possible | Enumerate the disclosure sites or reduce to one requirement |
| A-029 | Partial restatement | LOW | `plan.md` Technical Context L15 | States one of v1.2.1's three conjuncts for the buffer exemption | Cross-reference the full test |
| A-030 | Unconsumed export contracts | LOW | `tasks.md` T039 `elapsed_ms`, T040 `RecordWriter` | Declared but no `←` edge consumes them, so the symbol contract is unverified by any edge | Optional: add consumer edges |

Overflow: none — 30 findings, under the 50 cap.

## Quality Summaries

**Spec Quality** — Spec Validator: **FAIL**, 22/25. All 81 TR IDs present, none duplicated, none missing. Failures concentrate in verification coverage (11 uncovered requirements) and four testability defects. Base/closure requirement layering is mostly well-handled — TR-009/TR-042/TR-078 explicitly partition ownership — but TR-024/TR-059 and TR-030/TR-060 leave the base narrower than its closure.

**Compliance** — Policy Auditor against v1.2.1: **FAIL**, 2 CRITICAL / 3 HIGH / 5 MEDIUM / 2 LOW. Principles I, III, IV, V, and the Testing & Quality Policy pass. Failures are Governance (amendment protocol records, version stamp, stale row), Source Code Layout (root `/tests` exception), Data Provenance (stale row), Infrastructure (unaddressed amendment), and Publish the Miss (incomplete four-part form on one residual).

## Coverage Summary

| Requirement Key | Has Task? | Task IDs | Notes |
|-----------------|-----------|----------|-------|
| TR-001 – TR-081 | **Yes, 81/81** | see `tasks.md` | Every requirement carries at least one task tag; no task references a TR outside the range |
| TR-017 | Yes, with caveat | T031 *(withdrawn)*, T032 | Only remaining task is a verification task. ADR-0013 removed the builder, and TR-017 was restated as a verify obligation — consistent, but the requirement now has no implementation partner in this epic |

**Completion-point markers**: 12 `[COMPLETES TR-###]` markers, all positioned at or after the last task tagging that requirement. Every requirement mapping to 3+ tasks carries one. No ordering inversions.

**Dependency edges**: 6 `← T###:Symbol` consumer edges, **6/6 resolve** against a declared `→ exports:` symbol. Zero mismatches. No `[P]` task carries a dependency edge, so the parallel-batching rule holds across all 16 parallel tasks.

## Instructions Alignment Issues

See A-007, A-008, A-009, A-011, A-017, A-018. The substantive compliance posture is sound — the single-provider-import contract, the computation boundary, the storage rule under v1.2.1, and the test-first mandate all hold. What fails is the *record* of compliance: an unstamped gate, a stale governance row, a disposition that describes the feature doing what the default branch actually did, and one placement claim invalidated by ADR-0013.

## Unmapped Tasks

Five tasks carry no requirement tag: **T005**, **T006** (Setup); **T073**, **T074**, **T075** (Polish). All sit in phases the coverage rule exempts, and each cites an `AD-###` or `HINT-###` instead. **T006** is worth noting — it provisions the database service that seven OBJ3 requirements depend on for execution, yet carries no tag itself. Not gold-plating.

## Metrics

- Total requirements: **81** (TR-001 – TR-081)
- Total tasks: **75** (74 active, 1 withdrawn)
- Requirement coverage: **100%** (81/81)
- Dependency-edge integrity: **100%** (6/6)
- Findings: **30** — 3 CRITICAL, 8 HIGH, 15 MEDIUM, 4 LOW
- Blocking for implement: **OI-8** (E003 not started) is unchanged and independent of this analysis

## Remediation

Applied in the same run. All 30 findings actioned; 0 skipped.

| # | Finding | Severity | File(s) | Change | Status |
|---|---------|----------|---------|--------|--------|
| 1 | A-001 | CRITICAL | `spec.md` | OBJ3 deliverable restated as Alembic revisions in E003's `/src/model` arrangement | Applied |
| 2 | A-002 | CRITICAL | `spec.md` | `NEW-WORKER` citation corrected `{SAD:ADR-0014}` → `{SAD:ADR-0011}` | Applied |
| 3 | A-003 | CRITICAL | `spec.md`, `plan.md` | Both `v1.1.4` citations corrected to `v1.2.1` | Applied |
| 4 | A-004 | HIGH | `spec.md` | SC-015 denominator restated against E003's revision directory | Applied |
| 5 | A-005 | HIGH | `spec.md` | TR-024 and TR-030 now point at their closures (TR-059/TR-060) as authoritative | Applied |
| 6 | A-006 | HIGH | `spec.md` | TR-011's closure sentence exempts the replay miss and reconciles with TR-056 | Applied |
| 7 | A-007 | HIGH | `plan.md` | Instructions Check stamped with audited version v1.2.1 and re-check date | Applied |
| 8 | A-008 | HIGH | `plan.md` | Governance row adds ADR-0016 and discloses the number-collision miss | Applied |
| 9 | A-009 | HIGH | `plan.md` | PI-1 / OI-1 / OI-2 restated as needs raised here, discharged on the default branch; governance docs removed from the feature's file list | Applied |
| 10 | A-010 | HIGH | `spec.md` | SC-024, SC-025, SC-026, SC-027 and OBJ5 VC5 added, covering the 11 uncovered requirements | Applied |
| 11 | A-011 | HIGH | `spec.md`, `plan.md` | Root `/tests` placement justified on partition-between-two-claims grounds; "both directories" wording removed | Applied |
| 12 | A-012 | MEDIUM | `spec.md` | TR-034 deadline default stated as 120 seconds | Applied |
| 13 | A-013 | MEDIUM | `spec.md` | TR-065 names `GATEWAY_MODE` and `GATEWAY_PRICE_TABLE_VERSION` | Applied |
| 14 | A-014 | MEDIUM | `plan.md` | All six `.sql` migration references normalised to `.py` | Applied |
| 15 | A-015 | MEDIUM | `plan.md` | Bare `migrations/` paths qualified to `src/model/migrations/versions/` | Applied |
| 16 | A-016 | MEDIUM | `plan.md` | Principle V residual gains reversal trigger and production-scale alternative | Applied |
| 17 | A-017 | MEDIUM | `plan.md` | Data Provenance row engages the v1.2.0 per-layer rule and cites OI-5 | Applied |
| 18 | A-018 | MEDIUM | `plan.md` | IP-002 names the console-entry-point invocation mechanism | Applied |
| 19 | A-019 | MEDIUM | `plan.md` | Forward pointer to ADR-0016 at first ADR-0013 use | Applied |
| 20 | A-020 | MEDIUM | `spec.md` | `epic_sources` extended to ADR-0013/0014/0015/0016 | Applied |
| 21 | A-021 | MEDIUM | `spec.md` | TR-050 reframed as a verify obligation; the checksum mechanism is E003's to choose | Applied |
| 22 | A-022 | MEDIUM | `spec.md` | TR-020 states how the closed-list check treats an undeclared parameter | Applied |
| 23 | A-023 | MEDIUM | `spec.md` | OBJ3 VC1 aligned to TR-050's postcondition | Applied |
| 24 | A-024 | MEDIUM | `spec.md` | OBJ3 VC8 cross-reference retargeted to TR-048 | Applied |
| 25 | A-025 | MEDIUM | `spec.md` | TR-051 restated as a range partition, removing the circular epic lookup | Applied |
| 26 | A-026 | MEDIUM | `tasks.md` | Bare filenames in T011, T042, T065, T066, T073 fully qualified | Applied |
| 27 | A-027 | LOW | `spec.md` | TR-033 defines gateway revision as the recording run's commit SHA | Applied |
| 28 | A-028 | LOW | `spec.md` | TR-055/062/080 enumerate three disclosure sites | Applied |
| 29 | A-029 | LOW | `plan.md` | Technical Context carries v1.2.1's full three-part buffer test | Applied |
| 30 | A-030 | LOW | `tasks.md` | T041 consumes `RecordWriter` and `elapsed_ms` as declared edges | Applied |

**ID integrity after remediation**: TR 81, SC 27, STF 5, AD 8, HINT 9, VR 38, CD 5, T 75, CHK 116 — no ID changed, removed, or reordered. Four success criteria added (SC-024 – SC-027); one drafting collision on SC-023 was caught and the *new* criterion renumbered, leaving the pre-existing SC-023 untouched. Requirement coverage remains 81/81.

**OI-6 closed** — this run is the re-run the v1.2.0 concurrency protocol required.
