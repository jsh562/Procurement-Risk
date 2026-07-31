# Research: Hybrid Retrieval and Reranking — Requirements Quality

> Feature: E008 | Date: 2026-07-29 | Purpose: grounding for CHL001 (Performance), CHL002 (Testing)
> and CHL003 (API Quality). These checklists test **requirements quality, not implementation
> behaviour** — the subject under test is `spec.md`'s 36 functional requirements and 16 success
> criteria and `plan.md`'s AD-001–AD-011, judged for completeness, unambiguity, verifiability,
> consistency and traceability. Complements `research.md` (retrieval domain practice) and
> `research-implementation.md` (engineering detail); restates neither.

## Requirements-quality characteristics

- **Practice**: ISO/IEC/IEEE 29148:2018 is the standard that names them. It separates
  characteristics of an **individual** requirement — necessary, appropriate, unambiguous, complete,
  singular, feasible, verifiable, correct, conforming — from characteristics of the **set**:
  complete, consistent, feasible, comprehensible, able to be validated. INCOSE's *Guide to Writing
  Requirements* harmonises with it and converts each into checkable authoring rules.
- **Implies**: The checklist needs two kinds of item. Per-requirement items ask of one FR: does it
  state one thing, can it be verified, is it interpretable one way. Set-level items can only be
  asked of all 36 together: do any two conflict, does a term mean the same thing throughout, is the
  set sufficient for its scope. Do not ask a set question of a single requirement.
- **Flag**: 29148 is paywalled and was not read directly; the lists are corroborated across
  independent secondary sources but the exact wording is second-hand. Secondary sources also
  **disagree** on whether *traceable* is among the individual characteristics — the 2011 edition
  listed it, the 2018 edition handles traceability through attributes and relationships instead. Do
  not cite a characteristic count as settled.
- **Sources**: <https://www.iso.org/standard/72089.html>,
  <https://www.incose.org/docs/default-source/working-groups/requirements-wg/guidetowritingrequirements/incose_rwg_gtwr_v4_summary_sheet.pdf>

## Performance-requirement quality

- **Practice**: No single standard prescribes a form; the obligation assembles from two places.
  NASA's requirements guidance requires performance values carry **tolerances** and rejects
  unverifiable terms — *fast*, *adequate*, *minimize*, *robust*. Site-reliability practice requires
  an objective name its **event population** (good events over total), its **statistic**
  (percentiles rather than averages, because an average hides the tail), its **measurement window**,
  and its **measurement point** — server log, load balancer, probe and client instrumentation give
  different numbers for the same service.
- **Implies**: Test each performance statement for four parts: workload, environment, statistic,
  measurement method. This epic supplies a **range** — "reranking 50 candidates within 150–400 ms on
  one shared vCPU" — and a range is not a statistic: it does not say whether 400 ms is a p99, a mean
  or a never-exceed. "Container steady-state RSS ≤ 400 MB" names an environment and a threshold but
  no measurement occasion (before or after warm-up; with one session or the two AD-011 commits) and
  no counter (process resident set or container working set). FR-033 requires the figures be
  reported; nothing yet fixes how they are taken.
- **Flag**: The four-part form is a synthesis, not a citation — recognised practice rather than a
  rule. The commonly omitted part here is the statistic, and it is omitted in a way that **cannot
  fail**: any observed latency inside a 250 ms-wide band satisfies the sentence as written.
- **Sources**: <https://www.nasa.gov/reference/appendix-c-how-to-write-a-good-requirement/>,
  <https://sre.google/workbook/implementing-slos/>

## Test-strategy and coverage-requirement quality

- **Practice**: Substituting an independently written implementation for a missing oracle is a named
  technique — the **pseudo-oracle**, used where the program under test is "non-testable" because the
  expected answer is not independently known. Its soundness rests on the assumption that two
  implementations of one specification fail independently, and **that assumption has been
  empirically falsified**: 27 independently written versions of one specification, run through a
  million tests, produced coincident failures far above the independence prediction. The correlated
  fault is shared misreading of the specification — precisely what a pseudo-oracle cannot see.
