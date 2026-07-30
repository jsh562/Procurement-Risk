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
from api.retrieval.fusion import FUSION_SQL, retrieval_parameters
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

    ranked_ids = [str(row[0]) for row in fused][:limit]
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
        "deterministic_route": route.as_dict(),
        # FR-009: never raised to reach a target. `results: []` with
        # `result_count: 0` is a complete, successful answer.
        "result_count": len(results),
        "fused_candidate_count": len(fused),
    }
