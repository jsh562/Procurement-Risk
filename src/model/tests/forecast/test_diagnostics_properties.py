"""T078 (RED) — the threshold comparison and the pass verdict, as properties.

`plan.md` § What qualifies admits `diagnostics.py` to the property tier, so this
file is the RED half of the T078/T079 pair and must fail at collection: the
module under test does not exist yet.

**Wrong is silent here, which is why every claim below is stated in both
directions.** `ck_forecast_diagnostic__blocking_rows_passed` refuses a stored
blocking row whose `passed` is false, so the row the database *admits* is the
one wrongly marked passed — a breach that read as a pass would be written,
committed and published, and no constraint anywhere would object. A test that
only checked "a good value passes" would be satisfied by a function returning
`True`, and a test that only checked "a bad value fails" by one returning
`False`. Both halves, at every metric, are what pin the comparison.

Four boundary regions per metric, because a comparison can be wrong in each one
independently: strictly beyond the bar in the metric's **declared direction**;
strictly inside it; **exactly at** the threshold, where `<=` and `<` differ; and
`NaN`, where every comparison is false and a diverged sampler is what produces
one. The two infinities are included with the third: `+inf` satisfies a floor
arithmetically, and a blocking metric that could not be measured is not a metric
that passed (`plan.md` § Error Handling Strategy).
"""

from __future__ import annotations

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from model.forecast.config import (
    DIAGNOSTIC_THRESHOLDS,
    DIVERGENCES_MAX,
    EBFMI_MIN,
    ESS_BULK_MIN,
    ESS_TAIL_MIN,
    MAX_TREEDEPTH_HITS,
    R_HAT_MAX,
    DiagnosticThreshold,
    blocking_diagnostics,
)
from model.forecast.diagnostics import (
    PARAMETER_METRICS,
    RUN_METRICS,
    DiagnosticsError,
    blocking_breaches,
    diagnostic_row,
    passes,
    threshold_for,
)

# ---------------------------------------------------------------------------
# The interface this file pins
# ---------------------------------------------------------------------------
#
# `threshold_for(metric)` returns the `DiagnosticThreshold` `config.py`
# publishes for that metric, and raises `DiagnosticsError` on a metric outside
# `ck_forecast_diagnostic__metric`'s six.
#
# `passes(threshold, observed)` is the comparison and nothing else — the same
# arithmetic `ck_forecast_diagnostic__passed_matches_threshold` performs, so a
# row assembled here and a row the database re-derives cannot disagree.
#
# `diagnostic_row(metric, observed_value, parameter_name=None)` assembles one
# row in the vocabulary `forecast_diagnostic` stores it in, with `passed`
# computed rather than supplied.
#
# `blocking_breaches(rows)` is the gate FR-017 refuses on: the blocking rows
# that did not pass, **plus** any blocking row whose value could not be
# measured.

#: A generous but finite band for a realized value. Bounded rather than
#: unbounded so `exclude_min` and `exclude_max` have a neighbour to reach for;
#: the arithmetic under test is scale-free and a wider band buys nothing.
VALUE_SPAN = 1.0e9

#: Every metric's published bar, quoted from `config.py` by *name* rather than
#: by value, so this file cannot become a second home for a number that already
#: has one. The tuple is what the parametrisations below range over.
PUBLISHED_BARS: dict[str, float] = {
    "r_hat": float(R_HAT_MAX),
    "ess_bulk": float(ESS_BULK_MIN),
    "ess_tail": float(ESS_TAIL_MIN),
    "divergent_transitions": float(DIVERGENCES_MAX),
    "ebfmi": float(EBFMI_MIN),
    "max_treedepth_hits": float(MAX_TREEDEPTH_HITS),
}

#: The six metrics, taken from the published tuple rather than retyped.
METRICS: tuple[str, ...] = tuple(row.metric for row in DIAGNOSTIC_THRESHOLDS)

#: A parameter name for the three parameter-scoped metrics.
#: `ck_forecast_diagnostic__parameter_name_present` refuses a blank one, so a
#: real name is used rather than a placeholder.
A_PARAMETER = "tau_vendor"


