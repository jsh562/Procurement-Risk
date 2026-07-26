---
feature_branch: "00002-public-corpus-and-manifest"
created: "2026-07-25"
input: "e002"
spec_type: "product"
spec_maturity: "clarified"
epic_id: "E002"
epic_sources: "{PRD:CAP-001}"
---

# Feature Specification: Public Corpus and Manifest

**Feature Branch**: `00002-public-corpus-and-manifest`  
**Created**: 2026-07-25  
**Status**: Draft  
**Spec Type**: product  
**Spec Maturity**: clarified  
**Epic ID**: E002  
**Epic Sources**: {PRD:CAP-001}  
**Product Document**: `specs/prd.md`

## Problem Statement *(mandatory)*

Every downstream capability in this product — parsing, extraction, retrieval, identity resolution, and every published metric — reads from a document corpus that does not yet exist. Matched specification → submittal → purchase-order triples for a single project cannot be obtained from public sources, so the corpus must combine genuinely public-domain federal specifications with a synthesized project-document layer, and a reader must be able to tell which is which. Without a per-document provenance record, the project's central claim — that a coordinator can trace any number back to its source — has no foundation to rest on, and a technical evaluator has no way to audit what is real and what was generated. Assembling documents without that record is worse than not assembling them, because it invites trust the corpus has not earned.

## Scope *(mandatory)*

### Included

- Retrieval and verbatim vendoring of public-domain federal guide specification sections, weighted toward long-lead equipment
- Per-location corpus manifests carrying provenance appropriate to each layer, expressed as JSON against a committed schema, with validation that fails on any absent field
- A corpus location layout under `data/` in which every location carries exactly one license basis and exactly one manifest
- A seeded, re-runnable generator for the synthesized submittal and transmittal layer, living in the modeling boundary and bound to the E001 project/vendor roster, with its output committed
- A datasheet disclosing the synthetic layer's generative assumptions
- Deliberate formatting irregularity in the synthetic layer, recorded per document so downstream results stay reportable per layer
- Corpus validation executed automatically by the verification workflow, including detection of roster drift against already-generated documents
- A `pull_request` trigger on the verification workflow, closing the automatic-triggering deviation E001 carried forward

### Excluded

- Parsing, chunking, page-metadata capture, and persistence — E006 owns ingestion; this epic ships files, not rows
- Any database schema for documents or manifest entries — E003 owns the single-store schema in full
- Procurement lifecycle data — purchase-order lines, events, need-by dates, and criticality are E005's; this epic ships documents only, and synthesizes no purchase-order document
- Frozen evaluation sets and golden questions — E014 owns evaluation-set integrity, and mixing them into corpus assembly would put the test set in reach of corpus tuning
- Branch protection on the default branch — a hosting-platform setting configured outside the repository, which no committed artifact can assert. This is an open deviation from the project's CI requirement that checks pass before merge: its cause is that the setting lives outside the repository boundary, its owner is the repository administrator, and it closes when the verification check is marked required on the default branch and that state is evidenced in a release record.
- Any language-model invocation during generation — the traced gateway is E004 and E002 precedes it, so a model call here would breach both the epic sequencing and the single-invocation-path constraint
- Non-federal and international specification sources — each would need its own license basis established per jurisdiction for no added coverage of long-lead equipment
- Size, weight, page-count, and validation-runtime bounds — none are stated, by explicit decision. The consequence is recorded rather than discovered: committing 45 or more binaries with no weight ceiling leaves clone cost unbounded, and running corpus validation on every push and pull request with no wall-clock budget leaves CI cost unbounded. Accepted as an exposure at demonstration scale; it reverses if clone or CI time becomes a delivery obstacle, at which point bounds are set rather than the checks removed.
- Large-file storage indirection for `data/` — the corpus is committed to the repository directly, because a clone that has not fetched from a separate store would not hold the corpus, which is what SC-014 exists to guarantee

### Edge Cases & Boundaries

- A targeted specification section is withdrawn or unretrievable at assembly time — the shortfall is published with its cause, not silently backfilled with a substitute section
- Two agency variants of the same MasterFormat number are retrieved — both are vendored as separate documents, but the section counts once toward coverage
- A candidate document's license basis cannot be established from its source — the document is excluded and the exclusion is recorded with its reason
- A vendored section contains an excerpt of a copyrighted reference standard at point of use — the section is excluded rather than included with a hedge
- A generated document has no upstream source to cite — its record carries generator identity, seed, generation date, and roster hash in place of a retrieval location and issuing body, never a fabricated one
- The roster changes after synthetic documents have been generated from it — validation compares each recorded roster hash against the reader's current value, names every stale document, and fails; there is no reconciliation, only regeneration
- A manifest entry names a file that does not exist, or a corpus file has no manifest entry — both are validation failures, not warnings
- A generated document names a project or vendor absent from the roster — generation fails rather than emitting the document
- Retrieval date and document revision date differ — both are recorded, and neither substitutes for the other
- Injected scan degradation renders a page hard to read — degradation is bounded so every page's citation anchor stays machine-recoverable, since an uncitable page cannot serve the product's traceability claim
- The corpus totals fewer documents than targeted after exclusions — the count and its causes are published rather than the target being lowered to match

## User Scenarios & Testing *(mandatory for product specs only)*

### User Story 1 - Vendor the public-domain specification layer (Priority: P1)

