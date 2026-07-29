# Implementation Plan: Cross-Document Identity Resolution

**Branch**: `00009-cross-document-identity-resolution` | **Date**: 2026-07-29 | **Spec**: [spec.md](spec.md)

## Summary

**Goal**: Resolve submittal and purchase-order records that name one material three different ways into a single identity per project, withholding every pair the evidence does not settle.
**Approach**: A deterministic offline console job — normalize through a versioned alias table, block on a union of keys, score in `model.compute`, decide on two frozen thresholds, cluster under a clique constraint, and publish the run's own quality evidence with intervals.
**Key Constraint**: The specification leg of the three-way join is not deliverable — E006 extracts zero values from the 26 real UFGS documents — so the specification reaches a cluster only through the `specification_section` a vendor printed on a submittal transmittal, and the shortfall is published rather than papered over.

## Technical Context

**Language/Version**: Python 3.12
**Primary Dependencies**: psycopg 3, SQLAlchemy 2, Alembic, stdlib `difflib`/`unicodedata` for string comparison. **No new runtime dependency** — see AD-006
**Storage**: PostgreSQL 16 (no pgvector use; blocking is deterministic by decision — spec Scope > Excluded)
**Testing**: pytest, Hypothesis (property-based over the `model.compute` additions), coverage.py, import-linter
**Target Platform**: Offline console entry point under `/src/model`; Linux and Windows development, Linux CI
**Project Type**: single
**Project Mode**: brownfield
**Performance Goals**: None at request time — resolution is an offline batch job over one project's records. Reduction ratio is a *published figure*, not a latency target (FR-011)
**Constraints**: Append-only runs with an explicit active-run pointer; thresholds committed as constants and bound to the labeled-set hash; all-or-nothing write on guard failure; migration block `0500`–`0599`; no cross-project merge
**Scale/Scope**: 5 projects; extracted line items from 25 submittal transmittals plus their purchase-order lines; a frozen labeled set of ~40 pairs across two declared strata

## Instructions Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

**Audited against**: `project-instructions.md` **v1.2.8** (current head) · **Audit date**: 2026-07-29 · First audit for this feature.

| Principle / Section | Gate | Status |
|---|---|---|
| I. Traceable or It Does Not Ship | Every member resolves to its source record and document location; every merge records the alias rule and alias-table version that produced it; every published figure resolves to a run manifest | PASS — FR-033, FR-001, FR-004, FR-034; `identity/writer.py`, `identity/runs.py` |
| II. Uncertainty Is the Product | No bare point estimate is publishable: an estimated proportion carries a Wilson interval, a census carries its exact denominator and an explicit no-interval declaration, and a figure undefined at the realized size is published as undefined with its denominator | **DEVIATION (disclosed).** FR-025, FR-026, FR-028 and the Wilson half of FR-038 pass. But the principle is written unconditionally — *"every reported metric MUST be published together with its interval"* — and coverage, reduction ratio and the withheld share are reported metrics published **without** one. AD-008's reasoning is sound and is not an exemption claim, but it *narrows a Core Principle*, which only an amendment to `project-instructions.md` can do. Recorded as **P-11**, precedent v1.2.1 |
| III. Precision Over Recall Where a Mistake Is Silent | This principle names cross-document identity merges explicitly, and this is that feature. Two thresholds with a withhold band; **both cutoffs withhold** so no pair is auto-rejected invisibly; clique constraint forbids transitive joining; a run failing a guard writes nothing | PASS — FR-015, FR-042, FR-018, FR-035; SC-035, SC-005, SC-015 |
| IV. Agent Output Style | Tables and tagged lists throughout; prose confined to Summary | PASS |
| V. The Model Extracts, Code Computes | **No language model is invoked by this feature at all.** Scoring, thresholding, interval arithmetic and clustering are deterministic code under `model.compute` and `model.identity`; a new import contract forbids `model.identity` reaching `model.llm` or `gateway` | PASS — AD-001, AD-009; see Testing Strategy > Architecture |
| VI. Evaluate Before You Tune | The labeled set is frozen and hashed *before* threshold calibration, the hash is verified on every reporting run, and a run whose thresholds diverge from those calibrated against the verified hash **refuses rather than publishes** | PASS — FR-016, FR-017, FR-044; SC-040. The mechanical hole this principle exists to close is that E009 both authors and consumes the set — see Complexity Tracking |
| VII. Publish the Miss | The specification-leg shortfall, the precision-target reading, the recall-target reading, and the E003 table extension are each recorded as scope decision / supporting evidence / reversal trigger / production-scale alternative. Targets are not adjusted to match results | PASS — spec Risks (four-part limitations); FR-032, SC-026 |
| VIII. Honest Opponents | Merge precision and recall are published against a deterministic **exact-match baseline** — normalized manufacturer plus part number, with no alias expansion and no fuzzy scoring — labeled per the principle | **PASS — corrected during the compliance gate.** This row previously read `N/A` on the claim that "`specs/project-plan.md` scopes identity-resolution baseline comparison to E014." **That citation is false.** `project-plan.md:477` gives E014 a harness *covering* identity resolution, but the only baselines it registers are forecast (`:488`, naive and marginal) and retrieval ablation arms (`:487`); no clause assigns an identity-resolution baseline anywhere. The registered PRD wins and points the other way — `prd.md:219` gates P1 release on every result being published "with its interval **and its baseline**", and `:156–157` register merge precision and recall as P1 metrics of CAP-004, this feature's own capability. See AD-012 |
| Technology Stack | PostgreSQL 16 as the single datastore of record; no second store; no new framework; the resolution job is a console entry point under the modeling entry's own environment per ADR-0011 | PASS — AD-006, AD-010 |
| Testing & Quality Policy | The pair scorer, the band decision, and the interval estimators are **scoring functions** — their output is a number that is stored or published — so they take **both** mandates: strict test-first (red-green-refactor) **and** property-based tests. Placement under `model/compute/` is what makes the classification mechanical | PASS — AD-001; see Testing Strategy |
| Source Code Layout | All new code under `/src/model`; tests alongside within the entry; no fifth entry; no root-level scripts | PASS |
| Development Workflow | Branch matches `#####-feature-name`; Conventional Commits; migration block `0500`–`0599` claimed at epic start against the *delivered* revision chain, not against the plan; scratch confined to the checkout's `.tmp/` | PASS — see Implementation Signals in spec, and HINT-002 |
| Data Provenance | This feature ingests no new corpus documents and fabricates no provenance. The alias table's initial contents derive from E002's committed manufacturer catalogue and the run manifest records that catalogue's digest | **PASS — one obligation added during the compliance gate.** FR-040/SC-036 cover reproducibility, and the audit correctly found that is a different clause from *"Every synthetic dataset MUST ship a datasheet disclosing its generative assumptions."* The committed alias table is derived from E002's synthetic catalogue and is therefore a synthetic dataset. `data/identity/datasheet.md` is now a required deliverable in Project Structure, following the three existing datasheets |
| Governance | Migration block `0500`–`0599` and decision-record number `0022` claimed at epic start. **Eleven amendment needs recorded and not performed** — see Propagation Obligations. No ADR is authored on this branch: E006's QC established (its A-26) that authoring an ADR or a `specs/sad.md` catalog row on a feature branch is itself the Governance violation, so `0022` stays claimed and unused | PASS — with one disclosed boundary crossing, FR-045, justified in Complexity Tracking rather than waved through |

