---
feature_branch: "00006-document-ingestion-and-extraction"
created: "2026-07-27"
input: "E006"
spec_type: "product"
spec_maturity: "draft"
epic_id: "E006"
epic_sources: "{PRD:CAP-002}{SAD:ADR-0008}"
---

# Feature Specification: Document Ingestion and Extraction

**Feature Branch**: `00006-document-ingestion-and-extraction`
**Created**: 2026-07-27
**Status**: Draft
**Spec Type**: product
**Spec Maturity**: draft
**Epic ID**: E006
**Epic Sources**: {PRD:CAP-002}{SAD:ADR-0008}
**Product Document**: `specs/prd.md`

## Problem Statement *(mandatory)*

Fifty-one corpus documents sit on disk as PDFs and a database sits empty beside them: the tables that hold chunks, extracted values, and extraction failures exist, and nothing has ever written a row to them. Until something does, every capability the product is built on — retrieval, cross-document identity, grounded answering, and the source-traceability view a coordinator uses to check a number — has no input. Dana, the coordinator this product is designed for, distrusts any tool that produces a number she cannot trace back to a document; the mechanism that earns that trust is a page citation attached to every extracted value, and that mechanism starts here. Left undone, five downstream epics have nothing to consume and the product's central claim stays unbacked.

## Scope *(mandatory)*

### Included

- Reading the corpus through its manifests and creating one `document` row per corpus document, with the layer, license basis, and layer-appropriate provenance carried across unchanged
- Layout-aware parsing of all 51 documents, with every page number derived from the parser
- Structure-aware chunking of all 51 documents, each chunk confined to a single page and to the embedding encoder's input window
- Local embedding of every chunk into the 384-dimension vector the schema declares
- Schema-validated line-item extraction from the 25 synthetic submittal transmittals, with each value's page citation inherited from its source chunk and a per-field confidence recorded
- Multi-page provenance for a value whose label and value straddle a page boundary
- Routing values that fail validation, or fall below the confidence floor, to failure records rather than storing them
- A queryable ingestion-run record giving every chunk, value, and failure an attributable origin
- Total verification that each chunk's recorded page matches the page its text actually came from
- A deterministic template-based extractor over the same transmittals, serving as the baseline every extraction figure is reported against
- An ingestion report committed with this epic, carrying the run's figures and the conventions downstream epics must account for

### Excluded

- **Line-item extraction from the 26 real UFGS specifications** — they are requirement prose, not item records: no manufacturer, no part number, no quantity, and unresolved bracketed alternatives (`[on-off] [high-low-off] [modulating]`) that a model asked for "the" value would resolve into a project requirement the document never states. They are still parsed, chunked, and embedded, so retrieval and grounded answering reach them in full.
- **Vector search, ranking, and retrieval evaluation** — E008 owns the read path. E006 populates the column and the index the schema already declares.
- **Cross-document identity resolution and merge decisions** — E009 owns them. E006 produces the extracted values they operate on and deliberately asserts no identity between two spellings of one manufacturer.
- **Any change to the chunk, extracted-value, or extraction-failure schema** — E003 owns it and `specs/00003-core-data-schema/data-model.md` is normative over this spec ({SAD:ADR-0017}). E006 populates those tables and adds only its own ingestion-run record.
- **Optical character recognition** — every document in the corpus carries a real text layer, including the ones rendered to look scanned.
- **Correcting or re-typesetting source documents** — the corpus is byte-for-byte what E002 vendored.

### Edge Cases & Boundaries

- A structural unit that exceeds the encoder's 256 word-piece window: the encoder truncates silently and still returns a well-formed vector, so a too-long chunk is indistinguishable from a complete one at query time. Splitting must continue down the structural hierarchy, and a leaf that still exceeds the window fails the run rather than embedding a head-only vector.
- A field whose label ends one page and whose value begins the next — E002's `PAGE_SPLIT_FIELD` irregularity class. A chunk may not span pages, so this is a multi-source value with one contributing chunk per page, not a chunk that straddles the break.
- A UFGS section written as an unedited master, carrying bracketed alternatives nobody has chosen yet. Chunk text preserves the brackets verbatim so a downstream reader can see the choice is open.
- The PART 1 REFERENCES article: a dense list of ASTM designations that embeds poorly and looks near-identical across unrelated sections.
- Agency variants of one MasterFormat number are separate documents whose chunks are near-duplicates of each other.
- A `document_id` collision between two corpus files whose stems normalize to the same identifier.
- A manifest entry whose recorded content hash no longer matches the file on disk — the corpus changed underneath the run.
- A field printed with an alternate label (`Mfr` rather than `Manufacturer`), or absent entirely — both are seeded irregularity classes and neither may be defaulted into a value.
- Two documents in one resubmittal chain differing only by revision suffix, whose chunks retrieve as duplicates.
- A run interrupted part-way: the extraction tables are append-only by privilege, so there is no in-place repair path.
- The provider is unreachable, or a fixture is missing in replay: chunks are already written and extraction cannot proceed.

## User Scenarios & Testing *(mandatory for product specs only)*

### User Story 1 - Corpus becomes citable chunks (Priority: P1)

Every document in the corpus is parsed and cut into chunks that follow the document's own structure, and each chunk records which document, project, page, and specification section it came from, along with the vector that will later let it be found. A coordinator never sees a chunk directly, but every citation she does see resolves through one, so the page a chunk claims must be the page its words are actually on.

**Why this priority**: Nothing else in the epic or in five downstream epics can begin without populated chunks — retrieval, grounded answering, and the traceability view all read this table.

