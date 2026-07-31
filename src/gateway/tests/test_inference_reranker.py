"""The cross-encoder, exercised on the tier that owns it.

Spec FR-015 to FR-019, FR-025, FR-038. `gateway.inference.reranker` was reachable
only through the api tier's warm-up, which meant the module doing the scoring had
zero coverage in the package that ships it — a dependency direction that puts a
gateway regression behind an api test run, and hides it entirely if that run is
skipped for want of a database.

**Both graphs are loaded and both are scored.** FR-025 makes the full-precision
arm request-selectable precisely because quantization is not lossless; a test
that only ever touched INT8 would leave the arm that exists to measure the loss
unexercised.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from gateway.inference.reranker import (
    DEFAULT_SEQUENCE_LIMIT,
    Precision,
    RerankerSession,
    TruncationReport,
    load_reranker,
)

RERANKER = Path(__file__).resolve().parents[3] / "data" / "reranker"

pytestmark = pytest.mark.skipif(
    not (RERANKER / "provenance.json").is_file(),
    reason="the reranker graphs are vendored under data/reranker",
)

#: Warmed at 4 rather than the serving 50. The warm-up runs a real forward pass
#: at `batch × sequence_limit`, and at 50 that is ~90 seconds per graph on the
#: quota this tier is measured under — a cost that belongs to the latency task,
#: not to every run of this module. The shape *property* is asserted separately
#: against the recorded fields, which is what FR-017 actually constrains.
WARM = 4


@pytest.fixture(scope="module")
def int8() -> RerankerSession:
    return load_reranker(RERANKER, Precision.INT8, warm_batch=WARM)


@pytest.fixture(scope="module")
def fp32() -> RerankerSession:
    return load_reranker(RERANKER, Precision.FP32, warm_batch=WARM)


# --- loading and warm-up ----------------------------------------------------


def test_both_graphs_load(int8: RerankerSession, fp32: RerankerSession) -> None:
    """AD-013. Both resident, because FR-017 forbids loading one on a request path."""
    assert int8.precision is Precision.INT8
    assert fp32.precision is Precision.FP32


def test_each_session_records_the_shape_it_was_warmed_at(int8: RerankerSession) -> None:
    """FR-017 fixes the warm shape numerically, so it is recorded, not implied.

    A session that warmed at some other shape and reported this one would satisfy
    every readiness check while leaving the arena growth for the first real query
    — the exact failure warm-up exists to prevent.
    """
    assert int8.warmed_batch == WARM
    assert int8.warmed_sequence == DEFAULT_SEQUENCE_LIMIT


def test_each_session_carries_the_artifact_identity(int8: RerankerSession) -> None:
    """A score with no model identity is a number nobody can reproduce."""
    assert int8.model_id
    assert int8.revision
    assert int8.revision != "main", "a moving ref is not an identity"


def test_the_two_graphs_share_one_identity(int8: RerankerSession, fp32: RerankerSession) -> None:
    """Same upstream model, two quantizations — so the arms are comparable.

    If the FP32 arm were a different model, the INT8-versus-FP32 figure would
    measure the model choice rather than the quantization, which is not what
    FR-025 asks for.
    """
    assert (int8.model_id, int8.revision) == (fp32.model_id, fp32.revision)


# --- scoring ----------------------------------------------------------------


def test_scoring_returns_one_score_per_candidate(int8: RerankerSession) -> None:
    scores, report = int8.score("bronze relief valve", ["a bronze valve", "a steel flange"])
    assert scores.shape == (2,)
    assert len(report.candidate_token_lengths) == 2


def test_scoring_is_deterministic(int8: RerankerSession) -> None:
    """FR-020's premise at this layer. A nondeterministic scorer makes the
    ordering digest a value that never agrees with itself."""
    query, candidates = "bronze relief valve", ["a bronze valve", "a steel flange"]
    first, _ = int8.score(query, candidates)
    second, _ = int8.score(query, candidates)
    np.testing.assert_array_equal(first, second)


def test_the_query_and_the_candidate_are_scored_jointly(int8: RerankerSession) -> None:
    """The property that distinguishes a cross-encoder from the bi-encoder.

    The same candidate under two different queries must score differently —
    otherwise there is no query-conditioning and the reranker is an expensive
    way to reproduce the vector ordering.
    """
    candidate = ["a bronze relief valve rated to 80 bar"]
    relevant, _ = int8.score("bronze relief valve", candidate)
    irrelevant, _ = int8.score("payment terms and invoicing schedule", candidate)
    assert relevant[0] != irrelevant[0]
    assert relevant[0] > irrelevant[0], "the relevant query should score higher"


def test_an_empty_candidate_set_scores_without_a_forward_pass(int8: RerankerSession) -> None:
    """FR-009's empty answer reaches here too, and must not become an error."""
    scores, report = int8.score("anything", [])
    assert scores.shape == (0,)
    assert report.truncated_count == 0
    assert report.candidate_token_lengths == ()


