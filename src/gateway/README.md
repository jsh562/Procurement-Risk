# gateway

The single traced path to the model provider. Every model call in this project
goes through `gateway.api.invoke` and through nothing else — which is what makes
"every model call is traced" a property of the code rather than of reviewer
diligence.

This file documents the four procedures that are **not** obvious from the code
and that cost something to get wrong: changing what the invocation record
carries, bumping the semantic-convention pin, correcting a price table, and
regenerating fixtures.

---

## Changing the read contract (TR-055, TR-069)

`llm_invocation`'s column set is a **closed** field list, not a lower bound
(TR-068). E013 reads it as a contract, so adding, removing, or renaming a column
is a change to somebody else's inputs.

The procedure, in order:

1. **Amend TR-012 first.** The field list is the requirement; the schema is its
   implementation. A column added to the migration without amending TR-012 will
   fail `tests/test_read_contract.py`, which compares the declared list against
   the migrated information schema in both directions.
2. **Get E013's agreement for a rename.** An addition E013 can ignore; a rename
   breaks whatever it was reading. TR-069 requires the agreement, and it is a
   real gate rather than a courtesy — E013 has no way to detect the change
   except by breaking.
3. **Author a new higher-numbered migration** in the `0100`–`0199` block. Never
   edit an applied one: `tests/test_migrations.py` verifies the chain applies
   from empty and re-runs as a no-op, and an edited applied file makes those two
   properties describe different things.
4. **Update all four in one change**: TR-012, `data-model.md` (the column detail
   *and* the named-object inventory), `gateway.models.InvocationRecord`, and
   `gateway.record.writer.COLUMNS` with its literal `INSERT`.
5. Run `pytest tests/test_read_contract.py tests/test_record_writer.py` — the
   first catches a mismatch against the schema, the second against the type.

**Adding a column and leaving the old one is forbidden** (TR-068). It would put
a column outside TR-012's list, which is the closure this whole arrangement
rests on. A rename is a rename.

---

## Bumping the semantic-convention pin (TR-070, TR-074)

The `gen_ai_`-prefixed columns take their spellings from a **pinned** release of
the OpenTelemetry generative-AI semantic conventions, currently **1.37.0**.
Those attributes carry no stability guarantee — that is the whole reason a pin
exists.

**Read the pinned document before writing anything.** This is not advice; it is
how the current pin was found to be wrong. TR-070 originally pinned `1.36.0`
"as the version carrying `gen_ai.provider.name`" — and the registry published at
`v1.36.0` defines `gen_ai.system`, not `gen_ai.provider.name`. Had the column
been named from the requirement's prose rather than from the release it named,
`llm_invocation` would carry a column whose spelling no pinned version defines.

The procedure:

1. **Verify the candidate release defines every attribute** the classification
   in `data-model.md` calls convention-named, plus `error.type` in the *general*
   registry of the same release. Check for a cached-input-tokens attribute while
   you are there: if one appears, `cache_read_input_tokens` moves into the
   convention-named set and takes that attribute's spelling (TR-072).
2. **Update the three recording sites together**, which must agree:
   `gateway.config.OTEL_GENAI_SEMCONV_VERSION`, the `COMMENT ON TABLE
   llm_invocation` mirror, and TR-070 itself. `tests/test_field_naming.py`
   asserts all three, including against the *live* comment.
3. **Update the classification and stability tables** in `data-model.md` for
   every field touched.
4. **A rename is a column rename in a new higher-numbered migration** — not an
   edit to `0102`. Editing an applied file would not migrate existing rows
   anyway.
5. **Take it through the read-contract procedure above.** A pin-driven rename is
   still a rename, and E013 still has to agree.

While no rows exist, step 4 is cheap. Once one does, it is a data migration.
That asymmetry is the reason to bump early or not at all.

---

## Correcting a price table (TR-055, TR-081)

Price tables are **append-only**. A version is added, never edited, never
deleted.

**A rate correction is a new version in a new migration.** It is not an edit to
`0103`, and the reason is mechanical rather than stylistic: the ledger skips an
applied revision before the file is read, so an edited seed is a silent no-op on
every database that already ran it — the correction would appear to land and
would not. `ON CONFLICT DO NOTHING` makes the file re-runnable; it does not make
an edit take effect.

The procedure:

1. **Author a new revision** seeding a new `price_table_version` with its own
   `snapshot_date` and `source_url`. Both are mandatory columns (TR-081): a
   seeded rate whose origin is unrecorded makes every derived cost
   unattributable one hop up, which Principle I forbids.
2. **Move the pin** — `GATEWAY_PRICE_TABLE_VERSION` — to the new version.
   Historical rows keep citing the old one, which is what keeps their stored
   costs recomputable. That is the point of recording the version on every row.
3. **Do not delete the old version.** Both foreign keys are `ON DELETE RESTRICT
   ON UPDATE RESTRICT` (TR-046), so the database will refuse — deliberately.

