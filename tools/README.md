# tools

One-off scripts that produced committed artifacts. They are **not** part of any
package, nothing imports them, and no test runs them. They are here so that
`data/encoder/` has a recorded provenance rather than an unexplained 90 MB
binary.

| Script | Produced |
|---|---|
| `fetch_encoder.py` | `data/encoder/` — the pinned FP32 ONNX graph, tokenizer and configs, fetched from `sentence-transformers/all-MiniLM-L6-v2` at revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41` and hashed on arrival into `digests.json` |
| `build_probes.py` | `data/encoder/probes.json` — 21 probes spanning both corpus layers, two of them deliberately over the token cap |
| `build_reference.py` | `data/encoder/parity-reference.json` — reference vectors from `sentence-transformers` on torch, which `tests/ingest/test_encoder_parity.py` asserts the ONNX path reproduces |

## Running them

Only `fetch_encoder.py` reaches the network, and only when the artifact is being
replaced — which invalidates every stored vector and needs a decision record, so
it is close to a never.

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
indefensible: work belonging to this project should be visible inside it.