A developer assembling the corpus retrieves federal guide specification sections covering the equipment whose lead times actually constrain a construction schedule — medium- and low-voltage switchgear, transformers, unit substations, generators, and the comparable mechanical sections — and commits them into the repository exactly as retrieved, keeping their genuine formatting irregularity intact. Each section is identified by its MasterFormat number, its agency variant, and its revision date, because the same number exists as several separately dated documents. Sections whose license basis cannot be established, or that reproduce a copyrighted reference standard rather than citing it, are left out and the omission is written down.

**Why this priority**: Core value proposition — the real layer is what carries genuine document messiness, and no downstream extraction, retrieval, or evaluation work can begin without documents to read.

**Independent Test**: Point a reviewer at the vendored corpus location and confirm the sections cover the long-lead divisions, that each file is unmodified from its source, and that every exclusion has a recorded cause.

**Acceptance Scenarios**:

1. **Given** a target list of long-lead specification sections, **When** the real layer is assembled, **Then** the vendored documents span Division 26 and Division 23 long-lead equipment and include the submittal-procedures section that anchors document structure.
2. **Given** a vendored section, **When** its content hash is compared against the upstream digest its manifest entry recorded at retrieval, **Then** the two are equal, so the verbatim claim is checkable offline after the source is no longer reachable.
3. **Given** a candidate section that reproduces a copyrighted reference standard at point of use, **When** the license check runs, **Then** the section is excluded and the exclusion is recorded with its cause.
4. **Given** two agency variants of one MasterFormat number, **When** both are vendored, **Then** the real layer's manifest shows two documents with distinct agency variants and revision dates, and the section counts once toward coverage.

### User Story 2 - Record and validate per-document provenance (Priority: P1)

An evaluator auditing the corpus opens the manifest set — one manifest per corpus location, with no aggregate index above them — and, without opening a single document or reading any code, determines for every file what layer it belongs to, what makes it legally clean, and — for a retrieved document — where it came from, who issued it, and when. Generated documents carry a different record, because they have no issuing body and no retrieval date: theirs names the generator, its seed, the generation date, and the roster hash they were built from. Documents are grouped into corpus locations so a single location never mixes license bases, and each location carries its own manifest. Provenance fields are required rather than optional: a missing license basis fails validation instead of quietly defaulting to something that reads the same downstream as a verified one, and a generated document may not borrow a retrieval record it does not have.

**Why this priority**: Core value proposition and a non-negotiable project constraint — an unattributable document is a defect, and the manifest set is the artifact that makes the whole corpus auditable.

**Independent Test**: Run the corpus validation against the assembled corpus and confirm it passes, then remove one required field and confirm it exits non-zero naming the offending document and rule.

**Acceptance Scenarios**:

1. **Given** the assembled corpus, **When** validation runs, **Then** every document has exactly one manifest entry, every entry names an existing file, and every manifest parses against the committed JSON schema.
2. **Given** a manifest entry with an empty license basis, **When** validation runs, **Then** it fails and names the document and the missing field rather than substituting a default.
3. **Given** a REAL entry whose license basis names a statute but omits the document identifier with its revision date or the point-of-use copyright-check outcome, **When** validation runs, **Then** it fails and names the missing component.
4. **Given** a SYNTHETIC entry carrying a retrieval location or an issuing body, **When** validation runs, **Then** it fails, because a generated document has no such origin to record.
5. **Given** a SYNTHETIC entry whose license basis does not state that this project generated the document and that it carries no third-party rights, **When** validation runs, **Then** it fails on the missing statement.
6. **Given** a corpus location containing documents under two different license bases, **When** validation runs, **Then** it fails on the mixed-license condition.
7. **Given** a manifest entry whose layer label is neither REAL nor SYNTHETIC, **When** validation runs, **Then** it fails on the closed-enum condition.
8. **Given** the assembled corpus, **When** validation runs, **Then** every corpus location, every manifest, and the datasheet resolve under `data/`, and an entry naming a file outside `data/` fails.
9. **Given** the manifest set alone, **When** an evaluator reads it, **Then** every document's layer and license basis are determinable, and for REAL documents also its source location, issuing body, retrieval timestamp, upstream digest, MasterFormat section, agency variant, and revision date — without opening the document.
10. **Given** a commit that introduces a provenance defect, **When** the verification workflow runs on the push or pull request, **Then** corpus validation fails as part of that run rather than waiting for someone to invoke it.
11. **Given** the opt-in re-verification job, **When** it is invoked on demand, **Then** it re-fetches the recorded sources and reports any divergence from the recorded upstream digests — and it appears in no per-push workflow run, so no required check depends on the network.

### User Story 3 - Generate the project-document layer from the roster (Priority: P1)

A developer runs a generator that reads the committed project and vendor roster through the single reader E001 established, and emits submittal packages and transmittal cover sheets for the planned projects. The documents are structurally faithful to real ones — they carry a transmittal number, the specification section they answer, a submittal descriptor code, an approving-authority marker, a revision suffix, and a reviewer action stamp — because a document missing those fields gives downstream extraction nothing to extract. The material items they name are drawn from the equipment the vendored real layer specifies, so a specification-to-submittal link is there to be found. Every generated document and its manifest entry record the roster's content hash, so a later roster change is detectable rather than silent. Generation is deterministic from a committed seed, touches no network, and invokes no language model, and its output is committed so a fresh clone holds the corpus without running anything.

**Why this priority**: Core value proposition — the synthesized layer is the only way to obtain matched specification-to-submittal chains for one project, and E006, E008, and E009 all read it.