def _row(metric: str, observed: float):
    """One row for `metric`, with the parameter name its scope requires.

    Wrapped rather than repeated at every call site, because the biconditional
    `ck_forecast_diagnostic__parameter_iff_parameter_scope` makes the argument
    mandatory on three metrics and forbidden on the other three, and a test
    that got that wrong would fail for the wrong reason.
    """
    scoped = metric in PARAMETER_METRICS
    return diagnostic_row(metric, observed, A_PARAMETER if scoped else None)


def _beyond(threshold: DiagnosticThreshold) -> st.SearchStrategy[float]:
    """Realized values that breach `threshold` in its **declared** direction.

    Strictly beyond, never at: the boundary is its own case below, and folding
    it in here would let a `<` written where `<=` was meant survive both.
    """
    if threshold.threshold_direction == "max":
        return st.floats(
            min_value=threshold.threshold_value,
            max_value=VALUE_SPAN,
            exclude_min=True,
            allow_nan=False,
            allow_infinity=False,
        )
    return st.floats(
        min_value=-VALUE_SPAN,
        max_value=threshold.threshold_value,
        exclude_max=True,
        allow_nan=False,
        allow_infinity=False,
    )


def _within(threshold: DiagnosticThreshold) -> st.SearchStrategy[float]:
    """Realized values that satisfy `threshold`, the boundary excluded."""
    if threshold.threshold_direction == "max":
        return st.floats(
            min_value=-VALUE_SPAN,
            max_value=threshold.threshold_value,
            exclude_max=True,
            allow_nan=False,
            allow_infinity=False,
        )
    return st.floats(
        min_value=threshold.threshold_value,
        max_value=VALUE_SPAN,
        exclude_min=True,
        allow_nan=False,
        allow_infinity=False,
    )


# ---------------------------------------------------------------------------
# The published set itself
# ---------------------------------------------------------------------------


def test_every_published_metric_resolves_to_exactly_one_threshold() -> None:
    """Six metrics, one bar each, and the bar is the number `config.py` states.

    Stated first because every property below quantifies over this set: a
    metric that resolved to no threshold, or to a second one, would make the
    rest of this file assert over a different set than the gate does.
    """
    assert len(METRICS) == len(set(METRICS)) == len(PUBLISHED_BARS)
    for metric in METRICS:
        assert threshold_for(metric).threshold_value == PUBLISHED_BARS[metric]
    assert set(PARAMETER_METRICS) | set(RUN_METRICS) == set(METRICS)
    assert not set(PARAMETER_METRICS) & set(RUN_METRICS)


def test_a_metric_outside_the_published_six_has_no_threshold() -> None:
    """`ck_forecast_diagnostic__metric` closes the set; so does this lookup.

    A seventh metric silently acquiring a default bar is how a gate stops being
    a gate: the row would be storable, `passed` would be arithmetic against a
    number nobody published, and FR-017 would refuse on a threshold that is not
    in Published Constants.
    """
    with pytest.raises(DiagnosticsError):
        threshold_for("energy")


# ---------------------------------------------------------------------------
# The comparison, in both directions, at every metric
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("threshold", DIAGNOSTIC_THRESHOLDS, ids=lambda row: row.metric)
def test_a_value_breaching_in_its_declared_direction_never_yields_passed(
    threshold: DiagnosticThreshold,
) -> None:
    """The silent direction: a breach must never be recorded as a pass.

    `ck_forecast_diagnostic__blocking_rows_passed` refuses a blocking row whose
    `passed` is false, so a breach that read as a pass is exactly the row the
    database accepts — the run ships, the evidence says it converged, and no
    constraint contradicts it. Quantified over the maxima and the minima alike,
    because a comparison written the wrong way round passes one and fails the
    other.
    """

    @given(observed=_beyond(threshold))
    def check(observed: float) -> None:
        row = _row(threshold.metric, observed)

        assert not passes(threshold, observed), (
            f"{threshold.metric} at {observed!r} is beyond its published "
            f"{threshold.threshold_direction} of {threshold.threshold_value!r} and was "
            f"judged to pass"
        )
        assert not row.passed
        assert (row in blocking_breaches([row])) == threshold.is_blocking

    check()


