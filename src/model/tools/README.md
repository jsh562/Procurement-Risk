# src/model/tools

One-off scripts that produced committed artifacts. They are **not** part of any
package, nothing imports them, and no test runs them. They are here so that
`data/encoder/` has a recorded provenance rather than an unexplained 90 MB
binary.

## Why they live under `src/model`

They sat at `/tools` at the repository root until E006's QC, which is outside
the source root `project-instructions.md` §Source Code Layout requires
(`ENFORCE_SRC_ROOT`). That policy grants exactly one exception — `/tests`, for
cross-entry verification no entry owns — and these are neither tests nor
cross-entry: `build_probes.py` imports `model.ingest.documents`,
`model.ingest.manifest_reader` and `model.ingest.parse`, so the modelling entry
already owns them.

They sit **beside** `src/model/src/`, not inside it, and that is the point. The
build backend is `uv_build` with the default `src/` layout, so nothing here is
packaged; `testpaths = ["tests"]` keeps pytest from collecting it;
`import-linter` graphs the installed `model` package and never sees it; and the
entry's `[tool.coverage.run] source` does not reach it. What *does* reach it is
`ruff check .` and `ruff format --check .` run from `src/model`, whose rule set
is stricter than the root's — a gain rather than a cost, since at the root these
files were checked under a configuration with no `flake8-bandit`.

| Script | Produced |
|---|---|
| `fetch_encoder.py` | `data/encoder/` — the pinned FP32 ONNX graph, tokenizer and configs, fetched from `sentence-transformers/all-MiniLM-L6-v2` at revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41` and hashed on arrival into `digests.json` |
| `build_probes.py` | `data/encoder/probes.json` — 21 probes spanning both corpus layers, two of them deliberately over the token cap |
| `build_reference.py` | `data/encoder/parity-reference.json` — reference vectors from `sentence-transformers` on torch, which `src/model/tests/ingest/test_encoder_parity.py` asserts the ONNX path reproduces |

`build_probes.py` and `build_reference.py` resolve `data/encoder/` from their own
location. Both held one checkout's absolute path until the move; several
checkouts of this repository share a disk, so running either from a sibling
rewrote the first checkout's committed artifact.

## Running them

From the modelling entry, through its own environment:

```
uv run --directory src/model python tools/build_probes.py
```

Only `fetch_encoder.py` reaches the network, and only when the artifact is being
replaced — which invalidates every stored vector and needs a decision record, so
it is close to a never. It takes the target directory as its one argument.

`build_reference.py` needs `sentence-transformers` and torch, which this project
deliberately does not depend on: the whole point of ADR-0018 is that one ONNX
runtime serves both corpus and query embedding, and torch is the thing that
would not fit the 400 MB request-time envelope.

If you need that environment, **build it under `.tmp/`**, which is gitignored:

```
uv venv .tmp/refenv
uv pip install --python .tmp/refenv sentence-transformers
```

Not in a system temp directory. A previous run put a ~1 GB torch install under
`%LOCALAPPDATA%\Temp`, and Avast blocked it as a dropper — a gigabyte of
unsigned native DLLs appearing at once in a user-profile temp path is exactly
what that heuristic exists to catch. Nothing was corrupted, but the location was
indefensible: work belonging to this project should be visible inside it. This
is `project-instructions.md` v1.2.5's Temporary Files rule, and the amendment
was raised by exactly this script.
