# Product Requirements Document: Procurement Risk Copilot

> Date: 2026-07-25 | Status: Draft

## Product Overview

Procurement Risk Copilot tells a construction procurement coordinator which material orders are most likely to arrive late, how late, and how confident that judgment is — with every claim traceable back to the page of the document it came from.

It reads construction specification and submittal documents, links the material described in a spec to the submittal that proposes it and the purchase order that buys it, and produces a calibrated probability distribution over each open order's delivery date. The coordinator sees a ranked worklist rather than a static log: the lines most likely to hurt the schedule appear first.

The product is built as a capability demonstration. It is a complete, running system evaluated against published targets, but it serves a demonstration audience rather than a production tenant base. Its value is the combination of a working end-to-end pipeline and an honest, reproducible account of how well that pipeline performs and where it breaks.

## Vision and Why Now

Every construction project runs a submittal log with a "lead time" column holding a single integer. That integer is a guess, it is almost always optimistic, and it is treated as fact by everyone downstream of it.

The vision is to make that column probabilistic. Not "56 days," but "P50 of 61 days, P80 of 94 days, 38% chance of missing your need-by date" — with the reasoning attributable to specific vendor history, specific approval-cycle behavior, and specific source documents.

Why now: material lead times have been volatile enough for long enough that contractors have already changed their behavior in response. They accelerate purchases, switch suppliers, and substitute products — all decisions that would be better made against a distribution than against a point estimate. The forecasting methods to support this (hierarchical partial pooling, survival models with right-censoring) are mature and well understood; what has been missing is connecting them to the documents where the material commitments actually live.

## Problem Statement

A procurement coordinator managing hundreds of open material lines cannot tell which ones are actually in trouble.

The information needed to make that judgment exists, but it is fragmented across three document worlds that share no identifiers. The specification says what was required. The submittal says what the subcontractor proposed. The purchase order says what was bought. The same physical product appears in all three under different manufacturer spellings, different part-number formats, and different units. Reconciling them is manual, so it is done rarely and only under pressure.

Meanwhile the only forward-looking signal available is the quoted lead time, which is a single optimistic number that carries no uncertainty and does not update as an order moves through its lifecycle. An order that has been sitting in "submitted" for 40 days and an order that shipped yesterday look identical in the log.

The cost of not solving this is that late material is discovered rather than anticipated. By the time a coordinator knows an order is late, the mitigations that were cheap — expediting, resequencing, substituting — are no longer available, and the remaining options are schedule impact and cost.

## Background and Evidence

Evidence is tiered by strength. Each tier is labeled so a reader can weigh it appropriately.

**Tier 1 — Research bodies and industry associations.**

- The Construction Industry Institute's research on late deliverables (240 surveys, 54 case questionnaires, 9 case studies) found that the most *frequent* late deliverables are also the most *severe*, that project teams rarely trace the full downstream impact of a late deliverable, and that knock-on effects push schedule to the right. CII published a Late Deliverable Risk Catalog taxonomy as a result.
- CII places materials at 50–60% of total capital project cost.
- AGC's 2026 Outlook reports 63% of firms had a project postponed, scaled back, or canceled in the prior six months. Contractor mitigation behavior is procurement-side: 41% accelerate purchases after award, 29% switch suppliers, 24% substitute products, 10% pre-buy before award.
- Long-lead electrical and mechanical equipment remains the dominant offender, with medium-voltage switchgear reported at 52–78 weeks and power transformers at 128+ weeks.

**Tier 2 — Industry trade press and vendor-reported figures. Not peer-reviewed; directionally useful only.**

- Roughly 35% of submittals are rejected on first review.
- Typical submittals take 1.8–2.4 review cycles.
- Review processing runs 11–18 days, with 10–20 days added per resubmittal.

**Tier 3 — Assumptions made by this project, with no supporting evidence gathered.**

- No primary user research was conducted. The coordinator persona below is constructed from published role descriptions and the documented industry behavior in Tier 1, not from interviews.
- The specific ranking behavior coordinators want (what they would chase first given a probability) is assumed, not validated.