@pytest.mark.parametrize("threshold", DIAGNOSTIC_THRESHOLDS, ids=lambda row: row.metric)
def test_a_value_satisfying_its_declared_direction_always_yields_passed(
    threshold: DiagnosticThreshold,
) -> None:
    """The other direction, without which the claim above is met by `False`.

    A comparison that refused everything would satisfy every breach assertion
    in this file and would refuse every run this epic ever fits. The two halves
    together are what make the verdict a function of the two numbers.
    """

    @given(observed=_within(threshold))
    def check(observed: float) -> None:
        row = _row(threshold.metric, observed)

        assert passes(threshold, observed)
        assert row.passed
        assert not blocking_breaches([row])

    check()


@pytest.mark.parametrize("threshold", DIAGNOSTIC_THRESHOLDS, ids=lambda row: row.metric)
def test_the_published_threshold_itself_is_met_rather_than_breached(
    threshold: DiagnosticThreshold,
) -> None:
    """Exactly at the bar, where `<=` and `<` part company.

    The database settles this in `ck_forecast_diagnostic__passed_matches_
    threshold`: `max` is `observed_value <= threshold_value` and `min` is
    `>=`, so the boundary **passes** on both. A row that disagreed would be
    rejected on insert, which is the good case; the bad case is the mirror —
    the gate refusing a run the database would have accepted, at exactly the
    zero-divergence bar every passing run sits on.
    """
    row = _row(threshold.metric, threshold.threshold_value)

    assert passes(threshold, threshold.threshold_value)
    assert row.passed
    assert not blocking_breaches([row])


@pytest.mark.parametrize("threshold", DIAGNOSTIC_THRESHOLDS, ids=lambda row: row.metric)
def test_a_nan_never_yields_passed_at_any_metric(threshold: DiagnosticThreshold) -> None:
    """A diverged sampler produces a NaN R-hat, and NaN compares false to all.

    Asserted rather than inherited from IEEE 754, because the verdict is what
    reaches the stored row: `observed_value = observed_value` is the NaN test
    `ck_forecast_diagnostic__observed_finite` uses, so a NaN row cannot be
    stored at all — and the gate must therefore refuse *before* the write
    rather than discover it as a constraint violation inside transaction 1.
    """
    row = _row(threshold.metric, math.nan)

    assert not passes(threshold, math.nan)
    assert not row.passed
    assert not row.is_computable
    assert (row in blocking_breaches([row])) == threshold.is_blocking


@pytest.mark.parametrize("threshold", blocking_diagnostics(), ids=lambda row: row.metric)
def test_an_unmeasurable_value_is_a_breach_even_where_the_arithmetic_says_pass(
    threshold: DiagnosticThreshold,
) -> None:
    """`+inf` clears every floor, and a metric that could not be measured has not passed.

    This is the one case where `passed` and the gate legitimately disagree.
    `passed` is arithmetic — the database recomputes it from the row's own two
    numbers — while `plan.md` § Error Handling Strategy makes an uncomputable
    blocking metric a refusal in its own right, named as uncomputable rather
    than as out of range. A floor satisfied by an infinity is the shape that
    would otherwise ship.
    """
    for observed in (math.inf, -math.inf):
        row = _row(threshold.metric, observed)

        assert not row.is_computable
        assert blocking_breaches([row]) == (row,), (
            f"{threshold.metric} at {observed!r} was not treated as a breach; a blocking "
            f"metric that could not be measured is not a metric that passed"
        )


# ---------------------------------------------------------------------------
# The classification the row carries, mirrored from `0303`'s own checks
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("threshold", DIAGNOSTIC_THRESHOLDS, ids=lambda row: row.metric)
def test_the_direction_is_a_function_of_the_metric(threshold: DiagnosticThreshold) -> None:
    """`ck_forecast_diagnostic__direction_matches_metric`, in Python.

    Direction is not the writer's to choose: E-BFMI recorded as a ceiling would
    make a breach read as a pass on the same two numbers. The biconditional is
    restated here so the assembled row and the stored row cannot differ.
    """
    row = _row(threshold.metric, threshold.threshold_value)

    assert (row.threshold_direction == "min") == (
        threshold.metric in ("ess_bulk", "ess_tail", "ebfmi")
    )
    assert row.threshold_direction in ("max", "min")


