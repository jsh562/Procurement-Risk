"""The performance figures carry everything needed to read them.

Spec FR-033, FR-036, FR-041, SC-016. A latency without its workload,
environment, measurement point, occasion, counter, arm and corpus size is a
number that cannot be compared with another number — which is the only thing a
performance figure is for.
"""

from __future__ import annotations

import pytest

from api.retrieval.metrics import (
    MetricsError,
    compare_against_strongest,
    strongest_single_arm,
)
from api.retrieval.report import (
    LATENCY_NEVER_EXCEED_MS,
    NoIntervalReason,
    PerformanceReport,
    ReportError,
    degraded_never_exceeds,
)


def _report(**overrides: object) -> PerformanceReport:
    base: dict[str, object] = {
        "workload": "one query at a time",
        "environment": "one shared vCPU, quota enforced",
        "measurement_point": "the reranker component's scoring call",
        "occasion": "after readiness",
        "counter": "process resident set size",
        "arm": "fused_reranked",
        "corpus_size": 6,
        "ingest_generation": "gen-1",
        "per_query_reranking_ms": (120.0, 180.0, 95.0),
        "per_query_fusion_ms": (3.0, 4.0, 3.5),
        "per_query_encoder_ms": (8.0, 9.0, 8.5),
        "resident_bytes_by_session": {"encoder": 90_000_000, "int8": 23_000_000},
        "process_resident_bytes": 300_000_000,
        "peak_resident_bytes": 340_000_000,
        "memory_budget_bytes": 419_430_400,
    }
    base.update(overrides)
    return PerformanceReport(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "field",
    [
        "workload",
        "environment",
        "measurement_point",
        "occasion",
        "counter",
        "arm",
        "corpus_size",
        "ingest_generation",
    ],
)
def test_the_report_carries_every_term_fr033_fixes(field: str) -> None:
    """All eight, by name.

    FR-033's content *is* the list — the requirement is not "measure latency",
    it is "measure it in a way another run can be compared against". A missing
    term makes the figure incomparable without making it look wrong.
    """
    assert getattr(_report(), field)


def test_the_never_exceed_is_the_worst_observation_not_an_average() -> None:
    """SC-016. A single observation above the budget falsifies it.

    Chosen over a p95 because a p95 across fifty queries is decided by its two
    or three worst observations — a weak gate that reads like a strong one.
    """
    assert _report().worst_reranking_ms == 180.0
    assert _report().within_latency_budget is True
    assert _report(per_query_reranking_ms=(401.0,)).within_latency_budget is False


def test_a_run_that_timed_nothing_has_no_never_exceed() -> None:
    """Undefined, not zero. Zero would read as an excellent run."""
    with pytest.raises(ReportError, match="undefined"):
        _ = _report(per_query_reranking_ms=()).worst_reranking_ms


def test_memory_is_itemized_against_one_total_rather_than_apportioned() -> None:
    """FR-033. The 400 MB is deliberately not split between sessions.

    Apportioning would invent four sub-budgets nobody agreed to; itemizing
    makes the total attributable without inventing them.
    """
    figures = _report().as_figures()
    memory = next(f for f in figures if f.name == "process_resident_bytes")
    assert set(memory.extra["by_session"]) == {"encoder", "int8"}
    assert memory.extra["budget_bytes"] == 419_430_400
    assert memory.extra["peak_bytes"] >= memory.value


def test_the_peak_is_published_beside_the_steady_state() -> None:
    """Two readings of one run, and `specs/sad.md` names both.

    Its benchmark job prints peak RSS while its target names steady state — so
    publishing one without the other invites comparing a peak against a
    steady-state budget.
    """
    memory = next(f for f in _report().as_figures() if f.name == "process_resident_bytes")
    assert memory.extra["peak_bytes"] == 340_000_000


def test_both_figures_declare_why_they_have_no_interval() -> None:
    """Principle II as v1.2.10 amends it, on each figure separately.

    They draw *different* licensed reasons because they are censuses for
    different reasons: the latency covers every query in the run, and the
    memory reading is one observation with no population to sample at all.
    """
    figures = {f.name: f for f in _report().as_figures()}
    latency = figures["reranking_latency_never_exceed_ms"]
    memory = figures["process_resident_bytes"]
    assert latency.no_interval_reason is NoIntervalReason.CENSUS_OVER_ENUMERATED_POPULATION
    assert latency.denominator == 3
    assert memory.no_interval_reason is NoIntervalReason.SINGLE_OBSERVATION
    assert memory.denominator == 1


def test_every_figure_carries_the_ingest_generation() -> None:
    """FR-049. Corpus size alone cannot separate a pre- from a post-repair run."""
    for figure in _report().as_figures():
        assert figure.ingest_generation == "gen-1"
        assert figure.corpus_size == 6