**Re-check after design**: PASS **with two disclosed deviations** — Principle II (P-11) and the FR-045 boundary crossing (Complexity Tracking). Eleven amendment needs are recorded as obligations, **none performed**. The first pass of this gate self-reported PASS on all fourteen rows; a delegated compliance audit returned FAIL and corrected two of them — Principle VIII rested on a citation that does not exist in the document it named, and Principle II claimed a strengthening where the honest reading is a narrowing. Both are repaired above. Recorded rather than quietly fixed, because a gate that grades its own homework is the failure mode E006's QC documented at its A-26.

**Audit notes**

- **Principle II is not weakened by FR-038's census/estimate split, and the distinction is worth stating because it reads like an exemption.** Coverage, reduction ratio and the withheld share are computed over the run's *own* candidate set — every element is observed, nothing is sampled — so there is no sampling uncertainty for an interval to express, and attaching one would assert a population the figure does not generalize to. The principle forbids a point estimate that hides uncertainty; it does not require manufacturing uncertainty that does not exist. The obligation FR-025 keeps in force is the one that matters: a census figure must still carry an *explicit* no-interval declaration with its denominator, so a reader can tell a census from an estimate that quietly dropped its interval.
- **Principle VIII is declared N/A rather than passed.** A feature that publishes precision and recall and reports no baseline could plausibly be read as violating it. It is not: the registered project plan assigns baseline comparison to E014's frozen harness, and standing up a second, unfrozen comparison here would compete with the harness rather than satisfy the principle. Recorded so a later reader finds the reasoning instead of an unexplained blank.
- **Principle VI's exposure is structural, not mechanical.** E009 freezes the labeled set *and* calibrates against it, which is the arrangement the principle exists to prevent. FR-044 closes the mechanical hole — thresholds are bound to the verified hash and a divergent run refuses. What it cannot close is that the same epic chose the sample. That is why the amendment need moving `LabeledPair` from E014 is recorded (P-5) rather than treated as settled, and why the stratum frames are published (SC-032, SC-037) rather than merely honored.

## Architecture

```mermaid
C4Container
  Person(coordinator, "Coordinator", "Reads resolved identities")

  System_Ext(e006, "Extraction Output", "E006 line items")
  System_Ext(e002, "Manufacturer Catalogue", "E002 committed")

  Container_Boundary(job, "Identity Resolution Job") {
    Container(normalize, "Normalizer", "model.identity", "Alias + units")
    Container(block, "Blocking", "model.identity", "Candidate pairs")
    Container(score, "Scorer", "model.compute", "Pure, test-first")
    Container(cluster, "Clusterer", "model.identity", "Clique constrained")
    Container(evidence, "Evidence Reporter", "model.compute", "Figures and intervals")
  }

  ContainerDb(db, "PostgreSQL 16", "Single store of record")

  System_Ext(e016, "Review Workspace", "E016, consumes queue")
  System_Ext(e014, "Evaluation Harness", "E014, consumes labeled set")

  Rel(e006, normalize, "line items")
  Rel(e002, normalize, "seeds alias table")
  Rel(normalize, block, "canonical records")
  Rel(block, score, "candidate pairs")
  Rel(score, cluster, "merged pairs")
  Rel(score, evidence, "decisions")
  Rel(cluster, db, "writes entities")
  Rel(evidence, db, "writes manifest")
  Rel(db, e016, "withheld pairs")
  Rel(db, e014, "frozen labeled set")
  Rel(db, coordinator, "resolved identities")
```

## Architecture Decisions

Feature-local tradeoffs only. Project-wide architectural decisions belong in standalone ADRs under `specs/adrs/` — referenced by ID, never copied. Number `0022` is claimed and deliberately unused; see the Governance row above.

