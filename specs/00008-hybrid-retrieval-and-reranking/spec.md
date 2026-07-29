---
feature_branch: "00008-hybrid-retrieval-and-reranking"
created: "2026-07-29"
input: "E008"
spec_type: "product"
spec_maturity: "draft"
epic_id: "E008"
epic_sources: "{PRD:CAP-003}{SAD:ADR-0005}{SAD:ADR-0006}"
---

# Feature Specification: Hybrid Retrieval and Reranking

**Feature Branch**: `00008-hybrid-retrieval-and-reranking`
**Created**: 2026-07-29
**Status**: Draft
**Spec Type**: product
**Spec Maturity**: draft
**Epic ID**: E008
**Epic Sources**: {PRD:CAP-003}{SAD:ADR-0005}{SAD:ADR-0006}
**Product Document**: `specs/prd.md`

## Problem Statement *(mandatory)*

E006 filled the chunk store with 6,391 passages, each carrying the page it was printed on,
and **nothing reads it**. A coordinator with a question — which transformer did Norhelm
propose, what does the spec say about galvanizing repair, where did part `NRH-80347` come
from — has no way to reach the passage that answers it, and the page provenance the previous
epic was built to guarantee goes unspent. Until a query reaches the right passage, grounded
answering (E011) has nothing to ground on and the evaluation harness (E014) has nothing to
measure, so two downstream epics are blocked on this one. The corpus is small enough that a
poor retrieval design would still return *something* for every query, which is precisely the
danger: an unmeasured retrieval path looks like a working one.

## Scope *(mandatory)*

### Included

- A fused retrieval path combining a weighted full-text arm and a dense vector arm by
  reciprocal rank fusion, executing as **one database statement** inside the deterministic
  computation boundary
- A deterministic route for part-number-shaped queries that runs **before** fusion and falls
  through to it, never replacing it
- Reranking of the fused candidate set by a locally loaded, integer-quantized cross-encoder
- A degraded mode: when the reranker is unavailable the system serves fusion-only ordering
  and says so, in the response and at the readiness endpoint
- A configuration flag selecting exact or approximate vector search, controlling **index usage
  and nothing else**
- Each retrieval arm independently runnable, so E014 can measure sparse-only, dense-only,
  fusion-only, reranked, and the exact-versus-approximate delta
- A query-side embedding path, which does not exist today

### Excluded

- **Query transformation of any kind** — rewriting, expansion, or hypothetical-document
  generation. Measured evidence puts prompt-only rewriting at −9.0% nDCG@10 on one domain and
  non-significant on another, with lexical substitution in 95% of rewrites regardless of
  outcome; construction procurement is the stable-terminology vertical where never rewriting
  is safest. A rewrite also damages the sparse arm hardest, and that is the arm carrying the
  part numbers. **Reversal trigger**: a measured ablation arm showing a transformation improves
  recall on the frozen set with a paired difference the set size can resolve — the same bar every
  other arm clears. **Production-scale alternative**: a domain-tuned transformation trained on
  logged coordinator queries, which this project has none of and cannot obtain from a synthetic
  corpus. Any future transformation enters as a measured arm, never as a default
  (`research.md` §Query-side handling)
- **Publishing the ablation table, the frozen evaluation set, and the results manifest** —
  E014 owns those. E008 must make every arm runnable and prove each returns results; it does
  not own the claim
- **Answer composition and citation rendering** — E011 consumes `RetrievalResult`
- **Any write to the `chunk` table.** E003 owns its shape, E006 owns its population. E008
  reads. **This specifically includes `chunk.part_numbers`, which is null on every row.** The
  chunk store's search column weights heading, part numbers and specification section above
  body prose, but on the 25 synthetic transmittals `part_numbers` is null, `heading` is null
  on field blocks, and `spec_section` is null because the code is printed as body text — so
  the column reduces to unweighted body text on exactly the documents extraction targets.
  Populating it would strengthen the lexical arm and is deliberately not done here: E003
  defined the column, E006 records part numbers as extracted values in a **different table**,
  and no requirement ever assigned the denormalised copy to either. That gap is raised as
  FR-035 and gated by SC-015 rather than fixed by a cross-epic write.
  **Reversal trigger**: if the sparse-only arm's measured contribution is at or below the
  no-lexical-arm baseline, the weighting is not merely inert but load-bearing in its absence,
  and populating the column moves ahead of any further ranking work. **Production-scale
  alternative**: at production scale the column is populated during ingestion from the values
  already extracted per line item, so the denormalised copy is written once by the epic that
  owns the row rather than backfilled by a reader
- **Schema migrations.** E008 adds no tables, columns, or indexes; the full-text and vector
  indexes it uses already exist
- **Cross-document identity resolution** — E009 owns matching the same material across
  documents; E008 returns passages, not entities

### Edge Cases & Boundaries

- A query matching nothing in either arm — the empty result is reported as empty, never
  padded with low-scoring filler
- A query that is *only* a part number, and a query that mentions one inside a sentence; both
  must reach the deterministic route
- A part number that matches no chunk — the route must fall through rather than return empty
- A chunk longer than the reranker's sequence limit, which is scored on its prefix and must be
  counted rather than silently truncated
- Manufacturer alias spellings (`Ashvale Ind.`, `ASHVALE INDUSTRIAL`, `Ashvale Industrial
  Works`) and trailing-period abbreviations, which stem unpredictably
- Section codes written as space-separated MasterFormat (`26 11 13`), which tokenize to three
  bare integers with no rarity signal to separate them
- The reranker failing to load, failing to warm, or being killed mid-request under memory
  pressure
- A vector filter that returns fewer rows than requested because the graph index applied the
  filter after selecting candidates
- Both retrieval paths returning results whose ordering differs only because the index was
  rebuilt

## User Scenarios & Testing *(mandatory for product specs only)*

### User Story 1 - Ask a question, reach the passage (Priority: P1)

A coordinator types a question in ordinary language — "what does the spec require for
galvanizing repair" — and gets back the passages that answer it, each naming the document and
the page it was printed on. The lexical arm catches the exact words they typed; the dense arm
catches the passage that means the same thing in different words; the two are combined into
one ranked list.

**Why this priority**: This is the capability. Without it CAP-003 is unmet, and E011 and E014
have nothing to build on.

**Independent Test**: Issue a natural-language query against the ingested corpus and confirm
every returned passage resolves to a real document and page, with results drawn from both arms.

**Acceptance Scenarios**:

1. **Given** an ingested corpus, **When** a coordinator issues a natural-language query,
   **Then** the system returns ranked passages, each carrying its document identity and page
   number, and every page number resolves to a page that exists in that document.
2. **Given** a query whose answer is worded differently from the query, **When** it is issued,
   **Then** the dense arm contributes the matching passage and it appears in the fused result.
3. **Given** a query naming an exact token present in the corpus, **When** it is issued,
   **Then** the lexical arm contributes the passage containing that token literally.
4. **Given** any query, **When** retrieval runs, **Then** both arms are fetched and fused
   within a single database statement, and no ranking arithmetic occurs outside it.
5. **Given** a query matching nothing, **When** it is issued, **Then** an empty result is
   returned and reported as empty, with no low-scoring filler added to reach a result count.

### User Story 2 - Type a part number, get that item (Priority: P1)

A coordinator has a part number from a purchase order — `NRH-80347` — and wants the transmittal
that proposed it. They type it, and the system recognises the shape of what they typed and
looks it up directly rather than hoping a ranking function surfaces it. If nothing matches, the
question is still answered by ordinary hybrid retrieval; the route never swallows a query it
cannot serve.

**Why this priority**: Part numbers are the tokens a procurement coordinator most often has in
hand, and the arm that should carry them is inert (see Assumptions). The deterministic route is
what makes these queries reliable rather than lucky.

**Independent Test**: Query a part number known to exist and confirm the exact chunk is
returned; query a part-number-shaped string that does not exist and confirm hybrid results are
still returned.

**Acceptance Scenarios**:

1. **Given** a part number present in the corpus, **When** it is queried alone, **Then** the
   chunk printing it is returned and identified as a deterministic match rather than a ranked
   one.
2. **Given** a part number embedded in a longer question, **When** the query is issued,
   **Then** the deterministic route still fires on the recognised token.
3. **Given** a part-number-shaped string matching no chunk, **When** it is queried, **Then**
   the route falls through and hybrid retrieval results are returned.
4. **Given** any query, **When** the route fires, **Then** every result hybrid retrieval would
   have returned is still present in the response — the route adds, and never removes.

### User Story 3 - The ordering is worth trusting (Priority: P1)

The fused list is reordered by a cross-encoder that reads the query and each candidate passage
together, rather than comparing two independently produced summaries. The model is part of the
repository, loads once when the service starts, and is warmed before the service reports itself
ready, so the first real query is not the one that pays for loading it.

