"""The ONNX Runtime session, and the pooling and normalization it does not do.

FR-019 / FR-020 / ADR-0018 / HINT-005. A `sentence-transformers` model is a
transformer **plus** a mean-pooling module weighted by the attention mask
**plus** an L2-normalization module. A raw ONNX export emits token-level hidden
states and stops there, so the last two modules are repository code — this file
— and they are the part of the vector computation this project can get wrong.

**The mask is the whole risk.** Pooling over padding produces vectors that are
well-formed, well-scaled, plausible, and quietly wrong: retrieval simply gets
worse, permanently, with nothing raising and no metric isolating the cause. That
is why ADR-0018 makes the parity tolerance mandatory rather than diligent, and
why `src/model/tests/ingest/test_encoder_parity.py` asserts it against
independently produced reference vectors instead of against this module's own
output.

**Three things are taken from the artifact, not typed here**: the graph, the
tokenizer, and the sequence cap. All three are digest-verified before the
session exists (`artifacts.verified_encoder`), and a mismatch fails the run
naming the artifact rather than fetching a replacement.

**Truncation is configured, deliberately.** Chunks are cut to 254 content pieces
upstream, so truncation should never fire on a chunk — but the reference encoder
truncates at 256 and parity must hold on inputs that reach the cap, including
the probe set's over-length members. Silently *not* truncating would make this
module and the reference disagree on exactly those inputs, which is the
disagreement the parity check exists to detect. The counting tokenizer in
`tokens.py` is a **separate instance with truncation off** — measuring length
with a truncating tokenizer would report the cap as the length and let an
over-long unit measure as fitting.

Determinism: single-threaded session options and a fixed batch order, so two
runs over the same chunks produce the same vectors. ONNX Runtime's CPU provider
is used explicitly rather than by default discovery.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

from model.ingest.artifacts import (
    ArtifactError,
    EncoderArtifact,
    artifact_path,
    verified_encoder,
)

__all__ = [
    "DEFAULT_BATCH_SIZE",
    "EmbedError",
    "ParityMeasurement",
    "embed_chunks",
    "embedding_identity",
    "encoder_session",
    "parity_against_reference",
]

#: Chosen for memory, not for throughput: ingestion is offline and the 400 MB
#: request-time envelope binds E008's reuse of this encoder, not this job.
DEFAULT_BATCH_SIZE = 16

#: The tokenizer's padding token. Named, not a credential -- S106/S105 reads any
#: string literal bound to a name ending in TOKEN as one.
_PAD_TOKEN = "[PAD]"  # noqa: S105


class EmbedError(ValueError):
    """Raised when chunks cannot be embedded.

    One type for every failure: a caller learns the same thing from each of them
    — no vector may be written for these chunks, and the run fails.
    """


@lru_cache(maxsize=1)
def _artifact() -> EncoderArtifact:
    try:
        return verified_encoder()
    except ArtifactError as exc:
        raise EmbedError(str(exc)) from exc


@lru_cache(maxsize=1)
def encoder_session() -> ort.InferenceSession:
    """The ONNX Runtime session, created only after the artifact verifies.

    Single-threaded on purpose. Thread-count variation changes floating-point
    reduction order, and a reproducibility gate published to six decimal places
    should not depend on how many cores the runner happened to have.
    """
    artifact = _artifact()
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_BASIC
    try:
        return ort.InferenceSession(
            str(artifact.graph),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
    except Exception as exc:  # noqa: BLE001 - any session failure is this module's
        raise EmbedError(
            f"cannot create an ONNX Runtime session for {artifact.graph}: {exc}"
        ) from exc


@lru_cache(maxsize=1)
def _embedding_tokenizer() -> Tokenizer:
    """The **embedding** tokenizer: truncating at the cap, padding per batch.

    A second instance rather than a second file — the same digest-verified
    `tokenizer.json`, configured for what the session needs. `tokens.py`'s
    instance stays non-truncating because it is measuring, not feeding.
    """
    artifact = _artifact()
    try:
        tokenizer = Tokenizer.from_file(str(artifact.tokenizer))
    except Exception as exc:  # noqa: BLE001 - any load failure is this module's
        raise EmbedError(f"cannot load the tokenizer at {artifact.tokenizer}: {exc}") from exc
    tokenizer.enable_truncation(max_length=artifact.sequence_cap)
    pad_id = tokenizer.token_to_id(_PAD_TOKEN)
    if pad_id is None:
        raise EmbedError(f"the committed tokenizer declares no {_PAD_TOKEN} token")
    tokenizer.enable_padding(pad_id=pad_id, pad_token=_PAD_TOKEN)
    return tokenizer


def embedding_identity() -> tuple[str, str]:
    """FR-020's `(model id, revision)` for every chunk this session embeds."""
    artifact = _artifact()
    return artifact.model_id, artifact.revision