- **Implies**: Test whether the plan's substitution — a Python recomputation of the fusion
  arithmetic asserted equal to the SQL output — states the four things that make it sound: that the
  Python side is derived from the **published definition** rather than transcribed from the SQL;
  that its inputs are **generated** rank vectors rather than vectors read back from the query under
  test; what the oracle does **not** cover (candidate-set selection at the 50-row cut, the per-arm
  tie-break, limit semantics under CTE inlining); and that the substitution is recorded in the
  project's mandated limitation format rather than as a testing note. A coverage requirement is
  judged the same way — on whether it names its population, not on the number.
- **Flag**: Nothing authoritative endorses a pseudo-oracle for SQL-resident ranking specifically;
  `research-implementation.md` already records that no source covers testing this shape. Treat the
  technique as recognised and its independence assumption as known-false, which makes "the oracle
  and the SQL agree" a weaker claim than it reads.
- **Sources**: <https://dl.acm.org/doi/10.1145/800175.809889>,
  <https://www.csc.kth.se/utbildning/kth/kurser/DA2210/vettig13/Seminarier/KnightLeveson.pdf>

## API contract quality

- **Practice**: RFC 9457, which obsoletes 7807, defines the problem-details shape and makes **every
  member optional** — `type`, `status`, `title`, `detail`, `instance` — with extension members that
  unrecognising clients must ignore. It warns against defining new problem types for conditions a
  plain status code already expresses. RFC 9110 defines *safe* and *idempotent* as properties of the
  **method the client requests**, not guarantees about server implementation.
- **Implies**: Test the contract for an error taxonomy covering every declared failure mode, and
  specifically that the refuse-versus-degrade distinction survives into the wire format:
  encoder-identity mismatch (FR-007, refuse) and reranker-unavailable (FR-021, degrade) must be
  distinguishable by a machine, not only by prose. Because RFC 9457 requires nothing, "errors use
  one `Problem` shape" is not by itself a completeness claim — the contract must say which members
  are required *here*. That is exactly the defect found in the existing `risk_read/failures.py`,
  where the helper omits `status` while the declaration requires it. Every declared behaviour must
  be observable: the degraded flag, the arm that produced each result, the match kind and the
  ranking parameters are all state the contract asserts and must therefore expose.
- **Flag**: There is **no standard machine-readable representation for a ready-degraded state**.
  The closest precedent is an expired Internet-Draft and must not be cited as a standard. An item
  may require the representation be *defined and versioned in this contract*, but not that it
  *conform* to anything. Note also that HTTP idempotency is not FR-020's repeatable ordering — a
  request can be idempotent and still return a different order.
- **Sources**: <https://www.rfc-editor.org/rfc/rfc9457.html>,
  <https://www.rfc-editor.org/rfc/rfc9110.html#name-idempotent-methods>

## Measurability and falsifiability of success criteria

- **Practice**: 29148's *verifiable* characteristic requires that realisation be verifiable and the
  requirement measurable, which in practice means a stated verification method and a stated
  pass/fail threshold. Empirical work operationalises the opposite as detectable **requirements
  smells** — subjective language, non-verifiable terms, vague pronouns, comparatives with no
  referent — and finds such static checks uncover practically relevant defects cheaply. A proportion
  criterion is checkable only when both sides of its ratio are named: which events count as good,
  and over which enumerated population.
- **Implies**: Test each criterion for a named population and a named measurement occasion. This
  epic's "100% of X" criteria are sound *where the population is enumerable and the property
  deterministic* — over every returned result, over the enumerated part-number set, or explicitly
  declared a census needing no interval. They become defects when the population is left implicit:
  several lean on this epic's own evaluation set, which does not exist until it is built. Criteria
  carrying "incurs no load cost" or "the strongest single arm" need a stated decision rule or they
  cannot be adjudicated.
- **Flag**: **No standard states that a criterion which cannot fail is a defect.** It is derivable —
  a criterion true of every possible implementation constrains nothing and so fails *necessary* as
  well as *verifiable* — but report it as derived, not cited. Given Principle VII, the sharpest item
  available is simply: *for each criterion, state the observation that would make it fail.* A
  criterion with no such observation is the hazard this project already named once, where a
  comparison against a nearly unordered fusion list was close to guaranteed to succeed.
