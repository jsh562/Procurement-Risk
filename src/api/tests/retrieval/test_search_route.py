"""The search route, end to end against a real corpus.

Spec FR-007, FR-008, FR-009, FR-029, FR-046. This is the first place the whole
path runs together: the query is embedded through the gateway encoder, the
identity gate fires before any search, one statement ranks, and results project
from chunk rows.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.config import load_retrieval_config  # noqa: E402
from api.main import app  # noqa: E402
from api.retrieval.routes import get_connection  # noqa: E402
from retrieval.fixtures.seed_chunks import seeded_corpus  # noqa: E402

ENCODER = Path(__file__).resolve().parents[4] / "data" / "encoder"

pytestmark = pytest.mark.skipif(
    os.environ.get("DATABASE_URL") is None or not (ENCODER / "digests.json").is_file(),
    reason="the search route needs a database and the vendored encoder",
)


@pytest.fixture
def client() -> Iterator[TestClient]:
    """A client whose connection is a transaction the test rolls back.

    Overriding the dependency rather than seeding a shared database: the route
    then reads exactly the rows this test wrote, and writes nothing another test
    inherits.
    """
    url = os.environ["DATABASE_URL"]
    config = load_retrieval_config()
    from api.db import connection_options

    connection = psycopg.connect(url, options=connection_options(config))
    seeded_corpus(connection)

    def _connection() -> Iterator[psycopg.Connection]:
        yield connection

    app.dependency_overrides[get_connection] = _connection
    try:
        yield TestClient(app)
    finally:
        app.dependency_overrides.pop(get_connection, None)
        connection.rollback()
        connection.close()


def test_a_query_returns_ranked_passages(client: TestClient) -> None:
    response = client.get("/api/v1/retrieval/search", params={"q": "bronze relief valve"})
    assert response.status_code == 200
    body = response.json()
    assert body["result_count"] == len(body["results"])
    assert body["results"], "the fixture corpus contains a matching passage"


def test_every_result_carries_the_page_it_was_printed_on(client: TestClient) -> None:
    """Principle I. The page is the field a reader follows and cannot check."""
    body = client.get("/api/v1/retrieval/search", params={"q": "bronze valve"}).json()
    for result in body["results"]:
        assert result["page_number"] >= 1
        assert result["document_id"]
        assert result["project_id"].startswith("PRJ-")


def test_a_nonsense_query_still_returns_candidates(client: TestClient) -> None:
    """The dense arm has no relevance threshold, and that is by design.

    Worth asserting because the intuitive expectation is the opposite. Vector
    search returns the *nearest* neighbours whatever the query, so a six-chunk
    corpus answers "zzzznonexistenttokenzzzz" with six candidates: the lexical
    arm contributes none, the dense arm contributes all of them, and fusion
    unions the two.

    This is not FR-009 failing. FR-009 forbids **padding** a short result set to
    reach a target; it does not promise that an irrelevant query returns
    nothing, and no arm here could deliver that without a similarity cutoff
    nobody has chosen. It is also the concrete reason the reranker carries
    ranking quality — it is the only stage that can push a near neighbour which
    answers nothing down the list.
    """
    body = client.get("/api/v1/retrieval/search", params={"q": "zzzznonexistenttokenzzzz"}).json()
    assert body["result_count"] == len(body["results"])
    assert body["fused_candidate_count"] > 0


def test_an_empty_corpus_returns_an_empty_set(client: TestClient) -> None:
    """FR-009, on the condition that actually produces it.

    With no chunks there is nothing for either arm to return, so the response is
    `results: []` with `result_count: 0` — a complete, successful answer rather
    than an error or a widened search.
    """
    from api.retrieval.routes import get_connection

    connection = next(iter(client.app.dependency_overrides[get_connection]()))
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM chunk")
    body = client.get("/api/v1/retrieval/search", params={"q": "bronze valve"}).json()
    assert body["result_count"] == 0
    assert body["results"] == []
    assert body["fused_candidate_count"] == 0


def test_the_ranked_portion_is_bounded_by_limit(client: TestClient) -> None:
    body = client.get("/api/v1/retrieval/search", params={"q": "bronze valve", "limit": 1}).json()
    assert body["result_count"] <= 1


def test_a_limit_above_the_fetch_depth_is_refused(client: TestClient) -> None:
    """A caller cannot ask for more ranked results than fusion retrieves.

    The extra would be padding, which FR-009 forbids — so it is refused at the
    boundary rather than silently clamped, because a clamped request answers a
    different question than the one asked.
    """
    response = client.get("/api/v1/retrieval/search", params={"q": "valve", "limit": 9999})
    assert response.status_code == 422
    assert response.json()["detail"]["type"] == "limit-above-fetch-depth"


def test_an_over_long_query_is_refused_rather_than_truncated(client: TestClient) -> None:
    """FR-046. Truncating changes what was asked without saying so."""
    response = client.get("/api/v1/retrieval/search", params={"q": "x" * 1_001})
    assert response.status_code == 422


def test_the_response_publishes_the_parameters_in_force(client: TestClient) -> None:
    """FR-029, with every result set rather than only evaluation ones.

    Whether a result will be consumed by an evaluation is not knowable when it
    is produced.
    """
    body = client.get("/api/v1/retrieval/search", params={"q": "valve"}).json()
    parameters = body["ranking_parameters"]
    assert parameters["fusion_constant"] == 60
    assert parameters["tie_break_key"] == "chunk_id_ascending"
    assert parameters["fetch_depth"] == parameters["reranked_count"]
    assert parameters["search_breadth"] >= parameters["fetch_depth"]
    assert parameters["encoder"]["model_id"]


def test_the_response_never_names_bm25(client: TestClient) -> None:
    """FR-006, asserted over the emitted artifact rather than over intent."""
    raw = client.get("/api/v1/retrieval/search", params={"q": "valve"}).text
    assert "bm25" not in raw.lower()


def test_the_lexical_arm_declares_it_uses_no_corpus_statistics(client: TestClient) -> None:
    body = client.get("/api/v1/retrieval/search", params={"q": "valve"}).json()
    lexical = body["ranking_parameters"]["lexical"]
    assert lexical["uses_corpus_wide_term_statistics"] is False
    assert lexical["arm"] == "native_tsvector_ranking"


def test_the_same_query_twice_returns_the_same_ordering(client: TestClient) -> None:
    """FR-020, through the route rather than against the statement alone."""
    first = client.get("/api/v1/retrieval/search", params={"q": "bronze valve"}).json()
    second = client.get("/api/v1/retrieval/search", params={"q": "bronze valve"}).json()
    assert [r["chunk_id"] for r in first["results"]] == [r["chunk_id"] for r in second["results"]]


def test_an_encoder_identity_mismatch_refuses_before_searching(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """FR-007. The refusal names both identities, so the failure says which side to fix.

    Forced at the corpus-identity boundary rather than by swapping the encoder,
    because that is the direction the mismatch actually arrives from: the
    vectors were written once and the serving process is what changes.
    """
    import api.retrieval.routes as routes

    monkeypatch.setattr(
        routes, "corpus_encoder_identity", lambda _conn: ("other/model", "deadbeef")
    )
    response = client.get("/api/v1/retrieval/search", params={"q": "valve"})
    assert response.status_code == 500
    detail = response.json()["detail"]
    assert detail["type"] == "encoder-identity-mismatch"
    assert detail["corpus_model_id"] == "other/model"
    assert detail["encoder_model_id"] != detail["corpus_model_id"]


# ---------------------------------------------------------------------------
# FR-010 to FR-014: the deterministic route, through the response
# ---------------------------------------------------------------------------


def test_the_response_reports_what_the_route_did(client: TestClient) -> None:
    """The route's outcome is stated, not implied by the result list."""
    body = client.get("/api/v1/retrieval/search", params={"q": "NRH-80347"}).json()
    route = body["deterministic_route"]
    assert route["recognised_tokens"] == ["NRH-80347"]
    assert route["matched_tokens"] == ["NRH-80347"]


