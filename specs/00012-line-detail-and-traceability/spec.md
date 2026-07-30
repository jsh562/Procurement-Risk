---
feature_branch: "00012-line-detail-and-traceability"
created: "2026-07-30"
input: "E012 — Build the single-line detail view with a plotted posterior distribution and navigation back to every originating source document page."
spec_type: "product"
spec_maturity: "draft"
epic_id: "E012"
epic_sources: "{PRD:CAP-007}"
---

# Feature Specification: Line Detail and Traceability

**Feature Branch**: `00012-line-detail-and-traceability`
**Created**: 2026-07-30
**Status**: Draft
**Spec Type**: product
**Spec Maturity**: draft
**Epic ID**: E012
**Epic Sources**: {PRD:CAP-007}
**Product Document**: `specs/prd.md`

## Problem Statement *(mandatory)*

The worklist tells a coordinator which lines are most at risk and gives three inputs behind each position, but it deliberately withholds the distribution those figures were read from — the ranking arrives as a position, and the quantile pair as two labelled numbers. A coordinator who wants to know how bad the tail is, or which document said this part number, has nowhere to go: the draws never leave the serving boundary and no surface links a line to the page a value was extracted from. Without this, the product asks to be trusted on figures whose derivation is unreachable, which is the opposite of what a traceability-first system is for.

## Scope *(mandatory)*

### Included

- A detail view for one purchase-order line, reachable from that line's identity on the worklist.
- The line's posterior predictive delivery distribution, plotted, with the need-by date marked and the mass beyond it both shaded and stated.
- A secondary cumulative view of the stored day grid, with the mass beyond the horizon named rather than implied.
- A structured textual equivalent of both figures, available to every reader rather than to assistive technology alone.
- Navigation from each linked record to the originating document page, with that page displayed rather than merely cited.
- An explicit rendering for a line whose cross-document identity has not been resolved.
- The covariates the forecast conditioned on for this line, presented as inputs rather than as causes.

### Excluded

- **Editing anything.** The view is read-only; criticality override is E017's and need-by adjustment is E010's session what-if.
- **Retrieval or chat over the source documents** — E008 owns retrieval and E011 owns grounded answering; this feature navigates to a page, it does not search for one.
- **Populating cross-document identity** — E009 owns resolution; this feature reads what E009 produces and states plainly when there is nothing to read.
- **Application navigation chrome** — the shell is established in E010 and read-only thereafter; this feature adds one route, not a navigation system.
- **A second copy of any figure the worklist already publishes** — the detail view reads the same artifact rather than recomputing, so the two surfaces cannot disagree.

### Edge Cases & Boundaries

- A line with no posterior in the active run — not covered, roster mismatch, or no active run at all — has nothing to plot, and must say which of those it is.
- A line whose need-by date precedes the run's anchor has no readable miss probability, exactly as on the worklist; the distribution is still shown, because how much further slip is coming is the open question.
- A line whose need-by date falls beyond the modelled horizon has only an upper bound on the miss mass, and the bound must be marked as a bound.
- A line with a resolved identity but no linked documents is a different state from one with no resolved identity, and the two must not render alike.
- A displayed covariate value that cannot be reconciled against the value the fit used must be withheld rather than shown.
- A document page that cannot be resolved to a readable source must fail visibly rather than render an empty frame.
- A line identifier that does not exist, or exists but is closed, must produce a stated outcome rather than a blank view.

## User Scenarios & Testing *(mandatory for product specs only)*

### User Story 1 - Inspect the whole distribution behind a line (Priority: P1)

A coordinator looking at a high-ranking line opens its detail view and sees the full spread of modelled delivery outcomes rather than a summary of them. They can see where the bulk of outcomes fall, how long the tail runs, where their need-by date sits against that spread, and how much of the distribution lies past it. Nothing on the view offers a single answer to "when will it arrive".

**Why this priority**: Core value proposition — the epic exists to make the distribution behind a risk figure inspectable, and every other story on this view is context around it.

**Independent Test**: Open the detail view for a line with a posterior and confirm the distribution renders, the need-by mark and its mass are both shown, and no mean, mode or lone quantile appears anywhere.

**Acceptance Scenarios**:

