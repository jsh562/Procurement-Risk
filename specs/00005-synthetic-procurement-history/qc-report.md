# QC Report — E005 Synthetic Procurement History

**Verdict**: **PASS**
**Iterations**: 2 (iteration 1 FAILED with 4 CRITICAL, 3 HIGH, 6 MEDIUM, 5 LOW; all closed)
**Audited against**: `project-instructions.md` v1.2.4
**Date**: 2026-07-27

## Counts

Derived by counting definitions within their own sections, never asserted as literals. A whole-file prefix count over `spec.md` returns 39 and 34, because the audit-history section restates amended IDs in the same bullet form.

| Quantity | Value |
|---|---|
| Functional requirements | 37 of 37 satisfied (FR-001…FR-037, contiguous) |
| Success criteria | 33 of 33 satisfied (SC-001…SC-033, contiguous) |
| Task lines | 86 closed, 0 open (T029a and T029b withdrawn, struck through, not renumbered) |
| Validation rules | 28 (DV-001…DV-028) |
| Negative controls | 12 (NC-1…NC-12, rendered as 13 rows since NC-11 split) |
| Disclosed gaps | 8 (G-1 closed; G-2…G-8 open and recorded) |
| Limitation records | 9 active (L-5 withdrawn, not renumbered) |

FR-034 and SC-026 are **live and satisfied**. Both were `BLOCKED` under AD-008 while E002 published neither `manufacturer` nor `part_number`; E002 published both on 2026-07-26, the gate discharged, and both rejoined the completion denominator. `WITHDRAWN` was never used and is retired unused.

## Evidence

| Check | Result |
|---|---|
| Test suite | **1725 passed, 11 skipped** — model entry, live PostgreSQL 16 on 5435 |
| Coverage | **93%** combined, threshold 80. `model.procurement` 93% across 15 modules |
| Lint | `ruff check` clean at `src/model`, `src/api`, `src/gateway`, `tests` |
| Format | `ruff format --check` clean, 131 files |
| Security | `ruff check --select S` clean over `src/model` |
| Architecture | `import-linter`: gateway 1 kept, api 1 kept, model 2 kept, **0 broken** |
| Entry points | `procurement-generate`, `procurement-load`, `procurement-validate` all run end to end |
| Reproduction | `sha256:138a0fbff44acd5bdfd72dcd263f02c9ac3e616a787bc90410c88cdfd684cb6b` reproduces from the recorded seed |

The 11 skips are `test_report_conformance.py`, which reads this file and skips before QC writes it.

## Realized against intended

Every figure is bounded by a check that refuses before the write path, except the two marked *disclosed*.

| Figure | Bound | Realized |
|---|---|---|
| Line count | 190–210 (FR-003) | 199 |
| Delivered share | `[max(80%, 160/N), 90%]` (FR-010, DV-010) | 0.879 |
| Censored share | ≥10% (FR-010, SC-016) | 0.121 |
| Corpus-overlap share | ≥60% (FR-032, DV-014) | 0.698 |
| Catalog-overlap share | ≥60% (FR-034, DV-028) | 0.698 |
| Spread ratio, category-adjusted | 0.12–0.49 (FR-008, FR-036, DV-011) | 0.3064 |
| Spread ratio, unadjusted | recorded (FR-036) | 0.2674 |
| Aggregate median | 61 ± 5 days (SC-023, DV-012) | 58.0 |
| Aggregate P80 | 94 ± 8 days (SC-023, DV-012) | 90.4 |
| Delivered-only median / P80 | *disclosed, untoleranced* (FR-007) | 53.0 / 84.0 |
| Late-delivery share | 25–35% of delivered (FR-011, DV-013) | 0.263 |
| Already-overdue censored | recorded separately (SC-024) | 8 |
| Rework allocation | equality with declared (FR-006, DV-009) | (42, 13, 5) |

## Iteration 1 findings, all closed

