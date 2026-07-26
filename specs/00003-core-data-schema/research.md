# Research: Core Data Schema

> Feature: E003 Core Data Schema | Date: 2026-07-25 | Purpose: Inform schema design, enforcement mechanisms, migration tooling, and test harness for the single-datastore PostgreSQL 16 schema.

## Forward-only migrations

- **Decision**: Alembic, with the reserved `0001`–`0099` block carried as a migration filename prefix and CI asserting exactly one head plus in-range prefixes.
- **Rationale**: Alembic's partial-GUID revision ids make parallel work surface as multiple heads reconciled by an explicit merge, which is the E003/E004 Wave 2 case.
- **Rejected**: Hand-numbered SQL files force cross-workstream coordination and produce rename conflicts; a bespoke runner means building and testing merge semantics yourself.
- **Pitfalls**: Treating filename timestamps as ordering — the parent-revision link is the ordering; leaving divergent heads unmerged; writing a plausible downgrade body nobody tests instead of raising.
- **Sources**: https://alembic.sqlalchemy.org/en/latest/tutorial.html, https://alembic.sqlalchemy.org/en/latest/branches.html

## Invariants as constraints

- **Decision**: Enforce mandatory provenance with `NOT NULL` paired with a named `CHECK`; use stored generated columns for derived provenance fields.
- **Rationale**: A violation becomes impossible to persist rather than detectable after the fact, which is the measurement method the quality-attribute table records.
- **Rejected**: Application-side validation, which leaves the guarantee dependent on every future caller rather than on the storage boundary.
- **Pitfalls**: A `CHECK` is satisfied when its expression is null, so a range check alone admits nulls; cross-row `CHECK` expressions appear to work but permit inconsistent states and break dump/restore; unnamed constraints cannot be referenced by later migrations.
- **Sources**: https://www.postgresql.org/docs/16/ddl-constraints.html, https://www.postgresql.org/docs/16/ddl-generated-columns.html

## Cross-table invariants

- **Decision**: Carry the redundant column into a composite foreign key, or collapse two one-to-one tables into one row; reserve a deferrable foreign key for the single genuinely circular rule and a constraint trigger only as fallback.
- **Rationale**: Three of the four cross-table rules become declarative once the parent exposes a redundant uniqueness constraint, leaving only the closed-line/terminal-event cycle needing deferral.
- **Rejected**: Constraint triggers throughout — they fire per row, their condition clause evaluates immediately even when the body is deferred, and a data-only restore with triggers disabled loads straight past them.
- **Pitfalls**: `CHECK` and `NOT NULL` are the only constraints PostgreSQL will not defer, so any invariant spanning two statements needs a deferrable foreign key or a trigger; partial-match semantics on a nullable composite key silently skip the check.
- **Sources**: https://www.postgresql.org/docs/16/sql-createtable.html, https://www.postgresql.org/docs/16/sql-createtrigger.html

## pgvector at small scale

- **Decision**: Declare the dimension explicitly on the column; keep the dense vector in the same row as the search vector; build the serving index per ADR-0005 while keeping the exact path available on the same table.
- **Rationale**: A high-dimension vector exceeds the out-of-line storage threshold and costs nothing on queries that do not select it, so co-location is free.
- **Rejected**: An inverted-file index, which needs representative training data and degrades at low row counts; omitting the graph index entirely, which ADR-0005 forecloses because the recall delta is a published ablation arm.
- **Pitfalls**: pgvector's HNSW index caps at 2000 dimensions, so a larger model forecloses the serving index; selecting all columns in keyword-only queries forces every embedding to be read back.
- **Sources**: https://github.com/pgvector/pgvector, https://www.postgresql.org/docs/16/storage-toast.html

## Full-text weighting

