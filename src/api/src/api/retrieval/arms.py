"""The five request-selectable arms, each independently runnable.

Spec FR-025, FR-026, FR-027, FR-028. E014 publishes the ablation; E008's job is
to make each arm *runnable on its own* so there is something to publish. An arm
that could only run as part of the full pipeline would make its figure a
subtraction rather than a measurement.

**One flag, index usage only** (FR-026). The exact/approximate setting controls
whether the dense arm uses the vector index and *nothing else* — filters,
fusion, fetch depth and reranking are the same code on both settings. That is
asserted by building two differently configured applications and comparing an
enumerated observable set, not by reading the code, because "shared" is exactly
the kind of claim that decays silently.
"""

from __future__ import annotations

from collections.abc import Sequence
from enum import StrEnum
from typing import Any, Final

import psycopg

from api.config import RetrievalConfig
from api.retrieval.fusion import FUSION_SQL, retrieval_parameters

__all__ = ["Arm", "ArmResult", "run_arm"]


class Arm(StrEnum):
    """The five arms FR-025 makes request-selectable.

    Five, not six. SC-012's sixth is the FR-026 configuration flag, which is
    service configuration rather than a value this selects — building the two
    configured processes is how that one is exercised.
    """

    LEXICAL = "lexical"
    DENSE = "dense"
    FUSED = "fused"
    FUSED_RERANKED = "fused_reranked"
    FUSED_RERANKED_FULL_PRECISION = "fused_reranked_full_precision"


#: Arms that stop before reranking. Naming them is what keeps
#: `arm_excludes_reranking` distinguishable from `reranker_unavailable`.
UNRERANKED: Final = frozenset({Arm.LEXICAL, Arm.DENSE, Arm.FUSED})

#: The lexical arm alone. Cut and ordered exactly as it is inside the fusion
#: statement, so a single-arm run and the same arm's contribution to fusion are
#: the same rows in the same order — otherwise the ablation would compare an
#: arm against a differently-behaved version of itself.
_LEXICAL_SQL: Final = """
SELECT chunk_id::text
FROM chunk
WHERE search_vector @@ plainto_tsquery('english', %(query)s)
ORDER BY ts_rank(search_vector, plainto_tsquery('english', %(query)s)) DESC, chunk_id ASC
LIMIT %(depth)s
"""

#: The dense arm alone, same reasoning.
_DENSE_SQL: Final = """
SELECT chunk_id::text
FROM chunk
ORDER BY embedding <=> %(embedding)s::vector ASC, chunk_id ASC
LIMIT %(depth)s
"""


class ArmResult:
    """One arm's candidate identifiers and how it was configured."""

    __slots__ = ("arm", "chunk_ids", "index_mode", "iterative_scan")

    def __init__(
        self,
        arm: Arm,
        chunk_ids: tuple[str, ...],
        index_mode: str,
        iterative_scan: bool,
    ) -> None:
        self.arm = arm
        self.chunk_ids = chunk_ids
        self.index_mode = index_mode
        self.iterative_scan = iterative_scan

    def as_dict(self) -> dict[str, Any]:
        return {
            "arm": str(self.arm),
            "candidate_count": len(self.chunk_ids),
            "index_mode": self.index_mode,
            "iterative_scan": self.iterative_scan,
        }


def run_arm(
    connection: psycopg.Connection,
    arm: Arm,
    query: str,
    embedding: Sequence[float],
    *,
    config: RetrievalConfig,
) -> ArmResult:
    """Run one arm and return its candidates, independently of the others.

    The lexical and dense arms run their own statement rather than the fusion
    one with a side disabled: a fused statement with one arm zeroed still pays
    the join and still cuts at the fused limit, so its timing and its candidate
    set would both belong to fusion rather than to the arm.

    Reranking is **not** applied here even for the reranked arms — this returns
    the candidate set, and the route applies the reranker to it. Keeping the
    stages separate is what lets the ablation attribute a difference to the
    stage that caused it.
    """
    iterative = config.index_mode == "approximate"
    if arm is Arm.LEXICAL:
        with connection.cursor() as cursor:
            cursor.execute(_LEXICAL_SQL, {"query": query, "depth": config.fetch_depth})
            ids = tuple(str(row[0]) for row in cursor.fetchall())
        # The lexical arm never touches the vector index, so the flag is
        # reported as not applying rather than as its configured value — saying
        # "exact" here would imply a choice that was never made.
        return ArmResult(arm, ids, index_mode="not_applicable", iterative_scan=False)

    literal = "[" + ",".join(repr(float(v)) for v in embedding) + "]"
    if arm is Arm.DENSE:
        with connection.cursor() as cursor:
            cursor.execute(_DENSE_SQL, {"embedding": literal, "depth": config.fetch_depth})
            ids = tuple(str(row[0]) for row in cursor.fetchall())
        return ArmResult(arm, ids, index_mode=config.index_mode, iterative_scan=iterative)

    with connection.cursor() as cursor:
        cursor.execute(FUSION_SQL, retrieval_parameters(query, embedding, config=config))
        ids = tuple(str(row[0]) for row in cursor.fetchall())
    return ArmResult(arm, ids, index_mode=config.index_mode, iterative_scan=iterative)


def observable_flag_surface(result: ArmResult, response: dict[str, Any]) -> dict[str, Any]:
    """The set FR-026 says the flag may and may not change.

    Enumerated here so `test_flag_parity.py` compares a **named** set rather
    than whatever two responses happen to differ in. A parity test that diffed
    whole responses would fail on `generated_at` and pass on nothing.

    The dense candidate set — and the ordering that follows from it — is the one
    permitted difference. Everything else must be identical across the two
    settings, because the flag controls index usage and nothing else.
    """
    return {
        "fetch_depth": response["ranking_parameters"]["fetch_depth"],
        "reranked_count": response["ranking_parameters"]["reranked_count"],
        "fusion_constant": response["ranking_parameters"]["fusion_constant"],
        "tie_break_key": response["ranking_parameters"]["tie_break_key"],
        "missing_arm_convention": response["ranking_parameters"]["missing_arm_convention"],
        "lexical_arm": response["ranking_parameters"]["lexical"]["arm"],
        "reranked": response["mode"]["reranked"],
        "arm_served": response["mode"]["arm_served"],
        "route_recognised": response["deterministic_route"]["recognised_tokens"],
        "sequence_limit": response["reranking"]["sequence_limit_tokens"],
    }
