"""Reranking through the route: both arms, degradation, and truncation.

Spec FR-015 to FR-025. The reranker is what carries ranking quality — fusion at
depth 50 with k=60 separates rank 1 from rank 50 by a factor of 1.8, so the
fused ordering is weak *by construction*. These assertions are about the stage
that fixes that, and about what the system says when it cannot run it.
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
from api.db import connection_options  # noqa: E402
from api.main import app  # noqa: E402
from api.retrieval.readiness import (  # noqa: E402
    ReadinessState,
    RerankerFailure,
    RetrievalReadiness,
    readiness,
    reranker_directory,
    warm_rerankers,
)
from api.retrieval.routes import get_connection  # noqa: E402
from retrieval.fixtures.seed_chunks import seeded_corpus  # noqa: E402

RERANKER = Path(__file__).resolve().parents[4] / "data" / "reranker"

pytestmark = pytest.mark.skipif(
    os.environ.get("DATABASE_URL") is None or not (RERANKER / "provenance.json").is_file(),
    reason="reranking needs a database and the vendored reranker",
)


@pytest.fixture(scope="module", autouse=True)
def warmed() -> Iterator[None]:
    """Warm both graphs once, as the lifespan hook does.

    Module-scoped because FR-017 forbids loading a graph on a request path, and
    a per-test warm-up would make the suite assert against a system that does
    the thing the requirement bars.
    """
    if not readiness.sessions:
        warm_rerankers(reranker_directory(), warm_batch=4)
    readiness.encoder_ready = True
    yield


@pytest.fixture
def client() -> Iterator[TestClient]:
    connection = psycopg.connect(
        os.environ["DATABASE_URL"], options=connection_options(load_retrieval_config())
    )
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


def test_the_default_arm_reranks(client: TestClient) -> None:
    body = client.get("/api/v1/retrieval/search", params={"q": "bronze relief valve"}).json()
    assert body["mode"]["reranked"] is True
    assert body["mode"]["unreranked_reason"] is None
    assert body["reranking"]["candidates_scored"] > 0
    assert body["reranking"]["precision"] == "int8"


def test_reranking_changes_the_order_not_the_membership(client: TestClient) -> None:
    """Reranking reorders; it must not change which candidates are present.

    Compared against the fusion-only arm rather than against a stored
    expectation: two live orderings make this a measurement of the reranker
    rather than a golden file nobody re-derives.
    """
    fused = client.get(
        "/api/v1/retrieval/search", params={"q": "bronze relief valve", "arm": "fused"}
    ).json()
    reranked = client.get("/api/v1/retrieval/search", params={"q": "bronze relief valve"}).json()
    assert {r["chunk_id"] for r in fused["results"]} == {r["chunk_id"] for r in reranked["results"]}


def test_the_full_precision_arm_is_selectable(client: TestClient) -> None:
    """FR-025. The FP32 graph exists to measure what quantization costs."""
    body = client.get(
        "/api/v1/retrieval/search",
        params={"q": "bronze valve", "arm": "fused_reranked_full_precision"},
    ).json()
    assert body["mode"]["reranked"] is True
    assert body["reranking"]["precision"] == "fp32"


def test_the_two_precisions_are_reported_distinctly(client: TestClient) -> None:
    """An INT8 figure and an FP32 figure are different measurements.

    A response that did not say which produced it would be unusable for the
    comparison FR-025 exists to support.
    """
    int8 = client.get("/api/v1/retrieval/search", params={"q": "valve"}).json()
    fp32 = client.get(
        "/api/v1/retrieval/search",
        params={"q": "valve", "arm": "fused_reranked_full_precision"},
    ).json()
    assert int8["reranking"]["precision"] != fp32["reranking"]["precision"]
    assert int8["mode"]["arm_served"] != fp32["mode"]["arm_served"]


@pytest.mark.parametrize("arm", ["lexical", "dense", "fused"])
def test_an_arm_excluding_reranking_says_so_without_claiming_degradation(
    client: TestClient, arm: str
) -> None:
    """FR-022. Not reranked is not the same as degraded.

    Claiming fusion-only degradation for a caller who asked for the fused arm
    would be a false alarm, and the vocabulary exists so a consumer can tell a
    choice from a fault.
    """
    body = client.get("/api/v1/retrieval/search", params={"q": "valve", "arm": arm}).json()
    assert body["mode"]["reranked"] is False
    assert body["mode"]["unreranked_reason"] == "arm_excludes_reranking"
    assert body["mode"]["degraded"] is False


def test_an_empty_candidate_set_reports_nothing_to_score(client: TestClient) -> None:
    """Also not degradation. There was simply nothing to rank."""
    connection = next(iter(client.app.dependency_overrides[get_connection]()))
    with connection.cursor() as cursor:
        cursor.execute("DELETE FROM chunk")
    body = client.get("/api/v1/retrieval/search", params={"q": "valve"}).json()
    assert body["mode"]["unreranked_reason"] == "no_candidates_to_score"
    assert body["mode"]["degraded"] is False


def test_an_unknown_arm_is_refused(client: TestClient) -> None:
    response = client.get("/api/v1/retrieval/search", params={"q": "valve", "arm": "wishful"})
    assert response.status_code == 422
    assert response.json()["detail"]["type"] == "unknown-arm"


def test_every_response_says_which_arm_served_it(client: TestClient) -> None:
    """FR-023. The requested and served arms are both recorded.

    That pair is what makes the guarantee structural: an output claiming
    `fused_reranked` while `arm_served` was `fused` is caught by comparing two
    fields in the record rather than by remembering to consult a flag.
    """
    body = client.get("/api/v1/retrieval/search", params={"q": "valve"}).json()
    assert body["mode"]["arm_requested"] == "fused_reranked"
    assert body["mode"]["arm_served"] == "fused_reranked"


def test_the_sequence_limit_is_published_with_the_length_distribution(
    client: TestClient,
) -> None:
    """FR-019. A limit without the distribution cannot be acted on.

    A truncated fraction alone does not say whether the cut removed a trailing
    clause or half the passage, and a passage whose evidence was truncated away
    still gets a confident score.
    """
    body = client.get("/api/v1/retrieval/search", params={"q": "valve"}).json()
    reranking = body["reranking"]
    assert reranking["sequence_limit_tokens"] == 512
    assert len(reranking["candidate_token_lengths"]) == reranking["candidates_scored"]
    assert reranking["candidates_truncated"] >= 0


def test_readiness_reports_ready_when_both_graphs_are_warm(client: TestClient) -> None:
    body = client.get("/api/v1/retrieval/readyz").json()
    assert body["status"] == "ready"
    assert body["degraded"] is False
    assert set(body["available_arms"]) == {"int8", "fp32"}


def test_a_process_with_no_reranker_is_ready_degraded_not_unready() -> None:
    """FR-021. The distinction is the whole requirement.

    Reporting not-ready would have an orchestrator restart the container in a
    loop over a fault restarting cannot fix, while a working fusion-only service
    sat unused.
    """
    state = RetrievalReadiness(encoder_ready=True)
    assert state.state is ReadinessState.READY_DEGRADED
    assert state.degraded is True
    assert "fusion-only" in state.as_dict()["statement"].lower()


def test_a_process_with_no_encoder_is_not_ready() -> None:
    """The encoder is load-bearing in a way the reranker is not.

    Without a query embedding the dense arm cannot run at all, so this is
    genuine unreadiness rather than degradation.
    """
    state = RetrievalReadiness(encoder_ready=False)
    assert state.state is ReadinessState.NOT_READY


def test_a_partially_loaded_reranker_stays_ready() -> None:
    """One graph up, one down: the process serves, and names the missing one.

    Pulling it from service would trade a working product for a missing
    measurement.
    """
    state = RetrievalReadiness(
        encoder_ready=True,
        sessions={"int8": object()},
        failures=[RerankerFailure("fp32", "load_failed", "absent")],
    )
    assert state.state is ReadinessState.READY
    assert state.degraded is False
    assert "fp32" in state.as_dict()["statement"]


def test_a_degraded_response_states_it_is_fusion_only() -> None:
    """FR-022. A flag with no sentence beside it is skipped by people."""
    statement = RetrievalReadiness(encoder_ready=True).as_dict()["statement"]
    assert "fusion-only" in statement.lower()
    assert "weak" in statement.lower()