**Why this priority**: Fusion ordering is weak by construction — at fetch depth 50 with the
standard constant, the top candidate carries only 1.8× the weight of the fiftieth, so the fused
list is nearly unordered within its window. The reranker is what turns a candidate set into a
ranking.

**Independent Test**: Start the service, confirm readiness is withheld until warm-up completes,
then issue a query and confirm the returned order differs from the fusion order and is produced
by the reranker.

**Acceptance Scenarios**:

1. **Given** a cold service, **When** it starts, **Then** the reranker loads once and warms at
   the maximum batch shape before readiness is reported.
2. **Given** a warmed service, **When** a query returns fused candidates, **Then** the
   candidates are rescored jointly against the query and returned in the rescored order.
3. **Given** a candidate longer than the model's sequence limit, **When** it is scored, **Then**
   it is scored on its truncated form and counted in the published truncation figure.
4. **Given** repeated identical queries against an unchanged corpus, **When** they are issued,
   **Then** the returned ordering is identical.

### User Story 4 - A degraded system says so (Priority: P1)

If the reranker cannot load, the coordinator still gets results — ordered by fusion alone — and
the system states plainly that it is degraded. The result is worse and is labelled as worse.
Nothing that ran without the reranker may later be read as though it had one.

**Why this priority**: Publish-the-Miss applied to a live surface. A silently degraded system
serving fusion-only results under published reranked numbers is a false claim, and the fusion
ordering it falls back to is known to be weak.

**Independent Test**: Force the reranker to fail loading, confirm the service still answers
queries, and confirm the degraded state is visible in the response payload and at the readiness
endpoint.

**Acceptance Scenarios**:

1. **Given** a reranker that fails to load, **When** the service starts, **Then** it reports
   ready-degraded rather than not-ready, and answers queries using fusion-only ordering.
2. **Given** a degraded service, **When** any query is answered, **Then** the response states
   that results are fusion-only and unreranked.
3. **Given** a degraded service, **When** an evaluation run is executed against it, **Then**
   the run records the mode it ran in, so no figure it produces can be read as reranked.
4. **Given** a healthy service, **When** the degraded path is exercised deliberately by an
   automated test, **Then** the flag is set and results are still returned — the fallback is
   proven, not assumed.

### User Story 5 - Each arm can be measured on its own (Priority: P2)

A developer preparing the evaluation harness needs to run each part of the design in isolation:
lexical alone, dense alone, the two fused without reranking, the full reranked path, and the
same query set under exact and approximate vector search. Every arm returns results
independently, so the ablation table E014 publishes can actually be constructed.

**Why this priority**: MVP retrieval works without it, but E014 cannot publish an ablation
table over arms that cannot be run separately, and an unmeasured retrieval path is the failure
this epic's problem statement names.

**Independent Test**: Run the same query through each of the five arms and confirm each returns
a result set independently.

**Acceptance Scenarios**:

1. **Given** a query, **When** each arm is selected in turn, **Then** each returns a ranked
   result set without requiring the others to run.
2. **Given** the same query and corpus, **When** an arm is run twice, **Then** the results are
   identical.
3. **Given** an evaluation run, **When** results are produced, **Then** the parameters that
   define the ranking — fusion constant, tie-break key, missing-arm convention, fetch depth,
   and index settings where the index was used — are recorded alongside them.

### User Story 6 - One flag, index usage only (Priority: P2)

The evaluation path scans every vector exactly; the serving path uses the tuned graph index.
One flag chooses between them and controls nothing else. Filters, fusion, fetch depth and
reranking are the same code on both sides, so the path that gets measured is the path that runs.

**Why this priority**: ADR-0005 accepts two code paths only on condition they cannot drift.
Without the constraint being enforced, published numbers stop describing the served system.

**Independent Test**: Run the same query under both flag settings and confirm the only
difference is which vector-search strategy executed.

**Acceptance Scenarios**:

1. **Given** the flag in either position, **When** a query runs, **Then** filters, fusion
   constant, tie-break, fetch depth and reranking behaviour are identical.
2. **Given** the approximate path, **When** it runs, **Then** the index search breadth is
   greater than or equal to the fetch depth.
3. **Given** a filtered query on the approximate path, **When** it runs, **Then** it returns
   the requested number of candidates where that many matching rows exist.
4. **Given** both paths run over the same query set, **When** their results are compared,
   **Then** the difference is expressible as a recall delta attributable to approximation
   alone.

## Requirements *(mandatory)*

### Functional Requirements *(product specs only)*

**Retrieval and fusion**

- **FR-001**: System MUST retrieve candidates from a lexical arm and a dense vector arm and
  combine them by reciprocal rank fusion.
- **FR-002**: System MUST execute candidate selection from both arms and their fusion as **one
  database statement**. No ranking arithmetic may occur in application code.
- **FR-003**: System MUST fetch **50 candidates per arm**. This is part of the ranking
  definition, not a tuning parameter: changing it changes published output, so it MUST be
  recorded with any published figure.
- **FR-004** *(amended 2026-07-29)*: System MUST fix the fusion constant, the tie-break key applied
  when two candidates fuse to the same score, and the convention for a candidate appearing in only one
  arm's list **before any figure is measured against the evaluation set**, and MUST record all
  three alongside every result. The fusion formula fixes none of them and each changes the
  output, so a constant left free while a target is measured on a frozen set is the
  tune-to-the-test-set path Principle VI exists to close. Changing any of the three after
  measurement MUST be recorded as a decision and re-measured, exactly as FR-003 treats the
  fetch depth. **The two non-numeric values MUST be recorded as stable identifier tokens, not as
  prose**: what "record all three alongside every result" is *for* is an equality test between what two
  runs recorded, and `chunk_id ascending` against `ascending by chunk_id` names one rule while comparing
  unequal — under free-form text a re-wording is indistinguishable from a re-tuning, which is the one
  difference this requirement exists to catch. `contracts/openapi.yaml` §RankingParameters constrains
  the **form** (a lowercase identifier, emitted from the single source in force) and deliberately leaves
  the **choice of value** outside the contract, so fixing a value stays a decision recorded here rather
  than becoming a contract revision.
- **FR-005**: System MUST derive the lexical arm's ranking from the chunk store's existing
  field-weighted search column and MUST NOT introduce a second full-text column or a second
  weighting scheme. **The weighting this column applies is currently inert on the synthetic
  layer** — see Excluded and FR-035 — so a run MUST publish, per layer, the proportion of
  retrieved chunks whose heading, part-number and specification-section fields are all empty.
  A weighting that applies to nothing is reported rather than assumed to be working.
- **FR-006**: System MUST NOT name BM25 in any published figure, figure label, or accompanying
  description of the lexical arm, and any comparison drawn against an external retrieval
  benchmark MUST state that this arm uses no corpus-wide term statistics. Asserted over the
  emitted artifacts, not over intent: published BM25 results do not transfer to this arm
  (`research.md` §Sparse retrieval quality).
- **FR-007**: System MUST embed the query with the same pinned model identity and revision the
  chunks were embedded with ({SAD:ADR-0012}), reaching no network at query time, and MUST refuse to retrieve
  when the query encoder's identity differs from the identity recorded on the chunks.
- **FR-008**: System MUST return, for every result, the document identity, document type,
  project, and page number carried by the chunk, and MUST NOT synthesise, infer, or accept a
  page number from any other source.
- **FR-009**: System MUST report an empty result set as empty and MUST NOT pad a short result
  set to reach a target count.

**The deterministic route**

- **FR-010**: System MUST recognise part-number-shaped tokens in a query and resolve them by
  direct lookup before hybrid retrieval runs. **The recognised form MUST be declared as a
  published pattern** and MUST cover the form the corpus prints — an uppercase alphabetic
  manufacturer prefix, a hyphen, and a digit run — and MUST be verified against the enumerated
  set of part numbers the corpus contains, so the pattern's coverage is a measured figure
  rather than a claim. *(amended 2026-07-29)* **The enumeration's source is named rather than
  assumed available**: the generator's pre-render document model, already E006's FR-067 reference
  set and reproducible from the committed seed (`plan.md` AD-009). Neither `chunk.part_numbers`,
  null on every row, nor `extracted_value`, empty while extraction is fixture-blocked, can supply
  it — so a verification stated without its source would be unrunnable today.
  **Arm scoping, restated here because this requirement reads as unconditional** *(added 2026-07-29)*:
  the route runs on the `fused`, `fused_reranked` and `fused_reranked_full_precision` arms and **not**
  on the single-arm `lexical` and `dense` measurement paths, where a deterministic identifier hit is a
  contribution of neither arm and letting the route fire would attribute it to whichever arm was under
  measurement — the sparse-only figure being a first-class published row rather than an assumed
  positive (§Risks). FR-014 is narrowed with it. The narrowing is visible to a caller
  (`contracts/openapi.yaml` §DeterministicRoute.skipped_reason = `arm_excludes_route`) and is recorded
  in the four-part limitation format Principle VII mandates at `plan.md` §Contract Decisions Confirmed
  at Plan, rather than living only as an interface decision with a rationale.