- **Decision**: One stored generated search-vector column concatenating a weighted conversion per field against an explicitly named configuration, indexed with GIN.
- **Rationale**: The two-argument conversion form is immutable and therefore admissible in a generated column, and storing it avoids re-running the conversion during index verification.
- **Rejected**: An expression index, which forces every call site to repeat the configuration name; the one-argument conversion form, which depends on a session setting and is rejected outright.
- **Pitfalls**: Omitting a null-coalesce on any field nulls the whole concatenation; the weight labels carry no numbers of their own, so relevance tuning is a query-time change rather than a migration.
- **Sources**: https://www.postgresql.org/docs/16/textsearch-tables.html, https://www.postgresql.org/docs/16/textsearch-controls.html

## Posterior draw arrays

- **Decision**: Store draws as a double-precision array in a dedicated table keyed by run and line, with length enforced by composite foreign key and sortedness by an immutable numeric helper in a `CHECK`; hash over a defined byte serialization.
- **Rationale**: Sortedness is what turns a percentile into a subscript, so enforcing it converts a silent wrong answer into a write failure.
- **Rejected**: A row-per-draw table, which forces a sort on every read and hashes as a set rather than a record; declared array sizes, which PostgreSQL ignores entirely.
- **Pitfalls**: Hashing via text rendering implicitly pins settings-dependent float formatting; an inverted index over draw arrays is the misdesign the array documentation calls out.
- **Sources**: https://www.postgresql.org/docs/16/arrays.html, https://www.postgresql.org/docs/16/storage-toast.html

## Versioned artifact contract

- **Decision**: An integer schema version on the run row, distinct from the run identifier, with the active run selected through a partial unique index on the active flag.
- **Rationale**: The partial unique index makes "exactly one active run" a database fact and turns publication into a single atomic flip with instant rollback.
- **Rejected**: Latest-timestamp selection, which exposes partially written runs, is sensitive to clock skew, and offers no rollback.
- **Pitfalls**: Overloading the run identifier as the version; a nullable active flag with no uniqueness enforcement; tolerating an unknown version instead of failing closed.
- **Sources**: https://www.postgresql.org/docs/16/indexes-partial.html, https://mlflow.org/docs/latest/ml/model-registry/

## Reproducibility metadata

- **Decision**: Record code revision with a dirty-worktree flag, an input hash, the random root seed, library versions, chain and draw counts, and a digest per emitted array.
- **Rationale**: Verification is an actual job — re-run, recompute the input hash and artifact digest, compare — which only works if every input is recorded.
- **Rejected**: Recording library versions without the code revision, which leaves the largest source of variance unpinned.
- **Pitfalls**: Small human-chosen seeds and seed-plus-chain-index arithmetic produce correlated, overlapping streams; claiming bit-identical reproducibility across library or linear-algebra versions rather than scoping the claim to a pinned environment.
- **Sources**: https://numpy.org/doc/stable/reference/random/parallel.html, https://python.arviz.org/en/stable/schema/schema.html

## Closed vocabularies

- **Decision**: A lookup table seeded by migration and referenced by foreign key, not a PostgreSQL enum.
- **Rationale**: It is the only option that is a join surface, grows by insert under a forward-only chain, and lets a term be retired.
- **Rejected**: An enum — values can never be removed, ordering is fixed at creation, and comparison needs an explicit cast; free text with a format check, which enforces shape rather than membership.
- **Pitfalls**: An enum value added by altering the type is unusable until the transaction commits, so a migration that adds a term and backfills with it in the same revision fails at runtime.
- **Sources**: https://www.postgresql.org/docs/16/datatype-enum.html, https://www.postgresql.org/docs/16/sql-altertype.html

## Sharing constants across boundaries

- **Decision**: Publish shared constants in a single-row configuration table read over the connection; the migration DDL literal remains the source and a test asserts the two agree.
- **Rationale**: It keeps the four-entry rule intact and leaves ADR-0010 unamended, which a shared fifth package would not.
- **Rejected**: A fourth path-dependency package — cleaner imports, but it adds an entry the source-layout rule forbids; duplicating constants in both packages behind a comment.
- **Pitfalls**: The table cannot serve the migration that needs the dimension inside its own DDL, so treating it as the single source is wrong — it is the published copy, and drift between the two is the failure mode the agreement test exists to catch.
- **Sources**: https://docs.astral.sh/uv/concepts/projects/workspaces/, https://docs.astral.sh/uv/concepts/projects/dependencies/

