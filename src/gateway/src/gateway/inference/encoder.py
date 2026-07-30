"""Masked mean pooling, L2 normalization, and the encoder identity gate.

Spec FR-007. {SAD:ADR-0023} puts this here so that **exactly one** pooling
implementation exists in the repository. A `sentence-transformers` model is a
transformer *plus* a mask-weighted mean-pooling module *plus* an
L2-normalization module; a raw ONNX export emits token-level hidden states and
stops, so the last two are repository code — and they are the part of the vector
computation this project can get wrong.

**The mask is the whole risk.** Pooling over padding produces vectors that are
well-formed, well-scaled, plausible and quietly wrong: retrieval simply gets
worse, permanently, with nothing raising and no metric isolating the cause. Two
copies of that arithmetic kept in step by review is the same failure with a
second place to introduce it, which is why E006's ingest path imports from here
rather than keeping its own.

**No query prefix.** The vendored encoder declares none — prefix conventions
belong to asymmetric or prompt-declaring models and this one is neither. Adding
one would move the query off the chunks' vector space silently, with no error
and only degraded ranking as the symptom. That a symmetric encoder on a
short-query, long-passage task is a quality ceiling is a *measured* property of
the dense arm, not a defect to paper over with a prefix.

**Truncation is configured, deliberately.** The reference encoder truncates at
the cap, and parity must hold on inputs that reach it. The *counting* tokenizer
in `model.ingest.tokens` is a separate instance with truncation off: measuring
length with a truncating tokenizer reports the cap as the length and lets an
over-long unit measure as fitting.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Protocol

import numpy as np
import numpy.typing as npt

__all__ = [
    "EncoderIdentityError",
    "TokenizerLoadError",
    "assert_encoder_identity",
    "embed_texts",
    "l2_normalize",
    "load_tokenizer",
    "masked_mean",
]

#: The tokenizer's padding token. Named, not a credential — S105/S106 reads any
#: string literal bound to a name ending in TOKEN as one.
_PAD_TOKEN = "[PAD]"  # noqa: S105


class TokenizerLoadError(RuntimeError):
    """The committed tokenizer cannot be loaded or lacks a padding token."""


class _NamedInput(Protocol):
    name: str


class InferenceSession(Protocol):
    """The slice of an ONNX Runtime session this module uses.

    A protocol rather than the concrete type, so the arithmetic here is
    testable against a stub and this package does not have to import the
    runtime to describe what it needs from it.
    """

    def get_inputs(self) -> Sequence[_NamedInput]: ...

    def run(self, output_names: Sequence[str] | None, input_feed: Any) -> Sequence[Any]: ...

    # `output_names=None` means "every output", which is what the cross-encoder
    # uses: it has one output and naming it would hard-code a graph detail this
    # package does not otherwise depend on.


class BatchTokenizer(Protocol):
    """The slice of a tokenizer this module uses.

    `inputs` is a sequence of strings for the bi-encoder and a sequence of
    `(query, candidate)` pairs for the cross-encoder. Both are what
    `Tokenizer.encode_batch` accepts, and the difference is the whole reason a
    cross-encoder can rank better: it sees the two together in one forward pass
    rather than comparing two independently computed vectors.
    """

    def encode_batch(self, inputs: Sequence[Any]) -> Sequence[Any]: ...


def load_tokenizer(path: object, *, truncate_at: int | None) -> BatchTokenizer:
    """Load the committed tokenizer, configured for what the caller is doing.

    `truncate_at` is **required**, with no default, because the two callers need
    opposite settings and neither is safe to assume:

    - **Embedding** passes the sequence cap. The reference encoder truncates
      there, and parity must hold on inputs that reach it — silently *not*
      truncating would make this implementation and the reference disagree on
      exactly those inputs, which is the disagreement the parity gate exists to
      detect.
    - **Counting** passes `None`. Measuring length with a truncating tokenizer
      reports the cap as the length, so an over-long unit measures as fitting —
      the length check would then pass on precisely the units that break it.

    Two instances of one digest-verified `tokenizer.json`, not two files. This
    function lives in the gateway because `tokenizers` is declared here from
    {SAD:ADR-0023}: the modeling entry stopped declaring it and must not import
    a distribution it does not declare, which is what "each entry keeps an
    independent dependency manifest" means in practice.
    """
    from tokenizers import Tokenizer

    try:
        tokenizer: Any = Tokenizer.from_file(str(path))
    except Exception as exc:  # noqa: BLE001 - any load failure is this function's
        raise TokenizerLoadError(f"cannot load the tokenizer at {path}: {exc}") from exc
    if truncate_at is None:
        tokenizer.no_truncation()
        tokenizer.no_padding()
        counting: BatchTokenizer = tokenizer
        return counting
    tokenizer.enable_truncation(max_length=truncate_at)
    pad_id = tokenizer.token_to_id(_PAD_TOKEN)
    if pad_id is None:
        raise TokenizerLoadError(f"the committed tokenizer declares no {_PAD_TOKEN} token")
    tokenizer.enable_padding(pad_id=pad_id, pad_token=_PAD_TOKEN)
    embedding: BatchTokenizer = tokenizer
    return embedding


class EncoderIdentityError(RuntimeError):
    """The query encoder is not the encoder the stored vectors were made with."""


def masked_mean(hidden: npt.NDArray[Any], mask: npt.NDArray[Any]) -> npt.NDArray[np.float32]:
    """Attention-masked mean pooling — the reference's `Pooling` module.

    `hidden` is `(batch, sequence, dimension)`; `mask` is `(batch, sequence)`
    holding 1 for a real piece and 0 for padding. The mask is broadcast over the
    dimension axis and multiplied in **before** the sum, so a padded position
    contributes nothing to the numerator, and the denominator is the count of
    real pieces rather than the padded sequence length. Getting either half
    wrong is the quiet failure this module exists to have exactly one of.
    """
    weights = mask.astype(np.float32)[:, :, None]
    summed = (hidden * weights).sum(axis=1)
    counts = weights.sum(axis=1)
    # A row with no unmasked piece cannot arise from a non-empty text, but
    # dividing by zero would produce NaNs that propagate into stored vectors
    # rather than raising, so the denominator is clamped exactly as the
    # reference implementation clamps it.
    pooled: npt.NDArray[np.float32] = summed / np.clip(counts, 1e-9, None)
    return pooled


def l2_normalize(vectors: npt.NDArray[Any]) -> npt.NDArray[np.float32]:
    """L2 normalization — the reference's `Normalize` module.

    Normalized vectors are what make pgvector's cosine distance the inner
    product; without this the stored geometry and the queried geometry differ.
    """
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    unit: npt.NDArray[np.float32] = vectors / np.clip(norms, 1e-12, None)
    return unit


def embed_texts(
    session: InferenceSession,
    tokenizer: BatchTokenizer,
    texts: Sequence[str],
    *,
    batch_size: int = 16,
) -> npt.NDArray[np.float32]:
    """Embed `texts` into `(n, dimension)` L2-normalized float32 vectors.

    The session and tokenizer are **passed in**, not reached for. The two
    callers verify different artifact sets — E006's ingest path resolves the
    encoder through `model.ingest.artifacts`, and the serving path through
    `gateway.inference.artifacts` — and a module that reached for either would
    have to know which entry it was running in.

    Order is preserved: row *i* is `texts[i]`. Batching is a memory decision and
    must not become an arithmetic one, so batch boundaries change nothing about
    a row's value — each row is pooled over its own mask alone. A one-element
    batch pads to its own length, which is why the query path can call this with
    a single text and get the same vector the corpus path would have produced.
    """
    ordered = list(texts)
    if not ordered:
        return np.zeros((0, 0), dtype=np.float32)
    wanted = {value.name for value in session.get_inputs()}
    produced: list[npt.NDArray[np.float32]] = []

    for start in range(0, len(ordered), batch_size):
        batch = ordered[start : start + batch_size]
        encodings = tokenizer.encode_batch(batch)
        feed: dict[str, npt.NDArray[Any]] = {
            "input_ids": np.array([e.ids for e in encodings], dtype=np.int64),
            "attention_mask": np.array([e.attention_mask for e in encodings], dtype=np.int64),
        }
        if "token_type_ids" in wanted:
            feed["token_type_ids"] = np.array([e.type_ids for e in encodings], dtype=np.int64)
        hidden: npt.NDArray[Any] = session.run(["last_hidden_state"], feed)[0]
        produced.append(l2_normalize(masked_mean(hidden, feed["attention_mask"])))

    stacked: npt.NDArray[np.float32] = np.concatenate(produced, axis=0).astype(np.float32)
    return stacked


def assert_encoder_identity(
    query_identity: tuple[str, str],
    corpus_identity: tuple[str, str],
) -> None:
    """Refuse retrieval when the query encoder is not the corpus encoder.

    Spec FR-007, and Principle III: where a mistake would be invisible, refuse
    rather than answer. A query embedded by a different model or revision than
    the stored vectors lands in a different vector space. Nothing raises, no
    distance is out of range, and every result looks like a result — the dense
    arm simply returns near-arbitrary neighbours, and the only symptom is
    ranking that is worse than it should be, which is indistinguishable from a
    hard retrieval problem.

    Checked **before any search runs**, not at result assembly, so no work is
    done and no figure is produced under a mismatch that could later be read as
    a measurement.
    """
    if query_identity != corpus_identity:
        q_model, q_revision = query_identity
        c_model, c_revision = corpus_identity
        msg = (
            f"the query encoder ({q_model}@{q_revision}) is not the encoder the stored "
            f"vectors were produced with ({c_model}@{c_revision}). Refusing rather than "
            f"searching: the two embed into different vector spaces, so every distance "
            f"would be well-formed and meaningless."
        )
        raise EncoderIdentityError(msg)