| ID | Decision | Options Considered | Chosen | Rationale |
|----|----------|--------------------|--------|-----------|
| AD-001 | Where does the pair scorer live? | `model.identity.score` beside the rest of the job / `model.compute.pair_score` | `model.compute.pair_score` | The Testing & Quality Policy binds "scoring functions" to both the test-first and property-based mandates, and E006 fixed the classification rule by package placement: a module whose output is *a number that is stored or published* belongs under `model/compute/`. The pair score is written to a row and gates every merge. Placing it anywhere else would make the mandate a matter of argument rather than of path |
| AD-002 | How are merged pairs clustered? | Transitive closure (connected components) / correlation clustering / clique-constrained agglomeration | Clique-constrained agglomeration | FR-018 forbids a cluster inducing an unscored pair, which rules out closure outright — closure's whole behavior is joining records never compared. Correlation clustering is the principled optimum and is NP-hard, so every implementation is an approximation whose error would need its own published figure. The clique constraint is exact, cheap at this scale, and is itself the acceptance criterion (SC-005) |
| AD-003 | How does E009 get project and run columns onto E003's `resolved_entity`? | Parallel E009-owned entity tables / ALTER E003's tables in the `0500` block / block on the E003 amendment | ALTER in the `0500` block | Recorded in the spec as a deliberate governance exception with its cost, not as an oversight. A parallel table would split the join surface E012, E014 and E016 all read, and duplicating an entity to avoid touching it is the worse coupling. See Complexity Tracking and P-6 |
| AD-004 | Where does the alias table live? | Committed data file only / database table only / committed file loaded into a versioned table | Committed file loaded into a versioned table | The file is the reviewable source of truth and the thing a diff shows; the table is what a run joins against and what carries the version a merge cites (FR-004). One without the other loses either auditability or queryability |
| AD-005 | How are the two thresholds carried? | Runtime configuration / environment variables / committed constants module | Committed constants module, bound to the labeled-set hash in the manifest | Principle VI plus FR-044. A threshold that can be supplied at runtime cannot be shown not to have been tuned after seeing a result. Committing them makes a change a reviewable diff, and binding them to the verified hash makes a divergent run *refuse* rather than merely be forbidden |
| AD-006 | What computes string similarity? | `rapidfuzz` / `jellyfish` (Jaro-Winkler) / stdlib `difflib` + explicit part-number segmentation | stdlib `difflib` + explicit segmentation | No new runtime dependency, and the decisive comparison here is not fuzzy distance at all. The failure this feature must avoid is merging two catalog numbers whose *suffix* encodes voltage, enclosure rating or handing (SC-003) — a case where Jaro-Winkler scores near 1.0 and is actively wrong. Segment-aware comparison with an explicit suffix rule is the correct tool; a general edit-distance library would be a dependency bought to solve the easier half |
| AD-007 | How is the active run selected? | Highest run id / a `latest` boolean / most recent timestamp / partial unique index over an `is_active` flag | Partial unique index | Reuses E006's delivered pattern for the same problem. FR-039 forbids selection by recency; an index is what makes "exactly one active run" a database-enforced fact rather than a query convention that a second reader gets wrong |
| AD-008 | How is "every figure carries an interval" reconciled with census figures? | Attach a Wilson interval to everything / attach nothing to census figures / classify each figure and require an explicit declaration either way | Classify, and require the declaration either way | Attaching an interval to a census asserts a sampling frame that does not exist. Attaching nothing makes a census indistinguishable from an estimate whose interval was dropped. FR-025 and FR-038 together make the *classification* the published artifact |
| AD-009 | Is a new import contract needed? | Rely on the existing computation-boundary contract / add a contract forbidding `model.identity` → `model.llm`/`gateway` | Add the contract | The existing contracts run the other direction (`model.llm` may not reach `model.compute`). Nothing today would fail the build if a future edit reached the provider from the resolution job. The claim "this feature invokes no language model" is exactly the kind fixed in advance and checked by nobody — the shape E006's baseline contract was written to prevent |
| AD-010 | How is the job invoked? | Container job under a Compose profile / console entry point in the modeling entry | Console entry point `resolve-identity` | ADR-0011: a job owned by the modeling boundary cannot share the serving build context without defeating the contracts that keep the two apart. Follows the established `<domain>-<verb>` convention — one verb per job, no subcommand dispatcher |
| AD-012 | What is the honest opponent for merge precision and recall? | No baseline (deferred to E014) / a random pairing / **exact match on normalized manufacturer + part number** / a second scorer with different weights | Exact match on normalized manufacturer + part number, **labeled strong** | Principle VIII requires a baseline strong enough that beating it means something, and `prd.md:219` gates P1 release on publishing one. Exact match is the resolver *minus the two things whose complexity is on trial* — alias expansion and fuzzy scoring — so beating it is precisely the evidence that those two earned their place. It is also plausibly winning **on precision**, which is the metric this feature optimizes: exact match almost never merges wrongly. Random pairing would be a rhetorical opponent the principle names as worthless. Following E006's `ingest/baseline.py` precedent, an import contract keeps it independent: `model.identity.baseline` may not reach `alias.py`, `normalize.py` or `compute.pair_score` — a baseline that reads the alias table is not a baseline, it is the system under test with a smaller threshold |
| AD-011 | What is scored when an attribute is missing on one side? | Treat as disagreement / treat as agreement / a third "absent" state contributing neither | A third "absent" state | FR-008 requires a record with no part number to still enter comparison. Scoring absence as disagreement would make every part-number-less record unmergeable, converting a blocking guarantee into a scoring veto; scoring it as agreement would invent evidence. The component records `absent` and contributes zero, and that contribution is visible per FR-014 |

## Data Model Summary

15 new tables and 2 views, plus an extension of two E003 tables. (`data-model.md` says "thirteen" in its summary prose while enumerating fifteen; the enumeration is correct.) The design's method is to make each criterion **unrepresentable to violate** rather than checked after the fact — the notes column records which constraint carries which requirement.