@pytest.mark.parametrize("threshold", DIAGNOSTIC_THRESHOLDS, ids=lambda row: row.metric)
def test_the_scope_is_a_function_of_the_metric(threshold: DiagnosticThreshold) -> None:
    """`ck_forecast_diagnostic__metric_matches_scope` and its companion.

    The three per-parameter metrics occur only at parameter scope and carry a
    `parameter_name`; the three run metrics occur only at run scope and carry
    none. Both directions are refused, because a per-parameter divergence count
    is not a quantity and a bare `r_hat` does not say which parameter it is.
    """
    row = _row(threshold.metric, threshold.threshold_value)

    assert (row.metric in PARAMETER_METRICS) == (row.diagnostic_scope == "parameter")
    assert (row.diagnostic_scope == "parameter") == (row.parameter_name is not None)

    with pytest.raises(DiagnosticsError):
        diagnostic_row(
            threshold.metric,
            threshold.threshold_value,
            None if threshold.metric in PARAMETER_METRICS else A_PARAMETER,
        )


@pytest.mark.parametrize("threshold", DIAGNOSTIC_THRESHOLDS, ids=lambda row: row.metric)
def test_blocking_is_a_function_of_the_metric_and_treedepth_is_the_exception(
    threshold: DiagnosticThreshold,
) -> None:
    """FR-018 as `ck_forecast_diagnostic__blocking_matches_metric`.

    Treedepth is reported and never blocking; the other five always block.
    Neither classification is editable row by row, which is what stops an
    inconvenient blocking metric being reclassified on the run that breaches it.
    """
    row = _row(threshold.metric, threshold.threshold_value)

    assert row.is_blocking == (threshold.metric != "max_treedepth_hits")
    assert row.is_blocking == threshold.is_blocking


def test_a_treedepth_hit_fails_its_own_bar_and_still_refuses_nothing() -> None:
    """The published bar of 0 is a real comparison whose verdict gates nothing.

    `forecast_diagnostic.threshold_value` is NOT NULL and `passed` is computed
    against it, so the reported row would otherwise assert a verdict against a
    threshold nobody stated. At 0 with direction `max`, `passed = false` means
    the sampler hit the cap at least once — and the run still ships, which is
    exactly what `ck_forecast_diagnostic__blocking_rows_passed` permits by
    constraining only the rows where `is_blocking`.
    """
    hit = diagnostic_row("max_treedepth_hits", 1.0, None)
    clean = diagnostic_row("max_treedepth_hits", 0.0, None)

    assert not hit.passed
    assert clean.passed
    assert not hit.is_blocking
    assert not blocking_breaches([hit, clean])


# ---------------------------------------------------------------------------
# The stored expression, re-derived by a second code path
# ---------------------------------------------------------------------------


@given(
    metric=st.sampled_from(METRICS),
    observed=st.floats(
        min_value=-VALUE_SPAN, max_value=VALUE_SPAN, allow_nan=False, allow_infinity=False
    ),
)
def test_the_verdict_agrees_with_the_databases_own_expression(
    metric: str, observed: float
) -> None:
    """An alternate implementation of `ck_forecast_diagnostic__passed_matches_threshold`.

    Written here as the `CASE` the migration declares, rather than by calling
    the function under test, so agreement is between two independently written
    expressions. A row whose `passed` disagreed with this would be refused on
    insert — but only after a whole transaction had been assembled, and only
    with a constraint name where a metric name belongs.
    """
    row = _row(metric, observed)
    threshold = threshold_for(metric)
    expected = (
        observed <= threshold.threshold_value
        if threshold.threshold_direction == "max"
        else observed >= threshold.threshold_value
    )

    assert row.passed is expected
    assert row.observed_value == observed
    assert row.threshold_value == threshold.threshold_value


@given(
    values=st.lists(
        st.floats(
            min_value=-VALUE_SPAN, max_value=VALUE_SPAN, allow_nan=False, allow_infinity=False
        ),
        min_size=len(METRICS),
        max_size=len(METRICS),
    )
)
def test_every_breaching_blocking_row_is_reported_and_not_merely_the_first(
    values: list[float],
) -> None:
    """FR-017's "every breached diagnostic, not merely the first one found".

    An operator handed one breach out of four returns for a second run to
    discover the next, which is the failure mode this clause exists to close.
    Asserted as set equality against an independently filtered expectation, so
    a gate that stopped at the first breach or that dropped the treedepth row
    from the wrong side of the filter fails here.
    """
    rows = tuple(_row(metric, value) for metric, value in zip(METRICS, values, strict=True))
    expected = tuple(row for row in rows if row.is_blocking and not row.passed)

    assert blocking_breaches(rows) == expected