**CRITICAL — DV-012 and DV-013 implemented nowhere.** Both were defined in `data-model.md` and listed in `plan.md` as fail-fast breaches. The aggregate median/P80 and the late-delivery band were computed for the datasheet and bounded by nothing. Now gates in `generate()`, ahead of the write path, with `test_aggregate_gates.py` asserting the refusal rather than the number. **This was the third occurrence in this epic of a value recorded in an artifact that no check enforces**, which is why the closing change asserts the gate is *called*, not merely that it exists.

**CRITICAL — five required disclosures absent from the datasheet.** Delivered-only median and P80 (FR-007), realized censored share (FR-010, SC-016), the declared per-vendor vector with its realized dispersion (FR-004, SC-002), the realized per-project split (FR-003), and the already-overdue censored count (SC-024). Each was computed and used; a reader of the datasheet alone could recover none of them.

**CRITICAL — the provenance section was headed "Collection Process".** FR-014 requires *Generation Process*, and labelling a wholly-generated dataset's provenance as collection is the retrieval/generation confusion the Data Provenance clause exists to prevent. Both checks were tautological: they iterated `SECTION_TITLES` imported from the implementation, so neither could fail on a wrong name. The names are now asserted as literals.

**CRITICAL — the report-conformance check did not exist.** T078 was closed against a file that was never created, so `plan.md`'s "assertable conditions of the QC report, not guidance about it" were guidance after all.

**HIGH — `tasks.md` still forbade satisfying FR-034.** "No task attempts to satisfy them, **and none may**" survived a day past the discharge that removed it from `data-model.md` — the plan's own recorded lesson about the half that says *this cannot be done* outliving the half that says *this is not done*.

**HIGH — the published tercile cut points came from a second implementation.** `generate.py` computed them by index while `criticality.py` assigned bands by rank; the published value was one position off the real boundary. Unified.

**HIGH — six task lines named test files that do not exist.** Five had their substance elsewhere and are repointed; the sixth was the missing report-conformance check.

**MEDIUM and LOW** — a vacuous seeds test that called one function twice with identical arguments; a DV-004 null guard resting on a premise that stopped being true when the complement changed; four stale counts in `tasks.md`; the half-propagated seventh-module correction; a duplicated `G-4` identifier, renumbered to `G-8`; three docstrings naming superseded constants.

## Open and disclosed

**FR-032's complement cannot fail every clause.** Clause 1 asks whether `material_category` is a key of the committed map, which DV-004 requires of every line; clause 4 asks whether `vendor_id` resolves through the roster, which FR-001 requires of every line. The requirement is restated to "every clause any line can fail" and recorded as **gap G-7** rather than resolved by weakening DV-004 or FR-001. The two discriminating clauses are 2 and 3, and the complement fails both on every line.

**The red-green obligation is evidenced for six of seven mandatory modules.** `plan.md` § The test-first observable makes the branch history the observable. Six pairs carry a `test:` commit before their `feat:` commit; **T026 and T027 for `equipment.py` landed inside one commit**. History cannot be rewritten to manufacture the evidence, and doing so would be the simulation the execution policy forbids. Recorded as a real miss in `tasks.md`'s validation table (6 / 7) rather than closed.

**Carried open by explicit user decision, unchanged**: A-006, A-007, A-009, A-010, A-011, A-019, A-020, A-021, the SC-008 leg of A-029, A-030, A-032. **A-020** — FR-008's band is derived without a category term while the ratio asserted against it is category-adjusted — reaches the datasheet's reader in limitation record L-4 rather than living only in an internal report.

**Disclosed gaps**: G-2 (one-vendor-per-order is generator-only), G-3 (two digest conventions), G-4 (realized purchase-order size distribution vs the declared cycle), G-5 (post-split count unobserved), G-6 (empty non-terminal state possible under an unlucky seed), G-7 (above), G-8 (no row-level generation provenance). G-1 is closed by the trigger it declared.

## Environment

PostgreSQL 16.14 on `${PRC_DB_PORT:-5434}`, resolved here to 5435 to avoid a sibling checkout. `tests/checks/test_orchestration.py` was never run — its teardown destroys the database volume.
