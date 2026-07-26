---
adr_id: ADR-0016
status: accepted
date: 2026-07-26
tags: [layout, schema, data, boundaries, governance]
supersedes: ["ADR-0013"]
superseded_by: ""
related_artifacts: ["specs/adrs/0013-schema-ownership-in-the-modeling-entry.md", "ADR-0002", "ADR-0004", "ADR-0010", "ADR-0015", "specs/sad.md", "E003", "E004", "E005", "E006", "E007"]
deciders: ["Project owner", "Solution architect"]
---

# ADR-0016: Database-Client Access Is Not Restricted by Schema Ownership

## Status

Accepted. Supersedes [ADR-0013](../adrs/0013-schema-ownership-in-the-modeling-entry.md) on one clause only — the database-client clause in its consequences.

ADR-0013's decision is otherwise carried forward verbatim: `/src/model` owns the Alembic configuration, the migration scripts, and every schema asset, and is the entry the migration job image builds from; the six shared constants are published in a single-row `schema_constants` table that `/src/api` reads over the connection rather than importing; migration numbers are claimed in reserved filename-prefix blocks layered over Alembic's revision identifiers, with CI asserting a single head and in-range prefixes. Only the clause "Exactly one entry — `/src/model` — declares the database client and migration tooling" is replaced, and only in its database-client half: migration tooling remains exclusive to `/src/model`, while a Postgres driver may be held by any entry for a purpose an accepted record already sanctions.

## Context

ADR-0013 fixed schema ownership in `/src/model`, and that decision is correct. Nothing about it is in question here. What is in question is a single sentence in its Consequences: "Exactly one entry — `/src/model` — declares the database client and migration tooling, owns every schema asset, and is what the migration job image builds from." Three of those four clauses restate the decision the record argued for. The database-client clause does not — it is broader than the record it sits in, and it contradicts the record on the face of it.

The contradiction is visible without leaving ADR-0013. Its decision drivers already pair the client with the schema and the tooling as one bundle, but its Option A pros state that "`/src/api` never acquires the migration tooling, the ORM, or the modeling stack" — a list that conspicuously omits the driver. Its Option A cons state that "`/src/api` pays a startup read against the database before it can serve," which is a read `/src/api` performs itself and therefore requires a driver. Its Decision Outcome describes the constants as "read from the store both boundaries were already built to read." A boundary built to read the store holds a client. The record's own reasoning assumes throughout the thing its consequence sentence forbids.

ADR-0004 settles the point independently: the request-serving boundary computes risk in SQL at request time against materialized posterior draws. That is not possible without a driver in `/src/api`. So the consequence clause, read literally, contradicts an accepted record that predates it as well as the record that contains it.

E004 hit this directly. The gateway must write invocation records — an obligation ADR-0010's consequences already list among the gateway's sanctioned contents, alongside the provider client and schema validation — and a write requires a driver. E004 has been proceeding on a narrow reading recorded as an interpretation in its spec. That is not stable footing. An interpretation in a feature artifact does not bind a reviewer who reads the decision record, and E005, E006, and E007 all inherit ADR-0013's arrangement and would each arrive at the same sentence and re-argue it.

The decision is needed now, and it is needed as a superseding record rather than an edit. Governance is explicit that a material change to a recorded consequence must supersede rather than amend — ADR-0013 itself invoked that rule against its own Option B, on the grounds that expanding ADR-0010's minimality consequence would have required supersession. The same rule applies to ADR-0013's consequences.

## Decision Drivers

- Settling an ambiguity before three more epics inherit it, rather than after
- Leaving ADR-0013's actual decision — schema ownership, constants publication, and migration numbering — untouched
- Changing the smallest surface that removes the contradiction
- Keeping the request-serving image free of the modeling stack, which is the constraint the layout exists to protect

## Considered Options

### Option A: Supersede ADR-0013 on the database-client clause only

Schema ownership means ownership of the schema, the DDL, the migration tooling, and the migration job image — not exclusive possession of a Postgres driver. Any entry may hold a driver for a purpose already sanctioned by an accepted record: `/src/api` holds one to read `schema_constants` at startup and to compute risk in SQL at request time under ADR-0004, and `/src/gateway` holds one solely to write invocation records under ADR-0010. No entry other than `/src/model` may hold migration tooling, an ORM, or any schema asset.

- **Pros**:
  - Removes the contradiction without touching the arrangement E003 is about to build — the schema, the constants table, and the prefix blocks are all unchanged
  - States the constraint that actually matters positively and in a checkable form: no migration tooling, no ORM, and no schema asset outside `/src/model`
  - E004's gateway persistence stops being an interpretation carried in a feature artifact and becomes a sanctioned consequence of a decision record
  - E005, E006, and E007 inherit a decision rather than an ambiguity, so the sentence is argued once instead of four times
- **Cons**:
  - ADR-0013's status flips to superseded while its substance is unchanged, so every existing citation now points at a superseded record
  - A reader must follow one hop from ADR-0013 to this record to get the corrected clause

### Option B: Leave ADR-0013 as written and read it literally

The gateway may not hold a driver. Invocation recording moves out of the gateway to a sink injected by each caller, which performs the write with its own client.

- **Pros**:
  - No new record, and the consequence sentence is honoured exactly as written
- **Cons**:
  - Reverses a clarified E004 decision and collapses ADR-0015's premise — a local spool for invocation records whose database write fails has nothing to buffer if the component holding the spool has no writer
  - Turns 100% invocation recording into a wiring property that any caller can defeat by injecting a null sink, rather than a structural property of the single invocation boundary
  - Contradicts ADR-0010, whose consequences list invocation recording as gateway scope, so honouring one record's consequence sentence breaks another's
  - Resolves an internal contradiction in ADR-0013 by choosing the clause that disagrees with the rest of ADR-0013, and with ADR-0004

