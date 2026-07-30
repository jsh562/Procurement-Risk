---
feature_branch: "00003-core-data-schema"
created: "2026-07-25"
input: "e003"
spec_type: "technical"
spec_maturity: "clarified"
epic_id: "E003"
epic_sources: "{SAD:ADR-0002}{SAD:ADR-0004}{SAD:ADR-0008}"
---

# Feature Specification: Core Data Schema

**Feature Branch**: `00003-core-data-schema`
**Created**: 2026-07-25
**Status**: Clarified
**Spec Type**: technical
**Spec Maturity**: clarified
**Epic ID**: E003
**Epic Sources**: {SAD:ADR-0002}{SAD:ADR-0004}{SAD:ADR-0008}
**Product Document**: specs/prd.md

## Problem Statement *(mandatory)*

The repository runs a pinned `pgvector`-enabled PostgreSQL 16 container and contains no schema at all — no tables, no migration sequence, no extension enablement, and no database client in any dependency manifest. Six downstream epics are blocked on that gap: the model gateway, the synthetic procurement history, document ingestion, the forecast fit job, identity resolution, and every read surface built on them. Until the schema exists, the project's two defining guarantees are conventions rather than facts: an extracted value with no page citation is merely discouraged instead of impossible to store, and the contract between the offline modeling boundary and the request-serving boundary — which the architecture defines as the database itself, not a Python interface — has no versioned shape for either side to honor.

## Scope *(mandatory)*

### Included

- A forward-only Alembic migration sequence that applies cleanly from an empty database, with a reserved migration-number block carried as a filename prefix so parallel schema work in the same wave cannot collide
- Enablement of the `vector` extension and any other extension the schema depends on
- A single-row schema-constants table holding the values both Python boundaries need, so neither has to import from the other
- A project-level decision record naming the embedding model and fixing the vector dimension, authored before the chunk migration that declares the column
- The document table keyed by the corpus manifest identifier, giving every chunk's document reference a real referent
- The chunk store: structure metadata (document reference, project, document type, specification section, page), a field-weighted full-text search column, a fixed-dimension dense vector column with recorded embedding model identity and revision, and the indexes serving both retrieval arms
- Extraction storage with traceability enforced by constraints: a seeded field-name vocabulary, mandatory page citation and per-field confidence, an explicit representation for values derived from more than one chunk, and a separate failure record for values that never became storable
- Procurement lifecycle storage: purchase-order lines carrying need-by date, criticality, and open/closed state, plus their lifecycle event histories including rework loops
- The versioned forecast artifact contract: a forecast run carrying full reproducibility metadata, a schema version, and the run's as-of anchor date; an explicit active-run pointer; and one row per line-run holding both the sorted posterior draw array and the derived day-grid survival array with its explicit residual tail mass
- The resolved cross-document entity store (P2)
- Persistence of the roster content hash that E001 requires every generated artifact to carry
- Schema-level integrity verification: apply-from-empty, constraint-rejection, and index-presence tests

### Excluded

- The model-invocation table, response-fixture cache, and price-table versioning — owned by E004, which claims its own migration block; disjoint table ownership is what makes the two epics parallel-safe in Wave 2
- Candidate-pair and review-queue tables — owned by E009, which establishes the review-queue record shape that E016 must extend additively
- Criticality override storage — owned by E017 (P2), which must be additive to the purchase-order line defined here
- Populating any domain table with data — the corpus, procurement history, chunks, extractions, and posteriors are produced by E002, E005, E006, and E007 respectively. The two exceptions this epic does write are migration-seeded reference data: the field vocabulary and the single schema-constants row
- The fused retrieval statement and the read-time risk arithmetic — E008 and E010 own those queries; this epic owns only the column and array shapes they read against
- Generating embeddings, loading corpus manifest rows, and populating the field vocabulary beyond its seeded values — E006 does all three; this epic defines the columns, the referent tables, and the constraints they must satisfy
- Any reference from an extracted value directly to a purchase-order line — a value's only outbound reference in this epic is to its source chunk. The resolved-entity membership table defined in OBJ6 is the sanctioned join surface, and E009 populates it
- Backup, restore, and retention policy — out of scope per the architecture document, since the full dataset is regenerable from the repository and its jobs
- Any change to the existing Compose service definitions — the `db` service shape, image digest, host port, and credentials, and the `ingest` and `fit` job definitions — since E001's orchestration checks assert the current values. `docker-compose.yml` is not modified at all: ADR-0011 requires a modeling-owned job to run as a console entry point, so no migration service is added (TR-007, TR-037, SC-003)

### Edge Cases & Boundaries

- The migration sequence is applied twice, or applied against a database that is already at head
- A migration fails partway through, leaving the database between revisions
- An extracted value is derived from several chunks and therefore has no single source page
- An extracted value fails validation and never becomes storable, so it must be recorded as absent rather than stored wrong
- A reader encounters a forecast artifact whose schema version it does not recognize
- No forecast run is marked active, or more than one row claims to be active
- A posterior extends past the survival grid's fixed horizon, leaving residual probability mass outside the array
- A posterior draw array and its derived survival array are written out of step, or belong to different runs
- A chunk's vector is written under a different embedding model or revision than the rest of the corpus
- A vector of the wrong dimension is inserted, or a chunk is stored with no text to build a search vector from
- A purchase-order line has a need-by date earlier than its order date, or is still open and therefore right-censored with no delivery event
- A project or vendor identifier does not match the format E001 froze, or a roster hash is not a `sha256:`-prefixed 64-character lowercase hexadecimal string

## Technical Objectives *(mandatory for technical specs only)*

### Objective 1 - Forward-Only Migration Sequence (Priority: P1)

Establish the mechanism by which schema reaches the single Postgres instance: a numbered, forward-only migration sequence that applies cleanly against an empty database, enables the extensions the schema depends on, and reserves a migration-number block so a second epic adding tables in the same wave cannot collide with it.

**Why this priority**: Every other objective in this epic ships as a migration, so nothing here is applicable or verifiable without the sequence, and E004 cannot begin its own table in the same wave until the numbering claim exists.

**Rationale**: The repository has no migration tooling, no database client dependency, and no schema. The execution plan makes migration-number collision the named integration risk for Wave 2 and requires the claim to be made at epic start. The architecture document fixes schema evolution as forward-only, which is a policy that has to be expressed in the tooling rather than left to authoring discipline.

**Deliverables**:
- Alembic configuration and a `[project.scripts]` console entry point, residing in `/src/model`, which also carries the database-client dependency; migrations are invoked through that entry's own environment, never from a container image (ADR-0011)
- The initial migration enabling the `vector` extension
- A recorded migration-number reservation: `0001`–`0099` owned by E003, `0100`–`0199` reserved for E004, carried as a filename prefix over Alembic's own revision identifiers
- No Compose service and no container image — ADR-0011 rules both out for a modeling-owned job, and a context rooted at `src/model` could not resolve the `gateway` path dependency in any case
- An apply-from-empty verification test executed against the Compose `db` service, plus a check asserting a single head revision and that every filename prefix falls inside the reserved block
- A stated forward-only policy: no reverse operations, with the downgrade path raising rather than carrying an untested body
- A single-row schema-constants table stating, once, the declared vector dimension, the survival-grid horizon in days, the anchor-date convention, the percentile convention, the per-run draw count, and the probability-sum tolerance — read over the connection by both Python boundaries so neither imports from the other
- An ADR authored through the ADR Author sub-agent naming the embedding model and fixing the vector dimension, landing before the chunk migration

**Validation Criteria**:
1. **Given** an empty database, **When** the full migration sequence is applied, **Then** every migration succeeds without manual intervention and the resulting object set matches the declared schema.
2. **Given** a database already at the head revision, **When** the sequence is applied again, **Then** no migration re-runs and the schema is unchanged.
3. **Given** a migration whose filename prefix falls outside E003's reserved block, **When** the migration set is verified, **Then** verification fails and names the offending prefix.
4. **Given** a migration set that resolves to more than one Alembic head revision, **When** it is verified, **Then** verification fails rather than applying an ambiguous order.
5. **Given** the migration set, **When** it is inspected for reverse operations, **Then** none exist.
6. **Given** ordinary `docker compose up`, **When** the stack starts, **Then** only the persistent services start — and `docker-compose.yml` is unchanged by this epic, so `tests/checks/test_orchestration.py` passes without modification.
7. **Given** a migration that fails partway through, **When** the database is inspected, **Then** the recorded revision matches the objects actually present, and re-applying the sequence advances from that revision without manual repair.
8. **Given** the migrated database, **When** the schema-constants table is read, **Then** it holds exactly one row carrying the vector dimension, survival-grid horizon, anchor-date convention, percentile convention, draw count, and probability-sum tolerance, and no second row can be inserted.
9. **Given** the verification tests this epic adds, **When** their locations are inspected, **Then** each test belonging to a single entry lives inside that entry, and only cross-entry verification lives at the repository root.
10. **Given** the dependency manifests, **When** they are inspected, **Then** only `/src/model` declares the database client and migration tooling, and neither Python boundary declares the other as a dependency.
11. **Given** the repository before the chunk migration is authored, **When** `specs/adrs/` is inspected, **Then** a decision record naming the embedding model and fixing the vector dimension is present and accepted.
12. **Given** the migrated schema, **When** every constraint is inspected, **Then** no check or non-null constraint is declared deferrable, and every invariant spanning two statements is carried by a deferrable foreign key or the single sanctioned fallback trigger.

### Objective 2 - Retrievable Chunk Store (Priority: P1)

Define the chunk table so both retrieval arms operate against a single row set: a field-weighted full-text search column that ranks headings, part numbers, and specification section text above body prose, and a fixed-dimension dense vector column carrying the identity and revision of the embedding model that produced it. Both the exact evaluation path and the approximate serving path must be executable against this table.

**Why this priority**: The chunk table is the first-wave dependency for document ingestion and, transitively, for retrieval, grounded answering, and the detail view; it is also the subject of the epic's first acceptance criterion.

**Rationale**: The single-datastore decision puts both retrieval arms in one Postgres instance so fusion can execute as one statement inside the deterministic-computation boundary. The evaluation-versus-serving split requires an index on the serving path while evaluation runs an exact scan, so the schema must support both without the configuration flag reaching beyond index usage. Recording the embedding model and revision per chunk is what lets retrieval refuse to serve on a vector-space mismatch instead of silently mixing spaces.

