# Analysis Report: Core Data Schema

**Feature**: `00002-core-data-schema` (E003) | **Date**: 2026-07-25 | **Mode**: Analysis, followed by remediation

Artifacts analysed: `spec.md` (86 TR, 28 SC), `plan.md`, `tasks.md` (54 tasks), `data-model.md`, `research.md`, `checklists/data-integrity.md`, `specs/adrs/0011`, `specs/adrs/0012`.

## Findings

| ID | Category | Severity | Location(s) | Summary | Recommendation |
|----|----------|----------|-------------|---------|----------------|
| A-001 | Instructions violation | CRITICAL | `plan.md` Source Code delta; `tasks.md` T012 | `src/.dockerignore` is a single shared allowlist for the whole `/src` build context, and `src/api/Dockerfile` builds from that context. Admitting `model` breaks `test_only_the_serving_boundary_and_the_gateway_are_admitted` and `test_excluded_entries_are_unreachable_from_the_build[model]`, and relaxes an architectural constraint with no superseding ADR | Build the migration image from context `./src/model` with its own Dockerfile. Never touch `src/.dockerignore`; `ADMITTED` stays `{api, gateway}` |
| A-002 | Consistency | HIGH | `plan.md` Requirement Coverage Map, 43 rows | Map was written against the retired 11-prefix chain. 28 rows name a prefix that exists but holds entirely different content — a reader tracing TR-026 lands on the P2 resolved-entity migration | Renumber all 43 rows to the live chain |
| A-003 | Phasing | HIGH | `spec.md` TR-084/TR-086, SC-028; `tasks.md` T048/T049 | SC-028 is `[OBJ3]`, and OBJ3 is P1, but the privileges migration is sequenced after a P2 migration. Dropping P2 leaves a P1 objective incomplete. The dependency is not technical — the migration only touches tables `0006` creates | Renumber the privileges migration to sit immediately after `0006`; move its tasks into the OBJ3 phase |
| A-004 | Requirement contradiction | HIGH | `spec.md` TR-065, TR-067 | TR-067 mandates the deferrable foreign key unconditionally; TR-065 permits an ordered fallback ladder. Both cannot hold | Make TR-067 conditional on the primary mechanism being viable, consistent with TR-065 |
| A-005 | Scope contradiction | HIGH | `spec.md` TR-053 vs Scope › Excluded | TR-053 specifies read-time risk arithmetic, which Scope explicitly assigns to E008/E010 | Restate as a storage obligation: the residual is stored so the beyond-horizon answer is derivable, without this epic computing it |
| A-006 | Ambiguity | HIGH | `spec.md` TR-046 vs TR-074 | TR-046 keys `document` by the corpus manifest identifier; TR-074 requires one row per source-and-project pair under a distinct key. Unresolved, and it changes every chunk foreign key | State that the manifest carries one entry per source-and-project pair, so the manifest key already is the per-project key |
| A-007 | Duplication | MEDIUM | `spec.md`, 13 clusters over 46 IDs | The checklist pass split single obligations into statement + mechanism + fallback triples. Roughly 60 distinct obligations across 86 IDs | **User judgment** — merging reverses an explicit decision to keep all 33 additions |
| A-008 | Coverage | MEDIUM | `spec.md`, 24 requirements | TR-053, 054, 056–065, 068, 074–083, 085 have no validation criterion and no success criterion | **User judgment** — coupled to A-007; roughly half reclassify into Scope, Assumptions, Glossary, or Integration Points |
| A-009 | Task graph | MEDIUM | `tasks.md` T021→T023, T029→T031, T036→T039, all "Extend test_*.py" runs | Same-file sequential tasks carry no machine-readable `after:` edges. A scheduler reading tags alone would treat them as independent and corrupt a shared migration file | Add explicit `after:` edges to every same-file chain |
| A-010 | Ambiguity | MEDIUM | `spec.md` TR-041, TR-077 | Both reference "the declared format" for `document_id`; no spec requirement declares it. It exists only in `data-model.md` | Declare the format in TR-041 |
| A-011 | Requirement quality | MEDIUM | `spec.md` TR-060, TR-064, TR-076, TR-078, TR-079, TR-080, TR-081, TR-085 | Non-obligations phrased as MUST — reader beliefs, other-epic obligations, hypothetical future work, consequences of exclusions | **User judgment** — coupled to A-007 |
| A-012 | Lifecycle inversion | MEDIUM | `spec.md` TR-056, TR-065, TR-076, TR-083 | Four spec requirements make `data-model.md`, a Plan-phase artifact, normative. A plan re-run can invalidate a spec requirement | **User judgment** — architectural call about artifact authority |
| A-013 | Staleness | MEDIUM | `spec.md` Compliance Check | Audits a 52-requirement spec; TR-053–TR-086 are unaudited. The block already flags its own re-audit as outstanding | Replace with the result of this pass |
| A-014 | Coverage gap | MEDIUM | `spec.md` Document key entity, OBJ2 deliverables and criteria | TR-075's five mandatory provenance columns appear in no key entity, deliverable, or validation criterion | Add to the Document entity and an OBJ2 criterion |
| A-015 | Staleness | LOW | `plan.md` Data Model Summary preamble | "9 migration prefixes" — the live chain has 10 | Correct the count |
| A-016 | Omission | LOW | `plan.md` Source Code delta | `script.py.mako`, which carries TR-002/TR-004 across every future migration, is absent | Add it |
| A-017 | Wrong verdict | LOW | `plan.md` Instructions Check | Principle VII marked N/A, but AD-005 and the gap-disclosure record are exactly VII machinery | Split VII out as PASS |
| A-018 | Staleness | LOW | `spec.md` header | `Status: Draft` against `spec_maturity: clarified` and existing plan/tasks/data-model | Update to Clarified |
| A-019 | Convention | LOW | `spec.md` TR-052, TR-086 | Out of ID order — the fingerprint of append-without-integration | Reorder |
| A-020 | Grammar | LOW | `spec.md` TR-052 | "System MUST have" is the wrong subject for a document edit | Restate as "This epic MUST correct" |
| A-021 | Evidence understated | LOW | `plan.md` Governance gate row | TR-052 edits a registered document; the justification (the registered document is internally self-inconsistent) is not stated, so it reads as a downstream artifact overriding | State the justification |