**Independent Test**: Run the ingestion job against the committed corpus, then pick any chunk at random, open the PDF it names at the page it names, and find its text there.

**Acceptance Scenarios**:

1. **Given** the 51-document corpus and an empty database, **When** the ingestion job runs, **Then** every document has a `document` row and every chunk carries a non-null document, document type, project, page number, and ordinal.
2. **Given** a chunk, **When** its text is compared against an independent extraction of the page it names, **Then** the text is found on that page — checked for every chunk in the corpus, not a sample.
3. **Given** a specification section whose article exceeds the encoder's input window, **When** the chunker processes it, **Then** the article is split at the next structural level down, and no chunk is embedded whose tail the encoder would discard.
4. **Given** a structural unit that cannot be split small enough to fit the window, **When** the chunker reaches it, **Then** the run fails and names the unit, rather than storing a vector that represents only its beginning.
5. **Given** a real UFGS specification containing unresolved bracketed alternatives, **When** it is chunked, **Then** the bracket markup is preserved verbatim in the stored text.
6. **Given** a completed run, **When** it is run again against unchanged inputs, **Then** the same chunk boundaries and ordinals are produced.
7. **Given** a manifest entry whose recorded content hash no longer matches the file on disk, **When** the run reaches that document, **Then** the run fails and no rows are written for it.
8. **Given** any chunk, **When** its page attribution is read, **Then** it names exactly one page — a field crossing a page break produces two chunks, never one chunk spanning the break.
9. **Given** the ingestion report, **When** it is read, **Then** it names the shared-library project convention, the chunk-identity contract, and the zero-recognition-error upper bound.

### User Story 2 - Every extracted value points at its page (Priority: P1)

Line items are extracted from the 25 submittal transmittals — the manufacturer, part number, quantity, dates and descriptor codes a vendor actually proposed. Each value carries the page it came from, and that page is not something the model was asked for: it is inherited from the chunk the text was read out of. When Dana questions a part number, one click puts her on the page that prints it.

**Why this priority**: This is CAP-002's stated outcome and the product's core claim. E009 has nothing to match without extracted line items, and the 100% traceability target is measured on exactly these rows.

**Independent Test**: Pick any extracted value, read its cited page, open that document at that page, and find the printed value.

**Acceptance Scenarios**:

1. **Given** a submittal transmittal chunk, **When** extraction runs, **Then** every value produced carries a source chunk, a cited page equal to that chunk's page, a field name, and a confidence.
2. **Given** any extracted value in the database, **When** its cited page is compared against its source chunk's page, **Then** they agree — because a value whose citation disagrees with its chunk cannot be stored at all.
3. **Given** an extraction request, **When** it is issued, **Then** it goes through the single traced model path and no other module reaches the provider.
4. **Given** a manufacturer printed as `EMBERDYNE CONTROLS`, **When** it is extracted, **Then** the stored value is that text unaltered, and no normalized or canonicalized form is stored beside it — normalization is the join key identity resolution owns, not a record this epic keeps.
5. **Given** a field name the model proposes that is not in the seeded vocabulary, **When** persistence is attempted, **Then** the write is refused rather than the vocabulary being widened at run time.
6. **Given** the 26 real specifications, **When** the run completes, **Then** they have chunks and embeddings and zero extracted values, and that exclusion is recorded rather than left to be inferred from an empty result.
7. **Given** a generated document, **When** its record is written, **Then** it carries its generator identity, seed, and generation date and carries no retrieval provenance, while a retrieved document carries its source, issuing body, and retrieval date and carries no generator fields.
8. **Given** an extracted value whose kind is numeric or a date, **When** the typed form is produced, **Then** it was coerced by deterministic code from the printed text rather than accepted as a typed value the model returned.
9. **Given** the same transmittals, **When** the deterministic baseline extractor is run over them, **Then** its figures are published beside the model's on the same documents, with the baseline labelled strong or weak.

### User Story 3 - An untrustworthy value is absent, not wrong (Priority: P1)

When the model returns something that does not fit the schema, the system tries once to repair it and then stops. When it returns a value it is barely confident in, that value does not enter the record either. In both cases a failure record is written naming the chunk, the field, and the cause. Dana sees a gap she can investigate rather than a number that looks like every other number and is wrong.

**Why this priority**: Precision over recall where a mistake is silent is a project principle, and a wrongly-stored extracted value is the silent mistake — it propagates into matching, ranking, and the forecast with no symptom.

**Independent Test**: Drive extraction over a chunk whose response fails validation twice, then confirm zero extracted values and exactly one failure record naming the cause.

**Acceptance Scenarios**:

1. **Given** a model response that fails schema validation, **When** one repair attempt also fails, **Then** no value is persisted and a failure record is written with outcome `repair_budget_exhausted`.
2. **Given** an extracted value whose confidence falls below the published floor, **When** it is processed, **Then** it is not persisted and a failure record is written with outcome `confidence_below_threshold`.
3. **Given** an extracted value whose confidence is at or above the floor, **When** it is persisted, **Then** its confidence is stored with it and carried through to anything that displays it.
4. **Given** any failure record, **When** it is inspected, **Then** it carries no value text and no confidence — a failure cannot smuggle a partial answer.
5. **Given** a field the document simply does not print, **When** extraction runs over its chunk, **Then** the outcome is `no_value_found` rather than a defaulted or inferred value.
6. **Given** a completed run, **When** its confidence figures are published, **Then** the distribution is published rather than the mean, and the score is stated to be computed from parse signals and uncalibrated.
7. **Given** any stored extracted value, **When** its confidence is recomputed from the signals recorded with it, **Then** the result equals the stored score exactly.
8. **Given** the confidence floor, **When** the run's distribution is inspected afterwards, **Then** the floor is the value declared before the run and has not been moved to fit what the distribution turned out to be.

