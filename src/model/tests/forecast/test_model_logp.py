"""The PyMC graph's log-density against `likelihood.py`, the pure reference.

T028 and T116. The graph is the only thing in this epic that cannot be property
-tested directly, so its per-row contribution is compared against the NumPy
implementation over hand-built extremes: a σ two orders of magnitude either side
of plausible, offsets drawn from an extreme τ, a vendor carrying exactly one
sojourn, and a leg censored on the day it began.

**T116 is the load-bearing half.** The oracle rebuilds `likelihood.py`'s `mu`
from `design.py`'s `vendor_index` and `category_index` by dictionary lookup —
never from `frame.design`, and never from the `design @ offsets` product the
graph assembled. Feeding the oracle the graph's own row assembly would make it
inherit the index mapping it exists to check, and the agreement would then hold
just as well with two vendors swapped. The swapped case is asserted to fail.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import numpy as np
import pytensor
import pytest
from numpy.typing import NDArray

from model.forecast.design import category_index, vendor_index
from model.forecast.likelihood import log_contribution
from model.forecast.model import (
    LOG_CONTRIBUTION,
    REWORK_OBSERVATION,
    SojournFrame,
    build_model,
    build_sojourn_frame,
)
from model.forecast.read import LifecycleEventRow, LineRow

NS = uuid.uuid5(uuid.NAMESPACE_URL, "e007/tests/forecast/model-logp")

#: A roster small enough that one vendor can be given a single sojourn row and
#: still leave the hierarchy something to pool over. The identifiers follow the
#: committed roster's shape; nothing here reads `data/roster`, because this tier
#: is asserting an arithmetic identity rather than a fact about the dataset.
VENDORS: tuple[str, ...] = ("VND-0001", "VND-0002", "VND-0003", "VND-0004")
CATEGORIES: tuple[str, ...] = ("electrical-gear", "process-piping", "rotating-equipment")

ORDER_DATE = date(2025, 9, 1)
AS_OF = date(2025, 12, 1)

#: A clean forward walk and the walk with one rework loop, spelled out rather
#: than generated: the point of this tier is that the expected value is obvious.
FORWARD_WALK = (
    "submitted",
    "under_review",
    "approved",
    "released_for_fabrication",
    "shipped",
    "delivered",
)
REWORK_WALK = (
    "submitted",
    "under_review",
    "revise_and_resubmit",
    "submitted",
    "under_review",
    "approved",
    "released_for_fabrication",
    "shipped",
    "delivered",
)

TOLERANCE = {"rtol": 1e-11, "atol": 1e-11}


def make_line(
    ordinal: int,
    vendor_id: str,
    material_category: str,
    states: tuple[str, ...],
    gaps: tuple[int, ...],
) -> LineRow:
    """A line that walked `states`, with `gaps[i]` days before state `i + 1`.

    The first event lands on the order date, which is how the loaded dataset
    records a line's own submission, so the first sojourn is the time spent in
    `submitted` rather than a zero-length leg nothing measured.
    """
    if len(gaps) != len(states) - 1:
        raise ValueError("one gap per step between states")
    po_line_id = uuid.uuid5(NS, f"pol|{ordinal}")
    moment = ORDER_DATE
    events: list[LifecycleEventRow] = []
    for position, state in enumerate(states):
        if position:
            moment = moment + timedelta(days=gaps[position - 1])
        events.append(
            LifecycleEventRow(
                event_id=uuid.uuid5(NS, f"evt|{ordinal}|{position}"),
                po_line_id=po_line_id,
                sequence_no=position + 1,
                from_state=states[position - 1] if position else None,
                to_state=state,
                is_terminal=state == "delivered",
                occurred_at=datetime(moment.year, moment.month, moment.day, tzinfo=UTC),
                note=None,
            )
        )
    return LineRow(
        po_line_id=po_line_id,
        project_id=f"PRJ-{1 + ordinal % 3:03d}",
        vendor_id=vendor_id,
        po_number=f"PO-{ordinal:04d}-0001",
        line_number=1,
        material_category=material_category,
        description="Water Chiller (Tag 201-14)",
        manufacturer="Ironvane Thermal",
        part_number=f"IRV-2365-{ordinal:04d}",
        quantity=Decimal("6.0"),
        unit_of_measure="EA",
        order_date=ORDER_DATE,
        need_by_date=ORDER_DATE + timedelta(days=180),
        criticality=3,
        lifecycle_state=states[-1],
        is_closed=states[-1] == "delivered",
        closing_event_id=None,
        roster_hash="sha256:" + "0" * 64,
        events=tuple(events),
    )


def cohort() -> tuple[LineRow, ...]:
    """Six lines covering every branch the contribution expression can take.

    One delivered line per hierarchy member is not the point; what is covered is
    the *shape* of each row — a completed leg, a censored leg out of a
    single-exit state, a censored leg out of the one decision state (the two-way
    mixture), a leg censored on the day it began, and a vendor whose whole
    contribution to the fit is one row.
    """
    return (
        # Delivered, clean forward walk.
        make_line(0, "VND-0001", "electrical-gear", FORWARD_WALK, (4, 9, 3, 21, 12)),
        # Delivered, one rework loop — two decision points, the second at cycle 1.
        make_line(1, "VND-0001", "process-piping", REWORK_WALK, (6, 11, 2, 7, 14, 5, 18, 9)),
        # Open in `shipped`: a censored leg out of a single-exit state.
        make_line(2, "VND-0002", "rotating-equipment", FORWARD_WALK[:5], (3, 8, 5, 30)),
        # Open in `under_review` at cycle 1: the censored two-way mixture.
        make_line(3, "VND-0002", "electrical-gear", REWORK_WALK[:5], (2, 4, 1, 6)),
        # Open, and the last event landed on the anchor: `S(0) = 1` exactly.
        make_line(4, "VND-0003", "process-piping", FORWARD_WALK[:4], (5, 10, 76)),
        # The one-row vendor: submitted and nothing since, so its whole
        # contribution to the fit is a single censored leg.
        make_line(5, "VND-0004", "rotating-equipment", FORWARD_WALK[:1], ()),
    )


def frame_over(
    lines: tuple[LineRow, ...] = (),
    vendors: tuple[str, ...] = VENDORS,
    categories: tuple[str, ...] = CATEGORIES,
) -> SojournFrame:
    """The cohort's sojourn frame at the anchor, against a stated roster order."""
    return build_sojourn_frame(lines or cohort(), vendors, categories, AS_OF)