## Integration-test harness

- **Decision**: Reuse the Compose `db` service through a `DATABASE_URL` env var, with the same digest-pinned image as a GitHub Actions service container in CI; per-test isolation by outer transaction with savepoint rollback.
- **Rationale**: One image reference and one digest, no second container lifecycle, and tests run against exactly the image the application uses.
- **Rejected**: `testcontainers-python`, which duplicates the image pin in Python and drifts from Compose; `pytest-postgresql`'s process fixture, which needs local binaries that will not carry pgvector.
- **Pitfalls**: A savepoint-rollback fixture never reaches a real commit, so deferred-constraint tests pass vacuously; `CREATE EXTENSION` is per-database and must run in the template; the host port differs between local and CI, so it must never be hardcoded.
- **Sources**: https://github.com/dbfixtures/pytest-postgresql, https://docs.github.com/en/actions/use-cases-and-examples/using-containerized-services/creating-postgresql-service-containers

## Testing migrations

- **Decision**: `pytest-alembic` with `test_single_head_revision` and `test_upgrade` enabled, plus `alembic check` for schema-shape drift; model idempotence as a second `upgrade head` asserted to be a no-op.
- **Rationale**: `alembic check` runs the autogenerate pipeline without writing files and exits non-zero on divergence, which beats hand-written column assertions that rot.
- **Rejected**: The up-down-consistency and downgrade built-ins, which are meaningless under a forward-only policy and would force downgrades this project will not maintain.
- **Pitfalls**: Migration tests need a scratch database, not the shared rollback-isolated session database; `alembic check` reports benign type and server-default noise unless comparison options are configured deliberately.
- **Sources**: https://pytest-alembic.readthedocs.io/en/latest/api.html, https://alembic.sqlalchemy.org/en/latest/autogenerate.html

## Asserting constraint rejection

- **Decision**: Catch the specific psycopg error subclass and assert on the diagnostic's constraint name and SQLSTATE, never on message text.
- **Rationale**: psycopg exposes a distinct class per SQLSTATE and the diagnostic carries the exact server-side constraint name, which is a stable contract where messages are locale- and version-dependent.
- **Rejected**: Catching the generic integrity error alone, which passes when the wrong constraint fires; matching on error text.
- **Pitfalls**: A deferred constraint is not checked until commit, so the offending insert succeeds and an assertion around it fails — wrap the commit instead, or set constraints immediate to force the violation at a precise point mid-transaction.
- **Sources**: https://www.psycopg.org/psycopg3/docs/api/errors.html, https://www.postgresql.org/docs/16/sql-set-constraints.html

## Security scanning

- **Decision**: No separate security tier. Enable Ruff's `S` ruleset inside the existing lint category and treat dependency scanning as repo-wide CI hygiene.
- **Rationale**: The epic's surface is DDL and migrations — no request handling, deserialization, subprocess, or untrusted input — and the `S` rules already cover the two real risks: credentials in a connection string and SQL built by concatenation.
- **Rejected**: A third QC category, which would violate the two-category policy for near-zero marginal detection.
- **Pitfalls**: `S101` flags every assert, so test paths need a per-file ignore or the lint gate turns red immediately.
- **Sources**: https://docs.astral.sh/ruff/rules/, https://astral.sh/blog/uv-audit

## Data-quality characteristics as requirement axes

- **Decision**: Use ISO/IEC 25012's fifteen characteristics as a coverage grid, each rephrased as "does the specification *state* it", with 25024 supplying the measurement column.
- **Rationale**: A schema specification is a data-quality requirement document, and 25012 is the only standardised grid for data quality as distinct from software quality.
- **Rejected**: Ad-hoc dimension lists, which overlap and carry no shared definition.
- **Pitfalls**: Schema specs reliably state completeness and consistency but omit currentness (how stale the active run may be), credibility (who asserted a confidence value), precision (the declared scale of that number), understandability (per-column semantics), and recoverability — the five worth explicit checklist items here.
- **Sources**: https://iso25000.com/index.php/en/iso-25000-standards/iso-25012, https://www.omgwiki.org/dido/doku.php?id=dido:public:ra:xapend:xapend.b_stds:tech:iso:square_data_model