| Entity | Key Fields | Relationships | Notes |
|--------|------------|---------------|-------|
| `manufacturer_alias_table` | `version`, `content_digest`, `e002_catalogue_digest`, `base_unit_system` | parent of the two alias tables | FR-004, FR-040 — the digest is what makes the seed reproducible |
| `canonical_manufacturer` | `version`, `canonical_key`, `preferred_label` | ← `manufacturer_alias` | Preferred label is a **column, not a row**, so a second preferred label is unrepresentable (SKOS integrity condition) |
| `manufacturer_alias` | `version`, `normalized_alias`, `alias_class`, `raw_alias` | → `canonical_manufacturer` | `UNIQUE (version, normalized_alias)` **is** FR-002 — alias→canonical is a function, and a duplicate fails table load rather than tie-breaking at runtime. Three disjoint classes: preferred / alternate / hidden |
| `unit_alias` | `version`, `symbol`, `unit_class`, `base_unit`, `factor` | → alias table | Dimensional units carry a base and a factor; **arbitrary units carry neither**, so FR-005's "never converted" has nothing to multiply by |
| `labeled_pair_set` | `stratum`, `set_hash`, `sampling_frame`, `size`, `true_count` | ← `labeled_pair` | One row **per stratum** with its own hash and frame — FR-037's separation is structural, not a convention |
| `labeled_pair` | `set_hash`, canonical pair identity, `is_true`, `annotator`, `annotation_provenance` | → `labeled_pair_set` | FR-036 — provenance makes the stratum verifiable from the artifact |
| `threshold_calibration` | `merge_threshold`, `reject_threshold`, both stratum hashes | ← `resolution_run`, `candidate_pair` | One calibration per frozen set, ever. FR-044 falls out: a run whose thresholds differ from a calibrated pair has **no referent** and fails at its first insert |
| `resolution_run` | `project_id`, `alias_version`, `is_active`, input counts | → calibration, alias table | FR-039 — `is_active` under a **per-project partial unique index** (AD-007), so the pointer is a database fact, not a query convention |
| `resolution_run_record` | raw + normalized value, `alias_rule_fired`, `unit_class` | → run, → source record | FR-003's additivity — the raw string sits beside the normalized one in the same row, so no stage can discard it |
| `unmatched_manufacturer_string` | `raw_string`, `occurrence_count` | → run | FR-013 — alias-table gaps as data, not as absent merges |
| `candidate_pair` | canonical unordered identity, `score`, denormalized thresholds, `decision` | → run; ← attribute scores | `(left_kind,left_id) < (right_kind,right_id)` gives FR-009's identity and forbids self-pairs in one constraint. **FR-015/FR-042/SC-035 is a single-row `CHECK`** deriving `decision` from the score and the two cutoffs with strict comparisons, so both cutoffs land in `withhold` by arithmetic |
| `candidate_pair_attribute_score` | `field_key`, `agreement`, `contribution` | → pair, → `field_vocabulary` | FR-014 — real FK to E003's vocabulary, not a free-text field name |
| `resolved_entity_induced_pair` | entity, candidate pair, both member rows | → entity, → pair (FK pins `decision='merge'`) | FR-018 — **transitive closure cannot write these rows**, because an unscored pair has no candidate row to reference |
| `review_queue_item` | run, pair identity, score, band, alias version | → run, → pair | FR-021/FR-043 — one per withheld pair *per run*; E016 adds adjudication state additively (FR-023) |
| `resolution_figure` | `figure`, `stratum`, value, interval or explicit null-with-declaration, denominator | → run | FR-037a/FR-038 — `stratum` domain is `('estimation','run_candidate_set')`, so `hard_negative` and any union value are **unwritable** |
| `resolved_entity` *(E003, extended)* | +`resolution_run_id`, +`project_id` | → run | FR-045/FR-020. `uq_resolved_entity__normalized_identity` re-scoped to the run |
| `resolved_entity_member` *(E003, extended)* | +`resolution_run_id`, +`member_role`, +generated `member_record_id` | → run | FR-045. `uq_rem__extracted_value` / `uq_rem__po_line` → `uq_rem__run_*`, both still relying on `NULLS DISTINCT` |