def test_a_fall_through_is_reported_as_such(client: TestClient) -> None:
    """FR-011. A well-formed designation the corpus lacks is not an empty answer."""
    body = client.get("/api/v1/retrieval/search", params={"q": "ZZZ-99999"}).json()
    route = body["deterministic_route"]
    assert route["recognised_tokens"] == ["ZZZ-99999"]
    assert route["matched_tokens"] == []
    assert route["fell_through"] is True
    assert body["result_count"] > 0, "hybrid retrieval still answers on its own"


def test_the_route_adds_rather_than_replaces(client: TestClient) -> None:
    """FR-012, measured against the same query with no recognisable token.

    The comparison is the assertion: every chunk the plain query returned is
    still present when the route fires.
    """
    plain = client.get("/api/v1/retrieval/search", params={"q": "pressure relief valve"}).json()
    routed = client.get(
        "/api/v1/retrieval/search", params={"q": "pressure relief valve NRH-80347"}
    ).json()
    plain_ids = {r["chunk_id"] for r in plain["results"]}
    routed_ids = {r["chunk_id"] for r in routed["results"]}
    assert plain_ids <= routed_ids, (
        f"the route removed {sorted(plain_ids - routed_ids)}; FR-012 makes it a union"
    )


def test_a_route_match_carries_no_fused_rank(client: TestClient) -> None:
    """FR-013. A deterministic match was never scored, so it has no rank.

    Giving it one would let it sort among ranked results as though it had
    competed — the additive union quietly becoming a substitution.
    """
    body = client.get("/api/v1/retrieval/search", params={"q": "NRH-80347"}).json()
    for result in body["results"]:
        if result["match_kind"] == "deterministic_identifier":
            assert result["fused_rank"] is None
        else:
            assert result["fused_rank"] is not None


def test_route_matches_are_counted_outside_limit(client: TestClient) -> None:
    """FR-046. `limit` bounds the ranked portion only.

    A single ceiling over both would make the route subtractive at exactly the
    boundary FR-012 exists to protect.
    """
    body = client.get(
        "/api/v1/retrieval/search", params={"q": "valve NRH-80347", "limit": 1}
    ).json()
    ranked = [r for r in body["results"] if r["match_kind"] == "ranked_relevance"]
    assert len(ranked) <= 1
    assert body["result_count"] == len(ranked) + body["deterministic_route"]["added_count"]


def test_no_chunk_appears_twice(client: TestClient) -> None:
    """One chunk, one account of how it was found."""
    body = client.get(
        "/api/v1/retrieval/search", params={"q": "pressure relief valve NRH-80347"}
    ).json()
    ids = [r["chunk_id"] for r in body["results"]]
    assert len(ids) == len(set(ids))
