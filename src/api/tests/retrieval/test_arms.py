"""Each arm runs on its own, and the flag controls index usage only.

Spec FR-025 to FR-028, SC-012, SC-013. E014 publishes the ablation; what is
asserted here is the property that makes an ablation possible — each arm is
independently runnable, so its figure is a measurement rather than a
subtraction.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from api.config import RetrievalConfigError, load_retrieval_config  # noqa: E402
from api.db import connection_options  # noqa: E402
from api.retrieval.arms import Arm, run_arm  # noqa: E402
from retrieval.fixtures.seed_chunks import seeded_corpus  # noqa: E402

pytestmark = pytest.mark.skipif(
    os.environ.get("DATABASE_URL") is None,
    reason="the arms run real statements against real rows",
)

#: A 384-dimension probe. Constant so two runs of the same arm are comparable —
#: a random vector would make the determinism assertions below meaningless.
PROBE = [0.05] * 384


@pytest.fixture
def seeded() -> Iterator[psycopg.Connection]:
    config = load_retrieval_config({})
    with psycopg.connect(os.environ["DATABASE_URL"], options=connection_options(config)) as conn:
        seeded_corpus(conn)
        yield conn
        conn.rollback()


@pytest.mark.parametrize("arm", list(Arm))
def test_every_arm_runs_independently(seeded: psycopg.Connection, arm: Arm) -> None:
    """FR-025. Five arms, each returning candidates on its own.

    Independently runnable is the requirement, not "reachable through the
    pipeline": an arm that only ran as part of the whole would make its figure a
    subtraction from the total rather than a measurement of itself.
    """
    config = load_retrieval_config({})
    result = run_arm(seeded, arm, "bronze relief valve", PROBE, config=config)
    assert result.arm is arm
    assert isinstance(result.chunk_ids, tuple)


def test_the_lexical_arm_returns_only_matching_rows(seeded: psycopg.Connection) -> None:
    """Unlike the dense arm, it has a match predicate — so it can return nothing.

    Worth asserting as the contrast: the dense arm always returns nearest
    neighbours, and knowing which arm has a threshold is what makes an empty
    lexical result readable rather than alarming.
    """
    config = load_retrieval_config({})
    matched = run_arm(seeded, Arm.LEXICAL, "bronze valve", PROBE, config=config)
    unmatched = run_arm(seeded, Arm.LEXICAL, "zzzznonexistent", PROBE, config=config)
    assert matched.chunk_ids
    assert unmatched.chunk_ids == ()


def test_the_dense_arm_always_returns_candidates(seeded: psycopg.Connection) -> None:
    """No threshold, by design — see the contrast above."""
    config = load_retrieval_config({})
    result = run_arm(seeded, Arm.DENSE, "zzzznonexistent", PROBE, config=config)
    assert result.chunk_ids


def test_the_lexical_arm_reports_the_flag_as_not_applying(
    seeded: psycopg.Connection,
) -> None:
    """It never touches the vector index.

    Reporting the configured value here would imply a choice that was never
    made, and an ablation table reading "lexical / exact" invites the question
    of what "lexical / approximate" would be.
    """
    config = load_retrieval_config({})
    result = run_arm(seeded, Arm.LEXICAL, "valve", PROBE, config=config)
    assert result.index_mode == "not_applicable"
    assert result.iterative_scan is False


@pytest.mark.parametrize("arm", list(Arm))
def test_an_arm_returns_the_same_candidates_on_two_runs(
    seeded: psycopg.Connection, arm: Arm
) -> None:
    """FR-020, per arm, on an unrebuilt index."""
    config = load_retrieval_config({})
    first = run_arm(seeded, arm, "bronze valve", PROBE, config=config)
    second = run_arm(seeded, arm, "bronze valve", PROBE, config=config)
    assert first.chunk_ids == second.chunk_ids


def test_fusion_returns_the_union_of_the_two_arms(seeded: psycopg.Connection) -> None:
    """The fused set is what the arms contributed, not a third retrieval.

    Asserted because it is the premise every ablation row rests on: if fusion
    could return a candidate neither arm produced, comparing an arm against
    fusion would be comparing against something else entirely.
    """
    config = load_retrieval_config({})
    lexical = run_arm(seeded, Arm.LEXICAL, "bronze valve", PROBE, config=config)
    dense = run_arm(seeded, Arm.DENSE, "bronze valve", PROBE, config=config)
    fused = run_arm(seeded, Arm.FUSED, "bronze valve", PROBE, config=config)
    assert set(fused.chunk_ids) == set(lexical.chunk_ids) | set(dense.chunk_ids)


def test_the_fused_set_is_bounded_by_twice_the_depth(
    seeded: psycopg.Connection,
) -> None:
    """FR-037's derived constraint: two arms of `depth` each, so at most 2×depth.

    The reranked count then takes the top `depth` of that, which is what makes
    "the top 50 of the fused ordering" a real cut rather than a restatement.
    """
    config = load_retrieval_config({})
    fused = run_arm(seeded, Arm.FUSED, "bronze valve", PROBE, config=config)
    assert len(fused.chunk_ids) <= config.fetch_depth * 2


# --- FR-026: one flag, index usage only -----------------------------------


def test_both_flag_settings_are_constructible() -> None:
    """SC-013's sixth arm is service configuration, not a request value.

    Which is why it is exercised by building two configurations rather than by
    passing a parameter.
    """
    exact = load_retrieval_config({"PRC_RETRIEVAL_INDEX_MODE": "exact"})
    approximate = load_retrieval_config({"PRC_RETRIEVAL_INDEX_MODE": "approximate"})
    assert exact.index_mode == "exact"
    assert approximate.index_mode == "approximate"


def test_the_flag_changes_nothing_but_index_usage_in_the_configuration() -> None:
    """FR-026. Every other setting is identical across the two.

    Compared field by field rather than by trusting the loader, because "the
    flag controls index usage and nothing else" is exactly the sort of claim
    that decays when a second setting is added next to it.
    """
    exact = load_retrieval_config({"PRC_RETRIEVAL_INDEX_MODE": "exact"})
    approximate = load_retrieval_config({"PRC_RETRIEVAL_INDEX_MODE": "approximate"})
    shared = {field for field in exact.model_dump() if field != "index_mode"}
    for field in shared:
        assert getattr(exact, field) == getattr(approximate, field), (
            f"{field} differs between the two flag settings; FR-026 makes the flag "
            f"control index usage and nothing else"
        )


def test_an_unknown_flag_value_is_refused() -> None:
    """Two values, closed. A third would be an index mode nobody implemented."""
    with pytest.raises(RetrievalConfigError, match="exact"):
        load_retrieval_config({"PRC_RETRIEVAL_INDEX_MODE": "approximately"})


def test_the_approximate_setting_reports_iterative_scan(
    seeded: psycopg.Connection,
) -> None:
    """AD-003. Strict-order iterative scan is the only compatible mode.

    Relaxed order improves filtered recall most and returns results slightly out
    of distance order, which contradicts FR-020 — so the mode that would help
    most is the one this epic cannot use.
    """
    config = load_retrieval_config({"PRC_RETRIEVAL_INDEX_MODE": "approximate"})
    result = run_arm(seeded, Arm.DENSE, "valve", PROBE, config=config)
    assert result.iterative_scan is True
    assert result.index_mode == "approximate"


def test_the_exact_setting_does_not(seeded: psycopg.Connection) -> None:
    config = load_retrieval_config({"PRC_RETRIEVAL_INDEX_MODE": "exact"})
    result = run_arm(seeded, Arm.DENSE, "valve", PROBE, config=config)
    assert result.iterative_scan is False


def test_both_settings_return_the_same_candidates_on_this_corpus(
    seeded: psycopg.Connection,
) -> None:
    """The one permitted difference, and why it does not appear here.

    FR-026 allows the dense candidate set to differ between the settings — that
    is what an approximate index *is*. On six chunks the index is not used
    either way, so the sets coincide. Asserted so the parity claim is anchored
    to a measurement rather than left as an expectation, and labelled so nobody
    reads it as proof the two settings always agree.
    """
    exact = load_retrieval_config({"PRC_RETRIEVAL_INDEX_MODE": "exact"})
    approximate = load_retrieval_config({"PRC_RETRIEVAL_INDEX_MODE": "approximate"})
    a = run_arm(seeded, Arm.DENSE, "bronze valve", PROBE, config=exact)
    b = run_arm(seeded, Arm.DENSE, "bronze valve", PROBE, config=approximate)
    assert set(a.chunk_ids) == set(b.chunk_ids)