**Independent Test**: Run the generator twice from a clean checkout with the committed seed and confirm an identical document-model hash for every document, with byte-identical rendered files under the lockfile-pinned renderer and an unchanged manifest set; then confirm every generated document names only roster projects and vendors, references only real-layer equipment categories, and records the roster hash.

**Acceptance Scenarios**:

1. **Given** the committed roster and seed, **When** the generator runs twice, **Then** both runs produce an identical document-model hash for every document, and under the lockfile-pinned renderer the rendered files are byte-identical as well.
2. **Given** a generated submittal, **When** its content is inspected, **Then** it carries a transmittal number, a referenced specification section, a submittal descriptor code, an approving-authority marker, a revision suffix, and a reviewer action stamp.
3. **Given** the generated layer, **When** projects and vendors are tallied, **Then** all five projects and all twelve vendors appear, and each project carries at least one resubmittal chain sharing a submittal number across an incremented revision suffix and a changed action code.
4. **Given** a generated submittal, **When** each of its material items is checked against the vendored real layer, **Then** every item's equipment category maps to a specification section present in that layer.
5. **Given** a roster whose content has changed since generation, **When** corpus validation runs, **Then** it compares each recorded roster hash against the reader's current value, names every stale document, and fails.
6. **Given** the generator, **When** it runs, **Then** it makes no network request and no model invocation, and the run completes offline.
7. **Given** a fresh clone in which the generator has never run, **When** the corpus is listed, **Then** the complete synthetic layer is present, in one corpus location per roster project, each with its manifest.
8. **Given** the generated layer, **When** its datasheet is read, **Then** it discloses motivation, composition, generation process, preprocessing, intended uses, distribution, maintenance, and stated limits — including that the transmittal codes and field labels are a documented approximation rather than a reproduction of a live form — without reference to the generator's source code.
9. **Given** the repository, **When** the architecture contracts run, **Then** the generator resolves under `/src/model` and no entry outside it opens the roster.
10. **Given** an unchanged seed and roster, **When** the generator is re-run, **Then** the manifest set is byte-identical to its committed state, because the generation date is a committed constant rather than a wall-clock reading.
11. **Given** any corpus document from either layer, **When** its format is checked, **Then** it is a PDF.

### User Story 4 - Keep the synthetic layer honestly messy (Priority: P2)

The generated documents carry the irregularity that real submittal packages have — layouts that differ from vendor to vendor, blank and missing fields, field labels that disagree between documents, dates recorded out of order, fields split across a page break, and pages that look scanned rather than born-digital. Degradation stops short of destroying a page's citation anchor, because a page nothing can cite is useless to a product built on traceability. Each document's manifest entry records which irregularity classes it carries, so a downstream result can be reported against the conditions it was measured under rather than pooled into a single flattering number.

**Why this priority**: Significant value but the MVP works without it — a structurally faithful clean layer already unblocks every downstream epic, while irregularity is what keeps the extraction results from overstating themselves.

**Independent Test**: Sample the synthetic layer and confirm no two vendors share a layout template, that each document's recorded structural irregularity classes match what validation independently re-derives from it, that the injector unit tests covering scan degradation pass, and that every degraded page's citation anchor is still machine-recoverable.

**Acceptance Scenarios**:

1. **Given** the synthetic layer, **When** layouts are compared across vendors, **Then** no single template spans all vendors.
2. **Given** a document whose manifest entry records a structural irregularity class, **When** validation re-derives that document's structural classes from the emitted file, **Then** the derived set equals the recorded set, and any disagreement fails.
3. **Given** a page carrying injected scan degradation, **When** text is extracted from it, **Then** the full page text is recoverable without optical character recognition, and the page number and document identifier come back undegraded.
4. **Given** the manifest set, **When** results are partitioned by layer and irregularity class, **Then** the partition is derivable from recorded fields rather than from filenames or inspection.
5. **Given** the datasheet, **When** its stated limits are read, **Then** they disclose that the retained text layer carries no recognition error, so the corpus evidences no robustness to genuine scan noise.
6. **Given** the injector unit tests, **When** the test suite runs, **Then** it covers all five irregularity classes — the evidence path for scan degradation, which no structural re-derivation can confirm — and passes.

### User Story 5 - Verify pull requests automatically (Priority: P2)

A developer opens a pull request and the verification workflow runs on it without anyone dispatching it, reporting each check's status against the proposed merge. E001 landed the `push` half of this during its analyze phase; the remaining gap is that a branch's contract violations are only caught after the branch is pushed, not as a gate on the merge itself.

**Why this priority**: Enhances an existing P1 flow — push triggering already fails the build on a contract violation, so this closes the last automatic-triggering gap rather than establishing the capability.

**Independent Test**: Open a pull request containing a deliberate architecture-contract violation and confirm the workflow runs unprompted and reports a failing check.

**Acceptance Scenarios**:

1. **Given** the verification workflow, **When** a pull request is opened against the default branch, **Then** the workflow runs automatically and reports per-check status.
2. **Given** a pull request whose head violates an architecture contract, **When** the workflow runs, **Then** the check fails and names the violated contract.

## Requirements *(mandatory)*

### Functional Requirements *(product specs only)*

