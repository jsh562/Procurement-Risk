---
adr_id: ADR-0013
status: superseded
date: 2026-07-25
tags: [layout, schema, migrations, data, governance]
supersedes: []
superseded_by: "ADR-0016"
related_artifacts: ["ADR-0002", "ADR-0003", "ADR-0010", "ADR-0012", "specs/sad.md", "specs/00002-core-data-schema/spec.md", "E003", "E004", "E005", "E006", "E007"]
deciders: ["Project owner", "Solution architect"]
---

# ADR-0013: Schema Ownership in the Modeling Entry with Constants Published Through the Database

## Status

Superseded by [ADR-0016](../adrs/0016-database-client-access-is-not-restricted-by-schema-ownership.md), on the database-client clause only. The schema-ownership decision below stands: `/src/model` owns the migration tooling, the DDL, and every schema asset, and is what the migration job image builds from; the shared constants are published through `schema_constants`; the reserved prefix blocks and the single-head check are unchanged. ADR-0016 replaces only the database-client half of the consequence clause "Exactly one entry — `/src/model` — declares the database client and migration tooling," holding that an entry may declare a Postgres driver for a purpose an accepted record already sanctions. Migration tooling, ORMs, and schema assets remain exclusive to `/src/model`.

Alembic migrations and all schema assets live in `/src/model`; shared constants are published in a single-row `schema_constants` table that `/src/api` reads over the connection rather than importing; migration numbers are claimed in reserved filename-prefix blocks layered over Alembic's revision identifiers.

## Context

There is one database (ADR-0002), so there is one schema, so exactly one entry under `/src` must own it. Deciding which is not a matter of taste, because three recorded constraints intersect at this point and only one arrangement satisfies all of them.

ADR-0010 forbids either Python boundary from depending on the other. That prohibition is the whole reason the gateway package exists: an edge from `/src/model` to `/src/api` or back would pull one resolution graph into the other and convert boundary independence from an actual property into a directional one. The source-layout rule permits exactly four entries under `/src`, and ADR-0010's own consequences record the gateway as deliberately minimal — provider client, schema validation, invocation recording, and explicitly no framework and no modeling stack.

Against those constraints sits a plain requirement: both Python boundaries need the schema's constants. `/src/model` needs the vector dimension to write chunk vectors, the survival-grid horizon and draw count to shape posterior arrays, and the anchor-date and percentile conventions to compute them. `/src/api` needs the same six values to read those artifacts back and compute risk at request time under ADR-0004. These are not two sets of constants that happen to overlap; they are one set, and if they are declared twice they will eventually disagree.

The obvious move — a shared constants module — is the one the layout forbids. Wherever it is put, it is either an import across the boundary ADR-0010 closed, or a new entry that breaks the four-entry rule, or an expansion of a package whose minimality is a recorded consequence of an accepted decision.

The decision is needed now because E003 authors the first migration and every later schema-touching epic inherits whatever this establishes: E004 adds tables in the same wave, and E005, E006, and E007 all write against the tables E003 declares. A layout decided implicitly by whichever epic migrated first would be discovered, not chosen.

## Decision Drivers

- ADR-0010 must stand unamended: neither Python boundary depends on the other, and the four-entry rule under `/src` holds
- Exactly one entry owns the schema, the migration tooling, and the database client — one database implies one owner
- Shared constants are declared once, in one place, and cannot silently diverge between the two consumers
- The gateway package stays minimal, as ADR-0010's consequences record
- Parallel schema work in the same delivery wave cannot collide on migration numbers or resolve to an ambiguous apply order
- The request-serving image does not acquire the modeling stack — the constraint the original layout existed to protect

## Considered Options

### Option A: `/src/model` owns migrations; constants published through a `schema_constants` table

Alembic configuration, migration scripts, and all schema assets live in `/src/model`, which alone declares the database client and migration tooling and is the entry the migration job image builds from. The six shared constants — vector dimension, survival-grid horizon, anchor-date convention, percentile convention, per-run draw count, and probability-sum tolerance — are written by a migration into a single-row `schema_constants` table and read by `/src/api` over the connection it already holds. Migration numbers are claimed in reserved filename-prefix blocks (E003 `0001`–`0099`, E004 `0100`–`0199`) layered over Alembic's own revision identifiers, with CI asserting a single head revision and every prefix inside its owner's block.

