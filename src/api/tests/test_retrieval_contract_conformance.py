"""E008's served response conforms to E008's committed contract.

AD-014. E010 added a conformance module and it names one contract **by path**:

    CONTRACT = … / "00010-risk-ranked-coordinator-worklist" / … / "openapi.yaml"

so it covers E010's surface and nothing else. E008's contract would ship
unvalidated — the same shape as the benchmark module that runs nowhere and the
merge gate that runs against an empty chunk table: a check that exists, passes,
and does not cover the thing you assumed it did.

**A second module rather than an extension.** Extending E010's would make one
test own two epics' contracts and fail for reasons belonging to the other;
following it keeps each contract's authority with its own epic.

**Closure is asserted against the contract, not against a hand-written key set.**
E010's own reasoning, adopted here: *"Hand-written key sets are a bad way to
assert closure over a whole document, because the ones nobody wrote are exactly
the ones that drift."* This response carries members no hand-written list would
be likely to enumerate — `weighted_fields`, `match_kind`, `deterministic_route`,
`no_interval_reason`, `ordering_digest`.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import psycopg
import pytest
import yaml
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from retrieval.fixtures.seed_chunks import seeded_corpus  # noqa: E402

from api.config import load_retrieval_config  # noqa: E402
from api.db import connection_options  # noqa: E402
from api.main import app  # noqa: E402
from api.retrieval.readiness import readiness, reranker_directory, warm_rerankers  # noqa: E402
from api.retrieval.routes import get_connection  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
CONTRACT = (
    REPO_ROOT / "specs" / "00008-hybrid-retrieval-and-reranking" / "contracts" / "openapi.yaml"
)
RERANKER = REPO_ROOT / "data" / "reranker"

pytestmark = pytest.mark.skipif(
    os.environ.get("DATABASE_URL") is None or not CONTRACT.is_file(),
    reason="conformance needs a database and E008's committed contract",
)


@pytest.fixture(scope="module")
def contract() -> dict[str, Any]:
    return yaml.safe_load(CONTRACT.read_text(encoding="utf-8"))


@pytest.fixture(scope="module", autouse=True)
def warmed() -> Iterator[None]:
    if (RERANKER / "provenance.json").is_file() and not readiness.sessions:
        warm_rerankers(reranker_directory(), warm_batch=4)
    readiness.encoder_ready = True
    yield


@pytest.fixture
def body() -> Iterator[dict[str, Any]]:
    config = load_retrieval_config({})
    connection = psycopg.connect(os.environ["DATABASE_URL"], options=connection_options(config))
    seeded_corpus(connection)

    def _connection() -> Iterator[psycopg.Connection]:
        yield connection

    app.dependency_overrides[get_connection] = _connection
    try:
        with TestClient(app) as client:
            yield client.get(
                "/api/v1/retrieval/search", params={"q": "bronze relief valve NRH-80347"}
            ).json()
    finally:
        app.dependency_overrides.pop(get_connection, None)
        connection.rollback()
        connection.close()


def test_the_contract_declares_the_path_the_route_serves(contract: dict[str, Any]) -> None:
    """The path the route actually serves is the one the contract describes.

    Asserted first because every assertion below is vacuous if the contract
    documents a different endpoint — which is precisely how E010's module came
    to cover one epic while reading as though it covered the surface.

    Composed from the server base rather than matched against a literal: OpenAPI
    path keys are relative to `servers[].url`, so `/retrieval/search` under a
    `/api/v1` base and the mounted `/api/v1/retrieval/search` are the same
    endpoint. Comparing the key alone would fail against a correct document, and
    "fix the test until it passes" is how a conformance module stops conforming
    to anything.
    """
    bases = [server["url"].rstrip("/") for server in contract["servers"]]
    served = {f"{base}{path}" for base in bases for path in contract["paths"]}
    assert "/api/v1/retrieval/search" in served, (
        f"the contract describes {sorted(served)}, none of which is the served route"
    )


def test_the_response_carries_every_required_member(
    contract: dict[str, Any], body: dict[str, Any]
) -> None:
    """Required means required, and the served body is where that is decided."""
    schema = contract["components"]["schemas"]["RetrievalResponse"]
    missing = [name for name in schema.get("required", []) if name not in body]
    assert not missing, (
        f"the served response omits contract-required members: {missing}. "
        f"A contract requiring a member the surface never sends is a document "
        f"describing a different service."
    )


def test_the_response_sends_no_member_the_contract_forbids(
    contract: dict[str, Any], body: dict[str, Any]
) -> None:
    """`additionalProperties: false` is a closure claim, and it is checked.

    The direction that decays is this one: a member added to the response and
    not to the contract passes every required-field check while making the
    document false.
    """
    schema = contract["components"]["schemas"]["RetrievalResponse"]
    if schema.get("additionalProperties") is not False:
        pytest.skip("the response schema is not closed; there is nothing to assert")
    declared = set(schema.get("properties", {}))
    undeclared = set(body) - declared
    assert not undeclared, (
        f"the response sends members the contract does not declare: {sorted(undeclared)}"
    )


def test_the_counting_invariants_hold(body: dict[str, Any]) -> None:
    """The contract states them; this is where they are true or false.

    `result_count == len(results)` is the one a caller relies on most, and
    `result_count == ranked + route_added` is what makes FR-012's additive union
    checkable from one response without a route-disable switch.
    """
    assert body["result_count"] == len(body["results"])
    ranked = [r for r in body["results"] if r["match_kind"] == "ranked_relevance"]
    added = body["deterministic_route"]["added_count"]
    assert body["result_count"] == len(ranked) + added


def test_every_result_carries_its_match_kind_and_rank_convention(
    body: dict[str, Any],
) -> None:
    """FR-013. A deterministic match has no fused rank; a ranked one has one."""
    for result in body["results"]:
        assert result["match_kind"] in {"ranked_relevance", "deterministic_identifier"}
        if result["match_kind"] == "deterministic_identifier":
            assert result["fused_rank"] is None
        else:
            assert isinstance(result["fused_rank"], int)


def test_the_mode_block_is_complete(body: dict[str, Any]) -> None:
    """FR-023. Requested and served arms both present, so the pair is comparable."""
    mode = body["mode"]
    for member in ("arm_requested", "arm_served", "degraded", "reranked"):
        assert member in mode