- **FR-001**: System MUST vendor public-domain federal guide specification sections into the repository as the published PDF, byte-for-byte — no reformatting, re-typesetting, re-encoding, or content edit, and no substitution of a structured source format.
- **FR-002**: System MUST weight the real layer toward long-lead equipment sections in MasterFormat Division 26 and Division 23, and MUST include the submittal-procedures section that defines descriptor codes and approval markers.
- **FR-003**: System MUST identify each real document by MasterFormat section number, agency variant, and revision date together; two agency variants of one number are two documents.
- **FR-004**: System MUST exclude any candidate document whose license basis cannot be established from its source, and MUST record each exclusion with its cause.
- **FR-005**: System MUST cite copyrighted reference standards by designation and title only, and MUST exclude any document that reproduces such a standard's text at point of use.
- **FR-006**: System MUST record exactly one manifest entry per corpus document, and MUST fail validation on an entry without a file or a file without an entry.
- **FR-006a**: Manifests MUST be JSON, one per corpus location, validated against a committed schema; there is a manifest set rather than a single aggregate manifest, and no derived index is maintained.
- **FR-007**: Every manifest entry MUST carry the common field set: the document's corpus-relative location, its layer label, its license basis, and a content hash over the committed file's bytes.
- **FR-008**: A REAL entry MUST additionally carry source location, the response status observed at retrieval, retrieval timestamp, issuing body, MasterFormat section number, agency variant, document revision date, and a digest of the upstream bytes as retrieved.
- **FR-008a**: A REAL entry's content hash MUST equal its recorded upstream digest, and validation MUST fail on any divergence — this is what makes the verbatim-vendoring claim checkable offline after the source is no longer reachable.
- **FR-008b**: System MUST provide an opt-in re-verification job that re-fetches recorded sources and compares them against the recorded upstream digests. It MUST NOT run as part of the per-push verification, because a required check may not depend on the network.
- **FR-009**: A SYNTHETIC entry MUST additionally carry generator identity, the committed seed, generation date, the roster hash, its document-model hash, and its irregularity classes; it MUST NOT carry a source location, issuing body, retrieval timestamp, upstream digest, or third-party revision date, because a generated document has no such origin and recording one would be a provenance falsehood.
- **FR-009a**: The generation date MUST be a constant committed alongside the seed rather than a wall-clock reading, so that a re-run under an unchanged seed and roster does not rewrite the manifest set.
- **FR-010**: System MUST treat every field in an entry's applicable set as required, failing validation on an absent or empty value rather than applying a default.
- **FR-011**: A REAL license basis MUST state the governing statute or license identifier, the document identifier with its revision date, and the outcome of the point-of-use copyright check; validation MUST fail when any of the three is missing.
- **FR-012**: A SYNTHETIC license basis MUST state that the document was generated by this project and carries no third-party rights.
- **FR-013**: System MUST maintain one manifest per corpus location and MUST fail validation when a single location contains documents under more than one governing license basis. The comparison is over the basis identifier — the statute or licence the documents rest on — and not over the per-document components FR-011 requires, which necessarily differ from one document to the next; comparing whole license bases would make every real location illegal by construction.
- **FR-014**: System MUST treat the layer label as a closed set of exactly REAL and SYNTHETIC, failing validation on any other value.
- **FR-015**: Corpus validation MUST be repeatable on demand, exit non-zero on failure, and name both the offending document and the violated rule.
- **FR-016**: Corpus validation MUST compare each SYNTHETIC entry's recorded roster hash against the value the roster reader currently emits, and MUST fail naming every stale document on a mismatch.
- **FR-017**: Corpus validation MUST execute as part of the verification workflow on every push that triggers that workflow and on every pull request, not only on demand. The workflow's existing path filter and ref-scoped run cancellation are carried forward from E001 as intended behaviour; this epic does not remove them to manufacture a literal every-push claim.
- **FR-017a**: The synthetic layer MUST occupy one corpus location per roster project — five locations, each with its own manifest — and every generated document MUST reside in the location of the project it belongs to.
- **FR-018**: Corpus locations, their manifests, and the synthetic layer's datasheet MUST live under `data/`. The generator and the corpus validator MUST both live in the modeling boundary under `/src/model` — the validator ships as a module with a console entry point that the verification workflow invokes — because both must read the roster and `/src/model` is the only entry permitted to open it.
- **FR-019**: The synthetic generator MUST obtain projects and vendors solely through the single roster reader established in E001, and MUST NOT declare projects or vendors of its own.
- **FR-020**: Every synthetic document and its manifest entry MUST record the roster content hash under the field name `roster_hash`, in the exact `sha256:` plus lowercase-hexadecimal form the reader emits.
- **FR-021**: Generation MUST be deterministic from a committed seed: the same seed against an unchanged roster MUST reproduce an identical document-model hash for every synthetic document. The document-model hash is taken over a canonical serialization of the pre-render document model — ordered field values and per-page text — not over rendered file bytes, because a renderer stamps timestamps, a document identifier, and a producer string into the bytes and would fail the comparison for reasons unrelated to content.
- **FR-021a**: Byte-identity of the rendered documents MUST hold as a secondary check under the renderer version pinned in the modeling boundary's lockfile, and a change to that pinned version MUST be treated as a deliberate regeneration event rather than a validation failure.
- **FR-021b**: Synthetic documents MUST be rendered to PDF, the form these documents take in practice, so both corpus layers present one parse surface downstream.
- **FR-022**: Generation MUST complete without network access and without any language-model invocation.
- **FR-023**: Each synthetic document MUST carry a transmittal number, the specification section it answers, a submittal descriptor code, an approving-authority marker, a revision suffix, and a reviewer action stamp.
- **FR-023a**: The descriptor codes, review-code letters, and field labels used by synthetic documents MUST be a documented approximation of federal transmittal practice rather than a reproduction of any live form, and the datasheet MUST say so — the current form revision could not be retrieved to verify its codes, and presenting an unverified set as the real one would be a provenance claim the project cannot support.
- **FR-024**: The synthetic layer MUST cover all five roster projects, and every one of the twelve roster vendors MUST appear on at least one submittal.
- **FR-025**: The synthetic layer MUST include at least one resubmittal chain per project — documents sharing a submittal number across an incremented revision suffix and a changed action code.
- **FR-026**: Every material item named in a synthetic document MUST have an equipment category that maps to a specification section present in the vendored real layer, so a specification-to-submittal link is constructible downstream.
- **FR-027**: The synthetic layer MUST ship a datasheet disclosing motivation, composition, generation process, preprocessing, intended uses, distribution, maintenance, and stated limits.
- **FR-028**: Generator output MUST be committed to the repository alongside the per-project manifests covering it, so a clone that never runs the generator holds the complete synthetic layer.
- **FR-029**: The synthetic layer MUST vary document layout across vendors; no single template may span every vendor.
- **FR-030**: The irregularity vocabulary MUST be a closed set of exactly five classes — missing or blank field, inconsistent field label, out-of-order date, page-split field, and scan degradation — and all five MUST be present across the layer, so no class's requirements are left with nothing to assert against.
- **FR-031**: Each synthetic document's manifest entry MUST record the irregularity classes that document carries.
- **FR-031a**: Corpus validation MUST re-derive the four structural classes from the emitted document independently of what the generator recorded, and MUST fail on any disagreement between the derived set and the entry. A generator-asserted label that nothing checks is provenance by assertion, which this project does not accept.
- **FR-031b**: Scan degradation, which is a visual property no structural derivation can confirm, MUST instead be evidenced by unit tests over the injector together with the citation-anchor check in FR-032.
- **FR-032**: Scan degradation MUST be applied to the rendered page image while a complete, machine-readable text layer is retained beneath it, and every page's citation anchor — its page number and document identifier — MUST remain an undegraded text object. No page may require optical character recognition to be read.
- **FR-032a**: The datasheet MUST disclose that the retained text layer carries none of the character errors, dropped rotated text, or broken reading order a genuinely scanned document would exhibit, so no downstream claim of recognition robustness can rest on this corpus.
- **FR-033**: The manifest set MUST make layer membership and irregularity class machine-readable, so downstream results can be partitioned by layer without inspecting documents.
- **FR-034**: The verification workflow MUST trigger on `pull_request` against the default branch, in addition to its existing `push` and manual-dispatch triggers. When a push and a pull request cover the same head commit the two runs are independent, scoped by ref; the pull-request run is the authoritative gate for the merge.
- **FR-035**: A pull request whose head violates an architecture contract MUST report a failing check naming the violated contract.

