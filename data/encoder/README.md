# Committed encoder artifact (E006, FR-019, ADR-0018, AD-014)

The pinned sentence encoder, its tokenizer, and the parity evidence for both —
committed rather than fetched, because SC-023's no-network window opens *before*
the package is imported. A resolution at import time is a network access inside
that window, so nothing here is downloaded at run time and a missing or altered
file **fails the run naming the artifact** rather than falling back to a fetch.

## What is pinned

| Term | Value |
|---|---|
| Model identity | `sentence-transformers/all-MiniLM-L6-v2` |
| Revision | `1110a243fdf4706b3f48f1d95db1a4f5529b4d41` |
| Precision | **FP32** (AD-014; ADR-0012 budgets ~80 MB of full-precision weights) |
| Vector dimension | 384 (`EMBEDDING_DIM`, ADR-0012) |
| Effective sequence cap | **256** — `max_seq_length` in `sentence_bert_config.json` |
| Content budget | **254** pieces — the cap less `[CLS]` and `[SEP]` (HINT-001) |
| Graph output | `last_hidden_state` — token-level states, pooling is repository code |

`model_max_length` in `tokenizer_config.json` is `512` and is **not** the cap.
Nothing in this repository reads it; `model/ingest/tokens.py` reads
`sentence_bert_config.json` and subtracts a *measured* special-token overhead.

## Files

| File | What it is |
|---|---|
| `model.onnx` | The FP32 ONNX graph — the transformer alone |
| `tokenizer.json`, `tokenizer_config.json`, `special_tokens_map.json`, `vocab.txt` | The tokenizer, at the same revision as the weights |
| `config.json`, `sentence_bert_config.json`, `modules.json`, `pooling_config.json` | The encoder's own declared configuration, including the pooling mode the repository code implements |
| `probes.json` | The parity probe set: 21 probes spanning both corpus layers, each recording the document, page and line range it was taken from, plus the **declared** tolerance bounds |
| `parity-reference.json` | The reference implementation's vectors over that probe set, with the library and platform that produced them |
| `digests.json` | Every file above, digested. `model/ingest/artifacts.py` checks each before a session is created |

## Provenance of the export, stated plainly

The graph is the **upstream-published FP32 ONNX export at the pinned revision**,
not a graph re-exported on a developer machine. That is a deliberate choice and
it trades one property for another:

- *Given up*: the export is not produced by a toolchain this repository pins, so
  "how was this graph produced" resolves to an upstream artifact rather than to a
  local script.
- *Gained*: the artifact is reproducible by anyone from a content digest and a
  revision, with no PyTorch toolchain, no export-time nondeterminism, and no
  possibility of two developers vendoring two different graphs from the same
  model identity. A locally re-exported graph is byte-unstable across exporter
  versions, so its digest would pin nothing.

Either way the export is worthless unproven, which is why the parity assertion
exists and why its bounds were declared before the first comparison ran.

`README.md` is deliberately **not** in `digests.json`: it is documentation, and
listing it would make a wording fix fail the ingestion run.

## Pooling and normalization are repository code

A raw export emits token-level hidden states and stops (HINT-005). Mean pooling
weighted by the attention mask and L2 normalization are separate modules in the
reference implementation and are implemented in `model/ingest/embed.py` here.
Pooling over padding produces plausible vectors that are quietly wrong, which is
why ADR-0018 makes the tolerance mandatory rather than diligent.

## Re-recording these files (a one-off, never a run-time step)

1. Fetch each file in `digests.json` from
   `https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2/resolve/<revision>/<path>`
   — `onnx/model.onnx` is stored here as `model.onnx` and `1_Pooling/config.json`
   as `pooling_config.json`.
2. Rebuild `probes.json` from the corpus through `model.ingest.parse.read_pages`,
   recording document, page and line range per probe.
3. Recompute `parity-reference.json` in a throwaway environment holding
   `sentence-transformers` at the recorded versions. That environment is not a
   repository dependency and nothing imports it.
4. Recompute `digests.json` over every file in this directory.
5. Re-run `src/model/tests/ingest/test_encoder_parity.py`. A change of revision
   changes every published retrieval figure, so it is a run-record change and not
   a maintenance edit.
