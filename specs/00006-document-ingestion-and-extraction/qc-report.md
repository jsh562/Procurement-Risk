# QC Report — E006 Document Ingestion and Extraction

**Run 3 · 2026-07-28 · verdict: PASS with two blocked tasks disclosed**
Governing document: `project-instructions.md` v1.2.5.
Measured on the branch `00006-document-ingestion-and-extraction`, with `main`
already merged in — so this describes what a merge would produce rather than
what the branch produced in isolation.

**How this verdict was reached, because the first version of this line was
wrong.** This file was authored during the iteration-2 *fix* commit `ccd9c23`
and already carried "verdict: PASS" before iteration 3 had run. That is a
simulated pass state, which the Continuous Execution Policy forbids in the same
breath as simulated test results, and iteration 3 caught it (F-D3). Every
figure in the file was subsequently reproduced by that audit statement for
statement, so nothing here was fabricated — but the verdict was issued by the
implementer for an audit that had not happened, and a correct answer arrived at
in the wrong order is still the wrong order. The PASS now recorded is
iteration 3's, run at `ccd9c23` against all five tiers, and the findings it
raised are in §Run 3 below.

**This epic did not pass QC first time, and this report does not present it as
though it did.** Two prior iterations produced nineteen findings between them.
Both are recorded in full below, with what was fixed and what remains, because a
report that opened at run 3 and showed only green numbers would be the same
class of defect the iterations kept finding: a component that looks finished
because nothing exercised the part that is not.

**Why this file appears only now.** Iterations 1 and 2 wrote no `qc-report.md`.
All five prior features have one; this workspace had none after two iterations,
which was itself iteration 2's finding 4. Iteration 1's findings are therefore
reconstructed from two durable sources — `analysis-report.md`'s compliance
re-run section, which records A-23/A-24/A-25 verbatim, and the fix commit
`9bfbedc`, whose message enumerates the rest. Where the reconstruction is
inference rather than record, it says so.

---

## Required categories (profile `standard`)

### linting — PASS

Static analysis and code quality, run for real, no cache:

| Check | Sites | Result |
|---|---|---|
| `ruff check` | gateway, api, model, **repository root** | clean at all four |
| `ruff format --check` | gateway (43 files), api (4), model (196), **root (403)** | clean at all four |
| `mypy --strict` | gateway (`src`) | no issues, 18 source files |
| `lint-imports` | gateway 4, api 1, model 3 | 8 contracts kept, 0 broken |
| `uv lock --check` | gateway, api, model | all consistent |
| `npm run lint` / `prettier --check` / `tsc --noEmit` | web | exit 0 / clean / clean |

**The root sites are listed separately on purpose, both of them.** `verify.yml`
ran `ruff check .` at the root and did **not** run `ruff format --check .`
there — iteration 2's finding 3. Root formatting covers 403 files against the
entries' 243, and everything under `/tests` is in the difference — the 22
modules of `tests/checks` and the 41 import-linter negative fixtures under
`tests/fixtures` — so every check in this repository's cross-entry harness was
lint-enforced and format-unenforced. Fixed, and closed structurally:
`tests/checks/test_lint_and_format_cover_the_same_tiers.py` asserts the set of
tiers ruff lints equals the set it format-checks, so the *class* of omission
fails a build instead of waiting for a reader.

`npm run lint` exits 0 with one warning — an unused `readFileSync` import in
`src/web/__tests__/boundary.test.ts:1`, which arrived with E001 and is outside
this epic. It is recorded rather than fixed: CI is unaffected, and editing
another epic's file to silence a warning that gates nothing is scope drift.

### coverage — PASS

| Gate | Threshold | Measured | Exit |
|---|---|---|---|
| Combined | 80 | **92%** (10,631 statements, 605 missed, 2,880 branches) | 0 |
| `*/model/corpus/*` | 80 | **93%** | 0 |
| `*/model/ingest/*` | 80 | **90%** | 0 |
| `*/model/llm/*` | 80 | **95%** | 0 |
| `*/model/compute/*` | 80 | **92%** | 0 |

