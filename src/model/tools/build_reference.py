"""One-off: compute the reference encoder's vectors over the committed probe set.

Runs in a throwaway environment holding `sentence-transformers` — the reference
implementation ADR-0018 requires the ONNX export proven against. The *output* is
committed; the environment is not, and nothing in the repository imports it.
"""

from __future__ import annotations

import json
import platform
from pathlib import Path

import sentence_transformers
import torch
import transformers
from sentence_transformers import SentenceTransformer

# `src/model/tools/build_reference.py` -> the checkout root. Derived rather
# than written out, for the reason recorded in `build_probes.py`.
ENCODER = Path(__file__).resolve().parents[3] / "data" / "encoder"
MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"

probes = json.loads((ENCODER / "probes.json").read_text("utf-8"))["probes"]
texts = [probe["text"] for probe in probes]

LOCAL = Path(__file__).resolve().parent / "refmodel"
model = SentenceTransformer(str(LOCAL), device="cpu", local_files_only=True)
model.eval()
print("max_seq_length", model.max_seq_length, flush=True)

with torch.no_grad():
    vectors = model.encode(
        texts,
        batch_size=8,
        convert_to_numpy=True,
        normalize_embeddings=True,
        show_progress_bar=False,
    )

payload = {
    "purpose": (
        "Reference vectors for FR-019's parity assertion. Produced once by the "
        "reference implementation and committed, so the assertion runs offline "
        "from a clean checkout with no network and no second inference stack."
    ),
    "reference_implementation": {
        "library": "sentence-transformers",
        "sentence_transformers": sentence_transformers.__version__,
        "transformers": transformers.__version__,
        "torch": torch.__version__,
        "python": platform.python_version(),
        "platform": platform.platform(),
        "model_id": MODEL_ID,
        "revision": REVISION,
        "max_seq_length": int(model.max_seq_length),
        "normalize_embeddings": True,
    },
    "vectors": {
        probe["probe_id"]: [float(value) for value in vector]
        for probe, vector in zip(probes, vectors, strict=True)
    },
}
# newline="\n" is load-bearing — see the note in build_probes.py. The digest is
# taken over these bytes; Windows text mode would write CRLF and record a hash
# that no Linux checkout can reproduce.
(ENCODER / "parity-reference.json").write_text(
    json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
    encoding="utf-8",
    newline="\n",
)
print("wrote", len(payload["vectors"]), "vectors of", len(next(iter(payload["vectors"].values()))))
