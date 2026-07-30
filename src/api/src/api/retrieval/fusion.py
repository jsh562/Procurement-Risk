"""Reciprocal rank fusion, as one SQL statement.

Spec FR-001, FR-002. Two CTEs — one per arm, each with its own ordering and its
own `LIMIT` — joined by a full outer join on the chunk identifier, with the
reciprocal-rank terms summed so a candidate missing from one arm scores zero on
that side. One statement, because FR-002 permits exactly one and every setting
the query needs rides on the connection instead (`db.connection_options`).

**The RRF term is defined once, in `_RRF_TERM`, and both the retrieval statement
and the oracle harness below use it.** That is deliberate and it is what makes
the pseudo-oracle worth running: a property test that compared the published
formula against a *Python* reimplementation would prove the reimplementation
right and say nothing about the SQL that ships. `fuse_candidates` runs the same
expression over supplied rank vectors, so the generated inputs reach the real
arithmetic.

**Why the fused ordering is weak, stated here because this is where someone
would tune it.** At a fetch depth of 50 with `k = 60`, rank 1 contributes 1/61
and rank 50 contributes 1/110 — a ratio of 1.8. Fusion barely separates
candidates *by construction*, and the reranker is what carries ranking quality.
Raising the depth does not fix it: reranking quality declines past a depth
limit, so a deeper fetch trades one loss for another. No criterion in this epic
may rest on fusion-only ordering being good, and FR-036 labels fusion-only the
weak comparator for that reason.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Final

import psycopg

from api.config import RetrievalConfig
from api.retrieval.parameters import FUSION_CONSTANT

__all__ = [
    "FUSION_SQL",
    "explain_plan",
    "fuse_candidates",
    "retrieval_parameters",
    "retrieval_statement",
]

#: One arm's reciprocal-rank contribution, written from the published
#: definition: rank is one-based, so the top candidate contributes `1/(k+1)`.
#: `coalesce(..., 0)` is the missing-arm convention — a candidate the arm did
#: not return scores zero on that side rather than dropping the row, which is
#: what makes the full outer join meaningful.
_RRF_TERM: Final = "coalesce(1.0 / ({k} + {rank}), 0)"


def _fused_score(*rank_columns: str, k: int = FUSION_CONSTANT) -> str:
    """The summed RRF score over the named per-arm rank columns."""
    return " + ".join(_RRF_TERM.format(k=k, rank=column) for column in rank_columns)


#: The ranking statement. Both arms cut at the fetch depth, then fused.
#:
#: The tie-break appears **inside each arm's ordering** as well as in the final
#: one, and that is not redundancy. A row limit needs an ordering that
#: constrains rows into a unique order; without a tie-break inside an arm, a tie
#: at the last in-window position changes which rows survive the cut — the
#: candidate *set*, not merely its order — and the reranker then scores
#: different rows between runs of the same query.
FUSION_SQL: Final = f"""
WITH lexical AS (
    SELECT chunk_id,
           row_number() OVER (
               ORDER BY ts_rank(search_vector, plainto_tsquery('english', %(query)s)) DESC,
                        chunk_id ASC
           ) AS rank
    FROM chunk
    WHERE search_vector @@ plainto_tsquery('english', %(query)s)
    ORDER BY ts_rank(search_vector, plainto_tsquery('english', %(query)s)) DESC, chunk_id ASC
    LIMIT %(depth)s
),
dense AS (
    SELECT chunk_id,
           row_number() OVER (
               ORDER BY embedding <=> %(embedding)s::vector ASC, chunk_id ASC
           ) AS rank
    FROM chunk
    ORDER BY embedding <=> %(embedding)s::vector ASC, chunk_id ASC
    LIMIT %(depth)s
)
SELECT coalesce(lexical.chunk_id, dense.chunk_id) AS chunk_id,
       lexical.rank AS lexical_rank,
       dense.rank   AS dense_rank,
       {_fused_score("lexical.rank", "dense.rank")} AS fused_score