# ---------------------------------------------------------------------------
# Reading the graph
# ---------------------------------------------------------------------------


def parameter_point(model: object, seed: int, spread: float) -> dict[str, NDArray[np.float64]]:
    """A point in the sampler's own (transformed) space, at a stated spread.

    Written in transformed space on purpose. Every constrained parameter — the
    two group scales, the seven log-scales, both zero-sum offset vectors — has
    its bijection applied by PyMC, so a large draw here is what "extreme σ" and
    "extreme τ" actually mean to the sampler, and the untransformed values are
    read back from the graph rather than guessed at.
    """
    generator = np.random.default_rng(seed)
    point = model.initial_point()  # type: ignore[attr-defined]
    return {
        name: np.asarray(
            generator.normal(0.0, spread, size=np.shape(value)), dtype=np.asarray(value).dtype
        )
        for name, value in point.items()
    }


def graph_rows(model: object, point: dict[str, NDArray[np.float64]]) -> NDArray[np.float64]:
    """The per-row contribution the graph's own log-density is built from.

    Reached through `replace_rvs_by_values`, which is what PyMC does when it
    compiles `logp`: the returned vector is the `Potential`'s value at this
    point, not a re-evaluation of the expression beside it.
    """
    (values,) = model.replace_rvs_by_values(  # type: ignore[attr-defined]
        [model.named_vars[LOG_CONTRIBUTION]]  # type: ignore[attr-defined]
    )
    inputs = list(model.value_vars)  # type: ignore[attr-defined]
    compiled = pytensor.function(inputs, values, on_unused_input="ignore")
    return np.asarray(compiled(*[point[variable.name] for variable in inputs]), dtype=float)


