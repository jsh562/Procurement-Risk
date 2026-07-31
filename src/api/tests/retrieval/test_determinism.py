"""The ordering is a property of the query, not of the plan.

Spec FR-020. Asserted three ways, because "the same query twice returns the same
rows" is the weakest of the three and passes on a system that would reorder the
moment anything changed.

1. **Twice in a row.** The floor. Catches nondeterminism inside one plan.
2. **Under flipped planner settings.** The real assertion. An ordering that
   survives a *different plan* is defined by the `ORDER BY`, not by the access
   path the planner happened to choose. PostgreSQL documents that the optimizer
   plans differently for different limit values and yields different row orders
   where the ordering is not total — so flipping settings is how that shows.
3. **Across a rebuild of the exact path.** Golden orderings are only valid
   because the tie-break makes the ordering total; a rebuild that changed the
   answer would mean it is not.

**The approximate path is deliberately excluded from (3).** HNSW graph
construction is randomized, so a rebuild can legitimately return different
approximate neighbours — the spec's own edge case. FR-020's identical-ordering
guarantee is assertable across rebuilds on the **exact** path only, and the
approximate path gets an overlap and recall-delta assertion instead, which
belongs with the flag-parity work rather than here. Asserting rebuild-identity
on the approximate path would be asserting something the algorithm does not
promise, and it would fail intermittently for a correct implementation.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.config import load_retrieval_config  # noqa: E402
from api.retrieval.fusion import FUSION_SQL, explain_plan, retrieval_parameters  # noqa: E402
from retrieval.fixtures.seed_chunks import seeded_corpus  # noqa: E402

QUERY = "bronze pressure relief valve"

#: Planner settings flipped one at a time. Each removes an access path or a
#: strategy, so the planner is forced to a different plan for the same query.
PLANNER_FLIPS = (
    "enable_seqscan",
    "enable_indexscan",
    "enable_bitmapscan",
    "enable_sort",
    "enable_hashjoin",
    "enable_mergejoin",
    "enable_nestloop",
)


@pytest.fixture
def seeded() -> Iterator[psycopg.Connection]:
    url = os.environ.get("DATABASE_URL")
    if url is None:
        pytest.skip("DATABASE_URL is unset; determinism is a property of real execution")
    with psycopg.connect(url) as conn:
        seeded_corpus(conn)
        yield conn
        conn.rollback()


def _ordering(connection: psycopg.Connection) -> list[str]:
    config = load_retrieval_config({})
    with connection.cursor() as cursor:
        cursor.execute(FUSION_SQL, retrieval_parameters(QUERY, [0.1] * 384, config=config))
        return [str(row[0]) for row in cursor.fetchall()]


def test_the_same_query_twice_returns_the_same_ordering(seeded: psycopg.Connection) -> None:
    assert _ordering(seeded) == _ordering(seeded)


def test_the_ordering_survives_every_planner_flip(seeded: psycopg.Connection) -> None:
    """The ordering is unchanged under seven different plans.

    This is the assertion that distinguishes "deterministic" from "happens to be
    stable on this plan". An implementation whose ordering depended on the
    access path would pass the repeat test above and fail here.
    """
    baseline = _ordering(seeded)
    for setting in PLANNER_FLIPS:
        with seeded.cursor() as cursor:
            cursor.execute(f"SET LOCAL {setting} = off")
        assert _ordering(seeded) == baseline, (
            f"the ordering changed with {setting}=off, so it is a property of the "
            f"plan rather than of the query"
        )
        with seeded.cursor() as cursor:
            cursor.execute(f"SET LOCAL {setting} = on")


def test_at_least_one_flip_actually_changes_the_plan(seeded: psycopg.Connection) -> None:
    """The flips above are only evidence if they change something.

    Without this, `test_the_ordering_survives_every_planner_flip` would pass
    vacuously on a corpus where the planner picks the same plan regardless —
    which is the failure mode a six-row fixture makes likely, not unlikely.
    """
    config = load_retrieval_config({})
    baseline = explain_plan(seeded, QUERY, [0.1] * 384, config=config)
    changed = []
    for setting in PLANNER_FLIPS:
        with seeded.cursor() as cursor:
            cursor.execute(f"SET LOCAL {setting} = off")
        if explain_plan(seeded, QUERY, [0.1] * 384, config=config) != baseline:
            changed.append(setting)
        with seeded.cursor() as cursor:
            cursor.execute(f"SET LOCAL {setting} = on")
    assert changed, (
        "no planner flip changed the plan, so the determinism assertion above is "
        "vacuous on this corpus — it compared one plan against itself seven times"
    )


def test_the_ordering_survives_a_rebuild_of_the_exact_path(seeded: psycopg.Connection) -> None:
    """Re-analysing and re-reading returns the same ordering.

    The exact path only. HNSW construction is randomized, so a rebuild there can
    legitimately return different approximate neighbours — asserting otherwise
    would fail intermittently for a correct implementation.
    """
    baseline = _ordering(seeded)
    with seeded.cursor() as cursor:
        cursor.execute("ANALYZE chunk")
    assert _ordering(seeded) == baseline