def _masked_mean(hidden: np.ndarray, mask: np.ndarray) -> np.ndarray:
    """Attention-masked mean pooling — the reference's `Pooling` module.

    `hidden` is `(batch, sequence, dimension)` and `mask` is `(batch, sequence)`
    holding 1 for a real piece and 0 for padding. The mask is broadcast over the
    dimension axis and multiplied in **before** the sum, so a padded position
    contributes nothing to the numerator, and the denominator is the count of
    real pieces rather than the padded sequence length. Getting either half
    wrong is the quiet failure HINT-005 names.
    """
    weights = mask.astype(np.float32)[:, :, None]
    summed = (hidden * weights).sum(axis=1)
    counts = weights.sum(axis=1)
    # A row with no unmasked piece cannot arise from a non-empty chunk, but
    # dividing by zero would produce NaNs that propagate into stored vectors
    # rather than raising, so the denominator is clamped exactly as the
    # reference implementation clamps it.
    return summed / np.clip(counts, 1e-9, None)


def _l2_normalize(vectors: np.ndarray) -> np.ndarray:
    """L2 normalization — the reference's `Normalize` module.

    ADR-0012 requires normalized vectors, and cosine distance in pgvector is
    only the inner product when they are.
    """
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    return vectors / np.clip(norms, 1e-12, None)


def embed_chunks(
    texts: Sequence[str],
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
) -> np.ndarray:
    """Embed chunk texts into `(n, dimension)` L2-normalized float32 vectors.

    Order is preserved: row *i* is `texts[i]`. Batching is a memory decision and
    must not be an arithmetic one, so batch boundaries change nothing about a
    row's value — each row is pooled over its own mask alone.
    """
    if isinstance(texts, str):
        raise EmbedError("embed_chunks takes a sequence of texts, not a single string")
    ordered = list(texts)
    artifact = _artifact()
    if not ordered:
        return np.zeros((0, artifact.vector_dimension), dtype=np.float32)
    for index, text in enumerate(ordered):
        if not isinstance(text, str):
            raise EmbedError(f"texts[{index}] must be a string, found {type(text).__name__}")
    if batch_size < 1:
        raise EmbedError(f"batch_size must be positive, found {batch_size}")

    session = encoder_session()
    tokenizer = _embedding_tokenizer()
    wanted = {value.name for value in session.get_inputs()}
    produced: list[np.ndarray] = []

    for start in range(0, len(ordered), batch_size):
        batch = ordered[start : start + batch_size]
        encodings = tokenizer.encode_batch(batch)
        input_ids = np.array([encoding.ids for encoding in encodings], dtype=np.int64)
        attention_mask = np.array(
            [encoding.attention_mask for encoding in encodings], dtype=np.int64
        )
        feed: dict[str, np.ndarray] = {
            "input_ids": input_ids,
            "attention_mask": attention_mask,
        }
        if "token_type_ids" in wanted:
            feed["token_type_ids"] = np.array(
                [encoding.type_ids for encoding in encodings], dtype=np.int64
            )
        try:
            hidden = session.run(["last_hidden_state"], feed)[0]
        except Exception as exc:  # noqa: BLE001 - any inference failure is this module's
            raise EmbedError(
                f"the encoder session failed on a batch of {len(batch)}: {exc}"
            ) from exc
        produced.append(_l2_normalize(_masked_mean(hidden, attention_mask)))

    vectors = np.concatenate(produced, axis=0).astype(np.float32)
    if vectors.shape != (len(ordered), artifact.vector_dimension):
        raise EmbedError(
            f"FR-021: expected {(len(ordered), artifact.vector_dimension)} vectors, "
            f"produced {vectors.shape}"
        )
    return vectors


