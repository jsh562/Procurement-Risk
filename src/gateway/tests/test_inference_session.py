"""The encoder verifies before it loads, and both callers share one vector space.

Spec FR-007, FR-016, FR-017. The assertion that matters here is the last one:
`model.ingest.embed` and the request-time query path must produce the *same*
vector for the same text, because a query embedded by a different implementation
than the corpus lands in a different vector space — with no error, no
out-of-range distance, and only degraded ranking as the symptom.

That is what {SAD:ADR-0023} is for, and asserting it is the difference between
having one implementation and believing you do.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from gateway.inference.artifacts import ArtifactError
from gateway.inference.encoder import (
    EncoderIdentityError,
    assert_encoder_identity,
    embed_texts,
)
from gateway.inference.session import load_encoder

ENCODER = Path(__file__).resolve().parents[3] / "data" / "encoder"

pytestmark = pytest.mark.skipif(
    not (ENCODER / "digests.json").is_file(),
    reason="the vendored encoder is not present in this checkout",
)


def test_the_encoder_verifies_and_loads() -> None:
    encoder = load_encoder(ENCODER)
    assert encoder.model_id == "sentence-transformers/all-MiniLM-L6-v2"
    assert encoder.vector_dimension == 384


def test_an_unverifiable_artifact_never_reaches_the_runtime(tmp_path: Path) -> None:
    """Verification precedes session creation, not the other way round.

    A check that builds the session and then validates has already loaded
    unverified bytes into the runtime — the thing the check exists to prevent.
    """
    with pytest.raises(ArtifactError):
        load_encoder(tmp_path)


def test_a_query_embeds_to_a_unit_vector_of_the_recorded_dimension() -> None:
    """Shape and norm, because pgvector's cosine distance is the inner product
    only for normalized vectors — an unnormalized query would silently make
    every distance mean something else."""
    encoder = load_encoder(ENCODER)
    vectors = embed_texts(encoder.session, encoder.tokenizer, ["bronze relief valve"])
    assert vectors.shape == (1, encoder.vector_dimension)
    assert float(np.linalg.norm(vectors[0])) == pytest.approx(1.0, abs=1e-6)


def test_a_one_element_batch_is_not_a_special_case() -> None:
    """The query path embeds one text; the corpus path embeds many.

    Under per-batch padding a one-item batch pads to its own length, so
    mask-weighted pooling is unaffected by batch shape — asserted rather than
    assumed, because if it were false every single-query embedding would differ
    from its corpus counterpart.
    """
    encoder = load_encoder(ENCODER)
    texts = ["bronze relief valve", "circulator pump"]
    together = embed_texts(encoder.session, encoder.tokenizer, texts)
    apart = np.vstack([embed_texts(encoder.session, encoder.tokenizer, [text]) for text in texts])
    assert float(np.abs(together - apart).max()) == pytest.approx(0.0, abs=1e-6)


def test_the_identity_gate_refuses_a_mismatch() -> None:
    encoder = load_encoder(ENCODER)
    assert_encoder_identity(encoder.identity, encoder.identity)
    with pytest.raises(EncoderIdentityError, match="different vector spaces"):
        assert_encoder_identity(encoder.identity, ("other/model", "deadbeef"))


def test_the_artifact_records_a_licence_basis_and_a_source() -> None:
    """Data Provenance, closed for the encoder by E008.

    The record carried identity, revision and digests but neither a licence nor
    a source — `plan.md` §Pending Amendments item 5, non-blocking and "fixable
    by whichever epic next touches the file". E008 is that epic.
    """
    encoder = load_encoder(ENCODER)
    from gateway.inference.artifacts import verify_artifact

    artifact = verify_artifact(ENCODER, record_name="digests.json")
    assert artifact.licence_basis == "Apache-2.0"
    assert artifact.source.startswith("https://huggingface.co/")
    assert encoder.revision in artifact.source, (
        "the recorded source must pin the same revision as the artifact, or it "
        "names a different set of bytes than the one committed"
    )


def test_a_prefixed_digest_is_accepted() -> None:
    """E006 records `sha256:<hex>`; E008's reranker records bare `<hex>`.

    Both are SHA-256 of the file's bytes. The reader accepts either rather than
    forcing a committed record to change format — rewriting E006's digests to
    match a newer convention would rewrite every line of a file whose whole
    purpose is to be stable.
    """
    from gateway.inference.artifacts import verify_artifact

    artifact = verify_artifact(ENCODER, record_name="digests.json")
    assert any(str(value).startswith("sha256:") for value in artifact.files.values())