### Key Entities *(include for product or technical specs if feature involves data)*

- **Document**: One corpus file, real or generated, in PDF form. Identified by its location within a corpus location; for real documents also by MasterFormat section, agency variant, and revision date. Carries a content hash over its committed bytes, used to detect modification after manifesting.
- **CorpusManifestEntry**: The provenance record for exactly one Document. Common fields on every entry: corpus-relative location, layer label, license basis, content hash. REAL entries add source location, response status at retrieval, retrieval timestamp, issuing body, MasterFormat section, agency variant, revision date, and the upstream digest. SYNTHETIC entries add generator identity, seed, generation date, roster hash, document-model hash, and irregularity classes, and carry none of the retrieval fields. Every field in the applicable set is required; no entry exists without its document and no document without its entry.
- **CorpusLocation**: A grouping of documents under `data/` sharing a single license basis and served by a single JSON manifest validated against a committed schema. The unit at which the no-mixed-licenses rule is expressed; the manifests form a set, with no aggregate index above them.
- **ProjectVendorRoster**: The committed fixture from E001, read here rather than redefined. Supplies the five projects and twelve vendors the synthetic layer references by identifier, and the content hash every generated artifact records.
- **SyntheticCorpusDatasheet**: The disclosure document for the generated layer — its assumptions, composition, generation process, and stated limits. Ships with the layer under `data/` and is revised with it.

## Assumptions & Risks *(mandatory)*

### Assumptions

- Federal guide specification sections remain publicly retrievable at assembly time; vendoring at the moment of retrieval is the standing mitigation, so later unavailability does not invalidate the corpus.
- Both layers are PDF — the form these documents take in practice. The real layer is vendored as the published PDF rather than any structured source format, because the genuine formatting irregularity is precisely what that layer exists to contribute and a structured source would let downstream parsing bypass it.
- Agency-suffixed variants of one MasterFormat number are separate documents for manifesting but count once toward section coverage.
- Synthetic documents are produced from templates and seeded randomness with no model invocation, since E002 precedes the traced gateway epic and a model call would breach both the epic sequencing and the single-invocation-path constraint.
- No purchase-order documents are synthesized — purchase-order records arrive as data from E005, so this corpus covers specifications and submittals only.

### Risks

- **Copyrighted excerpt inside a vendored section** *(likelihood: low, impact: high)*: A reproduced reference-standard excerpt would make the repository's provenance claim false at its foundation. Mitigation: a per-document point-of-use check, exclusion on doubt rather than inclusion with a hedge, and the exclusion recorded.
- **Synthetic layer still too clean despite injection** *(likelihood: medium, impact: medium)*: Uniform generated documents inflate downstream extraction and retrieval results, and the inflation is invisible in a pooled metric. Mitigation: irregularity classes recorded per document so results stay partitionable, and the real layer carrying the primary messiness burden.
- **Roster changes during the generation window** *(likelihood: low, impact: medium)*: E001 disclosed that nothing watches the fixture while a consumer generates from it, so drift is only observable after the fact. Mitigation: FR-016 makes the comparison a validation step that runs automatically, so drift surfaces on the next push rather than by hand.