### User Story 4 - A value split across a page break keeps both pages (Priority: P2)

Some fields in the corpus have their label at the foot of one page and their value at the head of the next — a deliberate irregularity E002 seeded. The extracted value records both pages rather than picking one, so the citation stays honest about where the evidence actually is.

**Why this priority**: A real corpus condition with a designed representation already waiting in the schema; without it these fields either lose half their provenance or are dropped, but P1 remains viable because the majority of fields sit on one page.

**Independent Test**: Extract from the document carrying the seeded page-split irregularity and confirm the value records two contributing pages.

**Acceptance Scenarios**:

1. **Given** a field whose label and value straddle a page boundary, **When** it is extracted, **Then** the value is recorded as multi-chunk with one contributing chunk per page it drew on.
2. **Given** a multi-chunk value, **When** its declared source count is compared against its recorded contributors, **Then** they agree.
3. **Given** a value drawn from a single chunk, **When** it is recorded, **Then** it is marked single-chunk and has no additional contributors.

### User Story 5 - Every value names the run that produced it (Priority: P2)

Each ingestion run records what produced it: which agent and model, which chunker and embedding model at which revision, which corpus content, and when. Any chunk, value, or failure resolves to its run, so "what produced this number" is a query rather than an archaeology exercise.

**Why this priority**: The schema deliberately omits a per-row agent column on the grounds that E006 records this at run granularity; nothing else in the project holds it. P2 because a value is already citable to a page without it.

**Independent Test**: Take any extracted value, join to its ingestion run, and read the agent, model, chunker version, and embedding model revision that produced it.

**Acceptance Scenarios**:

1. **Given** a completed ingestion run, **When** its record is read, **Then** it names the agent identity, the provider model, the chunker version, the embedding model identity and revision, the corpus manifest digests, the resolution mode, and its start and finish.
2. **Given** any chunk, extracted value, or failure record, **When** it is queried, **Then** exactly one ingestion run is reachable from it.
3. **Given** two runs over the same corpus with different chunker versions, **When** their outputs are compared, **Then** each row is attributable to the run that wrote it.

### User Story 6 - Re-ingesting is safe and repeatable (Priority: P3)

Running ingestion again does not duplicate what is already there, and correcting a mistake is a documented remove-and-reload rather than an edit, because the provenance tables refuse in-place updates by design. Repeatability is also what lets continuous integration run the whole pipeline offline against committed responses, reaching no provider and no network.

**Why this priority**: Operational quality that matters the second time the job is run, not the first; P1 and P2 are demonstrable from a single clean run.

**Independent Test**: Run ingestion twice against an unchanged corpus and confirm the row counts are unchanged and no chunk identity moved.

**Acceptance Scenarios**:

1. **Given** a populated database, **When** ingestion runs again over unchanged inputs, **Then** no duplicate chunks, values, or failures are created.
2. **Given** a document needing re-ingestion, **When** it is reloaded, **Then** its dependent rows are removed in an order the restricting foreign keys permit and reloaded, with no row updated in place.
3. **Given** a run that fails part-way, **When** the database is inspected, **Then** no document is left half-ingested.

## Requirements *(mandatory)*

### Functional Requirements *(product specs only)*

**Corpus intake and document records**

- **FR-001**: System MUST enumerate corpus documents through the committed manifests, resolving each entry's location with the repository's existing containment-checked path resolution, and MUST NOT ingest a PDF that no manifest lists.
- **FR-002**: System MUST create exactly one document record per corpus document, deriving its identifier from the file stem in lower case — `ufgs-23-52-00`, `prj-001-t0002-r0` — which satisfies the identifier format E003 declares and closes the disclosed gap that left that format unadopted.
- **FR-003**: System MUST record real specifications under the reserved shared-library project `PRJ-000` and synthetic documents under their own project, and MUST record `PRJ-000` in the ingestion report as a named convention project-scoped readers have to account for, since a reader filtering on project alone would silently miss every governing specification.
- **FR-004**: System MUST carry each document's layer, license basis, and layer-appropriate provenance from the manifest into its record unchanged, and MUST NOT give a generated document retrieval provenance it does not have.
- **FR-005**: System MUST verify each document's recorded content hash against the file on disk before parsing it, and MUST fail the run without writing rows when they differ.
- **FR-006**: System MUST classify each document by type from a closed set, recording real specifications as specifications and synthetic transmittals as transmittals, and MUST NOT invent a type outside that set.
- **FR-052**: System MUST fail the run, naming both files, when two corpus files yield the same derived identifier, rather than overwriting one document record with another or attaching one document's chunks to the other — a collision silently resolved would give a citation a referent it never came from.

**Parsing and page provenance**

- **FR-007**: System MUST derive every page number from the parser and MUST NOT accept, request, or store a page number produced by a language model.
- **FR-008**: System MUST derive page text under the same extraction tolerances and normalization form the repository already fixes for corpus derivation, so that page attribution and the corpus's own structural derivation cannot reach different answers about what text a page contains.
- **FR-009**: System MUST NOT require optical character recognition for any document, and MUST disclose in the ingestion report that the corpus text layer carries zero recognition error, so every accuracy figure measured here is an upper bound a genuinely scanned corpus would not reproduce.
- **FR-010**: System MUST verify, for every chunk in the corpus rather than a sample, that the chunk's text is present in an independent extraction of the page it names.
- **FR-011**: System MUST record in the ingestion report, for any claim resting on human inspection rather than the total check of FR-010, the number of items inspected, the number of defects found, and the error bound that sample supports, computed by a stated method.

