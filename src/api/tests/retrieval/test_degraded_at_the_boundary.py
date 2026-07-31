"""Degradation forced where it actually happens, not by setting the flag.

Spec FR-021, FR-022, FR-024, **SC-011**. `plan.md`'s coverage map states the
obligation in as many words: the failure is forced *at the artifact-loading
boundary* — an absent, unreadable or digest-mismatched graph — so the exception
arises where FR-016's verification runs, and **setting the flag directly does not
discharge it**.

`test_reranking.py` constructs `RetrievalReadiness(encoder_ready=True)` with no
sessions and asserts the state that follows. That is a real assertion about the
state machine and it is worth keeping, but it is not this one: it starts from the
degraded state rather than arriving at it, so it would pass unchanged against a
`warm_rerankers` that raised, that swallowed the failure without recording it, or
that never verified the artifact at all.

**Three loading failures, not one.** Absent, corrupt, and digest-mismatched fail
at three different points — the path check, the runtime's parser, and FR-016's
verification — and a handler that caught two of them would look correct against
a test that only tried one.
"""

from __future__ import annotations

import json
import os
import shutil
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
    RetrievalReadiness,
    warm_rerankers,
)
from api.retrieval.routes import get_connection  # noqa: E402
from retrieval.fixtures.seed_chunks import seeded_corpus  # noqa: E402

RERANKER = Path(__file__).resolve().parents[4] / "data" / "reranker"

pytestmark = pytest.mark.skipif(
    not (RERANKER / "provenance.json").is_file(),
    reason="the boundary is the vendored artifact; there is nothing to break without it",
)


def _state_after_loading(directory: Path) -> RetrievalReadiness:
    """Attempt a real load against `directory` into a fresh state.

    A fresh `RetrievalReadiness` rather than the module-level one, because the
    module-level object is shared with every other test in the session and a
    warm session left in it would make this pass for the wrong reason — and,
    worse, would make *other* modules fail for reasons belonging to this one.
    """
    state = RetrievalReadiness(encoder_ready=True)
    warm_rerankers(directory, warm_batch=1, state=state)
    return state


def test_an_absent_graph_degrades_rather_than_raises(tmp_path: Path) -> None:
    """The first of the three boundaries: nothing is there at all.

    Degrading rather than raising is the requirement. A startup that raised
    would take a working fusion-only service down over a fault fusion does not
    need.
    """
    state = _state_after_loading(tmp_path / "nothing-here")
    assert state.state is ReadinessState.READY_DEGRADED
    assert state.degraded is True
    assert not state.sessions
    assert state.failures, "the failure was swallowed; a degraded service must say why"


def test_a_corrupt_graph_degrades(tmp_path: Path) -> None:
    """The second: the file exists and the runtime cannot parse it.

    A distinct path from absence — this one fails inside ONNX Runtime rather
    than in a path check, and a handler catching only `FileNotFoundError` would
    pass the test above and crash here.
    """
    broken = tmp_path / "reranker"
    shutil.copytree(RERANKER, broken)
    for graph in broken.glob("model-*.onnx"):
        graph.write_bytes(b"not a serialised graph")
    state = _state_after_loading(broken)
    assert state.state is ReadinessState.READY_DEGRADED
    assert not state.sessions


def test_a_digest_mismatch_degrades_at_verification(tmp_path: Path) -> None:
    """The third, and the one that matters most: FR-016's verification refuses.

    The graph here is *valid* — the runtime would load it happily. What stops it
    is the recorded digest disagreeing, which is the check that exists to catch
    a substituted artifact. If this degraded by some other route the substitution
    would be caught only by luck.
    """
    tampered = tmp_path / "reranker"
    shutil.copytree(RERANKER, tampered)
    provenance_path = tampered / "provenance.json"
    provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
    # Every recorded digest is rewritten, not just one: the graphs load
    # independently (AD-013), so leaving one intact would leave one arm serving
    # and the process READY rather than READY_DEGRADED.
    provenance["files"] = {name: "0" * 64 for name in provenance["files"]}
    provenance_path.write_text(json.dumps(provenance), encoding="utf-8")

    state = _state_after_loading(tampered)
    assert state.state is ReadinessState.READY_DEGRADED
    assert state.failures
    assert any("digest" in failure.detail.lower() for failure in state.failures), (
        f"degraded, but not at the verification boundary: {[f.detail for f in state.failures]}. "
        f"SC-011 is discharged by the failure arising where FR-016's check runs"
    )


def test_the_degraded_state_says_fusion_only_and_names_what_failed(tmp_path: Path) -> None:
    """FR-022. A flag with no sentence beside it is skipped by people."""
    state = _state_after_loading(tmp_path / "nothing-here")
    reported = state.as_dict()
    assert "fusion-only" in reported["statement"].lower()
    assert reported["available_arms"] == []


@pytest.mark.skipif(
    os.environ.get("DATABASE_URL") is None,
    reason="the third observable is that results are still returned",
)
def test_a_degraded_process_still_returns_results(tmp_path: Path) -> None:
    """FR-024's third observable, and the one that makes degradation worth having.

    Ready-degraded and a fusion-only statement are both satisfiable by a service
    that returns nothing. What SC-011 is actually about is that the answers keep
    coming — so this issues a real query against a process whose graphs failed to
    load and asserts results, with the mode recorded on the evaluation-facing
    output.
    """
    # Patched on `routes`, not on `readiness`. The route imported the object by
    # name at module load, so rebinding the defining module's attribute leaves
    # the route holding the original — a substitution that looks applied and
    # is not, which is how this test first passed against an undegraded process.
    from api.retrieval import routes as routes_module

    original = routes_module.readiness
    degraded = RetrievalReadiness(encoder_ready=True)
    warm_rerankers(tmp_path / "nothing-here", warm_batch=1, state=degraded)
    assert degraded.state is ReadinessState.READY_DEGRADED

    config = load_retrieval_config({})
    connection = psycopg.connect(os.environ["DATABASE_URL"], options=connection_options(config))
    seeded_corpus(connection)

    def _connection() -> Iterator[psycopg.Connection]:
        yield connection

    app.dependency_overrides[get_connection] = _connection
    routes_module.readiness = degraded
    try:
        with TestClient(app) as client:
            body = client.get(
                "/api/v1/retrieval/search", params={"q": "bronze relief valve"}
            ).json()
        assert body["results"], "a degraded service returned nothing; fusion still works"
        assert body["mode"]["degraded"] is True
        assert body["mode"]["reranked"] is False
        assert body["mode"]["unreranked_reason"] == "reranker_unavailable", (
            "the reason must distinguish a fault from a choice; an arm that excludes "
            "reranking is not a degraded service"
        )
    finally:
        routes_module.readiness = original
        app.dependency_overrides.pop(get_connection, None)
        connection.rollback()
        connection.close()