## Quality Summaries

**Spec Quality** — FAIL, 21/26. Structure, sections, and ID integrity are clean; the failures are all consequences of the checklist pass appending 33 requirements without integrating them. Three genuine contradictions (A-004, A-005, A-006), 13 duplicate clusters, 24 uncovered obligations.

**Compliance** — FAIL: 1 CRITICAL, 2 HIGH, 4 minor. Principles I, II, III, V all PASS with strong evidence. Source layout, two-category QC policy, technology stack, data provenance, and ADR governance all PASS. The CRITICAL is a build-context contract break introduced by a late plan edit.

**Task Coverage** — clean. All TR-001…TR-086 carry at least one task tag; no gaps, no out-of-range IDs. Three `[COMPLETES]` markers land correctly on the only three requirements spanning 3+ tasks.

## Coverage Summary

| Requirement Key | Has Task? | Notes |
|-----------------|-----------|-------|
| TR-001 … TR-086 | Yes, all 86 | Verified by tag extraction, not description matching |
| SC-001 … SC-028 | n/a | Success criteria are verified at QC, not task-mapped |

Unmapped tasks: T002, T003, T004 carry no requirement tag. All three are Setup-phase infrastructure, which the workflow exempts. Not gold-plating.

## Metrics

- Total requirements: 86 · Total tasks: 54 · Requirement coverage: 100%
- Distinct obligations after de-duplication: ~60 (A-007)
- Requirements without acceptance coverage: 24 (A-008)
- Findings: 21 — CRITICAL 1, HIGH 5, MEDIUM 8, LOW 7
