---
feature_branch: "00006-document-ingestion-and-extraction"
created: "2026-07-27"
input: "E006"
spec_type: "product"
spec_maturity: "clarified"
epic_id: "E006"
epic_sources: "{PRD:CAP-002}{SAD:ADR-0008}"
---

# Feature Specification: Document Ingestion and Extraction

**Feature Branch**: `00006-document-ingestion-and-extraction`
**Created**: 2026-07-27
**Status**: Draft
**Spec Type**: product
**Spec Maturity**: clarified
**Epic ID**: E006
**Epic Sources**: {PRD:CAP-002}{SAD:ADR-0008}
**Product Document**: `specs/prd.md`

## Problem Statement *(mandatory)*

Fifty-one corpus documents sit on disk as PDFs and a database sits empty beside them: the tables holding chunks, extracted values, and extraction failures exist, and nothing has ever written a row to them. Until something does, every capability the product is built on — retrieval, cross-document identity, grounded answering, and the source-traceability view used to check a number — has no input. Dana, the coordinator this product is for, distrusts any tool producing a number she cannot trace back to a document; the mechanism that earns that trust is a page citation on every extracted value, and it starts here. Left undone, five downstream epics have nothing to consume and the product's central claim stays unbacked.

## Scope *(mandatory)*

### Included

- Reading the corpus through its manifests and creating one `document` row per corpus document, layer, license basis, and layer-appropriate provenance carried across unchanged
- Layout-aware parsing of all 51 documents, every page number derived from the parser
- Structure-aware chunking of all 51 documents, each chunk confined to one page and to the encoder's input window
- Local embedding of every chunk into a vector of the dimension the schema publishes at run time (FR-021)
- Schema-validated line-item extraction from the 25 synthetic transmittals, each value's page citation inherited from its source chunk and a per-field confidence recorded
- Multi-page provenance for a value whose label and value straddle a page boundary
- Routing values that fail validation, or fall below the confidence floor, to failure records rather than storing them
- A queryable ingestion-run record giving every chunk, value, and failure an attributable origin
- Total verification that each chunk's recorded page matches the page its text came from
- A deterministic template-based extractor over the same transmittals, the baseline every extraction figure is reported against
- An ingestion report committed at a fixed path with a closed list of contents (FR-071), each figure labelled (FR-072)

### Excluded

- **Line-item extraction from the 26 real UFGS specifications** — requirement prose, not item records: no manufacturer, part number, or quantity, and unresolved bracketed alternatives (`[on-off] [high-low-off] [modulating]`) that a model asked for "the" value would resolve into a requirement the document never states. They are still parsed, chunked, and embedded.
- **Vector search, ranking, and retrieval evaluation** — E008 owns the read path; E006 populates the column and index the schema already declares.
- **Cross-document identity resolution and merge decisions** — E009 owns them; E006 produces the values they operate on and asserts no identity between two spellings of one manufacturer.
- **Any change to an E003-owned table this epic populates** — all six, not only the three the append-only revoke names: `document`, `chunk`, `field_vocabulary`, `extracted_value`, `extracted_value_contributing_chunk`, `extraction_failure`. `specs/00003-core-data-schema/data-model.md` is normative over this spec ({SAD:ADR-0017}); E006 populates all six, alters none, and adds only the records it owns in its own block (FR-065).
- **Optical character recognition** — every corpus document carries a real text layer, including those rendered to look scanned.
- **Correcting or re-typesetting source documents** — the corpus is byte-for-byte what E002 vendored.

### Edge Cases & Boundaries

An index; each condition is carried by the requirement or criterion named beside it.

- A structural unit above the 256 word-piece window — 476 of 9,020 real-layer leaves, largest 592 words — split to the sentence (FR-014, SC-041)
- A page carrying no detectable structural marker — 175 in the real layer — where the page is the terminal unit (FR-012)
- A field whose label ends one page and whose value begins the next, E002's `PAGE_SPLIT_FIELD` class: a multi-source value, never a chunk spanning the break (FR-029)
- An unedited UFGS master carrying bracketed alternatives nobody has chosen (FR-016)
- The PART 1 REFERENCES article, agency variants of one MasterFormat number, resubmittal chains differing only by revision suffix — three near-duplicate causes (FR-061, SC-048)
- Two corpus files whose stems normalize to one identifier (FR-052)
- A manifest content hash that no longer matches the file on disk (FR-005)
- A field printed with an alternate label (`Mfr` for `Manufacturer`) or absent entirely — neither defaulted into a value (FR-037, FR-057)
- A run interrupted part-way, survivable by per-document transaction under append-only privilege (FR-042, FR-054)
- An unreachable provider or a missing fixture in replay — a run-level failure, not one of the seven per-field outcomes (FR-056)

## User Scenarios & Testing *(mandatory for product specs only)*

### User Story 1 - Corpus becomes citable chunks (Priority: P1)

Every document is parsed and cut into chunks following its own structure, each recording its document, project, page, and specification section, with the vector that will later let it be found. A coordinator never sees a chunk, but every citation she does see resolves through one, so the page a chunk claims must be the page its words are on.

**Why this priority**: Nothing in this epic or in five downstream epics can begin without populated chunks.

**Independent Test**: Run ingestion against the committed corpus, pick any chunk, open the PDF it names at the page it names, and find its text there.

**Acceptance Scenarios**:

1. **Given** the 51-document corpus and an empty database, **When** the ingestion job runs, **Then** every document has a `document` row and every chunk carries a non-null document, document type, project, page number, and ordinal.
2. **Given** a chunk, **When** its text is compared against an independent extraction of the page it names, **Then** the text is found on that page — for every chunk in the corpus, not a sample.
3. **Given** a specification article exceeding the encoder's window, **When** the chunker processes it, **Then** the ladder descends — paragraph, subparagraph, sentence — until each fragment fits, and no chunk is embedded whose tail the encoder would discard.
4. **Given** a single sentence exceeding the window, **When** the chunker reaches it, **Then** the run fails and names it; a leaf that is merely long does not fail the run.
5. **Given** a real UFGS specification with unresolved bracketed alternatives, **When** it is chunked, **Then** the bracket markup is preserved verbatim.
6. **Given** a completed run, **When** it is run again against unchanged inputs, **Then** the same chunk boundaries and ordinals are produced.
7. **Given** a manifest entry whose content hash no longer matches the file on disk, **When** the run reaches that document, **Then** the run fails and no rows are written for it.
8. **Given** any chunk, **When** its page attribution is read, **Then** it names exactly one page — a structural unit crossing a page break is cut at the break into two chunks that each keep that unit's structural identifier.
10. **Given** a page on which no structural marker is detectable, **When** it is chunked, **Then** the page itself is the terminal structural unit, and the report names every document chunked that way with its count.
9. **Given** the ingestion report, **When** it is read, **Then** it names the shared-library project convention, the chunk-identity contract, and the zero-recognition-error upper bound.

### User Story 2 - Every extracted value points at its page (Priority: P1)

Line items are extracted from the 25 submittal transmittals — manufacturer, part number, quantity, dates and descriptor codes a vendor proposed. Each value carries the page it came from, inherited from its source chunk rather than asked of the model, so questioning a part number is one click from the page that prints it.

**Why this priority**: CAP-002's stated outcome and the product's core claim; the 100% traceability target is measured on exactly these rows.

**Independent Test**: Pick any extracted value, read its cited page, open that document at that page, and find the printed value.

**Acceptance Scenarios**:

1. **Given** a submittal transmittal chunk, **When** extraction runs, **Then** every value carries a source chunk, a cited page equal to that chunk's page, a field name, and a confidence.
2. **Given** any extracted value, **When** its cited page is compared against its source chunk's page, **Then** they agree — a value whose citation disagrees cannot be stored at all.
3. **Given** an extraction request, **When** it is issued, **Then** it goes through the single traced model path and no other module reaches the provider.
4. **Given** a manufacturer printed as `EMBERDYNE CONTROLS`, **When** it is extracted, **Then** the stored value is that text unaltered, with no normalized form stored beside it.
5. **Given** a field name the model proposes that is not in the seeded vocabulary, **When** persistence is attempted, **Then** the write is refused rather than the vocabulary being widened at run time.
6. **Given** the 26 real specifications, **When** the run completes, **Then** they have chunks and embeddings and zero extracted values, and that exclusion is recorded rather than inferred from an empty result.
7. **Given** a generated document, **When** its record is written, **Then** it carries its generator identity, seed, and generation date and no retrieval provenance, while a retrieved document carries its source, issuing body, and retrieval date and no generator fields.
8. **Given** an extracted value whose kind is numeric or a date, **When** the typed form is produced, **Then** it was coerced by deterministic code from the printed text rather than accepted as the model returned it.
9. **Given** the same transmittals, **When** the deterministic baseline extractor is run over them, **Then** its figures are published beside the model's on the same documents, with the baseline labelled strong or weak.

### User Story 3 - An untrustworthy value is absent, not wrong (Priority: P1)

When the model returns something that does not fit the schema, the system repairs once and then stops; a value it is barely confident in does not enter the record either. Both cases write a failure record naming the chunk, the field, and the cause, so Dana sees a gap she can investigate rather than a wrong number that looks like every other number.

**Why this priority**: A wrongly-stored value is the silent mistake the project's third principle exists to prevent — it propagates into matching, ranking, and the forecast with no symptom.

**Independent Test**: Drive extraction over a chunk whose response fails validation twice; confirm zero extracted values and one failure record naming the cause.

**Acceptance Scenarios**:

1. **Given** a model response that fails schema validation, **When** one repair attempt also fails, **Then** no value is persisted and a failure record is written with outcome `repair_budget_exhausted`.
2. **Given** an extracted value whose confidence falls below the published floor, **When** it is processed, **Then** it is not persisted and a failure record is written with outcome `confidence_below_threshold`.
3. **Given** an extracted value at or above the floor, **When** it is persisted, **Then** its confidence is stored with it and carried through to anything that displays it.
4. **Given** any failure record, **When** it is inspected, **Then** it carries no value text and no confidence.
5. **Given** a field the document does not print, **When** extraction runs over its chunk, **Then** the outcome is `no_value_found` rather than a defaulted or inferred value.
6. **Given** a completed run, **When** its confidence figures are published, **Then** the distribution is published rather than the mean, and the score is stated to be computed from parse signals and uncalibrated.
7. **Given** any stored extracted value, **When** its confidence is recomputed from the signals recorded with it, **Then** the result equals the stored score exactly.
8. **Given** the confidence floor, **When** the run's distribution is inspected afterwards, **Then** the floor is the value declared before the run and has not been moved to fit it.

### User Story 4 - A value split across a page break keeps both pages (Priority: P2)

Some fields carry their label at the foot of one page and their value at the head of the next — an irregularity E002 seeded. The extracted value records both pages rather than picking one.

**Why this priority**: A real corpus condition with a representation already in the schema; P1 stays viable without it because most fields sit on one page.

**Independent Test**: Extract from the document carrying the seeded page-split irregularity; the value records two contributing pages.