def parameters_at(model: object, point: dict[str, NDArray[np.float64]]) -> dict[str, object]:
    """The untransformed parameter values this point stands for.

    Read back from the graph rather than recomputed. The zero-sum transform in
    particular maps `n − 1` free values onto `n` offsets summing to zero, and a
    test that reimplemented it would be asserting its own arithmetic.

    The deterministics are read alongside the free variables because AD-012
    made `vendor_offset` one of them — it is `tau_vendor · z` rather than a
    sampled vector, and the oracle wants the product the design matrix is
    actually multiplied by. Recomputing it here from `tau_vendor` and `z` would
    be this test re-deriving the very expression under test.
    """
    free = [*model.free_RVs, *model.deterministics]  # type: ignore[attr-defined]
    replaced = model.replace_rvs_by_values(free)  # type: ignore[attr-defined]
    inputs = list(model.value_vars)  # type: ignore[attr-defined]
    compiled = pytensor.function(inputs, replaced, on_unused_input="ignore")
    drawn = compiled(*[point[variable.name] for variable in inputs])
    return {variable.name: value for variable, value in zip(free, drawn, strict=True)}


# ---------------------------------------------------------------------------
# T116: the oracle, built from `design.py` and never from the graph's assembly
# ---------------------------------------------------------------------------


def oracle_rows(
    frame: SojournFrame,
    lines: tuple[LineRow, ...],
    parameters: dict[str, object],
    vendors: tuple[str, ...] = VENDORS,
    categories: tuple[str, ...] = CATEGORIES,
    swap_branches: bool = False,
) -> NDArray[np.float64]:
    """`likelihood.py`'s answer for every row, assembled independently.

    The log-location is `mu_sojourn[k] + vendor_offset[j] + category_offset[c]`
    with `j` and `c` taken from `design.py`'s two index functions by *lookup on
    the line's own identifiers*. `frame.design` is deliberately untouched: the
    graph forms the same quantity as a matrix product, and reusing the matrix
    would let a permuted index satisfy both sides at once (A-002).

    `swap_branches` is the planted failure — density where a survival belongs
    and the reverse — because an agreement test that never sees the swap it was
    written for is an agreement with itself.
    """
    vendors_at = vendor_index(vendors)
    categories_at = category_index(categories)
    by_id = {line.po_line_id: line for line in lines}

    mu_sojourn = np.asarray(parameters["mu_sojourn"], dtype=float)
    sigma_sojourn = np.asarray(parameters["sigma_sojourn"], dtype=float)
    vendor_offset = np.asarray(parameters["vendor_offset"], dtype=float)
    category_offset = np.asarray(parameters["category_offset"], dtype=float)
    intercept = float(np.asarray(parameters["rework_intercept"]))
    beta = float(np.asarray(parameters["rework_approval_cycle_beta"]))

    contributions = np.empty(frame.row_count, dtype=float)
    # At the widest spread the survival underflows to exactly zero deep in the
    # tail and its log is `-inf` — the honest answer, which the graph reaches
    # too, so the comparison still holds there. The warning is suppressed rather
    # than the extreme avoided: the extreme is the point of the parametrization.
    with np.errstate(divide="ignore"):
        for row in range(frame.row_count):
            line = by_id[frame.po_line_ids[row]]
            group = (
                vendor_offset[vendors_at[line.vendor_id]]
                + category_offset[categories_at[line.material_category]]
            )
            duration = float(frame.duration_days[row])

            if bool(frame.is_censored[row]) == swap_branches:
                realized = int(frame.transition_index[row])
                # `f(0)` has no value and `likelihood.py` refuses it, so a row
                # the swap moved onto the density branch at zero elapsed days
                # has no counterfactual. `NaN` rather than a substitute, so a
                # *correct* oracle that ever produced one fails visibly.
                contributions[row] = (
                    np.nan
                    if duration == 0.0
                    else float(
                        log_contribution(
                            duration,
                            mu_sojourn[realized] + group,
                            sigma_sojourn[realized],
                            False,
                        )
                    )
                )
                continue

            forward = int(frame.forward_transition_index[row])
            survives_forward = float(
                log_contribution(
                    duration, mu_sojourn[forward] + group, sigma_sojourn[forward], True
                )
            )
            if not bool(frame.is_decision[row]):
                contributions[row] = survives_forward
                continue

            looped = int(frame.rework_transition_index[row])
            survives_rework = float(
                log_contribution(duration, mu_sojourn[looped] + group, sigma_sojourn[looped], True)
            )
            logit = intercept + beta * float(frame.approval_cycle_count[row])
            probability = 1.0 / (1.0 + np.exp(-logit))
            contributions[row] = float(
                np.logaddexp(
                    np.log1p(-probability) + survives_forward,
                    np.log(probability) + survives_rework,
                )
            )
    return contributions


