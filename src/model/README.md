# `/src/model` — the offline modeling entry

The modeling boundary: corpus generation, the schema and its migrations, the
procurement dataset, and E006's document ingestion. Every job this entry owns is
a **console entry point** invoked through the entry's own environment
(`{SAD:ADR-0011}`), never a container job and never a request path.

```
DATABASE_URL=postgresql://procurement@<host>:<port>/procurement \
GATEWAY_PRICE_TABLE_VERSION=2026-07-26-published \
  uv run --directory src/model ingest --mode replay
```

**Both variables are required, and the second is the surprising one.** Every
invocation is priced against a pinned price-table version (TR-048) — a replayed
one from the fixture's recorded token counts — so `replay` needs the pin exactly
as `record` does. The value is a `price_table_version.version_id`; revision
`0103` seeds `2026-07-26-published`. The entry refuses an unset pin before it
enumerates anything, because the gateway's own refusal arrives on the first
invocation, which this job reaches only after committing every document
extraction does not reach: 26 documents and 6,391 chunks written before a
missing variable is reported, and reported as `provider_unreachable`.

The exit codes are **0** (the run resolved), **2** (refused, nothing written)
and **3** (aborted part-way: the documents before the abort are committed and
active, the one in flight rolled back, the rest never begun). A run at the
committed corpus with no extraction fixtures (T081) exits 3 by design — see
`ingest/cli.py`'s `main`.

## Operator procedures

Three procedures below are **not reachable from the ingestion job**, and that is
the point of writing them down rather than coding them. The job connects as the
application role `procurement_app`, which holds:

- `SELECT, INSERT` on the six tables E006 adds beyond the run record,
- `SELECT, INSERT, UPDATE` on `ingestion_run` — `UPDATE` for the finish
  timestamp and the two run-failure columns and nothing else,
- **no `DELETE` anywhere**, and **no data-definition privilege at all**.

Migration `0009` revoked `UPDATE` and `DELETE` on `extracted_value`,
`extracted_value_contributing_chunk` and `extraction_failure`; revision `0304`
extends the same posture to everything this epic adds. Neither is weakened by
these procedures — each is executed under the **schema-owning role**, which is a
different actor rather than a temporarily larger job.

Each procedure states its role, its statements in order, and what it leaves
behind if it is interrupted. `specs/00006-document-ingestion-and-extraction/data-model.md`
§Operator Procedures is the normative source; this file is the runbook.

---

### 1. Whole-document correction: remove and reload (FR-041)

**Role**: schema-owning. **Reachable from the job**: no.

**Nothing is ever corrected in place.** Not an extracted value, not a
contributing-chunk record, not a failure record. A wrong value is not edited to
the right one — the whole affected document is removed and re-ingested, because
an in-place edit leaves a row whose provenance says it came from a run that
produced something else, and nothing downstream can tell.

The correction **is** procedure 3. There is no separate purge that precedes it:
under `{SAD:ADR-0020}` a promotion removes the prior generation as it writes the
successor, in one transaction, so "correct this document" and "re-ingest this
document" are one operation.

```
# 1. Make the document's inputs differ, so FR-043 does not skip it.
#    A correction that changes nothing about the input tuple is a correction the
#    run will decline to perform: the recorded digest still matches and the
#    document is skipped as unchanged. Fix the cause first — the corpus file,
#    the chunker version, the prompt, or the encoder revision — and the tuple
#    moves by itself.

# 2. Re-ingest that document under the schema-owning role.
DATABASE_URL=postgresql://<schema owner>@<host>:<port>/procurement \
  uv run --directory src/model ingest --mode replay --promote
```

**What the run does, per document, in one transaction**: marks the resident
generation superseded, captures its chunk, value and failure identifier sets from
the three run-output associations, deletes leaf-up, inserts the new generation
row, and writes the successor's rows. An interruption at any point rolls the
document back to its **prior generation, intact and active** — which is the
correct state to fail into, and needs no deletion privilege to reach.

**Zero rows are updated in place at any point, by anyone.** The only permitted
updates in this epic's whole object set are four: `ingestion_run.finished_at`,
its two run-failure columns, and the `active`-to-`superseded` mark, which is
performed only under the schema-owning role and only as the first step of the
removal that follows it.

---

### 2. Vector index drop and rebuild around a full-corpus load (FR-064)

**Role**: schema-owning. **Reachable from the job**: no — `DROP INDEX` requires
ownership of `chunk`, and `procurement_app` owns nothing.

`ix_chunk__embedding_hnsw` is **E003's** object, declared in migration `0004`.
pgvector is explicit that indexes belong *after* the initial data load: building
the graph one row-insert at a time costs far more than one bulk build.

