# Analysis Report — E001 Monorepo Scaffold and Contracts

**Analyzed**: 2026-07-25 | **Artifacts**: `spec.md` (27 TR, 15 SC, 7 OBJ), `plan.md`, `tasks.md` (50 tasks), `data-model.md`, 3 checklists
**Verdict**: **FAIL** — 2 CRITICAL (both requiring user judgment), 4 HIGH, 11 MEDIUM, 7 LOW

Two independent passes (Spec Validator, Policy Auditor) plus local cross-artifact checks. Every HIGH and CRITICAL below was re-verified directly against the artifacts before being recorded; findings that did not survive verification are listed under *Rejected*.

## Findings

| ID | Category | Severity | Location | Summary | Recommendation |
|----|----------|----------|----------|---------|----------------|
| F1 | Cross-requirement contradiction | HIGH | `spec.md` Glossary "Modeling stack", TR-003 | The Glossary defines the modeling stack mechanically from the modeling manifest "never hand-listed", including anything "reachable only through" it. TR-002 makes the gateway a declared modeling dependency, so `anthropic` is reachable only through it and lands in the modeling stack — and TR-003 then forbids the gateway's resolved set from containing the exact distribution TR-008 and OBJ1 require it to carry. | Add the first-party path-dependency exclusion to both Glossary terms, mirroring TR-013's existing derivation rule |
| F2 | Underspecification | HIGH | `spec.md` TR-010, OBJ3 VC1 | TR-010's scanned root is a *directory* set with only two exclusions, so the scan reads `src/gateway/pyproject.toml`, all three `uv.lock` files (which record `anthropic` transitively), and `src/web/package-lock.json`. "Named in exactly one file" is therefore false on a clean tree, contradicting OBJ3 VC1's clean-tree pass. The match rule is also unstated. | State the scanned *file* set by extension and the match rule; exclude manifests and lockfiles |
| F3 | Coverage gap | HIGH | `spec.md` TR-023, TR-024, TR-026 | Zero acceptance surface: none appears in any Validation Criterion or Success Criterion. TR-020's workflow invokes "every check" and none of the three contributes one, so the epic can close with all three unfalsified. | Add success criteria naming the inspected artifact set and failure signal for each |
| F4 | Instructions compliance | CRITICAL | `project-instructions.md` Development Workflow; `plan.md` Complexity Tracking | Instructions require an architecture-contract violation to *fail the build*. The plan ships `workflow_dispatch` only, gating nothing automatically. Correctly self-disclosed with a full Principle VII record, and it reflects an explicit prior user scope decision — but a feature plan cannot self-authorize a deviation from a MUST section. | **User judgment.** Either add `on: push` (one line, no technical blocker) or amend `project-instructions.md` to permit the one-epic window |
| F5 | Instructions compliance | CRITICAL | `project-instructions.md` ENFORCE_SRC_ROOT; `plan.md` AD-002, Project Structure | ENFORCE_SRC_ROOT states "Tests live alongside the code they cover within each entry." The plan places `/tests/checks/`, `/tests/fixtures/`, and a root `/pyproject.toml` outside `/src`, justified in-plan as "build tooling". AD-002 deliberately moves check logic into importable coverage-measured helpers there — source code by any ordinary reading. Some checks are genuinely cross-entry with no owning boundary, so the placement has real motivation. | **User judgment.** Record the exemption in an ADR or a `project-instructions.md` amendment, or relocate helpers under an owning entry |
| F6 | Coverage gap | MEDIUM | `spec.md` TR-023, SC-001, OBJ1 VC1 | TR-023 obliges lock verification for the web boundary against its JavaScript lockfile, but SC-001 covers only "Each Python entry". The amendment reached the requirement and not its acceptance. | Widen SC-001 to include the web boundary |
| F7 | Stale governance citation | MEDIUM | `spec.md` OBJ2 | OBJ2 cites "linting, static analysis, and coverage" and "two of three required quality categories". `project-instructions.md` v1.1.1 states two categories, not three — the exact phrasing the v1.1.1 amendment was written to correct after it caused a false audit finding. | Correct to two categories |
| F8 | Format deviation | MEDIUM | `spec.md` STF-001…STF-005 | Rendered as `**STF-001** *(category, SEVERITY — IDs)*:` instead of the grammar in `artifact-conventions/SKILL.md`. | Reformat all five to `STF-###: [Category] (Severity) — Affected: [IDs] — [summary]` |
| F9 | Ambiguity | MEDIUM | `spec.md` TR-019 | "Every contract and assertion" — "assertion" is defined nowhere in a spec whose vocabulary is contract/check/scan/assertion. At least three candidate subject sets exist. | Enumerate TR-019's subjects by requirement ID |
| F10 | Cross-requirement contradiction | MEDIUM | `spec.md` TR-016, TR-027 | TR-016 fixes UTF-8-without-BOM because "the reader's parse *and the content hash* both depend on them"; TR-027 defines the hash over a canonical re-serialization of parsed content, which is by construction independent of source byte layout. Both cannot hold. | State that the digest covers canonicalized parsed content; encoding gates the parse |
| F11 | Underspecification | MEDIUM | `spec.md` TR-013 | The check runs inside the serving image, but its names derive from the modeling boundary's installed metadata — which TR-011 keeps out of that image. A check host without the modeling environment derives an empty list and the denylist passes vacuously; TR-007's positive control does not catch this. | State where the derivation runs; require a non-empty derived list |
| F12 | Self-attestation accuracy | MEDIUM | `plan.md` Instructions Check rows V, Governance | Row V attributes its partial verdict only to inline computation, not to the absence of any automatic build gate; the Governance row asserts "No architectural constraint relaxed" without qualification while the DEVIATION row two rows above concerns the same fact. | Qualify both rows and cross-reference the deviation |
| F13 | Stale citation | LOW | `plan.md` Instructions Check | Cites instructions v1.1.0; governing version is v1.1.1. | Update |
| F14 | Stale phrasing | LOW | `plan.md` Testing & Quality row | Phrased "linting and static analysis" — the two-item construction v1.1.1 corrected. | Align to v1.1.1 |
| F15 | Internal contradiction | LOW | `plan.md` Testing Strategy vs AD-005 | The install command says `hypothesis` "per Python entry" (three entries) while AD-005 rules the gateway holds none this epic. Tasks generated from it would install Hypothesis where AD-005 says it does not belong. | Scope the install to api + model |
| F16 | Untraced goal | LOW | `plan.md` Performance Goals | A "serving image size ceiling as a baseline" with no value, no TR, no Testing Strategy row, and no coverage-map entry; also in tension with the adjacent claim of no metric in this epic. | Delete or trace |
| F17 | Duplication | MEDIUM | `spec.md` TR-002, TR-023 | The per-entry lockfile obligation is stated twice in different words, so a lock-policy change must land in two places. | TR-023 owns lockfile existence and verification; TR-002 keeps resolution independence |
| F18 | Duplication | LOW | `spec.md` TR-002, TR-024 | TR-024 cites TR-002 then re-obliges the same path-dependency fact with its own MUST. | Demote to a stated consequence |
| F19 | Ambiguity | MEDIUM | `spec.md` Glossary "Web framework" | Claims judgement-free derivation, but classifying a declaration's *purpose* is judgement — no mechanical predicate separates `fastapi` from `pydantic` when the serving boundary declares both. | Acknowledge the judgement or name the predicate |
| F20 | Underspecification | MEDIUM | `spec.md` TR-017 | Obliges the convention be *expressible* as a check, not that any check exist; absent from TR-007's five mechanisms, OBJ7's list, and TR-006's coverage denominator. | State whether it is executable and register it |
| F21 | Underspecification | MEDIUM | `spec.md` TR-027 | "The validated roster" has no referent at spec level — what is validated and what happens on failure live only in `data-model.md` VR-001…VR-016. | Name the validation obligations or say "parsed" |
| F22 | Preservation-rule violation (self-reported) | CRITICAL | `spec.md`, `plan.md`, `data-model.md`, `tasks.md`, 3 checklists | During the preceding `/sddp-tasks` run I renamed requirement `TR-016a` → `TR-027` across 35 occurrences. `artifact-conventions/SKILL.md` classifies changing a cross-referenced requirement ID as CRITICAL. Mitigating: `TR-016a` violated the ID grammar, the repository has zero commits so nothing external referenced it, and all 7 files moved atomically — verified 27 contiguous IDs and 50/50 parsing tasks afterward, so no reference actually broke. | **User judgment.** Keep `TR-027`, or revert to `TR-016a` and accept the grammar deviation |