**Chunking**

- **FR-012**: System MUST place every chunk boundary on a structural boundary the parser identified — specification section, part, article, or paragraph for the real layer; field block or item entry for the synthetic layer — and MUST place zero boundaries at a fixed character, word, or token offset.
- **FR-013**: System MUST confine each chunk to a single page.
- **FR-014**: System MUST measure chunk length in the embedding encoder's own tokenizer, MUST split a unit exceeding the window at the next structural level down, and MUST fail the run naming the offending unit rather than embed a chunk the encoder would silently truncate.
- **FR-015**: System MUST record on each chunk the document, document type, project, page number, and a zero-based ordinal unique within its document, together with the specification section and heading where the document supplies them.
- **FR-016**: System MUST store chunk text as it appears in the source, including unresolved bracketed alternatives in unedited specification masters, so a downstream reader can see that a choice has not been made.
- **FR-017**: System MUST produce the same chunk boundaries and ordinals for the same inputs and chunker version, and MUST record the chunker version so a boundary change is attributable rather than mysterious.
- **FR-018**: System MUST record in the ingestion report the chunk-identity contract downstream consumers depend on — specifically that a chunk identifier is a function of the chunker, so a retrieval evaluation set frozen on chunk identity is invalidated by any legitimate re-chunk and must instead be keyed on document, page, and quoted span.

**Embedding**

- **FR-019**: System MUST embed every chunk locally with a pinned model identity and revision, reaching no network at run time.
- **FR-020**: System MUST record the embedding model identity and revision on every chunk, so vectors from two model versions are never silently mixed.
- **FR-021**: System MUST take the vector dimension from the value the schema publishes at run time, so that changing the dimension does not require changing this epic's code.

**Extraction**

- **FR-022**: System MUST extract line items from the synthetic submittal transmittals only, and MUST record that exclusion explicitly rather than leaving an empty result to be interpreted.
- **FR-023**: System MUST issue every model request through the single traced model path, and MUST NOT import or reach the provider client from any other module.
- **FR-024**: System MUST draw every extracted field name from the seeded field vocabulary, and MUST refuse a value naming a term outside it rather than widening the vocabulary at run time.
- **FR-025**: System MUST validate every model output against the caller's schema before persisting it, and MUST NOT persist, return, or log an unvalidated value.
- **FR-026**: System MUST attempt at most one repair after a validation failure and then fail closed.
- **FR-027**: System MUST store manufacturer and part-number values exactly as printed, and MUST NOT store a normalized, cleaned, or canonicalized form in place of them or alongside them. Normalization is the join key identity resolution operates on and belongs to the epic that owns that decision; E006 preserves the evidence the citation points at.
- **FR-028**: System MUST NOT assert that two differently-spelled manufacturer names are the same manufacturer; recognizing identity across documents belongs to a later epic.

**Citation and confidence**

- **FR-029**: System MUST give every extracted value a page citation inherited from the chunk it was read from, and MUST make a citation disagreeing with its source chunk unstorable rather than merely detectable.
- **FR-030**: System MUST record a per-field confidence on every extracted value, with no exemptions.
- **FR-031**: System MUST compute each per-field confidence deterministically from observable parse signals — whether the printed field label matched the canonical form or a known alternate, whether the value was printed or absent, whether it was read from one chunk or assembled across a page break, and whether the invocation validated on the first attempt or only after a repair — and MUST NOT store a confidence a language model asserted about its own output. System MUST NOT present the resulting score as a calibrated probability, publish it as a frequency, or compare it across different fields as though the values shared a scale; it is a heuristic ordering, and nothing in this epic measures it against ground truth.
- **FR-032**: System MUST fix the confidence floor as a declared value chosen before the first run and MUST NOT move it in response to the distribution that run produces; a value below it is not persisted and is recorded as a failure, and a value at or above it is persisted with its confidence intact. Re-deriving the floor from observed data is fitting a threshold to the set being measured, which requires a frozen and hashed labelled set and is therefore out of this epic's scope.
- **FR-033**: System MUST record in the ingestion report the declared floor together with the observed distribution of confidence values rather than a mean, since a distribution piled at one value makes any threshold either accept-all or reject-all and a mean conceals that. The distribution is published as disclosure, not as an input to choosing the floor.

**Failure handling**

- **FR-034**: System MUST classify every extraction failure with one outcome from the closed set the schema defines, and MUST NOT introduce a new outcome value.
- **FR-035**: System MUST record on each failure the source chunk, the attempted page, the field, the repair attempt count, and a diagnostic detail.
- **FR-036**: System MUST NOT attach a value or a confidence to a failure record.
- **FR-037**: System MUST record a field the document does not print as not found rather than defaulting, inferring, or omitting it silently.

**Run attribution**

- **FR-038**: System MUST write one ingestion-run record per run naming the agent identity, the provider model, the chunker version, the embedding model identity and revision, the corpus manifest digests, the resolution mode, and the run's start and finish.
- **FR-039**: System MUST make every chunk, extracted value, and failure record resolve to exactly one ingestion run through association records this epic owns in its own migration block, since those three tables carry no run column and adding one would change a schema this epic does not own.
- **FR-040**: System MUST claim migration prefix block `0300`–`0399` for any schema object it adds, leaving `0200`–`0299` to the epic sharing its wave, and MUST NOT place an object inside another epic's block.