FROM lexical
FULL OUTER JOIN dense ON lexical.chunk_id = dense.chunk_id
ORDER BY fused_score DESC, chunk_id ASC
LIMIT %(limit)s
"""

#: The oracle harness. Runs the **same** `_RRF_TERM` over supplied per-arm rank
#: vectors, so a property test drives the shipped arithmetic rather than a
#: Python restatement of it.
_ORACLE_SQL: Final = f"""
WITH lexical AS (
    SELECT candidate, rank FROM unnest(%(lexical)s::int[]) WITH ORDINALITY AS t(candidate, rank)
),
dense AS (
    SELECT candidate, rank FROM unnest(%(dense)s::int[]) WITH ORDINALITY AS t(candidate, rank)
)
SELECT coalesce(lexical.candidate, dense.candidate) AS candidate
FROM lexical
FULL OUTER JOIN dense ON lexical.candidate = dense.candidate
ORDER BY ({_fused_score("lexical.rank", "dense.rank")}) DESC,
         coalesce(lexical.candidate, dense.candidate) ASC
"""


def retrieval_statement() -> str:
    """The one ranking statement, as text.

    Returned rather than exported as a constant so a caller cannot accumulate
    onto it — FR-002 permits one statement, and a module-level string that
    callers concatenate is how a second one arrives.
    """
    return FUSION_SQL


def retrieval_parameters(
    query: str,
    embedding: Sequence[float],
    *,
    config: RetrievalConfig,
) -> dict[str, object]:
    """Bind the statement's parameters from configuration.

    The fetch depth is **bound from configuration, never typed into the SQL**.
    FR-003 makes it part of the ranking definition and FR-029 requires it
    published with every result set; a literal in the statement could not be
    published as *the value in force*, because nothing would keep the published
    figure and the executed query in agreement.

    The fused result is cut at the reranked count rather than at the fetch
    depth. The two are equal by FR-018 and the configuration asserts it, but
    they are different quantities: the depth is how many each arm contributes,
    and the reranked count is how many of the fused ordering are scored. The
    fused set of two 50-candidate arms may hold up to 100 distinct candidates,
    so this cut is what makes "the top 50 of the fused ordering" true.
    """
    return {
        "query": query,
        "embedding": "[" + ",".join(repr(float(value)) for value in embedding) + "]",
        "depth": config.fetch_depth,
        "limit": config.reranked_count,
    }


def explain_plan(
    connection: psycopg.Connection,
    query: str,
    embedding: Sequence[float],
    *,
    config: RetrievalConfig,
) -> dict:
    """The planner's chosen plan for the ranking statement, as a dict.

    `EXPLAIN` without `ANALYZE`: the plan shape is the claim under test, and
    executing the query would make the assertion depend on what happens to be
    in the corpus. A plan-shape assertion that only holds on a populated
    database is the empty-table failure this epic has already met once.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "EXPLAIN (FORMAT JSON) " + FUSION_SQL,
            retrieval_parameters(query, embedding, config=config),
        )
        row = cursor.fetchone()
    assert row is not None, "EXPLAIN returned no plan"
    return row[0][0]["Plan"]


def fuse_candidates(
    connection: psycopg.Connection,
    lexical: Sequence[int],
    dense: Sequence[int],
) -> list[int]:
    """Fuse two ranked arms and return the fused ordering.

    Takes candidate identifiers in rank order — position 1 is rank 1 — and
    returns them fused by reciprocal rank, highest score first, ties broken by
    identifier ascending.

    This exists for the pseudo-oracle: it executes the same reciprocal-rank
    expression `FUSION_SQL` uses, over inputs a property test generates rather
    than over rows read back from the query under test. Reading inputs back is
    the failure the oracle's soundness conditions name — it would prove only
    that the copy was faithful.
    """
    with connection.cursor() as cursor:
        cursor.execute(_ORACLE_SQL, {"lexical": list(lexical), "dense": list(dense)})
        return [row[0] for row in cursor.fetchall()]
