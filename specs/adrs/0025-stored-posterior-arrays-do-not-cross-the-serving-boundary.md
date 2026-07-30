---
adr_id: ADR-0025
status: accepted
date: 2026-07-30
tags: [serving-boundary, api-contract, uncertainty, posterior, computation-boundary, traceability]
supersedes: []
superseded_by: ""
related_artifacts: ["specs/prd.md", "specs/sad.md", "specs/project-plan.md", "specs/00010-risk-ranked-coordinator-worklist/spec.md", "specs/00012-line-detail-and-traceability/spec.md", "CAP-005", "CAP-006", "CAP-007", "CAP-014", "ADR-0004", "ADR-0008", "ADR-0018", "FR-053", "FR-052", "FR-011", "FR-012", "FR-013", "FR-038", "SC-007", "E003", "E007", "E010", "E012", "E019"]
deciders: ["Project owner", "Solution architect"]
---

# ADR-0025: Stored Posterior Arrays Never Cross the Serving Boundary; Surfaces Receive Derived, Resolution-Bounded Summaries

## Status

Accepted. Supersedes nothing and withdraws no clause of any earlier record.

Claimed by **E012 — Line Detail and Traceability**, which is the epic that raised it. Under the Governance clause introduced at project-instructions v1.2.11, a decision-record number is claimed by a record being visible on the default branch; this file landing there is that claim, and it carries decision content rather than a placeholder because the decision is resolved.

[ADR-0004](0004-materialized-posterior-draws-with-sql-side-risk-computation.md) decided what is *stored* and where the risk arithmetic *happens*. This record decides what may *leave* the request-serving boundary, which ADR-0004 did not address and which no record has addressed since. The two are complementary and not competing: ADR-0004's chosen option is untouched, both stored representations remain exactly as it specified, and the SQL-side computation it establishes is the mechanism this record depends on.

## Context

A forecast run stores two arrays per purchase-order line, per [ADR-0004](0004-materialized-posterior-draws-with-sql-side-risk-computation.md): a canonical sorted array of posterior predictive draws — on the order of four thousand values — and a derived day-grid survival array that serves as the read path. `specs/project-plan.md` § Shared Data Entities registers the pair as `PosteriorDraws / SurvivalArray | E003 (schema), E007 (populated) | E010, E012, E019`. Three surfaces consume them:

- **E010 — Risk-Ranked Coordinator Worklist**, delivered, `.qc-passed`.
- **E012 — Line Detail and Traceability**, in planning, and the epic claiming this record.
- **E019 — Vendor Lead-Time Scorecards**, unbuilt, P3, and explicitly constrained to derive from existing posteriors with no additional fitting.

The second core principle of this project — *Uncertainty Is the Product* — holds that the system MUST communicate distributions and intervals, never a bare point estimate, and that collapsing a forecast to a single date anywhere in the interface is a regression. That prohibition is the reason the product exists, and it is stated against the interface as a whole rather than against any one screen.

**The gap this record closes is that the prohibition has no general statement at the boundary.** E010's **FR-053** already keeps both arrays off *its* response, and gives the correct reason: *"A consumer holding either can compute a mean, a mode, or any quantile it chooses and render exactly the delivery date FR-007 removes, so withholding them is what keeps FR-007 enforceable against a client this feature does not write."* But that requirement then scopes itself deliberately, and says so in terms:

> The prohibition is scoped to this surface rather than to the arrays themselves: E003 stores them, E007 writes them, and the project plan records E012 as a second consumer of the same arrays for a single-line detail view whose presentation obligations are that feature's to state. This requirement binds the worklist response and asserts nothing about theirs — a scope stated here rather than left as the silence that would either over-reach into another epic or read as permission.

That self-limitation was correct conduct at the time: E010 could not bind an epic it did not own, and the alternative was to reach across a boundary it had no standing to cross. The consequence, however, is that the general rule is asserted nowhere. E012 has now re-derived it independently as **FR-011** and **FR-012**, arriving at the same answer for the same reason. E019 will face the identical question with no rule to inherit, and the deciding fact is that **silence at the boundary reads as permission** — the arrays are in the database, the read path is already established, and nothing refuses a surface that asks for them.