**Re-running and correction**

- **FR-041**: System MUST NOT update or delete an extracted value, a contributing-chunk record, or a failure record in place; a correction is a remove-and-reload of the affected document in the order the restricting foreign keys permit.
- **FR-042**: System MUST leave no document half-ingested when a run fails part-way.
- **FR-043**: System MUST NOT create duplicate rows when re-run over unchanged inputs.

**Execution environment**

- **FR-044**: System MUST run ingestion offline only, never on a request path.
- **FR-045**: System MUST complete a full ingestion run in continuous integration with no provider call and no network access, resolving every model response from committed fixtures.

**Boundary enforcement and baseline**

- **FR-046**: System MUST record in the ingestion report the signals a computed confidence is derived from and the weight each carries, so a given score is explainable and recomputable from the stored row rather than being an opaque number.
- **FR-048**: System MUST bring its model-facing modules under the enumerated computation-boundary contract, so that date, ranking, and probability arithmetic cannot be reached from them. The single-provider-import rule is enforced by a repository-wide scan and needs no extension; the computation boundary is enforced by a contract that names its modules one by one, so a new model-facing module is outside it until named.
- **FR-049**: System MUST coerce an extracted value to its numeric or date form in deterministic code rather than accepting a typed value the model produced, since a coercion the model performed is arithmetic done where it cannot be tested.
- **FR-050**: System MUST report every extraction quality figure against a deterministic baseline extractor run over the same transmittals, and MUST label that baseline as strong or weak rather than presenting an unlabelled comparison. The synthetic layer uses a fixed set of per-vendor templates, so a template-driven extractor is a baseline that could plausibly win, which is the only kind whose defeat carries information. System MUST publish every such figure with an interval, since a bare point estimate over 25 documents claims a precision that sample does not support.

**Cross-epic obligations**

- **FR-047**: System MUST record, without performing it, the amendment that computing confidence requires: E003's TR-081 fixes per-field confidence as "a self-reported score asserted by the extracting agent", and a deterministically computed score is not that. The non-calibration half of TR-081 survives unchanged and is restated in FR-031; the self-reported half does not. Amendments to registered documents serialize on the default branch, so this branch records the need and does not carry it out — and implementation MUST NOT begin until the amendment lands, because writing computed scores into a column the normative document describes as agent-asserted would mislead every reader who trusts that document.
- **FR-051**: System MUST claim decision-record numbers ADR-0018 and ADR-0019 at epic start, alongside the migration block of FR-040, since decision-record numbers are allocated by scanning for the highest in use and a concurrent epic branching from the same baseline would otherwise allocate the same number and be equally right.

### Key Entities *(include for product or technical specs if feature involves data)*

- **Document**: One corpus PDF as a record — its identifier, type, project, title, layer, license basis, and layer-appropriate provenance. Real specifications attach to the shared-library project; synthetic transmittals attach to their own. The table exists; E006 populates it.
- **Chunk**: A contiguous run of text from exactly one page of one document, cut on the document's structure, carrying its document, type, project, page, ordinal, specification section, heading, and its embedding with the model identity and revision that produced it. The unit every citation resolves through.
- **ExtractedValue**: One field read out of one chunk — its field name, value, kind, confidence, and the page citation inherited from its source chunk. Marked single-chunk or multi-chunk according to how many chunks it drew on.
- **ContributingChunk**: For a value assembled across a page break, the additional chunks beyond the anchor that it drew on, each with its page. The representation that keeps a split field's provenance whole.
- **ExtractionFailure**: A field that could not be extracted — its source chunk, attempted page, field name, one outcome from the closed set, the repair attempts spent, and a diagnostic detail. Carries no value and no confidence, by construction.
- **IngestionRun**: One execution of the pipeline — agent identity, provider model, chunker version, embedding model identity and revision, corpus manifest digests, resolution mode, start and finish. The record E006 owes because the schema deliberately left per-row agent identity out. New; every other entity here already has a table.
- **RunOutput**: The association between an ingestion run and each chunk, extracted value, and failure it wrote. New, and owned by this epic rather than added as a column, because the three tables it links belong to a schema this epic does not own.
- **BaselineExtraction**: What a deterministic template-driven extractor produced from the same transmittals, held so that every model figure can be reported against it rather than on its own.

## Assumptions & Risks *(mandatory)*

### Assumptions

- Real specifications carry `PRJ-000` as a shared-library project identifier; the value fits the project-identifier format the schema already enforces and needs no schema change, but downstream project-scoped readers must union it.
- A synthetic document's required fixture hashes are the digests of the four generation inputs its manifest entry already records; nothing declares this mapping, so E006 fixes it here.
- The 26 real specifications are uncharacterized structurally — the corpus datasheet governs only the synthetic layer — so section, part, and article detection must be validated against the real documents rather than assumed from the standard's format rules.
- The single traced model path fixes the repair budget at one attempt, so a recorded repair count is always zero or one even though the schema leaves it unbounded; and extraction prompts and output schemas are stable enough to make committed fixtures worth keeping, since any change to prompt text or to a schema constraint invalidates every existing fixture and forces a re-record.
- Computing confidence rather than having the model report it changes what E003's TR-081 says the stored number is; the column, its type, and its range are untouched, so this is a change to a requirement's meaning and not to a schema object. It is recorded as an amendment request under FR-047 rather than performed on this branch, and implementation waits on it.