# ---------------------------------------------------------------------------
# The frame is the one this tier claims it is
# ---------------------------------------------------------------------------


def test_the_cohort_exercises_every_branch_of_the_contribution() -> None:
    """Each row shape the expression can take is present, so agreement means something.

    An agreement test over a frame with no censored row would pass on an
    implementation that never wrote the survival branch at all.
    """
    frame = frame_over()

    censored = frame.is_censored
    assert censored.sum() == 4, "four lines are open at the anchor"
    assert (~censored).sum() == frame.row_count - 4
    assert bool(np.any(censored & frame.is_decision)), "the two-way mixture is exercised"
    assert bool(np.any(censored & (frame.duration_days == 0.0))), "`S(0)` is exercised"
    assert frame.decision_rows.size == 6, "six completed decision points feed the Bernoulli"
    assert not bool(frame.is_censored[frame.decision_rows].any()), (
        "a censored decision point has not chosen its branch and must not be observed"
    )
    assert float(frame.approval_cycle_count.max()) == 1.0, "a second approval cycle is present"


def test_the_one_row_vendor_contributes_exactly_one_sojourn() -> None:
    """`VND-0004` appears in one row, which is where a mis-indexed offset hides.

    A vendor with many rows averages a wrong offset away into something
    plausible; a vendor with one row carries it undiluted.
    """
    frame = frame_over()
    column = vendor_index(VENDORS)["VND-0004"]
    assert int(frame.design[:, column].sum()) == 1


# ---------------------------------------------------------------------------
# T028 / T116: agreement
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("seed", "spread"),
    [
        (11, 0.25),  # near the prior's centre
        (23, 1.0),  # ordinary posterior territory
        (37, 2.5),  # extreme sigma and extreme tau together
        (53, 4.0),  # further than any converged fit would visit
    ],
)
def test_the_graph_agrees_with_the_pure_likelihood(seed: int, spread: float) -> None:
    """Every row of the graph's contribution equals `likelihood.py`'s (FR-003).

    Parametrized over spread rather than over a hand-written parameter vector
    because the interesting disagreements are numerical: at `spread = 4` the
    log-scales run over several orders of magnitude and the survival is deep
    enough in the tail that `1 − Φ(z)` would have lost every digit.
    """
    lines = cohort()
    frame = frame_over(lines)
    model = build_model(frame)
    point = parameter_point(model, seed, spread)

    np.testing.assert_allclose(
        graph_rows(model, point),
        oracle_rows(frame, lines, parameters_at(model, point)),
        **TOLERANCE,
    )


