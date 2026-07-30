"""The retrieval surface: one statement, one embedding, one response.

Spec FR-007, FR-008, FR-009, FR-029. Composition only — the ranking lives in
`fusion.py`, the projection in `results.py`, the disclosures in `report.py`, and
this module wires them to a route.

**The identity gate runs before any search.** FR-007 and Principle III: a query
embedded by a different encoder than the corpus lands in a different vector
space, every distance is well-formed and meaningless, and the symptom is
indistinguishable from a hard retrieval problem. Checked first, so no work is
done and no figure is produced under a mismatch that could later be read as a
measurement.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated, Any

import psycopg
from fastapi import APIRouter, Depends, HTTPException, Query

from api.config import RetrievalConfig, load_retrieval_config
from api.db import connection_options
from api.retrieval.digest import ordering_digest
from api.retrieval.fusion import FUSION_SQL, retrieval_parameters
from api.retrieval.readiness import UnrerankedReason, readiness
from api.retrieval.report import LEXICAL_ARM_NAME, ranking_parameters_in_force
from api.retrieval.results import MatchKind, RetrievalResult, results_from_rows
from api.retrieval.router import recognise_part_numbers, resolve_part_numbers

__all__ = ["MAX_QUERY_CHARACTERS", "corpus_encoder_identity", "router"]

router = APIRouter()

#: FR-046. A longer query is refused rather than truncated: truncating changes
#: what was asked without saying so, and this epic's whole posture on truncation
#: (FR-019) is to count it rather than hide it.
MAX_QUERY_CHARACTERS = 1_000

#: FR-046's default and ceiling for the *ranked* portion. The ceiling is the
#: fetch depth, because a caller cannot ask for more ranked results than the
#: fusion statement retrieves.
DEFAULT_LIMIT = 10


#: Where the vendored encoder lives. Read from the environment with a
#: repository-relative default, not derived from `__file__` alone: the serving
#: image has no repository layout, so a path walked up from this module resolves
#: to nothing once the package is installed. The default is what a developer
#: running from a checkout gets; the image sets the variable.
def _encoder_directory() -> Path:
    configured = os.environ.get("PRC_ENCODER_DIR")
    if configured:
        return Path(configured)
    return Path(__file__).resolve().parents[5] / "data" / "encoder"


#: The columns `results_from_rows` projects, in `PROJECTED_COLUMNS` order, joined
#: onto the fused candidate identifiers.
_PROJECTION_SQL = """
SELECT c.chunk_id, c.document_id, c.document_type, c.project_id,
       c.page_number, c.body_text
FROM chunk c
JOIN unnest(%(ids)s::uuid[]) WITH ORDINALITY AS ordering(chunk_id, position)
  ON ordering.chunk_id = c.chunk_id
ORDER BY ordering.position
"""


def get_config() -> RetrievalConfig:
    """Configuration read once per request, overridable in a test."""
    return load_retrieval_config()


def get_connection(
    config: Annotated[RetrievalConfig, Depends(get_config)],
) -> Iterator[psycopg.Connection]:
    """A connection carrying the search breadth in its options.

    The breadth rides here rather than in a `SET` because FR-002 permits the
    search exactly one statement, and a per-query `SET` would be a second.
    """
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is unset. Retrieval reads the chunk store and has no "
            "meaningful behaviour without it; starting without a database would "
            "answer every query with an empty set that looks like an honest one."
        )
    connection = psycopg.connect(url, options=connection_options(config))
    try:
        yield connection
    finally:
        connection.close()


def corpus_encoder_identity(connection: psycopg.Connection) -> tuple[str, str] | None:
    """The `(model_id, revision)` the stored vectors were produced with.

    Read from the chunk rows themselves rather than from configuration, because
    the question FR-007 asks is what produced *these vectors* — a configured
    value would answer what the process intends, which is the thing already in
    doubt.

    Returns `None` for an empty corpus: there is nothing to disagree with, and
    refusing on "no identity" would make an empty database indistinguishable
    from a mismatched one.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT DISTINCT embedding_model_id, embedding_model_revision FROM chunk LIMIT 2"
        )
        rows = cursor.fetchall()
    if not rows:
        return None
    if len(rows) > 1:
        # More than one identity in one corpus is its own defect: half the
        # vectors are in a different space from the other half, and no single
        # query embedding can be right for both.
        raise HTTPException(
            status_code=500,
            detail={
                "type": "corpus-encoder-identity-split",
                "title": "The corpus holds vectors from more than one encoder",
                "status": 500,
                "detail": (
                    "Chunks record more than one (model_id, revision) pair, so no query "
                    "embedding can be correct for all of them. Re-ingest under one encoder."
                ),
            },
        )
    return (str(rows[0][0]), str(rows[0][1]))