| Step | Statement | Note |
|------|-----------|------|
| 1 | `DROP INDEX ix_chunk__embedding_hnsw;` | Schema-owning role. The job cannot do this and is not meant to. |
| 2 | `uv run --directory src/model ingest --mode replay` | Ordinary per-document transactions, unchanged. |
| 3 | `SET maintenance_work_mem = '2GB'; SET max_parallel_maintenance_workers = 4; SET max_parallel_workers = 8;` | Build speed is dominated by whether the graph fits in `maintenance_work_mem`. Session-scoped, and sized to the host. |
| 4 | `CREATE INDEX ix_chunk__embedding_hnsw ON chunk USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);` | **Verbatim** — same name, same operator class, same `m` and `ef_construction`. |

**Step 4 is reproduced verbatim and that is not pedantry.** Any deviation makes
the live schema disagree with `specs/00003-core-data-schema/data-model.md`, which
is normative over `chunk`. Not `CREATE INDEX CONCURRENTLY`: slower, and it buys
availability an offline job does not need.

#### The window this opens

**Between step 1 and step 4 every similarity query falls back to a sequential
scan.** Correctness is unaffected — a sequential scan returns the *exact* nearest
neighbours where the HNSW index returns approximate ones — and latency is not.
E003's scale note records exact scan as viable at this corpus's order of
magnitude (~15,000 chunks), so the window costs offline latency rather than a
wrong answer. It is still a window: do not run it against a database something
else is serving from.

#### If the rebuild aborts, or the load does

**The index stays absent until this procedure is re-run.** Nothing in the
database restores it. No migration recreates it on an already-migrated database —
the drop and rebuild is deliberately *not* a revision, because a revision would
run on every fresh database, where there is nothing to load and nothing to gain.

Recovery, in order:

1. **Check first, do not assume.** `SELECT indexname FROM pg_indexes WHERE
   tablename = 'chunk';` — the absence of `ix_chunk__embedding_hnsw` is the whole
   diagnosis, and it is invisible from the application, which keeps returning
   correct answers slowly.
2. **If the ingestion run aborted**, read `ingestion_run.run_failure_kind` and
   `run_failure_detail` for the run, fix the cause, and re-run. Documents
   committed before the abort are durable with their generations active; the run
   resumes as a fresh run and skips them if their input tuples are unchanged.
3. **Re-issue step 4 verbatim.** It is idempotent in effect: either the index
   exists and `CREATE INDEX` fails with `42P07`, which is the answer, or it does
   not and the build runs.
4. **A partially built index does not exist.** PostgreSQL rolls back a failed
   `CREATE INDEX` (this is not `CONCURRENTLY`, which is the form that can leave
   an `INVALID` index behind), so there is nothing to drop before retrying.

**A retrieval consumer starting against an index-less database gets
correct-but-slow answers with no signal at all.** That residual is carried as
**G-7** and its closure — a startup check reading `pg_indexes` before serving —
belongs to the consumer, not to this epic.

---

### 3. Promotion of a replacing generation (FR-055, `{SAD:ADR-0020}`)

**Role**: schema-owning, **for the whole length of the run**. **Reachable from
the job**: no.

**A first-ingest run stays unattended.** This is the line that matters and it is
stated first because it is the one most easily lost: a run in which every
document is a first ingest or a skip performs **no removal at all**, needs no
`DELETE`, and runs under the application role with no operator present. There is
no predecessor to remove, so promotion is simply not a gate for it. The `ingest`
entry defaults `--promote` off for exactly that reason.

A run that replaces **any** existing generation is a different operation. It runs
under the schema-owning role from start to finish — not per document, because the
removal has to be in the same transaction as the write that replaces it, and a
run cannot change roles mid-transaction.

```
DATABASE_URL=postgresql://<schema owner>@<host>:<port>/procurement \
  uv run --directory src/model ingest --mode replay --promote
```

**Retention bound: zero.** No superseded generation is retained. Promotion
removes the prior generation's rows for that document and then writes the new
one, so exactly one generation's rows exist per document at any time. That is not
a policy a purge job is trusted to honour — it is what the delivered schema
permits: `uq_chunk__document_ordinal UNIQUE (document_id, ordinal)` is scoped to
the **document**, so a second resident generation's ordinal 0 is rejected on
write. Retention was not expensive under this schema; it was impossible.

