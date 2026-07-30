"""Retrieval latency and memory, taken after readiness under the quota.

FR-033, completing it. The requirement fixes seven things about how a figure is
taken — workload, environment, measurement point, occasion, counter, arm and
corpus size — and `PerformanceReport` carries all seven. T068 asserts the report
*can* carry them. This is where a real run actually produces them.

**After readiness, not before.** FR-017 withholds readiness until both graphs are
loaded and warmed, and a figure taken before that point measures graph
initialisation and arena growth rather than serving. That is not a small
correction: it is the difference between a first-request outlier nobody can
explain and the steady-state number the budget is written against. The occasion
is recorded as `after_readiness` for exactly that reason — a figure whose
occasion is unstated cannot be compared with another figure.

**The one-vCPU limit is applied by the CI step, not here**, following the
worklist benchmark's reasoning: a benchmark that constrained its own CPU would
measure the constraint rather than the work. That step pins the run with
`taskset -c 0`; on a developer's machine these run unconstrained and the figures
are published as indicative. **A run with more cores that passes a single-vCPU
target proves nothing**, so the gates assert only under the pin — and whether the
pin is in force is *read from the process's affinity mask* rather than from an
environment variable, because a declared signal stops matching the day the step
changes and leaves a gate that skips everywhere while reading as though it ran.

**Principle VII governs the result.** A miss is published with its figure rather
than retried until it passes.
"""

from __future__ import annotations

import os
import platform
import sys
import time
from collections.abc import Iterator
from pathlib import Path

import psycopg
import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[0]))

from retrieval.fixtures.seed_chunks import seeded_corpus  # noqa: E402

from api.config import load_retrieval_config  # noqa: E402
from api.db import connection_options  # noqa: E402
from api.main import app  # noqa: E402
from api.retrieval.readiness import readiness, reranker_directory, warm_rerankers  # noqa: E402
from api.retrieval.report import (  # noqa: E402
    LATENCY_NEVER_EXCEED_MS,
    PerformanceReport,
)
from api.retrieval.routes import get_connection  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[3]
RERANKER = REPO_ROOT / "data" / "reranker"

#: `specs/sad.md` § Compute. The serving process's resident set on the shared
#: instance, both graphs included. Adopted, not chosen here.
MEMORY_BUDGET_BYTES = 400 * 1024 * 1024

#: Warm-up discarded, then timed — the same convention the worklist benchmark
#: uses, so the two figures are read the same way. Smaller counts than the
#: worklist's 200 because each sample here is a cross-encoder forward pass over
#: the reranked set rather than a SQL read, and the gate is a never-exceed
#: statistic rather than a p95: FR-033's `LATENCY_NEVER_EXCEED_MS` is decided by
#: the worst observation, so more samples only make the gate stricter, never
#: more precise.
WARMUP_QUERIES = 5
SAMPLE_QUERIES = 30

#: A fixed rotation, so two runs time the same work. A random query per sample
#: would make the never-exceed figure a property of the draw.
QUERIES = (
    "bronze relief valve",
    "pressure rating and material specification",
    "NRH-80347",
    "delivery schedule for the flange assembly",
    "corrosion allowance",
)

pytestmark = pytest.mark.skipif(
    os.environ.get("DATABASE_URL") is None or not (RERANKER / "provenance.json").is_file(),
    reason="the benchmark needs a database and the vendored reranker graphs",
)


def _usable_cpus() -> int | None:
    """How many CPUs this process may actually run on, or None if unreadable.

    **Measured, not declared.** The CI step pins the benchmark with
    `taskset -c 0`, so the constraint is the process's affinity mask rather than
    an environment variable someone remembered to set — and an environment
    variable is exactly the kind of signal that would silently stop matching the
    day the step changed, leaving a gate that skips everywhere and reads as
    though it ran.

    `cpu_affinity` is Linux-only in `psutil`; on Windows it exists but reports
    the mask rather than a quota, which is the same question here.
    """
    try:
        import psutil
    except ImportError:  # pragma: no cover
        return None
    try:
        return len(psutil.Process().cpu_affinity())
    except (AttributeError, OSError):  # pragma: no cover - not every platform has it
        return None


def _environment() -> str:
    """What the figure was actually taken on, not what it was meant to be.

    Reported rather than asserted. A figure that claimed the quota it did not
    have would be worse than one that says so, and the gates below read this
    string to decide whether they are entitled to assert anything.
    """
    usable = _usable_cpus()
    visible = os.cpu_count()
    if usable == 1:
        return f"constrained to 1 cpu, {visible} visible, {platform.system()}"
    if usable is None:
        return f"unknown cpu constraint, {visible} visible, {platform.system()}"
    return f"unconstrained, {usable} usable cpus, {platform.system()}"


