"""The determinism witness is itself witnessed.

FR-020, SC-012. `ordering_digest` is the value the contract offers in place of an
element-wise ordering comparison, so a digest that failed to move when the
ordering moved would make every determinism assertion downstream vacuous — the
same failure shape as the tie-break test that passed with the tie-break removed.

Both directions are asserted: equal orderings agree, and *unequal orderings
disagree*. The second is the one that decays silently.
"""

from __future__ import annotations

import os
import re
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
from api.retrieval.digest import EMPTY_ORDERING_DIGEST, ordering_digest  # noqa: E402
from api.retrieval.readiness import readiness, reranker_directory, warm_rerankers  # noqa: E402
from api.retrieval.routes import get_connection  # noqa: E402
from retrieval.fixtures.seed_chunks import seeded_corpus  # noqa: E402

RERANKER = Path(__file__).resolve().parents[4] / "data" / "reranker"

# --- the function, no database needed --------------------------------------


def test_the_same_sequence_digests_the_same() -> None:
    assert ordering_digest(["a", "b", "c"]) == ordering_digest(["a", "b", "c"])


def test_a_reordering_changes_the_digest() -> None:
    """The direction that matters. A digest insensitive to order witnesses nothing."""
    assert ordering_digest(["a", "b", "c"]) != ordering_digest(["a", "c", "b"])


def test_differently_split_sequences_do_not_collide() -> None:
    """Why the separator is not decorative.

    Joining bare identifiers would make `["ab", "c"]` and `["a", "bc"]` the same
    string, and a digest that can collide answers "did the order change?" with a
    confident no. Chunk identifiers are fixed-width UUIDs today, which is exactly
    the circumstance under which someone would remove the separator.
    """
    assert ordering_digest(["ab", "c"]) != ordering_digest(["a", "bc"])


def test_the_empty_sequence_has_a_digest() -> None:
    """FR-009's empty answer is still an answer, and it still made an ordering claim."""
    assert ordering_digest([]) == EMPTY_ORDERING_DIGEST
    assert EMPTY_ORDERING_DIGEST.startswith("sha256:")


def test_the_shape_matches_the_contract_pattern() -> None:
    """`^sha256:[0-9a-f]{64}$`, asserted rather than assumed."""
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", ordering_digest(["a"]))


# --- the served response ----------------------------------------------------

pytestmark_served = pytest.mark.skipif(
    os.environ.get("DATABASE_URL") is None,
    reason="the served digest needs real rows",
)


@pytest.fixture(scope="module", autouse=True)
def warmed() -> Iterator[None]:
    if (RERANKER / "provenance.json").is_file() and not readiness.sessions:
        warm_rerankers(reranker_directory(), warm_batch=4)
    readiness.encoder_ready = True
    yield


def _response(query: str) -> dict:
    config = load_retrieval_config({})
    connection = psycopg.connect(os.environ["DATABASE_URL"], options=connection_options(config))
    seeded_corpus(connection)

    def _connection() -> Iterator[psycopg.Connection]:
        yield connection

    app.dependency_overrides[get_connection] = _connection
    try:
        with TestClient(app) as client:
            return client.get("/api/v1/retrieval/search", params={"q": query}).json()
    finally:
        app.dependency_overrides.pop(get_connection, None)
        connection.rollback()
        connection.close()


@pytestmark_served
def test_the_served_digest_is_over_the_returned_ordering() -> None:
    """Not over the fused set, and not over anything else.

    Recomputed from `results` and compared: a digest taken at the fusion boundary
    would certify an ordering the caller never saw, because the deterministic
    route adds its matches afterwards.
    """
    body = _response("bronze relief valve NRH-80347")
    assert body["ordering_digest"] == ordering_digest(r["chunk_id"] for r in body["results"])


@pytestmark_served
def test_two_identical_queries_produce_the_same_digest() -> None:
    """FR-020, as one comparison instead of an element-wise walk."""
    assert (
        _response("bronze valve")["ordering_digest"] == _response("bronze valve")["ordering_digest"]
    )


@pytestmark_served
def test_generated_at_takes_no_part_in_it() -> None:
    """The one member two identical queries may differ in.

    Folding it in would make the digest differ on every call, which is a witness
    that never agrees — indistinguishable, from a test's side, from ordering that
    never repeats.
    """
    first, second = _response("valve"), _response("valve")
    assert first["generated_at"] != second["generated_at"]
    assert first["ordering_digest"] == second["ordering_digest"]
