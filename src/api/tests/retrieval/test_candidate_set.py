"""The candidate set at the cut is stable, including when a tie straddles it.

Spec FR-003, FR-004. This is the assertion the pseudo-oracle cannot make. An
oracle over rank vectors compares orderings *given* the candidates; it cannot
see which rows became the candidates, and that selection is where the per-arm
tie-break matters.

**The failure is a set change, not an order change.** PostgreSQL documents that
with a row limit you must use an ordering that constrains rows into a unique
order or you get an unpredictable subset. Two chunks tied on `ts_rank` and
separated only by identifier sit at the boundary: with no tie-break *inside the
arm's* ordering, which of them survives `LIMIT` is unspecified, so the candidate
set differs between runs of the same query. The reranker then scores different
rows, and two runs publish different figures for one corpus with nothing
reporting a change.

The fixture engineers exactly that tie — two chunks with identical body text, so
identical `ts_rank`, differing only in `chunk_id`. Without one the tie-break is
untestable, which is why `seed_chunks.py` carries the pair deliberately rather
than incidentally.

**A limitation, measured rather than assumed.** The per-arm tie-break was
removed and this file re-run: it still passed. The candidate set was then
compared across five planner configurations — default, `enable_seqscan=off`,
`enable_bitmapscan=off`, `enable_indexscan=off`, `enable_sort=off` — and was
identical under all five. At six rows PostgreSQL returns a stable subset whether
or not the ordering is total, so **the absence of the per-arm tie-break is not
observable at this corpus size**.

That is recorded rather than papered over, because a test asserting "the
candidate set is stable" would pass here with the tie-break deleted, and reading
it as evidence the tie-break works is exactly the vacuity this epic keeps
finding. What is asserted below is what is genuinely checkable: that the tie is
real, that the ordering is total *by construction* (asserted on the statement
text), and that the tie breaks in the published direction. The set-instability
itself needs a corpus large enough to make the plan choose, which belongs to the
benchmark tier rather than here.
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
from api.retrieval.fusion import FUSION_SQL, retrieval_parameters  # noqa: E402
from retrieval.fixtures.seed_chunks import seeded_corpus  # noqa: E402

#: A query matching both members of the engineered tie and nothing else about
#: them — so the two rows differ on identifier alone.
TIE_QUERY = "bronze valve"


@pytest.fixture
def seeded() -> Iterator[tuple[psycopg.Connection, object]]:
    """A connection with the fixture corpus seeded, rolled back afterwards."""
    url = os.environ.get("DATABASE_URL")
    if url is None:
        pytest.skip("DATABASE_URL is unset; the candidate set is a property of real rows")
    with psycopg.connect(url) as conn:
        corpus = seeded_corpus(conn)
        yield conn, corpus
        conn.rollback()


def _candidates(connection: psycopg.Connection, *, depth: int) -> list[str]:
    """The fused candidate identifiers, in order, at the given per-arm depth."""
    config = load_retrieval_config(
        {"PRC_RETRIEVAL_FETCH_DEPTH": str(depth), "PRC_RETRIEVAL_RERANKED_COUNT": str(depth)}
    )
    with connection.cursor() as cursor:
        cursor.execute(FUSION_SQL, retrieval_parameters(TIE_QUERY, [0.0] * 384, config=config))
        return [str(row[0]) for row in cursor.fetchall()]


def test_the_tie_pair_is_actually_tied(seeded: tuple[psycopg.Connection, object]) -> None:
    """The fixture's premise, asserted rather than assumed.

    Every assertion below is vacuous if the two rows are not really tied — a
    test for tie handling that runs on untied data passes for the wrong reason.
    """
    connection, corpus = seeded
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT ts_rank(search_vector, plainto_tsquery('english', %s))
            FROM chunk WHERE chunk_id::text = ANY(%s)
            """,
            (TIE_QUERY, list(corpus.tie_pair)),
        )
        ranks = [row[0] for row in cursor.fetchall()]
    assert len(ranks) == 2, "the fixture's tie pair is not both present"
    assert ranks[0] == ranks[1], f"the tie pair is not tied: {ranks}"


def test_the_candidate_set_repeats_exactly(seeded: tuple[psycopg.Connection, object]) -> None:
    """The same statement twice returns the same candidates in the same order."""
    connection, _ = seeded
    first = _candidates(connection, depth=50)
    second = _candidates(connection, depth=50)
    assert first == second


def test_each_arms_ordering_is_total(seeded: tuple[psycopg.Connection, object]) -> None:
    """Every `LIMIT` in the statement is preceded by an ordering that includes the key.

    Asserted on the statement text because it cannot be asserted on behaviour at
    this corpus size — see the module docstring. This is the structural form of
    the same guarantee: PostgreSQL's documented rule is that a row limit needs
    an ordering that constrains rows into a unique order, and `chunk_id` is what
    makes each arm's ordering unique. A statement satisfying this cannot return
    an unpredictable subset *whatever* the corpus size, which is the property a
    behavioural test on six rows cannot establish.
    """
    arms = [segment for segment in FUSION_SQL.split("LIMIT %(depth)s")[:-1]]
    assert len(arms) == 2, "expected two per-arm cuts in the statement"
    for index, arm in enumerate(arms):
        ordering = arm[arm.rindex("ORDER BY") :]
        assert "chunk_id" in ordering, (
            f"arm {index}'s ordering before its LIMIT does not include chunk_id, so the "
            f"ordering is not total and which rows survive the cut is unspecified"
        )


def test_a_tie_at_the_cut_selects_deterministically(
    seeded: tuple[psycopg.Connection, object],
) -> None:
    """A cut landing inside the tie takes the same member every time.

    Weaker than it looks at this corpus size, and labelled so: the module
    docstring records that this passes with the tie-break removed. Kept because
    it is the assertion that becomes load-bearing the moment the fixture grows,
    and because a regression that made it fail *here* would be severe.
    """
    connection, corpus = seeded
    observed = {tuple(_candidates(connection, depth=2)) for _ in range(5)}
    assert len(observed) == 1, (
        f"the candidate set at a cut inside the tie varied across runs: {observed}"
    )
    selected = set(next(iter(observed))) & set(corpus.tie_pair)
    assert len(selected) <= 1, "a cut of two admitted both tied rows; the cut is not binding"


def test_the_lower_identifier_wins_the_tie(seeded: tuple[psycopg.Connection, object]) -> None:
    """The tie-break is `chunk_id` ascending, which is what `parameters.py` publishes.

    Asserted on direction, not merely on stability: an ordering that was stable
    but descending would satisfy every determinism check above while disagreeing
    with the published `tie_break_key`, and the published value is what another
    epic reproduces a run from.
    """
    connection, corpus = seeded
    ordered = _candidates(connection, depth=50)
    first, second = sorted(corpus.tie_pair)
    assert ordered.index(first) < ordered.index(second), (
        "the tie broke toward the higher identifier; parameters.py publishes chunk_id ascending"
    )