Combined from three data files, as CI does: `.coverage.model`,
`.coverage.gateway`, `.coverage.checks`.

The three per-package floors beyond `model.corpus` are E006's own addition, and
the reason is arithmetic rather than style: a single combined figure lets four
already-covered packages carry a newly added one across the threshold with none
of its own lines exercised. Notable modules inside `ingest`: `publish.py` 94%,
`quality.py` 100%, `report.py` 93%.

**Deleting dead exports moved these numbers and they were re-measured, not
assumed.** `ingest` was 90% before the iteration-2 deletions and is 90% after;
`llm` 95% before and after; `compute` 92% before and after.

---

## Tests

| Suite | Result |
|---|---|
| Model (`src/model`) | **2,410 passed**, 0 skipped, 0 failed — 22m 19s |
| Root cross-entry (`tests/checks`) | **252 passed** |
| Gateway (`src/gateway`) | **405 passed, 5 skipped** |
| Web (`src/web`) | 3 passed |

Model was 2,413 at the close of iteration 2 and is 2,410 now: four tests were
deleted with the dead code they were the only caller of, and one was added. The
arithmetic is stated because a suite that shrinks is exactly the shape a
silently-deleted assertion has, and the two are worth telling apart.

The gateway's 5 skips are E004's provider-reaching smoke check, skipped because
`GATEWAY_ALLOW_PROVIDER_CALLS` is unset. That is the opt-in holding, not a gap.

**The gateway tier is named here because it was the finding.** Iteration 1
found it red and unrun: `src/gateway/tests/test_migrations.py` asserted the
Alembic head equals `0103`, which stopped being true the moment this epic
chained `0300`–`0304` onto it, and the implementation report had claimed the
epic green on four tiers out of five. Four of five tiers green is a statement
about four tiers.

Environment for a reader reproducing this: `DATABASE_URL` must carry the
password from `docker-compose.yml` or roughly 520 schema tests skip rather than
fail; `REQUIRE_DB=1` turns that skip into a refusal to start; `UV_NATIVE_TLS=1`
is required on a host running TLS-inspecting antivirus, or six tests in
`tests/checks/test_gateway_no_provider_env.py` error on certificate
verification; and the gateway suite must be invoked as `python -m pytest`.

---

## The replay pipeline, run for real

`ingest --mode replay` against a database migrated `0001` → `0304` from empty:

```
ingest: mode=replay promote=False documents=51 REAL=26 SYNTHETIC=25
        extraction_attempted=25 excluded=26
ingest: run_id=ab1d2be0-d752-40ae-8a35-593cad1b96df
        trace_id=78292f71c7dd4a6388e656d19fae42d7
ingest: dispositions ingested=26 skipped_unchanged=0 rolled_back=1
        not_reached=24 enumerated=51
ingest: written chunks=6391 values=0 failures=0 invocations=0
ingest: report not emitted: 5 of 21 items have no data because the run aborted
        at fixture_missing (prj-001-t0001-r0). … Missing: item 6 …, item 7 …,
        item 10 …, item 12 …, item 13 …
```

stderr, and exit code **3**:

```
ingest: aborted — fixture_missing: document in flight prj-001-t0001-r0:
  no committed fixture for resolution key sha256:adb7198195…
```

Every enumerated document carries exactly one disposition and the four sum to
the enumeration (FR-073): 26 + 0 + 1 + 24 = 51. The abort is the designed
behaviour rather than a failure of it: zero fixtures are committed (T081,
blocked), so the first synthetic transmittal misses and FR-045 / FR-056 require
a named run-level failure rather than a fallback to the provider. `verify.yml`'s
`reproduce` job asserts exactly this ledger against an oracle derived from the
committed corpus manifests rather than from the run's own enumeration.

**The refusal to publish is the part worth reading.** 16 of FR-071's 21 items
were built from the 6,391 chunks this run wrote; the other 5 are named
individually, each with the requirement that obliges it and the reason its
population is empty — "an empty population fails rather than passes (FR-068)".
Before iteration 1 this path did not exist and `cli.main` ended at four `print`
statements, so the same run would have reported nothing at all.

