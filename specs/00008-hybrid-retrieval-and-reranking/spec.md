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
- **FR-004**: System MUST fix the fusion constant, the tie-break key applied when two
  candidates fuse to the same score, and the convention for a candidate appearing in only one
  arm's list **before any figure is measured against the evaluation set**, and MUST record all
  three alongside every result. The fusion formula fixes none of them and each changes the
  output, so a constant left free while a target is measured on a frozen set is the
  tune-to-the-test-set path Principle VI exists to close. Changing any of the three after
  measurement MUST be recorded as a decision and re-measured, exactly as FR-003 treats the
  fetch depth.
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
  rather than a claim.
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
- **FR-016**: System MUST load the reranker from an artifact committed to the repository, with
  a recorded identity, revision, **license basis and source**, and a digest verified before the
  session is created, reaching no network at run time. License basis is required of every
  artifact entering this repository, and a vendored third-party model is no exception.
- **FR-017**: System MUST load the reranker once per process, warm it at the maximum batch
  shape, and withhold readiness until warm-up completes.
- **FR-018**: System MUST rerank exactly the fused candidate set FR-003 defines, and the
  reranked count MUST equal the fetch depth. Any change to that count MUST be accompanied by a
  re-measured ablation showing its effect, because reranking quality declines past a depth
  limit and a larger candidate set can rank worse than a smaller one
  (`research.md` §Cross-encoder reranking).
- **FR-019**: System MUST publish the distribution of candidate lengths against the reranker's
  sequence limit and the fraction of candidates truncated, because a truncated candidate is
  scored on its prefix and that is invisible in the result.
- **FR-020**: System MUST return identical ordering for identical queries against an unchanged
  corpus and an unchanged configuration.
- **FR-033**: System MUST report reranking latency per query at the configured candidate count,
  measured on the constrained CPU allocation the serving container is given, and MUST report
  the reranker session's resident memory against the container's budget. A run exceeding either
  MUST publish the overage rather than omitting the figure.

**Degradation**

- **FR-021**: System MUST serve fusion-only ordering when the reranker is unavailable, and MUST
  report ready-degraded rather than not-ready.
- **FR-022**: System MUST state in every response produced without reranking that the result is
  fusion-only, and MUST expose the degraded state at the readiness endpoint.
- **FR-023**: System MUST record the mode a run executed in on any output an evaluation consumes,
  so no figure produced in degraded mode can be read as reranked.
- **FR-024**: System MUST exercise the degraded path in an automated test that forces reranker
  load failure and asserts both that the flag is set and that results are still returned.

**Measurability and configuration**

- **FR-025**: System MUST make each retrieval arm independently runnable, returning results
  without requiring the others: **lexical only, dense only, fused without reranking, fused
  and reranked, and fused and reranked at full precision**. The full-precision arm is
  required because {SAD:ADR-0006} obliges the quantized-versus-full-precision difference to
  be measured and published rather than assumed negligible, and no other epic can construct
  an arm this epic did not make runnable.
- **FR-036**: System MUST report the reranked ordering against **the strongest single arm**,
  not only against fusion-only ordering. Fusion at the fixed fetch depth is nearly unordered
  within its window by construction, so a comparison drawn only against it is close to
  guaranteed to succeed and carries little information. Where fusion-only is reported it MUST
  be labelled as the weak comparator it is.
- **FR-026**: System MUST expose a configuration flag selecting exact or approximate vector
  search, and that flag MUST control index usage only. Filters, fusion, fetch depth and
  reranking MUST be shared by both paths, and that sharing MUST be asserted by a test rather
  than left to review.
- **FR-027**: System MUST set the approximate index's search breadth to at least the fetch
  depth. The default breadth is below the fetch depth and silently reduces recall
  (`{SAD:ADR-0005}`).
- **FR-028**: System MUST return the requested candidate count from a filtered vector search
  wherever that many matching rows exist, rather than the smaller set the index returns when it
  filters after selecting candidates.
- **FR-029**: System MUST record, with any result an evaluation consumes, the ranking parameters
  in force — fusion constant, tie-break key, missing-arm convention, fetch depth, and the index
  settings where the index was used.
**Measurement boundary.** E008 **computes and emits** the figures below; E014 owns the frozen
evaluation set, the results manifest, and the act of publishing. The requirements are worded as
emission so that E008 is testable inside its own boundary and E014's harness has something
well-defined to consume.

- **FR-030**: System MUST emit recall on the top five results as a proportion, together with a
  Wilson 95% interval, over whichever query set it is run against. The target is read against
  the point estimate, not the interval bound.
- **FR-031**: System MUST NOT emit a Wilson interval on mean reciprocal rank. Mean reciprocal
  rank is a mean of reciprocal ranks, not a binomial proportion, so no Wilson interval exists
  for it; the interval MUST be a percentile bootstrap over the query set. **`specs/prd.md`
  currently specifies a Wilson interval for this statistic** — see FR-034 and SC-015.
- **FR-032**: System MUST emit a verdict of "not resolvable at this set size" where two arms'
  intervals overlap, rather than reporting the larger point estimate as a win.

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
  evaluation path.

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
  model session is the dominant memory line item in the serving container and reranking fifty
  candidates on constrained CPU costs a few hundred milliseconds. Mitigation: candidate
  truncation and batching, an explicit thread configuration, and a degraded mode that is
  specified rather than improvised.
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
- **SC-005** [US2]: 100% of part numbers present in the corpus are returned by a direct query
  for them, over the enumerated set of part numbers the corpus prints.
- **SC-006** [US2]: Zero queries return fewer results with the deterministic route enabled than
  with it disabled, over the full evaluation set — the route never removes a result.
- **SC-007** [US3]: The service reports ready only after reranker warm-up completes, and the
  first query after readiness incurs no load cost.
- **SC-008** [US3]: The reranked ordering is reported against the strongest single arm as well
  as against fusion-only, each with intervals and paired per-query differences, with
  fusion-only labelled as the weak comparator; a difference the set size cannot resolve is
  reported as unresolved rather than as a win.
- **SC-009** [US3]: The fraction of candidates truncated at the reranker's sequence limit is
  reported with the candidate-length distribution, over the enumerated candidate set rather
  than a sample, so the figure is a census and needs no interval.
- **SC-010** [US4]: 100% of responses produced without reranking carry the fusion-only flag, and
  zero evaluation outputs omit the mode they ran in.
- **SC-011** [US4]: The degraded path is exercised by an automated test that forces load failure;
  the test asserts both that the flag is set and that results are returned.
- **SC-012** [US5]: All six arms — lexical, dense, fused, reranked, reranked at full
  precision, and approximate — return results independently, and each returns identical
  results across two runs on an unchanged corpus.
- **SC-013** [US6]: Zero behavioural differences other than vector-search strategy are observable
  between the two flag settings, over the full evaluation query set.
- **SC-014** [US6]: Zero queries return fewer candidates than requested where that many matching
  rows exist, including filtered queries, on either path — so no recall is lost to a retrieval
  setting rather than to the ranking design.
- **SC-015** [US1]: Both amendments this epic records — FR-034 against `specs/prd.md` and FR-035
  against `specs/project-plan.md` — have landed on the default branch before implementation
  begins, verified by the amending revision. Until then a registered document and FR-031
  disagree, and Governance holds the registered document wins.
- **SC-016** [US3]: Reranking latency per query and the reranker session's resident memory are
  both reported against the declared budgets on the constrained CPU allocation, over the full
  evaluation set; an overage is published rather than omitted.

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