1. **Given** a line covered by the active run, **When** its detail view is opened, **Then** the delivery distribution is plotted as fifty equal-probability marks over days counted from the run's as-of date, and the same quantile pair the worklist shows is labelled on it with its reference class.
2. **Given** that view, **When** the reader looks for a central estimate, **Then** none is present — no mean, no mode, no expected value, and no quantile drawn without the member that pairs with it.
3. **Given** a line whose need-by date falls inside the distribution, **When** the view renders, **Then** the need-by position is marked, the mass beyond it is shaded, and that mass is also stated as a frequency in both directions.
4. **Given** a line whose need-by date lies beyond the modelled horizon, **When** the view renders, **Then** the miss figure is presented as an upper bound and labelled as one.
5. **Given** a line with no posterior in the active run, **When** the view is opened, **Then** it names which of the three conditions removed the figures rather than rendering an empty plot or an error.
6. **Given** any line with a posterior, **When** the cumulative view renders, **Then** it shows delivery by day as an increasing quantity and names the share of outcomes falling beyond the modelled horizon.
7. **Given** a coordinator using the textual equivalent instead of the figures, **When** they read it, **Then** they obtain the reference class, the labelled quantile pair, the miss mass in both directions, the residual mass, and the banded summary of the day grid, and no single value is offered as the answer.

### User Story 2 - Read the source page behind a linked record (Priority: P1)

A coordinator who does not believe a figure follows the citation to the page it came from and reads the surrounding text themselves. Each linked specification, submittal or purchase-order record names its document and page, and opening it shows that page.

**Why this priority**: Core value proposition — this is the "Traceability" half of the epic, and the product document makes reaching the originating page the capability CAP-007 promises.

**Independent Test**: From a line with linked records, open one and confirm the originating document page is displayed and identified by document title and page number.

**Acceptance Scenarios**:

1. **Given** a line with at least one linked record, **When** the detail view renders, **Then** each linked record is listed with its document title, its page number, the extracted span it contributed, and the confidence recorded for it.
2. **Given** such a record, **When** the coordinator opens it, **Then** the originating document page is displayed rather than the document as a whole.
3. **Given** a record drawn from a synthesized document, **When** it is listed or opened, **Then** it is marked as synthetic rather than presented indistinguishably from a real published specification.
4. **Given** a document page that cannot be resolved to a readable source, **When** the coordinator opens it, **Then** the failure is stated with its cause rather than rendering an empty frame.

### User Story 3 - Be told when a line's identity has not been resolved (Priority: P1)

A coordinator opening a line whose records have never been linked across documents sees that stated plainly, with what it means for the rest of the view, rather than an empty panel they must interpret.

**Why this priority**: Core value proposition — it is the honest state of every line until identity resolution runs, and an empty section meaning "not run yet" is indistinguishable from one meaning "nothing to show", which is the confusion Principle III exists to prevent.

**Independent Test**: Open a line with no resolved identity and confirm the view names that condition in text rather than showing an empty traceability section.

**Acceptance Scenarios**:

1. **Given** a line that belongs to no resolved entity, **When** the detail view renders, **Then** it states that cross-document identity has not been resolved for this line and what that withholds.
2. **Given** a line that belongs to a resolved entity carrying no document-derived members, **When** the view renders, **Then** it states that outcome in different words from the unresolved case.
3. **Given** either state, **When** the view renders, **Then** the distribution and the line's own record are still shown, because neither depends on identity resolution.
4. **Given** a line identifier that names no line, or names a closed one, **When** the view is opened, **Then** it states that outcome rather than rendering an empty view.

### User Story 4 - See which covariates the forecast conditioned on (Priority: P1)

A coordinator who wants to know why this line looks worse than a similar one sees the covariates the forecast used for it, with their observed values, stated as things associated with slower delivery rather than as causes of it.

**Why this priority**: Core value proposition — the registered epic lists the covariate display among its four acceptance criteria without qualification, and the product document's capability is that a coordinator can inspect *why* a line is flagged, which this is the only surface for.

**Independent Test**: Open a line and confirm the covariates the fit conditioned on and their observed values are shown, with no contribution figure and no causal claim.

**Acceptance Scenarios**:

1. **Given** a line covered by the active run, **When** the detail view renders, **Then** the covariates the fit conditioned on are named with the values observed for this line at the run's anchor.
2. **Given** that panel, **When** the reader looks for how much each covariate contributed, **Then** no per-covariate share, weight or percentage is present.
3. **Given** that panel, **When** it describes what a covariate means, **Then** it states association with slower or faster delivery in comparable orders, never that changing it would change the date.
4. **Given** a covariate value that cannot be reconciled against the value the fit used, **When** the view renders, **Then** that value is withheld under a named state rather than displayed.

## Requirements *(mandatory)*

### Functional Requirements

**The distribution**