**Data provenance.** All procurement lifecycle history in this product is synthetic and generated from disclosed assumptions. The document corpus mixes verbatim public-domain federal specifications with synthesized project documents. Both are documented in shipped artifacts (see CAP-001, CAP-010) so a reader can audit what is real and what was generated.

## Target Users, Stakeholders, and Core Personas

### Target Users

- Procurement and expediting coordinators at general contractors — the primary and only user the product is designed for.

### Stakeholders

- **Technical evaluators** — engineers and hiring managers assessing the system's methodology, calibration honesty, and engineering judgment. This is the audience whose comprehension the success metrics measure.
- **Project managers and project engineers** — downstream consumers of the coordinator's escalations. Not designed for, but their need-by dates define what "late" means.
- **Vendors and subcontractors** — subjects of the forecast. Not users.

### Core Personas

- **Dana — Procurement / Expediting Coordinator, general contractor.** Manages 200–400 open material lines across several active projects. Owns the submittal log and the vendor call list. Starts the day deciding who to chase. Pain: the log tells her what exists, not what is at risk, so triage is driven by whoever escalated most recently rather than by expected harm. Wants a defensible answer to "why did you call this vendor and not that one." Distrusts any tool that produces a number she cannot trace back to a document.

- **Morgan — Technical evaluator.** Reads the README before running anything. Looks for whether the evaluation was designed before the tuning, whether baselines are honest opponents, and whether the limitations section discloses real epistemic weaknesses rather than listing deliberately excluded features. Will check a cited source. Discounts any metric published without an interval or a baseline.

## User Needs / Jobs To Be Done

- When I start my day, I want to know which of my open lines are most likely to hurt the schedule, so I can spend my calls where they matter.
- When a forecast says a line is at risk, I want to see why, so I can defend the escalation to a PM.
- When I question a number, I want to reach the source document page in one step, so I can verify rather than trust.
- When the same product appears in the spec, the submittal, and the PO under different names, I want them recognized as one thing, so I am not reconciling by hand.
- When the system is unsure whether two records are the same item, I want it to say so rather than merge them, because a wrong merge corrupts my log silently.
- When I need to answer a question about what a spec requires, I want a grounded answer with citations, so I am not searching hundreds of pages.
- When my judgment differs from the system's on how critical a line is, I want to override it, because I know things the data does not.

## Product Principles or UX Principles

- **Traceable or it does not ship.** Every extracted value, every retrieved answer, and every forecast input must be attributable to a source page or a disclosed generative assumption. An unattributable number is a defect.
- **Uncertainty is the product, not a caveat.** The system communicates distributions and intervals. Collapsing to a single date anywhere in the interface is a regression.
- **Precision over recall where a mistake is silent.** A wrong cross-document merge corrupts data invisibly; a missed merge is visible as an unlinked record. Bias toward refusing to merge and routing to human review.
- **The model extracts parameters; code computes dates.** Language models identify and structure information. All date arithmetic, all ranking, and all probability computation happen in deterministic code.
- **Evaluate before you tune.** Evaluation sets are frozen and hashed before any tuning run touches them.
- **Publish the miss.** A target that is not met is published in the README with its cause. Targets are never retroactively adjusted to match results.
- **Honest opponents.** Every model claim is reported against a baseline strong enough that beating it means something.

## Scope Summary

P1 is a complete vertical slice, running locally, evaluated and documented — from raw documents through to a coordinator-facing ranked worklist and a grounded chat panel, with all evaluation results published.

P1 is deliberately **local-first but deploy-ready by design**: it runs under container orchestration on a developer machine, but is architected so that public hosting is a deployment exercise rather than a rewrite. Two boundaries make that true and are treated as P1 requirements, not P2 concerns (see Constraints).

Public hosting itself, the human review-queue workspace, and coordinator criticality overrides are P2. Everything associated with operating this as a real service for real tenants is out of scope entirely.

### In-Scope Capabilities