- **FR-011**: System MUST fall through to hybrid retrieval when the deterministic lookup
  matches nothing, and MUST NOT return an empty result on the strength of the route alone.
- **FR-012**: System MUST include, in every response where the route fired, every result hybrid
  retrieval would have returned for the same query. The route is additive: it may reorder and
  it may add, and it MUST NOT remove.
- **FR-013**: System MUST distinguish a deterministic match from a ranked match in the response,
  so a caller can tell an exact identifier hit from a relevance judgement.
- **FR-014**: System MUST apply FR-010's declared pattern to tokens appearing anywhere in the
  query, not only to a query consisting of the token alone.

**Reranking**

- **FR-015**: System MUST rerank the fused candidate set with a cross-encoder that scores the
  query and each candidate jointly.
- **FR-016** *(extended 2026-07-29 at Analyze)*: System MUST load the reranker from an artifact
  committed to the repository, with a recorded identity, revision, **license basis and source**, and
  a digest verified before the session is created, reaching no network at run time. License basis is
  required of every artifact entering this repository, and a vendored third-party model is no
  exception. Two further obligations, previously carried by `plan.md` and `tasks.md` and by no
  requirement: **(a)** the INT8 graph is a *generated* artifact, so its record MUST additionally
  carry the **generator identity, the seed, the date and the hash of the source graph it was
  produced from** — without them the quantized graph is an unreproducible binary whose provenance
  stops at "someone quantized something"; and **(b)** both graphs MUST record their licence basis
  **separately**, because the INT8 graph derives from the FP32 one and a derived artifact does not
  automatically inherit the licence of its source — a single shared licence line would assert an
  inheritance nobody checked. *(The generated-artifact half was carried by AD-007 and T017 alone;
  Data Provenance governs it and no requirement enforced it.)*
- **FR-017** *(amended 2026-07-29)*: System MUST load the reranker once per process, warm it at the
  **maximum** batch and sequence shape, and withhold readiness until warm-up completes. The maximum
  shape is defined numerically rather than left to the implementer: **batch equal to the reranked
  count FR-018 fixes at 50**, and **sequence equal to the model's declared maximum sequence length**,
  the same number FR-019 publishes the truncation fraction against. The shape actually warmed at, and
  the measured load-and-warm duration, MUST both be reported — warming at a smaller shape and
  reporting "warm" leaves the arena growth this gate exists to move off the request path sitting in
  the first full-depth query, and a duration nobody records is a startup-probe threshold nobody can
  choose. **Both reranker sessions AD-011 commits to** — the INT8 graph and FR-025's full-precision
  graph — are loaded and warmed before readiness, because arms are selected per request (AD-006) and
  this requirement forbids loading a graph on a request path.
- **FR-018** *(amended 2026-07-29)*: System MUST rerank exactly the fused candidate set FR-003
  defines, and the reranked count MUST equal the fetch depth. Any change to that count MUST be
  accompanied by a re-measured ablation showing its effect **and by the re-measured latency figure
  FR-033 defines**, because reranking quality declines past a depth limit and a larger candidate set
  can rank worse than a smaller one (`research.md` §Cross-encoder reranking) — **and** because the
  count is the primary driver of the reranking latency budget, so a change to it moves the quality
  figure and the performance figure together. Stating only the quality consequence would leave a
  developer free to change the count against a budget nobody told them it governed.
- **FR-019** *(amended 2026-07-29)*: System MUST publish the distribution of candidate lengths
  against the reranker's sequence limit and the fraction of candidates truncated, because a
  truncated candidate is scored on its prefix and that is invisible in the result. **The sequence
  limit MUST itself be published as a number**, taken from the artifact identity FR-016 records,
  rather than left as a property of the model the reader is expected to look up: it is the
  denominator of the truncation fraction, the sequence half of FR-017's warm-up shape, and the
  per-candidate anchor of the latency budget, and all three are unanchored while it is only a name.

- **FR-037** *(placed out of sequence; appended by number because requirement IDs are never
  reused)*: System MUST treat three settings as **one derived constraint** rather than as three
  independent parameters: FR-003 fixes the fetch depth at 50 candidates per arm; FR-018 fixes the
  reranked count equal to that depth; FR-027 floors the approximate index's search breadth at that
  same depth. Together they fix the reranker's per-query workload at exactly the 50 candidates the
  latency budget is stated for, and no one of them says so on its own. Because the fused set of two
  50-candidate arms may contain up to **100 distinct candidates**, the reranker scores the **top 50
  of the fused ordering** — FR-018's reranked count is the binding number, and the total order AD-001
  establishes is what selects them. Changing any one of the three changes the workload the budget was
  set against, so each change MUST carry the re-measured latency figure FR-033 defines alongside the
  re-measured quality figure FR-018 requires.
- **FR-038** *(placed out of sequence; appended by number because requirement IDs are never
  reused)*: System MUST set the reranker's intra-op and inter-op thread counts explicitly from
  configuration, by a stated derivation rule: **intra-op equal to the container's CPU quota in whole
  vCPUs** — one, under the environment FR-033 fixes — and **inter-op equal to one**, the default
  sequential execution mode requiring no more. The values in force MUST be reported with the figures.
  The runtime's own default is one thread per core **the operating system reports**, which under a
  CPU quota is the host's core count rather than the container's, and a runtime that picks the count
  itself also pins thread affinity — so an unset count oversubscribes silently and degrades the
  latency figure with no error to attribute it to (`research-implementation.md` §ONNX Runtime
  cross-encoder inside a container). A thread configuration that exists only as a `NEW-CONFIG` signal
  and a mitigation note is a requirement nobody is obliged to meet.
- **FR-020** *(amended 2026-07-29)*: System MUST return identical ordering for identical queries
  against an unchanged corpus and an unchanged configuration. **The guarantee is narrowed on one
  axis and the narrowing is normative over this sentence**, restated here because a reader of the
  spec alone would otherwise read a guarantee the plan does not assert: across an **index rebuild**
  it is asserted on the **exact path only**, because graph construction is randomized by insertion
  order and parallel build workers ({SAD:ADR-0005}), and the approximate path is asserted instead by
  candidate overlap and recall delta. `plan.md` §Testing Strategy records that narrowing in the
  four-part limitation format Principle VII mandates and declares it normative under
  {SAD:ADR-0017}, the record that exists to let a plan-phase artifact override a specify-phase
  requirement. Two runs against the **same built index** remain identical on every arm — that is the
  unnarrowed half, and it is what SC-012 measures.
- **FR-033** *(amended 2026-07-29)*: System MUST report reranking latency per query and the resident
  memory of **every model session the serving process holds**, both against the budgets
  `specs/sad.md` declares — §Technical Context "Performance Goals" for latency and §Quality
  Attributes "Compute envelope" for the 400 MB. A run exceeding either MUST publish the overage
  rather than omitting the figure. The original wording named a workload, an environment and a
  reporting duty but no method, so two runs could report the same quantity differently and neither be
  wrong. The figures MUST therefore be taken as follows:
  - **Workload**: one query at a time — traffic is effectively single-user (see Assumptions), so this
    is a per-query cost and not a throughput figure — scoring the 50 candidates FR-037 fixes, each
    truncated at the sequence limit FR-019 publishes.
  - **Environment**: the serving container under an enforced **CPU quota of one vCPU (1.0)**, which
    is `specs/sad.md`'s "one shared vCPU" expressed as a container limit rather than as a
    description, because a figure taken without the quota applied is not comparable to one taken with
    it; and the thread configuration FR-038 requires, since the runtime cannot read the quota itself.
  - **Measurement point**: wall-clock time inside the serving process spanning the reranker
    component's scoring call — entered with the fused candidate set, left with its scores, inclusive
    of tokenization and batching. Database access, the fusion statement, and FR-007's query-side
    encoder call are **outside** this span. The fusion statement's wall-clock time and the encoder
    call's wall-clock time MUST each be reported beside it as their own figures, so the composition
    of a query is visible and no step is silently folded into the reranking number. Neither carries a
    budget of its own; see the Assumptions entry on corpus size.
  - **Measurement occasion**: after the service has reported ready, so FR-017's load-and-warm cost is
    excluded from the latency figure **by rule rather than by convention**. Resident memory is read
    at the same occasion and again after the run's queries have been served — that second reading is
    what "steady state" means here — with the peak observed during the run reported beside it.
  - **Counter**: the serving process's **resident set size (RSS)**, itemized by session — query
    encoder, INT8 reranker, and FR-025's FP32 reranker — against the one container total. AD-011
    ships two reranker graphs and FR-017 keeps both resident, so the envelope is reported for the
    sessions the process actually holds rather than for a single dominant one. The 400 MB is not
    apportioned between the sessions and the rest of the process; the itemization is what makes the
    single total attributable.
  - **Arms**: the budgets bind the **INT8 serving arm**. FR-025's full-precision arm is measured and
    reported against the same budgets with any overage published, because {SAD:ADR-0006} obliges the
    quantized-versus-full-precision difference to be measured rather than assumed — no separate
    budget has ever been declared for it, and this requirement does not invent one.
  - **Qualifier**: every figure MUST carry the corpus size it was measured at, so a number taken at
    6,391 chunks is not read as holding across the 5,000–15,000 design band.
  Per-query figures travel with the search response and process-level figures with the diagnostics
  surface, the split `contracts/openapi.yaml` §"Which figures live where, and why" makes normative.
  **What this requirement does not settle**: `specs/sad.md` states the latency budget as a
  150–400 ms *range* and names no mean, percentile or never-exceed, so it fixes how an observation is
  taken but not which statistic of those observations meets the budget. That statistic is an
  amendment against `specs/sad.md` — see `plan.md` §Pending Amendments — and is not chosen here,
  because the request-time compute envelope is an architectural constraint that a feature-level
  artifact may not set or relax.