## Requirement well-formedness

- **Decision**: Judge each requirement against ISO/IEC/IEEE 29148's nine individual characteristics and the set against its five, keeping per-statement defects separate from set-level gaps.
- **Rationale**: Verifiability has a concrete test — name a verification method and a measurable pass criterion, and if you cannot write the failing case the requirement is unverifiable.
- **Rejected**: Ambiguity-only review, which misses set-level contradiction and untraceable orphans.
- **Pitfalls**: The commonest incompleteness in a data-integrity requirement is omitting the outcome on violation — reject, default, or quarantine; the commonest non-singularity is bundling several obligations into one sentence, which makes none of them independently verifiable.
- **Sources**: https://www.modernrequirements.com/blogs/iso-29148-explained/, https://www.iso.org/standard/72089.html

## Provenance requirement completeness

- **Decision**: Require five things to be pinned down per W3C PROV: stable identity for source and agent, granularity of attribution, degenerate cases, immutability and retention, and event ordering.
- **Rationale**: PROV-CONSTRAINTS defines validity — uniqueness, ordering, impossibility — which is the difference between a provenance record that is merely present and one that is auditable.
- **Rejected**: A timestamp-plus-source-string column, which carries no identity or granularity semantics and cannot be audited.
- **Pitfalls**: A mandatory page citation that never says which document *version* the page belongs to; no stated rule for a value spanning several pages; provenance-of-provenance left unspecified, so nobody can say who recorded the citation or when.
- **Sources**: https://www.w3.org/TR/prov-dm/, https://www.w3.org/TR/prov-constraints/

## Summary

| Topic | Decision | Rationale |
|-------|----------|-----------|
| Forward-only migrations | Alembic; block as filename prefix; CI asserts single head | Merge semantics for the parallel E003/E004 case |
| Invariants as constraints | `NOT NULL` paired with named `CHECK` | Violation impossible, not merely detectable |
| Cross-table invariants | Composite FKs and collapsed tables; one deferrable FK | Declarative beats triggers wherever expressible |
| pgvector at small scale | Explicit dimension, co-located with search vector | Out-of-line storage makes co-location free |
| Full-text weighting | Stored generated column, named configuration, GIN | Immutable form is required in a generated column |
| Posterior draw arrays | Array per line-run; length by composite FK, sortedness by check | Sortedness is what makes a percentile a subscript |
| Versioned artifact contract | Integer schema version; active run by partial unique index | Publication becomes an atomic flip with rollback |
| Reproducibility metadata | Code revision, input hash, seeds, versions, digests | Verification is a re-run job, not a claim |
| Closed vocabularies | Seeded lookup table with foreign key | Only option that is a join surface and can retire terms |
| Sharing constants | Single-row table; DDL literal is source, table is copy | Keeps the four-entry rule and ADR-0010 intact |
| Integration-test harness | Compose `db` service; CI service container; savepoint rollback | One image, one digest, one source of truth |
| Testing migrations | `pytest-alembic` two built-ins plus `alembic check` | Catches drift without hand-written schema assertions |
| Asserting constraint rejection | Match constraint name and SQLSTATE, not text | Names are contract; messages are not |
| Security scanning | Ruff `S` rules inside lint; no third tier | Matches the two-category policy and the actual surface |
| Data-quality characteristics | ISO/IEC 25012 grid as "is it stated" questions | Only standardised grid for data quality as such |
| Requirement well-formedness | ISO/IEC/IEEE 29148, per-statement and per-set | Verifiability has a concrete failing-case test |
| Provenance completeness | W3C PROV: identity, granularity, degenerate cases, immutability, ordering | Validity constraints separate auditable provenance from present provenance |