- **FR-001**: The system MUST plot the line's posterior predictive delivery distribution as **fifty** equal-probability marks, each standing for two in one hundred comparable orders, with that share stated in words beside the figure. Discrete marks rather than a continuous density because a reader can count them and check a stated probability against the picture; a density supports no counting and must be taken on faith. Fifty is the largest count for which comprehension evidence exists — it is not extrapolated to a general rule.
- **FR-002**: The number of marks MUST NOT vary with the line, the run, or the stored draw count. A figure whose denominator moves between lines cannot be compared between them.
- **FR-003**: The system MUST NOT render any central summary of the distribution — no mean, mode, expected value, or single quantile displayed without the member that pairs with it. A drawn central mark becomes the most salient feature of the figure and therefore the one the reader answers from, which is the single-date reading this product refuses.
- **FR-004**: The system MUST label the same two quantiles the worklist publishes for that line, each carrying its reference class and complementary frequency, with the reference class named adjacent to each frequency rather than once for the figure.
- **FR-005**: The system MUST mark the line's effective need-by date against the distribution, shade the mass beyond it, AND state that mass as a frequency in both directions. Neither alone is sufficient: the shaded region is what makes the stated figure checkable, and the stated figure is what survives a reader who does not interpret shading.
- **FR-006**: The system MUST present the miss mass as an upper bound, labelled as a bound, where the need-by date falls beyond the modelled horizon, and MUST withhold it where the need-by date is at or before the run's anchor — matching the worklist's treatment of the same conditions rather than deciding them again.
- **FR-007**: The system MUST render the stored day grid as a cumulative view of delivery by day, placed after the distribution in reading order and never the only encoding present. Lay comprehension of a decreasing survival curve is unresolved in the literature; the increasing complement carries half the reading load.
- **FR-008**: The system MUST publish the mass unresolved at the end of the horizon as a labelled quantity wherever the day grid is shown. A curve terminating above zero is read as terminating at zero.
- **FR-009**: The horizontal scale of both figures MUST be days counted from the run's as-of date. An absolute calendar date MUST NOT appear as an axis, an axis tick label, or a figure title. A calendar date MAY appear only as the label of the need-by mark FR-005 requires or attached to a labelled quantile FR-004 requires — both of which carry a stated proportion, which is what separates them from a bare date.
- **FR-010**: Every figure derived from the forecast artifact MUST derive from the same stored row, for the same active run, that the worklist's figures for that line derive from. The two surfaces must be incapable of disagreeing, which requires reading the same row rather than recomputing from the same source. Covariate values are not derived from that row and carry their own obligation under FR-031.
- **FR-011**: The system MUST NOT transmit the stored draw array or the stored day-grid array to the interface. The response carries the resolution the figures need — fifty marks and a banded cumulative series — and not the several thousand values behind them. This is a limit on resolution rather than a claim that what is withheld cannot be approximated: FR-012 states plainly what cannot be guaranteed here.
- **FR-012**: No member of the response backing this view MAY be a central summary of the distribution — a mean, a mode, an expected value, an expected overrun, or a quantile carried without the member that pairs with it. Stated as a prohibition on what the response *carries* rather than on what a determined consumer could reconstruct: fifty equal-probability marks are the distribution, and any faithful rendering of a distribution permits approximating its centre. Claiming otherwise would be a guarantee this feature cannot keep.
- **FR-013**: The system MUST identify the artifact set its figures were computed from — the active run's identifier, its model version, the schema version of its stored arrays, and the as-of date those figures are counted from — so a reader can tell which run produced what they are looking at.

**The accessible equivalent**

- **FR-014**: The system MUST provide a structured textual equivalent of both figures, carrying the reference class, the labelled quantile pair, the miss mass in both directions, the residual mass, and a banded summary of the day grid. Alternative text is not sufficient: it forces the reader to accept the author's reading with no ability to drill down.
- **FR-015**: The equivalent MUST be reachable by every reader without assistive technology, and MUST be rendered within the same region as the figures it describes rather than in a separate part of the view.
- **FR-016**: The day-grid bands in the equivalent MUST be bounded by the quantiles FR-004 labels, so that a reader using the equivalent obtains the same figures a reader using the plot obtains. Bands chosen on a calendar grid would make the two populations answer different questions.
- **FR-017**: The equivalent MUST NOT state any single value as the expected outcome. The textual path is where a point estimate re-enters unnoticed.

**Traceability**

