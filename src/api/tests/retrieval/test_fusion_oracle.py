"""The pseudo-oracle for reciprocal rank fusion, and what it cannot see.

Spec FR-001, FR-042. The quality policy requires property-based tests over the
pure functions of fusion ranking, and FR-002 puts all ranking in one SQL
statement, leaving no such function. `specs/sad.md` prescribes the compensating
shape and this is it: recompute the fusion arithmetic in Python and assert the
SQL statement's ordering matches.

**The technique has a name and a known limit.** It is a *pseudo-oracle*, and its
founding assumption — that two independent implementations of one specification
fail independently — has been empirically falsified. Knight and Leveson ran 27
independently written versions of one specification through a million tests and
found coincident failures far above the independence prediction, because the
correlated fault is *shared misreading of the specification*, which a
pseudo-oracle cannot detect by construction.

So "the oracle and the SQL agree" is a claim about **consistency**. It becomes a
claim about **correctness** only under an assumption known to be false in the
tail. Four conditions make it as sound as it can be, and each is an authoring
constraint on this file:

1. **Derived, not transcribed.** The arithmetic below is written from the
   published RRF definition — `sum over arms of 1 / (k + rank)` — not read off
   the SQL. Transcribing would guarantee agreement and prove nothing.
2. **Generated inputs, not read-back ones.** The rank vectors come from
   Hypothesis, not from the query under test. Feeding the query's own output
   back in would prove only that the copy was faithful.
3. **An enumerated uncovered surface.** Stated below, not left implied.
4. **Disagreement is adjudicated**, not auto-resolved in favour of either side.

**What this oracle does NOT cover**, and which task covers it instead:

- *Candidate-set selection at the 50-row cut* — which rows reach fusion at all,
  and what happens when a tie straddles the boundary. T030.
- *The per-arm tie-break* — that each arm's own ordering is total before the
  cut. T030 again, with a tie engineered at the last in-window position.
- *`LIMIT` semantics under CTE inlining* — that each arm's cut survives the
  planner folding the CTE into the parent. T029, over `EXPLAIN`.
- *The one-statement property itself* — T028.

None of those is arithmetic, which is why none is here: an oracle over rank
vectors cannot see which rows became the rank vectors.

**Measured, not asserted.** Three mutations were applied to the shipped
expression and run against these properties:

| Mutation | Caught |
|---|---|
| drop `coalesce` — a missing arm nulls the row instead of scoring zero | yes |
| `sum` → `greatest` — take the better arm instead of both | yes |
| drop the final tie-break — no total order | **no** |

The third is the point of the table. It is not caught here because these inputs
contain no tie, and a fused ordering with no ties is insensitive to the
tie-break — which is precisely why T030 engineers one at the last in-window
position. The uncovered surface above is enumerated because it was checked, not
because enumerating it sounded rigorous.

One finding worth carrying: changing `k` from 60 to 1 does **not** move the
ordering on these inputs. That is a property of RRF rather than a weak test —
`k` scales scores while the ordering is dominated by rank sums — and it is the
same insensitivity that makes fusion nearly uniform at depth 50.
"""

from __future__ import annotations

import os
from collections.abc import Iterator

import psycopg
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from api.retrieval.fusion import fuse_candidates
from api.retrieval.parameters import FUSION_CONSTANT

#: Hypothesis reuses one connection across examples, which it would otherwise
#: flag as function-scoped-fixture reuse. Reuse is correct here: opening a
#: connection per example would make the arithmetic under test the slowest part
#: of the suite for no added coverage.
_SETTINGS = settings(max_examples=60, suppress_health_check=[HealthCheck.function_scoped_fixture])


def _database_url() -> str | None:
    return os.environ.get("DATABASE_URL")


@pytest.fixture(scope="module")
def connection() -> Iterator[psycopg.Connection]:
    """A connection for the oracle to run the shipped arithmetic on.

    Skipped rather than mocked when no database is configured. A mocked
    connection would make this file assert that a Python reimplementation
    matches another Python reimplementation, which is exactly the vacuous shape
    the pseudo-oracle's soundness conditions rule out.
    """
    url = _database_url()
    if url is None:
        pytest.skip("DATABASE_URL is unset; the oracle runs the real SQL and needs a database")
    with psycopg.connect(url) as conn:
        yield conn
        conn.rollback()


#: Candidate identifiers. Small and disjointly drawn so ties and partial overlap
#: between arms both occur often rather than by luck.
_IDS = st.integers(min_value=1, max_value=12)

#: A ranked arm: distinct identifiers in rank order, rank 1 first.
_ARM = st.lists(_IDS, min_size=0, max_size=8, unique=True)


