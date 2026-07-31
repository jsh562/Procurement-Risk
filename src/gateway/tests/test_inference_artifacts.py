"""`verify_artifact` refuses what it is supposed to refuse.

The committed artifact's *contents* are asserted cross-entry, in
`tests/checks/test_vendored_model_provenance.py`, and deliberately without this
module — an artifact checked by the code that ships to read it is checked
against itself. This module is the other half: a verifier that has only ever
seen a good artifact is a check nobody has watched fail.

Every case here is built by damaging a copy, because the failures worth
asserting are the ones a real artifact acquires — a flipped byte, a dropped
file, a provenance field someone removed because it looked redundant.
"""

from __future__ import annotations

import json
import shutil
from collections.abc import Iterator
from pathlib import Path

import pytest

from gateway.inference.artifacts import ArtifactError, verify_artifact

RERANKER = Path(__file__).resolve().parents[3] / "data" / "reranker"

pytestmark = pytest.mark.skipif(
    not (RERANKER / "provenance.json").is_file(),
    reason="the vendored reranker is not present in this checkout",
)


@pytest.fixture
def artifact(tmp_path: Path) -> Iterator[Path]:
    """A throwaway copy to damage."""
    target = tmp_path / "reranker"
    shutil.copytree(RERANKER, target)
    yield target
    shutil.rmtree(target, ignore_errors=True)


def _record(directory: Path) -> dict:
    return json.loads((directory / "provenance.json").read_text(encoding="utf-8"))


def _write(directory: Path, record: dict) -> None:
    (directory / "provenance.json").write_text(json.dumps(record), encoding="utf-8")


def test_an_undamaged_artifact_verifies(artifact: Path) -> None:
    """The baseline. Without it every refusal below could be vacuous."""
    verified = verify_artifact(artifact)
    assert verified.model_id
    assert verified.licence_basis
    assert verified.generated is not None


def test_a_flipped_byte_is_refused(artifact: Path) -> None:
    graph = artifact / "model-int8.onnx"
    payload = bytearray(graph.read_bytes())
    payload[len(payload) // 2] ^= 0xFF
    graph.write_bytes(bytes(payload))
    with pytest.raises(ArtifactError, match="digest mismatch"):
        verify_artifact(artifact)


def test_a_missing_file_is_refused(artifact: Path) -> None:
    """A tokenizer swapped or dropped is the failure a digest on the graph misses.

    It makes the *measured* sequence length and the *consumed* length disagree
    with no error anywhere.
    """
    (artifact / "tokenizer.json").unlink()
    with pytest.raises(ArtifactError, match="missing"):
        verify_artifact(artifact)


@pytest.mark.parametrize("field", ["model_id", "revision", "licence_basis", "source"])
def test_a_record_missing_required_provenance_is_refused(artifact: Path, field: str) -> None:
    record = _record(artifact)
    del record[field]
    _write(artifact, record)
    with pytest.raises(ArtifactError, match="missing required provenance"):
        verify_artifact(artifact)


@pytest.mark.parametrize("field", ["generator", "seed", "generated_on", "source_graph_sha256"])
def test_a_generated_record_missing_reproduction_detail_is_refused(
    artifact: Path, field: str
) -> None:
    record = _record(artifact)
    del record["generated"][field]
    _write(artifact, record)
    with pytest.raises(ArtifactError, match="generated artifact but omits"):
        verify_artifact(artifact)


def test_a_zero_seed_is_accepted(artifact: Path) -> None:
    """Absence and zero are different things.

    Dynamic quantization draws no random numbers, so the real seed is `0`. An
    earlier revision tested truthiness and rejected the committed artifact for
    omitting a field it carried. This is the regression guard for that fix, and
    it is asserted here rather than only against the real artifact so it holds
    even if the committed seed later changes.
    """
    record = _record(artifact)
    record["generated"]["seed"] = 0
    _write(artifact, record)
    assert verify_artifact(artifact).generated["seed"] == 0


def test_a_blank_provenance_field_is_refused_like_an_absent_one(artifact: Path) -> None:
    """A field padded with whitespace is the same nothing as no field."""
    record = _record(artifact)
    record["generated"]["generator"] = "   "
    _write(artifact, record)
    with pytest.raises(ArtifactError, match="generated artifact but omits"):
        verify_artifact(artifact)


def test_a_missing_record_is_refused(artifact: Path) -> None:
    (artifact / "provenance.json").unlink()
    with pytest.raises(ArtifactError, match="cannot read the provenance record"):
        verify_artifact(artifact)


def test_a_path_is_refused_where_a_bare_name_belongs(artifact: Path) -> None:
    """The artifact directory is committed and closed, so nothing needs a separator.

    Refusing one keeps a caller-supplied string from reaching a filesystem join.
    """
    verified = verify_artifact(artifact)
    for candidate in ("../secrets", "sub/dir.onnx", ""):
        with pytest.raises(ArtifactError, match="bare names"):
            verified.path(candidate)
