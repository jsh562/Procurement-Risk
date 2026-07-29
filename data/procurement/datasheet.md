# Dataset Datasheet — Synthetic Procurement History

## 1. Motivation

This dataset exists so the procurement-risk demonstration has a delivery history to reason over without using any real procurement record. It is **wholly synthetic**: no real firm, vendor, project or purchase order is represented.

It was created to support forecasting and identity-resolution work downstream, and to make those claims falsifiable — the parameters it was generated from are published in a separate ground-truth record, so a later claim to have recovered them can be checked rather than believed.

## 2. Composition

| Figure | Intended, and its bounding criterion | Realized |
|---|---|---|
| Line count | 190–210 (FR-003) | **199** |
| Delivered share | [max(80%, 160/N), 90%] (FR-010) | **0.879** |
| Uncensored delivery events | ≥ max(80% × N, 160) (FR-010) | **175** |
| Corpus-overlap share | ≥ 60% (FR-032, SC-025) | **0.698** |
| Catalog-overlap share | ≥ 60% (FR-034, SC-026) | **0.698** |
| Spread ratio, category-adjusted | 0.12–0.49 inclusive (FR-008, FR-036, SC-007) | **0.3064** |
| Spread ratio, unadjusted | recorded, not bounded (FR-036) | **0.2674** |
| Aggregate median duration | 61 ± 5 days (SC-023) | **58.0** |
| Aggregate P80 duration | 94 ± 8 days (SC-023) | **90.4** |
| Late-delivery share | 25–35% of delivered lines, delivered-only denominator (FR-011, DV-013) | **0.263** |
| Already-overdue censored lines | recorded separately, excluded from both sides of the late share (SC-024) | **8** |
| Censored share | ≥ 10% (FR-010, SC-016) | **0.121** |
| Delivered-only median duration | recorded beside the population figure so the censoring bias is visible, not bounded (FR-007, SC-023) | **53.0** |
| Delivered-only P80 duration | same — untoleranced, disclosed (FR-007, SC-023) | **84.0** |
| Rework lines | 30% of N, declared (FR-006, DV-009) | **60** |

### Declared allocation and its realized dispersion