- **Pros**:
  - Adds no `/src` entry and no boundary-to-boundary dependency, so ADR-0010 stands unamended and the four-entry rule holds verbatim
  - The database is already the shared medium under ADR-0002 — publishing shared values through it introduces no new coupling channel, only a new row in an existing one
  - Constants are declared once and read by both consumers, so they cannot diverge by editing one copy
  - `/src/model` is the natural owner: it is the offline entry that already writes every domain table under ADR-0003, and it is what the migration job image builds from
  - `/src/api` never acquires the migration tooling, the ORM, or the modeling stack — its dependency list is unchanged by schema ownership
  - Filename-prefix blocks let E003 and E004 add migrations in the same wave without coordinating on numbers, and the single-head check makes an ambiguous order a build failure rather than a runtime surprise
- **Cons**:
  - The vector dimension exists in two places — a literal in the migration DDL and a row in the published table — and nothing in the language prevents them from disagreeing
  - `/src/api` pays a startup read against the database before it can serve, and cannot resolve the constants statically
  - Constants become runtime values rather than importable literals, so a typo in a constant name fails at read time instead of at import time
  - Two numbering schemes coexist: Alembic's revision identifiers and the reserved filename prefixes layered over them

### Option B: Migrations and a constants module in `/src/gateway`

The gateway package, already depended on by both Python boundaries, additionally carries the schema, the migration tooling, and an importable constants module.

- **Pros**:
  - Both boundaries import the constants directly, so there is exactly one declaration and no startup read
  - No new `/src` entry; the four-entry rule holds
  - Constant names resolve at import time, so a typo fails immediately
- **Cons**:
  - Requires amending ADR-0010, whose consequences explicitly record the gateway as carrying only the provider client, validation, and tracing, with no framework and no modeling stack — and a material change to a recorded consequence requires supersession, not amendment
  - Puts the migration tooling and database client into a package the request-serving boundary depends on, so the serving image acquires schema machinery it never executes
  - Dissolves the gateway's reason for existing: it was created as the minimal shared surface for one specific problem, and turning it into a general shared-code bucket removes the argument that kept it from becoming one
  - A schema change now forces a version bump of the package both boundaries depend on, coupling their release cadence to the migration cadence

### Option C: `/src/api` owns migrations; `/src/model` reads over the connection

The request-serving boundary declares the schema and migration tooling; the offline modeling entry reads shared values over the connection.

- **Pros**:
  - Also avoids a new `/src` entry and a boundary-to-boundary dependency
  - Symmetric with Option A in mechanism — the constants table works identically regardless of which side owns the DDL
- **Cons**:
  - Puts the migration tooling, the ORM, and the schema assets into the image whose leanness is the constraint the layout exists to protect
  - Inverts ownership against ADR-0003: the offline modeling entry writes essentially every domain table, so the boundary that owns the DDL would be the one that barely writes it
  - The migration job image would have to be built from the request-serving entry, dragging a web framework into a job that runs no server
  - Schema evolution becomes coupled to the release cadence of the request path

### Option D: A fifth `/src` entry, or a shared path-dependency package for schema

A dedicated schema entry under `/src`, or a shared package on a path dependency that both Python boundaries resolve.

- **Pros**:
  - Cleanest conceptual separation — schema is genuinely a distinct concern from serving and from modeling
  - Constants are importable, statically resolvable, and declared once
  - Neither boundary owns the other's schema
- **Cons**:
  - Contradicts the four-entry rule directly, and ADR-0010 records that rule as a consequence of an accepted decision — so this option requires a new decision record superseding ADR-0010 before any of it can be built
  - A fourth Python dependency manifest and lockfile, for a package whose only content is DDL and six numbers
  - A path-dependency package is a `/src` entry by another name; routing it outside `/src` to evade the count is evasion, not compliance
  - The cost is paid immediately and in full, while the benefit — static resolution of six constants — is small

## Decision Outcome

Chosen option: **`/src/model` owns migrations; constants published through a `schema_constants` table** — Alembic configuration and all schema assets live in `/src/model`, which alone declares the database client and migration tooling and is the entry the migration job image builds from. The six shared constants are published in a single-row `schema_constants` table that `/src/api` reads over the connection rather than importing. Migration numbers are claimed in reserved filename-prefix blocks — `0001`–`0099` for E003, `0100`–`0199` for E004 — layered over Alembic's revision identifiers, with CI asserting a single head and in-range prefixes.