### Option C: Carry the narrow reading as a disclosed interpretation in each consuming epic's spec

Each epic that needs a driver outside `/src/model` records an interpretation of ADR-0013 in its own spec, as E004 has already done.

- **Pros**:
  - Costs nothing now — E004 continues on the footing it already has
- **Cons**:
  - The same ambiguity is re-argued in E005, E006, and E007, and nothing guarantees the four readings agree
  - An interpretation recorded in a feature artifact does not bind a reviewer reading the decision record, so the ambiguity survives every epic that discloses it
  - Defers the governance cost without reducing it: the record still has to be corrected eventually, by which point more epics cite the uncorrected clause

## Decision Outcome

Chosen option: **Supersede ADR-0013 on the database-client clause only** — schema ownership is ownership of the schema, the DDL, the migration tooling, and the migration job image, not exclusive possession of a database driver. Any entry may hold a Postgres driver for a purpose an accepted record already sanctions; `/src/api` holds one for the startup constants read and for SQL-side risk computation under ADR-0004, and `/src/gateway` holds one solely to write invocation records under ADR-0010. Migration tooling, ORMs, and schema assets remain exclusive to `/src/model`.

Option B is the only option that takes the clause at its word, and doing so costs more than the clause is worth. It would move invocation recording out of the single boundary that ADR-0007 exists to make traceable, leave ADR-0015's spool buffering nothing, and contradict ADR-0010's own scope list — all to preserve a sentence that ADR-0013's drivers, options, and outcome already contradict internally. Option C is not a decision at all; it distributes the argument across four epic specs and binds nobody, since a reviewer reads the record, not the interpretation. Option A changes one clause of one record and leaves everything the record actually decided in place.

The distinction that makes this narrow is between a driver and a stack. The constraint the source layout exists to protect is that the request-serving image does not install the modeling stack — the compiler toolchain, the linear-algebra libraries, the sampler, the ORM, and the migration tooling. A Postgres driver is none of those. `/src/api` has held one since ADR-0004 made SQL-side computation the request path, and the serving-image constraint has never been in tension with it. Restating the constraint as "no migration tooling, no ORM, no schema asset outside `/src/model`" is both narrower and more checkable than restricting the driver, because it names the things whose presence in a dependency manifest can actually be asserted.

## Consequences

### Positive

- **ADR-0013's arrangement is unchanged and E003 is unaffected in substance.** The schema stays in `/src/model`, the constants stay in `schema_constants`, the prefix blocks stay as allocated, and every obligation E003 carries — including the DDL-literal-versus-published-row test — is untouched.
- The real constraint is stated in a form that can be asserted rather than inferred: no migration tooling, no ORM, and no schema asset outside `/src/model`. A dependency manifest can be checked against that; it could not be checked against "exactly one entry declares the database client" without failing `/src/api`.
- E004's gateway driver is sanctioned by a decision record rather than tolerated by an interpretation, so invocation recording rests on the same footing as the rest of the gateway's scope.
- E005, E006, and E007 inherit a settled question instead of a sentence each would have had to read against ADR-0004 and ADR-0010 on its own.

### Negative

- A superseded record whose substance is unchanged is a confusing artifact for a reader who does not follow the forward link — ADR-0013 now reads as replaced when only one clause of it was.
- Every existing citation of ADR-0013 — in `specs/sad.md`, in E003's artifacts, and in later epic traceability — now resolves to a superseded record, exactly as already happened with ADR-0003 after ADR-0011.

### Neutral

- **The serving image constraint is untouched.** A driver is not the modeling stack, and the assertion that the request-serving image carries no modeling-stack packages is unaffected by this record.
- ADR-0010's gateway minimality consequence is likewise untouched. A driver held solely to write invocation records is inside the scope that record already grants; this decision does not widen the gateway's contents, it removes an obstacle to contents ADR-0010 already listed.
- The permission is purpose-bound, not general. An entry holds a driver for a purpose an accepted record sanctions — the constants read and SQL-side risk under ADR-0004 for `/src/api`, invocation recording under ADR-0010 for `/src/gateway` — and a new purpose in a new entry needs its own sanctioning record, not this one.

## Links

- [ADR-0013](../adrs/0013-schema-ownership-in-the-modeling-entry.md) — superseded on the database-client clause only; its schema ownership, constants publication, and migration numbering decisions carry forward unchanged
- [ADR-0002](../adrs/0002-postgres-as-the-single-datastore.md) — the single datastore that every entry holding a driver connects to
- [ADR-0004](../adrs/0004-materialized-posterior-draws-with-sql-side-risk-computation.md) — has the request-serving boundary computing risk in SQL at request time, which is impossible without a driver in `/src/api`
- [ADR-0010](../adrs/0010-source-layout-with-a-shared-gateway-package.md) — lists invocation recording among the gateway's sanctioned contents; its minimality consequence stands unamended
- [ADR-0015](../adrs/0015-local-spool-for-invocation-records-whose-database-write-fails.md) — the spool that presupposes a gateway-side writer, and whose premise Option B would have collapsed
- [specs/sad.md](../sad.md) — ADR catalog; requires the new row and the corrected ADR-0013 status row
- E003 — unaffected in substance; the arrangement it builds is carried forward verbatim
- E004 — raised this as an interpretation in its spec; the gateway's invocation-record driver is sanctioned here instead
- E005, E006, E007 — later epics that inherit ADR-0013's arrangement and would otherwise have re-litigated the same clause