**One incidental confirmation.** The fixture store the miss names resolves to
`src/model/.venv/Lib/fixtures` — evidence for the `DEFAULT_FIXTURE_ROOT` finding
recorded under §Not measured, observed in this run rather than reasoned about.

---

## Requirement coverage

**73 of 74 functional requirements are cited** in code or tests — extracted by
differencing every `FR-NNN` the spec declares against every one appearing under
`src/model`, `tests/checks` and `src/gateway/tests`.

The one uncited is **FR-051**, which claims decision-record numbers ADR-0018
through ADR-0020. It is discharged by artifacts rather than by code:
`specs/adrs/0018-*.md`, `0019-*.md`, `0020-*.md` and their `sad.md` catalog
rows exist on `main`. See finding I2-1 below for why "on `main`" is the
load-bearing part.

Success criteria: 58. Tasks: 103, of which 101 complete.

Citation is not verification and this report does not claim it is. It
establishes that no requirement was silently dropped.

---

## QC iteration 1 — FAILED, nine findings

Governing document at the time: `project-instructions.md` v1.2.5, freshly
amended. All nine were fixed in `9bfbedc`.

Three are recorded verbatim in `analysis-report.md` §Compliance re-run. The
other six are reconstructed from `9bfbedc`'s commit message; they had no
identifiers assigned at the time, and the `I1-n` labels below are this report's,
introduced so later text can refer to them.

| ID | Sev | Finding | Disposition |
|---|---|---|---|
| A-25 | HIGH | **A CI tier was red and had never been run.** `test_migrations.py` pinned E004's `0103` as the Alembic head; `0300`–`0304` moved it. The implementation report claimed the epic green having run four tiers of five | **Fixed.** The assertion restated against what TR-018 actually says — E004's four revisions present in the applied chain, contiguous, and exactly what that chain carries inside `0100`–`0199` — with three negative controls and one positive control |
| I1-1 | HIGH | **A ~4,900-line publish layer had no production caller.** `report.py`, `baseline.py`, `reference.py` and `compute/metrics.py` were reachable only from tests. `build_report` had no caller, `REPORT_PATH` was declared and never written, `results_manifest` had zero call sites, and `cli.main` ended at four `print` statements. Twenty-two requirements' publish obligations were unmet | **Fixed** (T098, T099). `publish.py` assembles all twenty-one items from the run's real data and writes both artifacts or neither. On the fixture-blocked run it builds 16 of 21 and refuses **by name**, listing each missing item with its obliging requirement |
| A-23 | **CRITICAL** | v1.2.5's Temporary Files rule unenforced at the root tier, at two layers: the root manifest pinned no `--basetemp` while hosting the only pytest code in the repository that builds a virtual environment, and the `verify` job set no `TMPDIR`/`TEMP`/`TMP` while `reproduce` set all three. Live, not theoretical — a `no-provider-env0` directory sat in `%LOCALAPPDATA%\Temp` | **Fixed.** Root `--basetemp` pinned; the three variables set on `verify` with a `mkdir -p "$TMPDIR"` ahead of the first tool; `test_scratch_stays_in_the_checkout.py` added to assert both halves over *all* manifests and *all* jobs |
| A-24 | **CRITICAL** | `/tools/*.py` was Python source outside `/src` under `ENFORCE_SRC_ROOT`, whose only exception is `/tests` | **Fixed.** Moved to `src/model/tools/`. Two of the three also hard-coded this checkout's absolute path, so running either from a sibling checkout rewrote *this* checkout's committed encoder artifact; both now derive the root from `__file__` |
| I1-2 | HIGH | **FR-069 could not balance.** An attempt is one field on one chunk, and most (chunk, field) pairs correctly yield nothing because the field is printed elsewhere — a correct negative, which the requirement's stored-or-failed binary had no room for | **Fixed** (T101), on the user's decision to admit a third resolution. SC-054 and SC-008 both restated the binary and were amended with it |
| I1-3 | MEDIUM | **FR-058's count was inverted in the spec.** The partition is ten attempted and twelve excluded over twenty-two terms; the requirement named the wrong side. The code was right | **Fixed.** The requirement text corrected in place with the amendment dated and attributed |
| I1-4 | MEDIUM | **`oversized_sentence` was never recorded.** One of FR-056's closed five; `ChunkerError` carried its three values as prose | **Fixed** (T100). They are attributes now, and `fixture_missing` and `provider_unreachable` route through their constructors instead of being built directly, so the mapping is wired rather than coincidental |
| I1-5 | MEDIUM | **`results_manifest` could never have executed on a real report.** `chunking_section` published each FR-053 figure three times under one label and `results_manifest` refuses duplicates, so the FR-074 artifact was unproducible and nothing had ever tried | **Fixed.** Found only by running it, which is the point |
| I1-6 | LOW | A draft restated the provider distribution's name in `cli.py`, which `tests/checks/test_single_import_site.py` rejects — exactly one source file may name it | **Fixed.** `PROVIDER_CLIENT = "gateway"`, the route this job actually observes failing |

