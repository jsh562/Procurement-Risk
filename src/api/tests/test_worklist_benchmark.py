"""The worklist's latency, under the plan's recorded conditions.

FR-040, SC-017, SC-018.

The target is the registered one — worklist p95 ≤ 1.5 s on one shared vCPU
(`specs/sad.md` § Compute) — adopted rather than chosen here. Principle VII
governs the result: a miss is published with its figure rather than quietly
retried until it passes.

The measurement conditions are fixed by the plan and restated here because a
p95 with unstated conditions is not a number anyone can reproduce:

| Cache state       | Warm — 20 discarded warm-up requests, no restart between samples |
| Sample count      | 200 timed requests per variant, p95 by nearest rank            |
| Measurement point | Server-side, request receipt to last byte of the response      |
| Variants          | The unmodified worklist (SC-017), and one carrying a single
|                   | `need_by_override` (SC-018). Both gated, reported separately.   |

The interface tier is deliberately outside the measurement: the registered
envelope is a container benchmark over the serving boundary, and no criterion
claims the rendered page is inside this budget.

**The one-vCPU limit is applied by the container, not here.** A benchmark that
tried to constrain its own CPU would measure the constraint rather than the
work. The CI step runs the api service with `cpus: 1.0`; on a developer's
machine these run unconstrained and the figure is reported as indicative — a
run with more cores that passes a single-vCPU target proves nothing, which is
why the gate is the CI step and not this file.
"""

from __future__ import annotations

import time
from datetime import date, timedelta
from typing import Any

import pytest

#: SC-017, SC-018. The registered envelope, in seconds.
P95_BUDGET_SECONDS = 1.5

#: The plan's recorded conditions.
WARMUP_REQUESTS = 20
SAMPLE_COUNT = 200


def _p95(samples: list[float]) -> float:
    """Nearest rank, one-based — the same convention the quantile pair uses.

    Stated rather than assumed: a different rule shifts the figure by up to one
    sample, and the whole point of a recorded condition is that two runs of this
    benchmark are comparable.
    """
    ordered = sorted(samples)
    rank = max(1, -(-95 * len(ordered) // 100))
    return ordered[rank - 1]


def _measure(client: Any, params: dict[str, Any] | None) -> tuple[float, list[float]]:
    """Warm up, then time ``SAMPLE_COUNT`` requests. Returns p95 and the samples."""
    for _ in range(WARMUP_REQUESTS):
        response = client.get("/api/v1/worklist", params=params)
        assert response.status_code == 200, response.text

    samples: list[float] = []
    for _ in range(SAMPLE_COUNT):
        start = time.perf_counter()
        response = client.get("/api/v1/worklist", params=params)
        samples.append(time.perf_counter() - start)
        assert response.status_code == 200

    return _p95(samples), samples


def _report(name: str, p95: float, samples: list[float]) -> None:
    """Publish the figure whether it passed or not (Principle VII)."""
    ordered = sorted(samples)
    print(
        f"\n{name}: p95={p95 * 1000:.1f} ms  "
        f"median={ordered[len(ordered) // 2] * 1000:.1f} ms  "
        f"max={ordered[-1] * 1000:.1f} ms  "
        f"n={len(samples)}  budget={P95_BUDGET_SECONDS * 1000:.0f} ms"
    )


@pytest.mark.benchmark
def test_the_unmodified_worklist_meets_its_p95(frozen_run: dict[str, Any], client: Any) -> None:
    """SC-017. The ordinary read, under the default sort key."""
    p95, samples = _measure(client, None)
    _report("worklist", p95, samples)

    assert p95 <= P95_BUDGET_SECONDS, (
        f"p95 of {p95 * 1000:.1f} ms exceeds the registered {P95_BUDGET_SECONDS * 1000:.0f} ms. "
        "Principle VII: this figure is published rather than retried until it passes."
    )


@pytest.mark.benchmark
def test_the_worklist_under_one_adjustment_meets_its_p95(
    frozen_run: dict[str, Any], client: Any
) -> None:
    """SC-018. The re-query an adjustment triggers.

    Measured separately because it is the interaction FR-011 puts in front of a
    coordinator in a loop — they adjust, read, adjust again — and a budget met
    only on the first read would be met nowhere the feature is actually used.

    It is the same query with a substituted date: no model call, no refit, the
    same rows and the same array offsets. That is the claim AD-004 rests on, and
    this is where it is checked rather than asserted.
    """
    line = next(item for item in frozen_run["lines"] if item["case"] == "nominal")
    adjusted = date.fromisoformat(line["need_by_date"]) - timedelta(days=10)
    params = {"need_by_override": [f"{line['po_line_id']}:{adjusted.isoformat()}"]}

    p95, samples = _measure(client, params)
    _report("worklist+override", p95, samples)

    assert p95 <= P95_BUDGET_SECONDS, (
        f"p95 of {p95 * 1000:.1f} ms exceeds the registered {P95_BUDGET_SECONDS * 1000:.0f} ms."
    )


@pytest.mark.benchmark
def test_an_adjustment_costs_no_model_call(frozen_run: dict[str, Any], client: Any) -> None:
    """AD-004's load-bearing claim, as a latency property rather than a comment.

    A re-query that reached the model provider would not be within a small
    multiple of the plain read — it would be a network round trip and an
    inference. The ratio is the observable that would catch it, and it is
    checked here because the two variants above would both pass a budget
    generous enough to hide it.
    """
    plain, _ = _measure(client, None)

    line = next(item for item in frozen_run["lines"] if item["case"] == "nominal")
    adjusted = date.fromisoformat(line["need_by_date"]) - timedelta(days=10)
    with_override, _ = _measure(
        client, {"need_by_override": [f"{line['po_line_id']}:{adjusted.isoformat()}"]}
    )

    # A generous multiple: the override path does one extra query to classify
    # lines the worklist excluded, so it is legitimately slower. What it cannot
    # be is an order of magnitude slower, which is what a provider call costs.
    assert with_override <= max(plain * 5, 0.05), (
        f"the adjusted read ({with_override * 1000:.1f} ms) is far slower than the plain one "
        f"({plain * 1000:.1f} ms) — which is what a request-time model call would look like"
    )