## Implementation Signals *(mandatory)*

- `NEW-ENTITY` — Document, CorpusManifestEntry, CorpusLocation, and SyntheticCorpusDatasheet as committed files under `data/`, with per-location JSON manifests governed by a committed schema; no database tables, which remain E003's.
- `NEW-WORKER` — two one-shot jobs in `/src/model`: a seeded PDF-rendering document generator and a corpus validator with a console entry point. Both invoked explicitly under the non-default container profile E001 established rather than starting with ordinary local startup. The generator's renderer must be pinned in the modeling boundary's lockfile, since byte-identity of the committed output depends on it.
- `NEW-CONFIG` — corpus location layout with one manifest per location, the committed generator seed and its fixed generation date, a corpus-validation step added to the verification workflow, and the `pull_request` trigger on that workflow.
- `EXTERNAL-SERVICE` — federal specification sources reached once at corpus-assembly time and, thereafter, only by the opt-in re-verification job, which is deliberately kept out of the per-push verification so no required check depends on the network. No runtime path depends on those sources.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001** [US1]: The corpus totals 45–50 documents, within the project's 30–60 document envelope, of which at least 20 are real specification sections spanning at least six distinct long-lead MasterFormat sections across Divisions 26 and 23.
- **SC-002** [US1]: 100% of vendored real documents have a content hash equal to the upstream digest recorded at retrieval, so the verbatim claim is verifiable offline.
- **SC-003** [US1]: Every excluded candidate document has a recorded cause; zero candidates are dropped without one.
- **SC-004** [US2]: 100% of corpus documents carry a manifest entry with every field in that entry's layer-appropriate set populated, and validation exits non-zero on any absence — including a license basis missing any of its required components.
- **SC-005** [US2]: Zero corpus locations contain more than one license basis, and zero SYNTHETIC entries carry a retrieval field.
- **SC-006** [US2]: An evaluator determines every document's layer and license basis from the manifest set alone, and for real documents also its source location, issuing body, retrieval timestamp, upstream digest, MasterFormat section, agency variant, and revision date — without opening a document or reading code. Every manifest validates against the committed schema.
- **SC-007** [US2]: 100% of corpus locations, manifests, and the datasheet resolve under `data/`, and an entry naming a file outside it fails validation.
- **SC-008** [US2]: Corpus validation runs in 100% of verification-workflow runs — every triggering push and every pull request — and a deliberately seeded provenance defect fails that run.
- **SC-009** [US2]: The opt-in re-verification job re-fetches 100% of recorded real-document sources when invoked, and appears in zero per-push workflow runs.
- **SC-010** [US3]: At least 25 synthetic documents span all five projects and all twelve vendors, with at least one resubmittal chain per project, distributed across five corpus locations — one per project.
- **SC-011** [US3]: Re-running the generator from a clean checkout with the committed seed and unchanged roster reproduces an identical document-model hash for 100% of synthetic documents, and byte-identical rendered files under the lockfile-pinned renderer.
- **SC-012** [US3]: A re-run under an unchanged seed and roster leaves the manifest set byte-identical; zero entries change.
- **SC-013** [US3]: 100% of synthetic documents and their manifest entries record the roster hash in the reader's exact emitted form, and a changed roster causes validation to name every stale document and fail.
- **SC-014** [US3]: Generation completes with zero network requests and zero model invocations.
- **SC-015** [US3]: 100% of material items named in synthetic documents map to an equipment category specified by a section present in the vendored real layer.
- **SC-016** [US3]: A clone that has never run the generator holds 100% of the synthetic layer and its five per-project manifests.
- **SC-017** [US3]: 100% of corpus documents, in both layers, are PDF.
- **SC-018** [US3]: The synthetic layer's datasheet contains all eight required disclosures — motivation, composition, generation process, preprocessing, intended uses, distribution, maintenance, and stated limits — with zero absent.
- **SC-019** [US4]: At least 80% of synthetic documents carry at least one injected irregularity class, with all five closed classes present across the layer and no template spanning every vendor. 100% of recorded structural classes match validation's independent re-derivation.
- **SC-020** [US4]: At least one page in the layer carries injected degradation, and 100% of such pages yield their full text without optical character recognition, with the page number and document identifier returned undegraded.
- **SC-021** [US4]: Injector unit tests cover 100% of the five irregularity classes and pass.
- **SC-022** [US5]: A pull request against the default branch runs the verification workflow automatically and reports per-check status, with a contract violation reported as a failing check.

## Clarifications

### Session 2026-07-25

