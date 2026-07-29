---
adr_id: ADR-0022
status: accepted
date: 2026-07-29
tags: [retrieval, embeddings, reranking, layout, dependency-isolation, compute-envelope]
supersedes: []
superseded_by: ""
related_artifacts: ["specs/prd.md", "CAP-003", "project-instructions.md", "ADR-0005", "ADR-0006", "ADR-0010", "ADR-0012", "ADR-0019", "specs/00008-hybrid-retrieval-and-reranking/spec.md", "specs/00006-document-ingestion-and-extraction/spec.md", "specs/00001-monorepo-scaffold-and-contracts/spec.md", "TR-003", "TR-004", "TR-013", "FR-007", "E006", "E008"]
deciders: ["Project owner", "Solution architect"]
---

# ADR-0022: Local Inference Lives in the Shared Gateway Package, and the Serving Image Admits Its Runtime

## Status

Accepted.

The encoder and the reranker session live in `/src/gateway`, the package both Python boundaries already declare. The serving image's derived denylist is extended to admit **NumPy**, on the same grounds the existing `psycopg` exclusion carries. The runtime and its tokenizer need no exclusion: the denylist derives from `/src/model`'s *direct* declarations, so relocating them to the gateway removes them from it automatically — and adding them anyway would trip the companion assertion that every excluded name is still declared by `model`.

Raised by E008, which is the first epic to need a query embedding at request time and therefore the first to hit the two constraints described below at once.

The number is claimed rather than inherited. ADR-0022 was verified free at the time of writing against both `specs/adrs/` — where the highest allocated number on disk was `0021` — and `specs/sad.md`'s catalog, which runs to the same number and reserves nothing beyond it.

The corresponding `specs/sad.md` catalog row is **not written by this record**. It is recorded as a pending amendment, because `sad.md` is a registered document whose amendments Governance serializes onto the default branch.

## Context

`specs/sad.md` places the Retrieval component and the Reranker Session inside the **API** container. {SAD:ADR-0006} requires an integer-quantized cross-encoder loaded in that container, and {SAD:ADR-0019} requires the query encoder to run there too, on the same ONNX Runtime the reranker already loads.

{SAD:ADR-0010} established four entries under `/src` — three boundaries plus a shared gateway package. Its load-bearing property is that **neither Python boundary depends on the other**: `/src/api` and `/src/model` each declare `gateway` as a path dependency and neither declares the other. That non-dependency is what makes a single provider import site achievable without a directional coupling, and E001's TR-002 asserts it directly.

Two consequences of those records collide, and E008 is the first epic to hit them.

**First: `/src/api` cannot import `model`.** Its declared dependencies are `fastapi`, `gateway`, `psycopg`, `uvicorn` — and TR-002 forbids adding `model` to that list. But retrieval needs a **query embedding**, and the encoder — ONNX session, attention-masked mean pooling, L2 normalization — lives in `model.ingest.embed`, authored by E006. {SAD:ADR-0019} already recorded why this matters more than ordinary code reuse: a query vector must occupy the same space as the corpus vectors, and if it does not, nothing errors. Retrieval simply degrades, permanently, with worse ranking as the only symptom and no assertion anywhere in a position to notice. E008's FR-007 requires the query to be embedded with the same pinned model identity the chunks were embedded with, and refuses to retrieve on an identity mismatch — but identity is metadata, and identical metadata over a second implementation of pooling is exactly the case FR-007 cannot see.

E006 refused a second page reader for this class of reason and asserts its absence with a source scan (`src/model/tests/ingest/test_single_page_reader.py`). The precedent is to make the second implementation impossible, not to detect it after the fact.

**Second: the serving image forbids the inference runtime.** `tests/checks/helpers/image_contents.py` derives TR-013's denylist as `declared - first_party - SHARED_INFRASTRUCTURE`, where `declared` is `/src/model`'s **directly declared** dependencies, `first_party` is `tool.uv.sources` (which is how `gateway` is excluded, after STF-001 was filed about exactly that), and `SHARED_INFRASTRUCTURE` is `frozenset({"psycopg"})`. E006 added `onnxruntime` and `tokenizers` to `/src/model`, and `numpy` was already there. All three are on the denylist today, and `onnxruntime` pulls `numpy` transitively — so admitting the runtime admits NumPy whichever route the code takes.