**The removal, in order, inside document *d*'s transaction.** `ON DELETE
RESTRICT` cannot be deferred and no setting rescues a wrong order:

| # | What | Why here |
|---|------|----------|
| 0a | Mark the resident generation `superseded` | The mark *names* the generation every later step acts on. |
| 0b | **Capture** the chunk, value and failure identifier sets from the three run-output associations | The associations are the only thing that says which of E003's rows belong to this generation, and step 0d deletes them. Identify first or the generation becomes unnameable mid-delete. |
| 0c | Delete `extracted_value_line_item`, `extracted_value_parse_signal` | Deepest leaves; keyed on the generation directly. |
| 0d | Delete the three run-output associations | Once their own children are gone. |
| 0e | Delete `extraction_failure`, then `extracted_value_contributing_chunk`, then `extracted_value`, by the sets from 0b | E003's rows. |
| 0f | Delete `chunk`, by the chunk-id set from 0b | The step the whole record exists for: until these are gone, ordinal 0 is taken. |
| 0g | Delete `ingestion_run_document` | Releases the single-active index for *d*. |
| — | **Stop.** `ingestion_run` is never removed. | A replaced run's identity, input tuple configuration, timings and model identifiers are what make the surviving history readable. |

**Removal precedes the write and is not merely convenient there.** Deleting after
writing would put both generations' ordinal 0 in `chunk` for the length of a
statement, which is the exact collision this design exists to avoid.

**Reverting a bad promotion is a re-run, not a flag flip.** The predecessor's
rows are gone and no status change recovers them. Recovery is re-running
ingestion for that document at the previous chunker version — possible, because
ingestion is deterministic given its input tuple (FR-043), so the earlier
generation is *reproducible* rather than merely lost. The cost is a full
ingestion pass instead of a transaction, and it is disclosed as such.

**Two chunker generations cannot be compared in one database.** A side-by-side
ablation needs two databases, or two sequential runs with the figures captured
between them.

---

## Extraction fixtures, and when they must be re-recorded (FR-045)

`replay` mode resolves **every** model response from the committed fixture store
and reaches no network. A miss does not fall back to the provider: it raises, and
the run records FR-056's `fixture_missing` on `ingestion_run.run_failure_kind`.
That is deliberate — a fallback is how an offline suite becomes quietly online.

**The resolution key is E004's, not a second one.** It digests the *resolved
request*: the provider model, the prompt text, the output schema's digest, and
the prompt template's digest. E006 declares no key of its own, which is what
makes the trigger below exact rather than approximate.

### The trigger

**Re-record whenever the prompt text or an output-schema constraint changes.**
Both move the key, so both resolve to a miss, and the miss is the signal. Named
concretely, in the two places that produce it:

| Change | Digest that moves | Effect in `replay` |
|---|---|---|
| Any edit to `_INSTRUCTIONS` or the field catalogue in `model/llm/prompts.py` | `prompt_template_digest()` **and** every resolved prompt | every key misses |
| Retiring or adding a term in `TRANSMITTAL_FIELD_SUBSET` | the rendered catalogue, so every resolved prompt | every key misses |
| Any constraint change in `ChunkExtraction` or `ExtractedField` in `model/llm/schemas.py` | `output_schema_digest()` | every key misses |
| A different provider model | the request's own model field | every key misses |

The first three also move FR-043's per-document input tuple, so a re-record is
always accompanied by a full re-ingestion rather than a partial one.

### The procedure

Re-recording is a `record`-mode run, and `record` mode reaches the provider. It
takes **two independent decisions**, which is the design rather than an
inconvenience: selecting the mode is a configuration choice, and setting the
opt-in is a deliberate one.

```
GATEWAY_MODE=record \
GATEWAY_ALLOW_PROVIDER_CALLS=1 \
GATEWAY_PRICE_TABLE_VERSION=2026-07-26-published \
DATABASE_URL=postgresql://procurement@<host>:<port>/procurement \
  uv run --directory src/model ingest --mode record
```

The provider credential must be exported in that environment as well. **Its
variable is deliberately not written here**, and not because it is a secret —
the *name* is public and `gateway/config.py` states it. A committed line of the
form `<CREDENTIAL_VARIABLE>=<anything>` is exactly what
`tests/checks/test_supply_chain.py` scans the source tree for, and that check is
right to fail on one: a runbook example and a leaked key are the same string.
Read the name off `gateway.config.CREDENTIAL_ENV_VAR` and export it in your
shell, out of the repository.

**Commit the regenerated fixtures with the change that invalidated them**, in one
commit. A commit that moves the prompt without its fixtures leaves the default
branch in a state where `replay` cannot complete, and the failure appears to be
about ingestion rather than about the prompt edit that caused it.

Continuous integration sets neither `GATEWAY_MODE=record` nor the opt-in, and
`tests/checks/test_ci_provider_gate_absent.py` asserts that absence.

### Where the fixtures live

`src/gateway/fixtures/`, laid out as `sha256/<first two hex>/<digest>.response.json`
plus a `.provenance.json` sidecar. That root is `gateway.orchestrator.
DEFAULT_FIXTURE_ROOT` and it is **not** configurable — see
`src/model/fixtures/README.md`, which records why this epic's fixtures are
committed to the gateway's store rather than beside this entry, and what would
have to change for them to move.
