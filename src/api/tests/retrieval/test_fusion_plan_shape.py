"""Each arm's `LIMIT` survives CTE inlining, and each cut is the fetch depth.

Spec FR-003. PostgreSQL 16 folds a non-recursive, volatile-free CTE into the
parent when it is referenced once, and folding is a *planner transformation* —
nothing in the documentation states that an inlined CTE's `LIMIT` is honoured.
It follows from semantics rather than from text: a sub-select carrying `LIMIT`
is not flattened further. `research-implementation.md` records that inference
and says to assert it with a plan-shape test rather than trust it, which is what
this file is.

The failure this guards against is quiet. If a per-arm `LIMIT` were dissolved by
inlining, both arms would contribute *every* matching row to the fusion instead
of their top 50. Nothing raises, the query returns results, and the only symptom
is a candidate set that is not the one the latency budget and the reranked count
were derived from — so the figures published against it describe a workload the
system never ran.

Asserted over `EXPLAIN` rather than over row counts: a row-count assertion
passes whenever the corpus happens to be smaller than the depth, which is
exactly the condition the integration fixture creates.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator

import psycopg
import pytest

from api.config import load_retrieval_config
from api.retrieval.fusion import explain_plan, retrieval_statement


@pytest.fixture(scope="module")
def connection() -> Iterator[psycopg.Connection]:
    url = os.environ.get("DATABASE_URL")
    if url is None:
        pytest.skip("DATABASE_URL is unset; a plan-shape assertion needs a real planner")
    with psycopg.connect(url) as conn:
        yield conn
        conn.rollback()


def _limit_nodes(plan: dict) -> list[dict]:
    """Every `Limit` node in the plan tree, in no particular order."""
    found: list[dict] = []
    if plan.get("Node Type") == "Limit":
        found.append(plan)
    for child in plan.get("Plans", []):
        found.extend(_limit_nodes(child))
    return found


def test_the_plan_holds_a_limit_for_each_arm_plus_the_final_cut(
    connection: psycopg.Connection,
) -> None:
    """Three cuts: one per arm, and one on the fused result.

    Two would mean an arm's cut was dissolved; one would mean both were.
    """
    config = load_retrieval_config({})
    plan = explain_plan(connection, "bronze valve", [0.0] * 384, config=config)
    limits = _limit_nodes(plan)
    assert len(limits) >= 3, (
        f"expected a Limit for each arm and one for the fused result, found "
        f"{len(limits)}. An arm whose LIMIT was dissolved by CTE inlining "
        f"contributes every matching row to fusion instead of its top "
        f"{config.fetch_depth}, silently."
    )


def test_each_arm_is_cut_at_the_configured_fetch_depth(
    connection: psycopg.Connection,
) -> None:
    """The per-arm cuts are the fetch depth, not some other number.

    A `LIMIT` that survived inlining but at the wrong value is the same defect
    with a different symptom: the candidate set is still not the one the
    reranked count was derived from.
    """
    config = load_retrieval_config({})
    plan = explain_plan(connection, "bronze valve", [0.0] * 384, config=config)
    counts = sorted(node.get("Plan Rows", -1) for node in _limit_nodes(plan))
    # `Plan Rows` on a Limit node is the planner's *estimate*, not the LIMIT
    # constant -- on a corpus smaller than the depth it is the corpus size. So
    # the assertable invariant is the bound, not equality: no cut may estimate
    # more rows than its LIMIT permits, and a dissolved LIMIT shows up as an
    # estimate above the depth.
    assert counts, "the plan holds no Limit node at all"
    assert all(0 <= count <= config.fetch_depth for count in counts), (
        f"a Limit node estimates more rows than the fetch depth {config.fetch_depth} "
        f"permits; found {counts}. An estimate above the depth means the cut is not "
        f"bounding that arm."
    )


def test_the_statement_binds_the_depth_from_configuration(
    connection: psycopg.Connection,
) -> None:
    """The depth in the plan is the configured one, not a literal in the SQL.

    FR-003 makes the fetch depth part of the ranking definition, and FR-029
    requires it published with every result set. A number typed into the
    statement could not be published as *the* value in force, because nothing
    would guarantee the published figure and the executed query agreed.
    """
    default = load_retrieval_config({})
    loose = explain_plan(connection, "bronze valve", [0.0] * 384, config=default)
    loose_counts = sorted(node.get("Plan Rows", -1) for node in _limit_nodes(loose))

    # Self-calibrating rather than assuming a corpus size. A depth only binds the
    # planner's estimate when it is *below* that estimate, so the discriminating
    # depth is derived from what the planner just said rather than typed here.
    # Typing one couples this assertion to the row count and to whether some
    # other test happened to run ANALYZE first — which is exactly how it failed
    # once, silently becoming a comparison of one plan against itself.
    binding = max(1, max(loose_counts) - 1)
    tight_config = load_retrieval_config(
        {
            "PRC_RETRIEVAL_FETCH_DEPTH": str(binding),
            "PRC_RETRIEVAL_RERANKED_COUNT": str(binding),
        }
    )
    tight = explain_plan(connection, "bronze valve", [0.0] * 384, config=tight_config)
    tight_counts = sorted(node.get("Plan Rows", -1) for node in _limit_nodes(tight))

    assert all(count <= binding for count in tight_counts), (
        f"a cut exceeds the configured depth of {binding}; found {tight_counts}"
    )
    assert tight_counts != loose_counts, (
        f"configuring the depth changed nothing in the plan ({binding} -> {tight_counts}, "
        f"{default.fetch_depth} -> {loose_counts}), which is what a literal in the "
        f"statement would look like"
    )


def test_the_statement_is_a_single_statement(connection: psycopg.Connection) -> None:
    """One statement, as FR-002 requires — asserted on the text, not inferred.

    `test_single_statement.py` asserts it over what the connection actually
    executes; this is the cheaper structural half, and it catches the accidental
    semicolon before the integration tier has to.
    """
    sql = retrieval_statement()
    assert sql.count(";") == 0, "the ranking statement contains a statement separator"
    assert json.dumps(sql).count("SET ") == 0, "the statement carries a SET"