**Acceptance Scenarios**:

1. **Given** a field whose label and value straddle a page boundary, **When** it is extracted, **Then** the value is recorded as multi-chunk, anchored on the page that prints the value, with one contributing chunk for each *additional* page it drew on — the anchor is contributor 1 and never appears in the contributing set.
2. **Given** a multi-chunk value, **When** its declared source count is compared against its recorded contributors, **Then** they agree.
3. **Given** a value drawn from a single chunk, **When** it is recorded, **Then** it is marked single-chunk and has no additional contributors.

### User Story 5 - Every value names the run that produced it (Priority: P2)

Each run records what produced it: which agent and model, which chunker and embedding model at which revision, which corpus content, and when. Any chunk, value, or failure resolves to its run.

**Why this priority**: Nothing else in the project holds agent identity, the schema having omitted a per-row column on E006's behalf; P2 because a value is citable to a page without it.

**Independent Test**: Join any extracted value to its ingestion run and read the agent, model, chunker version, and embedding model revision that produced it.

**Acceptance Scenarios**:

1. **Given** a completed ingestion run, **When** its record is read, **Then** it names the agent identity, provider model, chunker version, embedding model identity and revision, corpus manifest digests, resolution mode, and its start and finish.
2. **Given** any chunk, extracted value, or failure record, **When** it is queried, **Then** exactly one ingestion run is reachable from it.
3. **Given** two runs over the same corpus with different chunker versions, **When** their outputs are compared, **Then** each row is attributable to the run that wrote it.

### User Story 6 - Re-ingesting is safe and repeatable (Priority: P3)

Running ingestion again does not duplicate what is there, and correcting a mistake is a documented remove-and-reload rather than an edit. Repeatability is also what lets continuous integration run the pipeline offline against committed responses.

**Why this priority**: Quality that matters the second time the job runs; P1 and P2 are demonstrable from one clean run.

**Independent Test**: Run ingestion twice against an unchanged corpus; row counts are unchanged and no chunk identity moved.

**Acceptance Scenarios**:

1. **Given** a populated database, **When** ingestion runs again over unchanged inputs, **Then** no duplicate chunks, values, or failures are created.
2. **Given** a document needing re-ingestion, **When** it is reloaded, **Then** its dependent rows are removed in an order the restricting foreign keys permit and reloaded, with no row updated in place.
3. **Given** a run that fails part-way, **When** the database is inspected, **Then** no document is left half-ingested.

## Requirements *(mandatory)*

### Functional Requirements *(product specs only)*

`data-model.md` is normative over this specification under {SAD:ADR-0017}. Where a requirement names a mechanism that document fixes — write order, removal order, referential actions, privileges, the named-object inventory, the `agent_id` grammar, the confidence arithmetic — the obligation is stated once here and the mechanism cited rather than restated.

**Corpus intake and document records**

- **FR-001**: System MUST enumerate corpus documents through the committed manifests, resolving each entry's location with the repository's containment-checked path resolution, and MUST NOT ingest a PDF that no manifest lists.
- **FR-002**: System MUST create exactly one document record per corpus document, deriving its identifier from the file stem by a stated transformation — lower-case the stem, replace every run of characters outside `[a-z0-9]` with a single hyphen, strip a leading or trailing hyphen — yielding `ufgs-23-52-00`, `prj-001-t0002-r0`. The result MUST satisfy E003's identifier format, `^[a-z0-9]+(-[a-z0-9]+)*$` at 3 to 128 characters; a stem whose transform does not MUST fail the run naming the file rather than being truncated, padded, or coerced.
- **FR-003**: System MUST record real specifications under the reserved shared-library project `PRJ-000` and synthetic documents under their own project, and MUST publish `PRJ-000` in the ingestion report as a named convention project-scoped readers must account for. `PRJ-000` MUST be reserved corpus-wide against any producer minting it as an ordinary project identifier, and the report MUST state that nothing structural enforces the reservation.
- **FR-004**: System MUST carry each document's layer, license basis, and layer-appropriate provenance from the manifest into its record unchanged, and MUST NOT give a generated document retrieval provenance it does not have.
- **FR-005**: System MUST verify each document's recorded content hash against the file on disk before parsing it, and MUST fail the run without writing rows when they differ.
- **FR-006**: System MUST classify each document by type from a closed set — real specifications as specifications, synthetic transmittals as transmittals — and never invent a type outside it.
- **FR-052**: System MUST fail the run, naming both files, when two corpus files yield the same derived identifier, rather than overwriting one document record or attaching one document's chunks to the other. The check MUST complete over the **whole enumerated corpus before the first document transaction commits**, so a colliding run writes zero rows.

**Parsing and page provenance**

- **FR-007**: System MUST derive every page number from the parser and MUST NOT accept, request, or store a page number produced by a language model.
- **FR-008**: System MUST obtain page text by calling the committed reader the repository provides — its pinned word tolerances, line grouping, normalization — and MUST declare no second tolerance mapping, no second normalization, and no second page-text assembly anywhere in the ingestion package. SC-037 is therefore a contract check over the absence of a second reader, not a measured agreement; that the reader reads any page correctly is carried by FR-010 and FR-011 and disclosed.
- **FR-009**: System MUST NOT require optical character recognition for any document, and MUST disclose in the ingestion report that the corpus text layer carries zero recognition error, making every accuracy figure here an upper bound a genuinely scanned corpus would not reproduce. **The claim MUST be stated per layer**, the datasheet governing only one of them: for the **synthetic** layer, the only one extraction accuracy is measured on (SC-012), the datasheet records zero recognition error by construction; for the **real** layer the claim MUST be published as the narrower one — no recognition step is performed at any point, while whether the embedded text layer already disagrees with its printed page is unmeasured.
- **FR-010**: System MUST verify, for every chunk in the corpus and not a sample, that the chunk's text is present in an independent extraction of the page it names. That extraction MUST be a **fresh read of the document's own bytes, taken after the run and addressed by the chunk's recorded page number**, and MUST NOT consult the chunker's cached page text, its page-to-chunk mapping, or any other in-memory artifact of the run that wrote the chunk. It is **not** independent of the parser — both sides read through the one reader FR-008 fixes — and that limit is disclosed. Two actors discharge it: the **ingestion job** re-checks containment inside each document's transaction before it commits, so a chunk whose text is not on its named page is never written; the **verification suite** re-asserts containment corpus-wide against the post-run extraction and publishes the population it enumerated (FR-068).
- **FR-011**: System MUST record in the ingestion report, for any claim resting on human inspection rather than FR-010's total check, the items inspected, the defects found, and the error bound that sample supports. **The set of such claims MUST be enumerated**, and published even where a claim's inspected count is zero; its known member is **structural detection on the 26 real specifications**, for which no reference exists. Every other claim MUST appear in the enumeration with its inspected count or name the total check that carries it. The **method is fixed**: with zero defects the bound is the rule-of-three 95% upper bound **3/n**, stated with *n* and never quoted for *n* ≤ 30; with one or more defects it is the **continuity-corrected Wilson 95% interval** on the observed defect proportion (FR-060), with its denominator printed.

**Chunking**

- **FR-012**: System MUST place every chunk boundary in one of three named classes — a structural boundary the parser identified, a page break, or a sentence boundary inside a leaf above the encoder window — and MUST place zero boundaries at a fixed character, word, or token offset. A fragment produced by a page or sentence boundary MUST keep the structural identifier of the unit it came from.
- **FR-013**: System MUST confine each chunk to a single page.
- **FR-014**: System MUST measure chunk length in the encoder's own tokenizer and MUST descend the boundary ladder — article, paragraph, subparagraph, sentence — until a unit fits, rather than embedding a chunk the encoder would silently truncate, failing the run and naming the unit only when a single sentence exceeds the window.
- **FR-015**: System MUST record on each chunk the document, document type, project, page number, and a zero-based ordinal unique within its document, with the specification section and heading where supplied. Ordinals MUST be assigned in reading order — ascending page, then position within the page — and MUST be contiguous from zero: a page yielding no storable text produces no chunk and consumes no ordinal.
- **FR-016**: System MUST store chunk text as it appears in the source, including unresolved bracketed alternatives in unedited masters.
- **FR-017**: System MUST produce the same chunk boundaries and ordinals for **the same input tuple** — that document's content hash, the chunker version, *and* the embedding encoder identity and revision (FR-043) — and MUST record all three on the run so a boundary change is attributable to whichever member moved. **What obliges a chunker-version bump MUST be stated rather than inferred**: the version MUST change when the boundary-class rules (FR-012), the structural detection the ladder descends (FR-014), the ordinal assignment rule (FR-015), or the identity or pinned version of the sentence segmenter changes. The encoder identity and revision are deliberately **not** in that list.
- **FR-018**: System MUST record in the ingestion report the chunk-identity contract consumers depend on: **a chunk identifier is minted by the run that writes it**, is stable only while that generation is resident, and an evaluation set must therefore key on document, page, and quoted span. The contract MUST be stated over the *run* rather than the chunker, FR-043's tuple re-minting identifiers even where boundaries are identical. Chunk identity is deliberately **not** required to be a function of content and position.

**Embedding**

- **FR-019**: System MUST embed every chunk locally with a pinned model identity and revision, reaching no network at run time. The encoder artifact and its tokenizer MUST resolve from a repository-committed path integrity-checked against recorded digests before the encoder session is created, and the run MUST fail naming the artifact rather than fall back to a remote fetch when a digest does not match or a file is absent. The tokenizer MUST be pinned to the encoder's revision, boundaries being measured in it (FR-014). **The exported encoder MUST be accepted only against a parity tolerance in three parts** ({SAD:ADR-0018}): **declared** before the comparison runs; **measured over a committed probe set spanning both corpus layers**; **published with its observed maxima beside the declared bounds**. The bounds are **cosine similarity ≥ 0.999999 for every probe** and **maximum absolute per-dimension difference ≤ 1e-5**. A run MUST fail rather than embed when either is breached, and **an observed maximum landing near either bound MUST be published as a finding about the export**, never absorbed by widening the bound (Principle VII).
- **FR-020**: System MUST record the embedding model identity and revision on every chunk, so vectors from two versions are never silently mixed.
- **FR-021**: System MUST take the vector dimension from the value the schema publishes at run time.

**Extraction**

- **FR-022**: System MUST extract line items from the synthetic transmittals only, and MUST record that exclusion explicitly rather than leaving an empty result to be interpreted.
- **FR-023**: System MUST issue every model request through the single traced model path, and MUST NOT import or reach the provider client from any other module.
- **FR-024**: System MUST draw every extracted field name from the seeded field vocabulary **and only from terms unretired at run time**, and MUST refuse a value naming a term outside that set rather than widening the vocabulary at run time. The unretired filter is this epic's obligation, retirement being advisory in the normative schema (E003 G-7). **A refusal under this requirement MUST be recorded as an extraction failure with outcome `schema_violation`** (FR-034); no new outcome is introduced for it.
- **FR-025**: System MUST validate every model output against the caller's schema before persisting it, and MUST NOT persist, return, or log an unvalidated value.
- **FR-026**: System MUST attempt at most one repair after a validation failure and then fail closed.
- **FR-027**: System MUST store manufacturer and part-number values exactly as printed, and MUST NOT store a normalized, cleaned, or canonicalized form in place of them or alongside them. **This prohibition is scoped to text-kind values**, which manufacturer and part number are; numeric and date kinds are governed by FR-062.
- **FR-028**: System MUST NOT assert that two differently-spelled manufacturer names are the same manufacturer.