# ---------------------------------------------------------------------------
# The parity measurement (FR-019, ADR-0018)
# ---------------------------------------------------------------------------

_PROBES = "probes.json"
_REFERENCE = "parity-reference.json"


@dataclass(frozen=True)
class ParityMeasurement:
    """What the export scored against the reference, and against what bounds.

    Both the declared bounds and the observed maxima are carried, because
    FR-019 requires them **published together**: a tolerance reported without
    its observation is unfalsifiable, and an observation reported without its
    declared bound cannot be read as a pass or a fail.
    """

    declared_cosine_minimum: float
    declared_max_absolute_difference: float
    observed_minimum_cosine: float
    observed_maximum_absolute_difference: float
    per_probe: tuple[tuple[str, str, float, float], ...]
    reference: Mapping[str, object]

    @property
    def layers(self) -> frozenset[str]:
        return frozenset(layer for _, layer, _, _ in self.per_probe)

    @property
    def within_bounds(self) -> bool:
        return (
            self.observed_minimum_cosine >= self.declared_cosine_minimum
            and self.observed_maximum_absolute_difference <= self.declared_max_absolute_difference
        )


def _committed_json(name: str) -> Mapping[str, object]:
    verified_encoder()
    path = artifact_path(name)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EmbedError(f"cannot read {path}: {exc}") from exc
    if not isinstance(document, dict):
        raise EmbedError(f"{path} must hold a JSON object")
    return document


def parity_against_reference() -> ParityMeasurement:
    """Measure the export against the committed reference over the probe set.

    The reference vectors were produced once by the reference implementation and
    committed, so this runs offline from a clean checkout — which is what lets
    the assertion be a build-time gate rather than a thing someone re-derives by
    installing a second inference stack.

    Cosine is computed as a plain inner product **after** re-normalizing both
    sides: both are already unit vectors, so the product is the cosine, and
    re-normalizing costs nothing and removes any dependence on how the committed
    file was rounded.
    """
    probes = _committed_json(_PROBES)
    reference = _committed_json(_REFERENCE)
    entries = probes.get("probes")
    bounds = probes.get("declared_bounds")
    vectors_by_id = reference.get("vectors")
    if not isinstance(entries, list) or not entries:
        raise EmbedError(f"{_PROBES}: probes must be a non-empty array")
    if not isinstance(bounds, dict):
        raise EmbedError(f"{_PROBES}: declared_bounds must be a JSON object")
    if not isinstance(vectors_by_id, dict):
        raise EmbedError(f"{_REFERENCE}: vectors must be a JSON object")

    produced = _l2_normalize(embed_chunks([str(entry["text"]) for entry in entries]))
    per_probe: list[tuple[str, str, float, float]] = []
    for row, entry in zip(produced, entries, strict=True):
        probe_id = str(entry["probe_id"])
        recorded = vectors_by_id.get(probe_id)
        if not isinstance(recorded, list):
            raise EmbedError(f"{_REFERENCE}: no reference vector for probe {probe_id!r}")
        expected = _l2_normalize(np.array([recorded], dtype=np.float32))[0]
        per_probe.append(
            (
                probe_id,
                str(entry["layer"]),
                float(np.dot(row, expected)),
                float(np.abs(row - expected).max()),
            )
        )

    return ParityMeasurement(
        declared_cosine_minimum=float(bounds["cosine_similarity_min"]),
        declared_max_absolute_difference=float(bounds["max_absolute_per_dimension_difference"]),
        observed_minimum_cosine=min(cosine for _, _, cosine, _ in per_probe),
        observed_maximum_absolute_difference=max(diff for _, _, _, diff in per_probe),
        per_probe=tuple(per_probe),
        reference=dict(reference.get("reference_implementation") or {}),
    )