- **FR-018**: The system MUST list, for each record linked to this line, the document it came from, the page it came from, the extracted span it contributed, and the per-field confidence recorded with it. Confidence travels with the citation because this is the surface on which a coordinator judges whether to believe an extracted value.
- **FR-019**: Each linked record MUST be labelled with its document title and page number rather than with a generic word such as "source". A citation whose label carries no information is one the reader cannot judge without opening it.
- **FR-020**: The system MUST display the originating document page itself, not a reference to it. A citation that raises confidence without being checkable earns trust it has not tested, and linking to a whole document transfers the verification cost back to the reader.
- **FR-021**: The system MUST read document chunks only through the active ingestion generation. A reader that does not filter receives the resident generation's rows with no run attribution and cannot distinguish no live generation from one, and both failures are silent.
- **FR-022**: The system MUST distinguish a record drawn from a synthesized document from one drawn from a real published document, wherever such a record is listed or its page displayed. The corpus carries both layers deliberately, and a synthesized submittal page is otherwise indistinguishable from a verbatim federal specification page.
- **FR-023**: The system MUST state a resolvable failure when a page cannot be produced, naming the cause, rather than rendering an empty or partial frame.
- **FR-024**: The system MUST publish the share of linked records whose page resolves and whose extracted span is present on that page. This figure is a **census** over every linked record the active run carries — every element observed, none sampled — so it MUST be published as an exact count with its denominator and MUST NOT carry an interval. The figure MUST carry alongside it the declaration that it has no interval and the licensed reason it draws from, and the artifact publishing it MUST enumerate the closed set those reasons come from — the set E009 already fixes, adopted by reference rather than minted again, since two independently enumerated sets of licensed reasons in one product would let a figure be licensed under one and not the other. A census published with no declaration is indistinguishable from an estimate whose interval was quietly dropped. A link that looks right is frequently not, and this feature's value rests entirely on the links being right.
- **FR-025**: The target for that share is **100%** — every linked record resolves to a page and its extracted span is present on that page — and it MUST be fixed before the first measurement is taken. A share below it MUST be published with its cause rather than reported without one. A rate published against no threshold cannot be missed and therefore evidences nothing, and a threshold named after the result it judges is the retroactive adjustment the published-miss rule exists to prevent. This is a display-time property distinct from the registered storage-time traceability metric, and stating it does not restate that one.

**Identity resolution states**

- **FR-026**: The system MUST treat "cross-document identity has not been resolved for this line" as a named state whose wording is committed as data rather than composed at render time, in the same shape as the worklist's committed degraded-state copy table (E010 FR-044).
- **FR-027**: The system MUST distinguish, in wording, a line belonging to no resolved entity from a line belonging to a resolved entity that carries no document-derived members. They are different facts and an identical rendering asserts they are the same one.
- **FR-028**: The system MUST continue to render the distribution and the line's own record in either identity state, because neither depends on identity resolution.
- **FR-029**: The system MUST NOT write to any identity-resolution record. This feature reads what resolution produces; producing it belongs to another epic.

**Covariates**

- **FR-030**: The system MUST name the covariates the active run's fit conditioned on, and show the value observed for this line at the run's anchor date for each.
- **FR-031**: A displayed covariate value MUST be reconciled against the value the fit used for that line, and a value that cannot be reconciled MUST be withheld under a named state rather than displayed. The values are not stored beside the posterior and are reconstructed from the line's lifecycle history, so a reconstruction that drifts produces a confident, specific, unfalsifiable-looking claim about a model's reasoning — which Principle III requires the system refuse rather than render.
- **FR-032**: The system MUST NOT display a covariate the active run does not record as having entered its fit.
- **FR-033**: The system MUST NOT publish a per-covariate contribution, share, weight or percentage. Attribution makes a model's correlations legible without making them causal, and both observed and unobserved confounding redistribute credit between correlated covariates.
- **FR-034**: The system MUST describe each covariate as associated with slower or faster delivery among comparable orders, and MUST NOT state or imply that changing it would move this line's dates.

**The view itself**

- **FR-035**: The detail view MUST be reachable from the corresponding line on the worklist, and the worklist's existing line identity MUST be the navigation target. Adding any new element to the row would introduce content outside the three classes the worklist's specification closes, which that specification counts as a further comparison quantity wherever it is rendered.
- **FR-036**: Every failure on this view MUST be identifiable and reportable by the reader who met it, carrying a correlation identifier they can quote and a cause drawn from a closed set, in the same form the serving boundary already establishes (E010 FR-043) rather than a new one.
- **FR-037**: The system MUST NOT render a single predicted delivery date for a line anywhere on this view — in a figure, an axis, a label, a tooltip, the textual equivalent, or the response the view renders from.
- **FR-038**: The view's **stated scalar figures** are closed, in five classes and no sixth:
  - *distribution* — the labelled quantile pair FR-004 requires, and the per-mark share FR-001 states;
  - *mass* — the miss mass in both directions FR-005 requires, the bound FR-006 requires, and the residual mass FR-008 requires;
  - *date* — the as-of date FR-013 identifies, and the effective need-by date FR-005 marks;
  - *evidence* — the census share and its denominator FR-024 publishes, and the per-field confidence FR-018 carries;
  - *covariate* — the observed values FR-030 shows.

  The day-grid bands FR-016 requires introduce no sixth class, because FR-016 bounds them by the quantiles FR-004 already labels. No combination of figures drawn from these five classes may yield either a bare delivery date or a bare central summary of the line's distribution. The as-of date added to a labelled quantile is permitted, because the result carries a stated proportion and is therefore neither bare. Anything scalar and stated that falls outside the five classes is a sixth, and introducing one reopens this check — which is what makes the check finite, rather than a claim about whatever the interface happens to render.
