"""Two differently configured applications, one enumerated observable set.

Spec FR-026, SC-013. The flag's sixth "arm" is service configuration rather
than a request parameter, so the only honest way to exercise it is to build the
two processes and compare — which is what this does.

**The compared set is enumerated, not diffed.** A parity test that compared whole
responses would fail on `generated_at` and pass on nothing, so it would be
deleted within a week and the guarantee would go with it. What is compared is
the named set FR-026 says the flag may not change; the dense candidate set, and
the ordering that follows from it, is the one permitted difference.
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
from api.retrieval.arms import observable_flag_surface  # noqa: E402
from api.retrieval.readiness import readiness, reranker_directory, warm_rerankers  # noqa: E402
from api.retrieval.routes import get_config, get_connection  # noqa: E402
from retrieval.fixtures.seed_chunks import seeded_corpus  # noqa: E402

RERANKER = Path(__file__).resolve().parents[4] / "data" / "reranker"

pytestmark = pytest.mark.skipif(
    os.environ.get("DATABASE_URL") is None or not (RERANKER / "provenance.json").is_file(),
    reason="flag parity needs a database and the vendored reranker",
)


@pytest.fixture(scope="module", autouse=True)
def warmed() -> Iterator[None]:
    if not readiness.sessions:
        warm_rerankers(reranker_directory(), warm_batch=4)
    readiness.encoder_ready = True
    yield


def _response(index_mode: str, query: str) -> dict:
    """One request against an application configured for `index_mode`.

    The configuration is overridden through the dependency, which is what makes
    this two *applications* rather than one application read twice: the route
    reads its configuration once per request through `get_config`, so replacing
    that dependency replaces every setting the request sees.
    """
    config = load_retrieval_config({"PRC_RETRIEVAL_INDEX_MODE": index_mode})
    connection = psycopg.connect(os.environ["DATABASE_URL"], options=connection_options(config))
    seeded_corpus(connection)

    def _connection() -> Iterator[psycopg.Connection]:
        yield connection

    app.dependency_overrides[get_connection] = _connection
    app.dependency_overrides[get_config] = lambda: config
    try:
        with TestClient(app) as client:
            return client.get("/api/v1/retrieval/search", params={"q": query}).json()
    finally:
        app.dependency_overrides.pop(get_connection, None)
        app.dependency_overrides.pop(get_config, None)
        connection.rollback()
        connection.close()


def test_the_flag_changes_nothing_in_the_observable_set() -> None:
    """SC-013. The enumerated surface is identical across both settings.

    Every entry here is something FR-026 says the flag does not control:
    filters, fusion, fetch depth, the tie-break, reranking and the route. A
    difference in any of them means the flag reached past index usage.
    """
    exact = _response("exact", "bronze relief valve")
    approximate = _response("approximate", "bronze relief valve")
    assert observable_flag_surface(None, exact) == observable_flag_surface(  # type: ignore[arg-type]
        None,  # type: ignore[arg-type]
        approximate,
    )


def test_the_flag_is_reported_in_the_response() -> None:
    """The setting in force travels with the results.

    Without it a figure measured on the approximate path is indistinguishable
    from one measured on the exact path, and SC-014's ablation row would not
    know which it was reading.
    """
    exact = _response("exact", "valve")
    approximate = _response("approximate", "valve")
    assert exact["ranking_parameters"]["index_mode"] == "exact"
    assert approximate["ranking_parameters"]["index_mode"] == "approximate"


def test_both_settings_rerank() -> None:
    """Reranking is shared code, so the flag cannot switch it off.

    Asserted rather than assumed because "the flag controls index usage only"
    fails silently in exactly this direction: an approximate path that skipped
    reranking would still return results, and only the ordering would be worse.
    """
    for mode in ("exact", "approximate"):
        body = _response(mode, "bronze valve")
        assert body["mode"]["reranked"] is True, f"{mode} did not rerank"
        assert body["reranking"]["candidates_scored"] > 0


def test_both_settings_run_the_deterministic_route() -> None:
    """Also shared. The route is not part of index usage."""
    for mode in ("exact", "approximate"):
        body = _response(mode, "NRH-80347")
        assert body["deterministic_route"]["recognised_tokens"] == ["NRH-80347"]


def test_the_permitted_difference_is_named() -> None:
    """FR-026 allows the dense candidate set to differ — that is what an ANN is.

    On six chunks the index is not used either way, so the sets coincide here.
    Recorded as a measurement rather than as evidence they always agree: the
    difference is *permitted*, and a corpus large enough to use the index is
    where it would appear.
    """
    exact = _response("exact", "bronze valve")
    approximate = _response("approximate", "bronze valve")
    assert {r["chunk_id"] for r in exact["results"]} == {
        r["chunk_id"] for r in approximate["results"]
    }