**A scheduled future rate does not need a new version.** One version may hold
several `effective_from` rows for one model; the seed already carries a pair for
exactly that reason. The within-version lookup selects the latest effective date
at or before the pricing timestamp, compared as UTC calendar dates (CD-1).

---

## Operating notes

**The spool.** When PostgreSQL is unreachable *after* a provider request has
been issued, the record is written to a local SQLite spool and the invocation
still fails closed (TR-041 does not soften TR-036). The spool drains at the
start of the next invocation that opens a connection — there is no timer and no
background thread, because this is a library with no runtime of its own. If no
further invocation happens, records stay durably spooled until one does.

Spool depth is logged on every spool write and every drain, which is how that
condition is visible rather than silent. A non-zero depth that is not falling
means no invocation has run since the outage ended.

**A retained spool row** is one the drain deliberately left behind: a
referential failure, or a payload written by a different gateway version. The
error line names the failing constraint and the invocation identifier. The drain
steps over it and continues — one unreconcilable record must not turn a recovery
mechanism into an outage (TR-054).

**Log events are a closed set of five** (TR-077): invocation completion, the
absent-cost warning, the spool write, the drain, and the reconcile failure. The
gateway logs nothing else, and adding an event is an amendment to that list.

**An absent cost is always warned about** (TR-058), naming the pinned version,
the resolved model, and the reason — because a pin that has fallen behind a rate
change otherwise has no symptom except a cost that is not there, which nobody
reads until they go looking for it.

---

## Regenerating fixtures (TR-027, OBJ6)

Fixtures are committed model responses. Replay resolves from them and reaches
no network, so a stale fixture is not a broken test — it is a **passing** test
describing a request nobody makes any more.

### When to regenerate

Regenerate when any of these changes, because each one changes the fixture key
and every existing fixture stops matching:

| Trigger | Why the key moves |
|---|---|
| A prompt template's resolved text | Its digest is a hashed input (TR-038) |
| The caller's output schema, **including a validator** | Same — the digest covers constraints, not just field names |
| Any declared request field: model, sampling parameters, provider | The hashed set is the request model's declared fields (TR-020) |
| Adding a request field | Adding a parameter *is* adding a field; the old key described a different call |

Also regenerate when the provider's behaviour has changed enough that a
recorded response no longer represents what it returns — a model deprecation,
or a response shape change. Nothing detects that automatically; it is a
judgement call, which is why it is written down rather than left to a check.

**A replay miss is the signal.** `FixtureMissError` names the derived key. It
means either the request changed or the fixture was never recorded, and those
are the same action.

### How to regenerate

Reaching the provider takes three separate things, none of which happens by
accident:

```bash
export GATEWAY_MODE=record                  # no default (TR-021)
export GATEWAY_ALLOW_PROVIDER_CALLS=1       # separate opt-in (TR-063)
uv run --directory src/gateway pytest tests/test_provider_smoke.py -q
```

The third thing is the credential, in the variable the provider SDK itself
resolves (TR-062). It is deliberately **not** written as an example assignment
here: `tests/checks/test_supply_chain.py` fails on any assignment to that
variable anywhere in the committed tree, placeholder value or not. That is the
right rule — a committed assignment is the shape of the mistake, and a detector
that made an exception for documentation would make the same exception for the
line someone pasted a real key into. Export it in your shell; never in a file.

Then review the diff before committing. What to look at:

1. **The sidecar.** Every fixture carries `recorded_on`, `gen_ai_response_model`,
   `gateway_revision`, and the token counts (TR-033). A response without one is
   an orphan and both `has()` and `load()` treat it as absent.
2. **The response body.** It is committed to the repository and read by everyone
   who clones it. Only corpus material E002 labels public-domain or synthetic
   may go across that boundary (TR-067).
3. **No credential material.** `test_fixture_credential_scan.py` runs the two
   detectors over the committed store, but read the diff anyway — the scan is a
   backstop, not a substitute for looking.
4. **The old fixture.** A regenerated key is a *new* file; the one it replaced
   is still there. Delete it in the same commit, or the store grows a copy for
   every prompt edit ever made.
5. **Both files, if the invocation repaired.** A repair is a second provider
   call, so it is a second fixture — keyed on the original request plus the
   instruction that provoked it. Commit both or replay misses the repair and
   the invocation fails where it previously succeeded. That keying is also why
   `repaired` is reachable on a replay row at all: without it, the outcome
   enumeration would only ever be exercised in `record` mode, which is the mode
   continuous integration never runs.

### What never happens

- **Continuous integration does not regenerate.** It never sets the opt-in, and
  `tests/checks/test_ci_provider_gate_absent.py` asserts that of every workflow.
- **Replay never falls back to the provider.** There is no code path from the
  fixture store to a socket, which is stronger than a flag defaulting to off.
- **`replay` mode refuses to run beside a credential** (TR-023). If regeneration
  left one exported, the offline suite will stop rather than quietly reach out.
  Unset it, or run the offline suite in a child environment without it.