- Assembling an auditable, license-clean document corpus with per-document provenance
- Generating a synthetic procurement lifecycle dataset with disclosed generative assumptions
- Structure-aware document understanding and citable line-item extraction with per-field confidence
- Evidence retrieval over the corpus, measured against a frozen evaluation set
- Cross-document identity resolution across specification, submittal, and purchase order
- Probabilistic delivery-date forecasting with calibration evidence
- A risk-ranked coordinator worklist with forecast explanation and source traceability
- Grounded question answering with inline citations
- A reproducible evaluation harness and published rigor documentation

### Out-of-Scope Items

- Authentication, authorization, user accounts, and multi-tenancy
- Integration with ERP, Procore, or any construction management system of record
- Ingestion of real, non-public, or customer procurement data
- Schedule or CPM integration; the product consumes need-by dates, it does not compute them
- Automated actions on the coordinator's behalf — no emails sent, no orders placed, no vendors contacted
- Portfolio or executive rollup views across projects (owner's-rep persona)
- Real-time or streaming updates; the product operates on batch-refreshed state
- Mobile and field-device interfaces
- Production operational concerns: uptime targets, on-call, backup and restore, model drift monitoring
- Cost estimation, bid analysis, or change-order management

## Product Capability Map

Project-level execution anchors used by `specs/project-plan.md`. Keep these as capability clusters, not feature-level user stories.

| Capability ID | Capability | Priority | Outcome |
|---------------|------------|----------|---------|
| CAP-001 | Auditable Data Foundation | P1 | A document corpus and synthetic procurement history whose every record's origin, license basis, and generative assumption is documented and checkable |
| CAP-002 | Document Understanding & Extraction | P1 | Source documents become structured material line items, each value carrying a page citation and a confidence score |
| CAP-003 | Evidence Retrieval | P1 | A coordinator's question reaches the right passage, with accuracy measured against a frozen evaluation set and alternatives published |
| CAP-004 | Cross-Document Identity Resolution | P1 | The same material is recognized across specification, submittal, and purchase order, favoring refusal over incorrect merges |
| CAP-005 | Probabilistic Delivery Forecast | P1 | Each open line has a calibrated delivery-date distribution that accounts for vendor behavior, current lifecycle state, and still-open orders |
| CAP-006 | Risk-Ranked Coordinator Worklist | P1 | Open lines are ordered by expected schedule harm so the coordinator knows what to chase first |
| CAP-007 | Forecast Explanation & Source Traceability | P1 | A coordinator can inspect why a line is flagged and reach the originating document page |
| CAP-008 | Grounded Question Answering | P1 | Procurement questions are answered with inline citations to specific source pages |
| CAP-009 | Evaluation & Calibration Evidence | P1 | Every published performance claim is reproducible from a clean checkout and reported against an honest baseline |
| CAP-010 | Rigor & Limitations Documentation | P1 | A reader can audit the data, the model's intended use, and the project's epistemic limits without reading code |
| CAP-011 | Uncertain-Match Review Workspace | P2 | A human resolves ambiguous cross-document links that the system declined to merge |
| CAP-012 | Criticality Override | P2 | A coordinator adjusts a line's criticality when their judgment differs from the generated value |
| CAP-013 | Publicly Hosted Demonstration | P2 | An evaluator reaches a working instance from a URL without local setup |
| CAP-014 | Vendor Lead-Time Scorecards | P3 | Vendor-level performance summaries derived from the forecast model's posteriors |

## Success Metrics / KPIs / Desired Outcomes

All targets are release gates for P1 unless noted. Governing rule: **a missed target is published in the README with its cause; it is never hidden, re-tuned away, or retroactively adjusted.**

| Metric | Target | Why It Matters | Measurement Window |
|--------|--------|----------------|--------------------|
| Retrieval recall@5 | ≥ 0.85 on the frozen 50-item evaluation set, published with a Wilson 95% confidence interval | Top-5 is what the interface surfaces; the interval prevents overclaiming on a small set | Every evaluation run; gate at P1 release |
| Retrieval MRR | ≥ 0.70, published with a Wilson 95% confidence interval | Rank quality, not just presence, determines whether the coordinator finds the answer | Every evaluation run; gate at P1 release |
| Retrieval strategy ablation | Four-way comparison published in full, regardless of which strategy wins | Relative lift is more defensible than an absolute score on a self-authored set | P1 release |
| Identity-resolution merge precision | ≥ 0.95 on 40 hand-labeled pairs, published with its rule-of-three error bound | A wrong merge corrupts the log silently; this is the primary quality metric | P1 release |
| Identity-resolution recall | ≥ 0.80, explicitly secondary to precision | Missed links are visible and recoverable; the tradeoff is a stated design choice | P1 release |
| Forecast skill vs. Kaplan-Meier baseline | ≥ 20% improvement in threshold-weighted CRPS | The honest opponent: proves vendor and covariate structure earns its complexity beyond the pooled base rate | P1 release |
| Forecast skill vs. quoted lead time | Reported for context; not a gate | Shows the gain over current practice, but is an easy win and labeled as such | P1 release |
| 80% prediction-interval coverage | Empirical coverage within 73–87% | Calibration is the core claim; the band reflects the sampling error the dataset size actually supports | P1 release |
| Calibration artifacts | Reliability diagram and PIT histogram published | Coverage as a single number can hide compensating miscalibration | P1 release |
| Extraction traceability | 100% of extracted values carry a page citation and a per-field confidence | Traceability is a product principle, so partial compliance is a defect | Continuous |
| Language-model output validity | 100% of outputs schema-validated with repair-or-fail; no unvalidated value reaches storage or interface | Silent malformed output is the failure mode that corrupts everything downstream | Continuous |
| Evaluation reproducibility | Published numbers reproduce from a clean checkout using documented steps | An unreproducible claim is not evidence | P1 release |
| Evaluation-set integrity | Evaluation sets frozen and hashed before any tuning run | Prevents the tuning-to-the-test-set failure the small sample sizes invite | Before first tuning run |

## Assumptions

- A synthetic procurement history with disclosed assumptions is sufficient to demonstrate methodology, even though it cannot validate real-world predictive accuracy.
- Public-domain federal guide specifications are structurally representative enough of project specifications that a parser succeeding on them would largely succeed on real project specs.
- Matched specification → submittal → purchase-order triples for a single project cannot be obtained from public sources, making synthesis of the project-document layer necessary rather than merely convenient.
- The coordinator's decision — who to chase today — is well served by ranking on expected schedule harm. This is assumed, not user-validated.
- 200 procurement lines are enough to demonstrate hierarchical partial pooling structure, but not enough to validate vendor-level tail behavior.
- Approximately 120 uncensored delivery events will be available after held-out splitting, bounding calibration precision to roughly ±4 percentage points.
- Evaluation labels are single-annotator. No inter-annotator agreement is measured, and this is disclosed rather than corrected.

## Constraints

- **Data provenance.** All content is either public domain or synthetic. No proprietary, confidential, or customer data enters the corpus at any point.
- **Corpus composition.** The document corpus is a real public-domain federal specification base (verbatim, retaining genuine formatting irregularity) plus a synthesized project-document layer of submittals and transmittals tied to the synthetic projects and vendors. Every document is labeled REAL or SYNTHETIC in a shipped manifest carrying its license basis and the provenance its layer actually has — source, issuing body, and retrieval date for a retrieved document; generator identity, seed, generation date, and fixture hashes for a generated one. A generated document does not borrow a retrieval record it never had. Copyrighted reference standards are cited, never included.
- **Precomputed forecasts.** Delivery-date distributions are fitted offline and served from stored results. Nothing generates a posterior at request time. This is a P1 requirement because retrofitting it later is a rearchitecture, not a change.
- **Modest compute envelope.** All request-time components must run within a small hosted instance's CPU and memory budget, or delegate to a hosted service. Also a P1 requirement for the same reason.
- **Deterministic computation boundary.** Language models extract and structure. All date arithmetic, ranking, and probability computation happen in deterministic code.
- **Traced model invocations.** Every language-model call passes through a single instrumented path that records the request, response, token counts, latency, and cost.
- **Schema enforcement.** Every language-model output is validated against a schema with repair-or-fail semantics.
- **Fixed technology boundary.** The technology stack is a fixed given for this project rather than an open design question; it is recorded in `project-instructions.md` and elaborated in system design.
- **Single-developer scope.** Delivery capacity is one developer, which caps how much can be P1.

## Dependencies

- Public-domain federal specification sources (USACE/NAVFAC/AFCEC guide specifications and comparable federal masters) for the real corpus layer, and their continued public availability.
- A hosted or locally runnable language model for extraction and grounded answering.
- A relevance-reranking capability that fits the modest compute envelope — either a small local model or a hosted service.
- Reference taxonomies for construction specification structure (MasterFormat division and section numbering, submittal descriptor codes) used as extraction and chunking anchors.
- Published research sources cited in Background and Evidence, which must remain retrievable for the citations to be checkable.

## Risks

- **Synthetic data cannot validate real-world accuracy.** Every calibration result measures the model against the generator's assumptions, not against reality. If the generative assumptions are wrong in the same direction as the model, calibration looks good and means nothing. Mitigation: publish the generative assumptions as a first-class artifact so the circularity is visible rather than hidden.
- **Sample sizes bound what can be claimed.** With 50 evaluation queries, 40 labeled pairs, and roughly 120 uncensored events, confidence intervals are wide. Targets set above what the samples support would read as overfit. Mitigation: every metric published with its interval; ablations and skill scores lead over absolute numbers.
- **The generated project-document layer may be too clean.** If synthesized documents lack the formatting irregularity of real ones, the extraction stage demonstrates less than it appears to. Mitigation: the real federal specification layer carries the genuine-messiness burden; document which layer each extraction result came from.
- **Beating the honest baseline is not guaranteed.** If vendor-level signal in the synthetic data is weak, the hierarchical model may tie the marginal baseline. Mitigation: this is an accepted outcome, published as a finding with its cause, and treated as a legitimate result rather than a failure to conceal.
- **Traceability compliance erodes under delivery pressure.** The 100% citation and validation targets are the easiest to quietly relax. Mitigation: treat partial compliance as a defect, not a shortfall.
- **Scope inflation from a large P1.** Ten P1 capabilities against single-developer capacity is the primary delivery risk. Mitigation: P2 boundaries are explicit and the hosting deferral is deliberate.
- **Public availability of source specifications may change.** Mitigation: vendor the retrieved documents with their manifest at retrieval time.

## Open Questions

- What criticality scale do coordinators actually reason in — an ordinal band, a schedule-float-derived continuum, or something else? The synthetic generator's choice is an assumption pending validation.
- Should a line whose forecast has very wide uncertainty rank above or below a line with a confidently moderate risk? Ranking on expected harm alone may under-surface the lines a coordinator most needs to investigate.
- How should the interface represent a forecast for a line whose cross-document identity is unresolved? These lines have the weakest evidence and may warrant separate treatment.
- What is the right default action when extraction confidence for a field falls below threshold — suppress the value, show it flagged, or route it to review?
- Does the coordinator need to see how a forecast has moved over time, or only its current state? Change-over-time was not scoped and may be the more actionable signal.

## Release or Validation Approach

**P1 — local, evaluated, documented.** Validation is that an evaluator with no prior context can clone the repository, bring the system up locally following the documented steps, load the corpus and synthetic history, run the evaluation suite, and reproduce every number published in the README. The system is release-ready when all P1 capabilities are present, every success metric is measured, and every result — hit or miss — is published with its interval and its baseline.

The evaluation sequence is fixed and gated: evaluation sets are frozen and hashed first; retrieval, identity resolution, and forecast evaluations run against those frozen sets; results are written to the README before any tuning informed by them.

**P2 — publicly hosted.** A deployed instance an evaluator reaches by URL, plus the review workspace and criticality override. Success is that the hosted instance is behaviorally identical to the local one and that deployment required no change to how forecasts are produced or served.

**P3 — extensions.** Vendor scorecards, undertaken only after P1 and P2 are complete and published.

Because the primary audience is an evaluator rather than an operating team, the validating signal is not adoption or usage. It is whether a skeptical technical reader, having checked the sources and reproduced the numbers, concludes the methodology and its stated limits are sound.

## Domain Glossary / Terminology

- **Submittal**: A subcontractor's proposed product, material, or shop drawing, submitted for the design team's approval before fabrication or purchase.
- **Submittal log**: The register of all submittals on a project with their status, review cycles, and lead times. Owned by the general contractor.
- **Shop drawing**: A fabrication-level drawing prepared by a supplier or fabricator, submitted for approval.
- **Expediting**: The practice of actively chasing vendors and reviewers to keep material moving. The primary persona's core activity.
- **Long-lead item**: Material whose procurement duration is long enough to constrain the construction schedule.
- **Need-by date**: The date material must be on site to avoid delaying the work that consumes it.
- **Float**: Schedule slack — how long an activity can slip before it delays project completion.
- **Lead time**: Elapsed duration from procurement commitment to delivery. Conventionally recorded as one number; this product replaces it with a distribution.
- **MasterFormat**: The standard numbering system organizing construction specifications into divisions and sections.
- **Submittal descriptor codes**: Standard codes classifying required submittal types (shop drawings, product data, samples, and similar) within a specification section.
- **Purchase order line**: A single ordered material item, the unit this product forecasts.
- **Lifecycle state**: An order's current position in the sequence from submitted through approved, released, in fabrication, shipped, and delivered.
- **Rework loop**: A submittal rejected and resubmitted, adding one or more review cycles.
- **Right-censoring**: An observation where the outcome has not occurred yet — an order still open, known only to have taken *at least* its elapsed duration so far.
- **P50 / P80**: Delivery durations with 50% and 80% probability of not being exceeded. P80 is the planning-relevant figure.
- **Calibration**: Whether stated confidence matches observed frequency — an 80% interval should contain the outcome about 80% of the time.
- **CRPS / threshold-weighted CRPS**: A score measuring how close a predicted *distribution* is to the observed outcome. The threshold-weighted form remains valid when observations are right-censored, which plain CRPS does not.
- **Skill score**: A model's score expressed as improvement over a named baseline, making an otherwise uninterpretable absolute score meaningful.
- **Kaplan-Meier baseline**: A pooled empirical duration distribution using no vendor or covariate structure. The honest opponent for the forecast model.
- **Entity resolution**: Determining that records in different documents refer to the same real-world item.
- **Golden set**: A frozen, hand-labeled evaluation set of questions and their correct answers.
- **Partial pooling**: A modeling approach where vendor-specific estimates borrow strength from the overall population, stabilizing vendors with few observations.

## Handoff Guidance

Context that downstream architecture design or governance work must preserve.

- **Product intent to preserve**: The deliverable is a working system *and* an honest, reproducible account of its performance. The evaluation harness, the published results, and the limitations documentation are product, not documentation overhead. A design that delivers the pipeline while treating evaluation as a follow-on has missed the point.

- **Scope boundaries to respect**: One persona (the coordinator). No authentication, no multi-tenancy, no system-of-record integration, no automated outbound action. Hosting, the review workspace, and criticality override are P2 and must not be pulled into P1 to make a design cleaner.

- **Critical constraints**:
  - Forecast distributions are fitted offline and served from stored results. No request-time posterior generation, in P1 or ever.
  - Request-time components must fit a small hosted instance's compute envelope or delegate to a hosted service.
  - Language models extract and structure; deterministic code performs all date arithmetic, ranking, and probability computation.
  - Every model invocation is traced through one instrumented path; every model output is schema-validated with repair-or-fail.
  - Every extracted value carries a page citation and a per-field confidence. No exceptions for convenience.
  - Cross-document merging biases toward refusal. Uncertain pairs are withheld, not merged.
  - Evaluation sets are frozen and hashed before tuning. This ordering is a governance requirement, not a suggestion.
  - Corpus documents are labeled REAL or SYNTHETIC with provenance. Licenses are not mixed within a corpus location.

- **Open decisions needing technical input**:
  - How precomputed forecast results are stored and refreshed, given that they must be regenerable but never request-time generated.
  - Whether reranking uses a local quantized model or a hosted service — the choice is compute-envelope-bound, and the constraint applies from P1 regardless.
  - How confidence thresholds for extraction and for merge decisions are set and where they are configured, since both are product-behavior levers exposed as technical parameters.
  - What the review queue's data contract must be in P1 so that the P2 workspace is additive rather than a schema change.
  - How reproducibility of published numbers is guaranteed across environments, given that the evaluation harness is a release gate.

- **Rigor artifacts that are deliverables, not optional**: a corpus manifest with per-document provenance and license basis; a datasheet documenting the synthetic generator's assumptions; and a model card covering intended use, factors, metrics, and caveats. Limitations are written as decision records — scope decision, supporting evidence, the condition that would reverse it, and the production-scale alternative — and address epistemic validity only. Deliberately excluded features belong in Out-of-Scope, never in Limitations.

## Project Context Baseline Updates

- Product name adopted: **Procurement Risk Copilot**.
- Purpose confirmed as a capability demonstration: full working software, with success measured by methodological rigor and evaluator comprehension rather than operational adoption.
- Primary persona fixed to the procurement/expediting coordinator; other roles are stakeholders only.
- P1 boundary fixed to a complete local-first vertical slice; hosting deferred to P2 with two P1 architecture constraints preserving deployability.
- Metric posture fixed: numeric targets with a standing published-miss rule.
- Corpus shape fixed: real public-domain federal specification base plus synthesized project-document layer, with per-document REAL/SYNTHETIC provenance labeling.
- Forecast evaluation fixed to threshold-weighted CRPS as a skill score against two baselines, with a marginal survival baseline as the primary honest opponent.
- Criticality fixed as a generated per-line value with a documented assumption, coordinator-overridable in P2.
- Corpus scale fixed at 40–50 documents within the 30–60 envelope: at least 20 real specification sections weighted toward long-lead electrical and mechanical equipment, plus at least 25 synthesized project documents.
- "Public domain" is recorded as a basis, not asserted as a category. A license basis states the governing statute or license identifier, the document identifier with its revision date, and the outcome of a point-of-use copyright check; a document whose basis cannot be established is excluded and the exclusion recorded.
- Provenance vocabulary fixed: **layer** (REAL or SYNTHETIC, a closed set), **license basis**, **corpus location** (the unit of license segregation), and **citation anchor** (the page number and document identifier that make a page citable).
- Synthetic realism is treated as a measurable property rather than an aspiration: deliberate imperfections are recorded per document as named **irregularity classes**, so every downstream figure stays partitionable by layer and condition and no quality metric is computed on generated material alone without being labeled.
- Forecast horizon posture fixed: probability of lateness is published over a bounded day horizon, and probability mass falling beyond that horizon is disclosed as an explicit residual rather than silently truncated.
- Model-call vocabulary fixed for every surface that reports one: an invocation carries exactly one outcome of `valid`, `repaired`, or `failed`, with the repaired rate published as a quality signal in its own right rather than folded into a success rate.
- Model-dependent results are reproducible without the provider: automated verification resolves every model call from committed response fixtures with no credential and no network, so a published number never depends on a live call at the time it is checked.
- Calibration precision is governed by the held-out **uncensored event count**, not the split fraction. The two are not interchangeable: a fraction can be stated without knowing how many held-out lines actually finished, and only finished lines can grade a forecast. Any published coverage band implies an event count, and the epic performing the split is the one that learns the realized figure.
- A published band may only be changed by **pre-registration** — fixed before the result it judges is computed. Widening a band after seeing coverage is the retroactive adjustment the published-miss rule exists to prevent.