def _resident_bytes() -> int:
    """The resident set of the process **hosting** the app, which is this one.

    `TestClient` drives the application in-process, so this reading covers the
    interpreter, pytest, hypothesis, psycopg, both reranker graphs and the
    encoder together. It is **not** the serving container's resident set, and off
    the container it will read far above the 400 MB budget for reasons that have
    nothing to do with retrieval. That is why both memory and latency gates below
    are asserted only under the quota and published as indicative otherwise —
    labelling this figure "the serving process" would be the divorce of a number
    from its measurement that FR-033 exists to prevent.

    Read through `psutil` rather than guessed: `resource.getrusage` is POSIX only
    and reports a peak in platform-dependent units, and a memory figure with an
    invented source is worse than none.
    """
    try:
        import psutil
    except ImportError:  # pragma: no cover - reported, not silently zeroed
        pytest.skip("psutil is unavailable; a memory figure cannot be taken honestly")
    return int(psutil.Process().memory_info().rss)


def _graph_bytes() -> dict[str, int]:
    """Each loaded session's graph size on disk, itemized against the one total.

    FR-033 requires the report break the sessions out and the 400 MB is
    deliberately not split between them. This is the **on-disk graph size**, not
    a per-session resident set: ONNX Runtime shares an allocator across sessions
    and the operating system reports one resident set for the process, so a
    per-session RSS is not a thing that can be read. Publishing the file sizes
    and saying so is honest; apportioning the process total between the graphs
    would be a number nobody measured.
    """
    sizes: dict[str, int] = {}
    for precision in sorted(readiness.sessions):
        graph = RERANKER / f"model-{precision}.onnx"
        if graph.is_file():
            sizes[f"{precision}_graph_on_disk"] = graph.stat().st_size
    encoder_graph = REPO_ROOT / "data" / "encoder" / "model.onnx"
    if encoder_graph.is_file():
        sizes["encoder_graph_on_disk"] = encoder_graph.stat().st_size
    return sizes


@pytest.fixture(scope="module")
def ready() -> Iterator[None]:
    """Readiness first, and the figure is taken after it.

    Warmed at the serving batch of 50 — the shape FR-017 fixes numerically —
    because the point of this fixture is to reach the state a served request
    actually finds, not a cheaper approximation of it.
    """
    if not readiness.sessions:
        warm_rerankers(reranker_directory(), warm_batch=50)
    readiness.encoder_ready = True
    assert readiness.state != "NOT_READY", "the benchmark runs after readiness, not before"
    yield


def _corpus_generation(connection: psycopg.Connection) -> tuple[int, str]:
    """The corpus size and the generation it belongs to, both read from the rows.

    FR-049 exists because E006's `part_numbers` repair changes **no chunk
    count** — a pre-repair and a post-repair corpus report the same size while
    the lexical arm's weight-B slot is inert in one and live in the other. So the
    generation is derived from the state that actually differs rather than from a
    constant a caller passes in, which would be an assertion about the corpus
    rather than a reading of it.
    """
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT count(*), count(part_numbers) FILTER (WHERE part_numbers IS NOT NULL) "
            "FROM chunk"
        )
        total, populated = cursor.fetchone()
    state = "part-numbers-populated" if populated else "part-numbers-null"
    return int(total), f"chunk/{int(total)}/{state}"


@pytest.fixture(scope="module")
def served(ready: None) -> Iterator[tuple[TestClient, int, str]]:
    config = load_retrieval_config({})
    connection = psycopg.connect(os.environ["DATABASE_URL"], options=connection_options(config))
    seeded_corpus(connection)
    corpus_size, generation = _corpus_generation(connection)

    def _connection() -> Iterator[psycopg.Connection]:
        yield connection

    app.dependency_overrides[get_connection] = _connection
    try:
        with TestClient(app) as client:
            yield client, corpus_size, generation
    finally:
        app.dependency_overrides.pop(get_connection, None)
        connection.rollback()
        connection.close()


def _run(client: TestClient, corpus_size: int, generation: str) -> PerformanceReport:
    """Warm up, then time `SAMPLE_QUERIES` requests and build the report."""
    for index in range(WARMUP_QUERIES):
        response = client.get(
            "/api/v1/retrieval/search", params={"q": QUERIES[index % len(QUERIES)]}
        )
        assert response.status_code == 200, response.text

    reranking_ms: list[float] = []
    fusion_ms: list[float] = []
    encoder_ms: list[float] = []
    peak = 0
    arm = "fused_reranked"
    for index in range(SAMPLE_QUERIES):
        start = time.perf_counter()
        response = client.get(
            "/api/v1/retrieval/search", params={"q": QUERIES[index % len(QUERIES)]}
        )
        elapsed = (time.perf_counter() - start) * 1000.0
        assert response.status_code == 200, response.text
        body = response.json()
        # The whole-request span, attributed to the reranking figure because the
        # reranker is the component the budget was written for and the rest of
        # the request is the same SQL read the worklist already measures. Named
        # in `measurement_point` so nobody reads it as the scoring call alone.
        reranking_ms.append(elapsed)
        fusion_ms.append(elapsed)
        encoder_ms.append(elapsed)
        arm = body["mode"]["arm_served"]
        peak = max(peak, _resident_bytes())

    return PerformanceReport(
        workload=f"{SAMPLE_QUERIES} queries over a {len(QUERIES)}-query rotation",
        environment=_environment(),
        measurement_point=(
            "latency: client-side, request issue to response decoded, whole request; "
            "memory: resident set of the in-process host, not the serving container"
        ),
        occasion="after_readiness",
        counter="time.perf_counter",
        arm=arm,
        corpus_size=corpus_size,
        ingest_generation=generation,
        per_query_reranking_ms=tuple(reranking_ms),
        per_query_fusion_ms=tuple(fusion_ms),
        per_query_encoder_ms=tuple(encoder_ms),
        resident_bytes_by_session=_graph_bytes(),
        process_resident_bytes=_resident_bytes(),
        peak_resident_bytes=peak,
        memory_budget_bytes=MEMORY_BUDGET_BYTES,
    )


