"""FR-060: the continuity-corrected Wilson interval, and the figures it labels.

T049, the **red** half of the second strict red-green pair (`plan.md` §The
test-first boundary). Authored and run against an absent
`model.compute.metrics`, observed to fail with a collection error, and only then
implemented by T050. The observed failure is recorded on T049's task line.

**The property that distinguishes the two implementations.** An uncorrected
Wilson interval satisfies almost everything one would naturally assert about a
corrected one: both stay inside `[0,1]`, both contain their point estimate, both
have non-zero width at 0 and at *n* successes, and both narrow as *n* grows. A
test suite made of those would pass on an uncorrected implementation and the
`AD-011` decision — apply the correction rather than disclose its absence —
would be undone silently.

So the reference is written out here, independently, from the uncorrected score
formula, and two things are asserted against it: the corrected interval is
**never narrower** on any input, and it is **strictly wider** on inputs where the
correction has something to do. The first is the invariant; the second is what
makes the first non-vacuous, because equality satisfies "never narrower".

**No F1.** SC-047 requires zero F1 figures, so this file asserts the module
exports none and publishes the omission's reason. That is a real assertion
rather than a formality: F1 is the figure a reader expects beside precision and
recall, and its absence is only honest if the reason travels with it.
"""

from __future__ import annotations

import math

import pytest
from hypothesis import assume, given
from hypothesis import strategies as st

from model.compute.metrics import (
    F1_OMISSION_REASON,
    INTERVAL_METHOD,
    Z_95,
    FieldCounts,
    MetricsError,
    Proportion,
    per_field_figures,
    wilson_interval,
)


def uncorrected_wilson(successes: int, trials: int) -> tuple[float, float]:
    """The **uncorrected** Wilson score interval, written independently here.

    This is the implementation AD-011 rejected, present only as the thing the
    corrected one must never be narrower than. Written from the score-test
    inversion directly rather than by deleting terms from the module under test,
    so a defect shared by both would have to be made twice.
    """
    proportion = successes / trials
    denominator = 1 + Z_95**2 / trials
    centre = (proportion + Z_95**2 / (2 * trials)) / denominator
    half = (
        Z_95
        / denominator
        * math.sqrt(proportion * (1 - proportion) / trials + Z_95**2 / (4 * trials**2))
    )
    return max(0.0, centre - half), min(1.0, centre + half)


def width(interval: tuple[float, float]) -> float:
    return interval[1] - interval[0]


#: Denominators the per-field figures actually run at. AD-011 chose the
#: correction because per-field denominators are "frequently under 20", so the
#: properties are exercised hardest exactly there — but the upper bound is kept
#: generous so a property that only holds for small *n* cannot hide.
trial_counts = st.integers(min_value=1, max_value=500)


@st.composite
def counts(draw: st.DrawFn) -> tuple[int, int]:
    """A `(successes, trials)` pair with `0 <= successes <= trials`."""
    trials = draw(trial_counts)
    return draw(st.integers(min_value=0, max_value=trials)), trials


# ---------------------------------------------------------------------------
# Invariants
# ---------------------------------------------------------------------------


@given(counts())
def test_the_interval_lies_inside_the_unit_range(pair: tuple[int, int]) -> None:
    """A probability bound outside `[0,1]` is the Wald failure AD-011 rejects."""
    successes, trials = pair
    low, high = wilson_interval(successes, trials)
    assert 0.0 <= low <= high <= 1.0


@given(counts())
def test_the_interval_contains_its_point_estimate(pair: tuple[int, int]) -> None:
    """An interval that excludes the number it is an interval *for* is not one."""
    successes, trials = pair
    low, high = wilson_interval(successes, trials)
    assert low <= successes / trials <= high


@given(trial_counts)
def test_the_interval_has_width_at_zero_successes(trials: int) -> None:
    """The Wald degeneracy, asserted at the boundary it degenerates on.

    Wald gives `[0, 0]` for 0 of *n* and `[1, 1]` for *n* of *n*, so "100%
    precision" from 7 of 7 reads as certainty. Wilson does not, and this is
    where the difference matters.
    """
    assert width(wilson_interval(0, trials)) > 0.0


@given(trial_counts)
def test_the_interval_has_width_at_full_successes(trials: int) -> None:
    assert width(wilson_interval(trials, trials)) > 0.0


@given(st.sampled_from([0.0, 0.25, 0.5, 0.75, 1.0]), st.integers(min_value=1, max_value=60))
def test_width_is_non_increasing_in_n_at_a_fixed_proportion(
    proportion: float, multiple: int
) -> None:
    """More evidence never widens the interval.

    The denominators are chosen so the proportion is exact at both — comparing
    `3/7` against `6/14` would otherwise vary the proportion as well as *n* and
    assert nothing about either.
    """
    small = 4 * multiple
    large = 2 * small
    narrow = width(wilson_interval(round(proportion * small), small))
    narrower = width(wilson_interval(round(proportion * large), large))
    assert narrower <= narrow + 1e-12


# ---------------------------------------------------------------------------
# The correction itself — the property an uncorrected implementation fails
# ---------------------------------------------------------------------------


@given(counts())
def test_the_corrected_interval_is_never_narrower_than_the_uncorrected_one(
    pair: tuple[int, int],
) -> None:
    """AD-011's direction of error, as an invariant over the whole domain.

    The correction errs toward over-coverage, which is the honest direction
    under Principle II: an interval that is too wide overstates uncertainty, and
    one that is too narrow understates it.
    """
    successes, trials = pair
    corrected = wilson_interval(successes, trials)
    plain = uncorrected_wilson(successes, trials)
    assert width(corrected) >= width(plain) - 1e-12
    assert corrected[0] <= plain[0] + 1e-12
    assert corrected[1] >= plain[1] - 1e-12


