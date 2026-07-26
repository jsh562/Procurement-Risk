# Analysis Report: Core Data Schema

**Feature**: `00002-core-data-schema` (E003) | **Date**: 2026-07-26 | **Mode**: Analysis, then remediation
**Governing document**: `project-instructions.md` **v1.2.0** — this run is the mandatory re-run required when a feature's recorded audit names a superseded version.

Supersedes the 2026-07-25 report, which audited against v1.1.3.

## Findings

| ID | Category | Severity | Location(s) | Summary | Recommendation |
|----|----------|----------|-------------|---------|----------------|
| B-001 | Instructions violation | CRITICAL | `data-model.md:96`; `spec.md:129, 344, 359`; `plan.md:39, 86, 225`; `tasks.md:70, 75`; `checklists/data-integrity.md:43` | `document.issuing_body` is unconditionally `NOT NULL`, so a SYNTHETIC row cannot be inserted without a fabricated attribution — the exact failure v1.2.0 names Principle I as existing to prevent. `retrieval_date` is already layer-conditional; `issuing_body` and `source_ref` are not | Make both nullable with REAL-only presence checks *and* SYNTHETIC-only absence checks, mirroring `ck_document__real_has_retrieval_date`. Restate TR-075 per layer; split OBJ2 VC7 into REAL and SYNTHETIC branches |
| B-002 | Instructions violation | CRITICAL | `data-model.md:90-100`; `spec.md` (absent) | v1.2.0 requires a generated document to record generator identity, seed, generation date, and fixture content hashes. The table carries only `roster_hash`. No requirement anywhere mandates the other three, so a SYNTHETIC row cannot record the provenance the instructions mandate | Add four SYNTHETIC-conditional columns and a new TR-087; migration `0003` is unwritten so this is additive |
| B-003 | Instructions violation | CRITICAL | `spec.md:81, 84, 96, 276, 307, 394, 403, 457`; `plan.md:36, 51, 58, 60, 123, 157, 187, 257, 271, 272, 282, 291, 292`; `tasks.md:35, 39, 63, 64, 170, 171` | ADR-0011 mandates console entry points for modeling-owned jobs; v1.2.0 propagates it as mandatory. The plan specifies a Dockerfile plus a Compose `migrate` service. **Additionally unbuildable**: `src/model/pyproject.toml` sources `gateway` from `../gateway`, outside any context rooted at `src/model`, which ADR-0011 states in its Context | Delete T012. Replace the image with a `[project.scripts]` console entry point invoked as `uv run --directory src/model migrate`. `src/.dockerignore` stays untouched and no `src/model/.dockerignore` is needed |
| B-004 | Instructions violation | CRITICAL | `spec.md:306, 417, 427, 499`; `tasks.md:135`; `plan.md:40, 202, 278`; `checklists/data-integrity.md:49` | v1.2.0: *"A feature branch records the need for an amendment and does not perform it."* TR-052 and T054 perform an edit to `specs/project-plan.md`, a document named in that clause. SC-027 makes `.qc-passed` depend on that edit, which would deadlock the QC gate | Restate TR-052 as record-the-need; T054 writes the record; drop `project-plan.md` from the plan's file delta; restate SC-027 against the recorded request |
| B-005 | Traceability | MEDIUM | `plan.md:24-42` | The plan's Instructions Check carries no governing-version stamp. v1.2.0's drift-detection clause depends on that stamp, so a plan without one is undetectable drift by construction | Stamp `v1.2.0` after B-001…B-004 land, not before |
| B-006 | Task-graph error | MEDIUM | `tasks.md:156, 158, 165, 167` | The Dependencies prose still describes the pre-swap chain — Phase 7 as `0009`, Phase 8 as `0010`, and `after:T046` on T048 — while the task lines correctly read `0009` privileges → `0010` resolved-entity with `after:T039` / `after:T048`. An implementer following the prose rebuilds the P1-after-P2 ordering that was removed | Correct all four lines to `0008` (T036–T039) → `0009` (T048) → `0010` (T045, T046) |
| B-007 | Requirement inconsistency | MEDIUM | `spec.md:334` vs `102, 171, 240, 386, 424` | TR-065 defines a three-rung fallback ladder; five other locations describe two rungs and call the trigger "the single sanctioned fallback". The middle rung is unreachable as written | Drop the middle rung from TR-065 — no other location references it |
| B-008 | Unverified premise | MEDIUM | `spec.md:343, 346` | TR-074's load-bearing premise is that E002's manifest carries one entry per source-and-project pair. TR-077 obliges E002/E006 to adopt the `document_id` *format* only. If E002 emits one entry per source, TR-074 is silently unsatisfiable | Extend TR-077 to cover manifest granularity |
| B-009 | Convention breach | LOW | `plan.md:287-292` | Six Implementation Hints against a documented cap of five | Resolved as a side effect of B-003, which deletes HINT-005 and HINT-006 |
| B-010 | Governance gap | LOW | `specs/00002-core-data-schema/` vs ADR-0011 front matter | Workspace prefix `00002` is held by both this epic (E003) and E002. v1.2.0 added epic-start claiming for migration and decision-record numbers but not for workspace numbers | Out of scope to resolve unilaterally — renaming mid-flight breaks every path. Record as an amendment request alongside B-004 |
| B-011 | Requirement quality | LOW | `spec.md:322` | TR-053 has no testable delta over TR-030 and TR-055, and no VC or SC of its own | Fold or accept as documentation |

### Deferred by explicit decision — third consecutive appearance

A-007, A-008, A-011, A-012 from the prior report, unchanged: roughly 13 duplicate clusters across 46 of the 86 requirement IDs, ~24 requirements with no acceptance coverage, and nine non-obligations phrased as MUST. Left unapplied pending a user decision. The validator's recommendation stands — a third deferral should be recorded as an explicit scope decision with a reversal trigger rather than carried as a pending item.

## Quality Summaries

**Compliance** — **FAIL** against v1.2.0: 4 CRITICAL, 3 MEDIUM, 2 LOW. Principles II, V, VII pass with strong evidence. Principles I and III now fail on the same root cause — the document row compels a fabrication where it should record an absence. Source layout, two-category QC policy, and technology-stack language/storage all pass.

**Spec Quality** — FAIL, 20/25. The three contradictions fixed in the prior pass all hold: TR-067 is now subordinate to TR-065's ladder, TR-053 is a storage obligation, TR-074 resolves the key space. ADR renumbering is clean — zero stale references, and none silently resolving to the parallel branch's ADR-0011.

**Coverage** — clean. All TR-001…TR-086 carry a task tag and a plan coverage row. 54 tasks. Both artifacts agree on the ten-migration chain.

## Metrics

- Requirements 86 · Tasks 54 · Coverage 100% · Findings 11 — CRITICAL 4, MEDIUM 4, LOW 3
- Deferred by prior decision: 4 finding clusters