A decision is needed now rather than at E019's planning for three reasons. Two of the three surfaces have already paid the cost of deciding this from first principles. E012 is at the point where its response shape becomes a contract. And a rule authored after the third consumer has shipped is a rule that codifies whatever the third consumer did.

## Decision Drivers

- The single-date prohibition must be enforceable against clients this project does not write — a rule that binds only the renderers in this repository is not a rule about the product's behaviour, it is a convention about its current source tree
- The rule must bind every present and future surface that reads a posterior, so a new consumer inherits a boundary rather than re-deciding one; a rule that binds one surface and is silent for the next two has already failed once here
- Derivation must sit inside the deterministic-computation boundary established by [ADR-0008](0008-deterministic-provenance-and-computation-boundary.md) and required by Principle V, where the import contracts can reason about it and the mandatory property-based tests over pure functions already run
- The record must state its honest limit rather than claim a guarantee the architecture cannot keep — Principle VII requires publishing the miss, and an overclaimed prohibition is the kind of miss that is discovered by a reader rather than published by the author
- Resolution must be a *stated* property of the boundary that a reader can check, not an implementation detail of whichever surface happens to be rendering
- Payload cost: several thousand values per line, per surface, is a real transfer expense that buys the consumer nothing it is permitted to use

## Considered Options

### Option A: Ship the raw arrays and rely on client discipline

The response carries the stored draw array and the day-grid array; the presentation rules forbidding a central summary live in the interface layer, and each surface is trusted to honour them.

- **Pros**:
  - Simplest possible serving contract — one shape, no per-surface derivation, no resolution parameter to agree on
  - A future surface needing an unanticipated resolution is unblocked without a server-side change
  - Keeps every derivation decision next to the rendering that motivates it, which is where the presentation expertise sits
- **Cons**:
  - **Unenforceable.** The prohibition becomes a property of the clients this repository happens to contain. A consumer holding the draw array computes a mean with one call; the arrays *are* the point estimate, one reduction away. E010's FR-053 already names this exact failure mode as its reason for withholding
  - It relocates a product principle into the layer least able to test it. Principle V puts probability arithmetic in deterministic testable code and fails the build when computation appears where it does not belong; this option puts the decisive arithmetic in the browser
  - Several thousand values per line, transferred so the consumer can discard almost all of them
  - It makes the single-date prohibition a claim about intent rather than a claim about the system, which is precisely the false confidence Principle II exists to remove

### Option B: Per-surface rules, each surface deciding independently

Every consuming epic states its own response prohibition in its own requirements, as E010 did in FR-053 and E012 is doing in FR-011 and FR-012.

- **Pros**:
  - No epic reaches across a boundary it does not own — this is the conduct E010 correctly chose, and it is why the status quo is not a defect on anyone's part
  - Each surface's rule is written by the people who understand that surface's presentation obligations
  - Requires no new record and no cross-epic coordination
- **Cons**:
  - **This is the status quo, and it has already produced the failure.** FR-053 binds one surface and is silent for two, and its own text says so
  - Silence reads as permission. E019 arrives at a boundary where two surfaces withhold the arrays and nothing says it must; the absence of a rule is indistinguishable from an allowance
  - The cost is paid once per surface, and the reasoning is re-derived each time by someone who may reach a different answer under schedule pressure
  - It leaves the project unable to answer "does this system ever transmit a posterior?" with anything but an enumeration of current surfaces, which goes stale the moment a surface is added

### Option C: Derived summaries at a fixed, stated resolution, decided once

No response from the request-serving boundary carries a stored posterior array. Each surface receives figures derived server-side at a resolution that surface states and fixes, together with the artifact identification needed to attribute them to a run.