- **FR-039**: The distribution encoding MUST be excluded from FR-038's domain, and the exclusion is deliberate. Fifty equal-probability marks are the distribution, and counting to the twenty-fifth yields its middle — which is what plotting a distribution means; forbidding it would forbid publishing the distribution at all. FR-012 states why no rule here can forbid it. This feature reads Principle II as binding what the system states, labels, or carries as a field, and the encoding is outside FR-038's domain on that reading — a reading recorded here rather than asserted on the principle's behalf.
- **FR-040**: Every state this view can resolve to MUST be carried by text present in the accessibility tree. Colour, position, an icon with no accessible name, or the mere absence of a figure MUST NOT be the sole carrier of any of them.
- **FR-041**: A figure this view publishes that the worklist does not — the residual mass, the beyond-horizon bound, and the per-mark share — MUST take the same bounded form the worklist's percentage figures already take (E010 FR-008) rather than a new one: whole units, the extreme forms rendered as bounds, and the complement bounded with them, so that neither impossibility nor certainty is representable. Several thousand draws cannot evidence a certainty, and a figure this view invents is no better evidenced than one it inherits. These figures are the distribution communication Principle II's first sentence mandates rather than metrics reported about the system, so the interval-or-declaration obligation that governs a reported metric does not attach to them; the census share FR-024 publishes is a reported metric and does carry it.
- **FR-042**: The states this view can resolve to MUST be enumerated as closed sets at three stated scopes.
  - **Resolution** — exactly one of five, in this precedence: the absent-or-closed line FR-044 names; then a roster mismatch; then the line not covered by the active run; then no active run; then the state in which the figures render. Roster mismatch precedes not-covered because E010 FR-018a fixes that order and E010 FR-033 establishes the two as jointly constructible, so a precedence is required rather than optional; adopting E010's order by reference is what keeps the two surfaces from labelling one line differently, as FR-006 commits and Scope requires.
  - **Annotation** — zero or more of three, determined independently and only where the figures render: the stale-run state FR-045 names; the need-by date beyond the modelled horizon, under which FR-006 requires the miss mass labelled as a bound; and the need-by date at or before the run's anchor, under which FR-006 requires it withheld. These annotate rather than replace, which is why they cannot be members of the resolution set.
  - **Section-scoped** — exactly one per section that renders, each set carrying its own nominal member: for identity, the two states FR-027 distinguishes plus a resolved entity that does carry document-derived members; for covariates, the withheld state FR-031 names plus a value that reconciles; for pages, the unresolvable state FR-023 names plus a page that resolves.
  - Three scopes rather than one flat list because these genuinely co-occur — a stale run says nothing about whether identity resolved — and a single enumeration would have to either forbid the combination or leave undecided which member wins.
- **FR-043**: The wording of each state MUST differ from every other by a decidable property — no entry may be a substring of another, and each MUST carry a phrase occurring in no other. Distinctness asserted over committed copy is checkable; distinctness asserted over a rendered screen is evaluated against wording invented to pass it.
- **FR-044**: The system MUST state a named outcome for a line identifier that does not exist or that names a closed line, rather than rendering an empty view.
- **FR-045**: While the active run is stale, the view MUST mark its own figures as such rather than relying on a banner elsewhere in the product. A banner stops carrying once a line is read on its own, and this view is that case by construction.
- **FR-046**: The deterministic computations this feature introduces — mark allocation, quantile extraction, miss and residual mass derivation, and band alignment — MUST be developed test-first with property-based tests over their pure functions, and those tests MUST run in the merge gate. A check that does not run in the gate evidences nothing.

### Key Entities

- **PurchaseOrderLine**: The line under inspection — its identity, its recorded and effective need-by dates, its criticality, its lifecycle state, and the vendor and material category the fit pooled it under. Read-only here.
- **PosteriorDraws**: The stored per-line artifact for one run — the sorted draw vector, the day-grid array, and the mass beyond the horizon, together with the draw count and horizon the run declares. The source of every distribution figure on this view, and never transmitted whole.
- **ForecastRun**: The active run the artifact belongs to, carrying the as-of date every duration is counted from, the model version, the artifact schema version, and the set of covariates its fit conditioned on.
- **ResolvedEntity**: The sanctioned relationship between a purchase-order line and values extracted from documents. Produced by identity resolution; read here, never written.
- **ExtractedValue**: A value read out of a document, carrying the chunk it came from, the page it was cited to, and the confidence recorded for it.
- **Chunk**: A passage of a document with exactly one page, scoped to an ingestion generation.
- **Document**: The specification, submittal or record a chunk belongs to, carrying its title, its type, and whether its content is real or synthesized.

## Assumptions & Risks *(mandatory)*

### Assumptions