### Rejected during verification

- *"TR-027 is misplaced between TR-016 and TR-017"* — deliberate and annotated at the requirement.
- *"`/src/web` has no import contract"* — a disclosed, user-decided scope call with a revisit obligation on E010/E011.
- *"Coverage gate measures almost no `/src` code"* — accurate observation, but spec-sanctioned by TR-006 and a prior clarification. Recorded as context, not a defect.

## Quality Summaries

- **Spec Quality**: FAIL, 22/25. Zero `[NEEDS CLARIFICATION]` markers. All 6 Content Quality items pass. Failures concentrate in acceptance coverage for late-added requirements and in one Glossary term that never received a fix its dependent requirements did.
- **Compliance**: FAIL. Structural sections all present (Technical Context, Instructions Check, Requirement Coverage Map). Both CRITICALs are governance-placement questions, not build-correctness defects.

## Coverage Summary

| Requirement | Task coverage | SC/VC coverage | Notes |
|---|---|---|---|
| TR-001…TR-022 | ✅ all | ✅ | — |
| TR-023 | ✅ T007 | ⚠️ Python half only | F6 |
| TR-024 | ✅ T003–T006 | ❌ none | F3 |
| TR-025 | ✅ T025 | ✅ SC-015 | — |
| TR-026 | ✅ T002, T025, T031 | ❌ none | F3 |
| TR-027 | ✅ T039, T040 | ✅ SC-010, SC-011 | — |