**Degradation**

- **FR-021** *(amended 2026-07-29)*: System MUST serve fusion-only ordering when the reranker is
  unavailable, and MUST report ready-degraded rather than not-ready. **"Unavailable" includes a session
  lost mid-request** — the Edge Case naming a reranker killed under memory pressure — and not only a
  failure to load or to warm at startup. The in-flight request MUST complete as a **degraded success**
  carrying its results in fusion-only order, never as a fault: the candidates were already retrieved,
  and a worse ordering is still an ordering, which is the whole basis on which FR-021 degrades rather
  than refusing. The two causes MUST remain distinguishable in the response
  (`contracts/openapi.yaml` §Mode.unreranked_reason `reranker_unavailable` against
  `reranker_failed_during_request`, §RerankerFailure.reason `session_lost`), so a run does not read a
  mid-request loss as a startup failure. Stated because the original wording paired "unavailable" with
  a readiness report, and a readiness state is decided before a request arrives — leaving the request
  that fails halfway through owned by nothing.
- **FR-022** *(amended 2026-07-29)*: System MUST state, in every response whose ordering is
  **fusion-only because the reranker was unavailable**, that the result is fusion-only and unreranked,
  and MUST expose the degraded state at the readiness endpoint. Every response produced without
  reranking for **any** reason MUST additionally carry a machine-readable reason it was not reranked,
  so the unreranked fact is never silent.
  **The fusion-only claim is scoped to the degraded case and the original wording was wrong to
  generalise it.** "Every response produced without reranking" also caught a `lexical` or `dense`
  response, which is unreranked and is **not** fusion-only — it is one arm's ordering, not a fusion of
  two — and the empty result, where the reranker had nothing to score and nothing was degraded.
  Attaching a fusion-only statement to either would state something false about what produced it, which
  is the same defect in the opposite direction from the one this requirement exists to prevent. The
  vocabulary that separates them is already contract-normative:
  `contracts/openapi.yaml` §Mode.unreranked_reason distinguishes `arm_excludes_reranking` and
  `no_candidates_to_score` (neither a degradation) from `reranker_unavailable` and
  `reranker_failed_during_request` (both degradations, and both carrying the statement this requirement
  obliges).
- **FR-023**: System MUST record the mode a run executed in on any output an evaluation consumes,
  so no figure produced in degraded mode can be read as reranked.
- **FR-024** *(amended 2026-07-29)*: System MUST exercise the degraded path in an automated test
  that forces reranker load failure and asserts **each of the three observables FR-021, FR-022 and
  FR-023 separately declare**, rather than the flag alone — a test asserting one boolean proves one
  of three:
  - **(a)** the readiness endpoint reports **ready-degraded**, and neither not-ready nor plain ready
    (FR-021);
  - **(b)** the search response body **states that the result is fusion-only and unreranked**, and
    still carries a non-empty ranked result set for a query that matches (FR-022, and the
    "results are still returned" half);
  - **(c)** the **evaluation-facing output records the mode the run executed in** (FR-023), so no
    figure it produces can be read as reranked.

  **The failure MUST be forced at the artifact-loading boundary** — an absent, unreadable or
  digest-mismatched reranker artifact, so the exception arises where FR-016's verification runs and
  is caught inside the startup path that FR-021 constrains — and MUST NOT be forced by setting the
  degraded flag directly or by injecting an already-degraded component. A test that sets the flag
  proves the flag; the behaviour under test is the fallback that sets it.
- **FR-041** *(placed out of sequence; appended by number because requirement IDs are never
  reused)*: System MUST report the fusion-only degraded path's per-query latency on the same terms
  FR-033 sets for the reranked path, and that latency MUST NOT exceed the reranked path's latency
  over the same query set. The degraded path performs a strict subset of the reranked path's work, so
  a degraded query that is not faster is a defect rather than an accepted cost. The expectation is
  stated rather than assumed: "removing the reranker can only make a query faster" is an inference,
  and a path nobody budgeted is a path nobody is obliged to measure — which is how a degraded surface
  that is worse in a second dimension would go unnoticed.

**Measurability and configuration**

- **FR-025** *(amended 2026-07-29)*: System MUST make each retrieval arm independently runnable,
  returning results without requiring the others: **lexical only, dense only, fused without reranking,
  fused and reranked, and fused and reranked at full precision**. The full-precision arm is
  required because {SAD:ADR-0006} obliges the quantized-versus-full-precision difference to
  be measured and published rather than assumed negligible, and no other epic can construct
  an arm this epic did not make runnable. **Five arms are request-selectable; the sixth SC-012 counts
  is not one of them**, and the two numbers describe one design rather than disagreeing: the
  exact-versus-approximate delta is obtained from the FR-026 configuration flag, which is service
  configuration and not a request field ({SAD:ADR-0005}, `plan.md` AD-006), so it is measured by running
  the same query set against **two differently configured processes** and never by a sixth value a
  caller could select. `contracts/openapi.yaml` §Arm is closed at the five and
  §DiagnosticsResponse.arms enumerates the same five; SC-012's six is five request-selectable arms plus
  one configuration. Stated because the count previously appeared as five in this requirement, six in
  SC-012 and six again in `plan.md`'s coverage map, and a reader had no way to tell which was the
  miscount.
- **FR-036** *(amended 2026-07-29)*: System MUST report the reranked ordering against **the
  strongest single arm**, not only against fusion-only ordering. Fusion at the fixed fetch depth is
  nearly unordered within its window by construction, so a comparison drawn only against it is close
  to guaranteed to succeed and carries little information. Where fusion-only is reported it MUST
  be labelled as the weak comparator it is. **"Strongest single arm" is reduced to a selection rule
  and a statistic**, because a comparator chosen after the figures are seen makes SC-008
  unadjudicable — the comparison would be picked to be beaten. The rule: the candidates are the two
  **single** arms, lexical-only and dense-only (fusion is not a single arm and is the weak
  comparator this requirement exists to supplement); for **each reported statistic** the comparator
  is the candidate arm with the higher point estimate **of that statistic** on the same query set;
  and where the two candidates' intervals overlap on that statistic, "strongest" is not resolvable
  at the set size and **both** are reported as comparators under FR-032's verdict rather than one
  being chosen. What is fixed before measurement is the **rule**, as FR-004 fixes the ranking
  parameters; which arm it selects is a result. The selected arm and the statistic that selected it
  MUST be recorded with the comparison.
- **FR-026** *(amended 2026-07-29)*: System MUST expose a configuration flag selecting exact or
  approximate vector search, and that flag MUST control index usage only. Filters, fusion, fetch
  depth and reranking MUST be shared by both paths, and that sharing MUST be asserted by a test
  rather than left to review. **What that test compares is enumerated here**, because SC-013's
  "zero behavioural differences other than vector-search strategy" is a claim over an observable set
  that nothing previously named. For the same query against the same corpus, the two settings MUST
  agree on: the ranking parameters in force (fusion constant, tie-break key, missing-arm convention,
  fetch depth, reranked count — FR-029); whether the deterministic route fired and what it
  contributed (FR-010–FR-013); the reranker arm and the precision that ran; the degraded flag; the
  provenance fields and match kind on every result the two responses share; and the
  requested-candidate-count behaviour of a filtered query (FR-028). **The one permitted difference**
  is which vector-search strategy executed and what follows from it — the dense arm's candidate set
  and the fused ordering downstream of it — which US6 requires be expressible as a recall delta
  attributable to approximation alone. **The comparison is constructed across two differently
  configured processes**, not two requests: the flag is service configuration ({SAD:ADR-0005},
  `plan.md` AD-006), so the two settings never coexist in one process and a comparison written as
  two requests against one process could not be built at all.
- **FR-027**: System MUST set the approximate index's search breadth to at least the fetch
  depth. The default breadth is below the fetch depth and silently reduces recall
  (`{SAD:ADR-0005}`).