### Risks

- **Parser page attribution is correctness-critical** *(likelihood: medium, impact: high)*: deterministic provenance is only as good as the page mapping beneath it, and a wrong page number is the most damaging failure the product has. Mitigation: verify containment for every chunk rather than a sample, reusing the tolerances and normalization form already pinned in the repository.
- **Computed confidence is unvalidated** *(likelihood: high, impact: medium)*: the score is reproducible and explainable, but nothing in this epic checks it against ground truth, so a floor set against it may reject values that were correct and admit values that were not. Mitigation: declare the floor before the run rather than fitting it to the result, record the signals and their weights so any score is recomputable, and state that it is a heuristic ordering rather than a probability.
- **Extraction accuracy is measured only on generated documents** *(likelihood: high, impact: medium)*: line items come from the synthetic layer alone, and the corpus datasheet already records that its text layer carries zero recognition error. Mitigation: label every extraction figure by layer and publish the upper-bound caveat with it rather than after being asked.

### Disclosed Limitations

Each limitation below is recorded with its scope decision, the evidence behind it, the condition that would reverse it, and what a production-scale system would do instead. A limitation stated without the last two is a shortfall dressed as a decision.

| Limitation | Scope decision & evidence | Reversal trigger | Production-scale alternative |
|---|---|---|---|
| No line-item extraction from the 26 real specifications | UFGS masters are requirement prose carrying unresolved bracketed alternatives (`[on-off] [high-low-off] [modulating]`, `ufgs-23-52-00.pdf` p.30); resolving one would fabricate a requirement the document does not state | A project-edited specification enters the corpus, in which the brackets have been resolved by a spec writer | Ingest the edited project specifications a real contractor holds, where the alternatives are already chosen, and extract requirement values against them |
| Extraction accuracy is an upper bound | The synthetic text layer carries zero recognition error by construction (corpus datasheet); no document requires optical character recognition | A scanned document with genuine recognition error is added to the corpus | Run a recognition engine over genuinely scanned documents and report accuracy with recognition error included in the denominator |
| Computed confidence is uncalibrated | Nothing in this epic measures the score against ground truth; it is a heuristic ordering over parse signals, and the floor is declared rather than fitted | A frozen, hashed labelled sample of extracted fields becomes available | Fit and publish the threshold against that labelled set with its discrimination reported, and calibrate the score against observed correctness |
| `PRJ-000` is a sentinel every project-scoped reader must union | The schema requires a project on every document and a public specification belongs to none; a sentinel avoids duplicating each specification once per project | Specifications become genuinely project-specific, or the schema admits a null project | Model the specification-to-project relationship explicitly rather than through a reserved identifier |
| Chunk identity is a function of the chunker | Any legitimate re-chunk moves chunk identifiers, so an evaluation set frozen on them would trip its own hash gate for a reason the gate does not mean | The chunker is declared frozen for a release, making chunk identity stable by policy | Key evaluation sets on document, page, and quoted span, and resolve to chunks at harness run time |

## Implementation Signals *(mandatory)*