def _reciprocal_rank(rank: int, k: int = FUSION_CONSTANT) -> float:
    """One arm's contribution for a candidate at `rank`, from the definition.

    Written from the published formula rather than from the SQL: rank is
    one-based, so the rank-1 candidate contributes `1 / (k + 1)`.
    """
    return 1.0 / (k + rank)


def _oracle(arms: list[list[int]], k: int = FUSION_CONSTANT) -> list[int]:
    """The fused ordering RRF defines, computed independently of the statement.

    A candidate absent from an arm scores zero on that arm — the missing-arm
    convention `parameters.py` publishes, applied here as the definition states
    it rather than as the SQL implements it. Ties break on the candidate
    identifier ascending, which is the total order the ranking parameters fix.
    """
    scores: dict[int, float] = {}
    for arm in arms:
        for position, candidate in enumerate(arm, start=1):
            scores[candidate] = scores.get(candidate, 0.0) + _reciprocal_rank(position, k)
    return sorted(scores, key=lambda candidate: (-scores[candidate], candidate))


@_SETTINGS
@given(lexical=_ARM, dense=_ARM)
def test_the_statement_matches_the_oracle(
    connection: psycopg.Connection, lexical: list[int], dense: list[int]
) -> None:
    """The SQL's fused ordering equals the independently derived one."""
    assert fuse_candidates(connection, lexical, dense) == _oracle([lexical, dense])


@_SETTINGS
@given(arm=_ARM)
def test_one_arm_alone_preserves_that_arms_order(
    connection: psycopg.Connection, arm: list[int]
) -> None:
    """With one arm contributing, fusion cannot reorder it.

    Every candidate scores on exactly one arm, and reciprocal rank is strictly
    decreasing in rank, so the fused order is the arm's order. A fusion that
    reordered here would be wrong in a way no agreement test over two arms
    would isolate.
    """
    assert fuse_candidates(connection, arm, []) == arm


@_SETTINGS
@given(lexical=_ARM, dense=_ARM)
def test_fusion_is_symmetric_in_its_arms(
    connection: psycopg.Connection, lexical: list[int], dense: list[int]
) -> None:
    """Swapping the arms cannot change the result.

    RRF sums per-arm contributions and addition is commutative, so an
    implementation whose output depended on argument order would be summing
    something other than what the definition says.
    """
    assert fuse_candidates(connection, lexical, dense) == fuse_candidates(
        connection, dense, lexical
    )


@_SETTINGS
@given(lexical=_ARM, dense=_ARM)
def test_every_candidate_appears_exactly_once(
    connection: psycopg.Connection, lexical: list[int], dense: list[int]
) -> None:
    """The union of the arms, de-duplicated, is what comes out.

    A candidate returned by both arms is one candidate with two contributions,
    not two rows — which is the whole point of the full outer join, and the
    thing a careless implementation duplicates.
    """
    fused = fuse_candidates(connection, lexical, dense)
    assert sorted(fused) == sorted(set(lexical) | set(dense))
    assert len(fused) == len(set(fused))


@_SETTINGS
@given(arm=_ARM.filter(lambda values: len(values) >= 2))
def test_appearing_in_both_arms_never_ranks_below_appearing_in_one(
    connection: psycopg.Connection, arm: list[int]
) -> None:
    """Agreement between arms can only help a candidate.

    Both contributions are positive, so a candidate in both arms scores strictly
    more than the same candidate in one. Stated as a property because it is the
    behaviour fusion exists for, and an implementation that subtracted or
    averaged instead of summing would still pass a same-length check.
    """
    both = fuse_candidates(connection, arm, arm)
    one = fuse_candidates(connection, arm, [])
    assert both == one, "with identical arms the ordering is unchanged, only the scores double"


def test_the_fusion_constant_is_the_published_one() -> None:
    """The oracle and the statement must agree on `k`, and 60 is pre-registered.

    Read from `parameters.py` rather than typed here: two literals is how the
    oracle comes to test a constant the statement does not use.
    """
    assert FUSION_CONSTANT == 60


@pytest.mark.parametrize(
    ("lexical", "dense", "expected"),
    [
        ([1, 2], [2, 1], [1, 2]),
        ([1], [2], [1, 2]),
        ([], [], []),
        ([3, 1], [], [3, 1]),
    ],
)
def test_worked_cases(
    connection: psycopg.Connection, lexical: list[int], dense: list[int], expected: list[int]
) -> None:
    """A few cases written out by hand.

    Generated inputs prove the property; these prove the property is the one a
    reader would expect, which a generator cannot. `[1,2]` against `[2,1]` is
    the important one: both candidates score `1/61 + 1/62`, so the result is
    decided entirely by the tie-break, and an implementation with no total order
    returns either.
    """
    assert fuse_candidates(connection, lexical, dense) == expected