- **Pros**:
  - The prohibition is enforceable at the boundary, which is the only place it can be enforced against a client this project does not write
  - A new surface inherits the rule and states only its own resolution and derivation — the general question is answered once
  - Derivation lands in the computation modules, inside [ADR-0008](0008-deterministic-provenance-and-computation-boundary.md)'s boundary, where the import contracts reach it and where the testing policy already mandates test-first development and property-based tests
  - Resolution becomes a stated, checkable property of the response rather than an implementation detail — E012 fixes fifty marks under FR-002 precisely so the denominator cannot move between lines, and that guarantee is only meaningful if the resolution is part of the contract
  - Payload shrinks from thousands of values to the marks and bands a surface actually renders
- **Cons**:
  - A surface needing a resolution the boundary does not derive must extend the server rather than compute locally, so some presentation changes now require a backend change
  - Per-surface derivation code accumulates in the computation modules, and each new resolution is a new pure function with its own property tests
  - **It does not make a central summary unrecoverable**, and the record must say so — see the Decision Outcome
  - It adds a rule a reviewer must know about, where previously each epic's requirements were self-contained

## Decision Outcome

Chosen option: **Derived summaries at a fixed, stated resolution, decided once** — no response from the request-serving boundary may carry a stored posterior array, and a surface receives figures derived server-side at a resolution that surface states.

The rule, stated once so no future surface has to re-derive it:

1. **No response from the request-serving boundary carries a stored posterior array.** Neither the canonical draw array nor the derived day-grid survival array appears in any response, in any surface's payload, under any field name, in whole or as a contiguous slice. This binds every present and future surface that reads a posterior.
2. **A surface receives figures derived server-side at a stated, fixed resolution.** For E012 that resolution is fifty equal-probability marks and a banded cumulative series (FR-011). The resolution is fixed for the surface — it does not vary with the line, the run, or the stored draw count (E012 FR-002) — and it is stated in that surface's requirements, so a reader can check the figure against the denominator rather than infer it.
3. **Derivation happens server-side, in the computation modules.** Never in the client, and never by handing the client an intermediate the client must reduce. This places the arithmetic inside [ADR-0008](0008-deterministic-provenance-and-computation-boundary.md)'s deterministic-computation boundary as Principle V requires, and it is the reason clause 1 is a boundary rule rather than a rendering convention.
4. **Derived figures travel with the artifact identification needed to attribute them.** The active run's identifier, its model version, the schema version of its stored arrays, and the as-of date those figures are counted from — E010 FR-052 and E012 FR-013 each state this for their own surface, and Principle I requires it of every published figure. A derived summary that cannot be resolved back to the run that produced it is a worse artifact than the array it replaced.
5. **A surface needing a resolution the boundary does not currently derive extends the derivation server-side.** It does not reach for the array. This is the clause that keeps clause 1 from being negotiated away one legitimate-sounding need at a time.

### Why this is architectural rather than feature-local

A consumer holding either array can compute a mean, a mode, or any quantile it chooses, and render exactly the date the product refuses. Withholding the arrays is what keeps the single-date prohibition enforceable against clients this project does not write — the E010 requirement that first stated this reasoning was right about it, and the reasoning does not depend on which surface is asking. The rule therefore binds the boundary, not the worklist.

### The honest limit, stated rather than glossed

**This is a bound on resolution. It is not a guarantee of unrecoverability, and this record does not claim to be one.**

Fifty equal-probability marks *are* the distribution. Counting to the twenty-fifth yields its middle. That is not a leak in the design — it is what plotting a distribution means, and any faithful rendering of a distribution permits approximating its centre. E012's **FR-012** concedes exactly this and declines to claim otherwise:

> Stated as a prohibition on what the response *carries* rather than on what a determined consumer could reconstruct: fifty equal-probability marks are the distribution, and any faithful rendering of a distribution permits approximating its centre. Claiming otherwise would be a guarantee this feature cannot keep.

What Principle II actually rests on is that no such value is **stated, labelled, or carried as a field**. The system does not publish a central summary; it publishes a distribution, and a reader who does arithmetic on a faithfully published distribution is doing their own arithmetic rather than reading the system's answer. Forbidding *that* would require not publishing the distribution at all, which inverts the principle's first sentence.