The constant is mirrored. A second `SHARED_INFRASTRUCTURE` in `tests/checks/test_dependency_isolation.py` serves TR-003 and TR-004 by the same subtraction, and TR-003 forbids the **gateway's** resolved set from carrying any distribution of the modeling stack. So the collision is not confined to the image: putting the runtime in the gateway trips the gateway's own isolation check unless the same term is extended there too.

The existing `psycopg` exclusion carries both the precedent and the reasoning verbatim: *"The serving image is not merely permitted to carry the driver — it is required to."* The driver is declared by `model` while belonging to no boundary in particular, and without the subtraction the denylist forbids the image the thing the design requires it to have. An inference runtime that {SAD:ADR-0006} puts in the serving container is the same shape of fact.

A decision is needed now because E008 cannot implement hybrid retrieval against a layout that contradicts an accepted record, and cannot build a serving image that a shipped check forbids.

## Decision Drivers

- One implementation of pooling and normalization, so corpus and query vectors cannot drift — the failure being silent, permanent, and invisible to every assertion in the system
- Preserving {SAD:ADR-0010}'s load-bearing property: neither Python boundary depends on the other
- The serving image being able to carry what {SAD:ADR-0006} requires it to run
- Keeping the entry count under `/src` unchanged, since every entry costs a manifest, a lockfile, and a CI tier
- Keeping whatever TR-013 still protects **derived** rather than hand-listed, so the narrowing is bounded and visible

## Considered Options

### Option A: Encoder and reranker session in the shared gateway package, with the derived denylist extended to admit the runtime

The encoder and the reranker session move to `/src/gateway`. `/src/model` calls it for corpus embedding; `/src/api` calls it for query embedding and reranking. `SHARED_INFRASTRUCTURE` — in both mirrored locations — is extended to admit **NumPy alone**, with the same reasoning `psycopg` carries. An earlier draft of this record said "the inference runtime, its tokenizer, and NumPy"; implemented literally that fails the build, because the denylist derives from `/src/model`'s *direct* declarations and the first two leave it of their own accord once relocated, at which point excluding them trips the staleness assertion that every excluded name is still declared by `model`. NumPy stays declared for PyMC and pandas, so it is the only name that needs admitting.

- **Pros**:
  - Pooling and normalization exist once, so the two vector spaces are identical by construction rather than by test — the property {SAD:ADR-0019} chose Option A to obtain, extended from one runtime to one implementation
  - `/src/api` reaches query embedding without declaring `model`, so {SAD:ADR-0010}'s non-dependency and E001's TR-002 survive verbatim
  - The entry count under `/src` is unchanged: no fifth manifest, lockfile, or CI tier
  - One ONNX Runtime in the serving container serves both the reranker session and the query encoder, so {SAD:ADR-0019}'s "one runtime, not two" premise is preserved by construction rather than by discipline
  - The exclusion mechanism already exists, is already derived rather than hand-listed, and already carries a written justification for exactly this case
  - Follows E006's precedent for a shared implementation with a silent failure mode, rather than inventing a second enforcement style
- **Cons**:
  - NumPy is admitted, so TR-013's derived protection narrows from four heavy packages to three
  - The gateway's scope widens from "the sole provider import" to "the sole provider import **and** the shared local-inference surface"
  - The gateway is no longer minimal in the sense {SAD:ADR-0010} recorded; a change to the encoder now affects the offline pipeline and the request path together
  - `/src/model` stops declaring the runtime and tokenizer directly, which silently changes what the denylist denies

### Option B: Reimplement query embedding in `/src/api`, assert parity by test

`/src/api` grows its own encoder against the same vendored artifact, and a test asserts its output matches `model.ingest.embed` within a tolerance.

- **Pros**:
  - No change to the gateway's scope, and `/src/model` keeps declaring the runtime directly
  - No change to `SHARED_INFRASTRUCTURE` for the *tokenizer*, since the parity test could in principle live on the host
  - Each boundary stays self-contained in the sense E001's TR-002 describes