- **FR-028**: System MUST return the requested candidate count from a filtered vector search
  wherever that many matching rows exist, rather than the smaller set the index returns when it
  filters after selecting candidates.
- **FR-029**: System MUST record, with any result an evaluation consumes, the ranking parameters
  in force — fusion constant, tie-break key, missing-arm convention, fetch depth, and the index
  settings where the index was used.
- **FR-039** *(placed out of sequence; appended by number because requirement IDs are never
  reused)*: System MUST verify the `pgvector` extension version present in the digest-pinned image
  **before** relying on iterative scan to satisfy FR-028, and MUST record the observed version with
  the index settings FR-029 emits. Iterative scan exists only from extension version 0.8.0; below it
  the setting does not exist and AD-003's filtered-recall design is unavailable in its entirety.
  HINT-002 already calls this "a task, not an assumption" — a precondition an architecture decision
  rests on is a requirement, not an implementation hint, and a hint carries no obligation and no
  verifier.
- **FR-040** *(placed out of sequence; appended by number because requirement IDs are never
  reused)*: System MUST record the approximate index's search breadth as a ranking parameter
  (FR-029), and MUST treat any value above FR-027's floor as a **recorded change carrying a
  re-measured latency figure**, exactly as FR-003 treats the fetch depth. No numeric ceiling is
  declared here: breadth trades dense-arm latency for recall continuously, so what bounds it is
  FR-033's latency budget rather than a second number invented beside it. Where the extension
  predates 0.8.0 and a wider breadth is the only in-scope remedy for filtered recall (FR-039,
  AD-003), the run MUST state the breadth used, the recall it bought and the latency it cost, and
  MUST publish any resulting budget overage rather than absorbing it into a setting nobody reads.
**Measurement boundary.** E008 **computes and emits** the figures below; E014 owns the frozen
evaluation set, the results manifest, and the act of publishing. The requirements are worded as
emission so that E008 is testable inside its own boundary and E014's harness has something
well-defined to consume.

- **FR-030**: System MUST emit recall on the top five results as a proportion, together with a
  Wilson 95% interval, over whichever query set it is run against. The target is read against
  the point estimate, not the interval bound.
- **FR-031** *(amended 2026-07-29)*: System MUST NOT emit a Wilson interval on mean reciprocal
  rank. Mean reciprocal rank is a mean of reciprocal ranks, not a binomial proportion, so no Wilson
  interval exists for it; the interval MUST be a percentile bootstrap over the query set.
  **`specs/prd.md` currently specifies a Wilson interval for this statistic** — see FR-034 and
  SC-015. **The prohibition carries an observation that would detect its reintroduction**, because a
  prohibition is otherwise satisfied by every implementation that simply never calls the function
  and no test can fail: every emitted interval MUST record the **method that produced it** —
  `wilson`, or `percentile_bootstrap` with its resample count and seed — and the assertion is made
  over the **emitted artifact rather than over intent**, the same shape FR-006 uses. No figure
  emitted for mean reciprocal rank, or for any statistic that is not a proportion, may carry an
  interval whose recorded method is `wilson`; that is the observation, and it fails when the
  prohibition is breached rather than when someone remembers to look.
- **FR-032** *(amended 2026-07-29)*: System MUST emit a verdict of "not resolvable at this set size"
  where two arms' intervals overlap, rather than reporting the larger point estimate as a win.
  **Overlap is decided by a stated rule, so two adjudications of the same pair of figures agree**:
  intervals are treated as **closed**, and two overlap when `lower_A ≤ upper_B` **and**
  `lower_B ≤ upper_A` — so intervals meeting at exactly one endpoint count as overlapping and the
  verdict is unresolvable. The inclusive reading is fixed here rather than left to an implementer
  because Principle III biases a silent mistake toward refusal and Principle II forbids presenting a
  difference the set cannot support. The two intervals compared MUST be of the **same statistic,
  computed by the same method, over the same query set**. **The test is conservative and is labelled
  as such**: non-overlap implies a difference the set can resolve, while overlap does not by itself
  imply no difference — which is why SC-008 also requires paired per-query differences rather than
  resting on interval geometry alone.
- **FR-042** *(placed out of sequence; appended by number because requirement IDs are never
  reused)*: The functions computing FR-030's proportion and its Wilson interval, FR-031's percentile
  bootstrap, and FR-032's overlap verdict MUST be developed **test-first (red-green-refactor)** and
  MUST carry **property-based tests**. The obligation is carried by a requirement rather than by a
  plan table cell, so that something fails when it is skipped. These are pure scoring functions over
  a query set with **no SQL obstacle** — unlike fusion ranking, which FR-002 puts inside one
  statement and which is therefore tested through a substituted oracle (`plan.md` §Testing
  Strategy) — so the policy's mandate applies to them directly and needs no substitute. The
  properties MUST include at least:
  - **Wilson interval**: the interval contains the point estimate; both bounds lie within [0, 1];
    the interval is defined and non-degenerate at zero successes and at all successes, which is the
    reason for this interval rather than the normal approximation; and its width is non-increasing
    as the query set grows at a fixed observed proportion.
  - **Percentile bootstrap**: the lower bound never exceeds the upper; both bounds lie within the
    range of the per-query values resampled, so no bound is reachable that no query produced; a set
    whose per-query values are all equal yields a zero-width interval at that value; and the
    interval is reproducible from the recorded seed and resample count FR-031 requires emitted
    beside it.
  - **Overlap verdict**: symmetry — exchanging the two arms does not change the verdict;
    reflexivity — an interval compared with itself is unresolvable; and agreement with FR-032's
    closed-interval rule at the touching-endpoint boundary.

- **FR-043**: System MUST commit its own evaluation query set with its relevance judgements, hash it
  before any figure is measured against it, and MUST abort rather than report when the harness finds
  the digest does not match. Judgements are derived from the generator's pre-render document model,
  so every query is answerable by construction and the resulting recall is an **upper bound on
  real-world performance**, which MUST be published as such rather than as an estimate. The set may
  be measured against without limit; what is disciplined is **tuning after measurement**. Any change
  to a ranking parameter made after a figure has been measured MUST be recorded as a decision, the
  set re-measured, and **the figure before the change and the figure after it published together**.
  Publishing only the later figure satisfies the re-measurement and hides the tuning, which is the
  half that makes a re-tune visible. *(Added 2026-07-29. The set was committed to by AD-010 and
  depended on by SC-001 and SC-002, and no requirement obliged it — so a deliverable that two
  published figures rest on could have been skipped without any requirement failing. Principle VI is
  the source; this makes it enforceable. The both-figures obligation was extended in on the same
  date: §Decisions Taken at Checklist replaced a run budget with it, and it was carried by that
  section and by `plan.md` alone.)*
- **FR-044**: System MUST NOT begin implementation until the amendment against
  `project-instructions.md` §Source Code Layout has landed on the default branch, verified by citing
  the amending revision. That clause reads that the gateway package carries neither a web framework
  nor the modeling stack, and ADR-0022 places an inference runtime there which pulls NumPy — so
  until it lands, the design contradicts the governing document. *(Added 2026-07-29: FR-034 and
  FR-035 covered two of the four blocking amendments and this one, the most consequential, was
  traceable by prose alone.)*
- **FR-045**: System MUST NOT begin implementation until `specs/sad.md`'s decision-record catalog
  carries ADR-0022's row, verified by citing the amending revision. A registered index that omits an
  accepted record disagrees with the record set it indexes. *(Added 2026-07-29, same reason as
  FR-044.)*
- **FR-046**: System MUST bound the returned result array by a stated rule rather than a fixed
  ceiling: at most `limit` ranked results plus at most one deterministic match per part-number token
  recognised in the query, with the query length capped. The rule bounds only the ranked portion by
  `limit` because **FR-012** requires route matches be unioned additively and counted outside it — a
  single ceiling over both would make the route subtractive at the boundary, which is the property
  FR-012 exists to forbid. *(Added 2026-07-29. The contract declared a fixed maximum of 100 that
  nothing derived — `limit` cuts the ranked portion to 50 before return and route additions are
  counted outside it, so the cap silently assumed the route contributes at most 50. The rule was
  resolved at checklist and recorded in `plan.md`, and no requirement or task carried it.)*
- **FR-047**: System MUST NOT begin implementation until the amendment against
  `project-instructions.md` §Technology Stack has landed on the default branch, verified by citing
  the amending revision. That clause names ONNX Runtime **"for INT8 CPU inference"**, and this epic
  runs an FP32 query encoder and — per FR-025 — an FP32 reranker arm beside the INT8 one, so the
  qualifier excludes two things the design ships. *(Added 2026-07-29 at Analyze. E006 raised the same
  conflict in its PR body and E008's plan depended on that landing. A PR body is not the amendment
  queue: nobody is obliged to perform it, nothing fails when it is skipped, and both FR-025 and
  FR-007 rest on it. `plan.md` §Pending Amendments item 10.)*