- `NEW-ENTITY` — an ingestion-run record and the association records linking it to every chunk, value, and failure it wrote; the only entities in this epic without existing tables.
- `MIGRATION` — forward migrations in the newly claimed `0300`–`0399` block for those records, plus extension of the block partition the build asserts.
- `NEW-CONFIG` — extension of the enumerated computation-boundary import contract to this epic's model-facing modules; unlike the repository-wide provider-import scan, that contract names its modules one by one and does not cover a new one until told to.
- `NEW-WORKER` — an offline ingestion job invoked as a console entry point of the modeling entry, not as a service or a container job.
- `NEW-CONFIG` — the pinned embedding model identity and revision, the confidence floor, and the chunker version, each recorded per run rather than assumed.
- `EXTERNAL-SERVICE` — the model provider, reached only through the traced gateway and only in record mode; continuous integration replays committed fixtures.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001** [US1]: All 51 corpus documents have a document record and at least one chunk; zero corpus documents are skipped without a recorded cause.
- **SC-002** [US1]: 100% of chunks have their text found on the page they name, verified for every chunk rather than a sample.
- **SC-003** [US1]: 100% of chunks carry a non-null document, document type, project, page number, and ordinal, and every document's ordinals are contiguous from zero.
- **SC-004** [US1]: Zero chunks exceed the embedding encoder's input window when measured in that encoder's own tokenizer.
- **SC-005** [US1]: The total chunk count and the per-layer counts are published against the architecture's stated 5,000–15,000 estimate, and any deviation is published with its cause rather than the estimate being restated to match the result.
- **SC-006** [US1]: 100% of chunks carry a vector of the dimension the schema publishes, with a recorded embedding model identity and revision; zero chunks carry a vector from an unrecorded model.
- **SC-007** [US1]: Re-running ingestion over unchanged inputs produces identical chunk boundaries and ordinals.
- **SC-008** [US2]: 100% of extracted values carry a page citation and a per-field confidence — the project's continuous target, where partial compliance is a defect rather than a shortfall.
- **SC-009** [US2]: 100% of extracted values have a cited page equal to their source chunk's page, and the count of values whose citation could disagree is zero because such a row cannot be stored.
- **SC-010** [US2]: 100% of extracted field names appear in the seeded vocabulary; zero values are stored under a term invented at run time.
- **SC-011** [US2]: Every model request in the run is recorded on the traced path; zero model requests originate anywhere else.
- **SC-012** [US2]: All 25 synthetic transmittals yield at least one extracted value; the 26 real specifications yield zero, with the exclusion recorded.
- **SC-013** [US2]: Every extracted manufacturer and part number matches, character for character, the value the corpus generator recorded as printed on that page — compared against every extracted value rather than a sample, so no sampling bound is claimed. The reference is the pre-render document model, reproducible from the committed seed and generation inputs and pinned by the digest the manifest already carries, so it is frozen and hash-verifiable without introducing a new evaluation artifact.
- **SC-014** [US3]: 100% of model outputs are schema-validated before persistence, with at most one repair attempt and then failure — the project's continuous target for language-model output validity.
- **SC-015** [US3]: Zero extracted values are persisted from an invocation that ended in failure, and zero failure records carry a value or a confidence.
- **SC-016** [US3]: Every extraction failure carries one outcome from the closed set of seven; zero failures carry an outcome outside it.
- **SC-017** [US3]: The confidence floor, the full distribution of observed confidence values, and the signals the score is computed from with their weights are all published, together with the statement that the score is a heuristic ordering rather than a calibrated probability.
- **SC-018** [US3]: The valid, repaired, and failed counts are published separately, with the repaired rate reported in its own right rather than folded into a success rate.
- **SC-019** [US4]: Every value drawn from more than one chunk records one contributing chunk per page it drew on, and its declared source count equals its recorded contributor count in 100% of cases.
- **SC-020** [US4]: The document carrying the seeded page-split irregularity produces at least one multi-chunk value.
- **SC-021** [US5]: 100% of chunks, extracted values, and failure records resolve to exactly one ingestion run.
- **SC-022** [US5]: Every ingestion run record names its agent identity, provider model, chunker version, embedding model identity and revision, corpus manifest digests, and resolution mode; zero fields are absent.
- **SC-023** [US6]: A full ingestion run completes in continuous integration with zero provider calls and zero network access.
- **SC-024** [US6]: Zero rows in the extraction tables are updated or deleted in place across any run or correction.
- **SC-025** [US6]: Re-running ingestion over unchanged inputs adds zero chunk, extracted-value, or failure rows.
- **SC-026** [US3]: Recomputing every stored confidence from the signals recorded with it reproduces the stored value exactly, and zero stored confidences originate from a model assertion.
- **SC-027** [US2]: Zero identity assertions are made between two differently-spelled manufacturer names, and zero stored values differ from the generator's recorded pre-render text for their page — the same reference SC-013 names, compared over every stored value rather than a sample.
- **SC-028** [US2]: The enumerated computation-boundary contract names every model-facing module this epic adds; contracts kept with zero broken.
- **SC-029** [US2]: Every extraction quality figure is published beside the deterministic baseline's figure over the same documents, with the baseline labelled strong or weak and with an interval on the figure; zero figures are published without a baseline or without an interval.
- **SC-030** [US1]: Zero documents are ingested whose recorded content hash differs from the file on disk, and a mismatch aborts the run with zero rows written for that document.
- **SC-031** [US1]: Zero chunks span more than one page.
- **SC-032** [US1]: The ingestion report names the shared-library project convention, the chunk-identity contract, the zero-recognition-error upper bound, the declared confidence floor with its observed distribution and signal weights, and the inspected count and error bound of any sampled claim; zero of these are absent.
- **SC-033** [US2]: 100% of document records carry their layer, license basis, and layer-appropriate provenance unchanged from the manifest, and zero generated documents carry retrieval provenance.
- **SC-034** [US5]: The migration block and decision-record numbers this epic claims are recorded before implementation begins, zero schema objects are placed outside the claimed block, and the amendment FR-047 raises has landed on the default branch before the first extracted value is written.
- **SC-035** [US6]: Zero ingestion code paths are reachable from a request-serving entry point.
- **SC-036** [US1]: Zero document records exist for a PDF no manifest lists; 100% of document identifiers are the lower-cased file stem and satisfy the identifier format the schema requires; 100% of document types are drawn from the closed set the schema defines; and zero identifiers are shared by two documents.
- **SC-037** [US1]: Zero pages yield different text under page attribution than under the corpus's own structural derivation, since both read the page under one set of extraction tolerances and one normalization form.
- **SC-038** [US1]: Zero chunk boundaries fall at a fixed character, word, or token offset; 100% coincide with a structural boundary the parser identified.
- **SC-039** [US3]: 100% of failure records carry a source chunk, an attempted page, a field name, a repair attempt count, and a diagnostic detail; zero carry an absent field among those five.
- **SC-040** [US2]: 100% of numeric and date values were coerced from the printed text by deterministic code; zero typed values were accepted in the form the model returned them.

## Glossary *(include when spec introduces 2+ domain-specific terms)*

| Term | Definition |
|------|------------|
| Chunk | A contiguous run of text from exactly one page of one document, cut on the document's own structure, and the unit through which every page citation resolves. |
| Line item | A single proposed material or equipment entry read out of a submittal transmittal — manufacturer, part number, quantity and related fields. Distinct from a purchase order line, which is a procurement record and the unit the forecast operates on. |
| Specification section | A MasterFormat-numbered division of a construction specification, identifying what a document governs; recorded on chunks and cited by transmittals. |
| Citation anchor | The page number and document identifier that together make a page citable — the project's fixed form for a citation. |
| Confidence floor | The value below which an extracted value is recorded as a failure rather than stored. Declared before the first run and not moved to fit the distribution that run produces — a policy number, not a calibrated or fitted threshold. |
| Ingestion run | One execution of the parse, chunk, embed, and extract pipeline, recorded so that every row it wrote is attributable to the agent, models, and corpus state that produced it. |
| Shared-library project | The reserved project identifier `PRJ-000` under which real specifications are recorded, since a public specification belongs to no single project but the schema requires one. |
| Word piece | The sub-word unit the embedding encoder counts; its 256-unit window is a hard cap on chunk length and is not equivalent to a word or character budget. |
| Page-split field | A field whose label ends one page and whose value begins the next — a seeded corpus irregularity, and the reason a value may draw on more than one chunk. |
| Structure-aware chunking | Cutting a document at boundaries the document itself declares — section, part, article, paragraph — rather than at a fixed size. |

