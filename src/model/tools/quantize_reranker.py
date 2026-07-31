"""Produce the INT8 reranker graph from the vendored FP32 one, reproducibly.

Spec FR-016. The INT8 graph this repository serves is **generated here**, not
taken from upstream — and that is a deliberate choice with a stated reason.

`cross-encoder/ms-marco-MiniLM-L-6-v2` publishes four pre-quantized variants
(`model_qint8_arm64`, `model_qint8_avx512`, `model_qint8_avx512_vnni`,
`model_quint8_avx2`). Every one names a CPU feature set. Taking one would pin
the serving artifact to instruction extensions the container may not have, and
unsigned-signed quantization *saturates* on hardware lacking them — which
degrades scores silently rather than failing. Dynamic quantization applied here
produces one portable graph and computes activation scales at run time.

The cost is that the artifact is no longer upstream-published bytes, so its
provenance cannot stop at a digest. That is what the `generated` block in
`data/reranker/provenance.json` is for, and what
`gateway.inference.artifacts.verify_artifact` refuses an artifact without: the
generator, the seed, the date, and the SHA-256 of the source graph. Without
those an INT8 graph is an unreproducible binary whose provenance stops at
"someone quantized something".

**Quantization is not lossless and is not assumed to be harmless.** This is why
AD-011 ships the FP32 graph alongside and AD-013 keeps both resident: FR-025
makes the full-precision arm request-selectable so what quantization costs is
*measured* rather than asserted.

Run once, from the repository root::

    TMPDIR="$PWD/.tmp" TEMP="$PWD/.tmp" TMP="$PWD/.tmp" \\
      uv run --directory src/model python tools/quantize_reranker.py

Every scratch path resolves through `TMPDIR` per `AGENTS.md` §Temporary Files:
ONNX Runtime writes intermediate files during quantization, and this checkout
keeps them inside its own tree rather than in a user-profile temp directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

#: Deterministic by construction rather than by seeding. Dynamic quantization
#: computes activation scales at inference time, so the conversion itself draws
#: no random numbers — the same input graph yields the same output bytes. The
#: value is recorded anyway because FR-016 requires a seed in the generated
#: record, and recording "0, and here is why it does not matter" is honest where
#: omitting the field would leave a reader assuming one was used and lost.
GENERATION_SEED = 0

GENERATOR_ID = "model.tools.quantize_reranker"

_SOURCE_GRAPH = "model-fp32.onnx"
_TARGET_GRAPH = "model-int8.onnx"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _require_scratch_inside_checkout(root: Path) -> None:
    """Refuse to run if scratch would land outside this checkout.

    `AGENTS.md` §Temporary Files is not a preference. A quantization run writes
    unsigned intermediate files, and a previous task in this repository had a
    ~1 GB environment land in a user-profile temp directory and get flagged as a
    dropper by the machine's antivirus. It was right to. Checking here rather
    than trusting the caller's shell is the difference between a rule and a
    hope.
    """
    scratch = os.environ.get("TMPDIR") or os.environ.get("TEMP") or os.environ.get("TMP")
    if scratch is None:
        msg = (
            "TMPDIR/TEMP/TMP is unset. Set all three to this checkout's .tmp/ before "
            "running: quantization writes intermediate files, and AGENTS.md requires "
            "them inside the checkout."
        )
        raise SystemExit(msg)
    resolved = Path(scratch).resolve()
    if root.resolve() not in resolved.parents and resolved != root.resolve():
        msg = (
            f"scratch resolves to {resolved}, which is outside this checkout ({root}). "
            f"AGENTS.md requires temporary files inside the checkout's own .tmp/."
        )
        raise SystemExit(msg)


def quantize(directory: Path, *, repo_root: Path) -> dict[str, object]:
    """Quantize `directory/model-fp32.onnx` to INT8 and return its generated record.

    Returns the block that belongs under `"generated"` in the artifact's
    provenance record, rather than writing the record itself: this tool produces
    one file and describes it, and the record names several files. Keeping the
    two separate stops a re-run from silently rewriting digests for artifacts it
    did not touch.
    """
    _require_scratch_inside_checkout(repo_root)
    from onnxruntime.quantization import QuantType, quantize_dynamic

    source = directory / _SOURCE_GRAPH
    target = directory / _TARGET_GRAPH
    if not source.is_file():
        msg = f"the FP32 source graph is missing: {source}"
        raise SystemExit(msg)

    source_digest = _sha256(source)
    quantize_dynamic(
        model_input=str(source),
        model_output=str(target),
        # Signed INT8 for weights. `QUInt8` is what the upstream `quint8_avx2`
        # variant uses and it is the form that saturates on hardware without the
        # VNNI extensions -- the failure that degrades scores silently instead
        # of raising. Signed avoids that at no measured cost here.
        weight_type=QuantType.QInt8,
    )
    return {
        "generator": GENERATOR_ID,
        "seed": GENERATION_SEED,
        "generated_on": datetime.now(UTC).date().isoformat(),
        "source_graph": _SOURCE_GRAPH,
        "source_graph_sha256": source_digest,
        "method": "onnxruntime.quantization.quantize_dynamic",
        "weight_type": "QInt8",
        "note": (
            "Dynamic quantization computes activation scales at run time, so the "
            "conversion draws no random numbers and the seed is recorded for the "
            "record's completeness rather than because it varies the output."
        ),
    }


def main(argv: list[str] | None = None) -> int:
    root = Path(__file__).resolve().parents[3]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--directory",
        type=Path,
        default=root / "data" / "reranker",
        help="the vendored reranker directory holding model-fp32.onnx",
    )
    args = parser.parse_args(argv)
    record = quantize(args.directory, repo_root=root)
    print(json.dumps(record, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