def test_the_potential_is_the_term_the_models_own_logp_sums() -> None:
    """The compared vector is inside `model.logp()`, not beside it.

    Agreement on an expression the sampler never evaluates would be worth
    nothing, so the total log-density is decomposed: everything that is not a
    prior and not the rework Bernoulli is the sojourn contribution, and that
    remainder is what the oracle is checked against.
    """
    lines = cohort()
    frame = frame_over(lines)
    model = build_model(frame)
    point = parameter_point(model, 71, 1.0)

    total = float(model.compile_logp()(point))
    priors = float(model.compile_logp(vars=model.free_RVs)(point))
    branches = float(model.compile_logp(vars=[model[REWORK_OBSERVATION]])(point))

    assert LOG_CONTRIBUTION in {potential.name for potential in model.potentials}
    np.testing.assert_allclose(
        total - priors - branches,
        float(oracle_rows(frame, lines, parameters_at(model, point)).sum()),
        rtol=1e-9,
        atol=1e-9,
    )


# ---------------------------------------------------------------------------
# The two silent failures this test exists for
# ---------------------------------------------------------------------------


def test_a_censored_row_takes_the_survival_and_a_completed_row_the_density() -> None:
    """The branch is asserted per row, against `likelihood.py` called both ways.

    `spec.md` US2 names the swap as the epic's headline silent failure: a
    censored line written as a density at its censoring time builds cleanly and
    yields a plausible posterior. So each row is compared against the
    contribution its own status entitles it to, and the comparison is only
    evidence because the *other* contribution is asserted to differ.
    """
    lines = cohort()
    frame = frame_over(lines)
    model = build_model(frame)
    point = parameter_point(model, 89, 1.0)
    parameters = parameters_at(model, point)

    correct = oracle_rows(frame, lines, parameters)
    swapped = oracle_rows(frame, lines, parameters, swap_branches=True)

    np.testing.assert_allclose(graph_rows(model, point), correct, **TOLERANCE)

    # Every row whose duration is positive must actually distinguish the two.
    # `S(0) = 1` and `f(0)` is refused, so the zero-duration censored row is the
    # one place the swap has nothing to say and is excluded from the claim.
    moved = frame.duration_days > 0.0
    assert not np.allclose(correct[moved], swapped[moved]), (
        "the density and the survival agree on every row, so this test would pass "
        "against an implementation that took the wrong branch throughout"
    )
    assert np.all(swapped[frame.is_censored & moved] != correct[frame.is_censored & moved])
    assert np.all(swapped[~frame.is_censored] != correct[~frame.is_censored])


def test_a_swapped_vendor_index_breaks_the_agreement() -> None:
    """T116's whole point: the oracle is sensitive to which vendor is which.

    The frame is built against a roster with two vendors exchanged while the
    oracle keeps the true order. Nothing about the shapes changes — the design
    matrix has the same rank, the same column count and the same row sums — and
    the fit would sample exactly as well. Only the attribution moves, and the
    agreement must break on it. An oracle fed `frame.design`, or fed the graph's
    `design @ offsets` product, would pass this case.
    """
    lines = cohort()
    swapped_roster = ("VND-0002", "VND-0001", "VND-0003", "VND-0004")
    frame = frame_over(lines, vendors=swapped_roster)
    model = build_model(frame)
    point = parameter_point(model, 97, 1.0)
    parameters = parameters_at(model, point)

    against_true_order = oracle_rows(frame, lines, parameters, vendors=VENDORS)
    against_swapped_order = oracle_rows(frame, lines, parameters, vendors=swapped_roster)

    np.testing.assert_allclose(graph_rows(model, point), against_swapped_order, **TOLERANCE)
    assert not np.allclose(against_true_order, against_swapped_order), (
        "the two rosters produce the same contribution, so this cohort cannot detect a "
        "permuted vendor index and the negative control asserts nothing"
    )
    with pytest.raises(AssertionError):
        np.testing.assert_allclose(graph_rows(model, point), against_true_order, **TOLERANCE)