**Per-vendor line counts** (declared, not drawn — FR-004's 0.22–0.67 shrinkage span is a consequence of these numbers):

| Vendor | `VND-001` | `VND-002` | `VND-003` | `VND-004` | `VND-005` | `VND-006` | `VND-007` | `VND-008` | `VND-009` | `VND-010` | `VND-011` | `VND-012` |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| Lines | 35 | 28 | 24 | 21 | 18 | 16 | 14 | 12 | 10 | 9 | 7 | 5 |

Realized dispersion: min 5, max 35, mean 16.6, standard deviation 8.66. Shrinkage at the endpoints: 0.22 to 0.67.

**Per-project line counts** (realized):

| Project | `PRJ-001` | `PRJ-002` | `PRJ-003` | `PRJ-004` | `PRJ-005` |
|---|---|---|---|---|---|
| Lines | 40 | 40 | 40 | 40 | 39 |

**Rework allocation**, declared and realized as an equality (DV-009): [42, 13, 5] lines at one, two and three loops. The fixture's *observable* histogram is [39, 11, 4], because censoring truncates some chains before their later loops emit — expected, and recorded here so a reader counting loops in the artifact is not surprised.

Each line carries six descriptive fields, all present and non-blank. 69.8% of lines draw manufacturer and part number from E002's published catalog with the manufacturer's category list containing the line's own category; the complement draws a **category-mismatched** catalog entry, so the overlap share is a measurement that can fall below its floor rather than an artefact of construction.

## 3. Generation Process

- **Generator identity and revision**: `model.procurement.generate` revision 1
- **Root seed**: `20260416`
- **Seed derivation scheme**: `SeedSequence(entropy=root_seed, spawn_key=(int.from_bytes(sha256("<project_id>|<po_number>|<line_number>".encode("utf-8")).digest()[:8], "big"),))`
- **Generation date**: 2026-04-16 (a committed constant, never the run date)
- **Layer label**: `SYNTHETIC`
- **Content hash of every generation input** — all 3, each with the convention it was hashed under:
  - `data/corpus/synthetic/equipment-category-map.json` — `sha256:9308c2060c8be392d98704a9cea9c9b557e2d5c5a0cbd742f93aad809f7bde6f` (`raw_bytes`)
  - `data/corpus/synthetic/manufacturer-catalog.json` — `sha256:df7ee7affc9e355baeac5b1ae2180889bed76956bfed176bae620021caa4b6a9` (`raw_bytes`)
  - `data/roster/project-vendor-roster.json` — `sha256:87a9134ad586b52136659d1426a64dc0b4287d05e9d9476fba1faa9e9a73c89b` (`canonical_content`)

### Per-transition duration assumptions

- **Family**: lognormal per transition, drawn on the log scale.
- **Parameterization** (the generator's own, not a re-expressed one): per-leg location `ln(share × T_pre) − σ₀²/2 + b_v + c_k`, scale `σ₀ = 0.77`.
- **Pre-rework aggregate mean** `T_pre = 60.0`, calibrated by simulation so the rework-inclusive median and P80 land on 61 and 94.
- **Time unit**: whole days.
- **Rounding rule**: `round()` to the nearest whole day.
- **Minimum-duration floor**: 1 day. Load-bearing rather than cosmetic — a zero-day leg would make `occurred_at` non-increasing. It biases short legs upward.
- **Forward apportionment**: (0.12, 0.2, 0.08, 0.46, 0.14), summing to 1.0.
- **Rework loop**: (0.16, 0.12, 0.12) — three transitions per loop, not two; the third returns to `under_review` at the forward share it always carries.
- **Spread components**: σ_w = 0.51, τ = 0.1224, σ_c = 0.219, σ_r = 0.4606.

**Calendar**: order dates fall in [2025-06-16, 2026-02-16]; the as-of date is 2026-04-01. All three are committed constants — none is read from a clock, because a run-date default would move the content hash the day after generation while the recorded seed still looked honoured.

## 4. Preprocessing and Labeling

Two **distinctly named** duration quantities, which must never be written where the other is meant (FR-035):

- `category_expected_duration_days` = `exp(μ_base + c_k + σ_w²/2)` — a property of the **category**.
- `line_expected_total_duration_days` = the same with the vendor offset `b_v` added — a property of the **line**. FR-011's need-by derivation uses this one.

| Tier | Log offset | Expected duration (days) | Categories |
|---|---|---|---|
| **T1** | +0.20 | 84.9 | `COOLING_TOWER`, `GENERATOR_ASSEMBLY`, `HEATING_BOILER`, `LIQUID_FILLED_TRANSFORMER`, `MEDIUM_VOLTAGE_SWITCHGEAR`, `PRIMARY_UNIT_SUBSTATION`, `SECONDARY_UNIT_SUBSTATION`, `WATER_CHILLER` |
| **T2** | +0.00 | 69.5 | `AUTOMATIC_TRANSFER_SWITCH`, `COMPUTER_ROOM_AIR_CONDITIONER`, `ENERGY_RECOVERY_UNIT`, `LOW_VOLTAGE_SWITCHGEAR`, `PAD_MOUNTED_TRANSFORMER`, `STATIC_UNINTERRUPTIBLE_POWER_SUPPLY`, `SWITCHBOARD`, `VARIABLE_FREQUENCY_DRIVE` |
| **T3** | -0.40 | 46.6 | `CIRCUIT_PROTECTIVE_DEVICE`, `HYDRONIC_PUMP`, `LOW_VOLTAGE_TRANSFORMER`, `MEDIUM_VOLTAGE_CABLE` |

Tier offsets are mean-zero at the declared line weights, so a category term cannot shift the aggregate target.

| Tier \ Pressure | `TIGHT` | `MODERATE` | `RELAXED` |
|---|---|---|---|
| **T1** | 5 | 4 | 3 |
| **T2** | 4 | 3 | 2 |
| **T3** | 3 | 2 | 1 |

Slack is **multiplicative** on the line's expected duration: `f ~ Normal(0.13, 0.1)` truncated at 0. Criticality is **derived** and slack is **drawn**, in that direction — there is no cycle.

**Tercile cut points** over the realized dataset: 0.0713, 0.1650. Computed over the dataset as a whole rather than within each category, so the tier dimension of the table stays informative.

## 5. Uses

**Supports**: lead-time forecasting, right-censoring handling, partial pooling across vendors, and cross-document identity resolution against E002's synthetic corpus.

**Does not evidence**: any claim about real vendors, real lead times, or real procurement risk. Nothing here is measurement.

**No train/evaluation split is emitted**, and no split label appears anywhere in the artifact set. **The split is constructed by E007 and frozen and hashed by E014**, per `specs/project-plan.md`: this epic performs neither, and emits the full dataset so that whichever epic splits it does so from the whole. The 0.25 held-out fraction that appears in FR-033's reasoning is an **assumed cross-epic fraction**, used only to bound the post-split event count, and it is not a value this dataset observes or commits anyone to. E007 has since constructed the split at that same 0.25, so the assumption held.

## 6. Distribution

The fixture is **not a corpus document** and carries **no corpus manifest entry** — it is a dataset, and listing it in the corpus manifest would make it discoverable as a document to extract from.

**Licence basis**: `SYNTHETIC-GENERATED`. Wholly synthetic procurement history generated by this project from a committed seed. No real procurement record, vendor or firm is represented. Third-party rights: `NONE`.

## 7. Maintenance

**Regeneration**: `procurement-generate` rewrites the fixture, its sidecar digest and the ground-truth record from the committed seed. `procurement-validate` regenerates and compares the digest.

**A roster or category-map edit invalidates the recorded digest** and requires regeneration rather than patching. Editing the fixture by hand leaves it disagreeing with its own sidecar, which `procurement-validate` refuses.

## Limitations

**9 active records.** `L-5` is **withdrawn** — E002 published the corpus fields that record named as its own reversal trigger. The remaining records are **not renumbered**, so the identities other artifacts cite still mean what they meant.

### L-1 — Insufficient for validating vendor-level tail behaviour

- **Scope decision**: 199 lines across 12 vendors gives the smallest vendor 5 observations, which cannot support a claim about that vendor's tail.
- **Supporting evidence**: The declared per-vendor vector runs 35 down to 5; shrinkage at the small end is 0.22, so the pooled estimate dominates.
- **Reversal trigger**: A dataset with at least ~50 lines for the smallest vendor would make per-vendor tail estimates identifiable without heavy pooling.
- **Production-scale alternative**: Production scale: draw the vendor mix from realized purchase history rather than declaring it, and accept whatever tail support that gives.

### L-2 — Reproducibility claim is scoped to a pinned environment

- **Scope decision**: FR-021's digest is claimed only under the recorded `library_pin`; another NumPy version may produce a different stream and therefore a different digest.
- **Supporting evidence**: `procurement-validate` reports a scope limit rather than a pass when the observed version differs from the recorded pin.
- **Reversal trigger**: A pure-Python draw, or a stream specified independently of any library version, would remove the dependency entirely.
- **Production-scale alternative**: Production scale: pin the environment in a lockfile and regenerate on upgrade, treating the digest change as an expected consequence rather than a defect.

### L-3 — Rework rate is declared, not cited

- **Scope decision**: 30% of lines carry rework because the design declares it; no published source states that figure for this equipment class.
- **Supporting evidence**: `allocate.rework_loop_allocation` fixes the allocation before generation, and DV-009 asserts the realized histogram equals it exactly.
- **Reversal trigger**: A published rework rate for engineered equipment procurement, or an internal one measured from real submittal logs, would replace the declaration.
- **Production-scale alternative**: Production scale: measure the rate from the organisation's own submittal history.

### L-4 — FR-008's spread-ratio target and band are derived, not cited

- **Scope decision**: The 0.24 target and the 0.12–0.49 band come from a shrinkage identity applied to FR-007's published median and P80, not from a source that states them.
- **Supporting evidence**: σ_w = 0.51 back-solved from 94/61; τ = 0.1224 as 0.24 × σ_w. **The derivation carries no category term**, while the ratio actually asserted against the band is the category-adjusted τ/σ_r ≈ 0.2657 — so the published target and the measured quantity are not the same number (analysis finding A-020, carried open).
- **Reversal trigger**: A published between-vendor variance component for procurement lead times would replace the derivation and close the gap between the two ratios.
- **Production-scale alternative**: Production scale: fit the variance decomposition on real delivery history and use the fitted ratio rather than a target.

### L-6 — The duration model is a stand-in for real lead-time behaviour

- **Scope decision**: Per-transition lognormal draws with declared apportionment shares are a modelling convenience, not an observed process.
- **Supporting evidence**: Family, σ₀ = 0.77, the apportionment (0.12, 0.2, 0.08, 0.46, 0.14), the whole-day rounding and the 1-day floor are all disclosed in § Generation Process.
- **Reversal trigger**: Transition-level timestamps from a real procurement system would replace the model with measurement.
- **Production-scale alternative**: Production scale: fit per-transition distributions from the organisation's own lifecycle event log.

### L-7 — No row-level generation provenance

- **Scope decision**: A loaded row carries `roster_hash` but not the dataset content hash or the generator revision, so it is traceable to the roster it was generated against, not to the run that produced it.
- **Supporting evidence**: `purchase_order_line` has exactly one provenance column; the dataset content hash lives in the sidecar and the ground-truth record, neither of which is loaded.
- **Reversal trigger**: A `dataset_content_hash` column on every row would make each row traceable to its run, at the cost of one repeated constant per row.
- **Production-scale alternative**: Production scale: carry a load-batch identifier and join provenance through it, rather than repeating the digest per row.

### L-8 — The pre-split event floor was derived against the wrong side of the split, and does not deliver the calibration sample the product document assumes

- **Scope decision**: FR-010 sets a floor of 160 uncensored events pre-split, derived as 120 / (1 - 0.25) = 160 from the product document's ~120. That computes the total whose *training* remainder is 120, but the product document spends its 120 on calibration precision, which is measured on the *held-out* side. The floor implied is 120 / 0.25 = 480. The 160 floor is retained because it is what this generator enforces and what this dataset satisfies; the claim attached to it is withdrawn.
- **Supporting evidence**: E007 constructed the split at the assumed 0.25 and realized roughly 44 held-out uncensored events, against the ~120 the product document derives its coverage band from. 0.25 x ~175 delivered lines is ~44, so this is the arithmetic working as written rather than a generator shortfall.
- **Reversal trigger**: Raising the pre-split floor to ~480 uncensored events, which at this censored share requires roughly a threefold larger line count than FR-003's 190-210 band permits. The band and the floor cannot both stand once the target is read on the correct side.
- **Production-scale alternative**: Production scale: derive any pre-split floor from the side of the split the downstream claim is measured on, and state which side in the requirement itself. The fraction here was correct and applied to the wrong quantity, so no fraction-mismatch check would have caught it.

### L-9 — Corpus overlap is at vocabulary level, not instance level

- **Scope decision**: Overlap is on shared vocabulary — category token, description composition, quantity domain, vendor name, and now manufacturer and part number — not on a document reference; the material-item tag will rarely coincide with a real corpus item.
- **Supporting evidence**: The realized corpus-overlap share is measured against a ≥60% floor and recorded; the join that holds exactly is category + vendor + the material-item stem with the parenthetical tag normalised out.
- **Reversal trigger**: An instance-level key published by E002 — a stable item identifier appearing in both the corpus and this dataset — would make the join exact.
- **Production-scale alternative**: Production scale: join on the organisation's own material master, where the item identifier is shared by construction.

### L-10 — The late-delivery band sits below the figure in its own source sentence

- **Scope decision**: FR-011 targets 25–35% of delivered lines missing their need-by date, while the published sentence FR-007's 61/94 pair comes from states 38%.
- **Supporting evidence**: The departure is recorded rather than reconciled; the realized late share is measured and published in § Composition.
- **Reversal trigger**: Reconciling the two would mean either adopting 38% as the target or explaining why this dataset's population differs from the published one.
- **Production-scale alternative**: Production scale: measure the late share from realized deliveries rather than targeting it.