- The coordinator reading this view is not a statistician and will not supply a missing reference class from context.
- The corpus documents backing linked records remain available to the running system, and a document identifier can be resolved to the source it came from.
- The registered compute envelope governs this view as it governs the worklist — the request-serving container's steady-state memory ceiling and the one-shared-vCPU latency budget are adopted here, not re-chosen, and serving a rendered page must fit inside them.
- Cross-document identity is unresolved for every line until identity resolution first runs, so the unresolved state is the common path rather than an edge case.
- The active run's stored artifact is the one the worklist read for the same line; a run activated between the two reads is the staleness case the worklist already covers rather than a new one.

### Risks

- **The identity schema moves underneath this feature** *(likelihood: high, impact: medium)*: Identity resolution plans to extend both identity tables with run and project scoping and to re-scope their uniqueness, and records that the change is affordable only while the tables are empty. Anything built against today's shape must be expected to move. Mitigation: keep the traversal behind a narrow read path so the change lands in one place.
- **A displayed covariate value drifts from the value the fit used** *(likelihood: medium, impact: high)*: The values are reconstructed rather than stored, and the arithmetic that produced them lives behind a boundary the serving side may not import. Mitigation: FR-031 withholds rather than displays an unreconciled value, and the recorded limitation below states the durable fix.
- **Source pages resolve for the fixture and not for the corpus** *(likelihood: medium, impact: medium)*: The mapping from a document identifier back to a readable page is not stored and must be derived. A traversal proven only against seeded data would publish a traceability claim the real corpus does not support. Mitigation: FR-024's census, measured over real corpus documents rather than fixtures.

### Recorded Limitations

- **Covariate values are reconstructed rather than read from the artifact.**
  *Scope decision*: display covariate values recomputed on the serving side from the line's lifecycle history, reconciled against the fit and withheld when reconciliation fails, rather than read from a stored per-line record.
  *Supporting evidence*: the forecast artifact stores which covariates entered a fit but not the value each held per line; the walk that derives them lives in the modelling boundary, which the serving boundary may not import, and the contract between the two boundaries is the database rather than a shared module.
  *Reversal trigger*: the first time a reconciliation failure is observed against real data, or the first epic that needs the same values for a second surface — either makes the reconstruction the wrong place for this to live.
  *Production-scale alternative*: the forecast run stores the per-line conditioning values beside the posterior it produced, which requires an additive extension of another epic's table under the recorded conditions that permit one, and is not this feature's to perform.

- **The last hop of the traceability chain is configuration, not storage.**
  *Scope decision*: resolve a document identifier to a readable source through a binding held in configuration, and measure the resolution rate rather than enforce resolvability at the storage boundary.
  *Supporting evidence*: the schema stores the document identifier and page and constrains both, but stores no path to the source; the identifier is minted by a lossy transform that cannot be reversed, so recovering a source means scanning the corpus manifests.
  *Reversal trigger*: the published resolution rate falling below its stated target, or any linked record resolving to the wrong document — either means the binding is load-bearing enough to belong in storage.
  *Production-scale alternative*: the ingestion boundary records the source location alongside the document it produced, making an unresolvable citation impossible to store rather than merely detectable.

## Implementation Signals *(mandatory)*