@router.get("/api/v1/retrieval/search")
def search(
    connection: Annotated[psycopg.Connection, Depends(get_connection)],
    config: Annotated[RetrievalConfig, Depends(get_config)],
    q: Annotated[str, Query(min_length=1, max_length=MAX_QUERY_CHARACTERS)],
    limit: Annotated[int, Query(ge=1)] = DEFAULT_LIMIT,
    arm: Annotated[str, Query(pattern=r"^[a-z][a-z0-9_]*$")] = "fused_reranked",
) -> dict[str, Any]:
    """Return ranked passages for `q`, each carrying the page it was printed on.

    `limit` bounds the **ranked** portion only. FR-012 counts deterministic
    route matches outside it, so a single ceiling over both would make the route
    subtractive at the boundary — the property FR-012 exists to forbid.
    """
    if limit > config.fetch_depth:
        raise HTTPException(
            status_code=422,
            detail={
                "type": "limit-above-fetch-depth",
                "title": "limit exceeds the fetch depth",
                "status": 422,
                "detail": (
                    f"limit={limit} is above the fetch depth of {config.fetch_depth}. "
                    f"A caller cannot ask for more ranked results than the fusion "
                    f"statement retrieves; the extra would be padding."
                ),
            },
        )

    from gateway.inference.encoder import (
        EncoderIdentityError,
        assert_encoder_identity,
        embed_texts,
    )
    from gateway.inference.session import load_encoder

    encoder = load_encoder(
        _encoder_directory(),
        intra_op_threads=config.intra_op_threads,
        inter_op_threads=config.inter_op_threads,
    )

    # FR-007, before anything else touches the corpus.
    corpus_identity = corpus_encoder_identity(connection)
    if corpus_identity is not None:
        try:
            assert_encoder_identity(encoder.identity, corpus_identity)
        except EncoderIdentityError as exc:
            raise HTTPException(
                status_code=500,
                detail={
                    "type": "encoder-identity-mismatch",
                    "title": "The query encoder is not the corpus encoder",
                    "status": 500,
                    "detail": str(exc),
                    "encoder_model_id": encoder.identity[0],
                    "encoder_model_revision": encoder.identity[1],
                    "corpus_model_id": corpus_identity[0],
                    "corpus_model_revision": corpus_identity[1],
                },
            ) from exc

    embedding = embed_texts(encoder.session, encoder.tokenizer, [q])[0]

    with connection.cursor() as cursor:
        cursor.execute(FUSION_SQL, retrieval_parameters(q, embedding, config=config))
        fused = cursor.fetchall()

    # FR-018, FR-025. Rerank the top of the fused ordering before the cut, so
    # `limit` selects from a *reranked* list rather than reordering a slice
    # someone else already chose. Reranking after the cut would let a candidate
    # the reranker would have promoted be discarded before it was ever scored.
    fused_ids = [str(row[0]) for row in fused]
    reranking, fused_ids = _rerank(connection, q, fused_ids, arm=arm, config=config)
    ranked_ids = fused_ids[:limit]
    results: list[RetrievalResult] = []
    if ranked_ids:
        with connection.cursor() as cursor:
            cursor.execute(_PROJECTION_SQL, {"ids": ranked_ids})
            results = results_from_rows(
                cursor.fetchall(),
                match_kind=MatchKind.RANKED_RELEVANCE,
                ranked=True,
            )

    # FR-010 to FR-014. The route runs *after* fusion and unions additively:
    # every ranked result above is still here, and route matches are appended
    # with a null fused rank and counted outside `limit`. Excluding what fusion
    # already returned is what keeps the union additive rather than duplicating
    # -- one chunk appearing twice would carry two disagreeing accounts of how
    # it was found.
    route = resolve_part_numbers(
        connection,
        recognise_part_numbers(q),
        exclude_chunk_ids=[result.chunk_id for result in results],
    )
    if route.added_chunk_ids:
        with connection.cursor() as cursor:
            cursor.execute(_PROJECTION_SQL, {"ids": list(route.added_chunk_ids)})
            results = results + results_from_rows(
                cursor.fetchall(),
                match_kind=MatchKind.DETERMINISTIC_IDENTIFIER,
                ranked=False,
            )

    parameters = ranking_parameters_in_force(config)
    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "request": {"q": q, "limit": limit},
        "ranking_parameters": {
            "fusion_constant": parameters.fusion_constant,
            "tie_break_key": parameters.tie_break_key,
            "missing_arm_convention": parameters.missing_arm_convention,
            "fetch_depth": parameters.fetch_depth,
            "reranked_count": parameters.reranked_count,
            "search_breadth": parameters.search_breadth,
            "index_mode": parameters.index_mode,
            "lexical": {
                "arm": LEXICAL_ARM_NAME,
                "uses_corpus_wide_term_statistics": False,
            },
            "encoder": {
                "model_id": encoder.identity[0],
                "revision": encoder.identity[1],
            },
        },
        "results": [
            {
                "chunk_id": result.chunk_id,
                "document_id": result.document_id,
                "document_type": result.document_type,
                "project_id": result.project_id,
                "page_number": result.page_number,
                "body_text": result.body_text,
                "match_kind": str(result.match_kind),
                "fused_rank": result.fused_rank,
            }
            for result in results
        ],
        "mode": {
            "arm_requested": arm,
            "arm_served": reranking["arm_served"],
            "degraded": readiness.degraded,
            "reranked": reranking["reranked"],
            "unreranked_reason": reranking["unreranked_reason"],
            "statement": reranking["statement"],
        },
        "reranking": {
            "candidates_scored": reranking["candidates_scored"],
            "sequence_limit_tokens": reranking["sequence_limit_tokens"],
            "candidates_truncated": reranking["candidates_truncated"],
            "candidate_token_lengths": reranking["candidate_token_lengths"],
            "precision": reranking["precision"],
        },
        "deterministic_route": route.as_dict(),
        # FR-009: never raised to reach a target. `results: []` with
        # `result_count: 0` is a complete, successful answer.
        "result_count": len(results),
        "fused_candidate_count": len(fused),
        # Taken here rather than at the fusion boundary: the deterministic route
        # has already added its matches above, and they are part of the ordering
        # the caller received.
        "ordering_digest": ordering_digest(result.chunk_id for result in results),
    }