## Clarifications

### Session 2026-07-27

- Q: `document.project_id` is required on every document, including real specifications belonging to no single project. How should they attach? → A: A reserved shared-library project `PRJ-000`. One document record per specification, chunked and embedded once. Accepted cost: project-scoped readers must union `PRJ-000` with their own project, which makes the sentinel a contract downstream epics have to know about.
- Q: What happens when a field's extraction confidence is low? → A: Two bands. Below a published floor the value is not persisted and a failure is recorded; at or above it the value is persisted with its confidence carried through for display. Uses both the confidence column and the below-threshold failure outcome as designed, and avoids collapsing a graded signal into a binary one.
- Q: Which corpus layers get line-item extraction, as opposed to only being chunked and embedded? → A: The synthetic submittal transmittals only. All 51 documents are still parsed, chunked, and embedded. Real UFGS specifications are requirement prose containing unresolved bracketed alternatives, so asking a model for "the" value would fabricate a requirement the document does not state. Accepted cost: extraction accuracy is measured on generated material only, and must be published with that caveat.
- Q: Where does the ingestion-run agent identity live, given the schema deliberately omits a per-row agent column? → A: A new ingestion-run table, claiming migration prefix block `0300`–`0399` and leaving `0200`–`0299` for the epic sharing this wave.
- Q: Should per-field confidence be the model's own self-reported number, or computed from parse signals? → A: Computed deterministically from parse signals — label matched canonical or alternate, value printed or absent, single-chunk or page-split, validated first try or after a repair. Reproducible, explainable, and on the code side of "the model extracts, code computes"; the research finds self-reported confidence collapses toward all-positive at practical thresholds, which would make the chosen floor reject nothing. Consequence recorded rather than absorbed: E003's TR-081 fixes confidence as agent-asserted, so this needs an amendment, raised in FR-047 and not performed on this branch.

## Compliance Check

**Audited against**: `project-instructions.md` **v1.2.4** (last amended 2026-07-26) · **Audit date**: 2026-07-27 · **Verdict**: PASS with two sequencing conditions.

| Principle / Section | Verdict | Where |
|---|---|---|
| I. Traceable or It Does Not Ship | PASS | FR-007, FR-029, FR-030; SC-008, SC-009 — citation derived at ingestion and unstorable if it disagrees with its chunk |
| II. Uncertainty Is the Product | PASS | FR-033, SC-017, SC-032 — distribution published rather than a mean; no point estimate stands alone |
| III. Precision Over Recall Where a Mistake Is Silent | PASS | FR-025, FR-026, FR-028, FR-036, FR-037; SC-015, SC-016 — fail closed after one repair, absent rather than wrong |
| IV. Agent Output Style | PASS | Template sections only, no preamble or epilogue |
| V. The Model Extracts, Code Computes | PASS | FR-031 (confidence computed, not asserted), FR-048 (boundary contract extended to this epic's modules), FR-049 (coercion in deterministic code) |
| VI. Evaluate Before You Tune | PASS | FR-032 — the floor is declared before the run and not refitted to it; re-deriving it would require a frozen hashed set and is out of scope |
| VII. Publish the Miss | PASS | Disclosed Limitations — five limitations, each with scope decision, evidence, reversal trigger, and production-scale alternative |
| VIII. Honest Opponents | PASS | FR-050, SC-029 — every extraction figure reported against a deterministic template baseline, labelled strong or weak |
| Technology Stack | PASS | 384-dimension local embedding and the 256 word-piece window (ADR-0012); console entry point (ADR-0011); no second datastore of record |
| Testing & Quality Policy | PASS | FR-048 and SC-028 extend the enumerated computation-boundary contract; architecture contracts gate the build |
| Source Code Layout | PASS | Modeling-entry console entry point, gateway-mediated provider access, no fifth entry |
| Development Workflow | PASS | Workspace `00006-…` matches epic E006; branch cut to match before commit |
| Data Provenance | PASS | FR-004, SC-033 — layer, license basis, and layer-appropriate provenance carried unchanged; no fabricated retrieval provenance |
| Governance | PASS | FR-040 claims migration block `0300`–`0399`; FR-051 claims ADR-0018–0019; workspace number matches epic number; FR-047 records an amendment rather than performing it on a feature branch |

**Two conditions carried into Plan, both sequencing rather than defects:**

1. **FR-047 blocks implementation.** E003's TR-081 declares `extracted_value.confidence` a score the extracting agent asserted about its own output, and `data-model.md` is normative for reader-facing semantics under {SAD:ADR-0017}. Writing computed scores into that column before the amendment lands would mislead any reader who trusts the governing document. The amendment lands on `main`; this branch only records the need.
2. **`0200`–`0299` is left for E005 by this spec alone.** Nothing outside this document ratifies it, so the disjointness holds only if E005 reads E006's claim. Worth raising when E005 is specified.