- **Cons**:
  - It is a second implementation of the same arithmetic, and E006 refused exactly this for the page reader
  - A parity test catches drift only after someone introduces it, and only for the inputs the test happens to hold — the failure mode is a systematic difference on inputs nobody fixtured
  - The serving image still needs the runtime for the reranker under {SAD:ADR-0006}, so the denylist collision is not avoided, only the gateway half of it
  - The parity test is a permanent obligation on every future change to either copy, enforced by discipline, which is the enforcement model this project rejects everywhere else

### Option C: A fifth entry under `/src` for shared inference

A new first-party package holding the encoder and the reranker session, declared by both Python boundaries alongside the gateway.

- **Pros**:
  - The gateway's recorded scope is untouched, so {SAD:ADR-0010} reads unchanged
  - Inference dependencies are isolated from the provider client, so the gateway's resolved set stays free of the runtime
  - Still one implementation, so the drift property is obtained
- **Cons**:
  - Supersedes {SAD:ADR-0010}'s deliberate four-entry choice for a benefit the gateway already provides — both boundaries already declare the gateway, and adding a second shared package buys nothing the first does not
  - Every entry costs a manifest, a lockfile, and a CI tier
  - The denylist and TR-003 exclusions are needed anyway, since the new entry would be first-party by the same derivation rule and its runtime would still reach the serving image

### Option D: Reranker as its own container

The reranker, and by extension the encoder, run as a separate service behind a local interface.

- **Pros**:
  - The serving image carries no inference runtime, so TR-013's protection is untouched
  - Clean boundary; the encoder exists once, in one place
- **Cons**:
  - Already considered and rejected by {SAD:ADR-0006} as Option C — it relocates the memory rather than removing it and adds a third service for a single-user demo
  - Choosing it now would mean **superseding** {SAD:ADR-0006}, not deviating from it, and nothing in E008 has produced a fact that record did not already weigh
  - Reintroduces a service boundary {SAD:ADR-0003} removed and {SAD:ADR-0019} relied on staying removed

### Option E: Leave the denylist untouched and put nothing new in the serving container

No query embedding at request time; retrieval runs on the lexical arm alone.

- **Pros**:
  - TR-013's protection is untouched in full
  - No change to any accepted record
- **Cons**:
  - It abandons hybrid retrieval, which is the epic — CAP-003's dense arm, {SAD:ADR-0005}'s fused candidate set, and {SAD:ADR-0006}'s reranking over it all cease to exist
  - The serving container already carries the runtime for the reranker under {SAD:ADR-0006}, so the denylist is already contradicted by an accepted decision; leaving it untouched preserves the contradiction rather than the protection

## Decision Outcome

Chosen option: **Encoder and reranker session in the shared gateway package, with the derived denylist extended to admit the runtime** — both parts, and both are needed.

The placement decides itself once the failure mode is named. `/src/api` cannot import `model`, and the thing it needs from `model` is the one piece of arithmetic in the retrieval path whose divergence produces no error. Option B's parity test is the only alternative that keeps the code where it is, and it converts a structural guarantee into a detection mechanism for precisely the class of defect E006 established the precedent against. The gateway is the one package both boundaries already import; putting the encoder there costs no new entry and makes the second implementation unrepresentable rather than merely tested for.

Option C obtains the same property at the price of superseding {SAD:ADR-0010}'s four-entry choice, and buys nothing with it — the gateway is already declared by both consumers, so a fifth entry adds a manifest, a lockfile, and a CI tier to reach a place already reachable. Option D is not a live option: {SAD:ADR-0006} weighed and rejected it, and choosing it now would be a supersession dressed as a deviation. Option E abandons the epic.

The denylist extension is not a separate convenience; it is what makes the placement buildable. TR-003 forbids the gateway's resolved set from carrying the modeling stack, and that stack is derived from what `/src/model` declares — which today includes `onnxruntime`, `tokenizers`, and `numpy`. Without extending the term, putting the runtime in the gateway fails the gateway's own isolation check, and shipping it in the serving image fails TR-013. Both checks would be reporting a violation of a rule that {SAD:ADR-0006} already overrode when it put an ONNX session in the serving container.

The reasoning is `psycopg`'s, unchanged: the serving image is not merely permitted to carry the inference runtime — it is *required* to, because {SAD:ADR-0006} loads a cross-encoder session in it and {SAD:ADR-0019} embeds the query on the same runtime. What is being admitted is shared infrastructure that `/src/model` happens to declare, not modeling stack.

