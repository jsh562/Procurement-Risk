"""The cross-encoder reranker: two graphs, both resident, scored jointly.

Spec FR-015 to FR-019, FR-025, FR-038. A cross-encoder scores the query and each
candidate **together** in one forward pass, which is why it can rank better than
the bi-encoder that produced the vectors — and why it costs more: there is no
precomputable per-document representation, so every candidate is a forward pass.

**Both graphs load, and that is not redundancy.** AD-011 ships INT8 for serving
and FP32 for measuring what quantization costs, and AD-013 keeps both resident
because FR-017 forbids loading a graph on a request path. Quantization is
explicitly not lossless: FR-025 makes the full-precision arm request-selectable
so the cost is *measured* rather than asserted.

**Warm-up runs at the maximum shape, before readiness.** Memory-pattern
optimisation is documented as effective only for static shapes, so under the
variable sequence lengths a real query produces, warm-up buys arena growth,
page-in and first-run graph initialisation rather than buffer planning. Which is
still worth having: without it the first request after every deploy pays them
inside the latency budget and reads as an outlier nobody can explain.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Final

import numpy as np
import numpy.typing as npt

from gateway.inference.artifacts import verify_artifact
from gateway.inference.encoder import BatchTokenizer, InferenceSession, load_tokenizer
from gateway.inference.session import session_for

__all__ = [
    "DEFAULT_SEQUENCE_LIMIT",
    "Precision",
    "RerankerSession",
    "TruncationReport",
    "load_reranker",
]

#: The cross-encoder's declared maximum sequence length. Query and candidate are
#: encoded as one pair, so this bounds the two together — a long candidate and a
#: long query compete for the same budget.
DEFAULT_SEQUENCE_LIMIT: Final = 512


class Precision(StrEnum):
    """Which graph a score came from.

    Carried on every reported figure, because FR-025's whole point is that an
    INT8 number and an FP32 number are different measurements of the same
    ranking — and a figure that did not say which is unusable for the
    comparison it exists to support.
    """

    INT8 = "int8"
    FP32 = "fp32"


#: Which file holds which precision, in the vendored artifact.
_GRAPH_FOR: Final = {
    Precision.INT8: "model-int8.onnx",
    Precision.FP32: "model-fp32.onnx",
}


@dataclass(frozen=True)
class TruncationReport:
    """How many candidates were cut, and what the lengths looked like.

    Spec FR-019. The distribution travels with the count because a truncated
    fraction alone cannot say whether the cut removed a trailing clause or half
    the passage — and reranking a passage whose evidence was truncated away
    produces a confident score for text the model never saw.
    """

    sequence_limit: int
    candidate_token_lengths: tuple[int, ...]
    truncated_count: int

    @property
    def truncated_fraction(self) -> float:
        """Share of scored candidates that hit the limit.

        A **census** over the candidates actually scored, not an estimate — so
        it publishes its exact denominator and no interval, per FR-051.
        """
        if not self.candidate_token_lengths:
            msg = "no candidates were scored; the truncated fraction is undefined"
            raise ValueError(msg)
        return self.truncated_count / len(self.candidate_token_lengths)


@dataclass(frozen=True)
class RerankerSession:
    """One loaded graph, its tokenizer, and the shape it was warmed at."""

    session: InferenceSession
    tokenizer: BatchTokenizer
    precision: Precision
    sequence_limit: int
    warmed_batch: int
    warmed_sequence: int
    model_id: str
    revision: str

    def score(
        self,
        query: str,
        candidates: Sequence[str],
    ) -> tuple[npt.NDArray[np.float32], TruncationReport]:
        """Score `query` against each candidate jointly, in one batch.

        Returns the raw logits and what was truncated. **Not** a sorted order:
        sorting by a score the model already produced is ordering, not ranking
        arithmetic, and AD-005 puts it outside the computation boundary — which
        is what stops that carve-out being widened later to admit the scoring
        itself.
        """
        if not candidates:
            return (
                np.zeros((0,), dtype=np.float32),
                TruncationReport(self.sequence_limit, (), 0),
            )
        pairs = [(query, candidate) for candidate in candidates]
        encodings = self.tokenizer.encode_batch(pairs)
        lengths = tuple(len(encoding.ids) for encoding in encodings)
        # A candidate is truncated when its encoding reached the limit. Counted
        # rather than inferred from the text length, because the limit is in
        # word pieces and a character count answers a different question.
        truncated = sum(1 for length in lengths if length >= self.sequence_limit)
        wanted = {value.name for value in self.session.get_inputs()}
        feed: dict[str, npt.NDArray[Any]] = {
            "input_ids": np.array([e.ids for e in encodings], dtype=np.int64),
            "attention_mask": np.array([e.attention_mask for e in encodings], dtype=np.int64),
        }
        if "token_type_ids" in wanted:
            feed["token_type_ids"] = np.array([e.type_ids for e in encodings], dtype=np.int64)
        raw = self.session.run(None, feed)[0]
        scores: npt.NDArray[np.float32] = np.asarray(raw, dtype=np.float32).reshape(-1)
        return scores, TruncationReport(self.sequence_limit, lengths, truncated)


def load_reranker(
    directory: Path,
    precision: Precision,
    *,
    intra_op_threads: int = 1,
    inter_op_threads: int = 1,
    sequence_limit: int = DEFAULT_SEQUENCE_LIMIT,
    warm_batch: int = 50,
) -> RerankerSession:
    """Verify, load and **warm** one reranker graph.

    Warmed here rather than on first use, because FR-017 withholds readiness
    until warm-up completes: a session that warms lazily moves the cost into
    whichever request arrives first, and that request is indistinguishable from
    a slow one.

    Warmed at the **maximum** shape FR-017 fixes numerically — batch equal to
    the reranked count of 50, sequence equal to the declared limit — rather than
    at a typical shape, because the arena has to grow to the largest allocation
    it will ever serve and a small warm-up leaves that growth for a real query.
    """
    artifact = verify_artifact(directory)
    graph = artifact.path(_GRAPH_FOR[precision])
    tokenizer = load_tokenizer(artifact.path("tokenizer.json"), truncate_at=sequence_limit)
    session = session_for(
        graph,
        intra_op_threads=intra_op_threads,
        inter_op_threads=inter_op_threads,
    )
    loaded = RerankerSession(
        session=session,
        tokenizer=tokenizer,
        precision=precision,
        sequence_limit=sequence_limit,
        warmed_batch=warm_batch,
        warmed_sequence=sequence_limit,
        model_id=artifact.model_id,
        revision=artifact.revision,
    )
    # The warm-up pass. Text long enough to reach the sequence limit, repeated
    # to the full batch, so both dimensions of the arena are exercised.
    filler = "specification section " * (sequence_limit // 2)
    loaded.score(filler, [filler] * warm_batch)
    return loaded
