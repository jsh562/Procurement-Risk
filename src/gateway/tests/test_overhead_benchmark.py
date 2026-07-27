"""AD-008: gateway overhead ≤ 50 ms p95, replay resolution ≤ 10 ms p95.

The spec states no non-functional targets, so the plan adopted these — concrete
enough to test and to fail, loose enough not to distort the design.

**What "overhead" excludes is the whole point.** Provider time is not the
gateway's, and including it would measure the network. So the measurement
covers what the gateway does *around* a call: deriving the fixture key,
validating, classifying, and pricing. The provider is not involved in any of it,
which is what makes these numbers reproducible on a laptop and in CI.

**p95 over enough samples to mean something, and no sleeps.** A benchmark that
slept would measure the scheduler. These call the real functions in a loop.

**Deliberately generous, and stated as such.** 50 ms is enormous for arithmetic
over a few hundred numbers — the target is a regression alarm, not a claim that
the gateway is fast. Something has gone structurally wrong if it trips: an
accidental I/O call on a hot path, a quadratic walk, a per-invocation import.
"""

from __future__ import annotations

import time
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path

import pytest
from pydantic import BaseModel, Field

from gateway.compute.hashing import fixture_key
from gateway.compute.pricing import (
    PriceEntry,
    PriceRates,
    TokenCounts,
    compute_cost,
    resolve_price_entry,
)
from gateway.compute.timing import AttemptUsage, aggregate_usage, elapsed_ms
from gateway.fixtures import FixtureProvenance, FixtureStore
from gateway.models import InvocationRequest
from gateway.orchestrator import classify_outcome
from gateway.validation import validate_or_repair


class Assessment(BaseModel):
    """A caller's schema with constraints on both sides of the native mode's
    line, so the benchmark exercises the post-decode step rather than a schema
    the decoder could have enforced entirely.

    Defined here rather than imported from another test module: importing one
    test file from another couples their collection order, and a benchmark that
    failed because a neighbour was renamed would waste the reader's time.
    """

    label: str = Field(min_length=3)
    score: int = Field(ge=1, le=10)


#: AD-008's two numbers.
OVERHEAD_BUDGET_MS = 50.0
REPLAY_BUDGET_MS = 10.0

#: Enough samples for a p95 to be a percentile rather than a coincidence, and
#: few enough that the suite stays fast.
SAMPLES = 200

RATES = PriceRates(
    Decimal("5.000000"), Decimal("6.250000"), Decimal("0.500000"), Decimal("25.000000")
)


def p95(durations_ms: list[float]) -> float:
    """Nearest-rank, one-based, no interpolation — the convention E003 seeded
    into `schema_constants` and the one every reported percentile in this
    project uses. Stated here so two figures in the same product are not
    computed two different ways."""
    ordered = sorted(durations_ms)
    rank = max(1, int(0.95 * len(ordered) + 0.999999))
    return ordered[min(rank, len(ordered)) - 1]


def measure(work: object, samples: int = SAMPLES) -> list[float]:
    """Wall-clock milliseconds per iteration, from a monotonic clock."""
    durations: list[float] = []
    for _ in range(samples):
        started = time.monotonic()
        work()  # type: ignore[operator]
        durations.append((time.monotonic() - started) * 1000)
    return durations


def test_the_gateways_own_work_is_under_the_overhead_budget() -> None:
    """Everything the gateway does around one invocation, minus the provider.

    Key derivation, validation, aggregation, pricing, classification — the full
    path a `record`-mode call takes on either side of the network, run
    end to end so a regression in any one of them shows up here.
    """
    request = InvocationRequest(prompt="assess this vendor's delivery history")
    attempts = [AttemptUsage(1200, 0, 0, 340), AttemptUsage(1180, 0, 0, 310)]
    entries = [PriceEntry("claude-opus-5", date(2026, 1, 1), RATES)]
    stamp = datetime(2026, 7, 26, 12, 0, tzinfo=UTC)

    def one_invocation_worth_of_overhead() -> None:
        fixture_key(request, schema=Assessment, template="assess: {vendor}")
        value, repairs = validate_or_repair(Assessment, '{"label":"ok!","score":5}', lambda _: "")
        usage = aggregate_usage(attempts)
        entry = resolve_price_entry(entries, "claude-opus-5", stamp)
        assert entry is not None
        compute_cost(
            TokenCounts(
                usage.input_tokens,
                usage.cache_write_input_tokens,
                usage.cache_read_input_tokens,
                usage.output_tokens,
            ),
            entry.rates,
        )
        elapsed_ms(0.0, 1.234)
        classify_outcome(reached_valid_value=value is not None, repair_attempt_count=repairs)

    measured = p95(measure(one_invocation_worth_of_overhead))
    assert measured <= OVERHEAD_BUDGET_MS, (
        f"gateway overhead p95 is {measured:.2f} ms against a {OVERHEAD_BUDGET_MS:.0f} ms "
        f"budget (AD-008). This budget is generous for arithmetic, so tripping it "
        f"usually means something structural: I/O on a hot path, a quadratic walk, "
        f"or a per-invocation import."
    )


def test_replay_resolution_is_under_its_budget(tmp_path: Path) -> None:
    """Fixture lookup, which is what `replay` mode does instead of a call.

    A real store on a real filesystem — the budget is about disk, so an
    in-memory stand-in would measure nothing that matters.
    """
    store = FixtureStore(tmp_path / "fixtures")
    request = InvocationRequest(prompt="replayed")
    key = fixture_key(request)
    store.save(
        key,
        '{"label":"good","score":7}',
        FixtureProvenance(
            recorded_on=date(2026, 7, 26),
            gen_ai_response_model="claude-opus-5",
            gateway_revision="0" * 40,
            gen_ai_usage_input_tokens=12,
            gen_ai_usage_output_tokens=8,
        ),
    )

    def one_replay_resolution() -> None:
        store.load(fixture_key(request))

    measured = p95(measure(one_replay_resolution))
    assert measured <= REPLAY_BUDGET_MS, (
        f"replay resolution p95 is {measured:.2f} ms against a {REPLAY_BUDGET_MS:.0f} ms "
        f"budget (AD-008)"
    )


def test_the_percentile_is_nearest_rank_one_based() -> None:
    """A control on the statistic, not on the code under test.

    p95 computed three different ways gives three different numbers, and a
    benchmark whose percentile drifts is a benchmark whose budget means
    something different each run. Pinned against a known sample.
    """
    assert p95([float(n) for n in range(1, 101)]) == 95.0
    assert p95([1.0]) == 1.0
    assert p95([1.0, 2.0]) == 2.0


def test_the_measurement_is_not_dominated_by_its_own_harness() -> None:
    """If the timing loop cost as much as the work, the budget would be a
    statement about `time.monotonic()`. Measured against an empty callable."""
    empty = p95(measure(lambda: None))
    assert empty < REPLAY_BUDGET_MS, (
        f"the empty-loop p95 is {empty:.3f} ms, which is a large fraction of the "
        f"{REPLAY_BUDGET_MS:.0f} ms budget — the harness is measuring itself"
    )


@pytest.mark.parametrize("budget", [OVERHEAD_BUDGET_MS, REPLAY_BUDGET_MS])
def test_the_budgets_are_the_ones_the_plan_adopted(budget: float) -> None:
    """AD-008's numbers, pinned so a failing benchmark is fixed by making the
    code faster rather than by widening the target quietly."""
    assert budget in {50.0, 10.0}