The cost is stated plainly below and is not softened: NumPy leaves the set of things the serving image is *guaranteed* to exclude. That is a real reduction, it is the price of {SAD:ADR-0006}, and it is paid deliberately here rather than discovered later.

## Relationship to `project-instructions.md`

This decision **contradicts a clause of the governing document** and does not proceed on the
strength of being an ADR. §Source Code Layout reads *"The gateway package carries neither a web
framework nor the modeling stack"*, and §Technology Stack defines that stack as PyMC, ArviZ, pandas
and NumPy. The inference runtime pulls NumPy transitively, so placing it in the gateway puts the
gateway's resolved set in breach of that sentence.

A decision record cannot override a clause of the document that governs decision records.
`specs/00008-hybrid-retrieval-and-reranking/plan.md` §Pending Amendments therefore raises an
amendment against §Source Code Layout, to except a shared inference runtime from that prohibition,
and gates implementation on it landing. If the amendment is refused, this record is wrong and the
alternative is a fifth entry or a separate reranker container — both weighed below.

Recorded here rather than only in the plan because a reader arriving at this ADR alone would
otherwise have no way to know the decision was conditional.

## Consequences

### Positive

- **Pooling and normalization exist once.** The corpus encoder and the query encoder are the same code over the same weights, so {SAD:ADR-0019}'s "identical by construction rather than by coincidence" extends from one runtime to one implementation. The drift it named as invisible becomes unrepresentable rather than merely asserted against.
- `/src/api` obtains query embedding without declaring `model`. {SAD:ADR-0010}'s load-bearing non-dependency and E001's TR-002 are untouched, and the two boundaries' resolutions stay genuinely independent rather than directionally coupled.
- The serving image can carry what {SAD:ADR-0006} requires it to run. The checks stop forbidding the design, and they do so through the existing derivation rule rather than a new exception mechanism.
- The entry count under `/src` is unchanged. No fifth manifest, lockfile, or CI tier.
- One ONNX Runtime in the serving container serves both the reranker session and the query encoder. {SAD:ADR-0019}'s premise — that the unpriced runtime term in {SAD:ADR-0012}'s memory arithmetic is paid once, not twice — is now preserved by the layout rather than by two callers independently choosing the same library.

### Negative

- **TR-013's protection narrows.** It currently derives PyMC, ArviZ, pandas and NumPy. After this, NumPy is admitted and the protection covers PyMC, ArviZ and pandas. That is a real reduction in what the serving image is *guaranteed* to exclude — NumPy could henceforth arrive by a route nobody intended and no check would report it — and it is the price of {SAD:ADR-0006}.
- **Two committed guards fail on this change, and both are doing their job.** `tests/checks/test_dependency_isolation.py::test_the_shared_infrastructure_exclusion_cannot_hide_the_modeling_stack` asserts that `{"pymc", "arviz", "pandas", "numpy"}` is disjoint from `SHARED_INFRASTRUCTURE`, and separately that every member of `SHARED_INFRASTRUCTURE` is still declared by `model`. Admitting NumPy trips the first; admitting `onnxruntime` and `tokenizers` after `/src/model` stops declaring them trips the second. The implementing epic must reconcile both **deliberately and with the reasoning recorded at the constant**, narrowing the heavy set to `{"pymc", "arviz", "pandas"}` rather than silencing the guard. A guard weakened without a written reason is the failure mode the guard exists to prevent.
- **The gateway package's scope widens** from "the sole provider import" to "the sole provider import **and** the shared local-inference surface". Both are boundary-crossing concerns belonging to neither Python boundary, which is the coherent reading — but it is a widening, and {SAD:ADR-0010} should be read alongside this record. Its neutral claim that the gateway "acquires no framework or modeling dependency" and is "deliberately minimal — provider client, schema validation, invocation recording" is now qualified by this record and is no longer a complete description.
- **`/src/model` stops declaring the runtime and tokenizer directly**, inheriting them through the gateway. Note the second-order effect: TR-013's denylist is derived from *direct* declarations, so moving the code changes what is denied even before the exclusion term is touched. Stated explicitly because **a future reader who moves the encoder back into `/src/model` silently re-forbids the runtime in the serving image** — the image build would begin failing a check nobody edited, for a reason located in a manifest rather than in the check.
- The gateway becomes a shared component whose interface change affects the offline pipeline and the request path together, and it now ships a multi-tens-of-megabyte native runtime rather than a client library. Any environment that installs the gateway installs the runtime.