- `NEW-UI` — A route rendering one line's detail: the distribution, the cumulative view, their textual equivalent, the linked-record list, the identity state, and the covariate panel.
- `NEW-UI` — Making the worklist's existing line identity navigate, which is another epic's delivered surface and carries that epic's change rule.
- `NEW-API` — A read endpoint returning one line's detail, carrying derived distribution summaries rather than stored arrays, plus artifact identification and resolved state.
- `NEW-API` — A read path that serves an originating document page for display, resolving a document identifier to a readable source.
- `NEW-CONFIG` — Whatever binds the running system to the corpus location, since the mapping from document identifier to source is not stored.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001** [US1]: A coordinator opening any line covered by the active run can see the full spread of modelled delivery outcomes, and no central estimate appears anywhere on the view.
- **SC-002** [US1]: For a line whose need-by date falls inside the distribution, the coordinator can both see the shaded mass beyond that date and read the same mass stated as a frequency in both directions.
- **SC-003** [US1]: Every figure on the detail view that the worklist also publishes matches the worklist's figure for the same line under the same active run, with no case in which the two disagree.
- **SC-004** [US1]: A line with no posterior in the active run resolves to one of the three named conditions on the view, never to an empty figure and never to an error page.
- **SC-005** [US1]: A reader using the textual equivalent alone obtains the reference class, the labelled quantile pair, the miss mass in both directions, the residual mass, and a banded summary of the day grid whose bands are bounded by the labelled quantiles, and is offered no single value as the answer.
- **SC-006** [US1]: The cumulative view names the share of outcomes falling beyond the modelled horizon rather than ending at the horizon without comment.
- **SC-007** [US1]: No response backing this view carries a stored draw array, a stored day-grid array, or any central summary of the distribution.
- **SC-008** [US2]: From a line with linked records, a coordinator can open any one of them and read the originating document page it was extracted from.
- **SC-009** [US2]: Every linked record on the view is identified by document title, page number and recorded confidence before it is opened, and is marked as synthetic where its document is synthesized.
- **SC-010** [US2]: The share of linked records whose page resolves and whose extracted span is present on that page is published as an exact count with its denominator, against the stated 100% target, measured over real corpus documents; it carries alongside it the declaration that it has no interval and the licensed reason for that, drawn from an enumerated closed set; and a share below the target is published with its cause.
- **SC-011** [US3]: A line with no resolved identity is described in words that name that condition, and a reader can tell it apart from a line whose resolved identity carries no document-derived members.
- **SC-012** [US3]: The distribution remains readable on a line in either identity state, and a line identifier naming no line or a closed line resolves to a stated outcome.
- **SC-013** [US4]: A coordinator can see which covariates the fit conditioned on and the value each held for this line, with no contribution figure present and no causal claim made.
- **SC-014** [US4]: A covariate value that cannot be reconciled against the value the fit used is withheld under a named state rather than displayed.
- **SC-015** [US1]: Every state this view can resolve to is present as text in the accessibility tree.
- **SC-016** [US1]: The detail view is reachable from the worklist without any element having been added to a worklist row.
- **SC-017** [US1]: Every figure on the view is attributable to the run that produced it — that run's identifier, model version, stored-array schema version and as-of date are all readable from the view.
- **SC-018** [US1]: The view resolves to exactly one resolution state in every case, including where a roster mismatch and a not-covered line apply together; each section it renders resolves to exactly one section-scoped state, including where nothing is wrong; and no two states in any scope share wording that leaves them indistinguishable.
- **SC-019** [US1]: While the active run is stale, the view's figures are marked as such without the reader consulting another surface.
- **SC-020** [US1]: No figure this view publishes renders as an impossibility or a certainty.
- **SC-021** [US2]: Every linked record shown is drawn from the active ingestion generation, and none from a generation no longer resident.
- **SC-022** [US3]: No identity-resolution record is written by this feature under any interaction with the view.
- **SC-023** [US4]: No covariate is displayed that the active run does not record as having entered its fit.
- **SC-024** [US1]: A reader who meets a failure can quote a correlation identifier and a stated cause for it.
- **SC-025** [US1]: The computations this feature introduces are developed test-first with property-based tests over their pure functions, and those tests run in the merge gate.
- **SC-026** [US1]: No combination of the view's stated scalar figures, taken over the five closed classes, yields a bare delivery date or a bare central summary of the line's distribution.
- **SC-027** [US1]: The mark count is fifty for every line, under every run, and at every stored draw count.
- **SC-028** [US1]: A line whose need-by date falls beyond the modelled horizon carries the miss mass labelled as a bound, and a line whose need-by date is at or before the run's anchor carries no miss figure at all while still rendering the distribution.
- **SC-029** [US1]: The cumulative view follows the distribution in reading order and never appears as the only encoding present.
- **SC-030** [US1]: No absolute calendar date appears as an axis, an axis tick label, or a figure title; the only calendar dates present are the need-by mark's label and labels attached to the labelled quantiles.
- **SC-031** [US1]: The textual equivalent renders within the same region as the figures it describes, and is reachable without assistive technology.
- **SC-032** [US1]: No single predicted delivery date for the line appears in any figure, axis, label, tooltip, or textual equivalent, nor in the response the view renders from.

## Glossary *(include when spec introduces 2+ domain-specific terms)*

| Term | Definition |
|------|------------|
| Posterior predictive distribution | The modelled spread of delivery outcomes for one line, held as a set of sampled durations in days counted from the active run's as-of date. |
| Quantile dotplot | The display FR-001 requires: a distribution shown as a fixed number of equal-probability marks, each standing for the same share of the population, so a one-sided probability can be answered by counting marks. |
| Covariate | A quantity the fit conditioned on for this line, observed at the run's anchor date. The registered plan and the forecast epic both use this term; it is used here unchanged. Distinct from the pooling groups the model shares strength across. |
| Reference class | The population a displayed proportion is a proportion *of* — comparable orders, the class the line's posterior was fitted over — named at the point each frequency is rendered. |
| Day grid | The stored per-day array over the modelled horizon from which the chance of missing a date is read. |
| Residual mass | The share of modelled outcomes falling beyond the end of the modelled horizon, stored explicitly rather than truncated. |
| As-of date | The date the active run was fitted, from which every duration on this view is counted. |
| Census | A figure computed over every element of a defined set rather than a sample of it, published as an exact count with its denominator and no interval. |
| Resolved entity | The sanctioned relationship joining a purchase-order line to values extracted from documents; the only route between the two. |
| Extracted span | The text of a value as it appeared in its source document, with the page it was cited to. |
| Ingestion generation | The version of ingested document content a chunk belongs to, of which one is active at a time. |

