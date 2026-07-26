# Analysis Report: Core Data Schema

**Feature**: `00003-core-data-schema` (E003) | **Date**: 2026-07-26 | **Mode**: Analysis, then remediation
**Governing document**: `project-instructions.md` **v1.2.0** — this run is the mandatory re-run required when a feature's recorded audit names a superseded version.

Supersedes the 2026-07-25 report, which audited against v1.1.3. **Superseding its verdicts is not the same as deleting its findings** — see the restoration note below.

## Prior Pass — A-Series (2026-07-25, audited against v1.1.3)

> **These 21 rows were deleted, not superseded, and are restored here from git.** The v1.2.0 pass renumbered its findings to a B-series and overwrote this file in place, leaving zero `A-` rows — while `spec.md`, `.qc-passed`, and this report's own Deferred section continued to cite **A-007, A-008, A-011 and A-012** by ID. Four references therefore pointed at nothing, and A-012's definition existed nowhere in the working tree; it was recovered with `git show 7138026:specs/00003-core-data-schema/analysis-report.md`.
>
> Replacing stale *verdicts* was correct — they audited a governing version that no longer applied. Deleting the *definitions* of items still open and cited elsewhere was not, and the cost was real: resolving A-012 required git archaeology to discover what it had said. Restored rather than left as a footnote so every cited ID resolves in the file that owns it.
>
> **This section is a restoration, not the original history.** The rows are verbatim from `7138026`; the Disposition column is added and did not exist in the original.

| ID | Category | Severity | Summary | Disposition |
|----|----------|----------|---------|-------------|
| A-001 | Instructions violation | CRITICAL | `src/.dockerignore` is a shared allowlist for the whole `/src` context; admitting `model` breaks two build-context checks and relaxes an architectural constraint with no superseding ADR | Applied in `021f0eb`, then **overtaken by B-003** — ADR-0011 removed the image entirely, so no context is rooted at `src/model` at all |
| A-002 | Consistency | HIGH | `plan.md` Requirement Coverage Map, 43 rows written against the retired 11-prefix chain; 28 named a prefix holding different content | Applied in `021f0eb` — all rows renumbered to the live ten-migration chain |
| A-003 | Phasing | HIGH | SC-028 is OBJ3 (P1) but the privileges migration sat after a P2 migration, so dropping P2 would leave a P1 objective incomplete | Applied in `021f0eb` — privileges moved to `0009`, ahead of OBJ6's `0010` |
| A-004 | Requirement contradiction | HIGH | TR-067 mandated the deferrable FK unconditionally while TR-065 permitted a fallback ladder; both cannot hold | Applied in `021f0eb` — TR-067 made subordinate to TR-065 |
| A-005 | Scope contradiction | HIGH | TR-053 specified read-time risk arithmetic, which Scope assigns to E008/E010 | Applied in `021f0eb` — restated as a storage obligation |
| A-006 | Ambiguity | HIGH | TR-046 keyed `document` by manifest identifier; TR-074 required one row per source-and-project pair under a distinct key | Applied in `021f0eb` — the manifest carries one entry per pair, so the manifest key *is* the per-project key |
| A-007 | Duplication | MEDIUM | Checklist pass split single obligations into statement + mechanism + fallback; ~13 clusters over 46 of 86 IDs | Deferred 5 phases. **Closed post-QC as WILL-NOT-MERGE on evidence** — 15 clusters over 51 IDs, only TR-053 a true duplicate. See `spec.md` § Compliance Check |
| A-008 | Coverage | MEDIUM | 24 requirements with no validation criterion and no success criterion | Deferred 5 phases. **Closed post-QC, split** — 16 documentation gaps left on record, 5 of 8 verification gaps closed by test (T068-T071, T075), TR-064 unverifiable by nature |
| A-009 | Task graph | MEDIUM | Same-file sequential tasks carried no machine-readable `after:` edges; a scheduler reading tags alone would corrupt a shared migration file | Applied in `021f0eb` — explicit edges added to every same-file chain |
| A-010 | Ambiguity | MEDIUM | TR-041 and TR-077 both referenced "the declared format" for `document_id`; no requirement declared it — it existed only in `data-model.md` | Applied in `021f0eb` — format declared in TR-041. Note this is the same authority inversion A-012 names |
| A-011 | Requirement quality | MEDIUM | Non-obligations phrased as MUST — reader beliefs, other-epic obligations, hypothetical future work, consequences of exclusions | Deferred 5 phases. **Closed post-QC by reclassification** — 11, not 9; each now carries a trailing note naming what it is (T072) |
| A-012 | Lifecycle inversion | MEDIUM | TR-056, TR-065, TR-076, TR-083 make `data-model.md`, a Plan-phase artifact, normative over Specify-phase requirements. A plan re-run can invalidate a spec requirement | Deferred 5 phases; **definition lost to this file's overwrite and recovered from git**. **Closed post-QC as accepted**, mitigation named, ADR carried forward to `main` (T074) |
| A-013 | Staleness | MEDIUM | `spec.md` Compliance Check audited a 52-requirement spec; TR-053–TR-086 were unaudited | Applied in `021f0eb` — replaced with that pass's result |
| A-014 | Coverage gap | MEDIUM | TR-075's five mandatory provenance columns appeared in no key entity, deliverable, or validation criterion | Applied in `021f0eb`. Related gap survived to QC as **T066** — OBJ2 VC7 gained its criterion here but no test until post-QC |
| A-015 | Staleness | LOW | `plan.md` said "9 migration prefixes"; the live chain has 10 | Applied in `021f0eb` |
| A-016 | Omission | LOW | `script.py.mako`, which carries TR-002/TR-004 across every future migration, was absent from the Source Code delta | Applied in `021f0eb` |
| A-017 | Wrong verdict | LOW | Principle VII marked N/A, but AD-005 and the gap-disclosure record are exactly VII machinery | Applied in `021f0eb` — split out as PASS |
| A-018 | Staleness | LOW | `spec.md` header read `Status: Draft` against `spec_maturity: clarified` | Applied in `021f0eb` |
| A-019 | Convention | LOW | TR-052 and TR-086 out of ID order — the fingerprint of append-without-integration | **Not applied, and correctly so.** Reordering means renumbering, which changes requirement IDs already mapped to tasks and coverage rows. TR-052 and TR-087 remain out of sequence by design |
| A-020 | Grammar | LOW | "System MUST have" is the wrong subject for a document edit | Applied in `021f0eb` |
| A-021 | Evidence understated | LOW | TR-052 edits a registered document without stating the justification, so it read as a downstream artifact overriding | Applied in `021f0eb`, then overtaken by B-004 — TR-052 became record-the-need, so no edit is performed |

