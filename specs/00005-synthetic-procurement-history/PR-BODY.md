# E005: Synthetic Procurement History

A wholly synthetic procurement delivery history — 199 purchase-order lines across 5 projects and 12 vendors, 1388 lifecycle events — committed with its digest, a datasheet, and a ground-truth record held outside every fitting input root.

**QC passed.** 37/37 functional requirements, 33/33 success criteria, 1725 tests, 93% coverage against an 80 threshold, ruff clean at four roots, `ruff --select S` clean, import-linter 4 contracts kept / 0 broken. Full evidence in `specs/00005-synthetic-procurement-history/qc-report.md`.

## What ships

Three console entry points — `procurement-generate`, `procurement-load`, `procurement-validate` — and 15 modules under `model.procurement`.

The loader is **not an upsert**: it stages into `TEMP` tables, compares with `EXCEPT ALL` in both directions over an enumerated 17-field line and 6-field event projection, then refuses or inserts. `ON CONFLICT DO NOTHING` would silently tolerate a divergent row under the same natural key, which is the precise case FR-026 must refuse.

The validator **regenerates** from the recorded seed rather than re-reading the file, so reproduction is an oracle rather than a checksum. The dataset reproduces byte-identically under a fresh process, three time zones, three locales, a changed `PYTHONHASHSEED` and a changed working directory.

## Cross-epic

E002 published `manufacturer`, `part_number` and a manufacturer catalog during this epic, discharging the gate FR-034 and SC-026 had shipped under. The reversal trigger recorded against that gate is what detected it, rather than someone remembering to look. `WITHDRAWN` was never used and is retired unused.

## Carried open, deliberately

**FR-032's complement cannot fail every clause.** Clause 1 asks whether the material category is a key of the committed map, which DV-004 requires of every line; clause 4 asks whether the vendor resolves through the roster, which FR-001 requires of every line. The requirement is restated to "every clause any line can fail" and recorded as gap **G-7** — weakening either requirement so the original sentence became true would trade a real guarantee for a rhetorical one.

**The red-green obligation is evidenced for six of seven mandatory modules.** T026 and T027 for `equipment.py` landed in one commit, so the branch history does not carry the `test:` commit before the `feat:` one. `tasks.md` records **6/7**, not 7/7. Rewriting history to manufacture the evidence would be the simulation the execution policy forbids.

**Eleven analysis findings** remain open by earlier decision: A-006, A-007, A-009, A-010, A-011, A-019, A-020, A-021, the SC-008 leg of A-029, A-030, A-032. **A-020** — FR-008's band is derived without a category term while the ratio asserted against it is category-adjusted — reaches the datasheet's reader in limitation record L-4 rather than living only in an internal report.

## Defects the work surfaced

Each was invisible until something actually ran:

- **A rework loop is three transitions, not two.** Every rework line ran one event short and could never reach `delivered`. All unit tests passed, because the short line simply looked censored. Found by the first end-to-end generation.
- **The shape gate answered a different question than the artifact** — 0.874 delivered against the fixture's 0.618, both internally consistent and neither checkable against the other.
- **DV-012 and DV-013 were defined and implemented nowhere.** The aggregate median/P80 and the 25–35% late-delivery band were computed for the datasheet and bounded by nothing. Found by QC.
- **The complement's manufacturer and part number could not be `NULL`** — the delivered schema declares both `NOT NULL`. The artifact would have been generated, committed, and then refused at load.
- **The provenance section was headed "Collection Process"** where FR-014 requires "Generation Process", and both checks were tautological: they iterated the implementation's own list, so neither could fail on a wrong name.

## Artifacts

`data/procurement/procurement-history.json` · `data/procurement/procurement-history.hash.json` · `data/procurement/datasheet.md` · `data/ground-truth/vendor-offsets.json`

Dataset content hash `sha256:138a0fbff44acd5bdfd72dcd263f02c9ac3e616a787bc90410c88cdfd684cb6b`.