A reader arriving at this record for the proposition that the prohibition makes a central summary unrecoverable is reading it wrong. Principle VII requires the miss to be published rather than the guarantee asserted, and the miss is published here: the resolution bound raises the cost of reconstructing a centre and removes any *stated* one; it does not and cannot make reconstruction impossible.

### Why Option B is refused explicitly

Option B is the option that looks like discipline, because it is what every party has correctly done so far. It is refused on evidence rather than on principle: it is the status quo, it has already produced a rule that binds one surface out of three, and the requirement that produced it stated its own silence in writing. A second and third surface reaching the same conclusion independently is not confirmation that per-surface decision works — it is two surfaces paying the cost of a rule that should have been written once, with a third yet to pay it and nothing guaranteeing it reaches the same answer.

## Consequences

### Positive

- **The single-date prohibition becomes a property of the system rather than of its current clients.** A consumer this project did not write cannot compute a mean from a payload that does not contain the values, which is the only version of Principle II's enforcement that survives contact with a client outside this repository.
- **A new surface inherits a boundary instead of re-deciding one.** E019 states its own resolution and derivation and inherits clause 1; it does not re-derive the prohibition from Principle II, and its reviewer does not have to notice that it should have.
- **The arithmetic lands where the project already tests it.** Derivation in the computation modules falls under the Testing & Quality Policy's strict test-first mandate and its property-based-test requirement for pure functions, and under the import contracts that gate the build. Derivation in a renderer would fall under none of them.
- **Resolution becomes checkable.** Fifty marks stated in the contract is a denominator a reader can count against, which is what makes E012's FR-001 claim — each mark standing for two in one hundred comparable orders — verifiable rather than asserted. A resolution buried in a client is not part of any contract.
- **Payload cost drops by roughly two orders of magnitude per line**, and the drop compounds across E019's vendor-level views, which aggregate over many lines.
- **The E010 / E012 agreement becomes structural.** Both surfaces derive from the same stored row for the same active run (E012 FR-010), server-side, so the two cannot disagree by taking different reductions of the same array.

### Negative

- **The prohibition is a resolution bound and not a guarantee of unrecoverability**, and that limit is real rather than rhetorical. A determined consumer counting to the twenty-fifth of fifty equal-probability marks has the distribution's middle. The architecture cannot prevent this without refusing to publish the distribution, which would defeat the principle it serves. This is published here rather than discovered later.
- **Some presentation changes now require a backend change.** A surface wanting a resolution the boundary does not derive cannot compute it locally; it extends the derivation server-side. That is a slower loop than reducing an array in the client, and it is the cost of clause 5.
- **Derivation code accumulates per surface.** Each new resolution is a new pure function in the computation modules with its own property tests, rather than a shared array everyone reduces differently. The total code is larger; the untested portion is smaller.
- **No repository-wide assertion enforces clause 1 today.** Each surface's own requirement carries it — E010 FR-053, E012 FR-011 with its verification hook SC-007 (*"No response backing this view carries a stored draw array, a stored day-grid array, or any central summary of the distribution"*) — but nothing scans every response schema for an array-shaped posterior field. The rule is stated at ADR level and verified per surface, which means a surface that omits its own assertion is not caught by anything. Naming this is the point: the record establishes the obligation, it does not yet establish the check.
- **A reviewer now has one more record to know about.** An epic reading a posterior must find this record to know the rule exists, and the routes to it are the SAD catalog and `specs/project-plan.md` § Architecture Decisions — the second of which records that it has silently omitted records before.

### Neutral