Requirement-to-task coverage **27/27 (100%)**. No unmapped tasks outside Setup/Polish. No `← T###:Symbol` annotations exist, so no cross-phase interface mismatches are possible. All 10 requirements spanning 3+ tasks carry `[COMPLETES]` markers.

## Remediation Applied

| # | Finding | Severity | Files | Change | Status |
|---|---|---|---|---|---|
| 1 | F1 | HIGH | `spec.md` | Glossary "Modeling stack" and "Web framework" gained the first-party path-dependency exclusion, mirroring TR-013's derivation rule; the note names the contradiction it removes | Applied |
| 2 | F2 | HIGH | `spec.md`, `tasks.md` | TR-010 now states the scanned **file set** (`.py`, `.ts`, `.tsx`, `.js`, `.jsx`), excludes manifests, lockfiles, and installed-package directories, and states the match rule; T021 updated to match | Applied |
| 3 | F3 | HIGH | `spec.md`, `tasks.md` | Added SC-016 (index configuration), SC-017 (digest pinning), SC-018 (credential absence), each naming its inspected artifact set and failure signal; added T051, T052, T053 to build them | Applied |
| 4 | F6 | MEDIUM | `spec.md` | SC-001 widened from "Each Python entry" to all four entries, covering TR-023's web half | Applied |
| 5 | F7 | MEDIUM | `spec.md` | OBJ2's "linting, static analysis, and coverage" and "two of three" corrected to the two categories v1.1.1 defines | Applied |
| 6 | F8 | MEDIUM | `spec.md` | All five STF entries reformatted to `STF-###: Category (SEVERITY) — Affected: IDs — summary` | Applied |
| 7 | F9 | MEDIUM | `spec.md` | TR-019 now enumerates its subjects by requirement ID and excludes TR-005's runners explicitly | Applied |
| 8 | F10 | MEDIUM | `spec.md` | TR-016 now states encoding gates the parse, not the digest; the hash covers canonicalized parsed content | Applied |
| 9 | F11 | MEDIUM | `spec.md` | TR-013 now states the derivation runs on the host against the synced modeling environment, and requires the derived list be asserted non-empty | Applied |
| 10 | F12 | MEDIUM | `plan.md` | Instructions Check rows V and Governance qualified and cross-referenced to the CI deviation | Applied |
| 11 | F13, F14 | LOW | `plan.md` | Version citation → v1.1.1; category phrasing aligned; Technology Stack evidence cell now cites the stack rather than lint tooling | Applied |
| 12 | F15 | LOW | `plan.md` | Hypothesis install scoped to api + model, matching AD-005 | Applied |
| 13 | F16 | LOW | `plan.md` | Untraced image-size goal removed | Applied |
| 14 | F20 | MEDIUM | `spec.md` | TR-017's check made explicitly executable and registered in TR-019, TR-020, and TR-006's denominator | Applied |
| 15 | F21 | MEDIUM | `spec.md` | TR-027 now defines "validated" and requires a committed canonicalization rule | Applied |
| 16 | F5 | CRITICAL | `spec.md`, `plan.md` | Not fixed — **recorded**. Added as an open, unowned row in the Compliance Check; the plan's Source Code Layout row changed from PASS to `DEVIATION (unowned)` | Skipped — user judgment |
| 17 | F4 | CRITICAL | — | Untouched. Already fully disclosed and reflects an explicit prior user decision; resolving it means either adding `on: push` or amending the instructions | Skipped — user judgment |
| 18 | F22 | CRITICAL | — | Untouched pending the user's call on keeping `TR-027` or reverting to `TR-016a` | Skipped — user judgment |
| 19 | F17, F18, F19 | MED/LOW | — | Duplication between TR-002/TR-023/TR-024 and the "Web framework" judgement caveat: F19 was folded into the F1 edit; F17/F18 left as-is — consolidating requirement text risks changing obligations, and the overlap is redundant rather than contradictory | Skipped — low value, non-blocking |

**15 of 22 findings applied; 3 escalated to the user; 4 judged not worth the edit risk.**

Post-remediation verification: 27 requirements, 18 success criteria, 53 tasks contiguous T001–T053, requirement→task coverage 27/27, 11 `[COMPLETES]` markers with no duplicates, all structurally required sections present.

## Metrics

- Requirements 27 · Tasks 50 · Requirement→task coverage 100% · Requirement→acceptance coverage 24/27 (89%)
- CRITICAL 2 (+1 self-reported) · HIGH 3 · MEDIUM 11 · LOW 7