## Findings — B-Series (2026-07-26, audited against v1.2.0)

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
| B-010 | Governance gap | LOW | `specs/00003-core-data-schema/` vs ADR-0011 front matter | Workspace prefix `00002` is held by both this epic (E003) and E002. v1.2.0 added epic-start claiming for migration and decision-record numbers but not for workspace numbers | Out of scope to resolve unilaterally — renaming mid-flight breaks every path. Record as an amendment request alongside B-004 |
| B-011 | Requirement quality | LOW | `spec.md:322` | TR-053 has no testable delta over TR-030 and TR-055, and no VC or SC of its own | Fold or accept as documentation |

### Deferred by explicit decision — third consecutive appearance

A-007, A-008, A-011, A-012 from the prior report, unchanged: roughly 13 duplicate clusters across 46 of the 86 requirement IDs, ~24 requirements with no acceptance coverage, and nine non-obligations phrased as MUST. Left unapplied pending a user decision. The validator's recommendation stands — a third deferral should be recorded as an explicit scope decision with a reversal trigger rather than carried as a pending item.

> **RESOLVED after QC, 2026-07-26.** All four were examined against the delivered code and are now closed in `spec.md` § Compliance Check; per-ID dispositions are in the A-Series table above. The counts in the paragraph above did not survive that examination and should not be quoted from here: there are **15** clusters over **51** IDs of which **one** is a true duplicate, and **11** soft MUSTs rather than nine.
>
> A-007 is closed as will-not-merge on evidence — 14 of the 15 clusters are a general requirement plus genuine specialisations, and each specialisation is the sole traceability anchor for its own named constraint, so merging would delete evidence. A-008 split into 16 documentation gaps (left on record) and 8 verification gaps, 5 now closed by test. A-011 closed by reclassification in place. A-012 accepted with its mitigation named.
>
> **Process defect, recorded because it caused real cost.** A-012's definition was unrecoverable from this file: the pass that produced the B-series overwrote all 21 A-rows while three other artifacts still cited four of them, so resolving it required `git show 7138026:`. The rows are now restored above. The rule that would have prevented it — *an analysis pass that renumbers its finding series must archive the prior report rather than overwrite it, whenever its IDs are cited outside the report* — belongs in `.github/skills/analyze-compliance/SKILL.md`, which says only "write the complete analysis report to `FEATURE_DIR/analysis-report.md`" and nothing about the prior one. That skill is project-level, so the edit is **carried forward to `main`** alongside AR-1, AR-2 and the A-012 ADR, not applied from this branch.

## Quality Summaries

**Compliance** — **FAIL** against v1.2.0: 4 CRITICAL, 3 MEDIUM, 2 LOW. Principles II, V, VII pass with strong evidence. Principles I and III now fail on the same root cause — the document row compels a fabrication where it should record an absence. Source layout, two-category QC policy, and technology-stack language/storage all pass.

**Spec Quality** — FAIL, 20/25. The three contradictions fixed in the prior pass all hold: TR-067 is now subordinate to TR-065's ladder, TR-053 is a storage obligation, TR-074 resolves the key space. ADR renumbering is clean — zero stale references, and none silently resolving to the parallel branch's ADR-0011.

**Coverage** — clean. All TR-001…TR-086 carry a task tag and a plan coverage row. 54 tasks. Both artifacts agree on the ten-migration chain.

## Metrics

- Requirements 86 · Tasks 54 · Coverage 100% · Findings 11 — CRITICAL 4, MEDIUM 4, LOW 3
- Deferred by prior decision: 4 finding clusters