### Neutral

- **Session lifecycle is the caller's, not the module's.** There is one session per process, and the gateway is imported by an offline job (`model.ingest`) as well as by a long-lived serving process. A module-scope session would load in whichever process imports the module — including processes that embed nothing — and would load twice as readily as once. The gateway therefore exposes session construction and the caller owns the lifetime: the serving process loads at startup and warms before the readiness gate opens, per {SAD:ADR-0006}; the offline job constructs per run and releases at exit.
- {SAD:ADR-0006}'s explicit thread-configuration obligation is unchanged and unmoved. {SAD:ADR-0019} already extended it to two sessions in one process; this record changes where those sessions are constructed, not that their thread counts must be set explicitly because ONNX Runtime cannot see the container's CPU allocation.
- {SAD:ADR-0012} is untouched. The model class, the 384 dimensions, the L2 normalization, and the `vector(384)` column literal are unchanged — this record moves code, not vectors. {SAD:ADR-0019} is likewise untouched on its substance: it fixed the runtime for both paths, and this record fixes where the single implementation of that runtime's surrounding arithmetic lives.
- E008's FR-007 is unaffected in substance — the query is still embedded with the pinned identity and still refuses to retrieve on mismatch — but the identity check is now a second line of defence behind a structural guarantee rather than the only thing standing between the two vector spaces.
- The two mirrored `SHARED_INFRASTRUCTURE` constants remain mirrored. They are extended together and for the same reason; a change to one without the other reintroduces the contradiction this record resolves, in whichever half was left behind.
- {SAD:ADR-0006}'s degradation clause is unchanged: if the model fails to load, the system degrades to fusion-only ordering and flags the degraded mode rather than silently serving worse results under published reranked numbers.

## Links

- [specs/prd.md](../prd.md) — CAP-003 (Evidence Retrieval), whose dense arm this record makes buildable
- [ADR-0005](0005-exact-vector-search-for-evaluation-approximate-for-serving.md) — establishes the fused candidate set the query embedding feeds and the reranker reorders
- [ADR-0006](0006-local-quantized-cross-encoder-reranker.md) — puts the INT8 cross-encoder session in the serving container; its Option C is the separate-container option this record declines to reopen, and its envelope and thread-configuration obligations carry forward
- [ADR-0010](0010-source-layout-with-a-shared-gateway-package.md) — the four-entry layout and the shared gateway package; its non-dependency property is preserved and its "deliberately minimal" characterization of the gateway is widened by this record
- [ADR-0012](0012-embedding-model-and-vector-dimension.md) — the encoder class, `EMBEDDING_DIM = 384`, and the request-time memory envelope, all unchanged here
- [ADR-0019](0019-embedding-runtime-pinned-to-onnx-runtime.md) — pins ONNX Runtime for corpus and query embedding and records why divergent pooling is an invisible failure; this record places the single implementation that decision assumes
- [specs/00008-hybrid-retrieval-and-reranking/spec.md](../00008-hybrid-retrieval-and-reranking/spec.md) — E008; FR-007 requires the query encoder's identity to match the chunks', FR-015 reranks the fused set
- [specs/00006-document-ingestion-and-extraction/spec.md](../00006-document-ingestion-and-extraction/spec.md) — E006; authors `model.ingest.embed`, declares `onnxruntime` and `tokenizers`, and sets the single-implementation precedent asserted by `src/model/tests/ingest/test_single_page_reader.py`
- [specs/00001-monorepo-scaffold-and-contracts/spec.md](../00001-monorepo-scaffold-and-contracts/spec.md) — E001; TR-002 (neither Python boundary declares the other), TR-003 and TR-004 (the gateway and serving resolutions carry no modeling stack), TR-013 (the in-image denylist this record narrows), and STF-001 (the first-party derivation rule the exclusion mechanism rests on)
- [specs/sad.md](../sad.md) — ADR catalog; requires a new row, recorded as a pending amendment rather than written here