- **FR-048**: System MUST NOT begin implementation until the amendment against `specs/sad.md`'s
  retrieval-metrics row has landed on the default branch, verified by citing the amending revision.
  That row specifies a Wilson 95% interval for mean reciprocal rank, which is not a proportion —
  **the identical defect FR-034 raises against `specs/prd.md`**. *(Added 2026-07-29 at Analyze.
  FR-034 queued one occurrence and this second one went unrecorded, so `specs/prd.md` would have been
  corrected while a second registered document went on specifying the invalid interval. Governance
  holds the registered document wins where a downstream artifact conflicts, so FR-031 loses to
  `specs/sad.md` until this lands — the exposure FR-034 exists to close, left open on the other half.
  `plan.md` §Pending Amendments item 9.)*

  The generated input domains MUST reach the cases those properties are stated for: query-set sizes
  from one upward, all-hit and all-miss sets, ties in reciprocal rank, and per-query values at both
  ends of their range — so a property is neither reported as holding on inputs the harness never
  produced nor falsified on one it cannot produce. **An empty query set is refused rather than
  reported as a figure**: a proportion over zero queries is undefined, and Principle III records an
  absent value as absent rather than storing a wrong one.

**Amendments this epic records and does not perform.** Governance serializes amendments onto the
default branch: a feature branch records the need. Both are gated by SC-015 so the conflict they
name has a bounded life.

- **FR-034**: This epic MUST record an amendment request against `specs/prd.md`, whose retrieval
  MRR row specifies a Wilson 95% interval for a statistic that is not a proportion. Until it
  lands, a registered document and FR-031 disagree, and Governance holds that the registered
  document wins — so the disagreement MUST be visible at every point a reader could act on it,
  not only inside FR-031.
- **FR-035**: This epic MUST record an amendment request against `specs/project-plan.md`
  assigning the population of `chunk.part_numbers` to an owning epic. E003 defines the column
  and E006's acceptance criteria do not mention it, so a follow-up "against E006" currently
  records against nothing. The lexical arm's field weighting is inert until it is assigned.

### Key Entities *(include for product or technical specs if feature involves data)*

- **Chunk**: A passage of a document with its page number, structural metadata, field-weighted
  search vector and dense embedding. Owned by E003 (shape) and E006 (population); **read-only
  to this epic**.
- **RetrievalResult**: One ranked passage returned for a query — the chunk it came from, its
  document and page, the arm or arms that produced it, its fused rank, its reranked rank where
  reranking ran, and whether it was a deterministic identifier match or a relevance judgement.

## Assumptions & Risks *(mandatory)*

### Assumptions

- The chunk store is populated and its embeddings were produced by the pinned encoder identity.
  E006 delivers 6,391 chunks over 26 documents; the 25 synthetic transmittals carry chunks but
  **no extracted values**, because E006's extraction is blocked on recorded provider fixtures.
- **The two amendments FR-034 and FR-035 raise will land on the default branch before
  implementation begins.** Both are recorded and neither is performed here, because Governance
  serializes amendments on the default branch and a feature branch records the need. Until the
  `specs/prd.md` amendment lands, that document specifies a Wilson interval on mean reciprocal
  rank while FR-031 forbids one — and Governance holds that the registered document wins where
  a downstream artifact conflicts. SC-015 gates implementation on the amendments landing so the
  conflict has a bounded life rather than an open one.
- A published retrieval target is read against the point estimate rather than an interval bound.
  At the frozen set's size a lower-bound reading would make the recall target effectively
  unattainable. **Reversal trigger**: an evaluation set large enough that the Wilson interval's
  half-width at the target falls below the margin between the target and the observed estimate —
  at which point the lower-bound reading costs nothing and should be adopted. **Production-scale
  alternative**: a set sized in the hundreds of queries, which the power analyses this project
  cites treat as the minimum for resolving small differences, read against the lower bound.
- Traffic is effectively single-user, so reranking latency is a per-query cost rather than a
  throughput constraint.
- The corpus stays within the size band where an exact vector scan is fast enough to serve the
  evaluation path. **The band is numeric — 5,000–15,000 chunks (`plan.md` §Technical Context
  "Scale/Scope"), 6,391 today — but "fast enough" carries no threshold**, because no budget has ever
  been declared for a retrieval query as a whole: `specs/sad.md` budgets the reranking step, the
  worklist and the grounded answer, and not the fusion statement. The exact path's per-query latency
  is reported at the measured corpus size under FR-033, so the assumption becomes falsifiable the
  moment a query-level budget exists. Until then this entry is an assumption in the strict sense and
  is not a criterion anything can fail. **Reversal trigger**: a query-level latency budget declared
  in `specs/sad.md`, or a measured exact-path query exceeding the 400 ms this epic never-exceeds for
  the reranking step alone — either turns the band into a threshold and this entry into a criterion.
  **Production-scale alternative**: an approximate index sized and tuned against a declared
  end-to-end budget, with the exact path retained only as the recall reference the approximate path
  is measured against, which is what the FR-026 flag already makes selectable.

### Risks

- **The lexical arm contributes little or nothing** *(likelihood: medium, impact: high)*: it
  has no corpus-wide term statistics to tell a rare manufacturer name from a ubiquitous word,
  and the field weighting that was meant to compensate is inert on the synthetic layer because
  `part_numbers` is null on every row (see Excluded). Its ranking therefore rests on unweighted
  body-text frequency over a corpus whose discriminating tokens are hyphenated part numbers,
  alias-spelled manufacturer names and space-separated section codes. The epic's own acceptance
  criterion — fusion "with field weighting on the sparse arm" — is satisfiable in form while the
  weighting does nothing. Mitigation: the sparse-only arm is a first-class published row rather
  than an assumed positive, so its real contribution is measured before anything relies on it;
  FR-035 raises the population gap where it can be fixed.
- **The reranker does not fit the compute envelope** *(likelihood: medium, impact: high)*: the
  model **sessions** are the dominant memory line item in the serving container — plural, and
  restated as such on 2026-07-29: AD-011 commits to an INT8 graph and a full-precision graph, and
  FR-017 keeps both resident, so the 400 MB envelope covers two reranker sessions plus the query
  encoder rather than the single session this row and `specs/sad.md`'s matching note were both
  written against. Reranking fifty candidates on constrained CPU costs a few hundred milliseconds.
  Mitigation, each item bounded rather than left as an activity: candidate truncation **at the
  model's declared sequence limit with the truncated fraction published** (FR-019); batching **at a
  fixed shape equal to the reranked count, the same shape warm-up runs at** (FR-017), so batch size
  is not a per-run knob; an explicit thread configuration with a derivation rule (FR-038); and a
  degraded mode that is specified rather than improvised. The threshold all of them are held to is
  FR-033's budgets — a mitigation with no threshold is an activity, and an activity cannot fail.
- **The frozen set is too small to resolve the differences being published** *(likelihood: high,
  impact: medium)*: fifty queries cannot separate arms that differ by a few points. Mitigation:
  intervals published with every figure and an explicit unresolvable verdict, so a difference is
  never claimed that the set cannot support.

## Implementation Signals *(mandatory)*

- `NEW-API` — a retrieval surface consumed by E011 and E014, returning ranked passages with
  their provenance, the arm that produced them, and the degraded flag.
- `NEW-CONFIG` — the exact-versus-approximate flag, the index search-breadth setting, the
  fusion constant and fetch depth, and explicit reranker thread counts, since the runtime cannot
  see the container's CPU allocation.
- `EXTERNAL-SERVICE` — a vendored cross-encoder artifact committed to the repository with its
  tokenizer and digests, loaded at startup. No network dependency; the tag marks a new
  third-party artifact under version control.
- `NEW-WORKER` — startup loading and warm-up gating readiness, and a single worker process,
  because each worker would load its own copy of the model.

Deliberately absent: `MIGRATION`. This epic adds no schema. The full-text and vector indexes it
reads already exist.

## Success Criteria *(mandatory)*

### Measurable Outcomes

**Which set these are measured on.** E014 owns the frozen evaluation set and the act of
publishing; E008 owns the capability and emits the figures. Every criterion below is therefore
evaluable at this epic's own gate against a query set of its own choosing, and becomes a
*published* figure only when E014 runs it against the frozen set. A criterion that could only be
checked inside another epic would leave this one unverifiable — the failure the Problem
Statement names, applied to its own gate.

- **SC-001** [US1]: Recall on the top five results reaches at least 0.85 as a point estimate,
  emitted with a Wilson 95% interval beside it. Read against the point estimate. Measured at
  this epic's gate on its own query set; measured for publication by E014 on the frozen set.
- **SC-002** [US1]: Mean reciprocal rank reaches at least 0.70, emitted with a percentile
  bootstrap 95% interval over the query set — never a Wilson interval, which does not exist for
  a statistic that is not a proportion (FR-031, FR-034).
