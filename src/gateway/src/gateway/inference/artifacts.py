"""Digest, identity, licence and source verification, before a session exists.

Spec FR-016. E006 verifies the encoder the same way in `model.ingest.artifacts`,
and this is deliberately **not** an import of that: the gateway may not reach
the modeling entry, and a shared helper living in the gateway would have made
E006 depend on E008's package for a check it already had. The shape is the
same; the two verify different artifact sets and neither is the other's caller.

What this adds beyond E006's version is the half FR-016 gained at E008:

- **Licence basis and source** are required of every artifact, not just a
  digest. A vendored third-party model with a verified hash and no recorded
  licence is reproducible and unshippable.
- **A generated artifact records how it was generated.** The INT8 reranker graph
  is produced by quantizing the FP32 one, so its record carries the generator
  identity, the seed, the date and the hash of the source graph. Without those a
  quantized graph is an unreproducible binary whose provenance stops at
  "someone quantized something".
- **Each graph carries its own licence basis.** A derived artifact does not
  inherit its source's licence automatically, and one shared licence line would
  assert an inheritance nobody checked.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

__all__ = [
    "ArtifactError",
    "VerifiedArtifact",
    "verify_artifact",
]

_DIGEST_CHUNK_BYTES = 1024 * 1024

#: Every record must carry these. Absence is a refusal, not a default: a missing
#: licence basis silently becomes "unknown" if it is allowed to be optional, and
#: unknown is the state Data Provenance exists to forbid.
_REQUIRED_FIELDS = ("model_id", "revision", "licence_basis", "source", "files")

#: A record that declares itself generated must additionally carry these.
_REQUIRED_GENERATED_FIELDS = ("generator", "seed", "generated_on", "source_graph_sha256")


class ArtifactError(RuntimeError):
    """An artifact is absent, incomplete, or does not match its record.

    One type for every failure, because a caller learns the same thing from each
    of them: no session may be created from these bytes.
    """


@dataclass(frozen=True)
class VerifiedArtifact:
    """A verified artifact directory and the provenance it records."""

    directory: Path
    model_id: str
    revision: str
    licence_basis: str
    source: str
    files: Mapping[str, str]
    generated: Mapping[str, object] | None = None

    def path(self, name: str) -> Path:
        """A file inside the artifact directory, by bare name.

        A bare name, never a path: the directory is repository-committed and
        closed, so nothing here needs a separator, and refusing one keeps a
        caller-supplied string from reaching a filesystem join (CWE-73).
        """
        if "/" in name or "\\" in name or name in {"", ".", ".."}:
            msg = f"artifact file names are bare names, not paths: {name!r}"
            raise ArtifactError(msg)
        return self.directory / name


def _absent(record: Mapping[str, object], field: str) -> bool:
    """Whether `field` is genuinely missing, as opposed to falsy.

    Absence and zero are different things, and conflating them is not
    hypothetical here: the quantization seed is legitimately **0**, and a
    truthiness test rejected the real artifact for omitting a field it carried.
    An empty or whitespace-only string still counts as absent, because a
    provenance field padded with a blank is the same nothing as no field.
    """
    value = record.get(field)
    if value is None:
        return True
    return isinstance(value, str) and not value.strip()


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_DIGEST_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


def verify_artifact(directory: Path, *, record_name: str = "provenance.json") -> VerifiedArtifact:
    """Verify every recorded digest and return the artifact.

    Every file named in the record is checked, not only the weights. A tokenizer
    swapped for one at another revision makes the *measured* sequence length and
    the *consumed* length disagree with no error anywhere — precisely the
    failure a digest on the graph alone would miss.

    Raises:
        ArtifactError: The record is absent or malformed, a required provenance
            field is missing, a named file is missing, or a digest does not
            match. Every one is a refusal rather than a warning: this runs
            *before* session creation so that unverified bytes never reach the
            runtime, and a check that returns a session anyway is decoration.
    """
    record_path = directory / record_name
    try:
        document = json.loads(record_path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ArtifactError(f"cannot read the provenance record at {record_path}: {exc}") from exc
    except ValueError as exc:
        raise ArtifactError(f"{record_path} is not valid JSON: {exc}") from exc
    if not isinstance(document, dict):
        raise ArtifactError(f"{record_path} must hold a JSON object")

    absent = [field for field in _REQUIRED_FIELDS if not document.get(field)]
    if absent:
        msg = (
            f"{record_path} is missing required provenance: {sorted(absent)}. "
            f"Data Provenance requires identity, revision, licence basis and source "
            f"of every vendored artifact — a verified digest without them is "
            f"reproducible and unshippable."
        )
        raise ArtifactError(msg)

    files = document["files"]
    if not isinstance(files, dict) or not files:
        raise ArtifactError(f"{record_path}: files must be a non-empty JSON object")

    generated = document.get("generated")
    if generated is not None:
        if not isinstance(generated, dict):
            raise ArtifactError(f"{record_path}: generated must be a JSON object")
        missing_generated = [f for f in _REQUIRED_GENERATED_FIELDS if _absent(generated, f)]
        if missing_generated:
            msg = (
                f"{record_path} declares a generated artifact but omits "
                f"{sorted(missing_generated)}. A generated graph without its generator, "
                f"seed and source hash cannot be reproduced, and its provenance stops "
                f"at 'someone generated something'."
            )
            raise ArtifactError(msg)

    missing: list[str] = []
    altered: list[str] = []
    for name in sorted(files):
        expected = files[name]
        path = directory / name
        if not path.is_file():
            missing.append(name)
            continue
        if _sha256(path) != expected:
            altered.append(name)
    if missing or altered:
        parts = []
        if missing:
            parts.append(f"missing: {missing}")
        if altered:
            parts.append(f"digest mismatch: {altered}")
        msg = f"{directory} does not match {record_name} — " + "; ".join(parts)
        raise ArtifactError(msg)

    return VerifiedArtifact(
        directory=directory,
        model_id=str(document["model_id"]),
        revision=str(document["revision"]),
        licence_basis=str(document["licence_basis"]),
        source=str(document["source"]),
        files=dict(files),
        generated=dict(generated) if generated is not None else None,
    )