@given(counts())
def test_the_corrected_interval_is_strictly_wider_where_it_is_not_clamped(
    pair: tuple[int, int],
) -> None:
    """And this is what makes the invariant above non-vacuous.

    "Never narrower" is satisfied by equality, so an uncorrected implementation
    passes it. Strict widening is what it cannot pass. The clamped boundaries
    are excluded because both bounds are pinned to 0 or 1 there by construction
    and neither implementation is free to differ.
    """
    successes, trials = pair
    assume(0 < successes < trials)
    corrected = wilson_interval(successes, trials)
    assume(corrected[0] > 0.0 and corrected[1] < 1.0)
    assert width(corrected) > width(uncorrected_wilson(successes, trials))


def test_a_worked_case_matches_the_published_correction() -> None:
    """One arithmetic anchor, so a sign error cannot hide behind the properties.

    Newcombe (1998) tabulates both intervals for 81 successes in 263 trials at
    95%: the score interval (his method 3) is `0.2553` to `0.3662`, and the
    score interval **with continuity correction** (his method 4) is `0.2535` to
    `0.3682`. Both are asserted — the corrected one pins the module, and the
    uncorrected one pins this file's own reference implementation, so a shared
    arithmetic defect cannot make the comparison properties above agree by
    being wrong in the same direction.

    A property suite over inequalities alone would accept a systematically
    shifted interval. This does not.
    """
    low, high = wilson_interval(81, 263)
    assert low == pytest.approx(0.2535, abs=5e-5)
    assert high == pytest.approx(0.3682, abs=5e-5)

    plain_low, plain_high = uncorrected_wilson(81, 263)
    assert plain_low == pytest.approx(0.2553, abs=5e-5)
    assert plain_high == pytest.approx(0.3662, abs=5e-5)


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_an_empty_denominator_is_refused() -> None:
    """SC-047: zero figures rest on an empty denominator, and no layer row is
    blank or `0/0`. The real layer is published as *not measured* with its
    reason instead, which is only possible if `0/0` cannot be constructed."""
    with pytest.raises(MetricsError):
        wilson_interval(0, 0)


def test_more_successes_than_trials_is_refused() -> None:
    with pytest.raises(MetricsError):
        wilson_interval(8, 7)


def test_a_negative_count_is_refused() -> None:
    with pytest.raises(MetricsError):
        wilson_interval(-1, 7)


def test_a_proportion_refuses_an_empty_denominator() -> None:
    with pytest.raises(MetricsError):
        Proportion(name="precision", numerator=0, denominator=0, denominator_names="nothing")


# ---------------------------------------------------------------------------
# FR-060 — the published figures
# ---------------------------------------------------------------------------


def test_a_proportion_prints_its_denominator_and_names_the_method() -> None:
    """FR-060: both denominators stated and printed beside their figures, and the
    interval variant **named with the figures** rather than in a footnote."""
    figure = Proportion(
        name="precision",
        numerator=6,
        denominator=7,
        denominator_names="values the run stored for this field and layer",
    )
    rendered = figure.rendered()
    assert "6/7" in rendered
    assert INTERVAL_METHOD in rendered
    assert figure.point == pytest.approx(6 / 7)


def test_the_two_denominators_are_different_populations() -> None:
    """FR-060's second paragraph: precision is denominated on stored values and
    recall on the fields the generator recorded as printed. Denominating recall
    on stored values would make it unable to see a value that was never stored,
    which is the only thing recall is for."""
    figures = per_field_figures(
        [
            FieldCounts(
                field="manufacturer",
                layer="SYNTHETIC",
                stored=7,
                stored_matching=6,
                printed=9,
                printed_recovered=6,
            )
        ]
    )
    (entry,) = figures
    assert entry.precision.denominator == 7
    assert entry.recall.denominator == 9
    assert "stored" in entry.precision.denominator_names
    assert "printed" in entry.recall.denominator_names


def test_permuting_the_field_order_permutes_the_figures_and_changes_none() -> None:
    """The metamorphic relation `plan.md` names for this module.

    Pooling two fields to manufacture a larger *n* is deliberately **not**
    asserted to preserve either figure, and the absence is the point: pooling is
    what the research rejects, and a property asserting it were harmless would
    invite it.
    """
    observations = [
        FieldCounts("manufacturer", "SYNTHETIC", 7, 6, 9, 6),
        FieldCounts("part_number", "SYNTHETIC", 7, 7, 9, 7),
        FieldCounts("quantity", "SYNTHETIC", 5, 4, 9, 4),
    ]
    forward = per_field_figures(observations)
    reversed_order = per_field_figures(list(reversed(observations)))
    assert [entry.field for entry in forward] == [entry.field for entry in reversed_order]
    assert [entry.precision.point for entry in forward] == [
        entry.precision.point for entry in reversed_order
    ]


def test_no_f1_is_published_and_the_omission_carries_its_reason() -> None:
    """SC-047: zero F1 figures, and the omission published with this reason.

    A Wilson interval inverts the score test for a **binomial proportion**; F1 is
    a harmonic mean of two proportions with different denominators, so no
    interval for it exists while SC-029 admits no figure without one.
    """
    import model.compute.metrics as metrics

    assert not [
        name for name in dir(metrics) if "f1" in name.lower() and name != "F1_OMISSION_REASON"
    ]
    assert "harmonic mean" in F1_OMISSION_REASON
    assert "denominator" in F1_OMISSION_REASON


def test_the_interval_method_is_named_as_continuity_corrected() -> None:
    """FR-060 fixes the variant *and* requires it named with the figures, because
    "Wilson 95%" alone does not say which of the two was computed."""
    assert "continuity-corrected" in INTERVAL_METHOD
    assert "95" in INTERVAL_METHOD