- **SC-003** [US1]: 100% of returned results carry the document and page recorded on the chunk
  they were drawn from — identity with the stored value, not merely a page that exists — over
  every result returned, not a sample.
- **SC-004** [US1]: Zero ranked results are produced by arithmetic outside the deterministic
  computation boundary, over every query in the evaluation set.
- **SC-005** [US2] *(amended 2026-07-29)*: 100% of part numbers present in the corpus are returned
  by a direct query for them, over the enumerated set of part numbers the corpus prints — the
  enumeration taken from the source FR-010 now names and `plan.md` AD-009 fixes, the generator's
  pre-render document model, so the population this percentage is measured over is identified rather
  than assumed to exist.
- **SC-006** [US2]: Zero queries return fewer results with the deterministic route enabled than
  with it disabled, over the full evaluation set — the route never removes a result.
- **SC-007** [US3] *(amended 2026-07-29)*: The service reports ready only after reranker warm-up
  completes at the shape FR-017 fixes numerically, and the first query after readiness incurs no load
  cost. **"No load cost" is decided structurally rather than by a latency comparison**: zero artifact
  reads, zero session creations and zero warm-up invocations occur on any request path after
  readiness is reported, adjudicated by a counter over those three events that must read zero at the
  end of a run. A latency-difference rule was rejected as the decision rule because a first query
  differs from a second for reasons other than loading — page-in, cache state, planner warm-up — so
  no threshold on that difference separates a query that paid a load cost from one that did not.
- **SC-008** [US3]: The reranked ordering is reported against the strongest single arm as well
  as against fusion-only, each with intervals and paired per-query differences, with
  fusion-only labelled as the weak comparator; a difference the set size cannot resolve is
  reported as unresolved rather than as a win.
- **SC-009** [US3]: The fraction of candidates truncated at the reranker's sequence limit is
  reported with the candidate-length distribution, over the enumerated candidate set rather
  than a sample, so the figure is a census and needs no interval.
- **SC-010** [US4] *(amended 2026-07-29)*: 100% of responses whose ordering is fusion-only **because
  the reranker was unavailable** carry the fusion-only statement and the degraded flag; 100% of
  responses produced without reranking for any reason carry a machine-readable reason they were not
  reranked; and zero evaluation outputs omit the mode they ran in. **The first population is narrower
  than "every unreranked response", and deliberately** (FR-022): a `lexical` or `dense` response is
  unreranked and is not fusion-only, and an empty result had nothing to score and degraded nothing, so
  the previous wording made this criterion unsatisfiable except by making a false statement about those
  responses. The fusion-only claim is measured over the degraded population; the unreranked *fact* is
  measured over all of them.
- **SC-011** [US4] *(amended 2026-07-29)*: The degraded path is exercised by an automated test that
  forces load failure **at the artifact-loading boundary**, and the test asserts all three
  observables FR-024 enumerates: ready-degraded at the readiness endpoint, the fusion-only statement
  in a response body that still carries results, and the mode recorded on the evaluation-facing
  output. A test that sets the degraded flag directly does not satisfy this criterion.
- **SC-012** [US5] *(amended 2026-07-29)*: All six arms — lexical, dense, fused, reranked, reranked
  at full precision, and approximate — return results independently, and each returns identical
  results across two runs on an unchanged corpus **and an unrebuilt index**. The index qualifier is
  stated because a rebuild leaves the corpus unchanged while changing the approximate arm's graph:
  for the approximate arm the two runs are taken against the same built index, cross-rebuild
  stability being out of scope for that arm under FR-020's narrowing, while on the five exact-path
  arms the two runs may straddle a rebuild.
- **SC-013** [US6] *(amended 2026-07-29)*: Zero behavioural differences other than vector-search
  strategy are observable between the two flag settings, over the full evaluation query set, where
  "behavioural difference" ranges over the observable set **FR-026 enumerates** and the comparison is
  constructed across **two differently configured processes** (`plan.md` AD-006) — the only shape in
  which the two settings can be observed at once, since the flag is service configuration.
- **SC-014** [US6]: Zero queries return fewer candidates than requested where that many matching
  rows exist, including filtered queries, on either path — so no recall is lost to a retrieval
  setting rather than to the ranking design.
- **SC-015** [US1] *(amended 2026-07-29; extended at Analyze the same day)*: **All six blocking
  amendments** have landed on the default branch before implementation begins — FR-034 against
  `specs/prd.md`, FR-035 against `specs/project-plan.md`, the two the Plan phase added and gated
  (`plan.md` §Pending Amendments): item 3, `project-instructions.md` §Source Code Layout **and**
  §Testing & Quality Policy, without which ADR-0022's placement of inference in the gateway
  contradicts two governing clauses, and item 4, `specs/sad.md`'s ADR catalog row for ADR-0022 — and
  the two Analyze added: item 9 (FR-048) against `specs/sad.md`'s Wilson-on-MRR row, and item 10
  (FR-047) against §Technology Stack's INT8 qualifier. **The verifier is named and the gate is
  decidable at this epic's own boundary**: each is checked by reading the default branch for the
  revision that performed it, cited in the task that closes it, so this criterion is adjudicated here
  rather than asserted about another branch. Until items 1 and 9 land, two registered documents and
  FR-031 disagree, and Governance holds the registered document wins.
- **SC-016** [US3] *(amended 2026-07-29)*: Reranking latency and the resident memory of every model
  session the serving process holds are measured exactly as FR-033 prescribes and **fall within** the
  budgets `specs/sad.md` declares — resident set size **≤ 400 MB** for the serving container
  (§Quality Attributes "Compute envelope") and the reranking latency budget of §Technical Context
  "Performance Goals". **The aggregation window is fixed**: one latency observation per query over
  every query in the evaluation set, and one memory reading per run, so both are a **census over an
  enumerated population and need no interval** — the same convention SC-009 applies to the
  enumerated candidate set — and both carry the corpus size the run was measured at. Publishing an
  overage discharges FR-033's disclosure obligation and **does not satisfy this criterion**: a run
  exceeding a budget fails it and says so, because Publish-the-Miss makes a miss visible rather than
  making it a pass. **Both halves are adjudicable**: memory at ≤ 400 MB, and latency as a
  **never-exceed 400 ms** — no query in an evaluation run may exceed it, a single observation
  falsifies it, and 150 ms is a reported expectation rather than part of the requirement. That
  statistic was settled at §Decisions Taken at Checklist; `specs/sad.md` owns the number and states no
  statistic for it, so amendment 7 (`plan.md` §Pending Amendments) remains open as a request that the
  registered document **record** what this epic measures against. It does not gate this criterion.

## Glossary *(include when spec introduces 2+ domain-specific terms)*

| Term | Definition |
|------|------------|
| Arm | One retrieval strategy contributing a ranked candidate list — here the lexical arm and the dense vector arm. |
| Lexical arm | Retrieval by matching the query's words against the chunk store's field-weighted full-text column. Uses no corpus-wide term statistics and is **not** BM25. |
| Dense arm | Retrieval by nearest-neighbour search over chunk embeddings produced by the pinned encoder. |
| Reciprocal rank fusion | Combining ranked lists by summing a decreasing function of each candidate's rank in each list, using rank alone and ignoring scores. |
| Fetch depth | The number of candidates taken from each arm before fusion. Fixed at 50 and part of the ranking definition. |
| Cross-encoder | A model scoring a query and a candidate passage together in one pass, rather than comparing two independently produced representations. |
| Deterministic route | The pre-fusion path that resolves part-number-shaped queries by direct lookup and falls through to hybrid retrieval on a miss. |
| Degraded mode | Serving fusion-only ordering because the reranker is unavailable, with the state declared in the response and at readiness. |
| Exact path | Vector search scanning every chunk embedding. Used for evaluation so published figures carry no index variance. |
| Approximate path | Vector search through the tuned graph index. Used for serving. |
| Ablation arm | One configuration measured and published separately so its individual contribution is visible rather than assumed. |

## Compliance Check

**Audited against**: `project-instructions.md` **v1.2.8** (last amended 2026-07-29) · **Audit
date**: 2026-07-29 · **Verdict**: PASS after remediation, with two conditions that cannot be
cleared on this branch.

