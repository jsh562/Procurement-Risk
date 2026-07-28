"""The committed encoder artifact: resolved, digest-verified, never fetched.

FR-019 / ADR-0018. Everything the encoder needs — the ONNX graph, the tokenizer
at the same revision, the declared sequence cap, the probe set and the reference
vectors — lives under `data/encoder/` in the repository and is verified here
**before** an ONNX Runtime session is created.

**Committed rather than fetched, and the reason is a window, not a preference.**
SC-023's no-network condition opens before the package is imported, so a
resolution performed at import time is a network access *inside* that window
even if it would normally hit a cache. There is therefore no download path in
this module at all: a missing file and a mismatched digest both raise, naming
the artifact, and neither falls back to a fetch. That is FR-019's "fail naming
the artifact rather than fall back to a remote fetch", written as the absence of
the alternative rather than as a flag that defaults to off.

**Verification precedes session creation.** `verified_encoder()` is the only
sanctioned way to reach `model.onnx`, and it digests first. Verification is
cached per process because the graph is ~90 MB and a run embeds several thousand
chunks; the cache holds the *result*, so the first call in any process pays for
the check and no call skips it.

**`README.md` is deliberately outside the digest record.** It is documentation,
and listing it would make a wording fix fail an ingestion run — a check that
fires on something harmless is a check people learn to route around.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from model.corpus.paths import REPO_ROOT

__all__ = [
    "DIGEST_RECORD",
    "ENCODER_DIR",
    "ArtifactError",
    "EncoderArtifact",
    "artifact_path",
    "encoder_identity",
    "verified_encoder",
]

#: The committed artifact directory. Derived from the repository root E002
#: already computes rather than from a second derivation of the same path.
ENCODER_DIR = REPO_ROOT / "data" / "encoder"
DIGEST_RECORD = "digests.json"

_CHUNK = 1 << 20


class ArtifactError(ValueError):
    """Raised when the committed artifact is absent, unreadable, or altered.

    One type for every failure: a caller learns the same thing from each of them
    — no vector may be produced, and the run fails naming the artifact.
    """


@dataclass(frozen=True)
class EncoderArtifact:
    """The verified artifact and the identity every chunk records.

    `model_id` and `revision` are FR-020's pair, read from the digest record
    rather than typed into a module, so the identity a chunk carries and the
    bytes that produced its vector cannot disagree.
    """

    directory: Path
    model_id: str
    revision: str
    precision: str
    vector_dimension: int
    sequence_cap: int
    files: Mapping[str, str]

    @property
    def graph(self) -> Path:
        return self.directory / "model.onnx"

    @property
    def tokenizer(self) -> Path:
        return self.directory / "tokenizer.json"


def artifact_path(name: str) -> Path:
    """A file inside the artifact directory, by name.

    A bare name, never a path: the directory is repository-committed and closed,
    so nothing here needs to accept a separator, and refusing one keeps a
    caller-supplied string from reaching a filesystem join (CWE-73).
    """
    if not isinstance(name, str) or not name or "/" in name or "\\" in name or name == "..":
        raise ArtifactError(f"{name!r} is not a plain file name inside {ENCODER_DIR}")
    return ENCODER_DIR / name


def _digest_of(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            while chunk := handle.read(_CHUNK):
                digest.update(chunk)
    except OSError as exc:
        raise ArtifactError(f"FR-019: cannot read the committed artifact {path}: {exc}") from exc
    return "sha256:" + digest.hexdigest()


def _record() -> Mapping[str, object]:
    path = ENCODER_DIR / DIGEST_RECORD
    if not path.is_file():
        raise ArtifactError(
            f"FR-019: the encoder digest record is absent at {path}; the run fails rather "
            "than fetching the artifact"
        )
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ArtifactError(f"cannot read {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise ArtifactError(f"{path} must hold a JSON object")
    return document


def _text(record: Mapping[str, object], key: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ArtifactError(f"{DIGEST_RECORD}: {key} must be a non-empty string, found {value!r}")
    return value


def _positive_int(record: Mapping[str, object], key: str) -> int:
    value = record.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ArtifactError(f"{DIGEST_RECORD}: {key} must be a positive integer, found {value!r}")
    return value


@lru_cache(maxsize=1)
def verified_encoder() -> EncoderArtifact:
    """Verify every recorded digest and return the artifact (FR-019).

    Every file named in the record is checked, not only the graph: a tokenizer
    swapped for one at another revision makes the *measured* chunk length and
    the *consumed* length disagree with no error anywhere (ADR-0018), which is
    precisely the failure a digest on the weights alone would miss.
    """
    record = _record()
    files = record.get("files")
    if not isinstance(files, dict) or not files:
        raise ArtifactError(f"{DIGEST_RECORD}: files must be a non-empty JSON object")

    missing: list[str] = []
    altered: list[str] = []
    for name in sorted(files):
        expected = files[name]
        if not isinstance(expected, str):
            raise ArtifactError(f"{DIGEST_RECORD}: the digest of {name!r} must be a string")
        path = artifact_path(name)
        if not path.is_file():
            missing.append(name)
            continue
        observed = _digest_of(path)
        if observed != expected:
            altered.append(f"{name} (recorded {expected}, found {observed})")
    if missing or altered:
        raise ArtifactError(
            "FR-019: the committed encoder artifact does not verify and is never fetched; "
            f"absent={sorted(missing)} altered={altered}"
        )

    artifact = EncoderArtifact(
        directory=ENCODER_DIR,
        model_id=_text(record, "model_id"),
        revision=_text(record, "revision"),
        precision=_text(record, "precision"),
        vector_dimension=_positive_int(record, "vector_dimension"),
        sequence_cap=_positive_int(record, "effective_sequence_cap"),
        files=dict(files),
    )
    if not artifact.graph.is_file() or not artifact.tokenizer.is_file():
        raise ArtifactError(
            f"FR-019: {artifact.graph.name} and {artifact.tokenizer.name} must both be "
            f"present in {ENCODER_DIR}"
        )
    return artifact


def encoder_identity() -> tuple[str, str]:
    """FR-020's `(model id, revision)`, verified before it is returned."""
    artifact = verified_encoder()
    return artifact.model_id, artifact.revision