**Deliverables**:
- A document table keyed by the corpus manifest identifier, populated by E006 but defined and constrained here
- A chunk table whose document reference is a foreign key to that table, plus project identifier, document type, specification section, page number, and ordinal position within the document
- A uniqueness constraint on the chunk table exposing `(chunk, page)`, so an extracted value's citation can be tied to its source chunk's page by composite foreign key rather than by trigger
- A full-text search column populated with field weighting applied across heading, part-number, section, and body text
- A dense vector column of a single fixed dimension declared once in the schema, plus per-chunk embedding model identity and model revision columns
- A full-text index and an approximate-nearest-neighbour index supporting the serving path
- Constraints rejecting a chunk with no searchable text, a missing page number, a document reference with no matching document row, or a project identifier not matching E001's frozen format
- Tests demonstrating a weighted full-text query and both an exact and an approximate vector query against the same table

**Validation Criteria**:
1. **Given** chunks whose matching term appears in a heading in one row and in body prose in another, **When** a weighted full-text query is run, **Then** the heading match ranks above the body match.
2. **Given** chunks with stored vectors, **When** an exact-scan vector query and an approximate-index vector query are each run, **Then** both return results against the same table with no schema change between them.
3. **Given** a chunk row missing a page number, carrying a document reference with no matching row in the document table, carrying no searchable text at all, or carrying a project identifier that does not match the frozen format, **When** insertion is attempted, **Then** the database rejects it in each case.
4. **Given** a vector whose dimension differs from the declared one, **When** insertion is attempted, **Then** the database rejects it.
5. **Given** a chunk row, **When** it is read, **Then** the embedding model identity and revision that produced its vector are available on the row.
6. **Given** an identical chunk inserted by two sessions configured with different default text-search settings, **When** their search columns are compared, **Then** the values are identical, demonstrating the column is built against an explicitly named configuration.
7. **Given** a document row missing its license basis or its REAL/SYNTHETIC layer label, **When** insertion is attempted, **Then** the database rejects it — both are mandatory on every layer.
8. **Given** a `REAL` document row, **When** it is missing source, issuing body, or retrieval date, or carries any generator field, **Then** the database rejects it.
9. **Given** a `SYNTHETIC` document row, **When** it is missing generator identity, seed, generation date, or fixture hashes, **or carries an issuing body, source reference, or retrieval date**, **Then** the database rejects it — a generated document cannot record retrieval provenance it does not have.

### Objective 3 - Provenance-Enforced Extraction Storage (Priority: P1)

Make an unattributable extracted value impossible to persist. Citation and confidence are non-nullable columns rather than application convention, values synthesised from more than one chunk carry an explicit multi-source provenance representation, and a value that fails validation is recorded as an absence in a failure table rather than stored wrong.

**Why this priority**: This is the storage-boundary half of the project's traceability principle and the epic's second acceptance criterion; ingestion in the next wave cannot be written against a weaker guarantee without the guarantee ceasing to hold.

**Rationale**: Page provenance is a deterministic parsing fact inherited from the source chunk, never a model-emitted value. Placing enforcement in non-null constraints makes a violation impossible rather than merely detectable, which is the difference the quality-attribute table records as a measurement method. The multi-source representation and the failure table are the two named costs of that decision and have to exist in the schema for the guarantee to be complete.

**Deliverables**:
- A field-name lookup table seeded by migration, giving the extraction vocabulary a closed, foreign-key-enforced set that grows by inserting a row rather than by altering a type
- An extracted-value table with non-nullable source chunk reference, field-name foreign key, page citation, and per-field confidence; the value itself held as a canonical text column plus an optional typed numeric column
- A confidence range constraint admitting values from 0 through 1 inclusive, paired with a non-null constraint
- A composite foreign key carrying the cited page into the chunk reference, so a citation whose page differs from its source chunk's page cannot be inserted — no trigger required
- An explicit multi-source provenance representation for values derived from more than one chunk, with every contributing chunk and page recorded
- An extraction-failure table recording the attempted field, its source chunk, the validation outcome, and the repair attempt count
- Tests asserting that an insert missing a citation or a confidence is rejected, that an unknown field name is rejected, and that a failed extraction is representable only as a failure record

**Validation Criteria**:
1. **Given** an extracted value with no page citation, **When** insertion is attempted, **Then** the database rejects it.
2. **Given** an extracted value with no confidence, or a confidence outside the inclusive range 0 through 1, **When** insertion is attempted, **Then** the database rejects it.
3. **Given** a value derived from three chunks, **When** it is stored, **Then** all three contributing chunks and their pages are recoverable from the record.
4. **Given** an extraction that failed validation after its permitted repair attempt, **When** it is recorded, **Then** it appears as a failure record and no partial value row exists for it.
5. **Given** an extracted value whose cited page differs from its source chunk's page, **When** insertion is attempted, **Then** the composite foreign key rejects it, and every stored value's citation therefore resolves to an existing chunk at the cited page.
6. **Given** the schema as migrated, **When** every range or domain check is inspected, **Then** each has a paired non-null constraint on the same column, so no check is silently satisfied by a null.
7. **Given** an extracted value naming a field absent from the seeded vocabulary, **When** insertion is attempted, **Then** the database rejects it; **and given** a new field is required, **When** a row is added to the vocabulary table, **Then** it becomes usable without altering any column type.
8. **Given** the extracted-value table as migrated, **When** its columns are inspected, **Then** the value is held as a canonical text column plus an optional typed numeric column, and no foreign key references a purchase-order line or any other target record.

### Objective 4 - Procurement Lifecycle Store (Priority: P1)

Define the purchase-order line and its lifecycle event history: the ordered material item the product forecasts, carrying need-by date, criticality, and open-or-closed state, with an event sequence covering the progression from submitted through delivered including rework loops, and with the roster content hash recorded so every generated row is traceable to the roster revision it came from.

**Why this priority**: The synthetic procurement history in the next wave writes directly into these tables, and the forecast model reads them; nothing about delivery risk can be represented until the unit being forecast exists.

**Rationale**: The forecast model needs vendor and category structure, lifecycle state, days in state, approval-cycle count, and an explicit right-censoring signal for still-open orders — all of which are properties of these two tables rather than of the model. Project and vendor identifiers are frozen by E001 and must be adopted here rather than re-declared, and E001's contract requires the roster content hash to accompany every generated artifact while leaving the choice of where it is persisted to this epic.

**Deliverables**:
- A purchase-order line table with project reference, vendor reference, material category, order date, need-by date, criticality, current lifecycle state, and an open-or-closed indicator
- A lifecycle event table recording state, transition timestamp, and sequence, supporting repeated review cycles on one line
- A column persisting the roster content hash in the format E001 froze
- Constraints enforcing the frozen project and vendor identifier formats and a need-by date not earlier than the order date
- A `DEFERRABLE INITIALLY DEFERRED` foreign key from a closed line to its terminal delivery event, with the event's terminal flag carried into the referenced key so the pointer cannot name a non-terminal event; a constraint trigger is the named fallback if the cycle resists that shape
- Indexes supporting per-line event retrieval and per-vendor aggregation
- Tests asserting censoring is representable, rework loops are representable, and each constraint rejects its violation

**Validation Criteria**:
1. **Given** a purchase-order line whose need-by date precedes its order date, **When** insertion is attempted, **Then** the database rejects it.
2. **Given** a still-open line, **When** it is stored with no delivery event, **Then** it persists and is identifiable as right-censored; **and given** a line marked closed with no terminal delivery event, **When** the transaction is committed, **Then** the database rejects it. The criterion is stated at the commit boundary so it holds under either the deferrable foreign key or the fallback trigger.
3. **Given** a line whose submittal was rejected and resubmitted twice, **When** its history is stored, **Then** both review cycles are recoverable in order from the event table.
4. **Given** a project or vendor identifier not matching the frozen format, **When** insertion is attempted, **Then** the database rejects it.
5. **Given** a roster hash that is not a `sha256:`-prefixed 64-character lowercase hexadecimal string, **When** insertion is attempted, **Then** the database rejects it.

### Objective 5 - Versioned Forecast Artifact Contract (Priority: P1)

Define the contract between the offline modeling boundary and the request-serving boundary, which the architecture places in the database rather than in a Python interface: a forecast run carrying complete reproducibility metadata and a schema version, an explicit active-run pointer, per-line sorted posterior draw arrays as the canonical hashable artifact, and per-line day-grid survival arrays as the read path, bounded by a fixed horizon with residual tail mass recorded explicitly.

**Why this priority**: This is the epic's third acceptance criterion and the shared artifact the plan names as an E003 deliverable; the fit job, the worklist, the detail view, and the evaluation harness all read against this shape.

**Rationale**: Storing the full posterior is what lets a changed need-by date or criticality value reorder the worklist without a refit, and storing one row per line is what makes the artifact a natural checksummable unit a run manifest can point at. The derived day-grid array turns probability of lateness into a constant-time index lookup and the percentiles into inverse-cumulative lookups, keeping all of it inside the deterministic-computation boundary. The two representations can drift, which is why the schema — not just the writing job — must tie them to the same run. Selection by explicit active pointer rather than most-recent timestamp is what lets the interface state "no current forecast" instead of showing a stale one.

**Deliverables**:
- A forecast run table recording run identifier, code revision, input data hash, all sampling seeds, library versions, artifact hash, schema version, model version, creation time, the per-line draw count the run produced, and the run's single as-of anchor date
- An explicit active-run pointer enforced by a partial unique index, permitting at most one active run
- **One row per line-run** holding both the sorted posterior draw array and the derived day-grid survival array, so the two representations cannot half-exist and no cross-table pairing constraint is needed
- A residual tail-mass column on that same row recording probability beyond the horizon
- A composite foreign key carrying both the run's draw count and its survival horizon into the artifact row, so either array whose length disagrees with its run is rejected without a trigger
- An `IMMUTABLE` helper function backing a sortedness check on the draw array
- The anchor-date convention fixed as one as-of date per run, and the percentile convention fixed as nearest-rank one-based index arithmetic on the sorted draw array with no interpolation — both recorded in the schema-constants table
- A defined byte serialization over which each artifact hash is computed, so the digest does not depend on text-rendering settings
- Tests asserting that a second active run is rejected, that an unsorted or wrong-length array is rejected, and that reproducibility metadata is non-nullable