# ---------------------------------------------------------------------------
# FR-041: the degraded path's own span
# ---------------------------------------------------------------------------


def test_the_degraded_span_is_named_and_is_not_fr033s() -> None:
    """The reranker scoring call does not happen on the degraded path.

    Inheriting FR-033's span would leave the measured interval empty and the
    requirement unfalsifiable — so the span is stated, and it is the only one
    both paths share.
    """
    report = degraded_never_exceeds([100.0, 90.0], [150.0, 160.0])
    assert report["span"] == "total_in_process_query_wall_clock"


def test_the_degraded_path_is_gated_on_the_never_exceed() -> None:
    assert degraded_never_exceeds([399.0], [])["within_budget"] is True
    assert degraded_never_exceeds([401.0], [])["within_budget"] is False
    assert LATENCY_NEVER_EXCEED_MS == 400.0


def test_the_means_are_compared_rather_than_per_query() -> None:
    """A per-query comparison would be falsified by scheduling jitter.

    Ordinary variance would make some query slower on the degraded path in every
    run, so a true claim would fail routinely — which is why the comparison is
    between means and the gate is the never-exceed.
    """
    report = degraded_never_exceeds([100.0, 200.0], [150.0, 160.0])
    assert report["mean_ms"] == 150.0
    assert report["reranked_mean_ms"] == 155.0
    assert report["faster_than_reranked_on_average"] is True


def test_a_degraded_report_with_no_reranked_comparison_says_so() -> None:
    """None rather than True. There is nothing to be faster than."""
    assert degraded_never_exceeds([100.0], [])["faster_than_reranked_on_average"] is None


# ---------------------------------------------------------------------------
# FR-036: the honest comparator
# ---------------------------------------------------------------------------


def test_the_comparator_is_the_strongest_single_arm_not_fusion() -> None:
    """Principle VIII. Beating fusion-only is close to guaranteed.

    At depth 50 with k=60 the rank-1 to rank-50 contribution ratio is 1.8, so
    the fused ordering is weak by construction and a comparison against it says
    almost nothing.
    """
    arms = {"lexical": [0.2, 0.4, 0.3], "dense": [0.6, 0.5, 0.7], "fusion": [0.5, 0.5, 0.5]}
    comparison = compare_against_strongest("fused_reranked", [0.9, 0.8, 0.9], arms, statistic="mrr")
    assert comparison.comparator_arm == "dense"
    assert comparison.selecting_statistic == "mrr"


def test_a_tie_at_the_top_reports_every_tied_arm() -> None:
    """Picking one would be a silent choice."""
    assert strongest_single_arm({"a": 0.5, "b": 0.5}) == ("a", "b")


def test_differences_are_paired_per_query() -> None:
    """Pairing removes between-query variance, which at this set size is most of it.

    An unpaired comparison at fifty queries cannot separate arms differing by a
    few points, which is the spec's own recorded risk.
    """
    arms = {"dense": [0.5, 0.5, 0.5]}
    comparison = compare_against_strongest("fused_reranked", [0.9, 0.6, 0.7], arms, statistic="mrr")
    assert comparison.paired_differences == pytest.approx((0.4, 0.1, 0.2))
    assert comparison.mean_difference == pytest.approx(0.2333, abs=1e-4)


def test_mismatched_query_counts_are_refused() -> None:
    """A paired difference needs the same queries through both arms."""
    with pytest.raises(MetricsError, match="same queries"):
        compare_against_strongest("subject", [0.5], {"dense": [0.5, 0.5]}, statistic="mrr")


def test_overlapping_intervals_are_unresolvable_and_both_are_reported() -> None:
    """FR-032. Declaring a winner on overlapping intervals is the overclaim.

    Reporting only the subject would hide that the comparison did not settle,
    which is worse than reporting no winner.
    """
    comparison = compare_against_strongest(
        "fused_reranked",
        [0.9, 0.8],
        {"dense": [0.6, 0.7]},
        statistic="mrr",
        intervals={"fused_reranked": (0.7, 0.95), "dense": (0.5, 0.8)},
    )
    assert comparison.unresolvable is True
    assert set(comparison.both_reported) == {"fused_reranked", "dense"}


def test_disjoint_intervals_resolve() -> None:
    comparison = compare_against_strongest(
        "fused_reranked",
        [0.9, 0.9],
        {"dense": [0.3, 0.3]},
        statistic="mrr",
        intervals={"fused_reranked": (0.85, 0.95), "dense": (0.2, 0.4)},
    )
    assert comparison.unresolvable is False
    assert comparison.both_reported == ()


def test_comparing_against_no_arms_is_refused() -> None:
    with pytest.raises(MetricsError, match="nothing to compare"):
        strongest_single_arm({})