| Principle / Section | Verdict | Where |
|---|---|---|
| I. Traceable or It Does Not Ship | PASS (repaired at audit) | FR-008 forbids a synthesised page; SC-003 now asserts **identity** with the value stored on the chunk, not merely that the page exists — an existing-but-wrong page previously passed |
| II. Uncertainty Is the Product | PASS (repaired at audit) | SC-001, SC-002, SC-008 carry intervals; SC-009 was the one bare figure and is now stated as a census over the enumerated candidate set |
| III. Precision Over Recall Where a Mistake Is Silent | PASS | FR-009 refuses to pad, FR-011/FR-012 make the route additive, FR-007 refuses on encoder-identity mismatch |
| IV. Agent Output Style | PASS | Mandatory sections only |
| V. The Model Extracts, Code Computes | PASS, with a boundary the plan must settle | FR-002 and SC-004 are testable against the computation-boundary contract. **Open**: whether sorting by reranker scores sits inside or outside that contract's scope. The plan must state it, or the architecture test will be quietly weakened to pass |
| VI. Evaluate Before You Tune | PASS (repaired at audit) | FR-004 previously required the fusion constant be *published* while leaving it free, with targets measured on a frozen set — the tune-to-the-test-set path this principle exists to close. It is now fixed before measurement, changeable only by a recorded decision and a re-measurement, as FR-003 already treated the fetch depth |
| VII. Publish the Miss | PASS (repaired at audit) | The shortfall half was always strong — FR-032's unresolvable verdict, the sparse-only arm as a first-class row, FR-019's truncation disclosure, the whole US4 degraded surface. The **format** was honoured by none of the three limitations; all three now carry a reversal trigger and a production-scale alternative |
| VIII. Honest Opponents | PASS (repaired at audit) | The reranker's only comparator was fusion-only ordering, which this spec itself calls nearly unordered — beating it is close to guaranteed. FR-036 now requires reporting against the **strongest single arm** and labelling fusion-only as weak. The full-precision reranking arm {SAD:ADR-0006} obliges was absent from FR-025 and SC-012 and is now required runnable |
| Technology Stack | PASS | Postgres 16 with `pgvector` and `tsvector`, one instance; ONNX Runtime; no network at query time |
| Testing & Quality Policy | PASS, with a plan obligation | Fusion ranking is named for mandatory property-based tests, and FR-002 pushes all ranking into one statement, leaving no pure function in application code. `specs/sad.md` §302 prescribes the compensating shape — property tests over pure scoring functions plus fixed-input regression tests over the fusion query — and the plan must carry it |
| Source Code Layout | PASS, with a plan obligation | The vendored reranker artifact has no location named. `ENFORCE_SRC_ROOT` grants one exception and a binary fits neither it nor `/src` cleanly; the plan must place it |
| Development Workflow | PASS | Branch `00008-hybrid-retrieval-and-reranking` matches the workspace, which matches epic E008 |
| Data Provenance | PASS (repaired at audit) | FR-016 recorded identity and digest but no **license basis or source** for a third-party binary entering the repository. Both are now required |
| Governance | **CONDITIONAL — two items cannot clear on this branch** | See below |

**The two conditions.**

1. **`specs/prd.md` and this spec disagree today.** The PRD's retrieval row specifies a Wilson
   95% interval on mean reciprocal rank; FR-031 forbids one, because MRR is a mean of reciprocal
   ranks and no Wilson interval exists for it. Governance holds that the registered document wins
   where a downstream artifact conflicts — so this spec is *not* the resolution, the amendment
   is. FR-034 records it, SC-015 gates implementation on it landing. Correcting this spec instead
   would mean publishing an invalid statistic, which is the one outcome neither document wants.
   This is the same reasoning E006 applied to F1 and is recorded in `research.md`.

2. **The amendment queue is unsequenced.** Governance permits one amendment in flight at a time.
   E006 already raised at least one that has not landed — `project-instructions.md` still reads
   "ONNX Runtime for INT8 CPU inference" while E006 shipped FP32 — and E008 adds two more
   (FR-034, FR-035) without establishing its position behind them. Recorded rather than resolved:
   the ordering is the default branch's to decide, not a feature branch's.

**Remediation history.** Spec Validator returned FAIL at 19/25 and the Policy Auditor returned
FAIL with 4 CRITICAL and 3 HIGH. Both were remediated in one pass before this record was written.
The two findings worth carrying forward, because they were authoring errors rather than omissions:
FR-018 originally forbade increasing the candidate count *"as a remedy for poor recall"*, which
constrains a developer's motive and can never be tested; and FR-031 pointed at an Assumptions
entry that did not exist, so a conflict with a registered document was asserted in one place and
documented in none.

**Shared-document baseline updates: recorded, not performed.** The Specify workflow's
shared-document step instructs an epic to merge general-interest findings into the managed
`## Project Context Baseline Updates` sections of `specs/prd.md` and `specs/sad.md`. Both are
registered documents, and Governance serializes amendments to them onto the default branch. The
repository's own history agrees: every commit touching `specs/prd.md` landed on `main` as its own
`docs(...)` change, none from a feature branch. The workflow step and the governing clause
disagree, and the clause wins. Two entries are therefore proposed and left unwritten:

- For `specs/sad.md` — the lexical retrieval arm uses PostgreSQL's native `tsvector` ranking,
  which consults no corpus-wide term statistics. It has no inverse document frequency and is
  **not** BM25; published BM25 benchmark results do not transfer to it, and no figure produced
  by this project may imply they do. This bears on every future consumer of the chunk store, not
  only on E008.
- For `specs/prd.md` — a published proportion target is read against the point estimate with its
  interval printed beside it, not against the interval's lower bound, at the evaluation set sizes
  this project uses. Recorded as a measurement convention because it applies to every published
  proportion, not only to retrieval recall.

## Decisions Taken at Checklist

Four decisions the three quality checklists surfaced and could not make from the artifacts. Each is
recorded here with its reasoning, and the requirement or criterion it settles is amended in place
above. Dated 2026-07-29.

**The reranking latency budget is a never-exceed, not a percentile** (settles FR-033 and SC-016).
`specs/sad.md` states "150–400 ms" and names no statistic, and the same sentence states the worklist
and grounded-answer goals as p95 — so a p95 reading would be inferred from neighbours rather than
stated, with a pass/fail consequence. No query in an evaluation run may exceed **400 ms**; a single
observation falsifies it. 150 ms is a reported expectation, not part of the requirement. Chosen over
p95 because a p95 across a 50-query set is decided by its two or three worst observations, which is
a weak gate that reads like a strong one. `specs/sad.md` owns the number and is unchanged; amendment
7 asks it to record the statistic.

**Relevance judgements come from the generator's pre-render document model, and the ceiling is
published** (settles SC-001, extends AD-009). Every query is answerable by construction, so recall
measured this way is an **upper bound on real-world performance, not an estimate of it**, and must be
published as such rather than as a recall figure that happens to be high. Chosen over hand-labelling,
which is unreproducible without the labeller, and over pooled judgements, which still need a judge
and must be rebuilt whenever an arm is added. Recorded in the four-part limitation format below.

- **Scope decision**: recall and mean reciprocal rank are measured against queries derived from the
  generator's record of what each document prints.
- **Supporting evidence**: the corpus is synthetic and its pre-render model is committed, seeded and
  digest-verified, so the judgements are reproducible from a clean checkout with no labeller.
- **Reversal trigger**: any query set drawn from real coordinator questions — at which point the
  generator-derived figure becomes the ceiling that the real figure is reported against.
- **Production-scale alternative**: pooled judgements across arms, judged by a domain reader, which
  is the standard remedy for judgements biased toward the system that produced them.

**The evaluation set may be measured against without limit; tuning after measurement is what is
disciplined** (settles AD-010's freeze rule). A digest detects modification and cannot detect
repeated measurement, and no artifact in this repository can count runs across machines and branches
— so a run budget would be a rule enforced by memory. Instead: any change to a ranking parameter
after a measurement is recorded as a decision, the set is re-measured, and **both figures are
published**. Chosen over a split set because the spec's own risk already records that fifty queries
cannot separate arms differing by a few points, and halving it would make every comparison
unresolvable. Obliged by FR-043; recorded in the four-part limitation format below.

- **Scope decision**: the frozen evaluation set carries a digest and no run budget, and the
  discipline it enforces is disclosure of tuning rather than restraint in measuring.
- **Supporting evidence**: a digest is a modification detector and detects nothing about how often
  the set is read; no artifact in this repository counts runs across machines and branches, so a run
  budget here would be a rule enforced by memory and unfalsifiable at review.
- **Reversal trigger**: any evaluation figure published outside this repository, or any query set
  drawn from real coordinator questions — at which point overfitting stops being a local concern and
  a held-out split is worth the loss of statistical power.
- **Production-scale alternative**: a train/dev/test split with the test partition released only at
  a decision point, plus a run counter held by the evaluation service rather than the repository.
  That is the standard remedy and it needs a set large enough to survive being divided, which fifty
  queries is not.

**A partially loaded reranker reports ready, naming the arm that failed** (settles FR-021 and the
readiness contract). With two graphs, one loading while the other fails is reachable. The serving
path uses the quantized graph; the full-precision graph exists to measure what quantization costs.
So a process serving the quantized arm is **not degraded** — pulling it from service would trade a
working product for a missing measurement. Readiness reports `ready` with per-session detail naming
the failed graph, `mode.degraded` stays false for requests an available arm serves, and a request
for the unavailable arm is **refused explicitly** rather than silently served by the other. The
distinction matters because an evaluation that silently fell back would put a quantized figure in a
full-precision row.
