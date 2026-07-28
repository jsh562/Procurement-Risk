"""One-off fetch of the pinned encoder artifact. Run manually, never at import time.

Downloads the upstream-published FP32 ONNX graph and the tokenizer files for
`sentence-transformers/all-MiniLM-L6-v2` at a pinned commit, into `data/encoder/`.
"""

from __future__ import annotations

import hashlib
import sys
import urllib.request
from pathlib import Path

REPO = "sentence-transformers/all-MiniLM-L6-v2"
REVISION = "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
FILES = [
    "onnx/model.onnx",
    "tokenizer.json",
    "tokenizer_config.json",
    "special_tokens_map.json",
    "vocab.txt",
    "config.json",
    "sentence_bert_config.json",
    "modules.json",
    "1_Pooling/config.json",
]

target_root = Path(sys.argv[1])


def fetch(rfilename: str) -> None:
    url = f"https://huggingface.co/{REPO}/resolve/{REVISION}/{rfilename}"
    # Flatten `onnx/model.onnx` to `model.onnx`; keep pooling config named.
    name = {
        "onnx/model.onnx": "model.onnx",
        "1_Pooling/config.json": "pooling_config.json",
    }.get(rfilename, rfilename)
    out = target_root / name
    out.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=600) as response:
        payload = response.read()
    out.write_bytes(payload)
    print(f"{name} {len(payload)} sha256:{hashlib.sha256(payload).hexdigest()}", flush=True)


for rfilename in FILES:
    fetch(rfilename)
print("DONE", flush=True)