**Validation Criteria**:
1. **Given** a forecast run row missing any of run identifier, code revision, input data hash, sampling seeds, library versions, artifact hash, schema version, model version, or creation time, **When** insertion is attempted, **Then** the database rejects it.
2. **Given** one run already marked active, **When** a second run is marked active, **Then** the database rejects it.
3. **Given** no run marked active, **When** the active run is queried, **Then** the query returns no row rather than the most recent run.
4. **Given** an artifact row for a line-run, **When** it is written with a draw array but no survival array, or the reverse, **Then** the database rejects it — the two share one row, so a half-written pair is unrepresentable rather than merely detected.
5. **Given** a posterior extending beyond the fixed horizon, **When** its survival array is stored, **Then** the probability mass beyond the horizon is recorded as residual tail mass and the array plus the residual account for the full distribution within the recorded tolerance.
6. **Given** a stored artifact carrying a schema version a reader does not recognise, **When** the reader inspects it, **Then** the version is readable from the row so the reader can fail loudly rather than misread array offsets.
7. **Given** a draw array that is unsorted or whose length differs from the draw count recorded on its run, or a survival array whose length differs from the horizon recorded on that run, **When** insertion is attempted, **Then** the database rejects it in each case.
8. **Given** the same draw array hashed by two processes configured with different numeric text-rendering settings, **When** the digests are compared, **Then** they are identical, demonstrating the hash is taken over a defined byte serialization.
9. **Given** a forecast run with no as-of anchor date, **When** insertion is attempted, **Then** the database rejects it; **and given** an active run, **When** two lines' survival arrays are compared, **Then** the same array offset denotes the same calendar day for both.
10. **Given** a sorted draw array and a requested percentile, **When** the percentile is read, **Then** it is the value at the nearest-rank one-based index with no interpolation, and the result is reproducible from the array alone.

### Objective 6 - Resolved Cross-Document Entity Store (Priority: P2)

Define the table representing a material identity confirmed across specification, submittal, and purchase-order records, together with the references linking a resolved entity to each contributing record.

**Why this priority**: Significant value and named as an E003 key entity, but its first consumer is identity resolution two waves later, so the wave-three epics are unblocked without it.

**Rationale**: The epic's key-entity list and specify input both name the resolved entity, and holding it here keeps schema ownership in one epic rather than splitting it across waves. It is separated to P2 because the precision-biased scoring, candidate pairs, and review queue belong to identity resolution and are excluded from this epic — only the confirmed-identity shape is defined here.

**Deliverables**:
- A resolved-entity table with normalized manufacturer, normalized part number, and the attributes on which agreement was established
- A membership representation linking a resolved entity to each contributing extracted value and purchase-order line
- Constraints preventing a record from belonging to two resolved entities simultaneously
- Tests asserting the membership constraint and that a resolved entity with a single member is representable

**Validation Criteria**:
1. **Given** a resolved entity with members drawn from a specification, a submittal, and a purchase-order line, **When** it is stored, **Then** every member is recoverable from the entity.
2. **Given** a record already belonging to one resolved entity, **When** it is added to a second, **Then** the database rejects it.
3. **Given** a material appearing in only one document, **When** it is stored as a resolved entity, **Then** a single-member entity persists without error.

### Technical Constraints

- One PostgreSQL 16 instance holds all system state; no second datastore may be introduced
- Migrations are forward-only; no migration may contain a reverse operation
- E003 owns migration numbers `0001`–`0099` and a table set disjoint from E004's; neither epic may create a table the other owns
- Page citation and per-field confidence columns are non-nullable at the storage boundary
- Every forecast artifact carries a schema version, and run selection uses an explicit active-run pointer, never most-recent-timestamp ordering
- A line's canonical draw array and its derived survival array share one row, so they cannot exist independently
- Cross-table invariants are expressed as composite foreign keys or by collapsing tables wherever that is possible; the closed-line delivery-event rule is the one invariant no *immediate* declarative constraint can express, and it is carried by a deferrable foreign key, with a constraint trigger admissible only as a fallback
- The interface tier never queries the datastore, so the schema exposes no affordance intended for direct browser access
- Schema and migration assets reside in `/src/model`, which alone declares the database client and Alembic; `/src/api` reaches shared values through the schema-constants table rather than by importing, so neither Python boundary depends on the other and ADR-0010 stands unamended
- Verification belonging to a single entry lives inside that entry; only cross-entry verification may live at the repository root, and entry-local tests may not migrate there
- The concrete embedding dimension is set by a decision record authored as this epic's first task, landing before the chunk migration declares the vector column
- The Compose `db` service definition, its pinned image digest, host port, credentials, and the non-default `jobs` profile are fixed by E001's orchestration checks and must not change
- Design targets roughly 15,000 chunks, 200 purchase-order lines, and approximately 4,000 posterior draws per line, at effectively one concurrent user on a small hosted instance
- Project and vendor identifier formats and the roster hash format are frozen by E001 and are adopted here rather than re-declared

## Integration Points *(mandatory for technical and operational specs)*

- **IP-001**: This epic depends on E001 for the repository layout, per-entry dependency manifests, and container definitions.
- **IP-002**: This epic depends on E001's Compose `db` service and its `DATABASE_URL`, whose shape is asserted by the cross-entry orchestration check and must be consumed unchanged.
- **IP-003**: This epic depends on E001's frozen `PRJ-###` and `VND-###` identifier formats and its `sha256:`-prefixed roster hash format, which the procurement tables adopt.
- **IP-004**: E004 depends on this epic's migration sequence and reserved numbering for its own invocation-table migration, against a disjoint table set.
- **IP-005**: E005 depends on this epic's purchase-order line and lifecycle event tables and on the roster-hash column.
- **IP-006**: E006 depends on this epic's chunk, extracted-value, and extraction-failure tables and on the page-citation constraints.
- **IP-007**: E007 depends on this epic's forecast run contract and on the single per-line-run artifact row holding both the sorted draw array and the derived survival array, and on the composite foreign key carrying the run's draw count into that row.
- **IP-008**: E008 depends on this epic's field-weighted full-text column, dense vector column, and serving index, executing fusion as one statement against them.
- **IP-009**: E010 depends on this epic's survival-array shape, the run-level as-of anchor date, the nearest-rank percentile convention, and the active-run pointer for read-time risk computation, reading shared values from the schema-constants table rather than importing them.
- **IP-010**: E009 depends on this epic's resolved-entity store, and adds candidate-pair and review-queue tables that this epic does not define.
- **IP-011**: E017 depends on the purchase-order line remaining extensible so criticality override can be added without altering it.
- **IP-012**: The document table's key space depends on E002's corpus manifest identifiers; E006 loads document rows from that manifest before chunking, and every chunk's document reference is a foreign key into it.
- **IP-013**: The chunk migration depends on a decision record naming the embedding model, authored within this epic through the ADR Author sub-agent before that migration is written.
- **IP-014**: `/src/api` depends on the schema-constants table for the vector dimension, survival horizon, anchor-date and percentile conventions, draw count, and probability-sum tolerance, reaching them over the connection rather than by importing from `/src/model`.

## Requirements *(mandatory)*

### Technical Requirements *(technical specs only)*

**On the trailing italic notes.** Two kinds appear, both in the same parenthetical form:

- **Cross-references** — TR-021, TR-028, TR-029 and TR-030 name the requirements that carry their mechanism, length rule, or fallback. These exist because the checklist pass split several single obligations into a statement plus its mechanism, and without the pointer each half reads as a duplicate of the other.
- **Classifications** — TR-053, TR-057, TR-062, TR-064, TR-077, TR-078, TR-079, TR-080, TR-081, TR-082 and TR-085 are phrased as `MUST` but are not obligations on an artifact this epic delivers. Each is a hand-off to another epic, a scope statement, a semantic note, a deliberate absence, or a policy binding future work. The note says which, and where the enforceable half is if there is one.

The classifications were added after delivery, in remediation of analysis finding **A-011**. They change no obligation and no ID — the point is that a downstream epic reading this spec does not inherit TR-064 or TR-077 believing E003 owed them, and does not go looking for a constraint that TR-057 or TR-081 was never going to produce.

A requirement that turns out to be redundant is marked **subordinate in place** rather than removed. TR-053 is the one instance. The reason is referential, not ceremonial: `.github/skills/artifact-conventions/SKILL.md` prohibits changing a requirement ID because the IDs are mapped to tasks, coverage reports, and compliance checks — and TR-053's is live in three of them (a `{TR-###}` task tag on T044, a `plan.md` Requirement Coverage Map row, and a `data-model.md` traceability row). Deleting the ID orphans all three. This is a rule about artifact integrity, **not** a blanket prohibition on ever renumbering anything: an ID that collides across branches is renumbered locally before it reaches the repository, which is exactly what happened to ADR-0011 → ADR-0012 → ADR-0013 in this epic.