- Q: What file format do the real and synthetic layers take? -> A: PDF for both — the form these documents take in real life. The real layer is the published PDF vendored byte-for-byte; the structured SpecsIntact source is rejected because it would let downstream parsing bypass the formatting irregularity the layer exists to contribute.
- Q: A PDF renderer stamps timestamps, a document identifier, and a producer string into the bytes, so what is the reproducibility hash actually taken over? -> A: The pre-render document model — ordered field values and per-page text — as the primary hash. Byte-identity of the rendered file is a secondary check that holds under the lockfile-pinned renderer, and a pinned-version change is a deliberate regeneration event rather than a validation failure.
- Q: How deep does scan degradation go, given that the ingestion path it was written against does not exist yet? -> A: Degrade the rendered page image while retaining a complete machine-readable text layer beneath it, with the citation anchor kept as an undegraded text object. No page requires optical character recognition. The datasheet discloses that the text layer carries no recognition error.
- Q: Is there one manifest or one per corpus location, and in what format? -> A: One JSON manifest per corpus location, validated against a committed schema, with no aggregate index. Singular references to "the manifest" are corrected to the manifest set.
- Q: If the generator both injects and records irregularity classes, what verifies the record, and is the vocabulary closed? -> A: A closed five-value enum. Validation independently re-derives the four structural classes from the emitted document and fails on disagreement with the entry; scan degradation, being visual, is evidenced by injector unit tests together with the citation-anchor check.
- Q: How does the byte-identical-to-source claim stay checkable after the corpus goes offline? -> A: The manifest records the source location, response status, retrieval timestamp, and a digest of the upstream bytes; the vendored file's content hash must equal that digest. An opt-in re-verification job may re-fetch on demand, and is excluded from the per-push verification because a required check may not depend on the network.
- Q: Where does the corpus validator live? -> A: In `/src/model` alongside the generator, as a module with a console entry point the verification workflow invokes — both must read the roster, and that entry is the only one permitted to open it.
- Q: What size, weight, page-count, and validation-runtime bounds apply? -> A: None, by explicit decision. The consequence is recorded in Scope → Excluded: clone cost and CI time are unbounded, accepted as an exposure at demonstration scale, reversing to stated bounds rather than removed checks if either becomes a delivery obstacle.

## Stress-Test Findings

### Session 2026-07-25

- **STF-001** *(cross-requirement-contradiction, HIGH)* — FR-030's "at least four of five" floor let scan degradation be omitted entirely, leaving FR-032, FR-031b, FR-032a, US4 AS3 and the degradation criterion with nothing to assert against; its "100% of degraded pages" was vacuously true over an empty set. **Resolved**: FR-030 now requires all five classes; SC-019 matches; SC-020 carries a non-vacuity clause requiring at least one degraded page. Affects FR-030, FR-031b, FR-032, FR-032a, SC-019, SC-020, US4.
- **STF-002** *(cross-requirement-contradiction, HIGH)* — US3's Independent Test still said "confirm identical content hashes", which under FR-007 means file bytes — the comparison FR-021 declares will fail for reasons unrelated to content — while AS1 and SC-011 correctly compared document-model hashes under a pinned renderer. US4's Independent Test carried the twin defect, claiming all recorded classes are checkable against document content when FR-031b states scan degradation is not structurally derivable. **Resolved**: both Independent Tests rewritten to match their scenarios and criteria. Affects US3, US4, FR-007, FR-021, FR-021a, FR-031b, SC-011.
- **STF-003** *(constraint-impossibility, HIGH)* — FR-017 and SC-008 demanded validation on every push, but the committed workflow carries `paths-ignore` and ref-scoped `cancel-in-progress`, so a specs-only push runs nothing and a rapid second push cancels the first. Satisfying the requirement literally would have meant deleting two deliberate E001 decisions. **Resolved**: FR-017 scoped to "every push that triggers the workflow", the two E001 behaviours recorded as intended carry-forward, SC-008 restated against workflow runs, and FR-034 now states that push and pull-request runs are independent with the pull-request run authoritative for the merge. Affects FR-017, FR-034, SC-008, SC-022, US2, US5.
- **STF-004** *(coverage-gap, HIGH)* — Four requirements inserted during the clarification pass verified against nothing: FR-008b, FR-009a, FR-021b, FR-031b. A re-run stamping fresh wall-clock generation dates into every manifest entry would have violated FR-009a while passing every scenario and criterion in the spec. **Resolved**: US2 AS11 and SC-009 cover FR-008b; US3 AS10 and SC-012 cover FR-009a; US3 AS11 and SC-017 cover FR-021b; US4 AS6 and SC-021 cover FR-031b. Affects FR-008b, FR-009a, FR-021b, FR-031b.
- **STF-005** *(terminology-drift, MEDIUM)* — Singular "the manifest" survived the change to per-location manifests in US2's narrative, Scope → Included, FR-028, US3 AS7 and SC-016, and the singular silently presumed the synthetic layer occupies exactly one location — a constraint no requirement stated. **Resolved**: all surviving singulars pluralized, and FR-017a now states the intended layout explicitly — the synthetic layer occupies five corpus locations, one per roster project, each with its own manifest, while real and synthetic stay separate regardless because their license bases differ. Affects FR-006a, FR-017a, FR-028, SC-016, US2, US3.

Additionally, SC-001's stated floor of 40 documents was dead arithmetic — at least 20 real plus at least 25 synthetic forces at least 45 — and is corrected to 45–50.

## Compliance Check

Audited against `project-instructions.md` v1.1.3 and the governance rules in `AGENTS.md`. Verdict **PASS** — no instruction violation. Principle verdicts: I PASS, II N/A, III PASS, IV PASS, V PASS, VI PASS (evaluation sets excluded to E014 by an explicit scope decision), VII PASS, VIII N/A; Technology Stack N/A; Testing & Quality Policy deferred to Plan; Source Code Layout PASS; Data Provenance PASS; Governance partial (F5, an upstream document amendment).

