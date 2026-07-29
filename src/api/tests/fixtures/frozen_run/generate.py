"""Generate the frozen forecast fixture.

FR-036, FR-037. Run with::

    uv run --directory src/api python tests/fixtures/frozen_run/generate.py

The output is committed. It is *frozen*: a change to this generator does not
change the fixture until someone regenerates it deliberately and reviews the
diff. That is the whole point — if the fixture were regenerated at test time, an
edit here would silently move every expected value, and a test whose expected
values move with the code under test asserts nothing.

**Every boundary case FR-036 names has a line behind it**, because a boundary
case with no fixture line is untested however many tests run. The map from case
to line is in ``CASES`` below and is asserted by ``test_fixture_covers_cases``.

**The posteriors are constructed, not sampled.** A sampled posterior lands
wherever the RNG puts it, and the cases here are exact: a miss probability
sitting *precisely* on a half-percent rounding boundary, two lines tied
*exactly* on expected harm. Those are not properties one gets by drawing 4000
variates and hoping. Each line's draws are built to a stated target and the
survival curve is then computed from the draws, so the two agree by construction
rather than by assertion.

**Survival is P(late), with no complement.** ``survival[k] = count(draws > k) /
draw_count`` — the probability the delivery has *not* occurred by day ``k``, and
therefore the probability of missing a need-by at offset ``k``. Writing
``1 - survival[k]`` here would rank the safest lines first and look entirely
plausible on screen.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field
from datetime import date, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Final

# --- Frozen parameters -------------------------------------------------------
#
# Every one of these is part of the fixture's identity. Changing one changes the
# expected values of every test that reads it, which is why they are stated here
# rather than passed in.

SEED: Final[int] = 20260601
GENERATED_ON: Final[str] = "2026-07-28"
GENERATOR: Final[str] = "src/api/tests/fixtures/frozen_run/generate.py"
REGENERATE_COMMAND: Final[str] = (
    "uv run --directory src/api python tests/fixtures/frozen_run/generate.py"
)

AS_OF: Final[date] = date(2026, 6, 1)
HORIZON_DAYS: Final[int] = 365
DRAW_COUNT: Final[int] = 4000

#: The run's roster. A line carrying a *different* well-formed digest is the
#: roster-mismatch case — well-formed is essential, since a malformed one would
#: be rejected by `ck_pol__roster_hash_format` and would test the schema rather
#: than the state.
ROSTER_HASH: Final[str] = "sha256:" + sha256(b"frozen-roster-2026-06-01").hexdigest()
OTHER_ROSTER_HASH: Final[str] = "sha256:" + sha256(b"frozen-roster-superseded").hexdigest()

RUN_ID: Final[str] = "3f7c2b90-5a44-4e11-8b0a-6d9e1c33a201"

#: FR-036's eleven boundary cases, and the line realising each. Asserted rather
#: than documented: `test_fixture_covers_cases` fails if a case loses its line.
CASES: Final[dict[str, str]] = {
    "median_equals_eightieth": "PO-4471-2",
    "exact_harm_tie": "PO-4472-1|PO-4472-2",
    "no_posterior": "PO-4473-1",
    "roster_mismatch": "PO-4473-2",
    "need_by_equals_as_of": "PO-4474-1",
    "need_by_before_as_of": "PO-4474-2",
    "need_by_between_as_of_and_today": "PO-4475-1",
    "need_by_last_in_grid_day": "PO-4475-2",
    "need_by_day_after_horizon": "PO-4476-1",
    "residual_tail_rounds_to_extreme": "PO-4476-2",
    "miss_probability_half_percent_up": "PO-4477-1",
    "miss_probability_half_percent_down": "PO-4477-2",
    "adjustment_changes_no_ordering": "PO-4478-1",
}


@dataclass
class LineSpec:
    """One purchase-order line and the posterior to build for it."""

    po_number: str
    line_number: int
    project_id: str
    vendor_id: str
    description: str
    need_by_offset: int
    criticality: int
    #: Draws strictly greater than this offset, out of DRAW_COUNT. `None` means
    #: the line has no posterior at all — the not-covered case.
    late_count: int | None
    #: Spread of the duration distribution, in days.
    spread: float = 30.0
    #: When set, every on-time draw takes the same value, so the 50th and 80th
    #: percentiles land on it *identically* rather than merely close together.
    #: "Near-degenerate" and not fully degenerate: the late tail keeps its shape,
    #: so the posterior still has an interval to report.
    degenerate_body: bool = False
    #: When set, exactly this many draws exceed the horizon, whatever the
    #: need-by offset is — which is what fixes `residual_tail_mass`, since that
    #: is mass beyond day 365 and not mass beyond the need-by date. Conflating
    #: the two is the mistake this field exists to make impossible.
    horizon_tail_count: int | None = None
    #: The RNG stream this line's draws are built from. Two lines sharing a key
    #: get byte-identical draws, which is how the exact-tie case is exact — a
    #: tie built from two jittered streams is a near-tie, and a near-tie does
    #: not exercise the tiebreak at all.
    draw_key: str | None = None
    roster_hash: str = ROSTER_HASH
    lifecycle_state: str = "submitted"
    notes: str = ""
    case: str = ""
    tags: list[str] = field(default_factory=list)


def _build_draws(spec: LineSpec) -> list[float]:
    """Construct a sorted draw array hitting ``spec.late_count`` exactly.

    The boundary is placed by construction rather than by sampling: exactly
    ``late_count`` draws land strictly above the need-by offset and the rest at
    or below it. A sampled array lands near a target, and "near" cannot realise
    a case defined as sitting *exactly* on a rounding boundary.

    The RNG is per-line and seeded from ``draw_key``, so a line's draws do not
    depend on how many lines were generated before it — which means adding a
    line in the middle of ``SPECS`` does not silently change every expected
    value after it.
    """
    assert spec.late_count is not None
    key = spec.draw_key or f"{spec.project_id}/{spec.po_number}/{spec.line_number}"
    rng = random.Random(f"{SEED}:{key}")

    boundary = float(spec.need_by_offset)
    on_time = DRAW_COUNT - spec.late_count

    draws: list[float] = []

    # Below the boundary. Clamped at zero — `ck_line_posterior__draws_non_negative`
    # refuses a negative duration, and a negative remaining duration has no
    # meaning under `conditional_remaining_duration_from_run_as_of_date`.
    lower_floor = max(0.0, boundary - spec.spread)
    for index in range(on_time):
        if spec.degenerate_body:
            draws.append(max(0.0, lower_floor))
            continue
        position = 0.5 if on_time == 1 else index / (on_time - 1)
        jitter = rng.uniform(-0.02, 0.02) * spec.spread
        value = lower_floor + position * max(0.0, boundary - lower_floor) * 0.999 + jitter
        draws.append(max(0.0, min(value, boundary)))

    # Above the boundary. `+ 1e-6` guarantees strict inequality survives the
    # float64 round-trip through PostgreSQL, so `count(draws > k)` is the
    # number this generator intended and not one less.
    #
    # The floor is zero and not the boundary: a draw is a *remaining duration
    # from the run's as-of date* under `conditional_remaining_duration_from_
    # run_as_of_date`, so it cannot be negative however far in the past the
    # need-by date sits. `ck_line_posterior__draws_non_negative` refuses it, and
    # rightly — an already-late line still has a non-negative amount of time
    # left to run, which is the whole quantity the row is reporting.
    floor = max(0.0, boundary) + 1e-6
    for index in range(spec.late_count):
        position = 0.5 if spec.late_count == 1 else index / (spec.late_count - 1)
        jitter = rng.uniform(0.0, 0.02) * spec.spread
        draws.append(floor + position * spec.spread + jitter)

    draws.sort()
    if spec.horizon_tail_count is not None:
        draws = _pin_horizon_tail(draws, spec.horizon_tail_count)

    # Round late, once. Rounding before the sort could reorder equal-rounding
    # neighbours; rounding after keeps the array sorted, which
    # `ck_line_posterior__draws_sorted` requires.
    rounded = [round(value, 6) for value in draws]
    rounded.sort()
    return rounded


def _pin_horizon_tail(draws: list[float], tail_count: int) -> list[float]:
    """Compress all but the top ``tail_count`` draws to at or below the horizon.

    ``residual_tail_mass`` is ``survival[horizon_days]`` — the mass that has not
    resolved by day 365 — and it is therefore governed by how many draws exceed
    *365*, not by how many exceed the line's need-by date. A line whose need-by
    sits past the horizon has both, and they are different numbers.

    The compression is order-preserving, so the array stays sorted and every
    draw stays non-negative.
    """
    body_size = len(draws) - tail_count
    body = draws[:body_size]
    ceiling = float(HORIZON_DAYS) - 0.5
    span = max(body[-1], 1e-9)
    compressed = [round(value / span * ceiling, 9) for value in body]

    # The tail, spread just past the horizon, keeping the array sorted.
    tail = [float(HORIZON_DAYS) + 0.5 + index * 7.5 for index in range(tail_count)]
    return compressed + tail


def _survival_from_draws(draws: list[float]) -> list[float]:
    """``survival[k] = count(draws > k) / draw_count`` for ``k = 1..horizon``.

    One-based over the grid, matching E003's storage: there is no ``k = 0``
    entry, which is why a need-by on the as-of date has no offset to read and is
    resolved as already-late rather than by lookup.

    **No complement.** This is the probability the delivery has *not* happened by
    day ``k`` — the probability of being late — and it is what the worklist
    displays as the miss probability directly.
    """
    survival: list[float] = []
    index = 0
    remaining = len(draws)
    for day in range(1, HORIZON_DAYS + 1):
        while index < len(draws) and draws[index] <= day:
            index += 1
            remaining -= 1
        survival.append(round(remaining / DRAW_COUNT, 10))
    return survival


def _quantile(draws: list[float], percentile: int) -> float:
    """Nearest-rank, one-based, no interpolation — `schema_constants`' convention.

    Stated here rather than assumed because a different convention shifts every
    quantile in the fixture by up to one draw, and the fixture is what the
    expected values are read from.
    """
    rank = max(1, -(-percentile * len(draws) // 100))
    return draws[rank - 1]


def _expected_harm(draws: list[float], need_by_offset: int, criticality: int) -> float:
    """``E[max(0, delivery − need_by)] × criticality``.

    The ranking function, computed here so the fixture can carry the expected
    ordering as data. A test that recomputed it the same way the code does would
    assert only that the code agrees with itself.
    """
    overrun = sum(max(0.0, draw - need_by_offset) for draw in draws) / len(draws)
    return round(overrun * criticality, 10)


SPECS: Final[tuple[LineSpec, ...]] = (
    LineSpec(
        po_number="PO-4471",
        line_number=1,
        project_id="PRJ-001",
        vendor_id="VND-001",
        description="Air handling unit AHU-3, 12000 CFM",
        need_by_offset=60,
        criticality=4,
        late_count=1200,
        notes="An ordinary populated row: no degraded state applies.",
        case="nominal",
    ),
    LineSpec(
        po_number="PO-4471",
        line_number=2,
        project_id="PRJ-001",
        vendor_id="VND-001",
        description="Ductwork package, level 3 east",
        need_by_offset=40,
        criticality=3,
        late_count=800,
        spread=6.0,
        degenerate_body=True,
        notes=(
            "Near-degenerate posterior: the spread is small enough that the 50th and 80th "
            "percentiles land on the same whole day, so the displayed pair is a single day "
            "twice. The interface must still present it as an interval rather than collapsing "
            "to what looks like a point estimate."
        ),
        case="median_equals_eightieth",
    ),
    LineSpec(
        po_number="PO-4472",
        line_number=1,
        project_id="PRJ-001",
        vendor_id="VND-002",
        description="Switchgear lineup SWG-1",
        need_by_offset=50,
        criticality=4,
        late_count=1000,
        spread=20.0,
        draw_key="switchgear-pair",
        notes="Tied with PO-4472-2 on expected harm to the last representable digit.",
        case="exact_harm_tie",
    ),
    LineSpec(
        po_number="PO-4472",
        line_number=2,
        project_id="PRJ-001",
        vendor_id="VND-002",
        description="Switchgear lineup SWG-2",
        need_by_offset=50,
        criticality=4,
        late_count=1000,
        spread=20.0,
        draw_key="switchgear-pair",
        notes=(
            "The other half of the exact tie. Built from an identical spec so the harm values "
            "are equal by construction; the tiebreak — need-by, then criticality, then "
            "po_line_id — is what decides the order, and po_line_id is what makes it total."
        ),
        case="exact_harm_tie",
    ),
    LineSpec(
        po_number="PO-4473",
        line_number=1,
        project_id="PRJ-001",
        vendor_id="VND-002",
        description="Fire pump controller FPC-1",
        need_by_offset=70,
        criticality=5,
        late_count=None,
        notes=(
            "No posterior in the active run — the line was added after the run was fitted. "
            "Listed, unranked, carrying no figures at all."
        ),
        case="no_posterior",
    ),
    LineSpec(
        po_number="PO-4473",
        line_number=2,
        project_id="PRJ-001",
        vendor_id="VND-004",
        description="Chilled water pumps, pair",
        need_by_offset=80,
        criticality=3,
        late_count=1400,
        roster_hash=OTHER_ROSTER_HASH,
        notes=(
            "A well-formed roster digest that differs from the run's. Well-formed matters: a "
            "malformed one would be refused by ck_pol__roster_hash_format and would exercise "
            "the schema instead of the state."
        ),
        case="roster_mismatch",
    ),
    LineSpec(
        po_number="PO-4474",
        line_number=1,
        project_id="PRJ-002",
        vendor_id="VND-001",
        description="Rooftop unit RTU-2",
        need_by_offset=0,
        criticality=5,
        late_count=4000,
        notes=(
            "Need-by exactly on the as-of date. The survival grid is one-based over "
            "k = 1..horizon and stores no k = 0, so there is no offset to read — which is why "
            "the state resolves at `offset <= 0` and not at 'strictly earlier than'."
        ),
        case="need_by_equals_as_of",
    ),
    LineSpec(
        po_number="PO-4474",
        line_number=2,
        project_id="PRJ-002",
        vendor_id="VND-002",
        description="Exhaust fans, mechanical penthouse",
        need_by_offset=-10,
        criticality=4,
        late_count=4000,
        notes=(
            "Need-by before the as-of date: already late by construction. The miss probability "
            "is 1 and uninformative, so it is withheld; the quantile pair is sound and says how "
            "much further slip is coming, which is the question that remains open."
        ),
        case="need_by_before_as_of",
    ),
    LineSpec(
        po_number="PO-4475",
        line_number=1,
        project_id="PRJ-002",
        vendor_id="VND-003",
        description="Louvers and dampers, north facade",
        need_by_offset=1,
        criticality=2,
        late_count=3600,
        notes=(
            "Need-by strictly between the as-of date and today: the run still forecasts it and "
            "the coordinator's calendar has passed it. Both facts are true and the row states "
            "them separately."
        ),
        case="need_by_between_as_of_and_today",
    ),
    LineSpec(
        po_number="PO-4475",
        line_number=2,
        project_id="PRJ-002",
        vendor_id="VND-004",
        description="Cooling tower CT-1",
        need_by_offset=365,
        criticality=3,
        late_count=40,
        spread=60.0,
        notes="The last in-grid day. survival[365] exists, so a figure is readable.",
        case="need_by_last_in_grid_day",
    ),
    LineSpec(
        po_number="PO-4476",
        line_number=1,
        project_id="PRJ-003",
        vendor_id="VND-001",
        description="Emergency generator EG-1",
        need_by_offset=366,
        criticality=5,
        late_count=35,
        spread=60.0,
        notes=(
            "One day past the horizon. The grid stops at 365, so only the residual tail mass is "
            "available and only as a bound — the neighbour of PO-4475-2 by exactly one day, "
            "which is what makes the horizon edge a tested boundary and not an assumption."
        ),
        case="need_by_day_after_horizon",
    ),
    LineSpec(
        po_number="PO-4476",
        line_number=2,
        project_id="PRJ-003",
        vendor_id="VND-002",
        description="Transfer switches, ATS-1 and ATS-2",
        need_by_offset=380,
        criticality=4,
        late_count=3,
        spread=90.0,
        horizon_tail_count=3,
        notes=(
            "Residual tail mass of 3/4000 = 0.075%, which rounds to 0% at whole percent. The "
            "display must read '<1%' rather than '0%': a bound of zero states certainty the "
            "posterior does not carry."
        ),
        case="residual_tail_rounds_to_extreme",
    ),
    LineSpec(
        po_number="PO-4477",
        line_number=1,
        project_id="PRJ-003",
        vendor_id="VND-003",
        description="VAV boxes, floors 4-6",
        need_by_offset=55,
        criticality=3,
        late_count=1420,
        notes=(
            "Miss probability of 1420/4000 = 35.5% exactly — a half-percent boundary. Whatever "
            "rounding rule is chosen, it must be stated and applied identically here and at "
            "PO-4477-2, which sits on the boundary from the other side."
        ),
        case="miss_probability_half_percent_up",
    ),
    LineSpec(
        po_number="PO-4477",
        line_number=2,
        project_id="PRJ-003",
        vendor_id="VND-004",
        description="VAV boxes, floors 7-9",
        need_by_offset=55,
        criticality=3,
        late_count=1460,
        notes="Miss probability of 1460/4000 = 36.5% exactly — the other side of the boundary.",
        case="miss_probability_half_percent_down",
    ),
    LineSpec(
        po_number="PO-4478",
        line_number=1,
        project_id="PRJ-001",
        vendor_id="VND-001",
        description="Hydronic specialties package",
        need_by_offset=90,
        criticality=1,
        late_count=60,
        spread=15.0,
        notes=(
            "Second from the bottom of the ranking, with enough clearance above and below that "
            "pulling its need-by in raises its harm without moving its position. The exact pull "
            "is searched for at generation time and recorded in `adjustment` — hand-picking one "
            "would leave its harmlessness resting on numbers nobody rechecks when the fixture "
            "moves. FR-012 requires such an adjustment to be acknowledged as applied, since "
            "silence is indistinguishable from it having been ignored."
        ),
        case="adjustment_changes_no_ordering",
        tags=["adjustment_target"],
    ),
    LineSpec(
        po_number="PO-4479",
        line_number=1,
        project_id="PRJ-001",
        vendor_id="VND-002",
        description="Pipe insulation, mechanical rooms",
        need_by_offset=30,
        criticality=2,
        late_count=100,
        lifecycle_state="delivered",
        notes=(
            "Terminal. Excluded from the worklist entirely by FR-022 — present in the fixture "
            "precisely so the exclusion is proved against a line that exists rather than "
            "against one that does not."
        ),
        case="terminal_line",
    ),
)


def build() -> dict[str, object]:
    """Build the whole fixture as a JSON-serialisable document."""
    lines: list[dict[str, object]] = []

    for index, spec in enumerate(SPECS, start=1):
        # Deterministic and stable under reordering: derived from the natural
        # key rather than from the loop counter, so inserting a line in the
        # middle does not renumber every id after it.
        po_line_id = _uuid_from(f"{spec.project_id}/{spec.po_number}/{spec.line_number}")
        need_by = AS_OF + timedelta(days=spec.need_by_offset)

        record: dict[str, object] = {
            "po_line_id": po_line_id,
            "ordinal": index,
            "project_id": spec.project_id,
            "vendor_id": spec.vendor_id,
            "po_number": spec.po_number,
            "line_number": spec.line_number,
            "description": spec.description,
            "need_by_date": need_by.isoformat(),
            "need_by_offset": spec.need_by_offset,
            "criticality": spec.criticality,
            "lifecycle_state": spec.lifecycle_state,
            "roster_hash": spec.roster_hash,
            "case": spec.case,
            "notes": spec.notes,
            "tags": spec.tags,
            "posterior": None,
        }

        if spec.late_count is not None:
            draws = _build_draws(spec)
            survival = _survival_from_draws(draws)
            record["posterior"] = {
                "draws": draws,
                "survival": survival,
                "residual_tail_mass": survival[-1],
            }
            # The expected values, carried as data so a test reads them rather
            # than recomputing them alongside the implementation.
            record["expected"] = {
                "miss_probability": (
                    survival[spec.need_by_offset - 1]
                    if 1 <= spec.need_by_offset <= HORIZON_DAYS
                    else None
                ),
                "p50_offset": _quantile(draws, 50),
                "p80_offset": _quantile(draws, 80),
                "expected_harm": _expected_harm(draws, spec.need_by_offset, spec.criticality),
            }

        lines.append(record)

    ranking = _ranking(lines)
    adjustment = _no_op_adjustment(lines, ranking)

    document: dict[str, object] = {
        "ranking": ranking,
        "adjustment": adjustment,
        "provenance": {
            "kind": "test_fixture",
            "layer": "SYNTHETIC",
            "generator": GENERATOR,
            "seed": SEED,
            "generated_on": GENERATED_ON,
            "regenerate_command": REGENERATE_COMMAND,
            "note": (
                "Synthetic data committed to the repository. It carries generator provenance "
                "because that is the provenance it has; it carries no retrieval provenance "
                "because it was not retrieved from anywhere, and inventing a source for it "
                "would be the exact defect provenance exists to prevent."
            ),
        },
        "run": {
            "run_id": RUN_ID,
            "as_of_date": AS_OF.isoformat(),
            "horizon_days": HORIZON_DAYS,
            "draw_count": DRAW_COUNT,
            "roster_hash": ROSTER_HASH,
            "model_version": "lognormal-hierarchical-v3",
            "artifact_schema_version": 1,
        },
        "cases": CASES,
        "lines": lines,
    }
    document["row_digest"] = _row_digest(lines)
    return document


#: Lines excluded from the ranking, by the state that excludes them. Terminal
#: lines leave the worklist entirely (FR-022); the other two are listed but
#: unranked (FR-016, FR-021).
_UNRANKED_CASES: Final[frozenset[str]] = frozenset(
    {"no_posterior", "roster_mismatch", "terminal_line"}
)


def _ranking(lines: list[dict[str, object]]) -> list[dict[str, object]]:
    """The expected order under the default key, carried as data.

    FR-001 and FR-013a: expected harm descending, then need-by ascending, then
    criticality descending, then ``po_line_id`` ascending. The last is what
    makes the tiebreak *total* — without it the two exact-tie lines have no
    defined order and the ranking is not reproducible across reloads (FR-010).
    """
    ranked = [line for line in lines if line["case"] not in _UNRANKED_CASES]
    ranked.sort(
        key=lambda line: (
            -line["expected"]["expected_harm"],  # type: ignore[index]
            line["need_by_date"],
            -line["criticality"],  # type: ignore[operator]
            line["po_line_id"],
        )
    )
    return [
        {
            "rank": position,
            "po_line_id": line["po_line_id"],
            "identifier": f"{line['po_number']}-{line['line_number']}",
            "expected_harm": line["expected"]["expected_harm"],  # type: ignore[index]
        }
        for position, line in enumerate(ranked, start=1)
    ]


def _no_op_adjustment(
    lines: list[dict[str, object]], ranking: list[dict[str, object]]
) -> dict[str, object]:
    """Find a need-by adjustment that provably changes no ordering.

    FR-012 requires an adjustment that changes nothing to be *acknowledged as
    applied*, which is only testable against an adjustment known to change
    nothing. Searched rather than asserted: an adjustment hand-picked to look
    harmless is one whose harmlessness depends on numbers nobody rechecked when
    the fixture moved.
    """
    target = next(line for line in lines if "adjustment_target" in line["tags"])  # type: ignore[operator]
    draws: list[float] = target["posterior"]["draws"]  # type: ignore[index,assignment]
    baseline = [entry["po_line_id"] for entry in ranking]

    # Pull the date in a day at a time and keep the largest pull that leaves the
    # order untouched, so the case is not merely satisfied but satisfied with
    # the most visible adjustment that still satisfies it.
    chosen: int | None = None
    for pull in range(1, 31):
        offset = target["need_by_offset"] - pull  # type: ignore[operator]
        harm = _expected_harm(draws, offset, target["criticality"])  # type: ignore[arg-type]
        moved = [dict(line) for line in lines]
        for line in moved:
            if line["po_line_id"] == target["po_line_id"]:
                line["expected"] = dict(line["expected"])  # type: ignore[arg-type]
                line["expected"]["expected_harm"] = harm  # type: ignore[index]
                line["need_by_date"] = (AS_OF + timedelta(days=offset)).isoformat()
        if [entry["po_line_id"] for entry in _ranking(moved)] == baseline:
            chosen = pull
        else:
            break

    if chosen is None:  # pragma: no cover - the fixture is built so one exists
        raise RuntimeError(
            "No pull of the adjustment target leaves the ordering unchanged. FR-012's case "
            "needs one, so the fixture's harm spread must be widened rather than the case dropped."
        )

    offset = target["need_by_offset"] - chosen  # type: ignore[operator]
    return {
        "po_line_id": target["po_line_id"],
        "identifier": f"{target['po_number']}-{target['line_number']}",
        "need_by_date_of_record": target["need_by_date"],
        "adjusted_need_by_date": (AS_OF + timedelta(days=offset)).isoformat(),
        "days_pulled_in": chosen,
        "expected_harm_before": target["expected"]["expected_harm"],  # type: ignore[index]
        "expected_harm_after": _expected_harm(draws, offset, target["criticality"]),  # type: ignore[arg-type]
        "expected_ordering_unchanged": True,
        "note": (
            "FR-012. The harm rises and the position does not, so the interface must say the "
            "adjustment was applied and the order did not change — silence would be "
            "indistinguishable from the adjustment having been ignored."
        ),
    }


def _uuid_from(key: str) -> str:
    """A stable UUIDv5-shaped identifier derived from the natural key."""
    digest = sha256(key.encode("utf-8")).hexdigest()
    return f"{digest[0:8]}-{digest[8:12]}-5{digest[13:16]}-a{digest[17:20]}-{digest[20:32]}"


def _row_digest(lines: list[dict[str, object]]) -> str:
    """FR-037. A digest over every row, so a silent edit is detectable.

    Computed over the canonical serialisation — sorted keys, UTF-8, no spurious
    whitespace — which is the same convention `forecast_run.canonical_serialization`
    records for the artifacts themselves.
    """
    payload = json.dumps(lines, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "sha256:" + sha256(payload.encode("utf-8")).hexdigest()


def main() -> None:
    document = build()
    target = Path(__file__).parent / "fixture.json"
    # Canonical and compact: sorted keys, no indentation. Indentation would add
    # roughly half a megabyte of whitespace around sixty thousand floats and buy
    # no readability — nobody reviews a draw array by eye. What is reviewable is
    # `PROVENANCE.md`, the specs above, and the row digest, which is what a diff
    # should actually be checked against.
    target.write_text(
        json.dumps(document, sort_keys=True, separators=(",", ":"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"wrote {target} ({target.stat().st_size} bytes)")
    print(f"row digest: {document['row_digest']}")


if __name__ == "__main__":
    main()