- **TR-001**: System MUST provide a numbered migration sequence that applies cleanly against an empty database with no manual intervention.
- **TR-002**: System MUST make migrations forward-only, containing no reverse operations.
- **TR-003**: System MUST be idempotent on re-application, re-running no migration already applied.
- **TR-004**: System MUST use Alembic as the migration tool and record E003's reserved block as `0001`–`0099`, reserving `0100`–`0199` for E004, carried as a migration filename prefix over Alembic's own revision identifiers.
- **TR-005**: System MUST fail migration-set verification when a migration's filename prefix falls outside the owning epic's reserved block, or when the set resolves to more than one Alembic head revision.
- **TR-006**: System MUST enable the `vector` extension as part of the migration sequence rather than as a manual setup step.
- **TR-007**: System MUST expose the migration runner as a console entry point on the modeling entry, invoked through that entry's own environment, and MUST NOT package it as a container job (ADR-0011). Its determinism is bound by the entry's lockfile rather than by an image digest.
- **TR-008**: System MUST place all schema and migration assets in `/src/model`, which alone declares Alembic and the ORM toolkit they are driven through, without adding a fifth `/src` entry and without either Python boundary declaring the other as a dependency. *(Post-QC correction, 2026-07-26 — this requirement previously read "alone declares the database client and Alembic". Taken literally that forbade `/src/api` the driver it needs to satisfy TR-047's own constants read, and {SAD:ADR-0013}'s chosen option lists "`/src/api` pays a startup read against the database before it can serve" among its costs. {SAD:ADR-0016} draws the distinction this wording missed: schema **authorship** — the DDL, the migration tooling, the migration job image — is exclusive to `/src/model`; a database driver held for a purpose an accepted record already sanctions is not restricted. Narrowed, not relaxed: `tests/checks/test_dependency_isolation.py` gains a file-level schema-asset check that catches an entry authoring DDL in raw SQL, which the distribution-name check never could. No object added, no ID changed, and E003 still satisfies the requirement.)*
- **TR-009**: System MUST store each chunk with its document reference, project identifier, document type, specification section, page number, and ordinal position.
- **TR-010**: System MUST provide a full-text search column with field weighting that ranks heading, part-number, and specification-section text above body prose.
- **TR-011**: System MUST declare the dense vector column at a single fixed dimension for the whole corpus and reject a vector of any other dimension.
- **TR-012**: System MUST record the embedding model identity and model revision on every chunk carrying a vector.
- **TR-013**: System MUST support both an exact vector scan and an approximate index lookup against the same chunk table with no schema change between them.
- **TR-014**: System MUST reject a chunk with no searchable text, no page number, no document reference, a malformed document reference, or a project identifier not matching the frozen format.
- **TR-015**: System MUST make the source chunk reference, page citation, and per-field confidence of an extracted value non-nullable.
- **TR-016**: System MUST reject a confidence value outside the inclusive range 0 through 1.
- **TR-017**: System MUST require an extracted value's page citation to match the page of the chunk it was read from, enforced by a composite foreign key carrying the page into the chunk reference rather than by a trigger.
- **TR-018**: System MUST represent a value derived from multiple chunks such that every contributing chunk and page is recoverable.
- **TR-019**: System MUST record an extraction that failed validation as a failure record carrying the attempted field, source chunk, outcome, and repair-attempt count, with no partial value row.
- **TR-020**: System MUST store each purchase-order line with project reference, vendor reference, material category, order date, need-by date, criticality, lifecycle state, and open-or-closed indicator.
- **TR-021**: System MUST reject, at transaction commit, a purchase-order line marked closed that has no terminal delivery event. *(The right-censoring representation is TR-066, the enforcement mechanism TR-067, and the fallback ladder TR-065.)*
- **TR-022**: System MUST represent repeated review cycles on a single line as an ordered lifecycle event sequence.
- **TR-023**: System MUST reject a purchase-order line whose need-by date precedes its order date.
- **TR-024**: System MUST persist the roster content hash in the frozen `sha256:` plus 64-lowercase-hexadecimal format and reject any other form.
- **TR-025**: System MUST reject project and vendor identifiers not matching the frozen `PRJ-###` and `VND-###` formats.
- **TR-026**: System MUST record on each forecast run the run identifier, code revision, input data hash, all sampling seeds, library versions, artifact hash, schema version, model version, and creation time, each non-nullable.
- **TR-027**: System MUST permit at most one forecast run to be marked active, and MUST return no row rather than a fallback when none is active.
- **TR-028**: System MUST reject a posterior draw array that is not sorted in ascending order. *(The array's canonical status is TR-068, its length rule TR-069, the sortedness mechanism TR-070, and the length mechanism TR-073.)*
- **TR-029**: System MUST store one day-grid survival array per line per run, spanning the run's declared horizon in whole days with exactly one element per day. *(The shared day-zero is TR-049, the horizon record TR-071, the length rule TR-072, and the length mechanism TR-073.)*
- **TR-030**: System MUST record residual probability mass beyond the survival horizon as an explicit value, such that the array and the residual account for the full distribution. *(The residual's sufficiency for an out-of-horizon need-by date is restated as TR-053, whose remaining content is a hand-off to E010 rather than a second obligation here.)*
- **TR-031**: System MUST hold a line's draw array and survival array in a single row per line-run, so neither can exist without the other by construction rather than by a cross-table constraint.
- **TR-032**: System MUST expose the schema version on every forecast artifact so a reader can detect an unrecognised version before reading array offsets.
- **TR-033**: System MUST fix the anchor-date convention as one as-of date per forecast run, stored on the run row, and the percentile convention as nearest-rank one-based index arithmetic on the sorted draw array with no interpolation, both recorded once in the schema-constants table.
- **TR-034**: System MUST define the resolved-entity table with normalized manufacturer, normalized part number, agreement attributes, and membership references to contributing records.
- **TR-035**: System MUST prevent a record from belonging to more than one resolved entity.
- **TR-036**: System MUST create none of the following tables, which other epics own: llm-invocation, price-table-version, and price-table-entry (E004); candidate-pair and review-queue (E009); criticality-override (E017). The list is explicit so the requirement does not depend on an ownership predicate another document can invalidate. *(Post-QC correction, 2026-07-26 — E004's names were wrong here: the registered `specs/sad.md` names the invocation table `llm_invocation`, E004 owns a third table `price_table_entry` that was unlisted, and `response_fixture` is not a table at all — E004's fixtures are committed files with provenance sidecars. E003 creates none of these under either spelling, so the requirement's verdict is unchanged and the assertion is strictly stronger. ID unchanged.)*
- **TR-052**: This epic MUST **record the need to amend** — and MUST NOT itself perform — the correction to `specs/project-plan.md`'s Shared Data Entities rows, so ResolvedEntity and the posterior artifacts read `E003 (schema), E009 (populated)` and `E003 (schema), E007 (populated)`. Under v1.2.0 amendments serialize on the default branch and a feature branch records the need rather than performing it, so the edit itself is out of scope here and belongs to `.github/skills/amend-project/SKILL.md` run on `main` in the convention the Chunk row already uses.
- **TR-037**: System MUST leave `docker-compose.yml` entirely unchanged — no service added, altered, or removed — since the migration runner is a console entry point rather than a container job. `tests/checks/test_orchestration.py`'s `JOBS` frozenset is therefore untouched. This is stronger than the original obligation, which permitted adding a job under the non-default profile.
- **TR-038**: System MUST build the full-text search column against an explicitly named text-search configuration rather than a session-dependent default, so the column remains derivable and indexable.
- **TR-039**: System MUST pair every range or domain constraint with a non-null constraint, because a range check is satisfied when its expression is null.
- **TR-040**: System MUST compute each forecast artifact hash over an explicitly defined byte serialization of the array rather than over its text rendering.
- **TR-041**: System MUST treat a chunk's document reference as a non-null corpus manifest key formatted as a lowercase kebab-case slug of 3 to 128 characters, so every page citation resolves to a named source document rather than to a bare page number.
- **TR-042**: System MUST place verification that belongs to a single entry inside that entry, reserving the repository-root test location for cross-entry verification only.
- **TR-043**: System MUST record the declared vector dimension, survival-grid horizon, anchor-date convention, percentile convention, per-run draw count, and the probability-sum tolerance exactly once, in a single-row schema-constants table, rather than repeating them per call site.
- **TR-044**: System MUST define the extraction field vocabulary as a lookup table seeded by migration and referenced by foreign key, so a new term is added by inserting a row and no term can be used before it is defined.
- **TR-045**: System MUST store an extracted value as a canonical text column plus an optional typed numeric column, and MUST NOT place a direct foreign key from the extracted value to a purchase-order line — the resolved-entity membership table is the only sanctioned join between them.
- **TR-046**: System MUST define a document table keyed by the corpus manifest identifier, and MUST make every chunk's document reference a foreign key into it.
- **TR-047**: System MUST expose shared schema values through the single-row schema-constants table so `/src/api` reads them over the connection, never by importing from `/src/model`. Reading over the connection presupposes `/src/api` holds a database driver; that is sanctioned by {SAD:ADR-0016} and is not a breach of TR-008, which governs schema authorship rather than driver possession.
- **TR-048**: System MUST ensure the schema-constants table agrees with the migrated schema — in particular that the recorded vector dimension equals the dimension the chunk column was declared with — verified by test rather than asserted.
- **TR-049**: System MUST record a non-null as-of anchor date on every forecast run, so one array offset denotes the same calendar day across every line in that run.
- **TR-050**: System MUST have an accepted decision record naming the embedding model and fixing the vector dimension present in `specs/adrs/` before the chunk migration is authored, produced through the ADR Author sub-agent.
- **TR-051**: System MUST NOT rely on deferring a check or non-null constraint, since PostgreSQL does not permit it; an invariant that must hold across two statements within a transaction MUST use a deferrable foreign key or a constraint trigger.
- **TR-053**: System MUST store the residual tail mass such that a need-by date beyond the run's survival horizon is answerable from the stored row alone, without an out-of-range array offset. Performing that read-time arithmetic is E010's, not this epic's — this epic's obligation ends at making the value present and sufficient. *(Subordinate to TR-030, which states the storage obligation this repeats; the only delta is a read-time hand-off to E010. Retained rather than deleted because this ID is referenced by T044's task tag, a `plan.md` coverage row, and a `data-model.md` traceability row — deleting it orphans all three — but it imposes no duty TR-030 does not already carry.)*
- **TR-054**: System MUST treat per-field confidence as a continuous double-precision score on the closed interval 0 through 1, with both endpoints admissible as the extremes of the extracting agent's expressed confidence rather than as literal certainty or impossibility, and MUST impose no coarser scale, bucketing, or minimum discrimination step on the stored value.
- **TR-055**: System MUST require a stored residual tail mass to agree with the final element of its survival array to within the declared probability-sum tolerance, comparing both at double precision, rather than by exact floating-point equality.
- **TR-056**: System MUST seed the schema-constants row with a survival horizon of 365 days, a per-run draw count of 4000, and a probability-sum tolerance of 1e-9, each carrying a recorded reversal trigger and production-scale alternative in the data model's declared-constants scope-decision record.
- **TR-057**: System MUST treat a document row's manifest key as identifying one fixed revision of a source document, so a superseding revision is loaded as a new document row under a distinct key and every existing citation continues to resolve to the revision it was extracted from. *(Semantic definition of key identity, not a build obligation. No column or constraint carries it — a superseding revision is a new row by convention, and the enforceable half is the deliberate absence of a revision column.)*
- **TR-058**: System MUST anchor every chunk to exactly one page, so a value spanning several pages is representable only as a multi-source value carrying one contributing chunk per page, and a value appearing on one page in several chunks carries one contributing chunk per chunk.
- **TR-059**: System MUST hold the anchor citation of a multi-source value as the single chunk and page on the value row itself, with every further contributing chunk held at contributor ordinals 2 through N, so the composite page tie of TR-017 applies unchanged to multi-source values.
- **TR-060**: System MUST treat contributor ordinal 1 as denoting the anchor citation and ordinals 2 through N as a stable but unordered enumeration carrying no precedence, so no reader may infer importance, confidence, or document order from the ordinal.
- **TR-061**: System MUST make a value with no identifiable source page unstorable as an extracted value, and MUST record the attempt as an extraction failure with a missing-citation outcome against the chunk the attempt was made from.
- **TR-062**: System MUST carry the forecast artifact's provenance at run granularity — the recorded code revision, input data hash, and sampling seeds — with no per-line link back to the extracted values or lifecycle events the fit consumed, so reproduction is by re-running the recorded run rather than by row-level lineage. *(Deliberate absence. What this requires is that no per-line lineage link exists, so it is verified as an absence rather than as a delivered object.)*
- **TR-063**: System MUST make rejection of the write the outcome of every integrity rule in this schema — no rule may default, coerce, silently truncate, or quarantine a violating value — the only exception being the creation-timestamp and active-flag columns whose declared defaults supply a value solely when the writer omits the column.
- **TR-064**: System MUST oblige a reader that does not recognise a stored artifact's schema version to refuse to read the arrays and report no usable forecast, rather than reading array offsets under an assumed layout. *(Hand-off to E010. No object in this schema can oblige a reader to refuse a read; disclosed as gap G-10 in `data-model.md`.)*
- **TR-065**: System MUST take the closing-event enforcement fallbacks in the recorded order — the deferrable foreign key first, the plain-nullable-column variant second, the deferred constraint trigger last — with the choice made in the migration that creates the purchase-order line and its events, and the shape actually taken recorded in the data model's invariant-to-mechanism map before implementation closes.
- **TR-066**: System MUST allow a still-open purchase-order line to persist with no delivery event and be identifiable as right-censored.
- **TR-067**: System MUST carry the closed-line rule with a `DEFERRABLE INITIALLY DEFERRED` foreign key from the line to its closing event, with the event's terminal flag carried into the referenced key so the pointer cannot name a non-terminal event — **unless** that shape proves unworkable, in which case TR-065's ordered fallback ladder governs and the shape actually taken is recorded. This is the first rung of that ladder, not an alternative to it.
- **TR-068**: System MUST treat the sorted posterior draw array as the canonical artifact of a line-run, from which the survival array and every percentile are derived and over which the artifact digest is taken.
- **TR-069**: System MUST reject a posterior draw array whose length differs from the draw count recorded on its run.
- **TR-070**: System MUST enforce draw-array sortedness with an `IMMUTABLE` numeric helper function inside a named check constraint, so the invariant is re-proved row by row on restore rather than trusted.
- **TR-071**: System MUST record on every forecast run the survival horizon in whole days that the run's arrays were built over.
- **TR-072**: System MUST reject a survival array whose length differs from the horizon recorded on its run.
- **TR-073**: System MUST enforce both array lengths by carrying the run's draw count and horizon into a composite foreign key from the artifact row to its run, so each length comparison is a single-row check, since PostgreSQL does not enforce a declared array size.
- **TR-074**: System MUST scope every document row to exactly one project. The corpus manifest carries one entry per source-and-project pair, so the manifest key TR-046 keys on is already per-project and no synthesis is needed: a source referenced by several projects has several manifest entries, hence several keys and several document rows.
- **TR-075**: System MUST record the license basis and the REAL or SYNTHETIC layer label on every document row, and MUST make the remaining provenance layer-dependent: a `REAL` row records source, issuing body, and retrieval date and carries no generator fields; a `SYNTHETIC` row records generator identity, seed, generation date, and fixture content hashes and carries **no** retrieval provenance at all. Absence on the wrong layer MUST be enforced, not merely permitted — a fabricated issuing body is indistinguishable downstream from a verified one. Per-project duplication replicates the license basis with the row so no corpus location mixes licenses; a copyrighted reference standard is represented by its citation row alone, and loading body text for it remains E002's and E006's obligation rather than a constraint this schema carries.
- **TR-087**: System MUST provide generator identity, seed, generation date, and fixture content hash columns on the document row, required together on a `SYNTHETIC` row and rejected on a `REAL` one, so a generated document can record the provenance it actually has.
- **TR-076**: System MUST treat the migration DDL literal as authoritative over the published schema-constants row when the two disagree, so a drift failure is repaired by correcting the published row and never by altering the column the literal declared.
- **TR-077**: System MUST have E002 and E006 adopt the `document_id` format this epic declares, accepted when every corpus manifest key matches that format and E006's loader test asserts it before any document row is written. *(Hand-off to E002 and E006. Its acceptance condition sits in E006's loader test, so this epic satisfies only the half that declares the format.)*
- **TR-078**: System MUST make a later change to the `document_id` key space a forward migration that updates the document key in place, propagating to every chunk by cascade, leaving extracted-value citations untouched because they reference the chunk rather than the document, so no reload of loaded rows is required. *(Forward-migration policy, binding a migration nobody has written yet. Carried today by `fk_chunk__document ON UPDATE CASCADE`, which is what makes an in-place key update propagate; `ON DELETE RESTRICT` is TR-046's direction, not this one.)*
- **TR-079**: System MUST make the migration-seeded reference data — the single schema-constants row and the seeded field-vocabulary rows — recoverable only by re-applying the migration sequence against a rebuilt database, since backup and restore are out of scope and the epic stores no domain data; the loss of the constants row is detected by the constants-agreement check and the loss of a referenced vocabulary row is prevented by its restricting foreign key. *(Consequence of an out-of-scope decision — backup and restore. The enforceable half is loss detection, carried by the constants-agreement and field-vocabulary checks.)*
- **TR-080**: System MUST expose the run's as-of anchor date so a reader computes the active artifact's age itself, and MUST impose no maximum permitted age on the active forecast run; the staleness threshold and its interface treatment belong to E010. *(Three statements in one: it restates TR-049's anchor date, adds a deliberate non-requirement — no maximum age — and hands the staleness threshold to E010. The enforceable half is the absence of a max-age constant.)*
- **TR-081**: System MUST treat per-field confidence as a **computed score**, derived deterministically by the producing epic from parse signals recorded alongside the value at the recorded extraction time, and never as a calibrated probability, so no reader may interpret it as a frequency or compare it across fields as one. *(Reader-directed semantic note. It constrains interpretation, not storage; no constraint can prevent a miscomparison, so it is carried as recorded semantics in `data-model.md`.)* *(Amended 2026-07-27, correction carrying evidence: originally "a self-reported score asserted by the extracting agent". E006 computes the score in deterministic code from parse signals rather than accepting one the model asserted about its own output, which puts it on the code side of Principle V. The non-calibration half is unchanged and was always correct; only the source of the number moved. Requested by E006 FR-047 and landed here, on the default branch, because the requirement is E003's.)*
- **TR-082**: System MUST identify the agent responsible for a citation at run granularity through the ingestion run that wrote it rather than on the extracted-value row, so the storage boundary carries the extraction time as its only per-row temporal fact. *(Deliberate absence plus a hand-off to E006. The enforceable half is the absent per-row agent column; run-granularity attribution is E006's to write.)*
- **TR-083**: System MUST define every table and column it creates in `data-model.md`, which is normative for reader-facing semantics, and MUST NOT create an object absent from it — the understandability guarantee that `field_vocabulary.label` and `.description` give vocabulary terms, given at document level for tables such as `line_posterior` and `forecast_run` that carry no such column. **The prohibition binds the objects this epic creates.** A later epic that additively extends a table this epic created, under the recorded exception {SAD:ADR-0023} grants and only when every one of that record's admitting conditions holds, does not breach it: the added object is documented by name in the extending epic's own `data-model.md`, which is where TR-083's "somewhere, by name, in a reviewed artifact" is satisfied for an object E003 does not create. What this epic owes such an extension is **admission** — `data-model.md` MUST record the added columns, the dropped-and-re-created constraints, the new indexes and foreign keys, and any recorded rationale of this epic's that the extension reverses, so E003's own inventory stays true of the database it describes. **E003 itself still MUST NOT create an object absent from `data-model.md`**; that is the whole of this requirement's force against this epic and it is unchanged. *(Amended 2026-07-29 on E009's obligation `P-6`, with {SAD:ADR-0023} as its authority. Originally the first sentence alone. E009's migration `0505` adds five columns to `resolved_entity` and `resolved_entity_member` and drops and re-creates three of their unique constraints under new names; {SAD:ADR-0017}'s condition 3 makes adding or removing a named object or constraint a Specify-level change rather than a `data-model.md` correction, which is why this reaches a requirement and not only that document. **Read unamended, TR-083 was falsified by its own document**: from the moment `0505` landed, the inventory TR-083 declares normative was short five columns and wrong on three constraint names, so the requirement forbidding undocumented objects was itself the undischarged thing. Landed here, on the default branch, because the requirement is E003's. **E003's QC verdict is not reopened and `.qc-passed` stands** — QC audited the schema `0010` leaves behind and missed nothing; what changed is the schema, by a later epic under a recorded exception, not the audit.)*
- **TR-084**: System MUST make an extracted-value, contributing-chunk, or extraction-failure row append-only once written, enforced by revoking `UPDATE` and `DELETE` on all three tables from the application role in a migration rather than left to caller discipline: a correction is made by removing and reloading the affected chunks in the order the restricting foreign keys permit, never by editing a stored citation, page, confidence, or outcome in place.
- **TR-086**: System MUST grant the migration role the privileges a remove-and-reload correction needs while withholding them from the application role, so the correction path stays open without reopening in-place edit.
- **TR-085**: System MUST retain every extracted-value and extraction-failure row for the life of the database, since retention policy is out of scope and the full dataset is regenerable from the repository and its jobs. *(Scope statement. It asserts the absence of a deletion path rather than requiring one to be built; retention policy is out of scope and the dataset is regenerable.)*

### Key Entities *(include for product or technical specs if feature involves data)*

- **Document**: A source document from the corpus, keyed by its manifest identifier and defined here so every chunk's document reference has a real referent; populated by E006 from E002's manifest. Carries license basis and a REAL or SYNTHETIC layer label on every row, plus layer-dependent provenance: source, issuing body, and retrieval date when retrieved; generator identity, seed, generation date, and fixture hashes when generated. Each layer's fields are rejected on the other layer, so a generated document cannot carry retrieval provenance it does not have. Scoped to exactly one project.
- **Chunk**: A structure-aligned passage of a document, carrying a foreign-key document reference, plus project, document type, specification section, page, and ordinal position, a field-weighted full-text representation, and a fixed-dimension dense vector with its embedding model identity and revision.
- **FieldVocabulary**: The closed set of extraction field names, seeded by migration and referenced by foreign key, so the vocabulary grows by inserting a row rather than by altering a type.
- **ExtractedValue**: A structured field read out of a chunk, naming its field through the vocabulary, carrying a mandatory page citation tied to the source chunk's page by composite foreign key and a mandatory per-field confidence, and holding its value as canonical text plus an optional typed numeric; may reference several chunks when synthesised across them.
- **ExtractionFailure**: A record that an attempted extraction never became storable, holding the attempted field, source chunk, validation outcome, and repair-attempt count, so the field is absent rather than wrong.
- **PurchaseOrderLine**: A single ordered material item — the unit forecast by the product — with project, vendor, category, order date, need-by date, criticality, lifecycle state, open-or-closed indicator, and the roster hash it was generated against.
- **LifecycleEvent**: One transition in a line's progression from submitted to delivered, ordered within the line and able to repeat for rework loops.
- **ResolvedEntity**: A material identity confirmed across specification, submittal, and purchase-order records, with normalized manufacturer and part number and membership references to each contributing record.
- **ForecastRun**: One offline fit, carrying code revision, input hash, seeds, library versions, artifact hash, schema version, model version, creation time, draw count, and the run's single as-of anchor date — the reproducibility contract between the modeling and serving boundaries.
- **PosteriorDraws**: One sorted array of posterior predictive delivery-duration draws for a line under a run; the canonical, hashable artifact. Shares one row with SurvivalArray so the pair cannot half-exist.
- **SurvivalArray**: The day-grid array derived from the same row's draws, spanning the fixed horizon from the run's anchor date, with residual mass beyond the horizon recorded explicitly; the read path for probability and percentile lookups.
- **ActiveForecastRunPointer**: The explicit designation of at most one run as active, enforced by a partial unique index, replacing most-recent-timestamp selection.
- **SchemaConstants**: A single-row table publishing the vector dimension, survival-grid horizon, anchor-date convention, percentile convention, draw count, and probability-sum tolerance, so both Python boundaries read one source of truth over the connection instead of importing from each other.

## Assumptions & Risks *(mandatory)*

### Assumptions

- The `vector` extension is present in the pinned image and can be enabled by the migration role without a privileged operation outside the container.
- One embedding dimension serves the entire corpus; no chunk requires a different vector space, so a single declared dimension is sufficient.
- Corpus scale stays within roughly 15,000 chunks, 200 purchase-order lines, and approximately 4,000 draws per line, so no partitioning or sharding is needed.
- `/src/model` can carry the database client and Alembic without tripping E001's four-entry or dependency-isolation contracts, since it adds no new entry and no boundary-to-boundary dependency.
- E004 accepts the migration-number block reserved for it here, so the Wave 2 collision risk is closed by this claim rather than by later negotiation.

### Risks

- **The schema-constants table drifts from the migrated DDL** *(likelihood: medium, impact: high)*: The vector dimension is a literal in the chunk migration and also a published row that `/src/api` reads; if they disagree, the serving boundary computes against a dimension the column does not have. Mitigation: TR-048 requires a test asserting the recorded constants match the actual schema, so drift fails the build rather than surfacing as a query error.
- **The closing-event foreign key may not be expressible as specified** *(likelihood: medium, impact: medium)*: Enforcing "a closed line has a terminal delivery event" through a deferrable foreign key carrying the event's terminal flag is the preferred mechanism, but the cycle between line and event may resist it. Mitigation: a constraint trigger is the named fallback, accepting its cost — per-row firing and the fact that a data-only restore with triggers disabled loads straight past it.
- **The fixed survival horizon is chosen too short** *(likelihood: medium, impact: medium)*: If residual tail mass is large for slow-moving lines, the planning-relevant percentile falls outside the grid and becomes unreadable. Mitigation: store residual mass explicitly rather than truncating silently, so the condition is visible and the horizon can be raised in a forward migration.

## Implementation Signals *(mandatory)*

- `MIGRATION` — A forward-only Alembic sequence in `/src/model` with a reserved filename-prefix block, applying cleanly from empty and idempotent on re-application; the vehicle for every other signal in this epic.
- `NEW-ENTITY` — Document, chunk, field vocabulary, extracted value, extraction failure, purchase-order line, lifecycle event, resolved entity, forecast run, the combined draw-and-survival artifact row, and the schema-constants row, with traceability invariants expressed as composite foreign keys and checks rather than application logic.
- `NEW-CONFIG` — Database connection configuration consumed by the migration tooling, the schema-constants row (vector dimension, survival horizon, anchor-date and percentile conventions, draw count, probability-sum tolerance), and the recorded migration-block reservation.
- `NEW-CONFIG` — A migration console entry point declared in `src/model/pyproject.toml` under `[project.scripts]`, invoked through the modeling entry's own environment (ADR-0011). No container job, no Compose service, no image digest.
- `EXTERNAL-SERVICE` — None introduced; noted explicitly because the embedding-model decision record names a provider whose selection this epic gates but whose invocation belongs to E006.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001** [OBJ1]: The migration sequence applies from an empty database to the full schema in a single command with zero manual steps, and re-application changes nothing.
- **SC-002** [OBJ1]: 100% of migrations in the sequence contain no reverse operation, and a migration numbered outside the reserved block fails verification.
- **SC-003** [OBJ1]: `docker-compose.yml` is byte-identical to its pre-epic state, E001's orchestration, layout, build-context, and supply-chain checks all pass **unmodified**, and migrations run instead through the modeling entry's console entry point.
- **SC-004** [OBJ2]: A weighted full-text query ranks a heading match above an otherwise equivalent body-prose match on 100% of the weighting test cases.
- **SC-005** [OBJ2]: Both an exact vector scan and an approximate index lookup execute against the chunk table with no schema difference between the two paths.
- **SC-006** [OBJ2]: 100% of stored chunks carry a page number, a project identifier in the frozen format, an embedding model identity, and a vector of the declared dimension — measured over this epic's schema-test fixtures and, structurally, over every row the schema admits, since a chunk missing any of the four is not insertable. Measurement against loaded corpus data is E006's, not this epic's.
- **SC-007** [OBJ3]: 100% of attempts to insert an extracted value without a page citation or a per-field confidence are rejected by the database.
- **SC-008** [OBJ3]: Every stored extracted value's citation resolves to an existing chunk whose page matches the cited page, with zero unresolvable citations.
- **SC-009** [OBJ3]: A failed extraction is representable only as a failure record carrying its repair-attempt count, with zero partial value rows — measured over this epic's schema-test fixtures in two halves: a value row cannot be partial structurally, since its canonical text column is non-null and non-empty, and a value row and a failure row for the same attempt are **not** mutually exclusive — gap G-5 discloses that both can coexist, and its test asserts exactly that rather than their absence. The criterion is met by the structural half; the cross-row half is a disclosed gap, not an enforced guarantee, and must not be read as one.
- **SC-010** [OBJ4]: Right-censored lines, rework loops, and terminal delivery events are each representable and distinguishable from one another in the stored history.
- **SC-011** [OBJ4]: 100% of attempts to store a malformed project identifier, vendor identifier, roster hash, or an inverted order/need-by date pair are rejected.
- **SC-012** [OBJ5]: 100% of forecast run rows carry all nine reproducibility fields, with insertion rejected when any is missing.
- **SC-013** [OBJ5]: At most one run can be marked active at any time, and querying the active run with none set returns no row rather than a fallback.
- **SC-014** [OBJ5]: A line's draw array and survival array can only exist as a matched pair under one run; every attempt to write one without the other is rejected.
- **SC-015** [OBJ5]: For every stored survival array, the array plus its recorded residual tail mass accounts for the full probability distribution within a stated floating-point tolerance rather than by exact equality.
- **SC-016** [OBJ6]: A resolved entity spanning specification, submittal, and purchase-order members is fully recoverable, and a record cannot belong to two entities.
- **SC-017** [OBJ1]: The migrated schema contains none of the six explicitly named other-epic tables, keeping Wave 2 table ownership disjoint. The `specs/project-plan.md` discrepancy is carried as a recorded amendment request under SC-027, not as an edit this branch performs.
- **SC-018** [OBJ2]: 100% of stored chunks carry a document reference resolving by foreign key to a row in the document table, so no citation terminates at a page number alone — measured over this epic's schema-test fixtures and, structurally, over every row the schema admits, since a chunk with an unresolvable document reference is not insertable. Measurement against loaded corpus data is E006's.
- **SC-019** [OBJ1]: The schema-constants table holds exactly one row, its recorded vector dimension matches the dimension the chunk column was declared with, and only `/src/model` declares the database client and migration tooling.
- **SC-020** [OBJ1]: An accepted decision record naming the embedding model exists in `specs/adrs/` before the chunk migration is authored, and the migration's declared dimension matches it.
- **SC-021** [OBJ3]: 100% of attempts to store an extracted value under a field name absent from the seeded vocabulary are rejected, and adding a term requires no column-type change.
- **SC-022** [OBJ5]: Every forecast run carries a non-null as-of anchor date, and the same array offset denotes the same calendar day for every line in that run.
- **SC-023** [OBJ3]: The extracted-value table holds its value as canonical text plus an optional typed numeric column, with zero foreign keys to a purchase-order line or other target record.
- **SC-024** [OBJ1]: Zero check or non-null constraints in the migrated schema are declared deferrable, and every two-statement invariant is carried by a deferrable foreign key or the single sanctioned fallback trigger.
- **SC-025** [OBJ5]: Both arrays on an artifact row have their length enforced against the run row, so a wrong-length draw array or survival array is rejected at insert.
- **SC-026** [OBJ2]: Both retrieval arms resolve to the same relation and the same vector column, differing only by a session-level planner setting, with the migrated object set identical before and after each query — a second table, a second vector column, or any DDL applied between the two arms fails the criterion.
- **SC-027** [OBJ1]: A recorded amendment request states the exact replacement text for both Shared Data Entities cells — `E003 (schema), E009 (populated)` for ResolvedEntity and `E003 (schema), E007 (populated)` for the posterior artifacts, both Consumed-by cells unchanged — so the amendment can be applied on `main` without re-deriving it. The criterion is met by the request existing and being precise, **not** by the default-branch file having changed; making `.qc-passed` depend on a branch edit v1.2.0 prohibits would deadlock the gate. QC confirms the request is present and precise.
- **SC-028** [OBJ3]: 100% of attempts to `UPDATE` or `DELETE` an extracted-value, contributing-chunk, or extraction-failure row **as the role the revoke names** are refused by the database, while the same operations succeed as the migration role — so append-only is a privilege fact rather than a caller convention. **Scope qualifier, not a caveat to skim**: the deployed application currently connects as a superuser, which bypasses every privilege check, so this criterion is satisfied against `procurement_app` and **not** against the connecting role. Append-only is therefore latent rather than operative today. See gap G-11 for the reversal trigger. Do not read this criterion as evidence that an in-place edit is currently impossible.

## Glossary *(include when spec introduces 2+ domain-specific terms)*

| Term | Definition |
|------|------------|
| Anchor date | The single as-of date recorded on a forecast run, from which every line's survival grid in that run is measured, so one array offset denotes the same calendar day across all lines. |
| Percentile convention | Nearest-rank one-based index arithmetic on the sorted draw array with no interpolation — the read path ADR-0004 chose, kept as an integer index rather than a float computation. |
| Schema constants | The single-row table publishing the vector dimension, survival horizon, anchor-date and percentile conventions, draw count, and probability-sum tolerance, so both Python boundaries read one source of truth over the connection. |
| Field vocabulary | The closed, migration-seeded set of extraction field names, referenced by foreign key so an undefined term cannot be stored and a new term needs no type change. |
| Day-grid survival array | A per-line array over consecutive days holding the probability that delivery has not yet occurred, used as the read path for probability and percentile lookups. |
| Residual tail mass | The probability weight lying beyond the survival grid's fixed horizon, stored explicitly so no mass is silently discarded. |
| Active-run pointer | The explicit designation of one forecast run as current, used in place of most-recent-timestamp ordering so "no forecast" is distinguishable from "stale forecast". |
| Forward-only migration | A schema change with no reverse operation; the sequence advances and is never rolled back. |
| Migration block | A reserved range of migration filename prefixes claimed by an epic at its start, layered over Alembic's own revision identifiers to prevent collision between epics adding schema in the same wave. |
| Field weighting | Assigning greater full-text ranking weight to heading, part-number, and specification-section text than to body prose, compensating for the absence of corpus-level term statistics. |
| Right-censored line | A purchase-order line still open, known only to have taken at least its elapsed duration so far, with no delivery event recorded. |
| Multi-source provenance | The representation used when an extracted value is synthesised from more than one chunk, recording every contributing chunk and page rather than a single citation. |
| Roster hash | The content hash of the project and vendor roster, formatted as `sha256:` followed by 64 lowercase hexadecimal characters, recorded alongside every artifact generated from that roster. |
| Schema version | The version stamped on a forecast artifact so a reader detects an incompatible layout and fails loudly rather than misreading array offsets. |

## Clarifications

### Session 2026-07-25

- Q: How should the day-grid survival array be bounded? → A: A uniform fixed day horizon measured from a stated anchor date, with probability mass beyond the horizon stored as an explicit residual rather than truncated. The horizon value itself is a declared schema constant set during planning. Resolves the open question recorded in `specs/sad.md`.
- Q: The chunk table needs a vector column with a declared dimension, but no registered document names an embedding model. How should this epic handle it? → A: Require a single fixed dimension declared once for the whole corpus plus per-chunk embedding model identity and revision, so a mismatch refuses to serve. The concrete dimension is set by a project-level decision record naming the model, not asserted here.
- Q: Which migration-number claiming scheme should be recorded, given the plan requires numbers to be claimed at epic start? → A: Reserved numeric blocks — `0001`–`0099` for E003 and `0100`–`0199` for E004 — applied as an ordering or labelling convention over whatever revision identifier the tooling uses.
- Q: `specs/project-plan.md` is internally inconsistent about ResolvedEntity — the E003 epic detail lists it as a key entity while the Shared Data Entities table credits E009. Which reading applies? → A: Follow the more specific E003 epic detail and Specify input; define the resolved-entity store here at P2, since its first consumer is E009 two waves later. The plan's Shared Data Entities row warrants a corresponding correction.
- Q: Where do the migration assets and the single schema-constants record live, given TR-008, TR-043, and ADR-0010's prohibition on either Python boundary depending on the other? → A: Constants live in a single-row schema-constants table both boundaries read over the connection; migration assets live in `/src/model`, which alone declares the database client and Alembic, and the migration job image builds from it. No Python sharing between boundaries, so ADR-0010 stands unamended and no fifth `/src` entry is needed.
- Q: Four MUST requirements state invariants a plain check cannot express (TR-017, TR-021, TR-028, TR-031), and cross-row checks are ruled out. What is the sanctioned enforcement mechanism? → A: Carry the redundant column into composite foreign keys where that works — `(chunk, page)` and `(run, draw_count)` — and collapse the draw and survival arrays into one row per line-run so the pair cannot half-exist. Only the closed-line delivery-event rule needs more, and it uses a deferrable foreign key carrying the event's terminal flag, with a constraint trigger as the named fallback.
- Q: The chunk's document reference has no referent table anywhere. Who owns the document table? → A: E003 defines a document table keyed by the corpus manifest identifier, with the chunk's document reference as a real foreign key; E006 loads manifest rows before chunking. Permitted, since TR-036 bars only E004, E009, and E017 tables.
- Q: The anchor date is referenced throughout but its convention is never stated. Is it per run or per line? → A: One as-of date per forecast run, stored on the run row, so every line's grid shares day-zero and worklist comparisons need no shifting. The anchor becomes part of the hashable run manifest.
- Q: ExtractedValue is underspecified — what identifies the field, how is the value typed, and what does it attach to? → A: A field-name lookup table seeded by migration gives a closed, foreign-key-enforced vocabulary; the value is a canonical text column plus an optional typed numeric column; no target-record reference in E003, since E009 owns that join. Research confirmed the alternative — a PostgreSQL enum — would additionally break an Alembic revision that adds a term and backfills with it in the same transaction.
- Q: The percentile convention and the probability-sum tolerance are required to be recorded but never given. → A: Percentiles by nearest-rank one-based index arithmetic on the sorted draw array with no interpolation, keeping the read path an integer index as ADR-0004 intended; the probability-sum tolerance becomes a named schema constant set during planning and asserted in the OBJ5 tests.
- Q: Which migration tooling, given TR-004's tool-agnostic wording and TR-005's tool-specific single-head requirement? → A: Alembic, with the `0001`–`0099` block as a filename prefix convention and CI checking both single head and prefix range. The SQLAlchemy dependency lands in `/src/model` only.
- Q: IP-013 gates the vector column on a decision record that does not exist, without saying who authors it. → A: E003's plan opens with a task to author the embedding-model decision record through the ADR Author sub-agent, gating the chunk migration behind it, keeping the epic self-contained and honouring the governance rule that project-level decisions live in `specs/adrs/`.
- Q: How stale may the active forecast run be before a reader must treat it as unusable? → A: No maximum age is imposed by this epic (TR-080). The run's as-of anchor date is exposed so a reader computes the artifact's age itself; the threshold and its interface treatment belong to E010, which owns read-time risk. Reversal trigger: if a second consumer needs the same threshold, it becomes a seventh published schema constant rather than two copies. Chosen over adding a `max_forecast_age_days` constant now, which would fix a product threshold before the read surface that uses it exists.
- Q: Who asserts `extracted_value.confidence`, on what basis, and what identifies the agent that recorded a citation? → A: Confidence is a self-reported per-field score asserted by the extracting agent at the recorded extraction time, not a calibrated probability (TR-081); it is a continuous double-precision value on the closed interval 0–1 with both endpoints admissible and no coarser scale imposed (TR-054). Responsible-agent identity is carried at ingestion-run granularity by E006, not as a column on the value row (TR-082), so the storage boundary keeps `extracted_at` as its only per-row temporal fact. Reversal trigger: the first audit question that cannot be answered from the ingestion run adds `asserted_by` and `assertion_basis` columns as an additive forward migration. Chosen over adding those columns now, which would widen every extraction row for provenance E006 already records per run.
- Q: Where do reader-facing semantic definitions live for columns that carry no label, such as `line_posterior.draws` and `forecast_run.horizon_days`? → A: `data-model.md` is normative for column semantics and no migration may create an object absent from it (TR-083). Reversal trigger: if a consumer outside this repository reads the schema, definitions move into `COMMENT ON` statements emitted by the migrations and verified by a completeness test. Chosen over emitting a comment per object now, which pays for roughly 150 comments before any out-of-repository reader exists.
- Q: May an `extracted_value` or `extraction_failure` row be updated or deleted once written, and for how long is it kept? → A: Append-only — a correction is a remove-and-reload of the affected chunks in the order the restricting foreign keys permit, never an in-place edit of a citation, page, confidence, or outcome (TR-084) — and retained for the life of the database, since retention policy is out of scope and the dataset is regenerable (TR-085). Enforcement is by policy and by the restricting foreign keys, not by a grant or trigger; the schema carries zero triggers by design. Reversal trigger: a demonstration that a writer edited a stored citation in place moves enforcement to revoked `UPDATE` and `DELETE` privileges on the two tables.
- Q: TR-084/TR-085 made the provenance tables append-only by policy alone, which is weaker than every other integrity guarantee in this epic. Harden it? → A: Yes. A migration revokes `UPDATE` and `DELETE` on `extracted_value` and `extraction_failure` from the application role, making append-only a privilege fact rather than caller discipline (TR-084, TR-086, SC-028). The migration role keeps the privileges the prescribed remove-and-reload correction needs, so the correction path stays open without reopening in-place edit.
- Q: What recovers the migration-seeded reference data if the schema-constants row or a vocabulary row is deleted? → A: Re-applying the migration sequence against a rebuilt database, which is admissible because this epic stores no domain data and backup and restore are excluded from scope (TR-079). Loss of the constants row is detected by the constants-agreement check; loss of a referenced vocabulary row is prevented by its restricting foreign key. Reversal trigger: the first accidental deletion in a shared environment turns the seed into a separately invocable idempotent operation.
- Q: What links a `line_posterior` row back to the inputs it was computed from? → A: Provenance is carried at run granularity by the run's code revision, input data hash, and sampling seeds; there is no per-line lineage to extracted values or lifecycle events, so reproduction is by re-running the recorded run (TR-062). Reversal trigger: a published figure that cannot be resolved to its inputs from the run row alone adds a per-line input manifest.
- Q: The three constants AD-005 fixed during planning were never carried into a requirement. → A: TR-056 fixes the survival horizon at 365 days, the per-run draw count at 4000, and the probability-sum tolerance at 1e-9, each with a reversal trigger and production-scale alternative recorded in the data model's declared-constants scope-decision record, as Principle VII requires of a limitation.
- Q: Does a page citation identify a document version, given `document` carries no version column? → A: The manifest key identifies one fixed revision; a superseding revision is loaded as a new document row under a distinct key, so existing citations keep resolving to the revision they were extracted from (TR-057). A change to the key space itself is a forward migration updating the key in place and cascading to chunks, leaving citations untouched because they reference the chunk, not the document (TR-078).

## Compliance Check

**Instructions Check** — `project-instructions.md` **v1.2.0** · audited 2026-07-26 · **PASS** after remediating four CRITICAL findings (layer-dependent provenance, absent generator columns, containerized migration job, amendment performed on a feature branch) — see `analysis-report.md`. Prior record, superseded: v1.1.3 · 2026-07-25 · PASS (no CRITICAL or blocking violations)

| Rule | Verdict |
|------|---------|
| I. Traceable or It Does Not Ship | PASS |
| II. Uncertainty Is the Product | PASS |
| III. Precision Over Recall Where a Mistake Is Silent | PASS |
| V. The Model Extracts, Code Computes | PASS |
| VI / VII / VIII | N/A — no evaluation sets, published metrics, or baselines in scope |
| Technology Stack | PASS |
| Source Code Layout (ENFORCE_SRC_ROOT, four entries) | PASS |
| Testing & Quality Policy | PASS |
| Data Provenance | PASS — populates only migration-seeded reference data: the field vocabulary and the single schema-constants row; no domain data |
| Governance — registered documents win | PASS |
| Artifact conventions (sections, IDs, grammar) | PASS |

Findings raised and resolved in this spec: the chunk citation chain terminating at an unidentified document (TR-014, TR-041, TR-046, SC-018, IP-012); the apparent contradiction between adding a migration job and leaving job profiles unchanged (Scope Excluded, TR-007, TR-037, SC-003); embedding-model selection, now authored as an in-epic decision record rather than deferred (Scope Included, TR-050, IP-013, OBJ1 VC11); verification-test placement against the repository-root exception (TR-042, Technical Constraints, OBJ1 VC9); and validation coverage for the constraint-authoring requirements (OBJ2 VC6, OBJ3 VC6, OBJ5 VC7–VC8).

**Re-audited 2026-07-25 by `/sddp-analyze`** against the full 86-requirement set. Verdict stands at PASS for every principle above, with Principle VII reclassified from N/A to PASS. That pass raised one CRITICAL and five HIGH findings, all against `plan.md` and `tasks.md` rather than this spec's principle compliance, and all resolved — see `analysis-report.md`. Three were contradictions inside this spec: TR-065 against TR-067, TR-053 against Scope › Excluded, and TR-046 against TR-074; each is now reconciled in place.

The analysis also found that the checklist pass appended 33 requirements — TR-054 … TR-086 — without integrating them, recorded as A-007, A-008, A-011 and A-012. These were deferred through five phases and were finally examined against the delivered code after QC passed. **The examination changed the answer**, so the four are resolved individually below rather than as one "duplicate requirements" item.

- **A-007 — duplicate clusters. Closed as WILL-NOT-MERGE, on evidence.** The finding claimed roughly 13 clusters over 46 IDs. A re-examination against delivered code found 15 clusters over 51 IDs, of which **one** is a true duplicate: TR-053, now marked subordinate to TR-030 in place. The other 14 are a general requirement plus genuine per-case specialisations, or distinct obligations that merely read alike — and each specialisation is the sole traceability anchor for its own named constraint (`ck_line_posterior__draws_sorted` for TR-070, `ck_line_posterior__survival_length` for TR-072, `fk_line_posterior__run_shape` for TR-073, `fk_purchase_order_line__closing_event` for TR-067, the migration-role grant for TR-086). Merging them would leave those constraints tracing to nothing, which is a worse traceability state than the redundancy. The original deferral reason — that collapsing them reverses a decision to keep all 33 — was correct but incomplete; the stronger reason is that the merge destroys evidence.

**Re-confirmed on corrected grounds.** A first write-up of this decision rested on a second argument — that the project forbids renumbering — sourced to `tasks.md` line 171. That line is an aside about **task** IDs authored during implementation, not policy, and the blanket claim was wrong: the real constraint is `artifact-conventions/SKILL.md`'s prohibition on changing a **requirement** ID, which exists because those IDs are mapped to tasks, coverage reports, and compliance checks. Renumbering as such is permitted — a colliding ID is renumbered locally before it reaches the repository, as ADR-0011 → ADR-0012 → ADR-0013 was in this epic. The decision was put back to the user with that leg removed and **re-confirmed**: will-not-merge stands on the traceability-anchor argument alone. Recorded so a later pass does not reopen it assuming the premise was faulty.
- **A-008 — 24 requirements with no acceptance coverage. Split and partially closed.** 16 are *documentation* gaps: a test asserts the obligation but no success criterion or validation criterion cites it. Adding 16 criteria after an epic has been measured 28/28 buys nothing and invites the same retroactive-adjustment concern Principle VII raises, so those stay as recorded. **8 were real verification gaps** — no criterion and no test — and the three that named delivered obligations are now closed by test: TR-020's purchase-order-line column set, TR-056's seeded `365`/`4000` constants, and TR-078's `ON UPDATE CASCADE`. TR-062 and TR-080 are closed as verified deliberate absences. TR-064 remains unverifiable here by nature; it is E010's reader duty, disclosed as gap G-10.
- **A-011 — non-obligations phrased as MUST. Closed by reclassification.** 11 rather than the 9 first counted. Each now carries a trailing note naming what it actually is and where its enforceable half sits, if any; see the preamble to Technical Requirements. No ID and no obligation changed.
- **A-012 — lifecycle inversion. Accepted, with the mitigation named.** TR-056, TR-065, TR-076 and TR-083 make `data-model.md` — a Plan-phase artifact — normative over Specify-phase requirements, so in principle a Plan re-run can invalidate a spec requirement. This is a genuine inversion of the lifecycle order `AGENTS.md` calls strict, and it is accepted rather than corrected: the alternative is lifting thirteen tables of DDL detail into `spec.md`, which duplicates the normative source instead of removing the inversion. **The mitigation is already in practice** — `data-model.md` was amended four times during implementation (`fk_lifecycle_event__chain` to `MATCH SIMPLE`, two array-check NULL strengthenings, and the `0009` third table), and each amendment is labelled a correction carrying the PostgreSQL 16 evidence for why the declared form was wrong, adding no constraint name and no object. That is TR-083 working as designed. If this authority direction is to bind epics beyond E003 it needs an ADR, which must be authored on `main` — recorded as **AR-4** in `plan.md` § Amendment Requests, not applied from this branch.

The finding text for A-012 was not recoverable from the working tree — the superseding analysis pass renumbered to a B-series and overwrote the report in place, leaving four IDs with three descriptions between them. It was recovered from `git show 7138026:specs/00003-core-data-schema/analysis-report.md`. Worth noting as a process defect in its own right: an undefined item had been sitting in a `.qc-passed` marker.

Carried into Plan: E004 must honour rather than re-negotiate the `0100`–`0199` migration block reserved for it here, and TR-052 requires `specs/project-plan.md`'s Shared Data Entities rows for ResolvedEntity and the posterior artifacts to be corrected to the `E003 (schema), E00N (populated)` convention the Chunk row already uses.

## Stress-Test Findings

### Session 2026-07-25

Adversarial scan of the clarified spec. All eight resolved inline; none deferred, none left carrying a `[NEEDS CLARIFICATION]` marker.

STF-001: Cross-Requirement Contradiction (CRITICAL) — Affected: TR-034, TR-035, TR-036, SC-016, SC-017, IP-010 — TR-036 and SC-017 forbade creating an E009-owned table while OBJ6 creates ResolvedEntity, which the registered project plan credits to E009. Resolved: TR-036 restated against six explicitly named tables, and TR-052 added requiring the project-plan rows be corrected within this epic.

STF-002: Cross-Requirement Contradiction (HIGH) — Affected: TR-043, TR-044, TR-048, SC-019, SC-021 — The Compliance Check's "populates no table" verdict and the blanket no-population exclusion contradicted the migration-seeded vocabulary and the mandatory single constants row. Resolved: Data Provenance verdict changed to PASS with the two seeded exceptions named, and the Scope exclusion narrowed to domain tables.

STF-003: Cross-Requirement Contradiction (HIGH) — Affected: IP-007, TR-028, TR-031, SC-014 — IP-007 still named separate draw-array and survival-array tables and a single-transaction dual-write constraint that TR-031 deliberately eliminated. Resolved: IP-007 rewritten against the single per-line-run artifact row.

STF-004: Cross-Requirement Contradiction (HIGH) — Affected: TR-021, TR-039, TR-051, SC-010 — The closed-line rule had two mutually exclusive mandated mechanisms, and OBJ4 VC2's insert-time rejection could not hold under the deferrable foreign key. Resolved: the deferrable foreign key made primary throughout, the Technical Constraints wording corrected to "no immediate declarative constraint", and OBJ4 VC2 restated at the commit boundary so it holds under either mechanism.

STF-005: Boundary & Scale Stress (HIGH) — Affected: TR-045, TR-051 — Both were the only new requirements with no validation or success criterion, so an implementation using an untyped JSON value column with a direct purchase-order foreign key would have passed every test. Resolved: OBJ3 VC8, OBJ1 VC12, SC-023, and SC-024 added.

STF-006: Cross-Requirement Contradiction (HIGH) — Affected: TR-017, TR-046, SC-008 — The OBJ2 deliverable placed the composite `(document, page)` key on the document table, which has one row per manifest identifier and cannot expose it; TR-017 carries the page into the chunk reference. Resolved: the deliverable now specifies a `(chunk, page)` uniqueness constraint on the chunk table.

STF-007: Cross-Requirement Contradiction (HIGH) — Affected: TR-034, TR-045, SC-016, IP-010 — The Scope exclusion assigned resolved-entity membership to E009 while OBJ6's deliverable builds membership here. Resolved: the exclusion narrowed to a direct extracted-value-to-line foreign key, naming the OBJ6 membership table as the sanctioned join surface that E009 populates.

STF-008: Boundary & Scale Stress (MEDIUM) — Affected: TR-029, TR-030, SC-015 — The survival array had no length enforcement analogous to the draw array's, since the horizon lived only in the schema-constants table. Resolved: the horizon is now also recorded on the run row and carried into the same composite foreign key, with OBJ5 VC7 and SC-025 extended to cover it.