**The shape iteration 1 kept finding.** I1-1 was the third instance in this epic
of one pattern: a component built, unit-tested in isolation, and called by
nothing. The first two were nothing invoking `extract_fields` and nothing
running the pipeline from the console entry. A per-task check cannot see it,
because every individual piece is green.

---

## QC iteration 2 — FAILED, ten findings

| ID | Sev | Finding | Disposition |
|---|---|---|---|
| I2-1 | **CRITICAL** | **Governance violation.** ADR-0018, ADR-0019 and ADR-0020 and their `sad.md` catalog rows were authored **on this feature branch**. Governance serializes amendments to the documents it names onto the default branch: a feature branch records the need and does not perform it | **Fixed**, by the procedure E003 used for ADR-0017: landed on `main` in `e8bc1ff` and merged back at `7652e9d`. The branch now *contains* them by merge rather than *authoring* them |
| I2-2 | HIGH | **A fourth orphaned component.** `reconcile_invocations` and `InvocationReconciliation` (`ingest/cli.py`) were exported and had zero production call sites. They duplicated the live path, `report.reconciliation_section`, which `publish.py` calls — both restating FR-070's zero-attempt invariant independently, one of them unreachable | **Fixed.** The unreachable copy deleted with its `__all__` entries. **Correction, from iteration 3 (F-D1):** an earlier version of this row said two assertions were "moved onto the live path". They were not — `report.reconciliation_section` already carried both the signed count difference and the negative-count refusal before the fix, and `ccd9c23`'s diff of `report.py` touches only the `COUNTING_UNITS` removal. What was added is **tests** for live behaviour that had none on either copy |
| I2-3 | MEDIUM | **`ruff format --check` never ran at the repository root in CI.** The Lint step ran `ruff check .` at root; the Format step looped the three entries only. 403 files against 243 — everything under `/tests` is in the difference | **Fixed**, and closed structurally: a new check asserts every tier ruff lints is a tier ruff format-checks. Same root cause as A-23 — the root is not an entry, so it falls out of a per-entry loop — and this is the third instance, so the class was made detectable rather than the instance patched |
| I2-4 | MEDIUM | **No `qc-report.md`.** All five prior features have one; this workspace had none after two iterations and nineteen findings | **Fixed.** This file |
| I2-5 | LOW | **Four fully dead exports**: `artifacts.encoder_identity()` (byte-identical to the live `embed.embedding_identity`), `metrics.CONFIDENCE_LEVEL` (the level is carried by `INTERVAL_METHOD`), `schemas.printed_but_unattempted()` (superseded by `reference.printed_without_term`, and still wrong — it is the narrow vocabulary-only view that iteration 1's F6 corrected), `report.COUNTING_UNITS` (imported and never read) | **Fixed.** All four removed, each verified to have no reference in `src`, `tests` or `specs` first, and each with a comment at the deletion site saying what the live answer is. See §Reviewed and kept for the eleven test-only exports, which were **not** deleted |
| I2-6 | LOW | **Two of FR-056's five run-failure kinds are unreachable, disclosed only in a code comment.** `corpus_digest_mismatch` and `document_id_collision` are checked before the run record exists, so no row can carry the kind — two of migration `0300`'s five enum values cannot be written by the shipped pipeline | **Fixed as a disclosure, not as code.** FR-056 amended in place, dated, with the reason and the argument for keeping both kinds in the enum; a twelfth row added to Disclosed Limitations with its reversal trigger and production-scale alternative |
| I2-7 | LOW | **`.completed` was inaccurate.** It said "95 of 97 tasks complete" and named `2fd211f` as the final revision. At head, `tasks.md` holds 103 tasks, 101 complete | **Fixed.** Counts corrected; the `## Verification at 2fd211f` block left labelled with its own revision, because those figures measure that tree |
| I2-8 | LOW | **Two records described a positive control as its opposite.** `analysis-report.md` and `plan.md` both said the A-25 fix ships "four negative controls". It ships three negative controls and one positive control, and the positive one is the more valuable claim — without it a broken copy helper would make all three damage cases "fail correctly" for the wrong reason | **Fixed** in both files, with the reason stated rather than the number silently changed |
| I2-9 | LOW | **All four pytest tiers shared one `--basetemp`.** Root `.tmp/pytest` and three `../../.tmp/pytest` resolved to one directory, and pytest clears its basetemp at start of every run. Correct in CI, which is sequential; two tiers run concurrently wipe each other's `tmp_path` mid-run. This bit two agents during this epic | **Fixed.** `pytest-checks`, `-model`, `-api`, `-gateway`; the guard test still asserts every path resolves inside the checkout **and** now asserts no two tiers share a directory |
| I2-10 | — | Unused `readFileSync` in `src/web/__tests__/boundary.test.ts` | **Not fixed, deliberately.** Pre-existing, from E001, outside this epic; `npm run lint` exits 0 and CI is unaffected |

---

## Reviewed and kept — the exports whose only callers are tests

Iteration 2 listed eleven of these separately from the four dead ones, and
correctly: a test-only caller is legitimate for a declared domain constant or a
seam that exists so a property can be asserted. **Eight** are listed below —
an earlier version of this paragraph said nine twice while the table held eight
(iteration 3, F-D5). The remaining names were not enumerated in the finding and
are not guessed at here. Each of the eight was checked against `src` rather
than assumed.

| Export | Verdict |
|---|---|
| `chunker.chunk_document` | **Keep.** A seam. Production chunks through `chunk_pages`; this wrapper exists so the determinism check and the containment guard can chunk a `DocumentRecord` end to end, which is the unit those properties are stated over |
| `tokens.fits_budget` | **Keep.** The budget predicate, asserted directly so a boundary case is a one-line test rather than a chunking run |
| `runs.active_generation` | **Keep.** A query the append-only and write-order properties are stated in; production reads generation state through the writer's own path, and asserting through a second reader is what makes the first one's answer checkable |
| `runs.record_confidence_policy` | **Keep.** A deliberate alias of `write_run_record`, documented as such: FR-038 asks what the run ran with and FR-032 asks what policy its scores were judged under, and both names resolve to the one `INSERT` |
| `runs.RUN_STATES` | **Keep.** A declared domain, asserted against the database's own check constraint |
| `runs.GENERATION_STATUSES` | **Keep.** Same — a declared domain |
| `failures.REQUIRED_FIELDS` | **Keep.** Same — FR-035's required diagnostic content, declared once and asserted |
| `parse.normalized_page_text` | **Deleted at iteration 3.** This row previously read "genuinely dead — and not deleted here", and between writing it and acting on it the deletion was attempted, reverted on a mistaken argument, and then made properly. The mistaken argument, recorded because it is the instructive part: removing the wrapper orphaned `parse.py`'s imports of `normalize_page_text` and `page_text`, which was read as evidence that it was a load-bearing seam. It is the opposite — those imports existed *only* to implement this function, so they are orphaned **by** the deletion rather than evidence against it, and a normalization route no code takes normalizes nothing. SC-037 asserts the *absence* of a second normalization, which deleting a zero-caller wrapper cannot create. Original text follows.<br><br>**Genuinely dead.** Iteration 2 classified it as test-only; it is not. Its one mention outside its own definition is a *docstring* in `test_single_page_reader.py` explaining that `normalize_page_text` and `normalized_page_text` are different names. There is no call, from production or from a test. Reported rather than removed because iteration 2 scoped this list to review and not deletion. Recommend deleting it, or giving it a caller, in whichever epic next touches `parse.py` |

---

## Not measured — recorded rather than implied

**T081 — the extraction fixtures — is BLOCKED, and everything downstream of it
is unmeasured.** A fixture is a recorded provider response; none can be produced
without network access and a provider credential, neither of which is available.
That leaves the entire extraction half of the pipeline exercised only against
injected doubles, and the replay run aborts at the first synthetic transmittal.

**A second, independent cause, which matters because fixing the first would not
be enough.** `gateway.compute.hashing.fixture_key` digests
`request.model_dump_json()`, and `trace_id` is a declared field on
`InvocationRequest` carrying no `exclude=True`, so the correlation identifier is
part of the key. FR-070 mints one trace id per run, so the same request keys
differently on every run and **no fixture could be replayed even if recorded.**
Verified directly on the committed code. This is E004's defect; it survived
E004's QC because `resolve_trace_id` mints an id when the caller supplies none,
so E004's own tests key with `trace_id` null and stay stable. E006 is the first
caller obliged to supply one. Left unfixed because it belongs to a shipped,
QC-passed epic and fixing it does not by itself unblock T081.

**T089 — the regenerated ingestion report — is BLOCKED transitively.** No
results manifest is committed, so `verify.yml`'s `reproduce` job compares the
*reachable* portion of the replay pipeline against the corpus manifests rather
than performing FR-074's figure-level comparison. The job says so in its own
output rather than reporting a pass that means something narrower than it
sounds, and it fails loudly the day a manifest is committed without the emitter
to regenerate it.

**Two further E004 findings are recorded and not fixed here**:
`DEFAULT_FIXTURE_ROOT` resolves relative to the installed package and lands
inside `.venv` when reached through the model entry, and the gateway rejects the
`postgresql+psycopg://` spelling that `ingest.writer` normalizes before use.

**Two of FR-056's five run-failure kinds cannot be written by the shipped
pipeline** — I2-6 above. Now disclosed in `spec.md` rather than only in a code
comment.

---

## Verdict

**PASS.** Both required categories green. Four suites green — model 2,410,
checks 252, gateway 405 with 5 opt-in skips, web 3 — and the **fifth Python
entry, `src/api`, has no tests at all** (iteration 3, F-D6). It ships three
empty `__init__.py` files and no runtime code, `verify.yml` has no api test
step, and `src/api/pyproject.toml`'s `testpaths = ["tests"]` matches nothing,
so pytest warns and exits 0. Zero tests over zero code is defensible; leaving
it unnamed is not, because iteration 1's own HIGH finding was that four of five
tiers green is a statement about four tiers. Five coverage gates exit 0.
Eight import contracts kept, zero broken. 73 of 74 functional requirements cited
in code or tests, the 74th discharged by artifacts on `main`.

**With two blocked tasks disclosed rather than closed** (T081, T089), and their
cause named twice over: no credential, and a fixture key that would not be
stable even with one.

Nineteen findings across two prior iterations were fixed. Three of them —
A-23, I2-3 and I2-9 — were the same root cause wearing different clothes: *the
repository root is not an entry, so it falls out of a `for entry in …` loop.*
The third fix is the one that closes the class, and it is a committed check
rather than a note asking the next reader to remember.