## Sources Index

| URL | Topic | Fetched |
|-----|-------|---------|
| https://alembic.sqlalchemy.org/en/latest/tutorial.html | Forward-only migrations | 2026-07-25 |
| https://alembic.sqlalchemy.org/en/latest/branches.html | Forward-only migrations | 2026-07-25 |
| https://www.postgresql.org/docs/16/ddl-constraints.html | Invariants as constraints | 2026-07-25 |
| https://www.postgresql.org/docs/16/ddl-generated-columns.html | Invariants as constraints | 2026-07-25 |
| https://www.postgresql.org/docs/16/sql-createtable.html | Cross-table invariants | 2026-07-25 |
| https://www.postgresql.org/docs/16/sql-createtrigger.html | Cross-table invariants | 2026-07-25 |
| https://github.com/pgvector/pgvector | pgvector at small scale | 2026-07-25 |
| https://www.postgresql.org/docs/16/storage-toast.html | pgvector; draw arrays | 2026-07-25 |
| https://www.postgresql.org/docs/16/textsearch-tables.html | Full-text weighting | 2026-07-25 |
| https://www.postgresql.org/docs/16/textsearch-controls.html | Full-text weighting | 2026-07-25 |
| https://www.postgresql.org/docs/16/arrays.html | Posterior draw arrays | 2026-07-25 |
| https://www.postgresql.org/docs/16/indexes-partial.html | Versioned artifact contract | 2026-07-25 |
| https://mlflow.org/docs/latest/ml/model-registry/ | Versioned artifact contract | 2026-07-25 |
| https://numpy.org/doc/stable/reference/random/parallel.html | Reproducibility metadata | 2026-07-25 |
| https://python.arviz.org/en/stable/schema/schema.html | Reproducibility metadata | 2026-07-25 |
| https://www.postgresql.org/docs/16/datatype-enum.html | Closed vocabularies | 2026-07-25 |
| https://www.postgresql.org/docs/16/sql-altertype.html | Closed vocabularies | 2026-07-25 |
| https://docs.astral.sh/uv/concepts/projects/workspaces/ | Sharing constants | 2026-07-25 |
| https://docs.astral.sh/uv/concepts/projects/dependencies/ | Sharing constants | 2026-07-25 |
| https://github.com/dbfixtures/pytest-postgresql | Integration-test harness | 2026-07-25 |
| https://docs.github.com/en/actions/use-cases-and-examples/using-containerized-services/creating-postgresql-service-containers | Integration-test harness | 2026-07-25 |
| https://pytest-alembic.readthedocs.io/en/latest/api.html | Testing migrations | 2026-07-25 |
| https://alembic.sqlalchemy.org/en/latest/autogenerate.html | Testing migrations | 2026-07-25 |
| https://www.psycopg.org/psycopg3/docs/api/errors.html | Asserting constraint rejection | 2026-07-25 |
| https://www.postgresql.org/docs/16/sql-set-constraints.html | Asserting constraint rejection | 2026-07-25 |
| https://docs.astral.sh/ruff/rules/ | Security scanning | 2026-07-25 |
| https://astral.sh/blog/uv-audit | Security scanning | 2026-07-25 |
| https://iso25000.com/index.php/en/iso-25000-standards/iso-25012 | Data-quality characteristics | 2026-07-25 |
| https://www.omgwiki.org/dido/doku.php?id=dido:public:ra:xapend:xapend.b_stds:tech:iso:square_data_model | Data-quality characteristics | 2026-07-25 |
| https://www.modernrequirements.com/blogs/iso-29148-explained/ | Requirement well-formedness | 2026-07-25 |
| https://www.iso.org/standard/72089.html | Requirement well-formedness | 2026-07-25 |
| https://www.w3.org/TR/prov-dm/ | Provenance completeness | 2026-07-25 |
| https://www.w3.org/TR/prov-constraints/ | Provenance completeness | 2026-07-25 |