- **[ADR-0004](0004-materialized-posterior-draws-with-sql-side-risk-computation.md) is unaffected in every clause.** Both stored representations remain exactly as chosen there, the draw array remains the canonical checksummable unit tied to a fit run, and the day-grid array remains the read path. ADR-0004 decided what is stored and where the arithmetic runs; this record decides what may leave the boundary, which ADR-0004 did not address. The coexistence rule is satisfied because the scopes do not overlap.
- **This record narrows nothing E010 delivered.** FR-053 is already compliant and remains binding on its own surface with its own two additional clauses — no raw probability, and no numeric value in the bounded form — neither of which this record generalizes. Those clauses are about rounding and about a bounded display form, and generalizing them would be a different decision.
- **The prohibition binds the serving boundary, not the storage layer or the modeling entry.** E003 stores the arrays, E007 writes them, and the evaluation harness and offline jobs read them freely. Nothing here constrains a process that is not answering a request.
- **[ADR-0018](0018-two-anchor-distinguished-posterior-populations-in-two-tables.md)'s two posterior populations are both covered without needing enumeration**, because clause 1 binds the stored arrays as a class rather than naming tables. A third population would inherit the rule on arrival.
- **`specs/project-plan.md` § Architecture Decisions needs an `ADR-0025 | accepted | E010, E012, E019` row**, and `specs/sad.md` needs its catalog row. Both are amendments to registered documents and serialize on the default branch; this record does not perform them.
- **E019 has no requirements yet**, so this record binds it prospectively. That is the intended direction — the rule exists before the surface that would otherwise decide it.

## Links

- [ADR-0004](0004-materialized-posterior-draws-with-sql-side-risk-computation.md) — the storage decision this record complements: the sorted draw array as canonical checksummable artifact, the derived day-grid survival array as read path, and SQL-side risk computation. Unchanged by this record
- [ADR-0008](0008-deterministic-provenance-and-computation-boundary.md) — the deterministic-computation boundary that server-side derivation lands inside, and the contract that makes clause 3 mechanically meaningful
- [ADR-0018](0018-two-anchor-distinguished-posterior-populations-in-two-tables.md) — the two anchor-distinguished posterior populations, both covered by clause 1 without enumeration
- [specs/00010-risk-ranked-coordinator-worklist/spec.md](../00010-risk-ranked-coordinator-worklist/spec.md) — **FR-053** (the surface-scoped prohibition, its stated self-limitation, and the reasoning this record generalizes), **FR-052** (artifact identification), **FR-041** and **FR-007** (the single-date rules FR-053 exists to keep enforceable)
- [specs/00012-line-detail-and-traceability/spec.md](../00012-line-detail-and-traceability/spec.md) — **FR-011** (no array transmitted; fifty marks and a banded cumulative series), **FR-012** (the honest limit, conceded rather than glossed), **FR-013** (artifact identification), **FR-038** (the closed set of stated scalar figures), **FR-002** (resolution fixed against line, run, and draw count), **FR-010** (same stored row as the worklist), **SC-007** (the verification hook), and § Shared-document amendments, which names ADR-0025 as the next free number
- [specs/prd.md](../prd.md) — CAP-005 (Probabilistic Delivery Forecast), CAP-006 (Risk-Ranked Coordinator Worklist), CAP-007 (Forecast Explanation & Source Traceability), CAP-014 (Vendor Lead-Time Scorecards)
- [specs/project-plan.md](../project-plan.md) — § Shared Data Entities (`PosteriorDraws / SurvivalArray | E003 (schema), E007 (populated) | E010, E012, E019`), § Shared Interfaces (worklist read endpoint, line detail endpoint, risk-read module), § Architecture Decisions (needs an `ADR-0025 | accepted | E010, E012, E019` row)
- [specs/sad.md](../sad.md) — ADR catalog; requires a new row
- `project-instructions.md` — Principle II (*Uncertainty Is the Product*), the prohibition this record makes enforceable; Principle V (*The Model Extracts, Code Computes*), which puts probability arithmetic in deterministic testable code; Principle VII (*Publish the Miss*), which is why the resolution limit is stated rather than the guarantee asserted; § Governance, whose v1.2.11 number-claiming clause this file discharges for ADR-0025
- E010 (delivered), E012 (claiming epic), E019 (prospective) — the three registered consumers of the stored posterior arrays