| # | Finding | Severity | Status |
|---|---|---|---|
| F1 | Manifest field set was defined once for both layers, so a generated document would have carried an issuing body, retrieval date, and statute-based license basis it does not have | HIGH | Resolved in spec — FR-007 fixes the common set, FR-008 the REAL-only fields, FR-009 the SYNTHETIC-only fields with an explicit prohibition on retrieval fields; FR-011 and FR-012 split the license basis by layer |
| F2 | Artifact placement unstated — neither `data/` for corpus, manifests, and datasheet nor `/src/model` for the generator was named | MEDIUM | Resolved in spec — FR-018, covered by US2 AS8, US3 AS9, and SC-007 |
| F3 | Manifest validation was on-demand only; nothing required it to run in the automatically triggered workflow | MEDIUM | Resolved in spec — FR-017, covered by US2 AS10 and SC-008 |
| F4 | No mechanism owned the roster-drift comparison; the recorded hash was written but never checked | MEDIUM | Resolved in spec — FR-016, covered by US3 AS5 and SC-011 |
| F5 | `specs/project-plan.md` still assigns both `push` and `pull_request` to E002 and calls E001's workflow dispatch-only; E001 landed `push`, and this spec correctly scopes E002 to `pull_request` | MEDIUM | Open — amend `specs/project-plan.md` and E001 IP-002's stale prose via `.github/skills/amend-project/SKILL.md`. The end state still satisfies the plan's acceptance criterion; the narrative is stale, not the scope, and this spec is not widened |
| F6 | The branch-protection exclusion claimed it was "recorded as an open item" with no such record | MEDIUM | Resolved in spec — the Excluded entry now carries cause, owner, and closing condition |
| F7 | SC-022 requires a real pull request as evidence; the audit's snapshot showed the active branch was `main` | MEDIUM | Resolved — `00002-public-corpus-and-manifest` was cut from `main` before drafting |
| F8 | FR-023 carried an unresolved `[NEEDS CLARIFICATION]` on the federal transmittal form's review codes | LOW | Resolved in spec — FR-023a fixes a documented approximation, disclosed in the datasheet per US3 AS8 and SC-018. Zero markers remain |

**Verified against source rather than asserted**: FR-020's `sha256:` plus lowercase-hexadecimal form matches `content_hash()` in `src/model/src/model/roster/reader.py`; the five-project and twelve-vendor counts match that reader's validation constants; SC-001's 30–60 document envelope traces to `specs/sad.md` Scale/Scope; FR-007 through FR-009 together carry all five fields the Data Provenance section mandates; FR-005, FR-013, and FR-027 carry the cite-never-include, no-mixed-license, and datasheet rules; FR-022 upholds the single-invocation-path constraint, which E002 could not otherwise satisfy while preceding E004.

## Glossary *(include when spec introduces 2+ domain-specific terms)*

| Term | Definition |
|------|------------|
| Corpus location | A directory under `data/` grouping documents that share one license basis, served by exactly one manifest. The unit at which license segregation is expressed. |
| License basis | The recorded justification for a document's legal cleanliness. For a real document: governing statute or license identifier, document identifier with revision date, and the outcome of the point-of-use copyright check. For a generated document: a statement that this project produced it and that it carries no third-party rights. |
| Layer | Whether a document is REAL — retrieved verbatim from a public-domain source — or SYNTHETIC — generated by this project. A closed two-value set recorded per document. |
| Guide specification | A published master specification section that a project adapts, as distinct from a project-specific specification. The real layer's source material. |
| Agency variant | The suffix distinguishing one issuing agency's edition of a MasterFormat section number from another's. Variants of one number are separately dated documents. |
| Transmittal | The cover sheet accompanying a submittal package, carrying the transmittal number, referenced specification section, item rows, and the reviewer's action. |
| Submittal descriptor code | The standard code classifying a submitted item — shop drawings, product data, samples, test reports, and comparable types — within a specification section. |
| Resubmittal chain | Two or more documents sharing a submittal number across incremented revision suffixes, representing a rejected submittal and its resubmission. |
| Action stamp | The reviewer's recorded disposition of a submitted item, including whether resubmission is required. |
| Equipment category | The class of material a document names — medium-voltage switchgear, power transformer, chiller, and comparable classes — used to map a synthetic submittal's items back to the specification section that governs them. |
| Irregularity class | A named category of deliberate imperfection injected into a synthetic document — missing field, inconsistent label, out-of-order date, page-split field, or scan degradation — recorded per document so results stay partitionable. |
| Citation anchor | The page number and document identifier that make a page citable. Kept as an undegraded text object, so degradation never puts it behind character recognition. |
| Content hash | The digest over a committed corpus file's bytes, recorded in its manifest entry. Detects modification of a file after it was manifested. |
| Upstream digest | The digest of a real document's bytes as retrieved from its source, recorded at retrieval so the verbatim-vendoring claim stays checkable after the source is unreachable. |
| Document-model hash | The digest over a synthetic document's pre-render model — ordered field values and per-page text. What the reproducibility criterion compares, since rendered bytes vary with the renderer rather than with content. |
| Manifest set | The collection of per-location manifests, one per corpus location. There is no aggregate index above them; "the manifest set" is the evaluator-facing artifact. |
| Roster hash | The content hash the E001 roster reader emits for the project and vendor fixture, recorded by every generated artifact so drift between the roster and the data generated from it is detectable. |
| MasterFormat division | The top-level grouping of the standard construction specification numbering system; Division 26 covers electrical and Division 23 covers heating, ventilating, and air conditioning. |