**Citation and confidence**

- **FR-029**: System MUST give every extracted value a page citation inherited from its source chunk, and MUST make a citation disagreeing with that chunk unstorable rather than merely detectable. Where a field's label ends one page and its value begins the next, the anchor MUST be **the chunk carrying the printed value**, never the one carrying only the label; the label's page is recorded as an additional contributing chunk. The anchor being the *later* page, every comparison reassembling such a value MUST order its chunks by page rather than by contributor position (SC-027).
- **FR-030**: System MUST record a per-field confidence on every extracted value, with no exemptions.
- **FR-031**: System MUST compute each per-field confidence deterministically from three observable parse signals — whether the printed field label matched the canonical form or a known alternate, whether it was read from one chunk or assembled across a page break, and whether the invocation validated on the first attempt or only after a repair — and MUST NOT store a confidence a language model asserted about its own output. A fourth signal originally listed, whether the value was printed or absent, was withdrawn during clarification and MUST NOT be computed: an absent value is a failure record rather than a stored value with a confidence. System MUST NOT present the score as a calibrated probability, publish it as a frequency, or compare it across fields as though the values shared a scale.
- **FR-032**: System MUST fix the confidence floor as a declared value chosen before the first run and MUST NOT move it in response to the distribution that run produces; a value below it is recorded as a failure rather than persisted, and one at or above it is persisted with its confidence intact.
- **FR-033**: System MUST record in the ingestion report the declared floor and the observed distribution of confidence values rather than a mean, as disclosure and not as an input to choosing the floor. The distribution MUST be denominated on **every confidence the run computed, including every score the floor rejected**, the two populations labelled and counted separately, the rejected half carried from the run's own tally rather than queried from rows (FR-036) and labelled as such. The form is fixed: **all eight scores FR-057's three binary signals admit, with counts**, a score nothing took appearing as a **zero** rather than an absent row. System MUST also print, beside the distribution and not only in a limitations table, that the score is a heuristic ordering rather than a calibrated probability, **with the condition that would reverse it** — a frozen, hashed labelled sample of extracted fields.

**Failure handling**

- **FR-034**: System MUST classify every extraction failure with one outcome from the closed set of seven the schema defines, and MUST NOT introduce a new outcome value. The seven are enumerated here rather than referred to by count and owner — `no_value_found`, `unparseable_value`, `type_coercion_failed`, `schema_violation`, `missing_citation`, `confidence_below_threshold`, `repair_budget_exhausted`. It restates E003's `ck_extraction_failure__outcome`; where they disagree that document governs ({SAD:ADR-0017}). System MUST publish the failure count **broken down by each of the seven**, denominated per attempt (FR-069), an outcome no failure took published as a zero.
- **FR-035**: System MUST record on each failure the source chunk, attempted page, field, repair attempt count, and a diagnostic detail.
- **FR-036**: System MUST NOT attach a value or a confidence to a failure record.
- **FR-037**: System MUST record a field the document does not print as not found rather than defaulting, inferring, or omitting it silently.

**Run attribution**

- **FR-038**: System MUST write one ingestion-run record per run naming the agent identity, provider model, chunker version, embedding model identity and revision, corpus manifest digests, resolution mode, and start; and MUST record the finish **when the run completes**. A run that aborted MUST carry no finish, and a run that recorded a run-level failure (FR-056) MUST NOT carry one at all. Every field other than the finish and the two run-failure columns is present on every run record, aborted or not (SC-022). **The resolution mode takes one of exactly two values, `record` and `replay`**. The record's full column inventory — including the floor and three deduction weights (FR-057) and the trace identifier (FR-070) — is fixed in `data-model.md` §`ingestion_run`.
  **The agent identity MUST name both the invoking principal and the executing build**, in the parseable grammar `data-model.md` §`ingestion_run` declares and enforces by a format check on the column (`ck_ingestion_run__agent_id_format`) rather than by convention; E003's TR-082 dropped its per-row agent column on the grounds that this epic holds identity at run granularity. **The provider model is deliberately not a member**, having its own column. **Three run states are readable**: in flight (no finish, no failure kind), aborted (a failure kind, no finish), complete (a finish, no failure kind). The fourth is disclosed rather than claimed away — a run whose process died before writing its failure columns reads as in flight forever, its recovery the same as any abort (FR-042).
- **FR-039**: System MUST make every chunk, extracted value, and failure record resolve to exactly one ingestion run through associations this epic owns in its own migration block, those three tables carrying no run column. **Attribution is specified for every row kind this epic writes**: the line-item association (FR-059) and the parse-signal record (FR-063) MUST carry the run and document **directly**, equal to their value's own attribution; contributing-chunk rows carry **none** and resolve through their parent value.
- **FR-040**: System MUST claim migration prefix block `0300`–`0399` for any schema object it adds, leave `0200`–`0299` to the epic sharing its wave, and MUST NOT place an object inside another epic's block. System MUST **enumerate by name every database object its migrations create** in `data-model.md` §Named Object Inventory; an object absent from it is a defect whether or not its prefix is in range. System MUST also amend the build-gating migration-block partition check to declare **both** `0300`–`0399` as this epic's block and `0200`–`0299` as reserved-and-empty.
- **FR-065**: System MUST add **zero columns, zero constraints, and zero indexes** to any of the six E003-owned tables it populates — `document`, `chunk`, `field_vocabulary`, `extracted_value`, `extracted_value_contributing_chunk`, `extraction_failure` — and MUST make that verifiable by comparing those tables' catalog entries before and after this epic's migrations rather than by review.

**Re-running and correction**

- **FR-041**: System MUST NOT update an extracted value, a contributing-chunk record, or a failure record in place; a correction is a remove-and-reload of the whole affected document, in the order and with the referential actions `data-model.md` §Operator Procedures and §Referential Actions fix. The ingestion job connects as the **application role `procurement_app`**, whose `UPDATE` and `DELETE` are revoked on the three provenance tables; the removal MUST be executed under the **schema-owning role**, MUST NOT be reachable from the ingestion job, and this epic does not weaken the revoke.
- **FR-066**: System MUST carry the same append-only posture onto **every table it adds** — the generation record, the three run-output associations, the line-item association, the parse-signal record — granting the application role `SELECT` and `INSERT` and withholding `UPDATE` and `DELETE` (`data-model.md` §Privileges), and MUST enumerate the updates that remain permitted anywhere in this epic's object set. **The permitted updates are four and no others**: on the ingestion-run record, the **finish timestamp** and the **two run-failure columns**, written by the ingestion job; and on the generation record, the **`active`-to-`superseded` mark**, permitted **only under the schema-owning role** and never reachable from the ingestion job, naming the generation the promotion then removes and moving with the removal (FR-055, SC-024). `DELETE` is withheld on the run record too.
- **FR-042**: System MUST leave no document half-ingested when a run fails part-way, and MUST state the standing of the documents already committed when a run aborts — from a hash mismatch (FR-005), an identifier collision (FR-052), an over-long sentence, or a run-level invocation failure (FR-056): they remain durable, **their generations remain active**, and the corpus is consumable in that state. The run record carries its failure and no finish, so a partial corpus is identifiable as partial.
- **FR-043**: System MUST define a document's inputs as the tuple of **that document's own** recorded content hash, the chunker version, the embedding model identity and revision, the provider model, and the extraction prompt and schema digest; MUST skip a document whose tuple is unchanged, creating no rows for it; and MUST reload only the documents whose tuple differs. The digest is per document rather than corpus-wide, which would reload all 51, and the provider model is a member so an unchanged document is never skipped under a model whose fixtures differ. **Reloading a document that already holds a generation is the promotion FR-055 defines and the remove-and-reload FR-041 defines — one behaviour, not two**: the same removal, in the same order, inside the transaction that writes the successor. A run replacing any existing generation MUST therefore execute under the schema-owning role for its whole length; a run of first ingests and skips alone runs unattended under the application role.

**Execution environment**

- **FR-044**: System MUST run ingestion offline only, never on a request path.
- **FR-045**: System MUST complete a full ingestion run in continuous integration with no provider call and no network access, every model response resolved from committed fixtures. **The resolution key is stated rather than assumed**, a narrower key being what makes a stale fixture replay silently: the traced path's own, whose hashed inputs include the request — provider model and prompt text among them — the output schema's digest, and the prompt template's digest. E006 inherits E004's key rather than declaring a second, so **a changed prompt or schema constraint resolves to a miss and to FR-056's run-level failure**. Fixtures MUST be re-recorded whenever the prompt text or an output schema constraint changes, as a `record`-mode run whose fixtures are committed with the change that invalidated them.

**Boundary enforcement and baseline**

- **FR-046**: System MUST record in the ingestion report the signals a computed confidence derives from and each weight, so a score is explainable and recomputable from the stored row.
- **FR-048**: System MUST place its model-facing modules under `model.llm`, which the computation-boundary contract already names as a source module. A forbidden contract covers its named package's descendants, so placement is the whole mechanism and no contract edit is required. The gap it leaves is a module placed **outside** that package, which the provider-import scan also misses — that scan looks for the provider distribution, not the gateway — so System MUST assert that only `model.llm` may import the gateway.
- **FR-049**: System MUST coerce an extracted value to its numeric or date form in deterministic code rather than accept a typed value the model produced.
- **FR-050**: System MUST report every extraction quality figure against a deterministic baseline extractor run over the same transmittals, and MUST publish **two labels**, each by a stated criterion. The **declared** label MUST be fixed **before any figure exists**: **strong** where the baseline is authored under the independence contract below *and* template-driven over a corpus generated from fixed per-vendor templates, **weak** otherwise. The **observed** label is read off the published table: strong where the baseline beats or ties the model on at least one per-field figure, weak where the model dominates every field. **A disagreement between the two MUST be published as a finding and MUST NOT be reconciled by revising either label** (Principle VIII).
  **"Extraction quality figure" is a named set, not a description** (SC-029): **the per-field precision and recall figures FR-060 publishes, per field and per layer**. The repaired rate (SC-018), confidence distribution (FR-033), and near-duplicate cluster counts (FR-061) are deliberately **outside** it, none having a baseline counterpart. Every figure in the set MUST be published with an interval.
  **The baseline MUST be authored independently of the generator's own definitions**: it MUST read only the rendered documents' text, and MUST NOT import, read, or transcribe the generator's layout templates, its rendering code, or the pre-render document model FR-067 fixes. The independence MUST be enforced by an import contract naming the forbidden modules, not by review. **One shared input is permitted**: the committed field-label vocabulary FR-057 already gives the model path.

