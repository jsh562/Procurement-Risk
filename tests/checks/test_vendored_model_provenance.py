"""The vendored reranker's provenance, asserted independently of the code that reads it.

Spec FR-016 and §Data Provenance. Cross-entry, under `/tests`, for the reason
the layout rule gives: this asserts on what *entered the repository*, not on a
runtime path, so it belongs to no single entry.

**Deliberately stdlib-only.** It does not import `gateway.inference.artifacts`,
for two reasons. The root project is not a uv workspace and carries no entry
dependencies — that independence is the property the four entries exist to
guarantee. And an artifact checked with the same code that ships to read it is
checked against itself: if the verifier and the record drift together, both
agree and nothing reports it. The digests here are recomputed from the bytes.

The gateway's own suite covers the other half — that `verify_artifact` *refuses*
a tampered artifact — because that is a test of the verifier and belongs with it.

Every assertion has a failure it is for. A digest with no licence beside it is
reproducible and unshippable; a generated graph with no source hash is an
unreproducible binary whose provenance stops at "someone quantized something".
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
RERANKER = REPO_ROOT / "data" / "reranker"

#: Both graphs AD-011 commits to. The full-precision one is not spare weight:
#: FR-025 makes it a request-selectable arm so what quantization costs is
#: measured rather than asserted, and AD-013 keeps both resident because FR-017
#: forbids loading a graph on a request path.
GRAPHS = ("model-fp32.onnx", "model-int8.onnx")

#: What Data Provenance requires of every vendored artifact.
REQUIRED = ("model_id", "revision", "licence_basis", "source", "files")

#: What a *generated* artifact must additionally carry to be reproducible.
REQUIRED_GENERATED = ("generator", "seed", "generated_on", "source_graph_sha256")


def _record() -> dict[str, Any]:
    return json.loads((RERANKER / "provenance.json").read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def test_the_artifact_directory_exists() -> None:
    assert RERANKER.is_dir(), (
        "data/reranker/ is absent. The reranker is committed rather than fetched, "
        "because the no-network window opens before the package is imported."
    )


@pytest.mark.parametrize("field", REQUIRED)
def test_the_record_carries_required_provenance(field: str) -> None:
    value = _record().get(field)
    assert value, (
        f"provenance.json omits {field!r}. Data Provenance requires identity, "
        f"revision, licence basis and source of every vendored artifact — a "
        f"verified digest without them is reproducible and unshippable."
    )


@pytest.mark.parametrize("graph", GRAPHS)
def test_both_graphs_are_present(graph: str) -> None:
    """AD-011 commits to two graphs and both must be here.

    A record naming one would let the other drift unreported, which matters
    most for the FP32 graph: it exists only to be the comparison, so a silent
    change to it corrupts the measurement rather than the service.
    """
    assert (RERANKER / graph).is_file(), f"{graph} is missing from data/reranker/"


def test_every_committed_file_is_digested_and_every_digest_matches() -> None:
    """Equality both ways, not containment.

    A subset check passes when a file is added and never recorded, which is how
    an undigested artifact enters a repository that believes it digests
    everything.
    """
    record = _record()
    recorded = set(record["files"])
    present = {p.name for p in RERANKER.iterdir() if p.is_file() and p.name != "provenance.json"}
    assert recorded == present, (
        f"the record and the directory disagree — "
        f"recorded but absent: {sorted(recorded - present)}; "
        f"present but undigested: {sorted(present - recorded)}"
    )
    for name, expected in sorted(record["files"].items()):
        assert _sha256(RERANKER / name) == expected, f"{name} does not match its recorded digest"


@pytest.mark.parametrize("graph", GRAPHS)
def test_each_graph_records_its_own_licence_basis(graph: str) -> None:
    """A derived artifact does not inherit its source's licence automatically.

    The INT8 graph is produced from the FP32 one *in this repository*, so a
    single shared licence line would assert an inheritance nobody checked.
    """
    entry = _record()["graphs"][graph]
    assert entry["licence_basis"].strip(), f"{graph} records no licence basis of its own"


@pytest.mark.parametrize("field", REQUIRED_GENERATED)
def test_the_generated_graph_records_how_it_was_generated(field: str) -> None:
    """The INT8 graph is generated, so its provenance cannot stop at a digest."""
    generated = _record().get("generated")
    assert generated is not None, "the INT8 graph is generated and the record must say so"
    assert field in generated, (
        f"the generated record omits {field!r}. Without generator, seed, date and "
        f"source digest the graph is an unreproducible binary."
    )


def test_the_recorded_source_digest_is_the_committed_fp32_graph() -> None:
    """The quantization is recorded against bytes that are actually here.

    Without this the record could describe a quantization of a graph nobody
    committed, and the provenance chain would be internally consistent and
    false.
    """
    record = _record()
    assert record["generated"]["source_graph_sha256"] == record["files"]["model-fp32.onnx"]


def test_a_zero_seed_is_recorded_rather_than_omitted() -> None:
    """Absence and zero are different, and this artifact is where they differ.

    Dynamic quantization draws no random numbers, so the seed is legitimately
    `0`. An earlier revision of the reader tested truthiness and rejected the
    real artifact for omitting a field it carried. Asserted here so a
    plausible-looking `if not value` cannot quietly reintroduce it.
    """
    assert _record()["generated"]["seed"] == 0


def test_the_int8_graph_is_materially_smaller_than_the_full_precision_one() -> None:
    """The quantized graph is what the serving path loads, and it is smaller.

    Cheap, but it catches the failure that would otherwise be invisible: both
    names present, both digests valid, and the INT8 file a copy of the FP32 one
    because a conversion silently no-opped.
    """
    fp32 = (RERANKER / "model-fp32.onnx").stat().st_size
    int8 = (RERANKER / "model-int8.onnx").stat().st_size
    assert int8 < fp32 * 0.5, (
        f"the INT8 graph ({int8} bytes) is not materially smaller than the FP32 one "
        f"({fp32} bytes) — the quantization may have no-opped"
    )