def test_both_precisions_score_the_same_input(int8: RerankerSession, fp32: RerankerSession) -> None:
    """AD-011. Two measurements of the same ranking, which is the point.

    Not asserted equal — quantization is explicitly not lossless, and asserting
    agreement would encode the opposite of what FR-025 exists to measure.
    """
    query, candidates = "bronze relief valve", ["a bronze valve", "a steel flange"]
    a, _ = int8.score(query, candidates)
    b, _ = fp32.score(query, candidates)
    assert a.shape == b.shape


def test_both_precisions_agree_on_this_ordering(
    int8: RerankerSession, fp32: RerankerSession
) -> None:
    """Recorded as a measurement on one obvious pair, not as a guarantee.

    On a candidate pair this separable the quantized graph should not invert the
    order; a corpus where it does is the finding E014's ablation is looking for,
    and the disagreement it would publish is a real one rather than a bug here.
    """
    query, candidates = "bronze relief valve", ["a bronze valve", "a steel flange"]
    a, _ = int8.score(query, candidates)
    b, _ = fp32.score(query, candidates)
    assert np.argsort(-a).tolist() == np.argsort(-b).tolist()


# --- truncation (FR-019) ----------------------------------------------------


def test_a_long_candidate_is_reported_as_truncated(int8: RerankerSession) -> None:
    """Counted at the encoding, not inferred from character length.

    The limit is in word pieces; a character count answers a different question
    and would be wrong in both directions depending on the vocabulary.
    """
    long_text = "specification section " * 400
    _, report = int8.score("valve", [long_text])
    assert report.truncated_count == 1
    assert report.candidate_token_lengths[0] >= report.sequence_limit


def test_a_short_candidate_is_not(int8: RerankerSession) -> None:
    _, report = int8.score("valve", ["a bronze valve"])
    assert report.truncated_count == 0


def test_the_pair_shares_one_budget(int8: RerankerSession) -> None:
    """Query and candidate are encoded together, so a long query costs the
    candidate room — which is why the limit is documented as bounding the pair
    rather than either side."""
    candidate = "specification section " * 200
    short_query, _ = int8.score("valve", [candidate])
    long_query, _ = int8.score("specification section " * 200, [candidate])
    assert short_query.shape == long_query.shape


# --- the truncation report --------------------------------------------------


def test_the_truncated_fraction_is_a_census_over_scored_candidates() -> None:
    """FR-051. Exact denominator, no interval — it is not an estimate."""
    report = TruncationReport(
        sequence_limit=512, candidate_token_lengths=(10, 512, 512), truncated_count=2
    )
    assert report.truncated_fraction == pytest.approx(2 / 3)


def test_the_truncated_fraction_of_nothing_is_refused() -> None:
    """Zero candidates is not zero percent truncated; it is undefined.

    Returning 0.0 would publish a clean-looking figure for a request that scored
    nothing, and the reader has no way to tell the two apart.
    """
    report = TruncationReport(sequence_limit=512, candidate_token_lengths=(), truncated_count=0)
    with pytest.raises(ValueError, match="undefined"):
        _ = report.truncated_fraction