It is the only option that satisfies every recorded constraint without amending or superseding anything. The others each pay in governance. Option B requires amending ADR-0010's minimality consequence, and because that is a material change to a recorded consequence, the authoring rules require supersession rather than amendment — a superseded layout record is a large price for avoiding one startup read. Option D contradicts the four-entry rule outright and needs a decision record superseding ADR-0010 before a line of it can be written; a path-dependency package is the same entry wearing a different path. Option C is mechanically identical to the chosen option but assigns ownership backwards: it puts the migration tooling and schema assets into the image whose leanness the layout exists to protect, and hands the DDL to the boundary that writes almost none of it while ADR-0003 has the modeling entry writing nearly all of it.

The key insight is that ADR-0002 already made the database the shared medium between the two boundaries. Publishing six shared values through it adds a row to a channel that exists, rather than opening a code-level channel the layout closed. The constants are not being smuggled across a boundary; they are being read from the store both boundaries were already built to read.

The accepted cost is that the vector dimension exists twice — once as a literal in the migration's DDL and once as a published row. This is not sloppiness, it is unavoidable: the migration that creates the chunk column cannot read its dimension from a table that the same migration set is still creating. The DDL literal is therefore the source of truth and the table row is the published copy, and because nothing in the language keeps them aligned, a test asserting they agree is mandatory rather than advisable.

## Consequences

### Positive

- **ADR-0010 stands unamended and the four-entry rule holds.** No new `/src` entry, no boundary-to-boundary dependency, no expansion of the gateway package beyond the provider client, validation, and tracing its own record describes.
- Exactly one entry — `/src/model` — declares the database client and migration tooling, owns every schema asset, and is what the migration job image builds from.
- The six shared values are declared once and read by both consumers, so they cannot diverge through an edit to one of two copies.
- `/src/api` acquires no migration tooling, no ORM, and no modeling stack from schema ownership; the request-serving image constraint is untouched.
- Reserved filename-prefix blocks let E003 and E004 add migrations in the same wave without coordinating on numbers, and the single-head check turns an ambiguous apply order into a build failure.

### Negative

- **The vector dimension exists twice — as a literal in the chunk migration's DDL and as a row in `schema_constants` — so a test asserting the two agree is mandatory, not optional.** E003 TR-048 carries this obligation. Without it, `/src/api` would compute against a dimension the column does not have, and the failure would surface as a confusing query error far from its cause.
- `/src/api` pays one startup read against the database before it can serve, and cannot resolve the constants statically.
- Constants are runtime values, not importable literals: a wrong constant name fails when the row is read, not when the module is imported.
- Two numbering schemes coexist — Alembic's revision identifiers and the reserved filename prefixes layered over them — and both must be kept consistent, which is why CI checks the prefix range as well as the head count.
- Every later schema-touching epic (E004, E005, E006, E007) inherits this arrangement, so a future need to reverse it grows more expensive with each epic that lands.

### Neutral

- **The migration cannot read its own dimension from the constants table** — the table is created by the same migration set — which is precisely why the DDL literal is the source and the table row is the published copy, rather than the other way round. The direction of truth is fixed by ordering, not by preference.
- The published constants are the vector dimension (`EMBEDDING_DIM`, fixed at 384 by ADR-0012), the survival-grid horizon in days, the anchor-date convention, the percentile convention, the per-run draw count, and the probability-sum tolerance.
- `schema_constants` holds exactly one row and is constrained so a second cannot be inserted; a table that could hold two rows would reintroduce the divergence it exists to prevent.
- E003 owns migration prefixes `0001`–`0099` and E004 owns `0100`–`0199`; later epics claim further blocks at their start rather than renegotiating these.
- The migration sequence is forward-only and must apply cleanly from an empty database, verified against the Compose `db` service rather than asserted.

## Links

- [ADR-0002](0002-postgres-as-the-single-datastore.md) — establishes the single datastore that makes the database the existing shared medium this decision publishes through
- [ADR-0003](0003-offline-modeling-package-instead-of-a-model-service.md) — establishes `/src/model` as the offline entry that writes the domain tables, making it the natural schema owner
- [ADR-0010](0010-source-layout-with-a-shared-gateway-package.md) — the four-entry rule, the no-cross-boundary-dependency rule, and the gateway minimality consequence, all of which this decision leaves unamended
- [ADR-0012](0012-embedding-model-and-vector-dimension.md) — fixes `EMBEDDING_DIM` at 384, the value published through `schema_constants` and duplicated as the DDL literal
- [specs/sad.md](../sad.md) — Integration Strategy and Data Management sections
- [specs/00002-core-data-schema/spec.md](../00002-core-data-schema/spec.md) — E003; TR-004, TR-005, TR-008, TR-043, TR-047, TR-048, IP-014, SC-019
- E004, E005, E006, E007 — later schema-touching epics that inherit this arrangement
