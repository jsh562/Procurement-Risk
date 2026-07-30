"""The corpus path and the query path produce the same vector.

Spec FR-007, {SAD:ADR-0023}. This is the assertion the relocation exists for,
and it lives here — in the modeling entry — because this is the only tier that
can import both callers.

**The failure it guards against is silent.** A query embedded by a different
pooling implementation than the corpus lands in a different vector space.
Nothing raises. No distance is out of range. Every result looks like a result,
and the dense arm simply returns near-arbitrary neighbours — indistinguishable
from a hard retrieval problem, and permanent.

Two copies of pooling arithmetic kept in step by review is that failure with a
second place to introduce it. This asserts they are not two copies.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from model.ingest.embed import embed_chunks, embedding_identity

ENCODER = Path(__file__).resolve().parents[4] / "data" / "encoder"

pytestmark = pytest.mark.skipif(
    not (ENCODER / "digests.json").is_file(),
    reason="the vendored encoder is not present in this checkout",
)

TEXTS = [
    "bronze pressure relief valve",
    "Circulator pumps shall be inline, bronze fitted, with mechanical seals.",
]


def _query_path(texts: list[str]) -> np.ndarray:
    from gateway.inference.encoder import embed_texts
    from gateway.inference.session import load_encoder

    encoder = load_encoder(ENCODER)
    return embed_texts(encoder.session, encoder.tokenizer, texts)


def test_the_two_paths_agree_exactly() -> None:
    """Not "within tolerance" — exactly.

    A tolerance would be the right assertion for two *independent*
    implementations. These are one implementation reached through two callers,
    so any difference at all means they are not, and a tolerance would hide
    precisely the drift this test exists to detect.
    """
    corpus = embed_chunks(TEXTS)
    query = _query_path(TEXTS)
    assert float(np.abs(corpus - query).max()) == 0.0


def test_the_two_paths_report_the_same_identity() -> None:
    """FR-007 compares identities to decide whether to refuse.

    If the two callers could report different identities for the same artifact,
    the refusal would fire on a match or pass on a mismatch — either way
    deciding on a comparison of the wrong things.
    """
    from gateway.inference.session import load_encoder

    assert embedding_identity() == load_encoder(ENCODER).identity


def test_a_single_query_matches_its_corpus_counterpart() -> None:
    """The shape the serving path actually uses.

    The corpus path embeds in batches of sixteen; the query path embeds one
    text. If batch shape reached the arithmetic, every single-query embedding
    would differ from the corpus vector for the same text — and the dense arm
    would degrade with nothing reporting it.
    """
    corpus = embed_chunks(TEXTS)
    for index, text in enumerate(TEXTS):
        single = _query_path([text])
        assert float(np.abs(corpus[index] - single[0]).max()) == 0.0