**Cross-epic obligations**

- **FR-047**: System MUST record, without performing it, the amendment that computing confidence requires: E003's TR-081 fixes per-field confidence as "a self-reported score asserted by the extracting agent", and a deterministically computed score is not that. TR-081's non-calibration half survives and is restated in FR-031; the self-reported half does not. Amendments to registered documents serialize on the default branch, so this branch records the need — and **implementation MUST NOT begin until the amendment lands**.
- **FR-063**: System MUST record, in a table this epic owns, the parse signals each value's confidence was computed from — canonical or alternate label, assembled across a page break or not, validated on the first attempt or after a repair.
- **FR-064**: System MUST perform the drop and rebuild of the vector index around a full-corpus load as an operator procedure under the schema-owning role (`data-model.md` §Operator Procedures), and MUST NOT reach it from the ingestion job, which holds no privilege to alter an index another epic owns. System MUST state that similarity queries fall back to a sequential scan while the index is absent, and that an abort mid-load leaves it absent until the procedure is re-run.
- **FR-051**: System MUST claim decision-record numbers ADR-0018 through **ADR-0020**. Two were claimed at epic start; the third during the Checklist phase, when a requirements-quality pass found ADR-0019's retention clause unimplementable.

**Measured rather than assumed**

- **FR-053**: System MUST measure and record in the ingestion report the leaf-unit length distribution across all 51 documents in the encoder's own tokenizer, the count of leaves requiring a sentence-level split, and the documents chunked at the page-terminal fallback, **each with its own count of page-terminal chunks**. The 26 real specifications are structurally uncharacterized, so the distribution MUST be measured rather than inferred from the standard's format rules. System MUST also publish **the chunk count in each of FR-012's three boundary classes**, a class holding no boundaries published as a zero. **Every figure this requirement obliges MUST be published per layer as well as pooled** (FR-072).
- **FR-061**: System MUST record in the ingestion report the near-duplicate chunk cluster counts by cause — dense reference-designation lists, agency variants of one MasterFormat number, resubmittal chains differing only by revision suffix — as exact matches on normalized text and above a declared threshold. **The measure and thresholds MUST be fixed before the run and MUST NOT be chosen after the clusters are observed.** The measure MUST be **cosine similarity over the chunk embeddings this epic already computes**. The count MUST be published **at every threshold in the declared grid 0.80, 0.85, 0.90, 0.95, 0.99**, so what is published is a curve rather than a point.

**Run integrity**

- **FR-054**: System MUST commit **every** row of one document in a single transaction, in the order `data-model.md` §Write Order and Transaction Boundary states row by row. An aborted run therefore rolls back only the document in flight, restoring its prior generation where it had one, and needs no deletion privilege to clean up after itself.
- **FR-055**: System MUST mark a run's work **per document** as active or superseded rather than the run as a whole, a run re-ingesting only the documents whose tuple changed. At most one generation per document MUST be active, and downstream readers MUST filter on it. The bound readers are named — **E008 (retrieval), E009 (identity resolution), E012 (source-page traceability)** — with the outcome when one does not filter: it gets the resident generation's rows but **no run attribution**, and cannot distinguish "no live generation" from "one live generation". Both failures are silent. **Promotion MUST remove the prior generation's rows for that document rather than retaining them** (`data-model.md` §Operator Procedures), chunk ordinals being unique within a document rather than within a generation. Reverting a promotion is a re-run of the previous chunker version, not a status change, and no retention bound is needed because no superseded rows accumulate.
- **FR-056**: System MUST treat a missing fixture in replay, or an unreachable provider, as a named run-level failure that aborts the run, reported distinctly from per-field extraction failure and never under any of the seven per-field outcomes. The record explaining an aborted document MUST be written **after the rollback, in a fresh transaction**, as a run-level failure on the ingestion-run record rather than a per-field row (`data-model.md` §Write Order and Transaction Boundary).
  **The run-level set is closed at five, and each aborting requirement is mapped to the kind its abort is recorded under**: `corpus_digest_mismatch` (FR-005), `document_id_collision` (FR-052), `oversized_sentence` (FR-014), `fixture_missing` (FR-045), `provider_unreachable`. **One abort is outside the set**: FR-019's encoder-artifact check runs before the run record exists — a startup refusal, not a run that failed. The five kinds and the seven per-field outcomes (FR-034) MUST share zero values, checked as a set intersection over the two declared domains.
  **The diagnostic detail has a required content.** Every run-level failure MUST record, beside its kind, the subject its aborting requirement names — document in flight and offending file for `corpus_digest_mismatch`; **both** colliding files and the identifier they produced for `document_id_collision`; document, page, and structural unit for `oversized_sentence`; the resolution key that missed for `fixture_missing`; provider and model addressed for `provider_unreachable` — and MUST name the document in flight wherever one exists (FR-073).

**Extraction policy**

- **FR-057**: System MUST compute confidence as 1.0 less declared deductions — 0.15 where the printed field label matched a known alternate rather than the canonical form, 0.10 where the value was assembled across a page break, 0.25 where the invocation validated only after a repair — resolving alternate labels against the field-label vocabulary E002 committed rather than a list invented here. The floor is **0.80**, and MUST be stated as the combinations it excludes: any repaired invocation, and any value both alternate-labelled and page-split. System MUST record the three deduction weights on the run record alongside the floor and MUST apply them in the declared order `data-model.md` §`ingestion_run` fixes, so that "reproduces the stored value exactly" means bit equality.
- **FR-058**: System MUST attempt only the declared transmittal field subset per chunk rather than all twenty-two vocabulary terms, **twelve** of which cannot appear on a transmittal, and MUST record a field absent from an entire document once per document rather than once per chunk. **The subset MUST cover every vocabulary term the generator can print on a transmittal**, so no printed field goes unattempted by construction, FR-060 denominating recall on everything the generator recorded as printed. A printed field falling outside the subset MUST be published as unattempted-but-printed rather than absorbed into the miss total — **including a printed field the vocabulary has no term for at all**, which is outside the subset in the strongest sense available and is therefore the case this obligation most needs to reach. Such a field is rightly outside recall's denominator (FR-060), nothing being storable for it, and that exclusion MUST NOT be read as an exclusion from this publication. The per-document absence record still carries the source chunk and attempted page FR-035 requires, naming the **lowest-ordinal chunk the field was attempted on** and that chunk's page — deterministic under FR-015, a stated convention rather than a claim about where the field would have been printed. *(Amended 2026-07-28, two corrections. **The count was inverted**: the seeded vocabulary holds 22 terms, the corpus generator prints 10 of them on a transmittal, and 12 cannot appear — so "ten of which cannot appear" named the wrong side. The code was right and the requirement was wrong: `llm/schemas.py` declares a 10-term `TRANSMITTAL_FIELD_SUBSET` and 12 `EXCLUDED_TERMS`, each with its stated reason, and `_validate_declaration` asserts at import that the two partition the vocabulary; `corpus/generate.py` composes exactly 17 printed field keys of which `ingest/reference.py` maps 10 onto vocabulary terms. The requirement text was corrected rather than the declaration. **The escape hatch excluded what it exists to surface**: the seven printed keys with no vocabulary term — contract number, project identifier, vendor name, descriptor code, approving authority, revision suffix, date received — are printed on all but a handful of the 25 transmittals, **170 printed fields in total** (short of 7 x 25 because `MISSING_OR_BLANK_FIELD` blanks a few, and a blanked field is not a printed one), and the reference set filtered them out before the report saw them — so report item 14 published zero unattempted-but-printed fields on a corpus with 170 of them. The obligation is now explicit about that population. Evidence: `ingest/reference.py` `printed_without_term` and `ingest/report.py` `unattempted_fields_section`.)*
- **FR-062**: System MUST keep the printed text of a value as the evidence its citation points at, and MUST hold any coerced numeric or date form in the column the schema provides for it. **This requirement governs numeric and date kinds**; text kinds are governed by FR-027, whose prohibition on storing a canonical form alongside the printed text does not reach here, and the two scopings do not overlap. Where the schema stores a canonical form differing from the printed text — a date normalized to ISO-8601 — the printed form remains recoverable from the cited chunk, so comparisons asserting agreement with the printed document apply to text-kind values.
- **FR-059**: System MUST associate every extracted value with its line item, through an association this epic owns in its claimed block, keyed on the **value alone** — so a second membership is unrepresentable rather than merely wrong — and grouped by **run, document, and item ordinal**. The association's run and document MUST be held equal to the value's own attribution, so membership is scoped to a generation. **Item ordinal 0 is a named group meaning "printed once for the whole document"**, real items numbered from 1, which keeps SC-046 absolute; it is a declared membership, not a sentinel to pattern-match. Per-field cardinality *within* an item is **left unasserted** and disclosed as uncovered (Disclosed Limitations).

**Measurement**

