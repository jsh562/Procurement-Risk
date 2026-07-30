"""E008's gate, measured over the frozen set and published in full.

SC-001 (recall@5 ≥ 0.85, Wilson 95%) and SC-002 (MRR ≥ 0.70, percentile
bootstrap 95%). T094. Everything below this line has been about *whether the
machinery works*; this is the run that produces the two numbers the epic is
judged on.

**Measured at this epic's gate on its own query set.** SC-001 says so in as many
words and draws the line E014 sits on the other side of: E014 publishes on the
frozen set against the real corpus. What runs here is the same harness, the same
statistics and the same refusals, over the six-chunk seeded corpus this tier can
build without the ingest pipeline.

**The ceiling is published with the figure, not inferred from it.** FR-043:
judgements come from the generator's pre-render document model, so every query
is answerable by construction and the recall this produces is an **upper bound on
real-world performance rather than an estimate of it**. A reader who takes it for
an estimate has read a number that was never offered.

**Principle VII, and FR-043's tuning discipline.** The figures print whether they
clear the gate or not. If a ranking parameter is changed after a figure has been
measured, FR-043 requires the change recorded as a decision, the set re-measured,
and the before and after figures *emitted together* — publishing only the later
one satisfies the re-measurement and hides the tuning.
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
from api.retrieval.metrics import (  # noqa: E402
    BOOTSTRAP_BIT_GENERATOR,
    BOOTSTRAP_RESAMPLES,
    IntervalMethod,
    mean_reciprocal_rank,
    percentile_bootstrap,
    recall_at_k,
    strongest_single_arm,
    wilson_interval,
)
from api.retrieval.readiness import readiness, reranker_directory, warm_rerankers  # noqa: E402
from api.retrieval.report import FigureRecord, publish_figure  # noqa: E402
from api.retrieval.routes import get_connection  # noqa: E402
from retrieval.evaluation_set.harness import (  # noqa: E402
    COMMITTED_SET,
    EvaluationSet,
    load_frozen_set,
    outcomes_at_k,
    reciprocal_ranks,
)
from retrieval.fixtures.seed_chunks import seeded_corpus  # noqa: E402

RERANKER = Path(__file__).resolve().parents[4] / "data" / "reranker"

#: SC-001 and SC-002, read against the point estimate.
RECALL_GATE = 0.85
MRR_GATE = 0.70

#: SC-001's `k`. Named rather than inlined so the recall figure and the cut it
#: was taken at cannot drift apart.
K = 5

#: The arm the gate is measured on. Recorded with the figures because a recall
#: number that does not say which arm produced it cannot be compared with the
#: ablation rows E014 publishes beside it.
GATE_ARM = "fused_reranked"

pytestmark = pytest.mark.skipif(
    os.environ.get("DATABASE_URL") is None or not (RERANKER / "provenance.json").is_file(),
    reason="the gate measurement needs a database and the vendored reranker graphs",
)


@pytest.fixture(scope="module", autouse=True)
def warmed() -> Iterator[None]:
    if not readiness.sessions:
        warm_rerankers(reranker_directory(), warm_batch=4)
    readiness.encoder_ready = True
    yield


@pytest.fixture(scope="module")
def measured() -> Iterator[dict[str, object]]:
    """Run every query in the frozen set and collect what came back.

    The set is loaded through `load_frozen_set`, which verifies the digest and
    aborts before returning any query. That ordering is the point of the harness
    and it is used here rather than worked around — a gate measurement that read
    the JSON directly would be the first caller to skip the check.
    """
    frozen: EvaluationSet = load_frozen_set(COMMITTED_SET)

    config = load_retrieval_config({})
    connection = psycopg.connect(os.environ["DATABASE_URL"], options=connection_options(config))
    seeded_corpus(connection)
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*), count(*) FILTER (WHERE part_numbers IS NOT NULL) FROM chunk"
        )
        total, populated = cursor.fetchone()
    generation = (
        f"chunk/{int(total)}/{'part-numbers-populated' if populated else 'part-numbers-null'}"
    )

    def _connection() -> Iterator[psycopg.Connection]:
        yield connection

    app.dependency_overrides[get_connection] = _connection
    try:
        with TestClient(app) as client:
            retrieved: dict[str, list[str]] = {}
            served_modes: set[str] = set()
            for query in frozen.queries:
                response = client.get(
                    "/api/v1/retrieval/search", params={"q": query.text, "arm": GATE_ARM}
                )
                assert response.status_code == 200, response.text
                body = response.json()
                retrieved[query.query_id] = [r["chunk_id"] for r in body["results"]]
                served_modes.add(body["mode"]["arm_served"])
        yield {
            "frozen": frozen,
            "retrieved": retrieved,
            "arm_served": sorted(served_modes),
            "corpus_size": int(total),
            "ingest_generation": generation,
        }
    finally:
        app.dependency_overrides.pop(get_connection, None)
        connection.rollback()
        connection.close()


@pytest.fixture(scope="module")
def figures(measured: dict[str, object]) -> list[FigureRecord]:
    """The two gate figures, each published through the refusal rules.

    Built through `publish_figure` rather than assembled by hand, so a gate
    figure cannot be emitted as a bare point estimate, cannot carry both an
    interval and a no-interval reason, and cannot omit its ingest generation.
    """
    frozen: EvaluationSet = measured["frozen"]  # type: ignore[assignment]
    retrieved: dict[str, list[str]] = measured["retrieved"]  # type: ignore[assignment]

    outcomes = outcomes_at_k(frozen, retrieved, k=K)
    recall = recall_at_k(outcomes)
    recall_low, recall_high, recall_record = wilson_interval(outcomes, with_method=True)

    reciprocals = reciprocal_ranks(frozen, retrieved)
    mrr = mean_reciprocal_rank(reciprocals)
    # The seed comes from the frozen set's own manifest, not from a literal
    # here. It is a property of the set -- the same set re-measured must draw
    # the same resamples -- and a seed chosen at the call site would let two
    # runs over one set produce two different intervals.
    mrr_low, mrr_high, mrr_record = percentile_bootstrap(
        reciprocals, seed=frozen.seed, with_method=True
    )

    # The comparator, named by the fixed per-statistic rule rather than chosen
    # after seeing the numbers -- FR-036. On one measured arm it is that arm; the
    # rule is called anyway so the recorded comparator comes from the same place
    # it will come from when E014 runs every arm.
    comparator = strongest_single_arm({GATE_ARM: recall})

    published = [
        publish_figure(
            FigureRecord(
                name="recall_at_5",
                value=recall,
                interval=(recall_low, recall_high),
                interval_record=recall_record,
                corpus_size=measured["corpus_size"],  # type: ignore[arg-type]
                ingest_generation=measured["ingest_generation"],  # type: ignore[arg-type]
                extra={
                    "gate": RECALL_GATE,
                    "k": K,
                    "arm": GATE_ARM,
                    "arm_served": measured["arm_served"],
                    "comparator_arm": comparator,
                    "queries": len(frozen),
                    "ceiling": "upper bound on real-world performance, not an estimate (FR-043)",
                    "set_digest": frozen.digest,
                },
            )
        ),
        publish_figure(
            FigureRecord(
                name="mean_reciprocal_rank",
                value=mrr,
                interval=(mrr_low, mrr_high),
                interval_record=mrr_record,
                corpus_size=measured["corpus_size"],  # type: ignore[arg-type]
                ingest_generation=measured["ingest_generation"],  # type: ignore[arg-type]
                extra={
                    "gate": MRR_GATE,
                    "arm": GATE_ARM,
                    "arm_served": measured["arm_served"],
                    "comparator_arm": comparator,
                    "queries": len(frozen),
                    "resamples": BOOTSTRAP_RESAMPLES,
                    "bit_generator": BOOTSTRAP_BIT_GENERATOR,
                    "seed": frozen.seed,
                    "ceiling": "upper bound on real-world performance, not an estimate (FR-043)",
                    "set_digest": frozen.digest,
                },
            )
        ),
    ]

    print("\n--- E008 gate measurement over the frozen set")
    for figure in published:
        low, high = figure.interval  # type: ignore[misc]
        method = figure.interval_record.method if figure.interval_record else "?"
        print(
            f"    {figure.name} = {figure.value:.4f}  "
            f"[{low:.4f}, {high:.4f}] ({method})  gate={figure.extra['gate']}  "
            f"arm={figure.extra['arm']}  n={figure.extra['queries']}  "
            f"corpus={figure.corpus_size}  generation={figure.ingest_generation}"
        )
        print(f"      ceiling: {figure.extra['ceiling']}")
    return published


def _figure(figures: list[FigureRecord], name: str) -> FigureRecord:
    return next(figure for figure in figures if figure.name == name)


# --- what the measurement recorded -----------------------------------------


def test_the_measurement_records_everything_the_task_names(
    figures: list[FigureRecord],
) -> None:
    """Recall, MRR, their intervals, the comparator arm, the mode and the corpus size.

    Asserted as a set rather than one at a time, because the failure this
    catches is a qualifier quietly dropped from the emitting code while the two
    headline numbers keep printing.
    """
    for figure in figures:
        assert figure.interval is not None
        assert figure.interval_record is not None
        assert figure.corpus_size and figure.corpus_size > 0
        assert figure.ingest_generation
        assert figure.extra["comparator_arm"]
        assert figure.extra["arm_served"]
        assert figure.extra["set_digest"]


def test_the_interval_methods_are_the_ones_the_statistics_admit(
    figures: list[FigureRecord],
) -> None:
    """FR-031, FR-034. Wilson is defined for a proportion and for nothing else.

    Recall@5 is a binomial proportion over the query set, so Wilson applies. MRR
    is a mean of reciprocal ranks — not a proportion — so it takes the percentile
    bootstrap. A Wilson interval on MRR would be a number with the right shape
    and no meaning, which is the failure this asserts against.
    """
    recall = _figure(figures, "recall_at_5")
    mrr = _figure(figures, "mean_reciprocal_rank")
    assert recall.interval_record.method is IntervalMethod.WILSON  # type: ignore[union-attr]
    assert mrr.interval_record.method is not IntervalMethod.WILSON  # type: ignore[union-attr]
    assert mrr.extra["resamples"] == BOOTSTRAP_RESAMPLES
    assert mrr.extra["bit_generator"] == BOOTSTRAP_BIT_GENERATOR


def test_every_interval_contains_its_point_estimate(figures: list[FigureRecord]) -> None:
    """The cheapest check that would catch a wrongly-assembled interval.

    It has caught one already in this epic: Wilson at p = 1 returned an upper
    bound of 0.9999999999999999 and excluded the estimate it was drawn around.
    """
    for figure in figures:
        low, high = figure.interval  # type: ignore[misc]
        assert low <= figure.value <= high, (
            f"{figure.name} = {figure.value} lies outside its own interval [{low}, {high}]"
        )


def test_the_figures_cover_the_whole_frozen_set(
    figures: list[FigureRecord], measured: dict[str, object]
) -> None:
    """Not the queries that returned something.

    `outcomes_at_k` refuses a partial population outright; this asserts the
    denominator that reached the published figure is the set's own size, so a
    figure computed over a flattering subset cannot be published with the set's
    name on it.
    """
    frozen: EvaluationSet = measured["frozen"]  # type: ignore[assignment]
    for figure in figures:
        assert figure.extra["queries"] == len(frozen)


def test_the_published_ceiling_travels_with_both_figures(
    figures: list[FigureRecord],
) -> None:
    """FR-043. Answerable by construction, so this is a bound, not an estimate."""
    for figure in figures:
        assert "upper bound" in str(figure.extra["ceiling"])


# --- the gate itself --------------------------------------------------------


def test_recall_at_five_clears_its_gate(figures: list[FigureRecord]) -> None:
    """SC-001, read against the point estimate.

    Principle VII: the figure is printed above whether it clears or not. A miss
    is published rather than retried until it passes, and FR-043 makes any
    ranking-parameter change after this measurement a recorded decision with
    both figures emitted together.
    """
    recall = _figure(figures, "recall_at_5")
    low, high = recall.interval  # type: ignore[misc]
    assert recall.value >= RECALL_GATE, (
        f"recall@{K} = {recall.value:.4f} [{low:.4f}, {high:.4f}] misses the SC-001 gate of "
        f"{RECALL_GATE}. This figure is an upper bound (FR-043), so a miss here is a miss "
        f"anywhere."
    )


def test_mean_reciprocal_rank_clears_its_gate(figures: list[FigureRecord]) -> None:
    """SC-002, with the bootstrap interval SC-002 requires."""
    mrr = _figure(figures, "mean_reciprocal_rank")
    low, high = mrr.interval  # type: ignore[misc]
    assert mrr.value >= MRR_GATE, (
        f"MRR = {mrr.value:.4f} [{low:.4f}, {high:.4f}] misses the SC-002 gate of {MRR_GATE}."
    )