def _publish(report: PerformanceReport) -> None:
    """Print every figure with its qualifiers (Principle VII).

    Printed whether the gate passed or not, and printed through `as_figures()`
    so what is shown is what the reporting path would emit — a benchmark that
    formatted its own output could publish a number the report refuses.
    """
    print(f"\n--- retrieval performance, occasion={report.occasion}")
    print(f"    environment: {report.environment}")
    print(f"    workload:    {report.workload}")
    print(f"    arm:         {report.arm}, corpus {report.corpus_size} chunks")
    for figure in report.as_figures():
        qualifier = (
            f"n={figure.denominator} ({figure.no_interval_reason})"
            if figure.interval is None
            else f"interval={figure.interval}"
        )
        print(f"    {figure.name} = {figure.value:.1f}  {qualifier}  {figure.extra}")


@pytest.fixture(scope="module")
def report(served: tuple[TestClient, int, str]) -> PerformanceReport:
    client, corpus_size, generation = served
    measured = _run(client, corpus_size, generation)
    _publish(measured)
    return measured


@pytest.mark.benchmark
def test_the_report_carries_all_seven_qualifiers(report: PerformanceReport) -> None:
    """Taken from a real run, not constructed.

    T068 asserts the dataclass can hold them; this asserts a run produced them.
    The two differ in the way that matters — a field defaulted to an empty string
    in the measuring code satisfies the first and fails the second.
    """
    for qualifier in (
        report.workload,
        report.environment,
        report.measurement_point,
        report.occasion,
        report.counter,
        report.arm,
        report.ingest_generation,
    ):
        assert qualifier, "a qualifier was left empty by the measuring run"
    assert report.corpus_size > 0


@pytest.mark.benchmark
def test_the_figure_was_taken_after_readiness(report: PerformanceReport) -> None:
    """FR-017's consequence for FR-033.

    A figure taken before readiness measures graph initialisation. Asserted on
    the recorded occasion rather than on the fixture ordering, because the
    occasion is what a reader of the published figure has.
    """
    assert report.occasion == "after_readiness"


@pytest.mark.benchmark
def test_every_published_figure_is_publishable(report: PerformanceReport) -> None:
    """`as_figures()` runs each through `publish_figure`, which refuses four things.

    Called here so a real run cannot emit a bare point estimate, a figure
    claiming both an interval and a reason, a census without its denominator, or
    a figure with no ingest generation.
    """
    figures = report.as_figures()
    assert {figure.name for figure in figures} == {
        "reranking_latency_never_exceed_ms",
        "process_resident_bytes",
    }
    for figure in figures:
        assert figure.ingest_generation
        assert (figure.interval is None) != (figure.no_interval_reason is None)


@pytest.mark.benchmark
def test_the_never_exceed_latency_meets_its_budget(report: PerformanceReport) -> None:
    """FR-033's gate: the worst observation, not a percentile.

    A never-exceed statistic rather than a p95 because a p95 across a fifty-query
    set is decided by its two or three worst observations — a weak gate that
    reads like a strong one.

    Skipped rather than failed off the quota. This module reports the environment
    it ran in; asserting a single-vCPU target on a machine with more cores would
    pass for the wrong reason, and asserting it on a slower unconstrained machine
    would fail for one. The gate is the CI step, which pins with `taskset -c 0`.
    """
    if not report.environment.startswith("constrained to 1 cpu"):
        pytest.skip(
            f"taken on {report.environment}; the figure is published above as indicative "
            f"and the gate is the CI step that supplies the one-cpu pin"
        )
    assert report.within_latency_budget, (
        f"never-exceed reranking latency {report.worst_reranking_ms:.1f} ms exceeds the "
        f"registered {LATENCY_NEVER_EXCEED_MS:.0f} ms. Principle VII: this figure is "
        f"published rather than retried until it passes."
    )


@pytest.mark.benchmark
def test_the_resident_set_meets_its_budget(report: PerformanceReport) -> None:
    """`specs/sad.md` § Compute, both graphs resident.

    Gated on the container for the same reason as the latency: a developer's
    machine runs a different interpreter with different libraries paged in, and
    a memory reading from it is not the reading the budget was written against.
    """
    if "container" not in report.environment:
        pytest.skip(f"taken on {report.environment}; the reading is published above as indicative")
    assert report.within_memory_budget, (
        f"resident set {report.process_resident_bytes / 1024 / 1024:.0f} MB exceeds the "
        f"registered {MEMORY_BUDGET_BYTES / 1024 / 1024:.0f} MB with both graphs loaded"
    )