- **FR-067**: System MUST score every extraction accuracy figure against the generator's **pre-render document model** as the reference set — the per-document record of the fields and page text composed before rendering — and MUST verify it is the committed one before any figure is computed, by reproducing it from the committed seed and generation inputs and requiring equality with the document-model digest each synthetic manifest entry carries. System MUST NOT score a stored value against the chunk text it was read out of, against the page text this epic's own parse produced, or against any other artifact of the run being measured. The real layer has no such reference (FR-060).
- **FR-068**: System MUST publish, with every total check it claims, the population that check enumerated and its count, and MUST fail rather than report success when that count is zero. This binds every criterion phrased as "100%" or "zero" over an enumerated population, among them SC-002, SC-003, SC-009, SC-013, SC-027, SC-046.
- **FR-069**: System MUST record the field extractions it attempted and resolve every attempt to exactly one stored value, one failure record, **or one correct negative**, with zero unaccounted for. A **correct negative** is an attempt for which no value was offered because the field is not printed on that chunk; it MUST be counted and published in its own right, never folded into either of the other two and never left unaccounted for. **The counting units MUST be named beside every published figure**. An **attempt** is one field on one chunk, except a field absent from a whole document, which is one attempt per document (FR-058). An **invocation** is one model request covering one chunk's declared field subset, and is the unit FR-025's validity and FR-026's repair budget are counted in. A **document** is the unit of the whole-document absence record and of the transaction (FR-054). The three resolution counts MUST be published in **attempt units rather than in rows**, a chunk printing one field for two line items storing two values against one attempt. SC-018's valid, repaired, and failed counts MUST be published **per invocation**, per-field outcomes and any failure rate **per attempt**, as two tables rather than one whose rows do not share a denominator. *(Amended 2026-07-28. The original admitted two resolutions — "exactly one stored value or one failure record" — and that binary cannot balance on any real run. The model is invoked per chunk over the whole declared field subset, so a field printed on chunk 1 of 10 is correctly absent from the other nine; those nine attempts resolved, and resolved correctly, to nothing offered. Under the binary they were unaccounted for, so `unaccounted` was large and non-zero whenever anything was extracted at all — a ledger reporting a defect on every correct run reports nothing. The requirement's intent is unchanged and its teeth are unchanged: every attempt is still resolved, the resolution set is still closed, and the unaccounted count is still published whether or not it is zero, so a genuine imbalance is still visible. What changed is that the closed set now has the member reality has. Evidence: `model/ingest/extract.py` `_resolve_attempts` and `model/ingest/report.py` `AttemptLedger`.)*
- **FR-070**: System MUST issue every extraction invocation of a run under one run-scoped trace identifier, record that identifier on the ingestion-run record, and reconcile in the report the invocations attempted (FR-069) against those recorded under it, publishing both counts and requiring them equal.
- **FR-060**: System MUST publish per-field precision and recall with Wilson 95% intervals, per field and per layer, beside the baseline's figures on the same documents. **The variant is fixed as the continuity-corrected Wilson score interval and MUST be named with the figures**, and MUST be used everywhere an interval on a proportion is published in this epic, including FR-011's defect bound. System MUST NOT publish F1 — Wilson inverts the score test for a **binomial proportion**, and F1 is a harmonic mean of two proportions with different denominators, so no interval for it exists while SC-029 admits no figure without one — and the omission MUST be published with this reason.
  **Both denominators MUST be stated and printed beside their figures**: precision on **the values the run stored for that field and layer**, its numerator those matching the reference FR-067 fixes; recall on **the fields the generator recorded as printed**, never on the values stored.
  **"Per layer" MUST NOT be published as a figure over an empty denominator.** The real layer yields zero extracted values by design (SC-012) and has no generator record to denominate recall on, so it MUST be published as **not measured, with the reason**, never as `0/0`, `0%`, or a blank cell. **Each interval MUST be labelled descriptive rather than inferential**, the 25 transmittals being a seeded set from which no population was sampled, and MUST NOT be read as a confidence statement about extraction outside this corpus.

**The ingestion report as a published artifact**

- **FR-071**: System MUST publish the ingestion report as **one committed artifact at `specs/00006-document-ingestion-and-extraction/ingestion-report.md`** — one per repository, not one per run — regenerated in full by any run that writes or replaces a generation. **Its required content is the closed list below**; an item in the report but absent from the list is a defect in the list, and a list entry with nothing under it is a defect in the report.

  | # | Item | Obliged by |
  |---|---|---|
  | 1 | `PRJ-000` convention and its unenforced reservation | FR-003 |
  | 2 | Zero-recognition-error upper bound, per layer | FR-009 |
  | 3 | Human-inspection claims with counts and bound | FR-011 |
  | 4 | Chunk-identity contract | FR-018 |
  | 5 | Recorded exclusion of the 26 real specifications | FR-022 |
  | 6 | Floor, eight-score distribution with rejected and stored counted apart, weights, application order | FR-033, FR-046, FR-057 |
  | 7 | Failure count by each of the seven outcomes | FR-034 |
  | 8 | Chunk counts, total and per layer, against the 5,000–15,000 estimate with cause of deviation | SC-005 |
  | 9 | Leaf-length distribution, sentence-split count, boundary-class counts, page-terminal documents with counts | FR-053 |
  | 10 | Multi-chunk value and contributing-chunk row counts | FR-029 |
  | 11 | Near-duplicate cluster counts by cause, exact and per threshold | FR-061 |
  | 12 | Per-field precision and recall with intervals and denominators, the baseline's figures, both labels and any disagreement, F1's omission and reason | FR-050, FR-060 |
  | 13 | Valid, repaired, failed counts as invocation- and attempt-level tables with units | FR-069 |
  | 14 | Count of fields printed but not attempted | FR-058 |
  | 15 | Attempted-versus-recorded invocation reconciliation | FR-070 |
  | 16 | Per-document disposition ledger and its four counts | FR-073 |
  | 17 | Population and count behind every total check | FR-068 |
  | 18 | Sequential-scan fallback while the index is absent, and its absence after an abort | FR-064 |
  | 19 | Reproduction tolerance in force for each figure | FR-074 |
  | 20 | Scope labels on every figure above | FR-072 |
  | 21 | Encoder parity bounds declared before the comparison, with observed maxima | FR-019 |

  **The run's account is closed at two artifacts and no third**: the ingestion-run record with its associations, plus this report. No published figure may rest on console output, a log line, or any uncommitted artifact; a figure not recomputable by query (FR-033, FR-069, FR-073) MUST be carried from the run's own ledger and labelled as such (FR-072). **Regeneration replaces**: a regenerated report MUST replace the prior one wholesale.
- **FR-072**: System MUST label every figure the ingestion report publishes with **the run it was computed under, the generation set it ranges over, and its kind**, and MUST name in the report, by identifier, the run record it describes.
  - **Run and generation set.** Each figure MUST declare itself **corpus-resident** — computed by query over the generations resident when the report was written, naming the runs they belong to — or **run-scoped**, over the one named run's work and not recomputable from rows.
  - **Kind.** Every figure MUST be published as a **census** — a total check carrying its population and count (FR-068) and **no** interval — a **sampled estimate**, one of FR-011's claims with its counts and bound — or a **descriptive figure over a designed set**, which the per-field extraction figures are (FR-060).
  - **Unit and layer.** Every figure MUST name its counting unit — attempt, invocation, or document (FR-069) — and one whose population spans both layers MUST be published per layer as well as pooled (FR-053, FR-061).
- **FR-073**: System MUST publish, for every run, a **per-document disposition** from a closed set of four with the count of documents in each: **`ingested`** — a generation was written for it; **`skipped_unchanged`** — its input tuple was unchanged, so FR-043 created no rows for it; **`rolled_back`** — it was the document in flight when the run aborted; **`not_reached`** — it was enumerated but never begun. The four MUST partition the enumerated corpus, their counts MUST sum to the enumerated document count FR-068 publishes, and a disposition holding zero documents MUST be published as a zero.
- **FR-074**: System MUST make every figure the report publishes reproducible by a replay-mode run from a clean checkout (FR-045) against a committed results manifest, **within a tolerance printed with the figure** (the reproduction gate {SAD:ADR-0009} fixes). Every **count, rate derived from counts, interval computed from them, and stored confidence** MUST reproduce **exactly**, as bit equality. The one class **not** claimed exact is the near-duplicate cluster counts (FR-061), whose inputs are floating-point vectors from the exported encoder: they MUST reproduce within the encoder parity tolerance FR-019 declares — cosine similarity ≥ 0.999999 and maximum absolute per-dimension difference ≤ 1e-5 — printed with the counts beside the maxima observed. A reproduction outside the stated band MUST be published as a failure of the gate; widening the band to admit it is adjusting a target to match a result, which Principle VII forbids.

### Key Entities *(include for product or technical specs if feature involves data)*

Column-level detail is in `data-model.md`; the four marked **new** are what this epic adds.

- **Document**: One corpus PDF as a record — identifier, type, project, title, layer, license basis, provenance.
- **Chunk**: A contiguous run of text from one page of one document, cut on its structure, carrying document, type, project, page, ordinal, specification section, heading, and its embedding with the model identity and revision that produced it. The unit every citation resolves through.
- **ExtractedValue**: One field read out of one chunk — field name, value, kind, confidence, page citation inherited from its source chunk. Marked single-chunk or multi-chunk.
- **ContributingChunk**: For a value assembled across a page break, the chunks beyond the anchor, each with its page.
- **ExtractionFailure**: A field that could not be extracted — source chunk, attempted page, field name, one outcome from the closed set, repair attempts spent, diagnostic detail; no value and no confidence.
- **IngestionRun** (new): One execution of the pipeline, holding the attribution FR-038 fixes; the record E006 owes because the schema left per-row agent identity out.
- **RunOutput** (new): The association between a run and each chunk, extracted value, and failure it wrote, owned by this epic rather than added as a column to tables E003 owns.
- **ParseSignal** (new): The three signals one value's confidence was computed from (FR-063); two exist in no column anywhere.
- **LineItem** (new): One proposed material or equipment entry on a transmittal, and the association binding the values read out of it into one item. Survives an entry split across two chunks.
- **BaselineExtraction**: What a deterministic template-driven extractor produced from the same transmittals, authored from the rendered documents alone (FR-050).

## Assumptions & Risks *(mandatory)*

### Assumptions

- Real specifications carry `PRJ-000` as a shared-library project identifier; it fits the project-identifier format the schema enforces and needs no schema change, but project-scoped readers must union it.
- A synthetic document's required fixture hashes are the digests of the four generation inputs its manifest entry records; nothing declares this mapping, so E006 fixes it here.
- The 26 real specifications are structurally uncharacterized — the corpus datasheet governs only the synthetic layer — so section, part, and article detection must be validated against them rather than assumed from the standard's format rules.
- The single traced model path fixes the repair budget at one attempt, so a recorded repair count is always zero or one; and prompts and output schemas are stable enough to make committed fixtures worth keeping, any change to prompt text or a schema constraint invalidating every fixture.
- Computing confidence rather than having the model report it changes what E003's TR-081 says the stored number is; the column, its type, and its range are untouched, so this changes a requirement's meaning and not a schema object. It is recorded as an amendment request under FR-047 rather than performed here.

### Risks

- **Parser page attribution is correctness-critical** *(likelihood: medium, impact: high)*: a wrong page number is the most damaging failure the product has. Mitigation: verify containment for every chunk rather than a sample, reusing the tolerances and normalization the repository pins.
- **Computed confidence is unvalidated** *(likelihood: high, impact: medium)*: nothing here checks the score against ground truth, so the floor may reject correct values and admit incorrect ones. Mitigation: declare the floor before the run, record the signals and weights, and state that it is a heuristic ordering.
- **Extraction accuracy is measured only on generated documents** *(likelihood: high, impact: medium)*: line items come from the synthetic layer alone, whose text carries zero recognition error. Mitigation: label every extraction figure by layer and publish the upper-bound caveat with it.

### Disclosed Limitations

Each limitation carries its scope decision and evidence, the condition that would reverse it, and what a production-scale system would do instead. One stated without the last two is a shortfall dressed as a decision.

| Limitation | Scope decision & evidence | Reversal trigger | Production-scale alternative |
|---|---|---|---|
| No line-item extraction from the 26 real specifications | UFGS masters are requirement prose with unresolved bracketed alternatives (`ufgs-23-52-00.pdf` p.30); resolving one would fabricate a requirement | A project-edited specification enters the corpus with the brackets resolved | Ingest edited project specifications and extract requirement values from them |
| Extraction accuracy is an upper bound | The synthetic text layer carries zero recognition error by construction and is the only layer extraction runs on (SC-012); the real layer carries FR-009's narrower claim, its embedded text unvalidated | A scanned document with genuine recognition error enters the corpus, or a real-layer document's embedded text disagrees with its printed page | Report accuracy with recognition error in the denominator; validate embedded text against rendered pages |
| Page attribution and the corpus's structural derivation share one reader | The repository pins one reader (FR-008), so the two cannot disagree; SC-037 verifies no second reader exists, and neither establishes that it reads any page correctly | A page is found whose extracted text disagrees with what it prints, in a way FR-010 cannot see | Validate the reader against an independent extractor on a sampled page set and publish the disagreement rate with every page-attribution figure |
| Computed confidence is uncalibrated | Nothing here measures the score against ground truth; it is a heuristic ordering over parse signals, the floor declared rather than fitted | A frozen, hashed labelled sample of extracted fields becomes available | Fit and publish the threshold against that set with its discrimination, and calibrate the score against observed correctness |
| `PRJ-000` is a sentinel every project-scoped reader must union | The schema requires a project on every document and a public specification belongs to none; a sentinel avoids duplicating each specification per project | Specifications become genuinely project-specific, or the schema admits a null project | Model the specification-to-project relationship explicitly rather than through a reserved identifier |
| Chunk identity is a function of the **run**, not only of the chunker | FR-043's tuple carries the provider model and the prompt and schema digests, so identifiers are re-minted even where boundaries are unchanged (FR-018) | Chunk identity becomes a function of content and position, or the whole tuple is frozen for a release | Key evaluation sets on document, page, and quoted span, resolving to chunks at harness run time |
| Append-only enforcement is latent in the deployed configuration | The revoke is real, but the deployed connection uses the superuser role frozen by E001's `DATABASE_URL` (E003 G-11); SC-024 passes only under an explicit role switch | `DATABASE_URL` names a non-superuser role | The application connects as a least-privilege role, so append-only is enforced by the engine rather than a test that grants itself the role first |
| Half of SC-021 is unenforceable: an association *existing* for every chunk, value, and failure | "At most one run per row" is a primary key; "at least one" is cross-table absence, which nothing rejects without a deferred constraint trigger this schema does not use. Covered by the per-document transaction and an anti-join test | An unattributed row is observed in loaded data despite the test | A deferred constraint trigger comparing per-document row and association counts at commit, refusing the absence rather than detecting it afterwards |
| An association's document may disagree with its target row's own document | The chunk table has no unique key on `(chunk_id, document_id)` for a composite foreign key to reference, and this epic may add none. Covered by a test joining each association to its target | E003 adds the unique key that would make the agreement a composite FK | Hold the agreement structurally, as the citation's page agreement already is, rather than by a test after the write |
| The vector index is absent after an aborted run, and nothing restores it | The drop-and-rebuild is an operator procedure under the schema-owning role (FR-064) and the ingestion job holds no data-definition privilege; while absent, similarity queries stay **correct**, falling back to a sequential scan fast enough at ~15,000 chunks to go unnoticed (data-model §G-7) | A retrieval consumer starts against a database whose `ix_chunk__embedding_hnsw` is missing, caught by the startup check reading `pg_indexes` | Make the rebuild a step of the runbook that drops it, and gate serving on an index-presence check |
| Per-field cardinality within a line item is unasserted | "One manufacturer per item" needs a composite FK against a key the value table lacks and this epic may not add, and is not universally true — an item may cite two compliance standards (FR-059) | E003 adds a value-and-field unique key, or identity resolution shows the ambiguity costs a match | Make the cardinality rule a constraint on the association, so an item with two manufacturers is unstorable rather than matched as two candidates |

## Implementation Signals *(mandatory)*

- `NEW-ENTITY` — a run record, a per-document generation record carrying active/superseded state, the associations linking a generation to every chunk, value, and failure it wrote, a line-item association, and a parse-signal record; the only entities here without existing tables (FR-040).
- `MIGRATION` — forward migrations in the claimed `0300`–`0399` block for those records, plus the amendment to the block partition the build asserts (FR-040).
- `NEW-CONFIG` — a placement check asserting only `model.llm` may import the gateway. The computation-boundary contract needs no edit: its source module is a package and covers descendants.
- `NEW-WORKER` — an offline ingestion job invoked as a console entry point of the modeling entry, not a service or container job.
- `NEW-CONFIG` — the pinned embedding model identity and revision, the confidence floor, and the chunker version, each recorded per run, not assumed.
- `EXTERNAL-SERVICE` — the model provider, reached only through the traced gateway and only in record mode; CI replays committed fixtures.

## Success Criteria *(mandatory)*

### Measurable Outcomes

**Total unless it says otherwise.** Every criterion stated as "100%" or "zero" is **total over the population it names** — enumerated in full, never sampled — and publishes that population and its count (FR-068). The only sampled claims are the ones FR-011 enumerates, each with its inspected count, defect count, and bound. The population is fixed by SC-043.

- **SC-001** [US1]: All 51 corpus documents have a document record and at least one chunk; zero are skipped without a recorded cause.
- **SC-002** [US1]: 100% of chunks have their text found on the page they name, verified for every chunk, not a sample.
- **SC-003** [US1]: 100% of chunks carry a non-null document, document type, project, page, and ordinal, and every document's ordinals are contiguous from zero.
- **SC-004** [US1]: Zero chunks exceed the encoder's input window measured in its own tokenizer.
- **SC-005** [US1]: The total and per-layer chunk counts are published against the architecture's 5,000–15,000 estimate, any deviation published with its cause rather than the estimate restated to match the result.
- **SC-006** [US1]: 100% of chunks carry a vector of the dimension the schema publishes with a recorded embedding model identity and revision; zero carry one from an unrecorded model.
- **SC-007** [US1]: Chunking the same document twice under the same input tuple produces identical boundaries and ordinals, measured by chunking in isolation. The two chunkings MUST differ in process, hash seed, working directory and checkout path, process identity, and filesystem enumeration order, and agree in what the tuple names and in the pinned segmenter and tokenizer versions. Locale and timezone are out of scope.
- **SC-008** [US2]: 100% of **attempted field extractions** resolve to a stored value carrying a page citation and a per-field confidence, a failure record carrying one outcome from the closed set, or a correct negative; zero attempts are unaccounted for. **Denominated on attempts (FR-069), not on stored rows.** *(Amended 2026-07-28, with FR-069 and SC-054 and for their reason: this criterion also restated the two-resolution identity, which no run that extracts anything can satisfy. It is amended rather than left alone because a criterion contradicting the requirement it measures is measured against whichever of the two a reader happens to read first.)*
- **SC-009** [US2]: 100% of extracted values have a cited page equal to their source chunk's page, and the count whose citation could disagree is zero because such a row cannot be stored.
- **SC-010** [US2]: 100% of extracted field names appear in the seeded vocabulary **and carry no retirement date at the time of the run**; zero values are stored under a retired term or one invented at run time.
- **SC-011** [US2]: Every model request in the run is recorded on the traced path; zero originate anywhere else. **Measured as a reconciliation, not only as a contract** (FR-070): the invocations attempted equal those recorded under the run's trace identifier, both published.
- **SC-012** [US2]: All 25 synthetic transmittals yield at least one extracted value; the 26 real specifications yield zero, with the exclusion recorded.
- **SC-013** [US2]: Every extracted manufacturer and part number matches, character for character, the value the generator recorded as printed on that page, over every extracted value, not a sample, against the reference FR-067 fixes.
- **SC-014** [US3]: 100% of model outputs are schema-validated before persistence, with at most one repair attempt and then failure.
- **SC-015** [US3]: Zero extracted values are persisted from an invocation that ended in failure; zero failure records carry a value or a confidence.
- **SC-016** [US3]: Every extraction failure carries one outcome from the closed set of seven FR-034 enumerates; zero carry one outside it, and the seven per-field outcomes and five run-level kinds (FR-056) share **zero** values, checked as an intersection of the two declared domains.
- **SC-017** [US3]: The declared floor, the distribution over **all eight scores FR-057's signals admit** with a score nothing took published as a zero, the rejected and stored populations counted separately, the signal weights, and the heuristic-ordering statement with its reversal trigger are all published together; zero admissible scores are omitted.
- **SC-018** [US3]: The valid, repaired, and failed counts are published separately, the repaired rate in its own right, and **each names its counting unit** (FR-069), invocation- and attempt-level figures appearing as two tables; zero figures carry an unnamed unit. The failed count is **broken down by each of the seven per-field outcomes**, an outcome no failure took published as a zero.
- **SC-019** [US4]: Every value drawn from more than one chunk records its anchor plus one contributing-chunk row per *additional* chunk — the anchor is contributor 1 and never appears in the contributing table — and its declared source count equals one plus its contributor count in 100% of cases.
- **SC-020** [US4]: The document carrying the seeded page-split irregularity produces at least one multi-chunk value, and the **count** of multi-chunk values and of their contributing-chunk rows is published, not left as an existence claim.
- **SC-021** [US5]: 100% of chunks, extracted values, and failure records resolve to exactly one ingestion run.
- **SC-022** [US5]: Every ingestion run record names its agent identity, provider model, chunker version, embedding model identity and revision, corpus manifest digests, resolution mode, run start, trace identifier (FR-070), declared confidence floor, and three deduction weights (FR-057); zero fields are absent. **The population is the whole run record**, not the subset FR-038's opening sentence lists: the only columns permitted to be absent are the finish, carried only by a completed run, and the two run-failure columns, carried only by an aborted one. The agent identity names **both** principal and build; zero records name only one.
- **SC-023** [US6]: A full ingestion run completes in continuous integration with zero provider calls and zero network access, over a window opening at process start, **before the ingestion package is imported**, and closing at exit.
- **SC-024** [US6]: Zero rows are updated in place across any run or correction in the nine tables this criterion ranges over — the three E003 provenance tables (`extracted_value`, `extracted_value_contributing_chunk`, `extraction_failure`) and the six this epic adds beyond the run record (the generation record, the three run-output associations, the line-item association, the parse-signal record) — the only permitted updates being the run's finish timestamp, its two run-failure columns, and the `active`-to-`superseded` mark under the schema-owning role (FR-066); deletion occurs only through the documented whole-document remove-and-reload under that role, and zero deletions originate from the ingestion job. **Measured under an explicit switch to the application role**, the only configuration in which the revoke binds; enforced by design and latent in deployment (Disclosed Limitations).
- **SC-025** [US6]: Re-running ingestion when every document's input tuple is unchanged adds zero chunk, extracted-value, or failure rows; a run in which a tuple differs reloads exactly the documents whose tuple differs and no others.
- **SC-026** [US3]: Recomputing every stored confidence from the signals recorded with it reproduces the stored value exactly, and zero stored confidences originate from a model assertion.
- **SC-027** [US2]: Zero identity assertions are made between two differently-spelled manufacturer names, and zero text-kind values differ from the generator's recorded pre-render text, over every text-kind value, not a sample. A value assembled across a page break is compared against the concatenation of its source chunks' pre-render text **in ascending page order**, not contributor order (FR-029).
- **SC-028** [US2]: The enumerated computation-boundary contract names every model-facing module this epic adds; contracts kept with zero broken.
- **SC-029** [US2]: Every extraction quality figure — the set FR-050 names — is published beside the baseline's over the same documents, with an interval and **both** baseline labels, the declared one recorded before any figure was computed. Zero figures are published without a baseline or an interval; zero declared labels are written or revised after a figure exists; a label disagreement is published as a finding rather than resolved by changing one.
- **SC-030** [US1]: Zero documents are ingested whose recorded content hash differs from the file on disk, and a mismatch aborts the run with zero rows written for it.
- **SC-031** [US1]: Zero chunks span more than one page.
- **SC-032** [US1]: The ingestion report carries **every item on the closed content list FR-071 fixes** and zero items are absent; zero sampled claims are made outside FR-011's enumeration, and zero figures are published that the list does not name.
- **SC-033** [US2]: 100% of document records carry their layer, license basis, and layer-appropriate provenance unchanged from the manifest; zero generated documents carry retrieval provenance.
- **SC-034** [US5]: The migration block and decision-record numbers this epic claims are recorded before implementation begins, zero schema objects are placed outside the claimed block, and the amendment FR-047 raises has landed on the default branch **before implementation begins** — FR-047's own trigger, not a later one.
- **SC-035** [US6]: Zero ingestion code paths are reachable from a request-serving entry point.
- **SC-036** [US1]: Zero document records exist for a PDF no manifest lists; 100% of document identifiers are the file stem under FR-002's transform — lower-cased, runs outside `[a-z0-9]` replaced by a single hyphen, leading and trailing hyphens stripped — and satisfy the identifier format the schema requires; 100% of document types are drawn from the closed set the schema defines; zero identifiers are shared by two documents.
- **SC-037** [US1]: Zero second readers exist: the ingestion package declares zero extraction tolerance mappings, zero normalizations, and zero page-text assemblies of its own, and 100% of its page reads resolve through the one committed reader FR-008 fixes. That the reader reads any page correctly is not asserted here and is disclosed.
- **SC-038** [US1]: Zero chunk boundaries fall at a fixed character, word, or token offset; 100% fall in one of the three named classes, and every fragment produced by a page or sentence boundary carries the structural identifier of the unit it came from. **The chunk count in each class is published, per layer and pooled, a class holding none published as a zero.**
- **SC-039** [US3]: 100% of failure records carry a source chunk, attempted page, field name, repair attempt count, and diagnostic detail; zero carry an absent field among the five.
- **SC-040** [US2]: 100% of numeric and date values were coerced from the printed text by deterministic code; zero typed values were accepted as the model returned them.
- **SC-041** [US1]: Zero chunks exceed the encoder window; zero runs fail on an over-long leaf a sentence-level split could have resolved; and the leaf-length distribution, the sentence-split count, and the page-terminal document list — **each named document carrying its own count** — are published per layer as well as pooled. **The second clause is observed over the enumerated leaf population, not over failed runs**: the failing observation is a leaf above the window left unsplit that is not a single sentence.
- **SC-042** [US6]: An aborted run leaves zero half-ingested documents, and 100% of committed documents carry their complete row set. **Complete is defined per document**: exactly one generation record and at least one chunk, plus one run association per chunk, value, and failure written; every transmittal additionally at least one extracted value or failure, one line-item association per stored value, and one parse-signal record per stored value; each of the 26 specifications **zero** values, contributing chunks, failures, line items, and parse signals, complete by FR-022 rather than short.
- **SC-043** [US5]: At most one generation is active per document, zero downstream reads return rows from more than one generation of the same document, and zero superseded rows remain after a promotion completes. **Every resident chunk, extracted value, and failure therefore belongs to the one active generation of its document**, the population every total-count criterion here ranges over.
- **SC-044** [US3]: A missing fixture in replay produces exactly one named run-level failure and zero per-field failure records, and the run does not report completion.
- **SC-045** [US3]: 100% of stored confidences equal 1.0 less their declared deductions and are at or above the declared floor of 0.80; zero repaired invocations and zero values that are both alternate-labelled and page-split are stored.
- **SC-046** [US2]: 100% of extracted values belong to exactly one line item, and a line item split across two chunks remains one. The population is every extracted value: one printed once for the whole document belongs to item ordinal 0, real items numbered from 1 (FR-059). Zero values sit outside a group, and zero sit in two.
- **SC-047** [US2]: Per-field precision and recall are published per field and per layer with **continuity-corrected** Wilson 95% intervals, the variant named, beside the baseline's; zero intervals use a second method; zero F1 figures are published and the omission is published with its reason; zero **recall** figures are denominated on stored values alone; 100% of figures print their denominator (FR-060). **The real layer is published as not measured with its reason**, so zero figures rest on an empty denominator and zero layer rows are blank or `0/0`.
- **SC-048** [US1]: Near-duplicate chunk cluster counts are published by cause, as exact normalized-text matches and at **every threshold in the grid declared before the run** — 0.80, 0.85, 0.90, 0.95, 0.99 under cosine similarity over the chunk embeddings; zero thresholds are chosen or moved after the clusters are observed, and zero counts fall outside the grid.
- **SC-049** [US3]: 100% of stored confidences have their computing signals recorded alongside them, so recomputation reads an independent record, not the score itself.
- **SC-050** [US6]: Zero index alterations originate from the ingestion job; the drop-and-rebuild is reachable only through the operator procedure, and a run aborting while the index is absent is reported as such rather than completing silently.
- **SC-051** [US5]: Zero columns, zero constraints, and zero indexes are added by this epic's migrations to any of the six E003-owned tables it populates, verified by comparing their catalog entries before this epic's first revision and at head; 100% of the objects its migrations create appear in its named inventory.
- **SC-052** [US2]: The reference set every accuracy figure is scored against is reproduced from the committed generation inputs and equals the document-model digest the manifest records, verified before the first figure; zero figures are scored against text this epic's parse produced or against the chunk a value was read out of.
- **SC-053** [US1]: 100% of published total checks name the population they enumerated and its count; zero report success over an empty population.
- **SC-054** [US3]: Attempted field extractions equal stored values plus failure records plus correct negatives exactly, zero attempts unaccounted for; 100% of published counts and rates name their unit. *(Amended 2026-07-28, with FR-069 and for its reason: the criterion restated the two-resolution identity, which cannot hold on a run that extracts anything — the model is invoked per chunk over the whole field subset, so a field printed on one chunk of ten is correctly absent from the other nine. The identity now names the third resolution FR-069 admits. "Zero attempts unaccounted for" is unchanged and is still measured by a published `unaccounted` count computed from two independent derivations — the attempt total from the corpus shape, the three resolutions from what the extraction stage produced — so the criterion still fails on a genuine imbalance.)*
- **SC-055** [US6]: Every run publishes a per-document disposition for 100% of the documents it enumerated, from the four FR-073 names; the counts sum to the enumerated document count, zero documents carry no disposition or two, and zero dispositions are omitted for holding none. The skipped-as-unchanged count is published rather than inferred from the absence of new rows, and an aborted run publishes what it never reached separately from what it rolled back.
- **SC-056** [US5]: 100% of published figures name the run they were computed under, whether corpus-resident or run-scoped with the generation set named, their counting unit, and their kind — census, sampled estimate, or descriptive figure over a designed set; zero census figures carry an interval, zero estimates lack one, zero figures spanning both layers lack a per-layer breakdown, zero carry no label. The report names by identifier the run record it describes.
- **SC-057** [US6]: 100% of published figures carry the reproduction tolerance in force for them, and a replay-mode run from a clean checkout reproduces every count, rate, interval, and stored confidence **exactly** and the near-duplicate cluster counts within the stated encoder parity tolerance; zero figures lack a stated tolerance, and a difference outside the band is published as a gate failure rather than absorbed by widening it.
- **SC-058** [US1]: The exported encoder is accepted only against parity bounds recorded before the comparison ran, over the committed probe set spanning both layers: 100% of probes reach cosine similarity ≥ 0.999999 against the reference encoder and per-dimension difference at most 1e-5, a breach failing the run rather than embedding. The observed maxima are published beside the declared bounds; zero bounds are widened afterwards, and a maximum near a bound is published as a finding.

## Glossary *(include when spec introduces 2+ domain-specific terms)*

| Term | Definition |
|------|------------|
| Chunk | A contiguous run of text from one page of one document, cut on its own structure; the unit every page citation resolves through. |
| Line item | One proposed material or equipment entry on a transmittal — manufacturer, part number, quantity and related fields — numbered from 1. Ordinal **0** names the values printed once for the whole document (FR-059). Distinct from a purchase order line. |
| Specification section | A MasterFormat-numbered division of a construction specification, recorded on chunks and cited by transmittals. |
| Citation anchor | The page number and document identifier that make a page citable — the project's fixed citation form. |
| Reference set | The generator's **pre-render document model**, reproduced from the committed generation inputs and pinned by the manifest's digest; the expected side of every accuracy comparison (FR-067). Not the parsed page text, nor the chunk a value came from. |
| Attempt | One field on one chunk, except a field absent from a whole document, which is one attempt for it. Distinct from an **invocation**, one model request covering a chunk's declared field subset (FR-069). |
| Confidence floor | The value below which an extracted value is recorded as a failure rather than stored; declared before the first run and not moved to fit it. |
| Ingestion run | One execution of the parse, chunk, embed, and extract pipeline, recorded so every row it wrote is attributable to the agent, models, and corpus state behind it. |
| Shared-library project | The reserved project identifier `PRJ-000` under which real specifications are recorded, a public specification belonging to no project. |
| Word piece | The sub-word unit the embedding encoder counts; its 256-unit window caps chunk length and is not a word or character budget. |
| Page-split field | A field whose label ends one page and whose value begins the next; why a value may draw on more than one chunk. Its citation anchors on the page carrying the **printed value** (FR-029). |
| Structure-aware chunking | Cutting a document at boundaries it declares — section, part, article, paragraph — rather than at a fixed size. |
| Boundary class | One of the three kinds of cut a boundary may be: structural, a page break, or a sentence inside an over-long leaf (FR-012). |
| Input tuple | A **document's** inputs for deciding whether to re-ingest it: its own content hash, the chunker version, the embedding model identity and revision, the provider model, and the extraction prompt and schema digest (FR-043). The corpus-wide manifest digests are recorded on the run for attribution and are deliberately not members. |

## Clarifications

### Session 2026-07-27

- Q: How do specifications belonging to no project satisfy the required `document.project_id`? → A: A reserved shared-library project `PRJ-000`, one record per specification. Accepted cost: project-scoped readers must union it.
- Q: What happens when a field's extraction confidence is low? → A: Two bands. Below a published floor the value is recorded as a failure rather than persisted; at or above it the value is persisted with its confidence carried through for display.
- Q: Which corpus layers get line-item extraction rather than only chunking and embedding? → A: The synthetic transmittals only; all 51 documents are still parsed, chunked, and embedded. UFGS specifications are requirement prose with unresolved bracketed alternatives, so asking for "the" value would fabricate a requirement. Accepted cost: accuracy is measured on generated material only, with that caveat.
- Q: Where does agent identity live, given the schema omits a per-row agent column? → A: A new ingestion-run table, claiming migration block `0300`–`0399` and leaving `0200`–`0299` for the epic sharing this wave.
- Q: Should per-field confidence be the model's self-reported number, or computed from parse signals? → A: Computed deterministically from parse signals — label canonical or alternate, value printed or absent, single-chunk or page-split, validated first try or after a repair. Reproducible, explainable, and on the code side of "the model extracts, code computes"; research finds self-reported confidence collapses toward all-positive at practical thresholds, so the floor would reject nothing. Consequence recorded rather than absorbed: TR-081 fixes confidence as agent-asserted, so this needs an amendment, raised in FR-047 and not performed here. — **Amended during the Clarify session, recorded here rather than rewritten**: the fourth signal above, *value printed or absent*, was **withdrawn and MUST NOT be computed**; an absent value is a failure record rather than a stored value with a confidence, so the signal can never fire on a row the requirement ranges over, and three signals remain (FR-031, FR-057).

### Session 2026-07-27 (Clarify)

- Q: 476 of 9,020 real-layer leaf units exceed the encoder window and 175 pages carry no structural marker, so no legal chunking existed. How are boundaries made legal? → A: Three named classes — structural, page break, sentence within an oversized leaf — each fragment keeping the structural identifier of the unit it came from. The ladder descends article → paragraph → subparagraph → sentence; where no level is detectable the page is the terminal unit, named in the report with its count. The run fails only when a single sentence exceeds the window (FR-012, FR-014, FR-053).
- Q: SC-024 forbade the removal FR-041 defines as the only correction path, and an aborted run could not clean up after itself. → A: All rows for one document commit in one transaction in a stated order, so an abort rolls back only the document in flight and needs no deletion privilege. Deletion happens solely through the documented whole-document remove-and-reload under the schema-owning role (FR-041, FR-054, SC-042).
- Q: Does a chunker version change count as "changed inputs"? → A: A run's inputs are the tuple of corpus manifest digests, chunker version, embedding model identity and revision, and extraction prompt and schema digest. Runs carry active or superseded state, at most one is active per document, readers filter on active, and superseded runs have a stated retention bound and purge procedure (FR-043, FR-055, SC-025, SC-043). — **Amended three times after this session, recorded here rather than rewritten**: the tuple is **per document rather than corpus-wide** and carries the **provider model** as a fifth member (FR-043); the active/superseded mark is **per document rather than per run** (FR-055, {SAD:ADR-0019}); and **no retention bound or purge procedure exists or is needed**, promotion removing the prior generation's rows as it writes the successor (FR-055, {SAD:ADR-0020}).
- Q: A missing fixture in replay has no home among the seven per-field outcomes. → A: A named run-level failure that aborts the run, reported distinctly from per-field extraction failure (FR-056, SC-044).
- Q: A "line item" is this epic's output, but the value table has no line-item key. → A: An association this epic owns in its claimed block, keyed by value, document, and item ordinal. Chosen over keying on the source chunk, under which an over-long entry split across two chunks would silently become two items (FR-059, SC-046).
- Q: What are the confidence weights and the floor? → A: 1.0 less declared deductions — alternate label 0.15, page-split assembly 0.10, repaired invocation 0.25 — against E002's committed field-label vocabulary. Floor **0.80**, stated as the combinations it excludes: any repaired invocation, and any value both alternate-labelled and page-split. Raised from the 0.70 first proposed, which admits both combinations it claimed to exclude, each scoring 0.75 (FR-057, SC-045).
- Q: Which fields are attempted, given ten of the twenty-two vocabulary terms cannot appear on a transmittal? → A: Only the declared transmittal subset, per chunk, with a field absent from a whole document recorded once per document (FR-058). — **The question's count was wrong and is recorded here rather than rewritten**: **twelve** of the twenty-two cannot appear on a transmittal and ten can, which is what the declaration has always implemented. The answer is unaffected. Corrected in FR-058 on 2026-07-28.
- Q: FR-050 requires an interval and a baseline but names no figures and no denominator. → A: Per-field precision and recall with Wilson 95% intervals, per field and per layer, beside the baseline; recall denominated on the generator's printed-field set, not on stored values (FR-060, SC-047).
- Q: A Wilson interval is not defined for F1, a harmonic mean of two proportions rather than a proportion — but SC-029 admits no figure without an interval. → A: Publish precision and recall only, with the omission and its reason; they determine F1. Rejected: a bootstrap interval for F1 alone, putting two interval methods in one report; and exempting F1, which softens a target to match a result.

## Stress-Test Findings

### Session 2026-07-27

- **STF-001**: Constraint Impossibility (CRITICAL) — Affected: FR-012, FR-013, FR-014, SC-004, SC-031, SC-038, US1 — No legal chunking existed for a structural unit crossing a page break, and a too-long leaf aborted the run in a structurally uncharacterized corpus: 9,020 leaf units over the 26 real documents, 476 (5.3%) above a conservative proxy for the window, largest 592 words, 175 pages with no marker. **Resolved** by FR-012, FR-014, FR-053.
- **STF-002**: Cross-Requirement Contradiction (CRITICAL) — Affected: FR-005, FR-041, FR-042, FR-052, SC-024, SC-030, US6 — Append-only privilege made the no-half-ingested guarantee unachievable once a run aborted after extraction, SC-024 forbade the removal FR-041 defines as the only correction path, and write ordering was never stated. **Resolved** by FR-054's per-document transaction and write order, with SC-024 rewritten to forbid in-place update while permitting the documented remove-and-reload under the schema-owning role.
- **STF-003**: Concurrent-Trigger Ambiguity (HIGH) — Affected: FR-017, FR-039, FR-041, FR-043, SC-005, SC-007, SC-021, SC-025, US5, US6 — A re-run at a new chunker version had to both produce different boundaries and write zero rows, nothing marked a run superseded, and the corpus would grow by a chunk set per revision with no bound. **Resolved**: input tuple defined, active/superseded state, readers filter on active. **Amended after this session and recorded rather than rewritten**: the tuple and the mark are both **per document** rather than per run, and **no retention bound or purge procedure exists** — promotion removes the prior generation's rows as it writes the successor (FR-043, FR-055, {SAD:ADR-0019}, {SAD:ADR-0020}).
- **STF-004**: Constraint Impossibility (HIGH) — Affected: FR-027, FR-049, SC-013, SC-020, SC-027, SC-040, US4 — A criterion quantified at zero over a population where an exception is guaranteed: a date printed `3/14/26` is stored as ISO-8601, and a page-split value matches the pre-render text of neither page alone. **Resolved** by scoping printed-text comparisons to text-kind values (FR-027, FR-062) and comparing a page-split value against the concatenation of its contributing chunks in contributor order — **amended during the Checklist phase to ascending page order**, once FR-029 anchored the citation on the chunk carrying the printed value (SC-027).
- **STF-005**: Boundary/Scale Stress (HIGH) — Affected: FR-034, FR-035, FR-037, FR-045, SC-012, SC-016, SC-023, US3 — A missing fixture or unreachable provider had no requirement and no outcome in the closed set of seven. **Resolved** by FR-056's named run-level failure, distinct from per-field extraction failure.

## Compliance Check

**Audited against**: `project-instructions.md` **v1.2.5** (last amended 2026-07-28) · **Audit date**: 2026-07-28 · **Verdict**: PASS with two sequencing conditions. **Re-run**, superseding the v1.2.4 audit of 2026-07-27: v1.2.5 merged into this branch at `fead821` while the epic was in flight, and Governance requires a feature whose recorded audit names a superseded version to re-run its gate before its next phase gate. The re-run found three violations — all repaired on this branch — and no principle changed verdict; see `analysis-report.md` §Compliance re-run (A-23, A-24, A-25) and `plan.md` §Instructions Check.

| Principle / Section | Verdict | Where |
|---|---|---|
| I. Traceable or It Does Not Ship | PASS | FR-007, FR-029, FR-030; SC-008, SC-009 — citation derived at ingestion, unstorable if it disagrees with its chunk |
| II. Uncertainty Is the Product | PASS | FR-033, SC-017, SC-032 — distribution rather than a mean; FR-072 fixes which figures carry an interval |
| III. Precision Over Recall Where a Mistake Is Silent | PASS | FR-025, FR-026, FR-028, FR-036, FR-037; SC-015, SC-016 — fail closed after one repair |
| IV. Agent Output Style | PASS | Template sections only |
| V. The Model Extracts, Code Computes | PASS | FR-031, FR-048, FR-049 — confidence computed, boundary contract extended, coercion deterministic |
| VI. Evaluate Before You Tune | PASS | FR-032 — the floor is declared before the run, not refitted to it |
| VII. Publish the Miss | PASS | Disclosed Limitations — eleven, each with scope decision, evidence, trigger, alternative; `data-model.md` §Disclosed Gaps carries G-1..G-10 alike |
| VIII. Honest Opponents | PASS | FR-050, SC-029 — every extraction figure reported against a deterministic template baseline, labelled |
| Technology Stack | PASS | Local embedding at the dimension the schema publishes (FR-021, ADR-0012), 256 word-piece window; console entry point (ADR-0011); no second datastore of record |
| Testing & Quality Policy | PASS | FR-048, SC-028 extend the computation-boundary contract; architecture contracts gate the build |
| Source Code Layout | PASS (repaired at QC) | Modeling-entry console entry point, gateway-mediated provider access; `/tools` moved to `src/model/tools/` — the repository root is outside `ENFORCE_SRC_ROOT` and `/tests` is its one exception (A-24) |
| Development Workflow | PASS (repaired at QC) | Workspace `00006-…` matches epic E006; branch cut to match. **Temporary Files (new in v1.2.5)**: the root `pyproject.toml` pinned no `--basetemp` and `verify.yml`'s `verify` job set no `TMPDIR`/`TEMP`/`TMP`; both fixed (A-23) |
| Data Provenance | PASS | FR-004, SC-033 — layer, license basis, provenance carried unchanged; no fabricated retrieval provenance |
| Governance | PASS | FR-040 claims block `0300`–`0399` and ratifies the E005 reservation; FR-051 claims ADR-0018–0020; FR-065 holds the E003 boundary; FR-047 records an amendment rather than performing it |

**Two conditions carried into Plan, both sequencing rather than defects:**

1. **FR-047 blocks implementation.** E003's TR-081 declares `extracted_value.confidence` agent-asserted, and `data-model.md` is normative for reader-facing semantics ({SAD:ADR-0017}). The amendment lands on `main`; this branch records the need.
2. **`0200`–`0299` is reserved for E005, ratified outside this document rather than asserted inside it.** FR-040 requires the build-gating partition to declare both blocks, so disjointness is machine-checked rather than dependent on E005 reading E006's claim.

**Recorded after the 2026-07-27 clarification pass, and deliberately not a third condition:** E003's migration `0009` revokes `UPDATE` and `DELETE` on the three provenance tables from the application role, so FR-041's remove-and-reload cannot run from the ingestion job. Resolved without an amendment, no schema object changing: the removal is an operator procedure under the schema-owning role, and FR-054's per-document transaction means the job never needs deletion privilege to recover from an abort. Raised to E003 as a note.