## Compliance Check

Audited against `project-instructions.md` **v1.2.11** (Last Amended 2026-07-29) by the Policy Auditor on
2026-07-30, alongside a Spec Validator pass scoring **23/25**. **Result: FAIL — carried by one CRITICAL
Governance violation of repository state rather than by the artifact.** Principles I, II, III, IV, V and
VII were assessed and met; VI and VIII are not engaged by this feature.

| Finding | Severity | Status |
|---|---|---|
| Workspace number 00012 is claimed nowhere the allocator looks — the directory exists in the working tree only, on no branch, local or remote | CRITICAL | **Open.** Discharged only by committing `specs/00012-line-detail-and-traceability/` to `main` *and pushing*; an unpushed claim is invisible to the next allocator's scan |
| FR-038 bounded its domain by pointing at FR-042, which closes a set of *states* and contains no figures, so its finiteness claim had nothing under it | MEDIUM | Resolved — FR-038 now closes the view's stated scalar figures itself, in five classes and no sixth |
| FR-042 claimed a precedence it did not state, and on the list-order reading inverted E010 FR-018a for the one pair E010 FR-033 establishes as jointly constructible | MEDIUM | Resolved — the resolution scope states its precedence and adopts E010 FR-018a's order by reference |
| FR-042's section-scoped sets carried no nominal member, so "exactly one state" made a degraded state permanent for every section | LOW | Resolved — each section-scoped set now carries its nominal member |
| FR-024 minted a second closed set of licensed no-interval reasons alongside the one E009 already fixes | LOW (advisory) | Resolved — adopted by reference rather than enumerated again |
| FR-039 declared what Principle II "rests on" from a feature-level artifact, and carried no normative verb | LOW (advisory) | Resolved — the exclusion is now a MUST, and the construal is attributed to this feature rather than asserted for the principle |
| A maximal reading of "every reported metric" would attach the interval-or-declaration obligation to the distribution's own figures | LOW (advisory) | Resolved — FR-041 records that those figures are the distribution communication Principle II mandates, not metrics reported about the system |
| The last traceability hop is completed at display time rather than in storage | LOW (advisory) | Accepted — carried as a Recorded Limitation with its scope decision, evidence, reversal trigger and production-scale alternative |

The Policy Auditor examined the FR-012 / FR-038 / FR-039 triple specifically for a Principle II
regression and found none: what FR-039 declines to forbid is a reader's own arithmetic over a faithfully
published distribution, not the system communicating a point estimate, and forbidding it would require
not publishing the distribution at all — inverting the principle's first sentence. FR-011 and FR-012
disclose the limit rather than assert a guarantee the feature cannot keep, which is Publish the Miss
behaviour.

**Sequencing, stated plainly:** the eight resolutions above were applied *after* the audit and validation
passes that found them, so they are not themselves re-audited. The recorded verdict is FAIL at v1.2.11;
the artifact's state is the post-remediation one.

**Registered-document conformance** was checked and is clean. All four of the project plan's registered
acceptance criteria for E012 are covered (FR-010/SC-003, FR-020/SC-008, FR-026–027/SC-011, FR-030/SC-013),
both plan constraints are honoured, and the workspace number matches the epic number per the v1.2.3
convention. The plan's phrase "covariates *driving* the forecast" against this spec's association-only
framing (FR-033, FR-034) is a strengthening, not a conflict.

**Cross-feature overlap (Step 2.9): none.** E010 cedes this ground explicitly in two places — its Scope
names E012 as owner of the posterior plot, the covariates and source navigation, and its FR-053 scopes
the no-arrays prohibition to its own surface, stating that E012's presentation obligations are this
feature's to state. E003 names the detail view only as a downstream consumer.

**Shared-document amendments required (Step 6.5), recorded here and deliberately not performed on this
branch**, since governance serializes amendments to registered documents on the default branch:

1. `specs/project-plan.md` — E012's entry records no dependency on E009, yet the only route from a line to
   a page runs through `resolved_entity_member`, of which E009 is the first writer, and `extracted_value`
   is permanently forbidden a foreign key to `purchase_order_line`. Wave ordering implies the dependency;
   the entry does not record it, and E009 is not yet complete.
2. `specs/sad.md` — if the Plan phase authors an architectural decision for the shape of the response
   backing this view, **ADR-0025** is the next free number, confirmed against both the catalog in `sad.md`
   and `specs/adrs/`. Under v1.2.11 a number claim must be visible on the default branch, so the number
   must be claimed there when the decision is taken rather than asserted from this branch.