#: Arms that do not rerank at all. Naming them rather than testing for the
#: absence of a session is what keeps `arm_excludes_reranking` distinguishable
#: from `reranker_unavailable` -- one is a choice and the other is a fault.
_UNRERANKED_ARMS = {"lexical", "dense", "fused"}

_ARM_PRECISION = {
    "fused_reranked": "int8",
    "fused_reranked_full_precision": "fp32",
}


def _rerank(
    connection: psycopg.Connection,
    query: str,
    fused_ids: list[str],
    *,
    arm: str,
    config: RetrievalConfig,
) -> tuple[dict[str, Any], list[str]]:
    """Rerank the fused ordering, or say precisely why it was not reranked.

    Returns the reporting block and the possibly-reordered identifiers. Every
    path here produces an `unreranked_reason` or `reranked: True` -- there is no
    fall-through that leaves a response silent about which it was, because a
    figure that does not say whether it was reranked cannot be compared with one
    that does.
    """
    blank: dict[str, Any] = {
        "arm_served": arm,
        "reranked": False,
        "unreranked_reason": None,
        "statement": None,
        "candidates_scored": 0,
        "sequence_limit_tokens": None,
        "candidates_truncated": 0,
        "candidate_token_lengths": [],
        "precision": None,
    }

    if arm in _UNRERANKED_ARMS:
        blank["unreranked_reason"] = str(UnrerankedReason.ARM_EXCLUDES_RERANKING)
        blank["statement"] = f"The {arm} arm does not rerank; this is not degradation."
        return blank, fused_ids

    if not fused_ids:
        blank["unreranked_reason"] = str(UnrerankedReason.NO_CANDIDATES_TO_SCORE)
        blank["statement"] = "Fusion returned no candidates, so there was nothing to score."
        return blank, fused_ids

    precision = _ARM_PRECISION.get(arm)
    if precision is None:
        raise HTTPException(
            status_code=422,
            detail={
                "type": "unknown-arm",
                "title": "Unknown retrieval arm",
                "status": 422,
                "detail": f"{arm!r} is not one of {sorted(_UNRERANKED_ARMS | set(_ARM_PRECISION))}",
            },
        )

    session = readiness.session_for(precision)
    if session is None:
        # FR-021. Requesting an arm that did not load is refused explicitly
        # rather than served by the other: an evaluation that silently fell back
        # would put a quantized figure in a full-precision row.
        if readiness.sessions:
            raise HTTPException(
                status_code=503,
                detail={
                    "type": "arm-unavailable",
                    "title": f"The {arm} arm did not load",
                    "status": 503,
                    "detail": (
                        f"{precision} is unavailable and will not be silently served by "
                        f"another precision, because the two are different measurements."
                    ),
                },
            )
        blank["unreranked_reason"] = str(UnrerankedReason.RERANKER_UNAVAILABLE)
        blank["statement"] = (
            "Fusion-only: no reranker loaded. This ordering is weak by construction "
            "and no figure from it may be read as reranked."
        )
        return blank, fused_ids

    top = fused_ids[: config.reranked_count]
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT chunk_id::text, body_text FROM chunk WHERE chunk_id::text = ANY(%(ids)s)",
            {"ids": top},
        )
        text_for = {str(row[0]): str(row[1]) for row in cursor.fetchall()}
    ordered_top = [cid for cid in top if cid in text_for]
    try:
        scores, truncation = session.score(query, [text_for[cid] for cid in ordered_top])
    except Exception as exc:  # noqa: BLE001 - a session lost mid-request degrades
        # FR-021: the in-flight request completes as a degraded *success*, and
        # the cause stays distinguishable from a startup failure so a run does
        # not read a mid-request loss as "the reranker never loaded".
        blank["unreranked_reason"] = str(UnrerankedReason.RERANKER_FAILED_DURING_REQUEST)
        blank["statement"] = (
            f"Fusion-only: the reranker session was lost while serving this request "
            f"({str(exc)[:120]}). The results are complete; the ordering is not reranked."
        )
        return blank, fused_ids

    order = sorted(range(len(ordered_top)), key=lambda i: (-float(scores[i]), ordered_top[i]))
    reranked_ids = [ordered_top[i] for i in order] + fused_ids[config.reranked_count :]
    return (
        {
            "arm_served": arm,
            "reranked": True,
            "unreranked_reason": None,
            "statement": None,
            "candidates_scored": len(ordered_top),
            "sequence_limit_tokens": truncation.sequence_limit,
            "candidates_truncated": truncation.truncated_count,
            "candidate_token_lengths": list(truncation.candidate_token_lengths),
            "precision": precision,
        },
        reranked_ids,
    )


@router.get("/api/v1/retrieval/readyz")
def readyz() -> dict[str, Any]:
    """Readiness, including the degraded state.

    A **success response carrying a state field**, never a status code.
    Orchestrator probes are ternary with no partial state, so a degraded system
    reported through a status code would be pulled from rotation — which is the
    outcome FR-021 exists to prevent, because a fusion-only service still
    answers and restarting does not fix a missing graph.
    """
    return readiness.as_dict()