- **Sources**: <https://arxiv.org/abs/1611.08847>, <https://www.iso.org/standard/72089.html>

## Traceability

- **Practice**: The mature form is **bidirectional** traceability with coverage analysis in both
  directions, as required for safety-critical software: forward from a requirement to the design
  element and the verification that discharges it, backward from every design element and test to
  the requirement that justifies it. The recognised findings are symmetrical — a requirement with no
  downstream link (uncovered), and an artifact with no upstream parent (**orphan**), treated as a
  risk in its own right because it is capability nobody asked for. Derived requirements — introduced
  by design rather than flowed down — must be identified as such and fed back for review.
- **Implies**: Test the Requirement Coverage Map both ways: every requirement reaches a named
  component *and* a named verification, and every module in the project structure traces to a
  requirement or is declared derived. Test that obligations landing on **another epic or another
  branch** have an owner and a verifier, not merely a mention — the recorded amendments are exactly
  this shape. FR-036's own history is the worked example to generalise from: it was mapped to a
  module that computes nothing, and so was traceable to a component that could not deliver it.
- **Flag**: Bidirectional traceability with coverage analysis is a **safety-domain** obligation;
  29148 requires traceability but is thinner about coverage analysis, so importing that rigour here
  is a choice rather than compliance. The third failure mode — a requirement **satisfied in form**
  by a component that cannot deliver it — has no standard name and no citable rule; it is
  assurance-case reasoning plus this project's own observation that a criterion can be "satisfiable
  in form while the weighting does nothing". State it as convention.
- **Sources**: <https://www.parasoft.com/learning-center/do-178c/requirements-traceability/>,
  <https://www.iso.org/standard/72089.html>

## Two cautions that apply to all three checklists

1. **The characteristic lists are normative, not empirically validated.** A 2023 harmonisation of
   requirements-quality research finds the field concentrates on normative rules and largely fails
   to connect a quality attribute to its measured impact on downstream activities. An item asserting
   "this requirement is ambiguous" is a judgement backed by convention, not a measurement; items
   should say what reading is available and why it changes the build, rather than scoring compliance
   against a list. <https://arxiv.org/abs/2309.10355>
2. **Keep the subject under test correct.** Every item must be answerable by reading `spec.md` and
   `plan.md` alone. "Are error-handling requirements defined for all API failure modes?" is in
   scope; "Verify the API returns proper error codes" is not, because it tests the system rather
   than the text. The refuse-versus-degrade items are the easiest to write the wrong way round.

## Sources Index

| URL | Topic | Fetched |
|-----|-------|---------|
| <https://www.iso.org/standard/72089.html> | quality characteristics; verifiability; traceability | 2026-07-29 |
| <https://www.incose.org/docs/default-source/working-groups/requirements-wg/guidetowritingrequirements/incose_rwg_gtwr_v4_summary_sheet.pdf> | quality characteristics | 2026-07-29 |
| <https://www.nasa.gov/reference/appendix-c-how-to-write-a-good-requirement/> | performance-requirement quality | 2026-07-29 |
| <https://sre.google/workbook/implementing-slos/> | performance-requirement quality | 2026-07-29 |
| <https://dl.acm.org/doi/10.1145/800175.809889> | test-strategy quality (pseudo-oracle) | 2026-07-29 |
| <https://www.csc.kth.se/utbildning/kth/kurser/DA2210/vettig13/Seminarier/KnightLeveson.pdf> | test-strategy quality (correlated faults) | 2026-07-29 |
| <https://www.rfc-editor.org/rfc/rfc9457.html> | API contract quality | 2026-07-29 |
| <https://www.rfc-editor.org/rfc/rfc9110.html#name-idempotent-methods> | API contract quality | 2026-07-29 |
| <https://arxiv.org/abs/1611.08847> | measurability and falsifiability | 2026-07-29 |
| <https://www.parasoft.com/learning-center/do-178c/requirements-traceability/> | traceability (secondary) | 2026-07-29 |
| <https://arxiv.org/abs/2309.10355> | cross-cutting caution | 2026-07-29 |
