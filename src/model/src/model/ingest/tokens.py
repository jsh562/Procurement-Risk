"""Counting word pieces in the encoder's own tokenizer, against the 254 budget.

FR-014 / AD-004 / HINT-001. Chunk length is measured in the pieces the model
actually consumes, because the encoder truncates to its sequence cap **with no
signal** — a too-long chunk still yields a well-formed 384-dimensional vector
representing only its head, and nothing downstream can detect it (research
§Reconciling structural boundaries with the 256 word-piece cap).

**The cap is read, not guessed, and the field it is read from is the one that
matters.** `tokenizer_config.json` carries `model_max_length: 512`; that is the
wrong field and reading it doubles the budget silently. The effective cap is
`max_seq_length` in `sentence_bert_config.json` — **256** — and it counts the
`[CLS]` and `[SEP]` pieces the tokenizer adds. So the *content* budget is
**254**. Both halves are derived here rather than written down twice: the cap is
read from the committed config, the special-token overhead is **measured** by
encoding the empty string, and the difference is checked against the declared
254 at load. A revision whose tokenizer adds a third special piece therefore
fails loudly instead of shipping chunks two pieces over.

**Truncation is disabled on the counting tokenizer.** A tokenizer left with the
library's truncation enabled would clip its own output at the cap and report the
cap as the length — so an over-long unit would measure as exactly fitting, which
is the precise failure this module exists to prevent, arriving through the
instrument.

`add_special_tokens` is left at its default (`True`), which is what makes
`len(encoding.ids)` the number the model consumes; the content count subtracts
the measured overhead rather than calling a piece-only API, because
`tokenize()`-style APIs undercount by exactly the two pieces that decide whether
a unit fits.
"""

from __future__ import annotations

import json
from functools import lru_cache

from tokenizers import Tokenizer

from model.ingest.artifacts import ArtifactError, artifact_path, verified_encoder

__all__ = [
    "CONTENT_TOKEN_BUDGET",
    "TokenizerError",
    "content_pieces",
    "effective_sequence_cap",
    "encoder_tokenizer",
    "fits_budget",
    "special_token_overhead",
]

#: AD-004's number, declared rather than computed so the computed one has
#: something to disagree with. 256 effective cap less two special pieces.
CONTENT_TOKEN_BUDGET = 254

_SEQUENCE_CONFIG = "sentence_bert_config.json"
_TOKENIZER_FILE = "tokenizer.json"


class TokenizerError(ValueError):
    """Raised when the pinned tokenizer is absent, unreadable, or disagrees.

    One type for every failure: a caller learns the same thing from each of them
    — chunk length cannot be measured in the encoder's own pieces, so no chunk
    may be cut and no vector may be written.
    """


@lru_cache(maxsize=1)
def encoder_tokenizer() -> Tokenizer:
    """The pinned tokenizer, loaded once per process from the committed files.

    Cached because the Rust pipeline loads in well under a second but is asked
    for a length several thousand times per run. Truncation and padding are
    turned off explicitly: both would make the reported length a function of the
    tokenizer's configuration rather than of the text.
    """
    # Digest-verified before it is opened (FR-019). A tokenizer at a different
    # revision than the weights makes the *measured* length and the *consumed*
    # length disagree with no error raised anywhere, so verification is a
    # precondition of counting rather than of embedding.
    path = verified_encoder().tokenizer
    if not path.is_file():
        raise TokenizerError(
            f"FR-019: the committed tokenizer is absent at {path}; the run fails rather "
            "than fetching it"
        )
    try:
        tokenizer = Tokenizer.from_file(str(path))
    except Exception as exc:  # noqa: BLE001 - any load failure is this module's
        raise TokenizerError(f"cannot load the tokenizer at {path}: {exc}") from exc
    tokenizer.no_truncation()
    tokenizer.no_padding()
    return tokenizer


@lru_cache(maxsize=1)
def effective_sequence_cap() -> int:
    """`max_seq_length` from the committed sentence-encoder config — 256.

    Deliberately **not** `model_max_length`. The two differ by a factor of two
    and the larger one is the one a reader reaches for first (HINT-001).
    """
    try:
        artifact = verified_encoder()
        path = artifact_path(_SEQUENCE_CONFIG)
    except ArtifactError as exc:
        raise TokenizerError(str(exc)) from exc
    if not path.is_file():
        raise TokenizerError(f"FR-019: the committed sequence config is absent at {path}")
    try:
        config = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise TokenizerError(f"cannot read {path}: {exc}") from exc
    cap = config.get("max_seq_length")
    if not isinstance(cap, int) or isinstance(cap, bool) or cap <= 0:
        raise TokenizerError(f"{path}: max_seq_length must be a positive integer, found {cap!r}")
    if cap != artifact.sequence_cap:
        # Two independent statements of the same number: the encoder's own
        # config, and the artifact record the session and the report read. They
        # are compared rather than one being derived from the other, so a
        # re-vendored artifact whose cap moved cannot pass silently.
        raise TokenizerError(
            f"FR-019: the encoder's own max_seq_length is {cap} but the artifact record "
            f"declares {artifact.sequence_cap}"
        )
    return cap


@lru_cache(maxsize=1)
def special_token_overhead() -> int:
    """How many pieces the tokenizer adds to any input, measured not assumed.

    Encoding the empty string yields exactly the special pieces, which is two
    (`[CLS]`, `[SEP]`) for this BERT-family encoder. Measuring it means a
    tokenizer revision that changes the count is caught by the budget check
    below instead of quietly shifting every boundary by one piece.
    """
    return len(encoder_tokenizer().encode("").ids)


@lru_cache(maxsize=1)
def _budget() -> int:
    computed = effective_sequence_cap() - special_token_overhead()
    if computed != CONTENT_TOKEN_BUDGET:
        raise TokenizerError(
            f"FR-014: the committed artifact gives a content budget of {computed} "
            f"({effective_sequence_cap()} cap less {special_token_overhead()} special "
            f"pieces), but {CONTENT_TOKEN_BUDGET} is declared; a chunker-version bump "
            "and a re-declared budget are required, not a silent adjustment"
        )
    return computed


def content_pieces(text: str) -> int:
    """The number of **content** word pieces the encoder would consume.

    Special pieces are excluded, so the result is directly comparable with
    `CONTENT_TOKEN_BUDGET`. Measured on the exact string that will be embedded
    (research §Counting word pieces): a caller must not normalize afterwards.
    """
    if not isinstance(text, str):
        raise TokenizerError(f"chunk length is measured over a string, found {type(text).__name__}")
    total = len(encoder_tokenizer().encode(text).ids)
    return max(0, total - special_token_overhead())


def fits_budget(text: str) -> bool:
    """True when `text` fits the content budget and needs no further descent."""
    return content_pieces(text) <= _budget()
