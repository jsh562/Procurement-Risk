# E006: Document Ingestion and Extraction

Turns the committed corpus into citable chunks, extracts transmittal field values
against those chunks, and refuses to store a value it cannot place on a page. Five
migrations in block `0400`–`0499`, a vendored ONNX encoder asserted against a
reference implementation, per-document transactions, and a 21-item ingestion report
that either publishes in full or names what it could not compute.

**QC passed on run 3**, after failing runs 1 and 2. 101 of 103 tasks; **T081 and
T089 are blocked and open, not quietly closed**. All five test tiers green — model
3,220, cross-entry 269, gateway 407, web 3, and `src/api` named as having no tests
rather than counted. Coverage 92% combined; `ingest` 90%, `llm` 95%, `compute` 92%.
See `qc-report.md` for all 28 findings across three runs and `.qc-passed` for the
measured numbers.

## What ships

- **Chunking with page provenance.** One committed page reader, reused rather than
  re-implemented — the ingestion package declares no second tolerance map, no second
  normalization, no second page-text assembly, and a source scan asserts their
  absence. A three-class boundary ladder splits on the page break *before* the
  structural ladder, so a clean structural split cannot straddle a page and violate
  the scalar page number.
- **A vendored FP32 ONNX encoder** (`data/encoder/`, 90 MB, committed to git), with
  its tokenizer, recorded digests, and a two-layer probe set. Parity against the
  reference implementation is asserted at bounds **declared before the comparison
  ran** — observed min cosine 1.000000000 and max per-dimension difference 1.704e-07
  against 0.999999 and 1e-5.
- **Citation that cannot be supplied by the model.** The cited page is inherited from
  the source chunk. For a value split across a page break the anchor is the chunk
  carrying the *printed value*, not the label, and contributors start at ordinal 2 —
  so a two-page value has exactly one contributing row, which is what E003's
  `ck_evcc__ordinal_min` forces.
- **Computed confidence, never self-reported.** Deductions of 0.15/0.10/0.25 applied
  left to right from 1.0 against a floor of 0.80, with the floor and all three weights
  written to the run record so a score is recomputable from what the run declared.
- **Per-document transactions on an autocommit connection**, driving `data-model.md`'s
  write order 0a–7 unchanged, with re-ingest skipping documents whose input tuple is
  unchanged. The run-level failure is written *outside* the transaction — writing it
  inside would roll back the record describing the rollback.
- **A report that refuses rather than under-publishes.** All 21 items or none. On the
  current fixture-blocked run it builds 16 from real data and names the other 5 with
  their obliging requirement and the builder's own refusal text.
- **Three operator runbooks** — whole-document correction, HNSW drop-and-rebuild, and
  promotion-with-removal — because none is reachable from the ingestion job.

## Cross-epic

- Adds **zero** columns, constraints or indexes to the six E003-owned tables, verified
  by comparing their catalog entries before and after this epic's migrations.
- Extends the computation-boundary and single-provider-import contracts as
  `model.llm` and `model.ingest` grow. `model.ingest` never imports `gateway`.
- Landed on `main` in their own commits, as Governance requires: E003's TR-081
  amendment, and ADR-0019/0020/0021 with their `sad.md` rows.

## Carried open, deliberately

- **T081 — extraction fixtures.** Three independent causes. Recording needs
  `ANTHROPIC_API_KEY`. Separately, `fixture_key` digests `trace_id`, so FR-070's
  one-trace-id-per-run mints a different key every run — six distinct keys observed
  for one document across six runs, meaning a recorded fixture would miss anyway.
  And `DEFAULT_FIXTURE_ROOT` resolves into `src/model/.venv/Lib/fixtures`. The
  second and third are E004's, need no credential, and are being fixed separately.
- **T089 — the regenerated ingestion report**, blocked transitively on T081.
- **Two amendment requests recorded, not performed** (a feature branch records; it
  does not amend): the Technology Stack line reads "ONNX Runtime for **INT8** CPU
  inference" while this epic ships FP32, and `project-instructions.md:73` says
  feature branches are *squash merged* while all thirteen actual deliveries were
  non-squash merge commits.

## Defects the work surfaced

- **Five instances of one shape: code that exists, is tested, and has no caller.**
  `extract_fields` built and never invoked; the pipeline never driven from
  `cli.main`; ~4,900 lines of publish layer reachable only from tests;
  `reconcile_invocations` duplicating a live path; three class methods that fell out
  of an `__all__`-scoped sweep. Every per-task check passed every time — the defect
  lives between the pieces.
- **Three collisions with E007, none of which git could see.** Both epics claimed
  ADR-0018; both claimed migration block `0300`–`0399` and authored real revisions
  in it; and E007's dependency-isolation check asserts equality over the modeling
  entry's distributions, which E006 had added four to. In every case the two sides
  touched different lines or different filenames, so the merge was clean and the
  result was wrong. E006 moved to `0400`–`0499` and re-parented onto E007's head.
- **FR-069 could not balance as written.** An attempt is one field on one chunk, and
  most (chunk, field) pairs correctly yield nothing — a correct negative, which the
  requirement's stored-or-failed binary had no room for. Amended to admit a third
  resolution; SC-008 and SC-054 restated the same binary and were amended with it.
- **E004's fixture key includes `trace_id`.** Found only because this epic became
  the first caller obliged to supply one. Both epics were internally correct; the
  defect exists only at the seam.
- **The temporary-files rule was incomplete.** v1.2.5 pinned `TMPDIR` and pytest's
  `--basetemp`, verified against `tempfile` and pytest, and did not cover PyTensor
  or Numba, which keep private caches. v1.2.6 pins both by name and requires
  absolute paths, because PyTensor flattens an unparseable path into a directory
  created relative to the working directory rather than failing.

## Artifacts

- `specs/00006-document-ingestion-and-extraction/` — spec (74 FR, 58 SC), plan,
  data model, research, three checklists (117 items), analysis report, `qc-report.md`,
  `.completed`, `.qc-passed`
- `specs/adrs/0019`, `0020`, `0021` — on `main`, with their `specs/sad.md` rows
- `data/encoder/` — 13 files; `model.onnx` verified against HuggingFace's published
  sha256 at revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`
- `src/model/src/model/{ingest,llm,compute}/` — 20 modules
- `src/model/src/model/schema/versions/0400`–`0404`
- `src/model/tools/` — the three provenance scripts that produced `data/encoder/`