**Migration chain** (from E006's `0404`): `0500_alias_artifact` → `0501_labeled_set` → `0502_resolution_run` → `0503_run_records` → `0504_candidate_pair` → `0505_resolved_entity_extension` → `0506_induced_pair` → `0507_review_queue` → `0508_resolution_figure` → `0509_resolution_privileges`. `0505` must follow `0503`; `0506` cannot precede `0505`.

**Detail**: [data-model.md](data-model.md) — including ten recorded ambiguities (A-1…A-10) and the disclosed gaps.

## API Surface Summary

N/A — no API surface. The feature is an offline console job writing to PostgreSQL. `specs/00009-cross-document-identity-resolution/spec.md` Implementation Signals declares `NEW-WORKER` and `NEW-ENTITY`/`MIGRATION`/`NEW-CONFIG`, and no `NEW-API`. Consumers — E012, E014, E016 — read the tables; the read surface each needs is that epic's to design, and pre-declaring it here would fix a contract against requirements that do not yet exist.

## Testing Strategy

| Tier | Tool | Scope | Mock Boundary | Install |
|------|------|-------|---------------|---------|
| Unit | pytest | Alias-table load and duplicate-alias rejection, unit classification, blocking key derivation, unordered-pair identity, clique admission, manifest assembly, all-or-nothing abort | Fixtures only; no database | configured |
| Property | Hypothesis | **All four `model.compute` additions**, each taking **both** mandates — strict test-first and property-based. The set is defined by AD-001's placement rule, not enumerated by hand: every module computing a number that is stored or published. **Relation class per module**: `pair_score` — *boundedness and monotonicity* (score stays in range; raising one component's agreement never lowers the total); `decide` — *totality and disjointness*, enumerated exhaustively at and around both cutoffs rather than sampled, because the criterion (SC-016, SC-035) is about exact boundary values and a sampler will miss them; `metrics` — *interval containment and estimator selection* (the published interval contains the point estimate; zero errors selects the rule of three, one or more selects the exact binomial, zero denominator selects undefined), covering **every** published figure including pair completeness, coverage, the withheld share and the stratum argument; `calibrate` — *determinism and frozen-set dependence* (the same frozen set yields the same two constants; a perturbed set yields a detectably different pair) | Pure functions, nothing mocked | configured |
| Integration | pytest + live PostgreSQL | Append-only across two runs, active-run pointer under the partial unique index, run-scoped uniqueness on the extended E003 tables, guard failure leaving no partial write, one review item per withheld pair per run, cross-project isolation | Real database; no gateway involvement — this feature invokes no model | configured |
| Architecture | import-linter | **Two** new contracts, both with indirect detection ON: (a) `model.identity` may not reach `model.llm` or `gateway` (AD-009); (b) `model.identity.baseline` may not reach `identity.alias`, `identity.normalize` or `compute.pair_score` (AD-012) — a baseline that reads the alias table is the system under test, not an opponent. Existing computation-boundary and provider contracts remain green | — | **not configured** — two new contracts to add to `src/model/pyproject.toml` |
| Security | committed-fixture credential scan (E004's) + `ruff` | Committed tree, including the new committed alias table — it is vendor data and must carry no contact or account detail | — | configured |
| Coverage | coverage.py | Combined 80% floor **and a per-package 80% floor on `model.identity`**, asserted separately | — | **not configured** — `verify.yml`'s `--source` and `[tool.coverage.paths]` are enumerations that override rather than merge. `identity` must be appended to both or every line this epic adds is invisible to the gate. This is the same trap E006 documented, and it recurs because the lists are enumerations |

**New dependencies to add**: none. Every tier already has a configured tool, and AD-006 declines the one runtime dependency that was a candidate.

## Error Handling Strategy

| Error Category | Pattern | Response | Retry |
|----------------|---------|----------|-------|
| Alias table invalid (duplicate alias → two canonicals) | fail-fast at load, before any run row is written | Non-zero exit naming the offending alias; nothing written (FR-002, SC-028) | no |
| Normalization collision guard breached | fail-closed, whole run | No resolved entities, no review items, **no manifest** — the run leaves no trace to be mistaken for a result (FR-035, SC-015) | no |
| Labeled-set hash mismatch | fail-closed, whole run | Same as above; the mismatch and both hashes are reported (FR-017) | no |
| Threshold constants diverge from those calibrated against the verified hash | refuse before publishing | Non-zero exit naming both threshold sets; nothing published (FR-044, SC-040) | no |
| Manufacturer string matches no alias | record, continue | Written to the unmatched-alias record; the run completes and the gap is visible as data (FR-013, SC-030) | no |
| Every candidate pair withheld | complete normally | Zero merges, full review queue, precision reported **undefined with its zero denominator** — not a failure and not reported as one (FR-024, SC-013) | no |
| Attempt to admit a second specification section to a cluster | reject the admission, continue | The offending record is named; the cluster keeps its first (FR-041, SC-041) | no |
| Database unavailable / transaction aborts mid-write | fail-fast, single transaction per run | Run rolls back whole; no partial run is observable (FR-035) | no |

## Integration Points

| Spec Reference | System/Service | Technical Approach | Contract |
|----------------|----------------|--------------------|----------|
| E006 extraction output | Delivered ingestion tables | Read `extracted_value` line items from the 25 submittal transmittals — manufacturer, part number, quantity, stored exactly as printed. E006 asserts no identity between spellings (its FR-028), so normalization is wholly E009's | E006 `data-model.md`; the seam is named in spec Assumptions |
| E006 `specification_section` | Delivered extraction field | The only route by which a specification reaches a cluster. Not inferred — a direct reference a vendor printed on a transmittal | FR-041, SC-001, SC-041 |
| E002 manufacturer catalogue | Committed generator artifact | Seeds the alias table's initial contents; its digest is recorded in the run manifest so the seed is reproducible | FR-040, SC-036 |
| E003 `resolved_entity` / `resolved_entity_member` | Delivered schema | **Extended, not replaced** — additive ALTER in the `0500` block adding project and run columns and re-scoping uniqueness. Disclosed exception; see Complexity Tracking | FR-045, AD-003, P-6 |
| E016 review workspace | Downstream, not yet built | E009 emits `ReviewQueueItem` rows shaped so adjudication state can be added **additively**, without altering a field E009 writes | FR-023 |
| E014 evaluation harness | Downstream, not yet built | E009 authors and freezes the labeled set E014 consumes. This inverts the registered ownership; recorded as P-5 | FR-017, spec Risks |

## Risk Mitigation

| Risk (from spec) | Likelihood | Impact | Mitigation | Owner |
|-------------------|------------|--------|------------|-------|
| The published precision criterion may be unreachable as written, and this branch may not correct it | H | H | Implement the point-estimate reading (FR-026) and make the interval a *mandatory* published disclosure with its method and sidedness named, so the reading taken is visible in the output rather than buried in a spec. Record the amendment need (P-1, P-2); do not adjust the target | `model.compute.metrics`, Evidence Reporter |
| E009 alters tables E003 owns, and E006 forbids it | H | H | Additive ALTER only — new nullable-then-backfilled columns and re-scoped unique constraints, no column dropped and no type narrowed, in E009's own `0500` block. Integration test asserts E003's existing constraints still reject what they rejected before. Recorded as P-6 and in Complexity Tracking | Migration `0500` series |
| The registered recall target has the same interval problem as precision, and it is not recorded upstream | M | M | Same treatment: point-estimate reading (SC-018), Wilson interval published beside it, denominator stated. Recorded as P-3 | `model.compute.metrics` |
| E009 takes the frozen labeled set from E014, and that is a scope change | H | M | Close the mechanical hole rather than the structural one, and say which is which: FR-044 binds thresholds to the verified labeled-set hash and a divergent run refuses (SC-040), so tuning-against-the-test-set is detectable. The structural separation stays removed; recorded as P-5 | `identity/labeled.py`, `identity/thresholds.py` |
| This feature depends on E002 and the registered plan does not say so | H | L | Make the dependency explicit in code and in the manifest — the E002 catalogue digest is recorded on every run (FR-040), so the undeclared edge is at least visible in the artifact. Recorded as P-4 | `identity/alias.py`, `identity/runs.py` |
| The MVP slice did not carry the registered gate's evidence — resolved at clarification | M | M | Already resolved in the spec by raising US3 to P1; the plan carries it by placing the evidence reporter and its metrics in the P1 task set rather than deferring them. No further mitigation needed | Task sequencing |

## Requirement Coverage Map

| Req ID | Component(s) | File Path(s) | Notes |
|--------|--------------|--------------|-------|
| FR-001 | Normalizer | `src/model/src/model/identity/alias.py` | Resolution records the firing rule |
| FR-002 | Normalizer | `identity/alias.py` | Duplicate alias fails load; no runtime tie-break |
| FR-003 | Normalizer, Writer | `identity/normalize.py`, `identity/writer.py` | Raw retained beside normalized |
| FR-004 | Normalizer, Run manifest | `identity/alias.py`, `identity/runs.py` | Version on run and on every merge |
| FR-005 | Normalizer | `identity/units.py` | Dimensional canonical form; arbitrary units classified |
| FR-006 | Scorer | `compute/pair_score.py` | Scored attribute, never a filter |
| FR-007 | Guard | `identity/guard.py` | Collision guard over hard-negative stratum |
| FR-008 | Blocking | `identity/block.py` | Union of keys, not conjunction |
| FR-009 | Blocking | `identity/pairs.py` | Unordered-pair identity, counted once |
| FR-010 | Labeled set, Evidence | `identity/labeled.py`, `identity/report.py` | Frame independent of blocking keys |
| FR-011 | Metrics, Evidence | `compute/metrics.py`, `identity/report.py` | PC and RR separate from precision. PC is an *estimated* proportion with a Wilson interval, so its arithmetic is a `compute` module {AD-001} |
| FR-012 | Writer | `identity/writer.py` | Full candidate set persisted |
| FR-013 | Normalizer, Writer | `identity/alias.py`, `identity/writer.py` | Unmatched strings as data |
| FR-014 | Scorer, Writer | `compute/pair_score.py`, `identity/writer.py` | Per-component contributions stored |
| FR-015 | Decision | `compute/decide.py` | Three disjoint bands |
| FR-016 | Thresholds, Calibration | `identity/thresholds.py`, `compute/calibrate.py` | Committed constants, calibrated pre-publication. Calibration derives a stored, hash-bound number, so it is a `compute` module {AD-001} |
| FR-017 | Labeled set | `identity/labeled.py` | Freeze, hash, verify per run |
| FR-018 | Clusterer | `identity/cluster.py` | Clique constraint |
| FR-019 | Metrics, Evidence | `compute/metrics.py`, `identity/report.py` | Estimation stratum only; the population is an argument to the estimator, not a reporting convention |
| FR-020 | Blocking, Clusterer | `identity/block.py`, `identity/cluster.py` | Project scope enforced twice |
| FR-021 | Review queue | `identity/review.py` | One item per withheld pair per run |
| FR-022 | Review queue | `identity/review.py` | Records, score, band, agreements, alias version |
| FR-023 | Review queue, Schema | `identity/review.py`, migration `0500` series | Additive shape for E016 |
| FR-024 | CLI, Evidence | `identity/cli.py`, `identity/report.py` | All-withheld run succeeds |
| FR-025 | Evidence, Metrics | `identity/report.py`, `compute/metrics.py` | Interval or explicit declaration |
| FR-026 | Metrics | `compute/metrics.py` | Point estimate + 95% interval, method and sidedness named |
| FR-027 | Metrics | `compute/metrics.py` | Rule of three at zero errors, exact binomial otherwise, n<30 disclosure |
| FR-028 | Metrics | `compute/metrics.py` | Zero denominator → undefined, never zero |
| FR-029 | Metrics, Evidence | `compute/metrics.py`, `identity/report.py` | Coverage in the same statement as precision |
| FR-030 | Evidence, Metrics | `identity/report.py`, `compute/metrics.py` | Withheld/rejected/unblocked all count as misses |
| FR-031 | Metrics, Evidence | `compute/metrics.py`, `identity/report.py` | Withheld count, share, and yield |
| FR-032 | Evidence | `identity/report.py` | Shortfall with cause; target unmoved |
| FR-033 | Writer, Schema | `identity/writer.py`, migration `0500` series | Member → source record → document location |
| FR-034 | Run manifest | `identity/runs.py` | Alias version, thresholds, hashes, input counts |
| FR-035 | Writer, CLI | `identity/writer.py`, `identity/cli.py` | Single transaction; nothing partial |
| FR-036 | Labeled set | `identity/labeled.py` | Annotator judgment with provenance |
| FR-037 | Labeled set | `identity/labeled.py` | Two strata, separate frames, separate hashes |
| FR-037a | Metrics, Evidence | `compute/metrics.py`, `identity/report.py` | Each figure names its stratum; the stratum is an estimator argument |
| FR-038 | Metrics, Evidence | `compute/metrics.py`, `identity/report.py` | Wilson for estimates; census exact, no interval |
| FR-039 | Run manifest, Schema | `identity/runs.py`, migration `0500` series | Append-only; active-run pointer via partial unique index (AD-007) |
| FR-040 | Normalizer, Run manifest | `identity/alias.py`, `identity/runs.py` | E002 catalogue digest recorded |
| FR-041 | Clusterer | `identity/cluster.py` | At most one specification section; offending record named |
| FR-042 | Decision | `compute/decide.py` | **Both** cutoffs withhold |
| FR-043 | Review queue | `identity/review.py`, `identity/pairs.py` | Run id + stable pair identity |
| FR-044 | Thresholds, Run manifest | `identity/thresholds.py`, `identity/runs.py` | Refuse on divergence from the calibrated hash |
| FR-045 | Schema | migration `0500` series | Extends E003's tables — disclosed exception, AD-003 |

## Project Structure

### Source Code

```text
src/model/
  pyproject.toml                       ~ add `resolve-identity` entry point; add the model.identity import contract; add `identity` to coverage source and paths
  src/model/
    identity/                          + new package — the resolution job
      __init__.py                      +
      cli.py                           + `resolve-identity` console entry, single transaction
      alias.py                         + alias table load, function check, versioning, unmatched recording
      normalize.py                     + additive normalization, part-number segmentation
      units.py                         + dimensional canonicalization; arbitrary-unit classification
      block.py                         + union-key candidate generation, project scoping
      pairs.py                         + stable unordered-pair identity
      guard.py                         + normalization collision guard
      cluster.py                       + clique-constrained agglomeration, cardinality bounds
      review.py                        + review queue items, per run
      labeled.py                       + frozen labeled set, two strata, hashing and verification
      thresholds.py                    + committed threshold constants
      baseline.py                      + exact-match honest opponent {AD-012}, import-isolated
      runs.py                          + run manifest, active-run pointer
      writer.py                        + all-or-nothing persistence
      report.py                        + published figures with their classifications
    compute/
      pair_score.py                    + pure scoring function {AD-001}
      decide.py                        + pure two-threshold band decision {AD-001}
      calibrate.py                     + threshold calibration against the frozen set {AD-001}
      metrics.py                       ~ add rule-of-three, one-sided exact binomial, undefined branch, and the arithmetic for every published figure — pair completeness, coverage, reduction ratio, withheld share {AD-001}
    schema/versions/
      0500_*.py .. 0599_*.py           + migration block claimed at epic start
  tests/
    identity/                          + unit and integration tests for the package above
    compute/                           ~ extend with property tests for the three additions
data/
  identity/manufacturer-aliases.*      + committed alias table, seeded from E002's catalogue {AD-004}
  identity/datasheet.md                + REQUIRED — Data Provenance mandates a datasheet for every synthetic dataset, and the alias table is derived from E002's synthetic catalogue. Follows `data/roster/roster-datasheet.md`, `data/corpus/synthetic/datasheet.md`, `data/procurement/datasheet.md`
.github/workflows/verify.yml           ~ append `identity` to the coverage --source enumeration
```

**Patterns to reuse**: E006's partial unique index for the active-run pointer; E006's per-document transaction boundary, applied here as one transaction per *run* since FR-035 makes the run the atomic unit; the `<domain>-<verb>` console entry convention; `model.compute`'s existing Wilson interval implementation in `metrics.py`.
**Tests to extend**: `src/model/tests/compute/` (property tiers), `src/gateway/tests/test_migrations.py` — its Alembic-head assertion is block-scoped after E006's repair and must gain E009's block the same way rather than being re-pinned to a new head.
**Naming conventions**: revision files `NNNN_snake_case.py`; constraint prefixes as E003 uses them (`uq_`, `fk_`, `ix_`); one job per console entry point, no subcommand dispatcher.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|--------------------------------------|
| **E009 alters tables E003 owns (FR-045), and the alteration is larger than "additive".** `specs/project-plan.md:678` registers "ResolvedEntity \| E003 (schema), E009 (populated)" and E006's FR-065 enforces that E003's tables are not altered by a later epic. The compliance audit judged this should **block**, and the honest statement of the crossing is wider than an earlier draft of this row admitted: `0505` issues **three `DROP CONSTRAINT`s**, re-scopes `uq_resolved_entity__normalized_identity` on the same authority though FR-045 enumerates only the two member constraints (data-model A-2), and `0509` **revokes `UPDATE` and `DELETE`**, reversing E003's `0010` recorded rationale that "a resolved entity is a revisable judgement about identity". No column is dropped and no type narrowed, but "additive" was the wrong word and is withdrawn | The committed tables carry `uq_rem__extracted_value UNIQUE (extracted_value_id)` and `uq_rem__po_line UNIQUE (po_line_id)` with no run column. A record therefore cannot belong to two entities across runs, which makes FR-039's append-only contract and FR-020's per-project isolation both unbuildable against the schema as delivered. This is not a preference — the requirements cannot be satisfied without it | **Parallel E009-owned entity tables**: splits the join surface E012, E014 and E016 all read, and duplicating an entity to avoid touching it produces worse coupling than the extension does. **Blocking on the E003 amendment**: Governance serializes amendments onto the default branch and this epic cannot perform one; waiting would stall E009 behind a queue it does not control. **The extension is taken by an explicit decision the project owner made when the choice was put to them during clarification — "record an E003 schema amendment need, and E009 adds the columns itself."** That is what distinguishes this from a self-granted exception, and it is the reason the audit's recommendation to block is not followed. What the audit is right about is the framing, now corrected above, and the scope: the privilege revocation (P-9) reverses a *recorded E003 decision*, not just a column list, and is the part most likely to surprise a later reader. Disclosed here, in the spec's Risks, and as obligations P-6 and P-9 |
| **E009 both freezes and calibrates against the labeled set (FR-016, FR-017).** `specs/project-plan.md` assigns `LabeledPair` to E014 precisely to separate construction from calibration | The registered plan's own ordering is circular: it gives E014 the entity while making E014 depend on E009. One of the two has to move, and the set has to exist before thresholds can be calibrated at all | **Leaving it with E014**: E009 would have no set to calibrate against and could not satisfy FR-016 within its own scope. The mechanical guarantee the separation existed to provide is reconstructed by FR-044 — thresholds bound to the verified hash, divergent run refuses — so tuning to the test set is *detectable* rather than merely forbidden. The structural separation stays removed and is recorded as P-5 |

## Propagation Obligations

`project-instructions.md` § Governance: *"Amendments to the documents named in this section are serialized. At most one amendment is in flight at a time, it is performed on the default branch, and it lands before the next begins. A feature branch records the need for an amendment and does not perform it."* This branch is `00009-cross-document-identity-resolution`, not the default branch, so `AMEND_MODE = record`. **Nothing below has been written to any registered document.**

| # | Obligation | Owner | Trigger |
|---|---|---|---|
| P-1 | State whether the identity-resolution merge-precision target of **0.95 is a point estimate or the lower bound of its interval**. Read as a bound it is arithmetically unreachable at this sample size — the rule of three gives 3/40 = 0.075 even at the theoretical maximum denominator with zero errors, so the bound is 0.925. Belongs in `specs/prd.md` and `specs/sad.md` | Whoever owns `specs/prd.md` and `specs/sad.md` | Planning adopted the point-estimate reading (FR-026, SC-017) and must record that it diverges from a criterion the registered documents leave ambiguous |
| P-2 | The registered criterion names **only the rule of three**, which is inapplicable the moment one false merge is observed. Name an estimator for the non-zero-error case. FR-027 adopts a one-sided exact binomial (Clopper–Pearson) interval. Belongs in `specs/prd.md` and `specs/sad.md` | Whoever owns `specs/prd.md` and `specs/sad.md` | The plan implements a second estimator that neither registered document names |
| P-3 | State whether the identity-resolution **recall target of 0.80** is a point estimate or an interval lower bound. At ~20 true pairs a 95% Wilson interval around 0.80 spans roughly 0.58–0.92, so the bound reading is unreachable exactly as precision's is. Belongs in `specs/prd.md` and `specs/sad.md` | Whoever owns `specs/prd.md` and `specs/sad.md` | SC-018 takes the point-estimate reading on the same basis as P-1 |
| P-4 | Add the **E002 dependency edge** to E009's dependency contract. `specs/project-plan.md` records E009 as depending on E005 and E006; FR-040 derives the alias table from E002's committed manufacturer catalogue, which makes E002 a real dependency | Whoever owns `specs/project-plan.md` | AD-004 and FR-040 make the dependency structural — the run manifest records the catalogue digest |
| P-5 | Move **`LabeledPair` ownership from E014 to E009**, and correct the E009/E014 dependency contract to match. The plan's current ordering is circular: it assigns E014 the entity while making E014 depend on E009 | Whoever owns `specs/project-plan.md` | FR-016 and FR-017 place construction, freezing and calibration in E009; Complexity Tracking records why |
| P-6 | Admit E009's **extension of `resolved_entity` and `resolved_entity_member`** — project and run columns, three dropped constraints re-created run-scoped, and `uq_resolved_entity__normalized_identity` re-scoped on the same authority though FR-045 enumerates only the two member constraints. Belongs in `specs/project-plan.md` (the "ResolvedEntity \| E003 (schema), E009 (populated)" ownership row) and in `specs/00003-core-data-schema/data-model.md`, which is declared normative over Specify-phase requirements by ADR-0017 | Whoever owns `specs/project-plan.md`; E003's workspace for its data model | FR-045 performs the extension on this branch by explicit decision (AD-003). The obligation records the ownership change the migration has already made real |
| P-7 | Record in `specs/sad.md` that the **identity-resolution job is a fourth model-owned console entry point** (`resolve-identity`) under ADR-0011's pattern, and that a new import contract forbids `model.identity` reaching `model.llm` or `gateway`. Both are reusable project-level context: the entry-point inventory and the set of build-gating architecture contracts are cross-cutting, not feature-local | Whoever owns `specs/sad.md` | §5.6.2 — AD-009 and AD-010 add a system boundary and a build-gating contract, which are exactly the categories the managed section promotes |
| P-8 | Record in `specs/sad.md` the **census-versus-estimate classification** for published figures: a proportion estimated from a sample carries a Wilson interval; a census over a run's own population carries an exact denominator and an explicit no-interval declaration. Secondary to P-11 — `sad.md` cannot narrow a Core Principle, so this records the engineering convention only, after the principle itself is amended | Whoever owns `specs/sad.md` | AD-008 resolves an apparent Principle II conflict in a way later epics will re-derive independently unless it is recorded once |
| P-9 | Admit that E009 **revokes `UPDATE` and `DELETE` on `resolved_entity` and `resolved_entity_member`** (migration `0509`). E003's `0010` grants all four verbs with an explicit stated rationale — *"a resolved entity is a revisable judgement about identity"* — and FR-039's append-only contract requires the opposite. Belongs in `specs/00003-core-data-schema/data-model.md`, which ADR-0017 declares normative | E003's workspace for its data model | Design surfaced it. This is a **second, distinct** amendment need against E003 — P-6 covers the column lists; this one reverses a documented E003 *decision* and its rationale, which is the more serious of the two |
| P-10 | Correct E009's **key-entity list** in `specs/project-plan.md`. It records ResolvedEntity, CandidatePair and ReviewQueueItem; the delivered design adds the alias artifact, the labeled-pair set, the threshold calibration, the run manifest, the induced-pair record and the published-figure table | Whoever owns `specs/project-plan.md` | The plan's entity roster is the input other epics scope against, and it now understates E009 by six entities |
| P-11 | Amend **`project-instructions.md` § Principle II** to distinguish an estimated proportion from a census. The principle is written unconditionally — *"every reported metric MUST be published together with its interval"* — and coverage, reduction ratio and the withheld share are reported metrics this feature publishes **without** intervals. AD-008's reasoning is sound but it *narrows a Core Principle*, and Governance's first bullet is that project instructions supersede all other documentation: `specs/sad.md` cannot carry this, which is why P-8 is secondary to it. **Precedent**: v1.2.1 narrowed "No second datastore" to "no second datastore **of record**" after E004 planning raised exactly this shape, and it landed as an amendment to this document | Whoever owns `project-instructions.md` | The compliance audit found the Principle II row claimed a *strengthening* when it is a disclosed deviation. Recorded as the deviation it is |

Numbering is monotonic within this file. No obligation is routed to another feature branch.

**One finding that is not an amendment need.** E006's FR-065 ownership guard — `E003_OWNED_TABLES` in `src/model/tests/schema/test_table_ownership.py:518` — enumerates six tables (`chunk`, `document`, `extracted_value`, `extracted_value_contributing_chunk`, `extraction_failure`, `field_vocabulary`), and **neither `resolved_entity` nor `resolved_entity_member` is among them.** The test that exists to report exactly the alteration FR-045 performs is blind to it. This is not recorded as an obligation because the file is an ordinary test on this branch's own side of the line: E009 adds VR-028/VR-029 to close it. Recorded here because a reader who finds the guard green will otherwise conclude no ownership boundary was crossed, when the truth is that the guard never looked.

## Implementation Hints

- **[HINT-001]** Order: **freeze and hash the labeled set before writing `thresholds.py`.** FR-016 and FR-044 are not merely sequencing advice — a threshold constant committed before the set is frozen cannot be shown to predate it, and the hash binding in the manifest is what makes the claim checkable. Build `labeled.py` and its two strata first, commit the frozen artifact, then calibrate.
- **[HINT-002]** Gotcha: `verify.yml`'s coverage `--source` and `[tool.coverage.paths]` are **enumerations that override rather than merge**. Adding `model.identity` without appending to both leaves every line this epic writes outside the coverage denominator while the gate still reports green. E006 hit this exact trap; it recurs because the lists are enumerations, not globs.
- **[HINT-003]** Constraint: `0505` re-scopes `uq_rem__extracted_value` and `uq_rem__po_line` to include the run column — **drop and recreate, not an in-place widening** — and assert in an integration test that the *old* guarantee still holds within a single run, since the change permits a record in two entities across runs and never within one. It also **falsifies eight existing assertions** in `src/model/tests/schema/test_resolved_entity.py`: seven match the dropped constraints by name, and one asserts `resolved_entity_member` has *exactly three* foreign keys. Restate them against the new constraints rather than deleting them — a name-matched assertion that is simply removed leaves the re-scoping unasserted.
- **[HINT-004]** Gotcha: a record pair matching on **both** blocking keys must be one candidate pair, not two (FR-009). Deduplicate at the point of generation, on the unordered identity, before anything counts a denominator. Every published share is wrong by a silent factor if this is deferred to reporting.
- **[HINT-005]** Compatibility: three test files must move with the block, and one of them is a trap. `tests/checks/test_migration_ranges.py` needs `DECLARED_BLOCKS += (500, 599, "E009")` and `OWNERS_EXPECTED_TO_HAVE_REVISIONS += "E009"` — **and its negative-control probe must move from `"0500"` to `"0600"`**, because that probe is parametrized on `["0000", "0500", "9999"]` where `0500` is the just-past-the-last-block case. Left alone it becomes a real revision number and the control silently stops controlling for anything. `src/gateway/tests/test_migrations.py` asserts on block membership rather than a fixed head (E006 repaired it that way); extend it with E009's block and keep its positive control over an *undamaged* revision directory, so a broken fixture cannot make the negative controls pass for the wrong reason.
